"""Perfis de qualidade: YAML -> configuração validada. Puro.

O yt-dlp aceita um seletor de formato; ele não tem conceito de perfil nomeado
nem valida nada (SPEC 5.2). A tradução "nome legível -> config validada" é o
produto.

Ticket: T2.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from .erros import PerfilInvalido
from .models import Formato


@dataclass(frozen=True)
class Perfil:
    nome: str
    descricao: str
    format: str  # template com o placeholder {dim}
    limite_dimensao: int | None  # teto aplicado na MENOR dimensão
    format_sort: tuple[str, ...]
    merge_output_format: str
    postprocessors: tuple[dict, ...]
    exige_ffmpeg: bool
    conversao: str | None = None  # chave de CONVERSOES, aplicada após o download


@dataclass(frozen=True)
class Conversao:
    """Um transcode para a timeline, feito pelo ffmpeg DEPOIS do download.

    Existe porque o yt-dlp não expressa isto: o FFmpegVideoConvertor dele só
    recebe o container. Dado puro — os argumentos do ffmpeg por etapa; quem
    monta a linha de comando inteira é src/download/conversao.py.

    `video_gpu` é tentado ANTES de `video_cpu`, com `entrada_gpu` antes do
    `-i`. Se a placa falhar, a CPU refaz do zero.
    """

    rotulo: str
    container: str
    video_cpu: tuple[str, ...]
    audio: tuple[str, ...]
    entrada_gpu: tuple[str, ...] = ()
    video_gpu: tuple[str, ...] | None = None


CONVERSOES = {
    # ProRes 422 HQ: profile 3, 10 bits 4:2:2, PCM 24 bits — o mezzanine
    # clássico. Lento: ~0,1 a 0,36x o tempo real numa RTX 3060 com 4K60, e
    # ~11 GB por minuto. Na GPU (Vulkan) a decodificação do VP9 fica na CPU:
    # decodificar na placa e devolver cada quadro custou mais (medido).
    "prores_422_hq": Conversao(
        rotulo="ProRes 422 HQ",
        container="mov",
        video_cpu=("-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0",
                   "-pix_fmt", "yuv422p10le"),
        audio=("-c:a", "pcm_s24le"),
        entrada_gpu=("-init_hw_device", "vulkan=vk:0", "-filter_hw_device", "vk"),
        video_gpu=("-c:v", "prores_ks_vulkan", "-profile:v", "3", "-vendor", "apl0",
                   "-vf", "format=yuv422p10le,hwupload", "-async_depth", "4"),
    ),
    # H.265 inteiro na placa: NVDEC descomprime o VP9, NVENC grava o HEVC, e
    # a CPU fica livre. Medido numa RTX 3060 com o 4K60 real: 2,3x o tempo
    # real no preset p1, com a mesma qualidade do p5 (PSNR igual até a 2ª
    # casa) e ~0,9 GB por minuto. `-cq 16` é alta qualidade; o VP9 de origem
    # já veio comprimido do YouTube. `hvc1` é a marca que o Premiere e o
    # QuickTime esperam para HEVC em .mp4.
    "hevc_rapido": Conversao(
        rotulo="H.265 (placa de vídeo)",
        container="mp4",
        # Sem NVIDIA: x264 na CPU. Lento, mas o download não se perde.
        video_cpu=("-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                   "-pix_fmt", "yuv420p"),
        audio=("-c:a", "aac", "-b:a", "320k"),
        entrada_gpu=("-hwaccel", "cuda", "-hwaccel_output_format", "cuda"),
        video_gpu=("-c:v", "hevc_nvenc", "-preset", "p1", "-rc", "vbr", "-cq", "16",
                   "-b:v", "0", "-maxrate", "200M", "-bufsize", "400M", "-tag:v", "hvc1"),
    ),
}


CONTAINERS_VALIDOS = frozenset({"mp4", "mkv", "webm", "m4a", "mp3", "opus"})

# Nomes aceitos na chave `key` de um postprocessor (sem o sufixo PP), copiados
# de yt_dlp/postprocessor/__init__.py da versão 2026.07.04. É uma rede de
# segurança para falhar na CARGA em vez de com KeyError no meio de um job.
# Não importa yt_dlp de propósito (REGRA 1); a checagem autoritativa, se um
# dia for necessária, entra injetada como `validar_seletor`.
# Tabela protegida do formatador: 22 nomes do yt-dlp em 7 linhas, não em 22.
# fmt: off
POSTPROCESSORS_CONHECIDOS = frozenset({
    "FFmpegExtractAudio", "FFmpegMetadata", "FFmpegVideoRemuxer",
    "FFmpegVideoConvertor", "FFmpegEmbedSubtitle", "FFmpegSubtitlesConvertor",
    "FFmpegThumbnailsConvertor", "FFmpegSplitChapters", "FFmpegConcat",
    "FFmpegCopyStream", "FFmpegMerger", "EmbedThumbnail", "MetadataParser",
    "MetadataFromField", "MetadataFromTitle", "ModifyChapters",
    "MoveFilesAfterDownload", "SponsorBlock", "XAttrMetadata",
    "ExecAfterDownload", "Exec",
})
# fmt: on


def campo_limite(formatos: Sequence[Formato]) -> str | None:
    """Decide se o teto de qualidade vai em `width` ou `height`. SPEC 6.3.

    O teto tem que cair na MENOR dimensão do vídeo. Num vídeo horizontal a
    menor é a altura; num vertical, a largura. Um filtro fixo [height<=1080]
    entrega 480x854 num Short — medido.

    A gramática de filtros do yt-dlp não tem OR, então "height<=1080 ou
    width<=1080" não é expressável. Resolvemos o campo aqui, em tempo de
    execução, a partir da orientação — que já conhecemos da inspeção, antes
    de qualquer download.

    Vídeo QUADRADO (largura == altura) devolve "height": os dois filtros
    selecionariam o mesmo conjunto, e "height" é a convenção de como se
    descreve qualidade de vídeo ("1080p" = 1080 linhas). A regra é: usa
    "width" apenas quando o vídeo é ESTRITAMENTE vertical.

    Devolve None quando nenhum formato tem dimensão (conteúdo só de áudio);
    nesse caso o filtro de dimensão é omitido do seletor.
    """
    com_dimensao = [f for f in formatos if f.largura and f.altura]
    if not com_dimensao:
        return None

    maior = max(com_dimensao, key=lambda f: f.largura * f.altura)
    return "width" if maior.altura > maior.largura else "height"


def resolver_dim(perfil: Perfil, formatos: Sequence[Formato]) -> str:
    """Constrói o trecho que substitui {dim} no template do perfil.

    Devolve "[height<=1080]", "[width<=1080]" ou "" (string vazia).
    """
    if perfil.limite_dimensao is None:
        return ""
    campo = campo_limite(formatos)
    if campo is None:
        return ""
    return f"[{campo}<={perfil.limite_dimensao}]"


def resolver_format(perfil: Perfil, formatos: Sequence[Formato]) -> str:
    """Aplica resolver_dim ao template e devolve o seletor final."""
    return perfil.format.replace("{dim}", resolver_dim(perfil, formatos))


def carregar_perfis(dados: dict, validar_seletor=None) -> dict[str, Perfil]:
    """Valida e converte o dicionário lido do YAML.

    Recebe um dict já carregado — NÃO lê arquivo (o domínio não toca disco).

    TUDO OU NADA: se um perfil for inválido, levanta PerfilInvalido e não
    devolve nada. Um conjunto meio-carregado faria a UI mostrar alguns perfis e
    omitir outros em silêncio.
    """
    if (
        not isinstance(dados, dict)
        or not isinstance(dados.get("perfis"), dict)
        or not dados["perfis"]
    ):
        raise PerfilInvalido("Configuração de perfis vazia ou sem a chave 'perfis'.")
    perfis: dict[str, Perfil] = {}
    for nome, bruto in dados["perfis"].items():
        perfis[str(nome)] = validar_perfil(str(nome), bruto, validar_seletor)
    return perfis


def validar_perfil(nome: str, bruto: dict, validar_seletor=None) -> Perfil:
    """Valida um perfil isolado. Regras em SPEC 6.2.

    `validar_seletor` é um callable opcional que recebe o seletor e levanta se
    a sintaxe for inválida. Entra INJETADO porque checar sintaxe exige
    `build_format_selector` do yt-dlp, e a REGRA 1 proíbe importá-lo aqui.
    Sem ele, a sintaxe não é checada e o domínio continua puro.
    """

    def falha(motivo: str) -> PerfilInvalido:
        return PerfilInvalido(f"Perfil {nome!r}: {motivo}")

    if not isinstance(bruto, dict) or not bruto:
        raise falha("definição vazia ou inválida.")

    formato = bruto.get("format")
    if not isinstance(formato, str) or not formato.strip():
        raise falha("'format' ausente ou vazio.")
    formato = formato.strip()
    if "," in formato:
        raise falha(
            "'format' contém vírgula. Vírgula baixa vários formatos e quebra "
            "a premissa de um arquivo por job (SPEC 6.2)."
        )

    container = bruto.get("merge_output_format")
    if container not in CONTAINERS_VALIDOS:
        raise falha(
            f"merge_output_format {container!r} desconhecido. "
            f"Válidos: {sorted(CONTAINERS_VALIDOS)}."
        )

    limite = bruto.get("limite_dimensao")
    if limite is not None and (
        isinstance(limite, bool) or not isinstance(limite, int) or limite <= 0
    ):
        raise falha(f"limite_dimensao deve ser inteiro positivo ou null, veio {limite!r}.")
    if "{dim}" in formato and limite is None:
        raise falha("'format' usa {dim}, mas limite_dimensao é null.")

    pps = bruto.get("postprocessors") or []
    if not isinstance(pps, list):
        raise falha("'postprocessors' deve ser uma lista.")
    for pp in pps:
        chave = pp.get("key") if isinstance(pp, dict) else None
        if not chave:
            raise falha("postprocessor sem 'key'.")
        if chave not in POSTPROCESSORS_CONHECIDOS:
            raise falha(
                f"postprocessor {chave!r} desconhecido. "
                f"Conhecidos: {sorted(POSTPROCESSORS_CONHECIDOS)}."
            )

    sort = bruto.get("format_sort") or []
    if not isinstance(sort, list) or not all(isinstance(x, str) for x in sort):
        raise falha("'format_sort' deve ser uma lista de strings.")

    exige_ffmpeg = bruto.get("exige_ffmpeg", True)
    if not isinstance(exige_ffmpeg, bool):
        raise falha("'exige_ffmpeg' deve ser true ou false.")

    conversao = bruto.get("conversao")
    if conversao is not None:
        if conversao not in CONVERSOES:
            raise falha(
                f"conversao {conversao!r} desconhecida. Conhecidas: {sorted(CONVERSOES)}."
            )
        if not exige_ffmpeg:
            raise falha("um perfil com conversao exige ffmpeg: quem converte é ele.")

    perfil = Perfil(
        nome=nome,
        descricao=str(bruto.get("descricao") or ""),
        format=formato,
        limite_dimensao=limite,
        format_sort=tuple(sort),
        merge_output_format=container,
        postprocessors=tuple(dict(pp) for pp in pps),
        exige_ffmpeg=exige_ffmpeg,
        conversao=conversao,
    )

    if validar_seletor is not None:
        # Checa a sintaxe com o {dim} resolvido numa amostra plausível.
        amostra = formato.replace("{dim}", f"[height<={limite}]" if limite else "")
        try:
            validar_seletor(amostra)
        except Exception as erro:
            raise falha(f"seletor de formato inválido: {erro}") from erro

    return perfil


AVISO_ABAIXO_DO_PERFIL = (
    "O perfil {perfil!r} aceita até {limite}p, mas o arquivo veio em {resolucao}. "
    "Ou o site não tinha essa qualidade, ou ela só existia num codec que este "
    "perfil não aceita."
)


def menor_dimensao(resolucao: str | None) -> int | None:
    """A menor dimensão de uma resolução "LARGURAxALTURA".

    É a MESMA medida que `campo_limite` usa para aplicar o teto (SPEC 6.3):
    comparar altura com o teto diria que um Short 1080x1920 veio em 1920 e
    passou folgado, quando a qualidade dele é 1080.

    Total: devolve None para qualquer coisa que não seja duas medidas
    positivas. O `finished` do yt-dlp pode não trazer dimensão nenhuma.
    """
    if not isinstance(resolucao, str):
        return None
    partes = resolucao.lower().split("x")
    if len(partes) != 2:
        return None
    try:
        largura, altura = (int(p.strip()) for p in partes)
    except ValueError:
        return None
    if largura <= 0 or altura <= 0:
        return None
    return min(largura, altura)


def aviso_resolucao(perfil: Perfil, resolucao: str | None) -> str | None:
    """Avisa quando o arquivo veio ABAIXO do teto do perfil. SPEC 7.

    O teto é um máximo, não uma promessa: receber menos não é erro, é o site
    não ter. Mas o editor precisa saber, porque o sintoma — "pedi 4K e veio
    1080p" — é indistinguível de um acerto quando ninguém compara.

    Devolve None quando não há o que comparar: perfil sem teto, ou resolução
    desconhecida. Avisar sem base seria pior que não avisar.
    """
    if perfil.limite_dimensao is None:
        return None
    menor = menor_dimensao(resolucao)
    if menor is None or menor >= perfil.limite_dimensao:
        return None
    return AVISO_ABAIXO_DO_PERFIL.format(
        perfil=perfil.nome, limite=perfil.limite_dimensao, resolucao=resolucao
    )


def caminho_convertido(destino: str, conversao: Conversao) -> str:
    """O destino do download com a extensão do container convertido.

    Só troca a extensão: o nome já passou pelo orçamento de 240 caracteres
    (SPEC 8.3), e `.mkv` e `.mov` têm o mesmo tamanho.
    """
    base, ponto, _ = destino.rpartition(".")
    return f"{base if ponto else destino}.{conversao.container}"


def disponivel(perfil: Perfil, tem_ffmpeg: bool) -> bool:
    """Se o perfil pode ser usado agora.

    Perfil que exige ffmpeg numa máquina sem ffmpeg fica INDISPONÍVEL, e isso
    é estado, não erro: a interface o mostra desabilitado, com o motivo.
    """
    return tem_ffmpeg or not perfil.exige_ffmpeg


def opcoes_ytdlp(perfil: Perfil, formatos: Sequence[Formato], destino: str) -> dict:
    """Monta o dicionário de opções do yt-dlp a partir do perfil.

    Recebe os formatos porque o seletor depende da orientação do vídeo
    (ver campo_limite). Puro: devolve um dict. Quem o usa é o adapter.

    `noplaylist` entra sempre: é a segunda linha de defesa contra arrastar um
    canal inteiro. A primeira é a validação de link (SPEC 5.3).
    """
    return {
        "format": resolver_format(perfil, formatos),
        "format_sort": list(perfil.format_sort),
        "merge_output_format": perfil.merge_output_format,
        "postprocessors": [dict(pp) for pp in perfil.postprocessors],
        "outtmpl": destino,
        "noplaylist": True,
    }
