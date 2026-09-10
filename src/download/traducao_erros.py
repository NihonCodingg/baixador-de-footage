"""Traduz exceção do yt-dlp para MotivoFalha do domínio.

Fica aqui, e não no domínio, porque precisa conhecer as classes do yt-dlp
(SPEC 5.6). A taxonomia (MotivoFalha) e as mensagens em português vivem no
domínio; esta camada só faz o mapeamento.

O ponto central (RESEARCH 6.2): o yt-dlp embrulha quase tudo em DownloadError
e guarda a exceção real em `.exc_info`. Classificar olhando só o DownloadError
transforma tudo em "erro genérico".

A classificação por mensagem é frágil por natureza — o texto vem do site, não
do yt-dlp. Por isso é uma TABELA de dados com fallback obrigatório que mostra a
mensagem original. Quando o YouTube mudar o texto, a correção é uma linha de
tabela, e o usuário nunca fica sem informação porque o fallback mostrou o
original.

Ticket: T1.
"""

from dataclasses import dataclass, field

from yt_dlp.networking.exceptions import network_exceptions
from yt_dlp.utils import DownloadError, GeoRestrictedError, UnsupportedError

from ..domain.erros import MENSAGENS, RETENTAVEIS, MotivoFalha

# Substring (minúscula) -> motivo. A PRIMEIRA que casar vence, então o mais
# específico vem antes: "drm protected" antes de "video unavailable", porque a
# mensagem de DRM pode conter as duas.
# Origem de cada string documentada em RESEARCH 6.3; as de ffmpeg vêm de
# YoutubeDL.py:3544 e postprocessor/ffmpeg.py:225-234.
TABELA_MENSAGENS: list[tuple[str, MotivoFalha]] = [
    # ANTES de qualquer coisa com "cookie": a mensagem do bloqueio antibot
    # sugere --cookies-from-browser, e casaria com as entradas de cookie
    # abaixo. Sem apóstrofo na substring: o texto real usa U+2019 ("you’re"),
    # e comparar com o apóstrofo ASCII não casa.
    ("not a bot", MotivoFalha.BLOQUEIO_BOT),
    # Falhas de LEITURA dos cookies.
    #
    # A PRIMEIRA é a que realmente chega aqui: load_cookies (cookies.py:113)
    # embrulha TODA exceção em CookieLoadError('failed to load cookies') e
    # descarta a mensagem original. Por isso o pipeline testa a leitura na
    # hora de ESCOLHER o navegador — é lá que a causa real ainda existe.
    ("failed to load cookies", MotivoFalha.COOKIES),
    # As demais só aparecem no teste direto, sem o embrulho. Medidas no
    # yt-dlp 2026.08.19: banco travado pelo navegador aberto (cookies.py:363),
    # App-Bound Encryption do Chrome 127+ (cookies.py:1099), navegador
    # ausente, e o ValueError de extract_cookies_from_browser.
    ("could not copy chrome cookie database", MotivoFalha.COOKIES),
    ("failed to decrypt with dpapi", MotivoFalha.COOKIES),
    ("cookies database", MotivoFalha.COOKIES),
    ("unknown browser", MotivoFalha.COOKIES),
    ("unsupported platform", MotivoFalha.COOKIES),
    ("drm protected", MotivoFalha.DRM),
    ("ffmpeg is not installed", MotivoFalha.SEM_FFMPEG),
    ("ffmpeg not found", MotivoFalha.SEM_FFMPEG),
    ("private video", MotivoFalha.PRIVADO),
    # Duas formas, porque o YouTube usa as duas: "Video unavailable" e
    # "This video is unavailable". A segunda não casa com a primeira — o "is"
    # no meio quebra a substring, e o erro caía em DESCONHECIDO.
    ("video unavailable", MotivoFalha.INDISPONIVEL),
    ("video is unavailable", MotivoFalha.INDISPONIVEL),
    ("age-restricted", MotivoFalha.RESTRICAO_IDADE),
    ("confirm your age", MotivoFalha.RESTRICAO_IDADE),
    ("only available for registered users", MotivoFalha.RESTRICAO_IDADE),
    ("this content isn't available, try again later", MotivoFalha.RATE_LIMIT),
]


@dataclass(frozen=True)
class Classificacao:
    """Resultado de classificar(): motivo + mensagem original + detalhes.

    `detalhes` carrega o que o motivo tiver de específico: `paises` no
    bloqueio regional, `status_http` na falha de rede.
    """

    motivo: MotivoFalha
    mensagem_original: str
    detalhes: dict = field(default_factory=dict)

    @property
    def mensagem(self) -> str:
        """Texto legível em português, para a interface e o histórico.

        No DESCONHECIDO a mensagem original vai junto: é o fallback que impede
        o usuário de ficar sem informação quando o site muda o texto.
        """
        base = MENSAGENS[self.motivo]

        if self.motivo is MotivoFalha.BLOQUEIO_REGIONAL and self.detalhes.get("paises"):
            base += f" Disponível em: {', '.join(self.detalhes['paises'])}."
        elif self.motivo is MotivoFalha.REDE and self.detalhes.get("status_http"):
            base = f"Falha de rede (HTTP {self.detalhes['status_http']})."
        elif self.motivo is MotivoFalha.DESCONHECIDO:
            base += f" Detalhe: {self.mensagem_original}"

        return base

    @property
    def retentavel(self) -> bool:
        return self.motivo in RETENTAVEIS


class ErroDeDownload(Exception):
    """A única exceção que sai do adapter. Carrega a classificação.

    `original` guarda a exceção do yt-dlp para diagnóstico; a interface e o
    histórico usam só `classificacao`.
    """

    def __init__(self, classificacao: Classificacao, original: Exception | None = None):
        self.classificacao = classificacao
        self.original = original
        super().__init__(classificacao.mensagem)

    @property
    def motivo(self) -> MotivoFalha:
        return self.classificacao.motivo

    @property
    def retentavel(self) -> bool:
        return self.classificacao.retentavel


def desembrulhar(err: Exception) -> Exception:
    """Extrai a exceção original de dentro do DownloadError.

    Desce enquanto houver `exc_info` com uma exceção dentro: trouble() pode
    embrulhar um DownloadError que já embrulhava outro.
    """
    atual = err
    vistos: set[int] = set()
    while (
        isinstance(atual, DownloadError)
        and atual.exc_info
        and atual.exc_info[1] is not None
        and id(atual) not in vistos
    ):
        vistos.add(id(atual))
        atual = atual.exc_info[1]
    return atual


def _mensagem_de(err: Exception) -> str:
    """A mensagem limpa. ExtractorError tem `orig_msg` sem o prefixo de id e
    sem o boilerplate de "please report this issue" que str() acrescenta.
    Exceção sem texto nenhum vira o nome da classe — nunca string vazia."""
    texto = getattr(err, "orig_msg", None)
    if not texto:
        texto = str(err)
    if not texto or not texto.strip():
        texto = type(err).__name__
    return texto


def _excecao_de_rede(err: Exception):
    """A própria exceção se for de rede, ou a `cause` se ela for.

    Cobre o caso real de "Unable to download webpage": o tipo de fora é
    ExtractorError, mas a causa é HTTPError. Sem olhar a causa, uma falha de
    rede comum e retentável viraria DESCONHECIDO.
    """
    if isinstance(err, network_exceptions):
        return err
    causa = getattr(err, "cause", None)
    if isinstance(causa, network_exceptions):
        return causa
    if isinstance(err, (ConnectionError, TimeoutError)):
        return err
    return None


def classificar(err: Exception) -> Classificacao:
    """Devolve a Classificacao: motivo, mensagem original e detalhes.

    Primeiro por TIPO (confiável), depois por SUBSTRING da mensagem (frágil,
    tabela de dados), e por fim o fallback DESCONHECIDO — que SEMPRE devolve a
    mensagem original.
    """
    original = desembrulhar(err)
    mensagem = _mensagem_de(original)

    # --- por tipo -----------------------------------------------------------
    if isinstance(original, GeoRestrictedError):
        return Classificacao(
            MotivoFalha.BLOQUEIO_REGIONAL,
            mensagem,
            {"paises": list(original.countries or [])},
        )

    if isinstance(original, UnsupportedError):
        return Classificacao(MotivoFalha.SITE_NAO_SUPORTADO, mensagem)

    rede = _excecao_de_rede(original)
    if rede is not None:
        detalhes = {}
        status = getattr(rede, "status", None)
        if status:
            detalhes["status_http"] = status
        return Classificacao(MotivoFalha.REDE, mensagem, detalhes)

    if isinstance(original, OSError):
        # ConnectionError e TimeoutError já saíram acima; o que sobra é disco:
        # permissão negada, disco cheio, caminho inexistente.
        return Classificacao(MotivoFalha.DISCO, mensagem)

    # --- por mensagem ---------------------------------------------------------
    texto = mensagem.lower()
    for substring, motivo in TABELA_MENSAGENS:
        if substring in texto:
            return Classificacao(motivo, mensagem)

    # --- fallback ----------------------------------------------------------
    return Classificacao(MotivoFalha.DESCONHECIDO, mensagem)


def traduzir(err: Exception) -> ErroDeDownload:
    """classificar() embrulhado na exceção que o adapter levanta."""
    return ErroDeDownload(classificar(err), original=err)
