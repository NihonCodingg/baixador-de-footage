"""Testes de T6 — carga de config/projetos.yaml. Puro.

O domínio valida a ESTRUTURA (nome, pasta). Se a pasta existe e é gravável é
I/O, e fica com o pipeline.

Referência: SPEC 7.
"""

import pytest

from src.domain.erros import ProjetoInvalido
from src.domain.projetos import Projeto, carregar_projetos


def test_carrega_dois_projetos():
    p = carregar_projetos(
        {
            "projetos": {
                "pessoal": {"nome": "Canal pessoal", "pasta": "D:/FOOTAGE/pessoal"},
                "cliente_x": {"nome": "Cliente X", "pasta": "D:/FOOTAGE/cliente_x"},
            }
        }
    )
    assert set(p) == {"pessoal", "cliente_x"}
    assert p["pessoal"] == Projeto(
        nome="pessoal", rotulo="Canal pessoal", pasta="D:/FOOTAGE/pessoal"
    )


def test_rotulo_ausente_usa_a_chave():
    p = carregar_projetos({"projetos": {"pessoal": {"pasta": "D:/F"}}})
    assert p["pessoal"].rotulo == "pessoal"


@pytest.mark.parametrize("dados", [{}, {"outra": {}}, {"projetos": {}}, {"projetos": None}])
def test_yaml_degenerado(dados):
    with pytest.raises(ProjetoInvalido):
        carregar_projetos(dados)


@pytest.mark.parametrize("entrada", [{}, {"pasta": ""}, {"pasta": "   "}, {"nome": "x"}, "D:/F"])
def test_projeto_sem_pasta(entrada):
    with pytest.raises(ProjetoInvalido):
        carregar_projetos({"projetos": {"p": entrada}})


def test_carga_e_tudo_ou_nada():
    with pytest.raises(ProjetoInvalido):
        carregar_projetos({"projetos": {"bom": {"pasta": "D:/F"}, "ruim": {}}})


def test_pasta_e_normalizada_sem_barra_final():
    p = carregar_projetos({"projetos": {"p": {"pasta": "D:/FOOTAGE/pessoal/"}}})
    assert p["p"].pasta == "D:/FOOTAGE/pessoal"


def test_carrega_o_arquivo_real():
    """Lê config/projetos.yaml de verdade: editar o arquivo quebra o teste."""
    from pathlib import Path
    import yaml

    raiz = Path(__file__).resolve().parent.parent
    dados = yaml.safe_load((raiz / "config" / "projetos.yaml").read_text(encoding="utf-8"))
    p = carregar_projetos(dados)
    assert "pessoal" in p


def test_projeto_e_imutavel():
    p = Projeto(nome="a", rotulo="b", pasta="c")
    with pytest.raises(Exception):
        p.pasta = "d"
