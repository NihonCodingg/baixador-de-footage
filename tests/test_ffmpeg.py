"""Testes de T1 — detecção do ffmpeg.

shutil.which é substituído por monkeypatch nos testes de lógica. Um único
teste toca a máquina real, e é pulado onde o ffmpeg não existe.

Referência: RESEARCH 5.
"""

import shutil

import pytest

from src.download import ffmpeg
from src.download.ffmpeg import StatusFFmpeg, detectar


def which_falso(mapa: dict):
    def _which(nome, *args, **kwargs):
        return mapa.get(nome)

    return _which


def test_ambos_encontrados(monkeypatch):
    monkeypatch.setattr(
        ffmpeg.shutil,
        "which",
        which_falso({"ffmpeg": "C:/x/ffmpeg.EXE", "ffprobe": "C:/x/ffprobe.EXE"}),
    )
    s = detectar()
    assert s.disponivel and s.completo
    assert s.ffmpeg == "C:/x/ffmpeg.EXE"
    assert s.ffprobe == "C:/x/ffprobe.EXE"


def test_so_ffmpeg_sem_ffprobe(monkeypatch):
    """ffprobe falta com frequência quando alguém copia só um binário. O aviso
    'ffmpeg ok' com ffprobe ausente produz erro confuso lá na frente."""
    monkeypatch.setattr(ffmpeg.shutil, "which", which_falso({"ffmpeg": "C:/x/ffmpeg.EXE"}))
    s = detectar()
    assert s.disponivel is True
    assert s.completo is False
    assert s.ffprobe is None


def test_nenhum_encontrado(monkeypatch):
    monkeypatch.setattr(ffmpeg.shutil, "which", which_falso({}))
    s = detectar()
    assert s.disponivel is False and s.completo is False
    assert s.ffmpeg is None and s.ffprobe is None


def test_procura_pelo_nome_sem_extensao(monkeypatch):
    """No Windows, shutil.which resolve o .EXE via PATHEXT sozinho. Pedir
    'ffmpeg.exe' na mão quebraria em sistemas onde o binário não tem sufixo."""
    pedidos = []

    def _which(nome, *args, **kwargs):
        pedidos.append(nome)
        return None

    monkeypatch.setattr(ffmpeg.shutil, "which", _which)
    detectar()
    assert pedidos == ["ffmpeg", "ffprobe"]


def test_status_e_imutavel():
    s = StatusFFmpeg(ffmpeg="a", ffprobe="b")
    with pytest.raises(Exception):
        s.ffmpeg = "c"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg não está no PATH desta máquina")
def test_na_maquina_real_encontra_o_binario():
    """Critério de pronto do T1: caminhos reais na máquina de destino."""
    import os

    s = detectar()
    assert s.disponivel
    assert os.path.isfile(s.ffmpeg)
    assert "ffmpeg" in os.path.basename(s.ffmpeg).lower()
