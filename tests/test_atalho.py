"""O atalho da área de trabalho (Baixador.bat).

Um .bat não tem como ser testado de verdade sem abrir janela, então o que se
trava aqui são as três coisas que o quebram em silêncio: apontar para a porta
errada, depender da pasta de onde foi chamado, e fechar o console engolindo a
mensagem de erro.
"""

from pathlib import Path

import pytest

from src.web.app import PORTA

RAIZ = Path(__file__).resolve().parent.parent
ATALHO = RAIZ / "Baixador.bat"


@pytest.fixture(scope="module")
def texto():
    assert ATALHO.exists(), "Baixador.bat sumiu da raiz do projeto"
    return ATALHO.read_text(encoding="utf-8")


def test_usa_a_mesma_porta_do_servidor(texto):
    """Trocar PORTA em src/web/app.py sem trocar aqui faria o atalho abrir o
    navegador num endereço vazio, sem erro nenhum."""
    assert f"set PORTA={PORTA}\n" in texto, (
        f"o atalho precisa apontar para a porta {PORTA} de src/web/app.py"
    )


def test_entra_na_propria_pasta(texto):
    """Sem isto, o duplo clique a partir de um atalho na área de trabalho
    rodaria com o diretório do atalho, e `python -m src.web` não acharia
    o pacote."""
    assert 'cd /d "%~dp0"' in texto


def test_a_janela_nao_fecha_quando_da_erro(texto):
    """O sintoma clássico: o console pisca e some, e o usuário não tem como
    saber que faltava instalar dependência."""
    assert "pause" in texto


def test_pergunta_a_api_antes_de_subir_de_novo(texto):
    """A checagem é pela API, não pela porta: outro programa na 8000 não pode
    passar por Baixador."""
    assert "/api/config" in texto


def test_e_ascii_puro(texto):
    """O cmd.exe lê o .bat na codepage OEM: acento aqui vira lixo na tela."""
    fora = sorted({c for c in texto if ord(c) > 127})
    assert not fora, f"caracteres não-ASCII no .bat: {fora}"


def test_nao_usa_pythonw(texto):
    """pythonw esconde o console — e junto com ele a mensagem de erro, que é
    justamente o que este atalho precisa preservar."""
    assert "pythonw" not in texto.lower()
