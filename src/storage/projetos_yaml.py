"""Escreve config/projetos.yaml PRESERVANDO os comentários.

O arquivo é editado à mão e tem comentários que valem — inclusive o aviso
para apontar `pasta` para fora do repositório. Reserializar com o PyYAML
apagaria todos eles e reordenaria as chaves, então aqui a edição é
TEXTUAL: acrescenta um bloco no fim, remove um bloco por chave, e nada mais
no arquivo é tocado.

O `ruamel.yaml` faria isso nativamente, mas seria uma dependência a mais
para um arquivo de vinte linhas (requirements.txt: se não dá para
justificar, não entra).

Duas redes de proteção, porque um projetos.yaml corrompido derruba a
aplicação inteira na próxima subida:

1. O resultado é relido com o PyYAML e conferido ANTES de substituir o
   arquivo. Se não bater com o esperado, nada é gravado.
2. A gravação é atômica (arquivo temporário + os.replace), para uma queda
   no meio não deixar meio YAML no disco.

Ticket: gerenciamento de projetos pela interface.
"""

import json
import os
import re
import tempfile
from pathlib import Path

import yaml

INDENTACAO = "  "  # a chave do projeto vive com 2 espaços
_CHAVE = re.compile(r"^ {2}(?P<nome>[^\s:#][^:]*):\s*(?:#.*)?$")


class ConfigInvalida(Exception):
    """A edição produziria um YAML que não carrega. Nada foi gravado."""


def _escalar(texto: str) -> str:
    """Aspas duplas no estilo do YAML.

    `json.dumps` serve: a forma de aspas duplas do YAML aceita as mesmas
    escapes do JSON, e é o que trata caminho do Windows (`D:\\FOOTAGE`) sem
    virar escape acidental.
    """
    return json.dumps(str(texto), ensure_ascii=False)


def _ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8")


def _gravar_atomico(caminho: Path, texto: str) -> None:
    pasta = caminho.parent
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=pasta,
        delete=False,
        prefix=caminho.name,
        suffix=".tmp",
    ) as saida:
        saida.write(texto)
        temporario = Path(saida.name)
    os.replace(temporario, caminho)


def _conferir(texto: str, esperados: set[str]) -> None:
    """Relê o resultado e compara o conjunto de projetos com o esperado."""
    try:
        dados = yaml.safe_load(texto)
    except yaml.YAMLError as erro:
        raise ConfigInvalida(f"a edição geraria um YAML inválido: {erro}") from erro
    if not isinstance(dados, dict) or not isinstance(dados.get("projetos"), dict):
        raise ConfigInvalida("a edição deixaria o arquivo sem a chave 'projetos'.")
    obtidos = {str(k) for k in dados["projetos"]}
    if obtidos != esperados:
        raise ConfigInvalida(
            f"a edição mudaria os projetos de forma inesperada: "
            f"esperado {sorted(esperados)}, obtido {sorted(obtidos)}"
        )


def nomes(caminho: Path) -> list[str]:
    """Os nomes na ordem em que aparecem no arquivo."""
    dados = yaml.safe_load(_ler(caminho)) or {}
    return [str(k) for k in (dados.get("projetos") or {})]


def adicionar(caminho: Path, nome: str, rotulo: str, pasta: str) -> None:
    """Acrescenta um projeto no fim do arquivo. Não toca no que já existe."""
    texto = _ler(caminho)
    atuais = set(nomes(caminho))
    if nome in atuais:
        raise ConfigInvalida(f"o projeto {nome!r} já está no arquivo.")

    if not texto.endswith("\n"):
        texto += "\n"
    if not texto.endswith("\n\n"):
        texto += "\n"
    bloco = (
        f"{INDENTACAO}{nome}:\n"
        f"{INDENTACAO * 2}nome: {_escalar(rotulo)}\n"
        f"{INDENTACAO * 2}pasta: {_escalar(pasta)}\n"
    )

    novo = texto + bloco
    _conferir(novo, atuais | {nome})
    _gravar_atomico(caminho, novo)


def remover(caminho: Path, nome: str) -> None:
    """Tira o bloco do projeto, e só ele.

    O bloco vai da linha da chave até a próxima linha com indentação de 2 ou
    menos — o que deixa de fora um comentário que introduz o PRÓXIMO projeto,
    já que ele mora na coluna 2. Comentário nunca é apagado por engano: no
    máximo sobra um órfão, e sobrar é melhor que sumir.
    """
    texto = _ler(caminho)
    atuais = set(nomes(caminho))
    if nome not in atuais:
        raise ConfigInvalida(f"o projeto {nome!r} não está no arquivo.")
    if len(atuais) == 1:
        raise ConfigInvalida(
            "este é o único projeto do arquivo, e a aplicação não sobe sem "
            "nenhum. Cadastre outro antes de remover este."
        )

    linhas = texto.splitlines(keepends=True)
    inicio = next(
        (
            i
            for i, linha in enumerate(linhas)
            if (m := _CHAVE.match(linha.rstrip("\n")))
            and m.group("nome").strip().strip("\"'") == nome
        ),
        None,
    )
    if inicio is None:
        raise ConfigInvalida(
            f"o projeto {nome!r} existe no YAML mas não numa forma que este "
            "editor saiba remover. Edite o arquivo à mão."
        )

    fim = inicio + 1
    while fim < len(linhas):
        linha = linhas[fim].rstrip("\n")
        if linha.strip() and not linha.startswith(INDENTACAO * 2):
            break
        fim += 1

    novo = "".join(linhas[:inicio] + linhas[fim:])
    novo = re.sub(r"\n{3,}", "\n\n", novo)
    _conferir(novo, atuais - {nome})
    _gravar_atomico(caminho, novo)
