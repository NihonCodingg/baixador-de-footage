"""Fixtures compartilhadas.

REGRA ABSOLUTA: nenhum teste toca a rede.

Os metadados vêm de spike_meta.json, gerado uma vez pelo spike.py da Fase 3.
A fila é testada com um dublê de downloader.
"""

import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SPIKE_META = RAIZ / "spike_meta.json"


@pytest.fixture(scope="session")
def info_dict_real():
    """info_dict de um vídeo real, capturado pelo spike.

    Pula o teste se o arquivo não existe — o spike é rodado manualmente
    (Portão 2), então quem clonar o repo pode não ter o arquivo.
    """
    if not SPIKE_META.exists():
        pytest.skip("spike_meta.json não encontrado. Gere com: python spike.py <URL>")
    return json.loads(SPIKE_META.read_text(encoding="utf-8"))


class DownloaderFalso:
    """Dublê do adapter. Não toca a rede nem o disco.

    Permite roteirizar progresso e falha para testar a fila (T5) de forma
    determinística.
    """

    def __init__(self, info=None, eventos=None, erro=None, caminho="C:/fake/v.mp4"):
        self.info = info or {}
        self.eventos = eventos or []
        self.erro = erro
        self.caminho = caminho
        self.chamadas = []

    def inspecionar(self, url):
        self.chamadas.append(("inspecionar", url))
        if self.erro:
            raise self.erro
        return self.info

    def baixar(self, url, opcoes, ao_progredir):
        self.chamadas.append(("baixar", url))
        for evento in self.eventos:
            ao_progredir(evento)
        if self.erro:
            raise self.erro
        return self.caminho


@pytest.fixture
def downloader_falso():
    return DownloaderFalso
