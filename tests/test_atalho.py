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


def test_ja_rodando_abre_em_modo_app(texto):
    """O segundo duplo clique também abre a JANELA, não uma aba."""
    assert "--app=%ENDERECO%" in texto


def test_caminho_do_edge_fora_de_bloco_entre_parenteses(texto):
    """`%ProgramFiles(x86)%` dentro de um bloco ( ) fecha o parêntese no
    "(x86)" e o .bat quebra em silêncio. O caminho só pode aparecer em linha
    de nível zero."""
    profundidade = 0
    for linha in texto.splitlines():
        if "ProgramFiles(x86)" in linha:
            assert profundidade == 0, f"dentro de bloco: {linha.strip()}"
        if not linha.lstrip().lower().startswith("rem"):
            profundidade += linha.count("(") - linha.count(")") if "ProgramFiles" not in linha else 0


# ===========================================================================
# Modo app (src/web/app.py) e o ícone (scripts/criar_atalho.py)
# ===========================================================================

import importlib.util  # noqa: E402
import struct  # noqa: E402

from src.web.app import abrir_como_app  # noqa: E402


def test_abre_no_edge_em_modo_app():
    chamadas = []
    abrir_como_app("http://127.0.0.1:8000", existe=lambda c: True,
                   executar=chamadas.append, abrir_aba=lambda u: chamadas.append(("aba", u)))
    assert chamadas and "--app=http://127.0.0.1:8000" in chamadas[0]


def test_sem_edge_cai_na_aba_comum():
    abertas = []
    abrir_como_app("http://x", existe=lambda c: False, executar=None, abrir_aba=abertas.append)
    assert abertas == ["http://x"]


def test_edge_que_nao_inicia_cai_na_aba_comum():
    def falha(cmd):
        raise OSError("bloqueado")
    abertas = []
    abrir_como_app("http://x", existe=lambda c: True, executar=falha, abrir_aba=abertas.append)
    assert abertas == ["http://x"]


def _script_atalho():
    spec = importlib.util.spec_from_file_location("criar_atalho", RAIZ / "scripts" / "criar_atalho.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_ico_tem_os_quatro_tamanhos_em_png():
    """O Windows escolhe o tamanho conforme a tela: 16 na barra, 32/48 na área
    de trabalho, 256 no zoom do Explorer."""
    dados = _script_atalho().ico()
    reservado, tipo, quantidade = struct.unpack("<HHH", dados[:6])
    assert (reservado, tipo, quantidade) == (0, 1, 4)
    for i in range(quantidade):
        largura, _, _, _, _, bpp, tamanho, inicio = struct.unpack(
            "<BBBBHHII", dados[6 + 16 * i:22 + 16 * i])
        assert bpp == 32
        assert dados[inicio:inicio + 8] == b"\x89PNG\r\n\x1a\n"
        assert inicio + tamanho <= len(dados)


def test_icone_usa_as_cores_da_tela():
    m = _script_atalho()
    assert m.cor_em(32, 20) == m.ACENTO, "a haste da seta"
    assert m.cor_em(5, 32) == m.FUNDO
    assert m.cor_em(0.5, 0.5) == m.TRANSPARENTE, "o canto é arredondado"


def test_favicon_existe_e_e_servido_pela_pagina():
    html = (RAIZ / "web" / "index.html").read_text(encoding="utf-8")
    assert 'href="favicon.svg"' in html
    assert (RAIZ / "web" / "favicon.svg").exists()
