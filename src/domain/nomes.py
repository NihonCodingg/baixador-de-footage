"""Nomenclatura e sanitização de arquivo para Windows. Puro.

Esta é a justificativa mais forte da camada de domínio (SPEC 5.1).

O sanitize_filename do yt-dlp NÃO trata: nomes reservados do DOS, tamanho,
nome vazio, ponto/espaço final, comprimento do diretório de destino, nem
colisão. E o que ele faz — substituir proibidos por homóglifos Unicode de
largura total — é indesejado aqui: o nome deixa de ser digitável e buscável.

Medição decisiva (RESEARCH 7.3): gravar num arquivo chamado NUL não levanta
erro e os bytes desaparecem. Sanitização aqui é correção, não estilo.

Ticket: T3.
"""

import re
import unicodedata
from dataclasses import dataclass

from .erros import NomeImpossivel

# SPEC 8.2, regra 5. Microsoft: "avoid these names followed immediately by an
# extension; NUL.txt and NUL.tar.gz are both equivalent to NUL".
# Tabela protegida do formatador: cada linha é uma família de nomes reservados do DOS.
# fmt: off
NOMES_RESERVADOS = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
    # Windows trata os superscripts ISO-8859-1 como dígito.
    "COM\u00b9", "COM\u00b2", "COM\u00b3",
    "LPT\u00b9", "LPT\u00b2", "LPT\u00b3",
})
# fmt: on

# Mapeamento POR CARACTERE, não regra única (SPEC 8.2).
# Cada proibido carrega um significado diferente no título; achatar todos em
# "-" destrói informação:
#   "Highlights 2024/2025"  -> "Highlights 2024-2025"   (intervalo, colado)
#   "Gameplay | Rush B"     -> "Gameplay - Rush B"      (separador)
#   "Seleção: críticas"     -> "Seleção críticas"       (espaço, depois colapsa)
#   "Round1:Final"          -> "Round1 Final"           (sem colar palavras)
MAPA_PROIBIDOS = {
    "|": " - ",
    "/": "-",
    "\\": "-",
    ":": " ",
    "?": "",
    "*": "",
    '"': "",
    "<": "",
    ">": "",
}

# Teto do CAMINHO COMPLETO, não do nome (SPEC 8.3).
# 260 (MAX_PATH) menos 20 de folga para os temporários do yt-dlp, que são mais
# longos que o nome final: NOME.f616-drc.mp4.part custa +14 sobre NOME.mp4.
ORCAMENTO_CAMINHO = 240

# Abaixo disto sobrando para o título: AVISO, não erro. O download prossegue
# truncado; o usuário precisa é enxergar que a pasta está profunda demais.
MINIMO_TITULO_SEM_AVISO = 40

# Reserva para o sufixo de colisão " (2)" a " (99)", que entra DEPOIS do
# truncamento (SPEC 8.3). Sem ela, um título que couber exatamente no orçamento
# estoura o teto na primeira colisão.
RESERVA_COLISAO = 5

# Título usado quando o original não tem nenhum alfanumérico: vazio, só
# espaços, só pontuação ou só emoji (SPEC 8.2, regra 7).
# É "video" e não "video_{id}" porque o [{id}] do template já dá unicidade.
TITULO_FALLBACK = "video"


@dataclass(frozen=True)
class CaminhoMontado:
    """Resultado de montar_caminho.

    `aviso` é preenchido quando a pasta do projeto é profunda o bastante para
    espremer o título abaixo de MINIMO_TITULO_SEM_AVISO. Não é erro: o download
    prossegue truncado (SPEC 8.3).
    """

    caminho: str
    aviso: str | None = None


def sanitizar(titulo: str) -> str:
    """Regras 1 a 4 e 8 de SPEC 8.2.

    NÃO aplica o fallback (não conhece o id) e NÃO trunca (não conhece o
    destino). Pode devolver string vazia — quem trata isso é montar_nome.
    """
    if not titulo:
        return ""

    # 1. Controle primeiro. Se fosse depois do colapso, um \n viraria espaço
    #    e deixaria espaço sobrando.
    limpo = "".join(c for c in titulo if ord(c) >= 32 and ord(c) != 127)

    # 2. Mapeamento POR CARACTERE. Cada proibido carrega significado próprio.
    limpo = "".join(MAPA_PROIBIDOS.get(c, c) for c in limpo)

    # 3. Colapsar espaços — o mapeamento cria espaços duplos de propósito
    #    (":" vira " ", "|" vira " - ").
    limpo = re.sub(r"\s+", " ", limpo)

    # 4. Espaço e ponto do FIM. O Windows os remove em silêncio, o que faria o
    #    caminho gravado no histórico não ser o caminho do arquivo.
    #    No início, só espaço: ponto inicial é legal e intencional (".hitbox").
    limpo = limpo.lstrip(" ").rstrip(" .")

    # 8. NFC: "ç" tem duas representações Unicode. Sem normalizar, a mesma
    #    origem variando geraria dois nomes e dois registros no histórico.
    return unicodedata.normalize("NFC", limpo)


def tem_alfanumerico(texto: str) -> bool:
    """True se há ao menos um caractere de categoria Unicode L* ou N*.

    Critério do fallback (SPEC 8.2, regra 7): um nome sem nenhuma letra ou
    dígito é impossível de digitar numa busca.
    """
    return any(unicodedata.category(c)[0] in ("L", "N") for c in texto)


def e_reservado(nome_base: str) -> bool:
    """True se o nome-base é reservado no Windows.

    IGUALDADE, nunca prefixo. Um `startswith` aqui estragaria CONSOLE, CONTRA,
    NULO, COM10 e LPT0 — e "console" aparece o tempo todo em título de gaming.
    """
    return nome_base.upper() in NOMES_RESERVADOS


def aplicar_reservado(nome_base: str) -> str:
    """Sufixa `_` se o nome-base for reservado."""
    return f"{nome_base}_" if e_reservado(nome_base) else nome_base


def truncar_titulo(titulo: str, limite: int) -> str:
    """Corta o título para caber em `limite`. Só o título — data, id e
    extensão são invioláveis.

    O rstrip no fim não é detalhe: cortar "palavra final aqui" em 8 deixaria
    "palavra " com espaço no fim, que o Windows remove em silêncio.
    """
    if limite <= 0:
        return ""
    if len(titulo) <= limite:
        return titulo
    return titulo[:limite].rstrip(" .")


def montar_nome(titulo: str, video_id: str, data_upload: str | None, extensao: str) -> str:
    """Monta `{data} - {titulo} [{id}].{ext}`. SPEC 8.1.

    Sem data_upload, o template vira `{titulo} [{id}].{ext}`.
    NÃO trunca — quem trunca é montar_caminho, que conhece a pasta.
    """
    limpo = sanitizar(titulo)
    if not tem_alfanumerico(limpo):
        limpo = TITULO_FALLBACK

    prefixo = f"{data_upload} - " if data_upload else ""
    base = f"{prefixo}{limpo} [{video_id}]"
    return aplicar_reservado(base) + extensao


def _separador(pasta: str) -> str:
    return "" if pasta.endswith(("/", "\\")) else "/"


def montar_caminho(
    pasta_projeto: str, titulo: str, video_id: str, data_upload: str | None, extensao: str
) -> CaminhoMontado:
    """Caminho completo respeitando o orçamento de SPEC 8.3.

    Reserva o custo fixo primeiro; o que sobra é o orçamento do título.

    Puro: só monta a string. NÃO cria diretório nem toca o disco.
    """
    separador = _separador(pasta_projeto)
    fixo = len(pasta_projeto) + len(separador) + len(f" [{video_id}]") + len(extensao)
    if data_upload:
        fixo += len(f"{data_upload} - ")

    # RESERVA_COLISAO sai do orçamento porque o sufixo " (2)" é acrescentado
    # DEPOIS do truncamento, por resolver_colisao.
    orcamento = ORCAMENTO_CAMINHO - fixo - RESERVA_COLISAO

    if orcamento <= 0:
        raise NomeImpossivel(
            f"A pasta do projeto tem {len(pasta_projeto)} caracteres e não "
            f"sobra espaço para o nome do arquivo dentro do teto de "
            f"{ORCAMENTO_CAMINHO}. Use uma pasta de destino mais rasa."
        )

    aviso = None
    if orcamento < MINIMO_TITULO_SEM_AVISO:
        aviso = (
            f"A pasta do projeto está profunda demais: sobram apenas "
            f"{orcamento} caracteres para o título, abaixo do mínimo de "
            f"{MINIMO_TITULO_SEM_AVISO}. O nome será truncado."
        )

    limpo = sanitizar(titulo)
    if not tem_alfanumerico(limpo):
        limpo = TITULO_FALLBACK
    limpo = truncar_titulo(limpo, orcamento)

    nome = montar_nome(limpo, video_id, data_upload, extensao)
    return CaminhoMontado(f"{pasta_projeto}{separador}{nome}", aviso)


def resolver_colisao(caminho: str, existe) -> str:
    """Sufixo ` (2)`, ` (3)`... antes da extensão. SPEC 8.4.

    `existe` é injetado para o domínio permanecer puro e testável.

    A insensibilidade a maiúsculas vem de `existe`, NÃO daqui: no Windows o
    próprio os.path.exists já é case-insensitive. Manter aqui um conjunto
    comparado com sensibilidade a caixa seria sobrescrita silenciosa de
    footage — os IDs do YouTube diferenciam caixa e o sistema de arquivos não.
    """
    if not existe(caminho):
        return caminho

    # Separar extensão olhando só o último componente do caminho: uma pasta
    # com ponto no nome ("D:/F.old/video") não pode confundir o rpartition.
    corte = max(caminho.rfind("/"), caminho.rfind("\\"))
    prefixo, nome = caminho[: corte + 1], caminho[corte + 1 :]
    raiz_nome, ponto, ext = nome.rpartition(".")
    if ponto:
        raiz, extensao = prefixo + raiz_nome, f".{ext}"
    else:
        raiz, extensao = caminho, ""

    for n in range(2, 100):
        candidato = f"{raiz} ({n}){extensao}"
        if not existe(candidato):
            return candidato

    raise NomeImpossivel(
        f"Mais de 99 arquivos colidindo com {caminho!r}. "
        f"Isso indica problema de configuração, não uso normal."
    )
