"""Testes de T4 — histórico persistente em SQLite.

Nenhum teste toca a rede nem disco fora de tmp_path. O relógio é injetado para
as datas serem determinísticas.

Referência: SPEC 9 e 10.1.

Etapa 2, decisão 3: o histórico passou a guardar UMA LINHA POR TENTATIVA. Um
re-download não pode apagar o registro de um arquivo que continua no disco —
foi o que o smoke test flagrou (3 arquivos, 2 registros).
"""

import sqlite3
import threading

import pytest

from src.domain.models import Video
from src.storage.historico import (
    Historico,
    RegistroHistorico,
    RegistroNaoEncontrado,
    normalizar_busca,
)


class Relogio:
    """Cada chamada avança um segundo, em UTC ISO-8601."""

    def __init__(self):
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"2026-09-02T10:00:{self.n:02d}+00:00"


@pytest.fixture
def relogio():
    return Relogio()


@pytest.fixture
def h(tmp_path, relogio):
    hist = Historico(tmp_path / "historico.db", agora=relogio)
    hist.criar_schema()
    yield hist
    hist.fechar()


def video(
    video_id="LzS8kB6lIm0",
    titulo="Camisa azul da Seleção",
    extractor="Youtube",
    canal="Canal Michuruca",
    duracao=65,
) -> Video:
    return Video(
        video_id=video_id,
        extractor=extractor,
        url_canonica=f"https://www.youtube.com/watch?v={video_id}",
        titulo=titulo,
        canal=canal,
        duracao_s=duracao,
        thumbnail_url=None,
        data_upload="20260901",
        formatos=(),
    )


def iniciar(h, v=None, perfil="edicao_1080", projeto="pessoal"):
    v = v or video()
    return h.iniciar(v, perfil=perfil, projeto=projeto, url_original=v.url_canonica)


# ===========================================================================
# Schema
# ===========================================================================


def test_criar_schema_e_idempotente(h):
    h.criar_schema()
    h.criar_schema()


def test_cria_o_diretorio_do_banco_se_nao_existir(tmp_path, relogio):
    caminho = tmp_path / "data" / "sub" / "historico.db"
    hist = Historico(caminho, agora=relogio)
    hist.criar_schema()
    hist.fechar()
    assert caminho.exists()


def test_schema_nao_tem_mais_chave_unica(tmp_path, relogio):
    """Decisão 3: uma linha por tentativa. Um UNIQUE em
    (extractor, video_id, perfil) forçaria o upsert destrutivo de volta."""
    caminho = tmp_path / "h.db"
    hist = Historico(caminho, agora=relogio)
    hist.criar_schema()
    hist.fechar()
    con = sqlite3.connect(caminho)
    unicos = [row for row in con.execute("PRAGMA index_list(historico)") if row[2] == 1]
    con.close()
    assert unicos == [], f"índice único encontrado: {unicos}"


# ===========================================================================
# iniciar — a linha `baixando` que torna o `interrompido` possível
# ===========================================================================


def test_iniciar_grava_baixando_sem_caminho(h):
    r = iniciar(h)
    assert r.status == "baixando"
    assert r.caminho is None and r.tamanho_bytes is None and r.concluido_em is None


def test_iniciar_grava_datas_em_iso8601_utc(h):
    assert iniciar(h).criado_em == "2026-09-02T10:00:01+00:00"


def test_iniciar_copia_os_metadados_do_video(h):
    r = iniciar(h)
    assert (r.video_id, r.extractor, r.titulo) == (
        "LzS8kB6lIm0",
        "Youtube",
        "Camisa azul da Seleção",
    )
    assert (r.canal, r.duracao_s, r.perfil, r.projeto) == (
        "Canal Michuruca",
        65,
        "edicao_1080",
        "pessoal",
    )


def test_iniciar_com_o_fixture_real(h, info_dict_real):
    v = Video.de_info_dict(info_dict_real)
    r = h.iniciar(
        v, perfil="edicao_1080", projeto="pessoal", url_original=info_dict_real["original_url"]
    )
    assert r.titulo == info_dict_real["title"]
    assert r.url_canonica == info_dict_real["webpage_url"]
    assert r.url_original == info_dict_real["original_url"]


def test_iniciar_devolve_id_positivo_e_crescente(h):
    a = iniciar(h)
    b = iniciar(h, video("bbbbbbbbbbb"))
    assert 0 < a.id < b.id


# ===========================================================================
# Decisão 3 — preservação: uma linha por tentativa
# ===========================================================================


def test_reiniciar_a_mesma_chave_cria_UMA_NOVA_LINHA(h):
    """O caso do smoke test: rebaixar não pode apagar o registro anterior."""
    a = iniciar(h)
    h.concluir(a.id, caminho="D:/F/a.mp4", tamanho_bytes=100, resolucao="1080x1920")
    b = iniciar(h)
    assert b.id != a.id
    assert len(h.buscar()) == 2
    antigo = h.obter_por_id(a.id)
    assert antigo.status == "concluido"
    assert antigo.caminho == "D:/F/a.mp4", "o caminho do arquivo anterior foi apagado"


def test_redownload_que_falha_preserva_o_arquivo_anterior(h):
    """O caso que o autor chamou de bug, não de decisão."""
    a = iniciar(h)
    h.concluir(a.id, caminho="D:/F/a.mp4", tamanho_bytes=100)
    b = iniciar(h)
    h.falhar(b.id, motivo="rede", mensagem="timeout")

    assert h.obter_por_id(a.id).caminho == "D:/F/a.mp4"
    ja = h.ja_baixado("Youtube", "LzS8kB6lIm0", "edicao_1080")
    assert ja is not None and ja.caminho == "D:/F/a.mp4", (
        "depois de uma falha, o histórico esqueceu o arquivo que está no disco"
    )


def test_ja_baixado_devolve_a_tentativa_concluida_MAIS_RECENTE(h):
    a = iniciar(h)
    h.concluir(a.id, caminho="D:/F/a.mp4", tamanho_bytes=1)
    b = iniciar(h)
    h.concluir(b.id, caminho="D:/F/a (2).mp4", tamanho_bytes=2)
    assert h.ja_baixado("Youtube", "LzS8kB6lIm0", "edicao_1080").caminho == "D:/F/a (2).mp4"


def test_todo_arquivo_baixado_tem_uma_linha(h):
    """A invariante que o smoke test violou: nenhum arquivo fica órfão."""
    caminhos = []
    for nome in ("a.mp4", "a (2).mp4", "a (3).mp4"):
        r = iniciar(h)
        h.concluir(r.id, caminho=f"D:/F/{nome}", tamanho_bytes=1)
        caminhos.append(f"D:/F/{nome}")
    registrados = {r.caminho for r in h.buscar() if r.status == "concluido"}
    assert registrados == set(caminhos)


# ===========================================================================
# concluir / falhar — por id da tentativa
# ===========================================================================


def test_concluir_preenche_caminho_tamanho_e_data(h):
    r = h.concluir(iniciar(h).id, caminho="D:/F/x.mp4", tamanho_bytes=12345, resolucao="1080x1920")
    assert r.status == "concluido"
    assert (r.caminho, r.tamanho_bytes, r.resolucao) == ("D:/F/x.mp4", 12345, "1080x1920")
    assert r.concluido_em == "2026-09-02T10:00:02+00:00"


def test_concluir_marcando_que_o_arquivo_ja_existia(h):
    """Decisão 1: sucesso, mas o usuário precisa saber que não baixou."""
    r = h.concluir(iniciar(h).id, caminho="D:/F/x.mp4", tamanho_bytes=9, ja_existia=True)
    assert r.status == "concluido" and r.ja_existia is True
    assert r.aviso and "já existia" in r.aviso.lower()


def test_concluir_normal_nao_marca_ja_existia(h):
    r = h.concluir(iniciar(h).id, caminho="D:/F/x.mp4", tamanho_bytes=9)
    assert r.ja_existia is False and r.aviso is None


def test_falhar_guarda_motivo_e_mensagem_sem_caminho(h):
    r = h.falhar(iniciar(h).id, motivo="privado", mensagem="Private video")
    assert r.status == "falhou"
    assert (r.motivo_falha, r.mensagem_falha) == ("privado", "Private video")
    assert r.caminho is None and r.concluido_em is not None


@pytest.mark.parametrize("acao", ["concluir", "falhar"])
def test_atualizar_registro_inexistente_levanta(h, acao):
    with pytest.raises(RegistroNaoEncontrado):
        if acao == "concluir":
            h.concluir(9999, caminho="x", tamanho_bytes=1)
        else:
            h.falhar(9999, motivo="x", mensagem="y")


# ===========================================================================
# registrar_destino — decisão 5
# ===========================================================================


def test_registrar_destino_grava_o_caminho_pretendido(h):
    """Sem isto, um `interrompido` não tem caminho e a reconciliação da subida
    não tem onde procurar o arquivo."""
    r = iniciar(h)
    h.registrar_destino(r.id, "D:/F/pretendido.mp4")
    atual = h.obter_por_id(r.id)
    assert atual.caminho == "D:/F/pretendido.mp4"
    assert atual.status == "baixando", "registrar destino não conclui o job"


def test_registrar_destino_inexistente_levanta(h):
    with pytest.raises(RegistroNaoEncontrado):
        h.registrar_destino(9999, "x")


# ===========================================================================
# avisar — decisões 1, 4 e 5
# ===========================================================================


def test_avisar_grava_e_nao_muda_o_status(h):
    r = iniciar(h)
    h.concluir(r.id, caminho="D:/F/x.mp4", tamanho_bytes=1)
    h.avisar(r.id, "o arquivo pode estar truncado")
    atual = h.obter_por_id(r.id)
    assert atual.aviso == "o arquivo pode estar truncado"
    assert atual.status == "concluido"


def test_avisar_acumula_em_vez_de_sobrescrever(h):
    """Perder um aviso é o mesmo problema de perder um registro."""
    r = iniciar(h)
    h.avisar(r.id, "primeiro")
    h.avisar(r.id, "segundo")
    aviso = h.obter_por_id(r.id).aviso
    assert "primeiro" in aviso and "segundo" in aviso


def test_avisar_o_mesmo_texto_duas_vezes_nao_duplica(h):
    r = iniciar(h)
    h.avisar(r.id, "igual")
    h.avisar(r.id, "igual")
    assert h.obter_por_id(r.id).aviso.count("igual") == 1


# ===========================================================================
# Chaves e consultas
# ===========================================================================


def test_mesmo_video_em_perfis_diferentes_sao_registros_distintos(h):
    iniciar(h, perfil="edicao_1080")
    iniciar(h, perfil="so_audio")
    assert len(h.buscar()) == 2


def test_mesmo_video_id_em_extractors_diferentes(h):
    iniciar(h, video(extractor="Youtube"))
    iniciar(h, video(extractor="Vimeo"))
    assert len(h.buscar()) == 2


def test_ja_baixado_e_none_quando_nunca_visto(h):
    assert h.ja_baixado("Youtube", "LzS8kB6lIm0", "edicao_1080") is None


@pytest.mark.parametrize("desfecho", ["falhar", "nada"])
def test_ja_baixado_e_none_sem_conclusao(h, desfecho):
    r = iniciar(h)
    if desfecho == "falhar":
        h.falhar(r.id, motivo="rede", mensagem="x")
    assert h.ja_baixado("Youtube", "LzS8kB6lIm0", "edicao_1080") is None


def test_ja_baixado_distingue_o_perfil(h):
    r = iniciar(h, perfil="edicao_1080")
    h.concluir(r.id, caminho="a", tamanho_bytes=1)
    assert h.ja_baixado("Youtube", "LzS8kB6lIm0", "so_audio") is None


def test_obter_por_id_inexistente_e_none(h):
    assert h.obter_por_id(9999) is None


# ===========================================================================
# buscar
# ===========================================================================


def test_buscar_sem_filtro_devolve_tudo_mais_recente_primeiro(h):
    for vid, titulo in (
        ("aaaaaaaaaaa", "Primeiro"),
        ("bbbbbbbbbbb", "Segundo"),
        ("ccccccccccc", "Terceiro"),
    ):
        iniciar(h, video(vid, titulo))
    assert [r.titulo for r in h.buscar()] == ["Terceiro", "Segundo", "Primeiro"]


def test_buscar_por_termo_parcial(h):
    iniciar(h, video("aaaaaaaaaaa", "Melhores momentos do major"))
    iniciar(h, video("bbbbbbbbbbb", "Rush B"))
    assert [r.titulo for r in h.buscar(termo="major")] == ["Melhores momentos do major"]


@pytest.mark.parametrize("termo", ["selecao", "SELEÇÃO", "seleção", "Seleção"])
def test_buscar_ignora_acento_e_caixa(h, termo):
    """O LIKE do SQLite só ignora caixa em ASCII: 'Ç' não casa com 'ç'."""
    iniciar(h, video("aaaaaaaaaaa", "Camisa azul da Seleção"))
    assert len(h.buscar(termo=termo)) == 1


def test_buscar_por_projeto(h):
    iniciar(h, video("aaaaaaaaaaa", "A"), projeto="cliente_x")
    iniciar(h, video("bbbbbbbbbbb", "B"), projeto="pessoal")
    assert [r.titulo for r in h.buscar(projeto="cliente_x")] == ["A"]


def test_buscar_termo_e_projeto_juntos(h):
    iniciar(h, video("aaaaaaaaaaa", "Final major"), projeto="cliente_x")
    iniciar(h, video("bbbbbbbbbbb", "Final major"), projeto="pessoal")
    r = h.buscar(termo="major", projeto="pessoal")
    assert len(r) == 1 and r[0].projeto == "pessoal"


def test_buscar_respeita_o_limite(h):
    for i in range(5):
        iniciar(h, video(f"{i:0>11}", f"v{i}"))
    assert len(h.buscar(limite=2)) == 2


@pytest.mark.parametrize("termo", ["", "   "])
def test_buscar_termo_vazio_e_igual_a_sem_termo(h, termo):
    iniciar(h)
    assert len(h.buscar(termo=termo)) == 1


@pytest.mark.parametrize("curinga", ["%", "_"])
def test_buscar_nao_e_vulneravel_a_curinga_do_like(h, curinga):
    iniciar(h, video("aaaaaaaaaaa", "Rush B"))
    assert h.buscar(termo=curinga) == []


# Tabela protegida do formatador: entrada e saída esperada lado a lado.
# fmt: off
@pytest.mark.parametrize("entrada,esperado", [
    ("Seleção", "selecao"), ("SELEÇÃO", "selecao"), ("Ação!", "acao!"),
    ("  Rush  B ", "rush b"), ("", ""),
])
# fmt: on
def test_normalizar_busca(entrada, esperado):
    assert normalizar_busca(entrada) == esperado


# ===========================================================================
# marcar_interrompidos — a reconciliação na subida (SPEC 10.1)
# ===========================================================================

def test_marcar_interrompidos_so_mexe_em_baixando(h):
    a = iniciar(h, video("aaaaaaaaaaa", "A"))
    b = iniciar(h, video("bbbbbbbbbbb", "B"))
    h.concluir(b.id, caminho="x", tamanho_bytes=1)
    c = iniciar(h, video("ccccccccccc", "C"))
    h.falhar(c.id, motivo="rede", mensagem="x")

    marcados = h.marcar_interrompidos()
    assert [r.id for r in marcados] == [a.id]
    assert {r.video_id: r.status for r in h.buscar()} == {
        "aaaaaaaaaaa": "interrompido", "bbbbbbbbbbb": "concluido", "ccccccccccc": "falhou"}


def test_marcar_interrompidos_devolve_os_registros_com_caminho(h):
    """Decisão 5: o pipeline precisa do caminho para procurar o arquivo."""
    r = iniciar(h)
    h.registrar_destino(r.id, "D:/F/pretendido.mp4")
    marcados = h.marcar_interrompidos()
    assert len(marcados) == 1
    assert marcados[0].caminho == "D:/F/pretendido.mp4"
    assert marcados[0].status == "interrompido"


def test_interrompido_nunca_vira_concluido_sozinho(h):
    iniciar(h)
    h.marcar_interrompidos()
    assert h.ja_baixado("Youtube", "LzS8kB6lIm0", "edicao_1080") is None


def test_marcar_interrompidos_e_idempotente(h):
    iniciar(h)
    assert len(h.marcar_interrompidos()) == 1
    assert h.marcar_interrompidos() == []


def test_interrompido_pode_ser_reiniciado(h):
    iniciar(h)
    h.marcar_interrompidos()
    assert iniciar(h).status == "baixando"


# ===========================================================================
# Persistência e concorrência
# ===========================================================================

def test_dados_sobrevivem_a_reabertura(tmp_path, relogio):
    caminho = tmp_path / "h.db"
    a = Historico(caminho, agora=relogio)
    a.criar_schema()
    rid = a.iniciar(video(), perfil="edicao_1080", projeto="p", url_original="u").id
    a.fechar()

    b = Historico(caminho, agora=relogio)
    b.criar_schema()
    assert b.obter_por_id(rid) is not None
    b.fechar()


def test_escritas_concorrentes_de_varias_threads(h):
    """O worker grava enquanto a web lê. Threads reais, sem sleep."""
    erros = []

    def gravar(i):
        try:
            r = h.iniciar(video(f"{i:0>11}", f"v{i}"), perfil="edicao_1080",
                          projeto="p", url_original="u")
            h.concluir(r.id, caminho=f"D:/F/{i}.mp4", tamanho_bytes=i + 1)
            h.buscar()
        except Exception as e:      # noqa: BLE001
            erros.append(e)

    ts = [threading.Thread(target=gravar, args=(i,)) for i in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert erros == []
    assert len(h.buscar(limite=100)) == 20


def test_registro_e_imutavel(h):
    r = iniciar(h)
    with pytest.raises(Exception):
        r.status = "concluido"


def test_fechar_e_idempotente(tmp_path, relogio):
    hist = Historico(tmp_path / "h.db", agora=relogio)
    hist.criar_schema()
    hist.fechar()
    hist.fechar()


def test_usar_depois_de_fechar_levanta_erro_claro(tmp_path, relogio):
    hist = Historico(tmp_path / "h.db", agora=relogio)
    hist.criar_schema()
    hist.fechar()
    with pytest.raises(Exception):
        hist.buscar()


def test_datas_iso_ordenam_como_texto(h):
    iniciar(h, video("aaaaaaaaaaa", "A"))
    iniciar(h, video("bbbbbbbbbbb", "B"))
    datas = [r.criado_em for r in h.buscar()]
    assert datas == sorted(datas, reverse=True)


def test_registro_tem_os_campos_novos():
    """Guard: o dataclass precisa expor ja_existia e aviso para a API."""
    campos = RegistroHistorico.__dataclass_fields__
    assert "ja_existia" in campos and "aviso" in campos
