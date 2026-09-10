"""Escrita do config/projetos.yaml com os comentários preservados.

O arquivo é editado à mão e carrega avisos que valem mais que a formatação —
inclusive o de apontar `pasta` para fora do repositório. Um editor que apague
comentários silenciosamente é pior que não ter editor nenhum.
"""

from pathlib import Path

import pytest
import yaml

from src.storage.projetos_yaml import ConfigInvalida, adicionar, nomes, remover

ORIGINAL = """\
# Mapeamento de projeto/cliente para pasta de destino.
#
# RECOMENDACAO FORTE: apontar `pasta` para FORA do repositorio.

projetos:

  pessoal:
    nome: "Canal pessoal"
    pasta: "D:/FOOTAGE/pessoal"

  # Exemplo. Ajustar para os clientes reais.
  cliente_exemplo:
    nome: "Cliente Exemplo"
    pasta: "D:/FOOTAGE/cliente_exemplo"
"""


@pytest.fixture
def arquivo(tmp_path):
    alvo = tmp_path / "projetos.yaml"
    alvo.write_text(ORIGINAL, encoding="utf-8")
    return alvo


def carregar(alvo: Path) -> dict:
    return yaml.safe_load(alvo.read_text(encoding="utf-8"))["projetos"]


# ===========================================================================
# Adicionar
# ===========================================================================


def test_adicionar_preserva_todos_os_comentarios(arquivo):
    adicionar(arquivo, "cliente_novo", "Cliente Novo", "D:/FOOTAGE/novo")
    texto = arquivo.read_text(encoding="utf-8")
    assert "RECOMENDACAO FORTE" in texto
    assert "# Exemplo. Ajustar para os clientes reais." in texto
    assert texto.count("#") == ORIGINAL.count("#"), "nenhum comentário a mais nem a menos"


def test_adicionar_mantem_os_projetos_anteriores(arquivo):
    adicionar(arquivo, "cliente_novo", "Cliente Novo", "D:/FOOTAGE/novo")
    projetos = carregar(arquivo)
    assert set(projetos) == {"pessoal", "cliente_exemplo", "cliente_novo"}
    assert projetos["pessoal"]["pasta"] == "D:/FOOTAGE/pessoal"
    assert projetos["cliente_novo"] == {"nome": "Cliente Novo", "pasta": "D:/FOOTAGE/novo"}


def test_adicionar_escapa_caminho_do_windows(arquivo):
    """`D:\\FOOTAGE\\x` com barra invertida não pode virar escape de YAML."""
    adicionar(arquivo, "cru", "Cru", 'D:\\FOOTAGE\\cliente "x"')
    assert carregar(arquivo)["cru"]["pasta"] == 'D:\\FOOTAGE\\cliente "x"'


def test_adicionar_aceita_acento_no_rotulo(arquivo):
    adicionar(arquivo, "cliente", "Ação & Comunicação", "D:/FOOTAGE/a")
    assert carregar(arquivo)["cliente"]["nome"] == "Ação & Comunicação"


def test_adicionar_recusa_nome_repetido(arquivo):
    with pytest.raises(ConfigInvalida):
        adicionar(arquivo, "pessoal", "Outro", "D:/FOOTAGE/outro")


def test_adicionar_nao_quebra_arquivo_sem_quebra_de_linha_final(tmp_path):
    alvo = tmp_path / "projetos.yaml"
    alvo.write_text('projetos:\n  a:\n    nome: "A"\n    pasta: "D:/a"', encoding="utf-8")
    adicionar(alvo, "b", "B", "D:/b")
    assert set(carregar(alvo)) == {"a", "b"}


# ===========================================================================
# Remover
# ===========================================================================


def test_remover_tira_so_o_bloco_pedido(arquivo):
    remover(arquivo, "pessoal")
    projetos = carregar(arquivo)
    assert set(projetos) == {"cliente_exemplo"}
    assert projetos["cliente_exemplo"]["pasta"] == "D:/FOOTAGE/cliente_exemplo"


def test_remover_preserva_o_cabecalho_e_o_comentario_do_vizinho(arquivo):
    remover(arquivo, "pessoal")
    texto = arquivo.read_text(encoding="utf-8")
    assert "RECOMENDACAO FORTE" in texto
    assert "# Exemplo. Ajustar para os clientes reais." in texto


def test_remover_recusa_o_ultimo_projeto(arquivo):
    """carregar_projetos levanta com a lista vazia: remover o último deixaria
    a aplicação sem subir na próxima vez."""
    remover(arquivo, "pessoal")
    with pytest.raises(ConfigInvalida) as erro:
        remover(arquivo, "cliente_exemplo")
    assert "único projeto" in str(erro.value)
    assert set(carregar(arquivo)) == {"cliente_exemplo"}, "nada pode ter sido gravado"


def test_remover_inexistente_nao_toca_no_arquivo(arquivo):
    antes = arquivo.read_text(encoding="utf-8")
    with pytest.raises(ConfigInvalida):
        remover(arquivo, "nao_existe")
    assert arquivo.read_text(encoding="utf-8") == antes


def test_ciclo_completo_volta_ao_conjunto_original(arquivo):
    adicionar(arquivo, "temporario", "Temporário", "D:/FOOTAGE/tmp")
    remover(arquivo, "temporario")
    assert set(carregar(arquivo)) == {"pessoal", "cliente_exemplo"}
    assert "RECOMENDACAO FORTE" in arquivo.read_text(encoding="utf-8")


def test_nomes_devolve_na_ordem_do_arquivo(arquivo):
    assert nomes(arquivo) == ["pessoal", "cliente_exemplo"]


# ===========================================================================
# A rede de proteção
# ===========================================================================


def test_arquivo_ilegivel_nao_e_sobrescrito(tmp_path):
    """Se o YAML já estiver quebrado, o editor recusa em vez de piorar."""
    alvo = tmp_path / "projetos.yaml"
    quebrado = "projetos:\n  a:\n   - isto: nao\n  fecha: ]\n"
    alvo.write_text(quebrado, encoding="utf-8")
    with pytest.raises(Exception):
        adicionar(alvo, "b", "B", "D:/b")
    assert alvo.read_text(encoding="utf-8") == quebrado


def test_gravacao_nao_deixa_temporario_para_tras(arquivo):
    adicionar(arquivo, "cliente_novo", "Cliente Novo", "D:/FOOTAGE/novo")
    restos = [p.name for p in arquivo.parent.iterdir() if p.name != arquivo.name]
    assert restos == [], f"sobrou lixo na pasta: {restos}"
