"""Testes de T5 — leitura do progress hook e agregação por stream.

O dicionário do hook é NÃO CONFIÁVEL: quase toda chave é opcional
(RESEARCH 3.2). E no caminho DASH multi-formato, vídeo e áudio baixam em
threads separadas, cada uma chamando o hook com seus próprios bytes
(RESEARCH 3.4, caso 4) — sem agregar, a barra de progresso pula entre streams.

Referência: SPEC 10.4.
"""

import threading

import pytest

from src.domain.models import Progresso
from src.queue.progresso import AgregadorProgresso

# ===========================================================================
# Progresso.percentual
# ===========================================================================


@pytest.mark.parametrize(
    "baixados,total,esperado",
    [
        (50, 100, 50.0),
        (0, 100, 0.0),
        (100, 100, 100.0),
        (0, None, None),  # total desconhecido: sem percentual, não NaN
        (0, 0, None),  # total zero: sem divisão por zero
        (150, 100, 100.0),  # estimativa abaixo do real: trava em 100
    ],
)
def test_percentual(baixados, total, esperado):
    p = Progresso(baixados=baixados, total=total, velocidade_bps=None, eta_s=None)
    assert p.percentual == esperado


# ===========================================================================
# Progresso.de_hook — toda chave é opcional
# ===========================================================================


def test_de_hook_downloading_completo():
    p = Progresso.de_hook(
        {
            "status": "downloading",
            "downloaded_bytes": 500,
            "total_bytes": 1000,
            "speed": 2048.0,
            "eta": 7,
        }
    )
    assert p == Progresso(baixados=500, total=1000, velocidade_bps=2048.0, eta_s=7)


def test_de_hook_usa_estimativa_quando_total_falta():
    p = Progresso.de_hook(
        {
            "status": "downloading",
            "downloaded_bytes": 10,
            "total_bytes": None,
            "total_bytes_estimate": 800,
        }
    )
    assert p.total == 800


def test_de_hook_sem_speed_nem_eta_vira_none():
    p = Progresso.de_hook({"status": "downloading", "downloaded_bytes": 10})
    assert p.velocidade_bps is None and p.eta_s is None and p.total is None


def test_de_hook_eta_fracionario_vira_inteiro():
    p = Progresso.de_hook({"status": "downloading", "downloaded_bytes": 1, "eta": 7.9})
    assert p.eta_s == 7


def test_de_hook_sem_downloaded_bytes_e_zero():
    assert Progresso.de_hook({"status": "downloading"}).baixados == 0


def test_de_hook_finished_marca_total_igual_ao_baixado():
    p = Progresso.de_hook({"status": "finished", "downloaded_bytes": 1000, "total_bytes": 1000})
    assert p.baixados == 1000 and p.total == 1000 and p.eta_s == 0


def test_de_hook_finished_sem_total_usa_o_baixado():
    p = Progresso.de_hook({"status": "finished", "downloaded_bytes": 1000})
    assert p.total == 1000


def test_de_hook_finished_quando_arquivo_ja_existia():
    """RESEARCH 3.2: arquivo já presente dispara 'finished' direto, sem nenhum
    'downloading' antes e sem downloaded_bytes."""
    p = Progresso.de_hook({"status": "finished", "filename": "x", "total_bytes": 500})
    assert p.baixados == 500 and p.total == 500


@pytest.mark.parametrize(
    "d",
    [
        {"status": "error"},
        {"status": "inventado"},
        {},
        {"downloaded_bytes": 5},
    ],
)
def test_de_hook_status_desconhecido_ou_erro_devolve_none(d):
    """'Check this first and ignore unknown values' — docstring do yt-dlp."""
    assert Progresso.de_hook(d) is None


def test_de_hook_nunca_levanta_keyerror():
    """Um KeyError dentro do hook derruba o download inteiro."""
    Progresso.de_hook({"status": "downloading", "info_dict": None, "speed": "lixo"})


# ===========================================================================
# AgregadorProgresso — soma por stream (format_id)
# ===========================================================================


def p(baixados, total, vel=None, eta=None):
    return Progresso(baixados=baixados, total=total, velocidade_bps=vel, eta_s=eta)


def test_agregador_com_um_stream_devolve_ele_mesmo():
    a = AgregadorProgresso()
    a.atualizar("137", p(500, 1000, 100.0, 5))
    assert a.total() == p(500, 1000, 100.0, 5)


def test_agregador_soma_dois_streams():
    """Vídeo (137) e áudio (140) chegando intercalados de threads diferentes."""
    a = AgregadorProgresso()
    a.atualizar("137", p(500, 1000, 100.0, 5))
    a.atualizar("140", p(200, 400, 50.0, 4))
    assert a.total() == p(700, 1400, 150.0, 5)


def test_agregador_substitui_e_nao_acumula_o_mesmo_stream():
    a = AgregadorProgresso()
    a.atualizar("137", p(100, 1000))
    a.atualizar("137", p(300, 1000))
    assert a.total().baixados == 300


def test_agregador_total_desconhecido_se_algum_stream_nao_sabe():
    """Somar só os totais conhecidos daria uma barra que passa de 100%."""
    a = AgregadorProgresso()
    a.atualizar("137", p(500, 1000))
    a.atualizar("140", p(200, None))
    assert a.total().total is None
    assert a.total().baixados == 700


def test_agregador_velocidade_soma_so_as_conhecidas():
    a = AgregadorProgresso()
    a.atualizar("137", p(1, 2, 100.0))
    a.atualizar("140", p(1, 2, None))
    assert a.total().velocidade_bps == 100.0


def test_agregador_eta_e_o_maior():
    """O job termina quando o stream mais lento terminar."""
    a = AgregadorProgresso()
    a.atualizar("137", p(1, 2, eta=3))
    a.atualizar("140", p(1, 2, eta=9))
    assert a.total().eta_s == 9


def test_agregador_vazio():
    assert AgregadorProgresso().total() == p(0, None)


def test_agregador_stream_sem_id_usa_chave_padrao():
    a = AgregadorProgresso()
    a.atualizar(None, p(5, 10))
    a.atualizar(None, p(6, 10))
    assert a.total().baixados == 6


def test_agregador_e_thread_safe():
    """Threads reais: o caso 4 da RESEARCH 3.4 de verdade."""
    a = AgregadorProgresso()
    erros = []

    def bater(fid):
        try:
            for i in range(200):
                a.atualizar(fid, p(i, 1000))
                a.total()
        except Exception as e:  # noqa: BLE001
            erros.append(e)

    ts = [threading.Thread(target=bater, args=(fid,)) for fid in ("137", "140", "251")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert erros == []
    assert a.total().baixados == 199 * 3
