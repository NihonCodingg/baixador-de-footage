"""Modelos do domínio.

Fronteira anticorrupção contra o info_dict do yt-dlp, que tem centenas de
campos, não é garantidamente um dict e muda entre versões (SPEC 5.5).

Ticket: T2.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .erros import TransicaoIlegal


class EstadoJob(str, Enum):
    NA_FILA = "na_fila"
    BAIXANDO = "baixando"
    CONCLUIDO = "concluido"
    FALHOU = "falhou"
    CANCELADO = "cancelado"
    INTERROMPIDO = "interrompido"


ESTADOS_TERMINAIS = frozenset(
    {
        EstadoJob.CONCLUIDO,
        EstadoJob.FALHOU,
        EstadoJob.CANCELADO,
        EstadoJob.INTERROMPIDO,
    }
)

# Transições legais. SPEC 10.2.
TRANSICOES = {
    EstadoJob.NA_FILA: {EstadoJob.BAIXANDO, EstadoJob.CANCELADO},
    EstadoJob.BAIXANDO: {EstadoJob.CONCLUIDO, EstadoJob.FALHOU, EstadoJob.INTERROMPIDO},
    EstadoJob.CONCLUIDO: set(),
    EstadoJob.FALHOU: set(),
    EstadoJob.CANCELADO: set(),
    EstadoJob.INTERROMPIDO: set(),
}


@dataclass(frozen=True)
class Formato:
    """Um stream disponível. Subconjunto do que o yt-dlp devolve.

    `largura` e `altura` existem separadas de `resolucao` porque o teto de
    qualidade é aplicado na MENOR dimensão, e para isso é preciso comparar as
    duas numericamente (SPEC 6.3). `resolucao` é a string de exibição.

    Ambas são opcionais: no fixture real, 12 dos 45 formatos não têm dimensão
    (são só-áudio) e 4 são storyboards.
    """

    format_id: str
    ext: str
    resolucao: str | None
    largura: int | None
    altura: int | None
    fps: float | None
    vcodec: str | None
    acodec: str | None
    tbr: float | None
    tamanho_bytes: int | None


def _inteiro_positivo(valor) -> int | None:
    """int > 0, ou None. Zero e valores inválidos viram None: width=0 é dado
    corrompido, não um vídeo de largura zero."""
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _inteiro_ou_zero(valor) -> int:
    try:
        return max(0, int(valor))
    except (TypeError, ValueError):
        return 0


def _inteiro_ou_none(valor) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _float_ou_none(valor) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _formato_de_dict(bruto: dict) -> Formato:
    """Converte UM formato do info_dict.

    Tudo por .get(): a chave `acodec` está AUSENTE em 2 dos 45 formatos do
    fixture real, e um acesso por colchete derrubaria a conversão inteira.
    """
    fps = bruto.get("fps")
    tbr = bruto.get("tbr")
    return Formato(
        format_id=str(bruto.get("format_id") or ""),
        ext=str(bruto.get("ext") or ""),
        resolucao=bruto.get("resolution"),
        largura=_inteiro_positivo(bruto.get("width")),
        altura=_inteiro_positivo(bruto.get("height")),
        fps=float(fps) if fps is not None else None,
        vcodec=bruto.get("vcodec"),
        acodec=bruto.get("acodec"),
        tbr=float(tbr) if tbr is not None else None,
        tamanho_bytes=_inteiro_positivo(bruto.get("filesize") or bruto.get("filesize_approx")),
    )


@dataclass(frozen=True)
class Video:
    """Metadados de um vídeo, obtidos sem baixar."""

    video_id: str
    extractor: str
    url_canonica: str
    titulo: str
    canal: str | None
    duracao_s: int | None
    thumbnail_url: str | None
    data_upload: str | None  # AAAAMMDD
    formatos: tuple[Formato, ...]

    @classmethod
    def de_info_dict(cls, info: dict) -> "Video":
        """Converte o info_dict do yt-dlp neste modelo.

        Puro: recebe um dict já pronto, não chama o yt-dlp.

        Storyboards (ext=mhtml) ficam FORA na conversão: são as miniaturas da
        barra de progresso, não mídia. Nenhum consumidor os quer, e deixá-los
        obrigaria cada um a lembrar de filtrar.

        `url_canonica` vem de webpage_url: o yt-dlp já canonicaliza. A nossa
        normalização serve à decisão barata antes da rede (SPEC 5.3).
        """
        brutos = info.get("formats") or []
        formatos = tuple(_formato_de_dict(f) for f in brutos if f.get("ext") != "mhtml")
        duracao = info.get("duration")
        return cls(
            video_id=str(info.get("id") or ""),
            extractor=str(info.get("extractor_key") or info.get("extractor") or ""),
            url_canonica=str(info.get("webpage_url") or info.get("original_url") or ""),
            titulo=str(info.get("title") or ""),
            canal=info.get("channel") or info.get("uploader"),
            duracao_s=int(duracao) if duracao is not None else None,
            thumbnail_url=info.get("thumbnail"),
            data_upload=info.get("upload_date"),
            formatos=formatos,
        )


@dataclass(frozen=True)
class Progresso:
    """Instantâneo de progresso.

    Imutável de propósito: o hook SUBSTITUI o objeto sob lock, nunca muta campo
    a campo (SPEC 10.4).
    """

    baixados: int
    total: int | None
    velocidade_bps: float | None
    eta_s: int | None

    @property
    def percentual(self) -> float | None:
        """0 a 100, ou None quando o total é desconhecido ou zero.

        Trava em 100: total_bytes_estimate pode ficar abaixo do real, e uma
        barra em 104% é pior que uma em 100%.
        """
        if not self.total or self.total <= 0:
            return None
        return min(100.0, self.baixados / self.total * 100.0)

    @classmethod
    def de_hook(cls, d: dict) -> "Progresso | None":
        """Lê o dicionário do progress hook do yt-dlp.

        Toda chave é opcional (RESEARCH 3.2): só .get(), nunca [ ]. Devolve
        None para status 'error', desconhecido ou ausente — "check this first
        and ignore unknown values", diz a docstring do yt-dlp.

        'finished' pode chegar sem nenhum 'downloading' antes e sem
        downloaded_bytes (arquivo já existia): nesse caso baixados = total.
        """
        if not isinstance(d, dict):
            return None
        status = d.get("status")
        if status not in ("downloading", "finished"):
            return None

        baixados = _inteiro_ou_zero(d.get("downloaded_bytes"))
        total = _inteiro_positivo(d.get("total_bytes")) or _inteiro_positivo(
            d.get("total_bytes_estimate")
        )

        if status == "finished":
            if not baixados and total:
                baixados = total
            return cls(
                baixados=baixados, total=total or baixados or None, velocidade_bps=None, eta_s=0
            )

        return cls(
            baixados=baixados,
            total=total,
            velocidade_bps=_float_ou_none(d.get("speed")),
            eta_s=_inteiro_ou_none(d.get("eta")),
        )


@dataclass
class Job:
    """Um trabalho na fila."""

    id: str
    video: Video
    perfil: str
    projeto: str
    estado: EstadoJob
    criado_em: datetime
    progresso: Progresso | None = None
    caminho_final: str | None = None
    motivo_falha: str | None = None
    mensagem_falha: str | None = None
    url_original: str | None = None
    ja_existia: bool = False  # o arquivo já estava no destino
    aviso: str | None = None  # texto não-bloqueante para a tela

    def transicionar(self, novo: EstadoJob) -> None:
        """Aplica uma transição, recusando as ilegais. SPEC 10.2.

        Valida ANTES de mutar: se levantar, o job fica exatamente como estava.
        Mesmo estado -> mesmo estado é ilegal por decisão do autor, para que
        um worker reenviando estado apareça como erro em vez de passar batido.
        """
        permitidas = TRANSICOES.get(self.estado, set())
        if novo not in permitidas:
            raise TransicaoIlegal(f"Transição ilegal: {self.estado.value} -> {novo.value}")
        self.estado = novo


def tem_video(formato: Formato) -> bool:
    """True se o formato carrega stream de vídeo.

    `vcodec` está presente em 45/45 formatos do fixture real, então aqui não há
    o problema de chave ausente que existe em `acodec`.
    """
    return formato.vcodec not in (None, "none", "")


def tem_audio(formato: Formato) -> bool:
    """True se o formato carrega stream de áudio.

    TRÊS ESTADOS, não dois: tem / não tem / DESCONHECIDO.

    Medido no fixture: a chave `acodec` está AUSENTE em 2 dos 45 formatos
    (233 e 234, manifests HLS com resolution='audio only'). Ausente significa
    "o yt-dlp ainda não sabe", não "não tem áudio".

    A regra:
      acodec == 'none'          -> não tem, explicitamente
      acodec com valor          -> tem
      acodec None (desconhecido) -> tem, SE o formato também não tiver vídeo;
                                    caso contrário assume que não tem
                                    (conservador: um formato de vídeo com
                                    codec de áudio desconhecido é tratado
                                    como só-vídeo, e o merge resolve)

    Tratar 'desconhecido' como 'não tem' faria 233 e 234 não serem nem vídeo
    nem áudio, e eles sumiriam da lista.
    """
    if formato.acodec == "none":
        return False
    if formato.acodec:
        return True
    # Desconhecido: só é áudio se não for vídeo.
    return not tem_video(formato)
