"""O script do pacote portátil: só as partes puras (nada é baixado aqui)."""

import importlib.util
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("empacotar", RAIZ / "scripts" / "empacotar.py")
empacotar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(empacotar)

PTH_OFICIAL = "python314.zip\n.\n\n# Uncomment to run site.main() automatically\n#import site\n"


def test_pth_ganha_bibliotecas_a_raiz_e_o_site():
    linhas = empacotar.ajustar_pth(PTH_OFICIAL).splitlines()
    assert linhas[:2] == ["python314.zip", "."]
    assert "Lib\site-packages" in linhas and ".." in linhas
    assert linhas[-1] == "import site" and "#import site" not in linhas


def test_pth_ajustado_duas_vezes_nao_duplica():
    uma = empacotar.ajustar_pth(PTH_OFICIAL)
    assert empacotar.ajustar_pth(uma) == uma


def test_pacote_nunca_leva_os_dados_do_autor():
    """data/ tem o histórico e o cookies.txt (o login do autor);
    config/projetos.yaml tem as pastas dele. Nada disso pode ir para o
    computador de outra pessoa."""
    assert not any(i.startswith("data") for i in empacotar.COPIAR)
    assert "config/projetos.yaml" not in empacotar.COPIAR
    assert "config/cookies.yaml" in empacotar.COPIAR  # o padrão: desligado
    assert "navegador: null" in (RAIZ / "config" / "cookies.yaml").read_text(encoding="utf-8")


def test_so_escreve_dentro_de_dist():
    for alvo in (empacotar.PACOTE, empacotar.CACHE, empacotar.ZIP):
        assert alvo.is_relative_to(empacotar.DIST)
    assert empacotar.DIST == RAIZ / "dist"
