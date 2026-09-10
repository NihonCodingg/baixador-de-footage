"""Lê e escreve config/cookies.yaml.

Ao contrário do projetos.yaml, este arquivo é REESCRITO inteiro a cada
mudança: os comentários dele são nossos, fazem parte do modelo abaixo, e
voltam iguais. O projetos.yaml carrega comentários do usuário, e por isso
é editado cirurgicamente.

São duas chaves. Não vale um editor cirúrgico para duas chaves.

Ticket: cookies do navegador.
"""

import os
import tempfile
from pathlib import Path

import yaml

MODELO = """\
# De onde tirar os cookies do navegador (o --cookies-from-browser do yt-dlp).
#
# ATENÇÃO: este arquivo é REESCRITO quando a opção é mudada pela tela, em
# Ajustes. Comentários que você acrescentar aqui não sobrevivem — ao
# contrário do projetos.yaml, que é editado preservando os seus.
#
# DESATIVADO POR PADRÃO. Ative só quando o YouTube começar a pedir
# confirmação de que você não é um robô: aí a ferramenta passa a usar a
# sessão que você já tem aberta no navegador, e o site para de pedir.
#
# navegador: null desativa. Aceita, conferido contra o yt-dlp na subida:
#   {suportados}
#
# perfil: opcional. Nome ou caminho do perfil do navegador; null usa o padrão.
#
# NO WINDOWS, com Chrome e derivados, dois problemas medidos:
#
#   1. Com o navegador ABERTO o arquivo de cookies fica travado, e o yt-dlp
#      falha com "Could not copy Chrome cookie database". Feche antes.
#   2. O Chrome 127+ cifra os cookies com App-Bound Encryption, e a leitura
#      pode falhar mesmo com ele fechado ("Failed to decrypt with DPAPI").
#
# O Firefox não tem nenhum dos dois. Se for usar cookies no Windows, ele é a
# escolha menos problemática.

navegador: {navegador}
perfil: {perfil}
"""


def _escalar(valor: str | None) -> str:
    if valor is None:
        return "null"
    return '"' + str(valor).replace("\\", "\\\\").replace('"', '\\"') + '"'


def ler(caminho: Path) -> tuple[str | None, str | None]:
    """(navegador, perfil). Arquivo ausente ou vazio = desativado.

    Nunca levanta por conteúdo: cookies são um acessório, e um YAML torto
    aqui não pode impedir a aplicação de subir. Vale o desativado.
    """
    try:
        dados = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None, None
    if not isinstance(dados, dict):
        return None, None

    navegador = dados.get("navegador")
    perfil = dados.get("perfil")
    navegador = str(navegador).strip().lower() or None if navegador else None
    perfil = str(perfil).strip() or None if perfil else None
    return navegador, perfil


def escrever(
    caminho: Path, navegador: str | None, perfil: str | None, suportados: list[str]
) -> None:
    """Reescreve o arquivo a partir do modelo. Gravação atômica."""
    texto = MODELO.format(
        suportados=", ".join(suportados),
        navegador=_escalar(navegador),
        perfil=_escalar(perfil if navegador else None),
    )
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=caminho.parent,
        delete=False,
        prefix=caminho.name,
        suffix=".tmp",
    ) as saida:
        saida.write(texto)
        temporario = Path(saida.name)
    os.replace(temporario, caminho)
