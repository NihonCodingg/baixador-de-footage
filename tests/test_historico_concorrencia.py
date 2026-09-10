"""Concorrência do histórico — o worker grava enquanto a web lê.

Este arquivo é separado de propósito: a CI roda só ele num job Linux
permanente e bloqueante. A corrida que motivou estes testes (ADR 0001) nunca
apareceu no Windows — o escalonador dá fatias de tempo longas e esconde a
janela — e apareceu uma vez no Linux da CI. Os testes de estresse são a única
chance de ver o SINTOMA; o teste de invariante é o que garante a CAUSA.

Nenhum teste toca a rede nem disco fora de tmp_path.
"""

import sys
import threading
import traceback

import pytest

from src.domain.models import Video
from src.storage.historico import Historico


@pytest.fixture
def h(tmp_path):
    hist = Historico(tmp_path / "historico.db")
    hist.criar_schema()
    yield hist
    hist.fechar()


def video(video_id="LzS8kB6lIm0", titulo="Camisa azul da Seleção") -> Video:
    return Video(
        video_id=video_id,
        extractor="Youtube",
        url_canonica=f"https://www.youtube.com/watch?v={video_id}",
        titulo=titulo,
        canal="Canal Michuruca",
        duracao_s=65,
        thumbnail_url=None,
        data_upload="20260901",
        formatos=(),
    )


def iniciar(h, v=None, perfil="edicao_1080", projeto="pessoal"):
    v = v or video()
    return h.iniciar(v, perfil=perfil, projeto=projeto, url_original=v.url_canonica)


def test_escritas_concorrentes_de_varias_threads(h):
    """O worker grava enquanto a web lê. Threads reais, sem sleep."""
    erros = []

    def gravar(i):
        try:
            r = h.iniciar(
                video(f"{i:0>11}", f"v{i}"), perfil="edicao_1080", projeto="p", url_original="u"
            )
            h.concluir(r.id, caminho=f"D:/F/{i}.mp4", tamanho_bytes=i + 1)
            h.buscar()
        except Exception as e:  # noqa: BLE001
            erros.append(e)

    ts = [threading.Thread(target=gravar, args=(i,)) for i in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert erros == []
    assert len(h.buscar(limite=100)) == 20


@pytest.fixture
def preempcao_agressiva():
    """Faz o interpretador trocar de thread ~5000 vezes mais que o padrão.

    Não é sleep: é o contrário. Alarga a chance de outra thread entrar na
    conexão entre o execute() e o fetch(). Restaura o valor original no fim.
    """
    anterior = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(anterior)


def test_leitores_e_escritores_concorrentes_nao_corrompem_linhas(h, preempcao_agressiva):
    """Leitores chamam buscar() em laço enquanto escritores gravam e commitam.

    A janela: _executar solta o lock antes de fetchone()/fetchall(). O módulo
    sqlite3 do CPython solta o GIL dentro do fetch, então outra thread entra
    na mesma conexão no meio da leitura e a linha sai com menos colunas do que
    o description anuncia — IndexError dentro de _linha.

    Windows esconde isso (fatias de tempo longas); Linux atravessa a janela.
    Por isso este teste roda num job próprio em ubuntu na CI.

    As exceções são coletadas e não engolidas: exceção dentro de uma thread
    não derruba o teste sozinha — o join() volta normal e o teste passaria em
    silêncio. O assert final é o que faz a falha aparecer, com a lista.
    """
    for i in range(300):
        iniciar(h, video(f"{i:0>11}", f"v{i}"))

    erros: list[BaseException] = []

    def ler():
        for _ in range(40):
            try:
                for r in h.buscar(limite=300):
                    assert r.id > 0
            except Exception:  # noqa: BLE001 — coletado para o assert final
                erros.append(traceback.format_exc())

    def escrever(base):
        for k in range(40):
            try:
                r = iniciar(h, video(f"{10_000 + base * 40 + k:0>11}", "novo"))
                h.concluir(r.id, caminho=f"D:/F/{k}.mp4", tamanho_bytes=k + 1)
            except Exception:  # noqa: BLE001 — coletado para o assert final
                erros.append(traceback.format_exc())

    threads = [threading.Thread(target=ler) for _ in range(8)]
    threads += [threading.Thread(target=escrever, args=(b,)) for b in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert erros == []


def test_rajada_concorrente_com_busca_nao_corrompe_linhas(h):
    """A forma exata do teste que falhou na CI, com 60 threads em vez de 20.

    Cada thread faz UMA vez: iniciar() + concluir() + buscar(). Todas largam
    juntas — é a rajada de partida que empilha chamadas no mesmo instante, e
    não o laço sustentado do teste acima. Duas formas cobrem mais do espaço
    de escalonamento do que uma.

    Sem setswitchinterval de propósito: a janela está em código C com o GIL
    solto, e o intervalo de troca do interpretador quase não a governa.
    Guarda o traceback inteiro para a falha dizer de ONDE vem o IndexError.
    """
    erros: list[str] = []

    def gravar(i):
        try:
            r = iniciar(h, video(f"{i:0>11}", f"v{i}"))
            h.concluir(r.id, caminho=f"D:/F/{i}.mp4", tamanho_bytes=i + 1)
            h.buscar()
        except Exception:  # noqa: BLE001 — coletado para o assert final
            erros.append(traceback.format_exc())

    ts = [threading.Thread(target=gravar, args=(i,)) for i in range(60)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert erros == []


class _CursorVigiado:
    """Embrulha o cursor real e anota cada leitura feita sem o lock."""

    def __init__(self, cursor, lock, violacoes):
        self._cursor, self._lock, self._violacoes = cursor, lock, violacoes

    def _vigiar(self, chamada):
        if not self._lock._is_owned():
            self._violacoes.append(chamada)

    def fetchone(self):
        self._vigiar("fetchone")
        return self._cursor.fetchone()

    def fetchall(self):
        self._vigiar("fetchall")
        return self._cursor.fetchall()

    def __iter__(self):
        self._vigiar("iter")
        return iter(self._cursor)

    def __getattr__(self, nome):  # rowcount, lastrowid, description
        return getattr(self._cursor, nome)


class _ConexaoVigiada:
    """Embrulha a conexão real e anota cada chamada ao SQLite feita sem o lock.

    É um dublê de vigilância, não de comportamento: tudo continua indo para o
    SQLite de verdade. Ele só observa se a thread que chamou segurava o lock.
    """

    def __init__(self, con, lock, violacoes):
        self._con, self._lock, self._violacoes = con, lock, violacoes

    def _vigiar(self, chamada):
        if not self._lock._is_owned():
            self._violacoes.append(chamada)

    def execute(self, sql, params=()):
        self._vigiar("execute")
        return _CursorVigiado(self._con.execute(sql, params), self._lock, self._violacoes)

    def commit(self):
        self._vigiar("commit")
        self._con.commit()

    def __getattr__(self, nome):  # executescript, close, row_factory
        return getattr(self._con, nome)


def test_toda_chamada_ao_sqlite_acontece_com_o_lock_seguro(h):
    """Testa a promessa da classe — "uma conexão, um lock" — e não o sintoma.

    A corrida real (IndexError com threads gravando e lendo) só aparece quando
    o escalonador do SO coloca duas threads dentro do SQLite ao mesmo tempo.
    Em 38 rodadas no Linux da CI ela não voltou; no Windows, nunca. Um teste
    que depende de sorte não é regressão.

    Este é determinístico: embrulha a conexão num dublê que anota toda chamada
    ao SQLite feita sem o lock da classe. A causa raiz é exatamente essa —
    fetchone()/fetchall() rodando com o lock já solto — então, se a lista de
    violações estiver vazia, a janela da corrida não existe. Vale em qualquer
    sistema e em qualquer versão.

    RLock._is_owned() é o mesmo gancho que threading.Condition usa por dentro
    para saber se a thread atual segura o lock.
    """
    violacoes: list[str] = []
    h._con = _ConexaoVigiada(h._con, h._lock, violacoes)

    r = iniciar(h)
    h.concluir(r.id, caminho="D:/F/x.mp4", tamanho_bytes=1)
    h.obter_por_id(r.id)
    h.ja_baixado("Youtube", r.video_id, "edicao_1080")
    h.buscar()

    assert violacoes == []
