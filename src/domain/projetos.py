"""Projetos: nome legível -> pasta de destino. Puro. SPEC 7.

O domínio valida a ESTRUTURA do YAML (nome, pasta) e o FORMATO de um nome
novo. Se a pasta existe e é gravável é I/O, e fica com o pipeline.

Ticket: T6; nome de projeto novo em SPEC 7.1.
"""

import re
from dataclasses import dataclass

from .erros import ProjetoInvalido

# O nome é chave de YAML, identificador na API e segmento de URL no
# DELETE /api/projetos/{nome}. Restringir aqui evita ter que escapar em três
# lugares depois.
NOME_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")

# Reservado para o destino avulso: um download que aponta para uma pasta
# digitada na hora, sem projeto cadastrado (SPEC 7.2).
NOME_AVULSO = "avulso"


@dataclass(frozen=True)
class Projeto:
    nome: str  # a chave no YAML; identifica o projeto na API
    rotulo: str  # o `nome:` do YAML; o que a interface mostra
    pasta: str  # destino, sem barra final


def carregar_projetos(dados: dict) -> dict[str, Projeto]:
    """Valida e converte o dicionário lido do YAML. Tudo ou nada.

    Levanta ProjetoInvalido.
    """
    if (
        not isinstance(dados, dict)
        or not isinstance(dados.get("projetos"), dict)
        or not dados["projetos"]
    ):
        raise ProjetoInvalido("Configuração de projetos vazia ou sem a chave 'projetos'.")

    projetos: dict[str, Projeto] = {}
    for chave, bruto in dados["projetos"].items():
        nome = str(chave)
        if not isinstance(bruto, dict):
            raise ProjetoInvalido(f"Projeto {nome!r}: definição inválida.")
        pasta = bruto.get("pasta")
        if not isinstance(pasta, str) or not pasta.strip():
            raise ProjetoInvalido(f"Projeto {nome!r}: 'pasta' ausente ou vazia.")
        projetos[nome] = Projeto(
            nome=nome,
            rotulo=str(bruto.get("nome") or nome),
            pasta=pasta.strip().rstrip("/\\"),
        )
    return projetos


def validar_nome(nome: str) -> str:
    """Nome de projeto novo, ou ProjetoInvalido com o motivo em português.

    Puro: não olha o disco nem os projetos existentes. Colisão e pasta são
    checagens do pipeline, que tem acesso aos dois.
    """
    nome = (nome or "").strip()
    if not nome:
        raise ProjetoInvalido("O nome do projeto não pode ficar em branco.")
    if nome.casefold() == NOME_AVULSO:
        raise ProjetoInvalido(f"{NOME_AVULSO!r} é reservado para downloads em pasta avulsa.")
    if not NOME_VALIDO.match(nome):
        raise ProjetoInvalido(
            "O nome do projeto aceita letras, números, hífen e sublinhado, "
            "começa por letra ou número e tem no máximo 40 caracteres. "
            f"Recebido: {nome!r}"
        )
    return nome
