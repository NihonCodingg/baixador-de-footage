"""config/cookies.yaml — leitura tolerante, escrita que devolve os comentários.

Ao contrário do projetos.yaml, este arquivo é reescrito inteiro: os
comentários são do modelo, não do usuário. O que se trava aqui é que eles
voltem, que o caminho do Windows não vire escape, e que arquivo torto
signifique "desativado" em vez de derrubar a aplicação.
"""

import pytest
import yaml

from src.storage.cookies_yaml import escrever, ler

NAVEGADORES = ["chrome", "firefox"]


@pytest.fixture
def arquivo(tmp_path):
    return tmp_path / "cookies.yaml"


# ===========================================================================
# Leitura
# ===========================================================================


def test_arquivo_ausente_e_desativado(arquivo):
    """Quem clona o repositório e não tem o arquivo não pode ver um erro."""
    assert ler(arquivo) == (None, None)


def test_le_navegador_e_perfil(arquivo):
    arquivo.write_text('navegador: "firefox"\nperfil: "default"\n', encoding="utf-8")
    assert ler(arquivo) == ("firefox", "default")


def test_navegador_nulo_e_desativado(arquivo):
    arquivo.write_text("navegador: null\nperfil: null\n", encoding="utf-8")
    assert ler(arquivo) == (None, None)


def test_normaliza_caixa_e_espaco(arquivo):
    arquivo.write_text('navegador: "  FireFox  "\n', encoding="utf-8")
    assert ler(arquivo)[0] == "firefox"


def test_yaml_torto_nao_derruba_a_aplicacao(arquivo):
    """Cookies são acessório: um arquivo quebrado vale desativado, não uma
    exceção na subida."""
    arquivo.write_text("navegador: [isto: nao\n  fecha: ]\n", encoding="utf-8")
    assert ler(arquivo) == (None, None)


def test_conteudo_que_nao_e_mapa_e_desativado(arquivo):
    arquivo.write_text("- uma\n- lista\n", encoding="utf-8")
    assert ler(arquivo) == (None, None)


# ===========================================================================
# Escrita
# ===========================================================================


def test_escrever_e_ler_de_volta(arquivo):
    escrever(arquivo, "firefox", "default", NAVEGADORES)
    assert ler(arquivo) == ("firefox", "default")


def test_escrever_traz_os_comentarios_do_modelo(arquivo):
    """Quem abrir o arquivo depois de mexer pela tela precisa continuar
    encontrando a explicação — inclusive a das armadilhas do Windows."""
    escrever(arquivo, "firefox", None, NAVEGADORES)
    texto = arquivo.read_text(encoding="utf-8")
    assert "App-Bound Encryption" in texto
    assert "Could not copy Chrome cookie database" in texto
    assert "chrome, firefox" in texto, "a lista aceita vem do yt-dlp instalado"


def test_desativar_apaga_o_perfil_junto(arquivo):
    """Perfil sem navegador não quer dizer nada, e confundiria na volta."""
    escrever(arquivo, "chrome", "Profile 2", NAVEGADORES)
    escrever(arquivo, None, "Profile 2", NAVEGADORES)
    assert ler(arquivo) == (None, None)


def test_perfil_com_caminho_do_windows_nao_vira_escape(arquivo):
    escrever(arquivo, "chrome", 'C:\\Users\\Pichau\\Perfil "2"', NAVEGADORES)
    assert ler(arquivo)[1] == 'C:\\Users\\Pichau\\Perfil "2"'


def test_escrita_e_atomica_e_nao_deixa_temporario(arquivo):
    escrever(arquivo, "firefox", None, NAVEGADORES)
    restos = [p.name for p in arquivo.parent.iterdir() if p.name != arquivo.name]
    assert restos == []


def test_o_resultado_e_yaml_valido(arquivo):
    escrever(arquivo, "firefox", "default", NAVEGADORES)
    dados = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
    assert dados == {"navegador": "firefox", "perfil": "default"}


def test_cria_a_pasta_se_precisar(tmp_path):
    alvo = tmp_path / "config" / "cookies.yaml"
    escrever(alvo, "firefox", None, NAVEGADORES)
    assert alvo.exists()
