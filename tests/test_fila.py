"""Testes de T5 — o estado da fila, protegido por lock.

Nenhum teste toca a rede. Threads reais onde a concorrência importa, sem
sleep.

Referência: SPEC 10.2 a 10.5.
"""

import threading
from datetime import datetime

import pytest

from src.domain.erros import TransicaoIlegal
from src.domain.models import EstadoJob, Job, Progresso, Video
from src.queue.fila import Fila


def video(video_id="LzS8kB6lIm0") -> Video:
    return Video(
        video_id=video_id,
        extractor="Youtube",
        url_canonica=f"https://www.youtube.com/watch?v={video_id}",
        titulo="t",
        canal=None,
        duracao_s=None,
        thumbnail_url=None,
        data_upload=None,
        formatos=(),
    )


def job(video_id="LzS8kB6lIm0", id_="j1") -> Job:
    return Job(
        id=id_,
        video=video(video_id),
        perfil="edicao_1080",
        projeto="p",
        estado=EstadoJob.NA_FILA,
        criado_em=datetime(2026, 9, 2),
        url_original="u",
    )


def prog(baixados=1, total=10):
    return Progresso(baixados=baixados, total=total, velocidade_bps=None, eta_s=None)


# ===========================================================================
# adicionar / proximo — FIFO
# ===========================================================================


def test_adicionar_devolve_o_id_e_deixa_na_fila():
    f = Fila()
    assert f.adicionar(job()) == "j1"
    assert f.obter("j1").estado is EstadoJob.NA_FILA


def test_adicionar_id_repetido_levanta():
    f = Fila()
    f.adicionar(job())
    with pytest.raises(ValueError):
        f.adicionar(job())


def test_proximo_respeita_a_ordem_de_chegada():
    f = Fila()
    for i in ("a", "b", "c"):
        f.adicionar(job(id_=i))
    assert [f.proximo(timeout=0).id for _ in range(3)] == ["a", "b", "c"]


def test_proximo_com_fila_vazia_devolve_none():
    assert Fila().proximo(timeout=0) is None


def test_proximo_ja_devolve_o_job_em_baixando():
    """A transição para BAIXANDO acontece sob o mesmo lock do dequeue, para
    um cancelar() não entrar na fresta entre retirar e começar."""
    f = Fila()
    f.adicionar(job())
    assert f.proximo(timeout=0).estado is EstadoJob.BAIXANDO
    assert f.obter("j1").estado is EstadoJob.BAIXANDO


def test_proximo_descarta_os_cancelados():
    f = Fila()
    f.adicionar(job(id_="a"))
    f.adicionar(job(id_="b"))
    assert f.cancelar("a") is True
    assert f.proximo(timeout=0).id == "b"
    assert f.proximo(timeout=0) is None


def test_proximo_bloqueia_ate_chegar_um_job():
    """Sem sleep: uma thread enfileira, a outra estava esperando."""
    f = Fila()
    resultado = []
    pronto = threading.Event()

    def consumir():
        pronto.set()
        resultado.append(f.proximo(timeout=2.0))

    t = threading.Thread(target=consumir)
    t.start()
    assert pronto.wait(2.0)
    f.adicionar(job())
    t.join(2.0)
    assert resultado and resultado[0].id == "j1"


# ===========================================================================
# Cancelamento (SPEC 10.5)
# ===========================================================================


def test_cancelar_na_fila():
    f = Fila()
    f.adicionar(job())
    assert f.cancelar("j1") is True
    assert f.obter("j1").estado is EstadoJob.CANCELADO


def test_cancelar_baixando_e_recusado():
    """A API traduz False em 409."""
    f = Fila()
    f.adicionar(job())
    f.proximo(timeout=0)
    assert f.cancelar("j1") is False
    assert f.obter("j1").estado is EstadoJob.BAIXANDO


@pytest.mark.parametrize("terminal", [EstadoJob.CONCLUIDO, EstadoJob.FALHOU])
def test_cancelar_terminal_e_recusado(terminal):
    f = Fila()
    f.adicionar(job())
    f.proximo(timeout=0)
    f.transicionar("j1", terminal)
    assert f.cancelar("j1") is False


def test_cancelar_inexistente_e_false():
    assert Fila().cancelar("nao") is False


# ===========================================================================
# Transições
# ===========================================================================


def test_transicionar_ilegal_levanta_e_nao_muda():
    f = Fila()
    f.adicionar(job())
    with pytest.raises(TransicaoIlegal):
        f.transicionar("j1", EstadoJob.CONCLUIDO)
    assert f.obter("j1").estado is EstadoJob.NA_FILA


def test_transicionar_inexistente_levanta_keyerror():
    with pytest.raises(KeyError):
        Fila().transicionar("nao", EstadoJob.BAIXANDO)


def test_concluir_preenche_caminho_e_estado():
    f = Fila()
    f.adicionar(job())
    f.proximo(timeout=0)
    f.concluir("j1", "D:/F/x.mp4")
    j = f.obter("j1")
    assert j.estado is EstadoJob.CONCLUIDO and j.caminho_final == "D:/F/x.mp4"


def test_concluir_ilegal_nao_preenche_o_caminho():
    """Estado parcial: a transição é validada ANTES de gravar o caminho."""
    f = Fila()
    f.adicionar(job())
    with pytest.raises(TransicaoIlegal):
        f.concluir("j1", "D:/F/x.mp4")
    j = f.obter("j1")
    assert j.estado is EstadoJob.NA_FILA and j.caminho_final is None


def test_falhar_preenche_motivo_e_mensagem():
    f = Fila()
    f.adicionar(job())
    f.proximo(timeout=0)
    f.falhar("j1", motivo="privado", mensagem="Private video")
    j = f.obter("j1")
    assert j.estado is EstadoJob.FALHOU
    assert j.motivo_falha == "privado" and j.mensagem_falha == "Private video"


def test_interromper_em_andamento():
    f = Fila()
    f.adicionar(job())
    f.proximo(timeout=0)
    assert f.interromper_em_andamento() == ["j1"]
    assert f.obter("j1").estado is EstadoJob.INTERROMPIDO


def test_interromper_sem_nada_em_andamento():
    f = Fila()
    f.adicionar(job())
    assert f.interromper_em_andamento() == []
    assert f.obter("j1").estado is EstadoJob.NA_FILA


# ===========================================================================
# Progresso — o hook pode vir de outra thread
# ===========================================================================


def test_atualizar_progresso_substitui_o_objeto():
    f = Fila()
    f.adicionar(job())
    f.proximo(timeout=0)
    f.atualizar_progresso("j1", prog(1, 10))
    f.atualizar_progresso("j1", prog(5, 10))
    assert f.obter("j1").progresso == prog(5, 10)


def test_progresso_depois_de_terminar_e_ignorado():
    """Hook atrasado não ressuscita nem altera job terminal."""
    f = Fila()
    f.adicionar(job())
    f.proximo(timeout=0)
    f.concluir("j1", "x")
    f.atualizar_progresso("j1", prog(9, 10))
    j = f.obter("j1")
    assert j.estado is EstadoJob.CONCLUIDO
    assert j.progresso is None


def test_progresso_de_job_inexistente_nao_levanta():
    """Uma exceção dentro do hook derruba o download do yt-dlp."""
    Fila().atualizar_progresso("nao", prog())


def test_progresso_concorrente_de_varias_threads():
    """O caso 4 da RESEARCH 3.4 com threads reais. Sem sleep."""
    f = Fila()
    f.adicionar(job())
    f.proximo(timeout=0)
    erros = []

    def bater():
        try:
            for i in range(300):
                f.atualizar_progresso("j1", prog(i, 1000))
                f.instantaneo()
        except Exception as e:  # noqa: BLE001
            erros.append(e)

    ts = [threading.Thread(target=bater) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert erros == []
    final = f.obter("j1").progresso
    assert isinstance(final, Progresso) and 0 <= final.baixados <= 299


# ===========================================================================
# instantaneo / obter — cópias, não referências
# ===========================================================================


def test_instantaneo_devolve_copias():
    f = Fila()
    f.adicionar(job())
    copia = f.instantaneo()[0]
    copia.estado = EstadoJob.CONCLUIDO
    assert f.obter("j1").estado is EstadoJob.NA_FILA


def test_obter_devolve_copia():
    f = Fila()
    f.adicionar(job())
    f.obter("j1").estado = EstadoJob.CONCLUIDO
    assert f.obter("j1").estado is EstadoJob.NA_FILA


def test_obter_inexistente_e_none():
    assert Fila().obter("nao") is None


def test_instantaneo_preserva_ordem_de_chegada():
    f = Fila()
    for i in ("c", "a", "b"):
        f.adicionar(job(id_=i))
    assert [j.id for j in f.instantaneo()] == ["c", "a", "b"]


# ===========================================================================
# ETAPA 2 — aviso no job (decisoes 1, 4 e 5) e ja_existia (decisao 1)
# ===========================================================================


def test_avisar_grava_no_job_sem_mudar_o_estado():
    f = Fila()
    f.adicionar(job())
    f.avisar("j1", "a pasta do projeto esta profunda demais")
    j = f.obter("j1")
    assert j.aviso == "a pasta do projeto esta profunda demais"
    assert j.estado is EstadoJob.NA_FILA


def test_avisar_acumula_em_vez_de_sobrescrever():
    f = Fila()
    f.adicionar(job())
    f.avisar("j1", "primeiro")
    f.avisar("j1", "segundo")
    assert "primeiro" in f.obter("j1").aviso and "segundo" in f.obter("j1").aviso


def test_avisar_o_mesmo_texto_duas_vezes_nao_duplica():
    f = Fila()
    f.adicionar(job())
    f.avisar("j1", "igual")
    f.avisar("j1", "igual")
    assert f.obter("j1").aviso.count("igual") == 1


def test_avisar_job_inexistente_nao_levanta():
    """Mesmo espirito de atualizar_progresso: chamado de caminhos que nao
    podem quebrar."""
    Fila().avisar("nao", "x")


def test_concluir_pode_marcar_ja_existia():
    f = Fila()
    f.adicionar(job())
    f.proximo(timeout=0)
    f.concluir("j1", "D:/F/x.mp4", ja_existia=True)
    j = f.obter("j1")
    assert j.estado is EstadoJob.CONCLUIDO and j.ja_existia is True


def test_concluir_normal_nao_marca_ja_existia():
    f = Fila()
    f.adicionar(job())
    f.proximo(timeout=0)
    f.concluir("j1", "D:/F/x.mp4")
    assert f.obter("j1").ja_existia is False
