"""Validação e normalização de link. Puro.

Três responsabilidades que o yt-dlp não cobre (SPEC 5.3):
  1. rejeitar URL de canal/playlist — download em massa está fora de escopo
  2. normalizar para forma canônica, para o histórico não duplicar
  3. deduplicar a lista colada pelo usuário

Escopo: conhece APENAS o YouTube. URL de outro site suportado pelo yt-dlp passa
adiante sem normalizar, com aviso. Só texto que não é URL vira LinkInvalido.
A limitação que isso cria está registrada no SPEC 5.3.

Ticket: T2.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .erros import LinkInvalido

# Identificador de vídeo do YouTube: 11 caracteres do alfabeto base64url.
# Confirmado no _VALID_URL do extractor: (?P<id>[0-9A-Za-z_-]{11})
TAMANHO_ID_YOUTUBE = 11
_ID = r"[0-9A-Za-z_-]{11}"

# Sem o prefixo "www.", que é removido antes da comparação.
# Tabela protegida do formatador: domínios agrupados por família.
# fmt: off
HOSTS_YOUTUBE = frozenset({
    "youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "youtube-nocookie.com",
})
# fmt: on

# /shorts/ID, /embed/ID, /live/ID, /v/ID, /e/ID — o id vem no caminho.
_CAMINHO_COM_ID = re.compile(r"^/(?:shorts|embed|live|v|e)/(" + _ID + r")(?:/|$)")
# youtu.be/ID — o id É o caminho.
_YOUTU_BE = re.compile(r"^/(" + _ID + r")(?:/|$)")

# Caminhos que identificam canal, playlist ou aba — nunca um vídeo.
_PREFIXOS_CANAL = ("/@", "/c/", "/channel/", "/user/", "/feed/", "/playlist")

# Aceita "youtu.be/ID" colado sem esquema: algo que pareça host.tld/...
_PARECE_HOST = re.compile(r"^[\w.-]+\.[a-z]{2,}(?:[/?#:]|$)", re.IGNORECASE)

AVISO_OUTRO_SITE = (
    "Link fora do YouTube: o download pode funcionar, mas a deduplicação e o "
    "histórico só reconhecem este endereço se ele for colado exatamente igual."
)


@dataclass(frozen=True)
class LinkNormalizado:
    """Resultado da normalização de UMA linha colada.

    O aviso viaja DENTRO do resultado, e não por print ou log, para que o T7
    consiga exibi-lo no cartão de preview. Mesma forma do CaminhoMontado (T3).

    `url` é None quando a linha é inválida; nesse caso `erro` explica.
    """

    original: str
    url: str | None
    video_id: str | None
    e_youtube: bool
    aviso: str | None = None
    erro: str | None = None

    @property
    def ok(self) -> bool:
        return self.url is not None


def _analisar(url: str):
    """urlparse com esquema leniente.

    Devolve (partes, texto_usado) ou None se não parece URL de jeito nenhum.
    Um id nu de 11 caracteres cai no None: não tem ponto nem barra.
    """
    texto = (url or "").strip()
    if not texto:
        return None
    if "://" not in texto:
        if not _PARECE_HOST.match(texto):
            return None
        texto = "https://" + texto
    partes = urlparse(texto)
    if partes.scheme not in ("http", "https"):
        return None
    if not partes.netloc or "." not in partes.netloc:
        return None
    return partes, texto


def _host(partes) -> str:
    host = partes.netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _e_youtube(partes) -> bool:
    return _host(partes) in HOSTS_YOUTUBE


def extrair_id(url: str) -> str | None:
    """Extrai o id de 11 caracteres de uma URL de vídeo do YouTube.

    Devolve None se a URL não for de vídeo do YouTube. Preserva a caixa: ids
    diferenciam maiúsculas, e um lower() aqui juntaria vídeos distintos.
    """
    analise = _analisar(url)
    if analise is None:
        return None
    partes, _ = analise
    if not _e_youtube(partes):
        return None

    if _host(partes) == "youtu.be":
        m = _YOUTU_BE.match(partes.path)
        return m.group(1) if m else None

    m = _CAMINHO_COM_ID.match(partes.path)
    if m:
        return m.group(1)

    # /watch?v=ID em todas as suas formas, com qualquer ordem de parâmetros.
    v = parse_qs(partes.query).get("v", [""])[0]
    if re.fullmatch(_ID, v):
        return v
    return None


def e_playlist_ou_canal(url: str) -> bool:
    """True se a URL aponta para canal, playlist ou aba de canal.

    Download em massa está fora de escopo (SPEC 2.2), e extract_info percorre
    a lista inteira — a recusa tem que ser ANTES da chamada de rede.

    Uma URL de vídeo que carrega &list= NÃO é playlist: tem vídeo
    identificável, e a lista é descartada na normalização.
    """
    analise = _analisar(url)
    if analise is None:
        return False
    partes, _ = analise
    if not _e_youtube(partes):
        return False
    if extrair_id(url) is not None:
        return False
    return (partes.path or "/").startswith(_PREFIXOS_CANAL)


def normalizar_link(url: str) -> LinkNormalizado:
    """Normaliza UMA URL. Levanta LinkInvalido.

    YouTube  -> canônico https://www.youtube.com/watch?v=ID
    Outro site suportado -> devolve a URL intacta, com aviso
    Não é URL, ou é canal/playlist -> LinkInvalido
    """
    original = (url or "").strip()
    analise = _analisar(original)
    if analise is None:
        raise LinkInvalido(f"Não é um link válido: {original!r}")
    partes, texto = analise

    if _e_youtube(partes):
        video_id = extrair_id(original)
        if video_id:
            return LinkNormalizado(
                original=original,
                url=f"https://www.youtube.com/watch?v={video_id}",
                video_id=video_id,
                e_youtube=True,
            )
        if e_playlist_ou_canal(original):
            raise LinkInvalido(
                "Link de canal ou playlist. Download em massa está fora de "
                "escopo — cole o link de um vídeo."
            )
        raise LinkInvalido(f"Link do YouTube sem vídeo identificável: {original!r}")

    return LinkNormalizado(
        original=original,
        url=texto,
        video_id=None,
        e_youtube=False,
        aviso=AVISO_OUTRO_SITE,
    )


def normalizar_lote(texto: str) -> tuple[LinkNormalizado, ...]:
    """Recebe o conteúdo do textarea, um link por linha.

    NUNCA levanta: cada linha inválida vira um LinkNormalizado com `erro`
    preenchido. Resultado parcial é requisito (SPEC 11.1) — um link ruim numa
    lista de dez não pode invalidar os outros nove.

    Ignora linhas vazias, apara espaços, e deduplica preservando a ordem de
    aparição. A chave de deduplicação é a URL canônica para links válidos e o
    texto original para inválidos.
    """
    resultados: list[LinkNormalizado] = []
    vistos: set[str] = set()

    for linha in (texto or "").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            resultado = normalizar_link(linha)
        except LinkInvalido as erro:
            resultado = LinkNormalizado(
                original=linha,
                url=None,
                video_id=None,
                e_youtube=False,
                erro=str(erro),
            )
        chave = resultado.url if resultado.ok else resultado.original
        if chave in vistos:
            continue
        vistos.add(chave)
        resultados.append(resultado)

    return tuple(resultados)
