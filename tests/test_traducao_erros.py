"""Testes de T1 — tradução das exceções do yt-dlp para MotivoFalha.

Escritos ANTES da implementação. Nenhum teste toca a rede: as exceções são
construídas à mão, exatamente como o yt-dlp as levanta.

Referência: RESEARCH 6 e SPEC 5.6.
"""

import types

import pytest
from yt_dlp.networking.exceptions import HTTPError, TransportError
from yt_dlp.utils import (
    DownloadError,
    ExtractorError,
    GeoRestrictedError,
    PostProcessingError,
    UnsupportedError,
)

from src.domain.erros import MENSAGENS, RETENTAVEIS, MotivoFalha
from src.download.traducao_erros import (
    Classificacao,
    ErroDeDownload,
    classificar,
    desembrulhar,
    traduzir,
)


def http_error(status: int, reason: str = "Erro") -> HTTPError:
    """HTTPError exige um objeto de resposta; basta status, reason e close."""
    resposta = types.SimpleNamespace(status=status, reason=reason, close=lambda: None)
    return HTTPError(resposta)


def embrulhar(original: Exception, msg: str = "ERROR: algo") -> DownloadError:
    """Reproduz o que YoutubeDL.trouble() faz: DownloadError com exc_info."""
    return DownloadError(msg, (type(original), original, None))


# ===========================================================================
# desembrulhar — o coração do ticket (RESEARCH 6.2)
# ===========================================================================


def test_desembrulha_a_excecao_original_do_download_error():
    geo = GeoRestrictedError("bloqueado", countries=["BR"])
    assert desembrulhar(embrulhar(geo)) is geo


def test_download_error_sem_exc_info_devolve_ele_mesmo():
    de = DownloadError("ERROR: solto")
    assert desembrulhar(de) is de


def test_excecao_que_nao_e_download_error_devolve_ela_mesma():
    ex = ExtractorError("x", expected=True)
    assert desembrulhar(ex) is ex


def test_desembrulha_aninhado_ate_o_fundo():
    """trouble() pode embrulhar um DownloadError que já carrega exc_info."""
    geo = GeoRestrictedError("bloqueado", countries=["BR"])
    interno = embrulhar(geo)
    externo = DownloadError("ERROR: externo", (DownloadError, interno, None))
    assert desembrulhar(externo) is geo


# ===========================================================================
# Classificação por TIPO — confiável
# ===========================================================================


def test_geo_restrito_por_tipo_e_preserva_paises():
    c = classificar(embrulhar(GeoRestrictedError("bloqueado", countries=["US", "GB"])))
    assert c.motivo is MotivoFalha.BLOQUEIO_REGIONAL
    assert c.detalhes["paises"] == ["US", "GB"]
    assert "US" in c.mensagem and "GB" in c.mensagem


def test_geo_restrito_sem_lista_de_paises():
    c = classificar(embrulhar(GeoRestrictedError("bloqueado")))
    assert c.motivo is MotivoFalha.BLOQUEIO_REGIONAL
    assert c.detalhes.get("paises") in (None, [])


def test_site_nao_suportado_por_tipo():
    c = classificar(embrulhar(UnsupportedError("https://site.qualquer/x")))
    assert c.motivo is MotivoFalha.SITE_NAO_SUPORTADO
    assert "https://site.qualquer/x" in c.mensagem_original


def test_http_error_e_rede_com_status():
    c = classificar(embrulhar(http_error(404, "Not Found")))
    assert c.motivo is MotivoFalha.REDE
    assert c.detalhes["status_http"] == 404
    assert "404" in c.mensagem


def test_transport_error_e_rede():
    c = classificar(embrulhar(TransportError("Connection reset", cause=ConnectionResetError())))
    assert c.motivo is MotivoFalha.REDE


def test_extractor_error_com_causa_de_rede_e_rede():
    """O caso real de 'Unable to download webpage': o tipo de fora é
    ExtractorError, mas a causa é HTTPError. Sem olhar a causa, vira
    DESCONHECIDO — e é uma falha de rede comum, retentável."""
    ex = ExtractorError("Unable to download webpage", cause=http_error(503, "Unavailable"))
    c = classificar(embrulhar(ex))
    assert c.motivo is MotivoFalha.REDE
    assert c.detalhes["status_http"] == 503


@pytest.mark.parametrize("erro", [PermissionError("negado"), OSError(28, "No space left")])
def test_erro_de_sistema_de_arquivos_e_disco(erro):
    assert classificar(embrulhar(erro)).motivo is MotivoFalha.DISCO


# ===========================================================================
# Classificação por MENSAGEM — frágil, por isso tabela de dados
# Origem de cada string: RESEARCH 6.3
# ===========================================================================


@pytest.mark.parametrize(
    "mensagem,motivo",
    [
        ("Private video", MotivoFalha.PRIVADO),
        ("Video unavailable", MotivoFalha.INDISPONIVEL),
        ("This video is age-restricted; some formats may be missing", MotivoFalha.RESTRICAO_IDADE),
        ("Sign in to confirm your age", MotivoFalha.RESTRICAO_IDADE),
        ("This video is only available for registered users", MotivoFalha.RESTRICAO_IDADE),
        ("This video is DRM protected", MotivoFalha.DRM),
        ("This content isn't available, try again later", MotivoFalha.RATE_LIMIT),
        (
            "You have requested merging of multiple formats but ffmpeg is not installed",
            MotivoFalha.SEM_FFMPEG,
        ),
        (
            "ffmpeg not found. Please install or provide the path using --ffmpeg-location",
            MotivoFalha.SEM_FFMPEG,
        ),
        ("ffprobe and ffmpeg not found. Please install", MotivoFalha.SEM_FFMPEG),
    ],
)
def test_mensagens_reais_do_yt_dlp(mensagem, motivo):
    c = classificar(embrulhar(ExtractorError(mensagem, expected=True)))
    assert c.motivo is motivo, f"{mensagem!r} -> {c.motivo}"


def test_classificacao_por_mensagem_ignora_caixa():
    c = classificar(embrulhar(ExtractorError("PRIVATE VIDEO", expected=True)))
    assert c.motivo is MotivoFalha.PRIVADO


def test_drm_vence_quando_a_mensagem_tambem_diz_unavailable():
    """Ordem da tabela: o motivo mais específico vem primeiro."""
    c = classificar(
        embrulhar(ExtractorError("Video unavailable. This video is DRM protected", expected=True))
    )
    assert c.motivo is MotivoFalha.DRM


def test_drm_e_declarado_fora_do_escopo_na_mensagem():
    c = classificar(embrulhar(ExtractorError("This video is DRM protected", expected=True)))
    assert "escopo" in c.mensagem.lower()


def test_post_processing_error_de_ffmpeg():
    c = classificar(embrulhar(PostProcessingError("ffmpeg not found")))
    assert c.motivo is MotivoFalha.SEM_FFMPEG


# ===========================================================================
# O fallback — nunca "erro desconhecido" sem a mensagem original
# ===========================================================================


def test_desconhecido_preserva_a_mensagem_original():
    c = classificar(embrulhar(ExtractorError("Something new the site invented", expected=True)))
    assert c.motivo is MotivoFalha.DESCONHECIDO
    assert c.mensagem_original == "Something new the site invented"


def test_desconhecido_mostra_a_original_na_mensagem_legivel():
    """É o que impede o usuário de ficar sem informação quando o site muda o
    texto: a mensagem legível carrega a original."""
    c = classificar(embrulhar(ExtractorError("Something new", expected=True)))
    assert "Something new" in c.mensagem


def test_mensagem_original_vem_limpa_sem_boilerplate_de_bug_report():
    """str(ExtractorError) com expected=False acrescenta 'please report this
    issue on https://github.com/...'. O usuário não precisa disso na tela."""
    c = classificar(embrulhar(ExtractorError("Something new")))
    assert "please report" not in c.mensagem_original.lower()
    assert c.mensagem_original == "Something new"


def test_download_error_sem_exc_info_classifica_pela_propria_mensagem():
    c = classificar(DownloadError("ERROR: [youtube] abc: Private video"))
    assert c.motivo is MotivoFalha.PRIVADO


def test_excecao_generica_nao_estoura():
    c = classificar(RuntimeError("qualquer coisa"))
    assert c.motivo is MotivoFalha.DESCONHECIDO
    assert "qualquer coisa" in c.mensagem_original


def test_excecao_sem_texto_nao_gera_mensagem_vazia():
    c = classificar(RuntimeError())
    assert c.mensagem.strip()
    assert c.mensagem_original.strip()


# ===========================================================================
# Retry e o embrulho final
# ===========================================================================


@pytest.mark.parametrize("motivo", list(MotivoFalha))
def test_retentavel_bate_com_a_tabela_do_dominio(motivo):
    c = Classificacao(motivo=motivo, mensagem_original="x", detalhes={})
    assert c.retentavel is (motivo in RETENTAVEIS)


@pytest.mark.parametrize("motivo", list(MotivoFalha))
def test_todo_motivo_tem_mensagem_em_portugues(motivo):
    assert motivo in MENSAGENS and MENSAGENS[motivo].strip()


def test_traduzir_devolve_erro_de_download_com_a_classificacao():
    original = embrulhar(GeoRestrictedError("bloqueado", countries=["BR"]))
    erro = traduzir(original)
    assert isinstance(erro, ErroDeDownload)
    assert erro.classificacao.motivo is MotivoFalha.BLOQUEIO_REGIONAL
    assert erro.original is original


def test_str_do_erro_de_download_e_a_mensagem_legivel():
    erro = traduzir(embrulhar(ExtractorError("Private video", expected=True)))
    assert str(erro) == erro.classificacao.mensagem
    assert "privado" in str(erro).lower()


def test_erro_de_download_expoe_motivo_e_retentavel():
    erro = traduzir(embrulhar(http_error(500)))
    assert erro.motivo is MotivoFalha.REDE
    assert erro.retentavel is True


def test_classificacao_e_imutavel():
    c = Classificacao(motivo=MotivoFalha.REDE, mensagem_original="x", detalhes={})
    with pytest.raises(Exception):
        c.motivo = MotivoFalha.DRM


# ===========================================================================
# Bloqueio antibot do YouTube e falhas de cookie
#
# As mensagens abaixo são as REAIS, capturadas do yt-dlp 2026.08.19 contra o
# YouTube em 03/09/2026, e do código de cookies.py da mesma versão.
# ===========================================================================

# Repare no apóstrofo: é U+2019, não o ASCII. Casar por "you're" falharia.
BOT = (
    "Sign in to confirm you’re not a bot. Use --cookies-from-browser or "
    "--cookies for the authentication. See  "
    "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  "
    "for how to manually pass cookies."
)


def test_bloqueio_antibot_tem_motivo_proprio():
    """Era DESCONHECIDO, e despejava o texto em inglês do yt-dlp na tela."""
    c = classificar(ExtractorError(BOT))
    assert c.motivo is MotivoFalha.BLOQUEIO_BOT
    assert "robô" in c.mensagem
    assert "cookies" in c.mensagem.lower()
    assert "--cookies-from-browser" not in c.mensagem, "a tela não fala CLI"


def test_bloqueio_antibot_nao_vira_erro_de_cookie():
    """A mensagem do bloqueio CITA --cookies-from-browser: se a tabela
    olhasse "cookie" antes de "not a bot", o motivo sairia errado."""
    assert classificar(ExtractorError(BOT)).motivo is MotivoFalha.BLOQUEIO_BOT


def test_bloqueio_antibot_nao_e_retentavel():
    """Repetir sem cookies dá no mesmo; o caminho é ligar os cookies."""
    assert classificar(ExtractorError(BOT)).retentavel is False


@pytest.mark.parametrize(
    "texto",
    [
        "This video is unavailable",  # a forma que estava escapando
        "Video unavailable",
    ],
)
def test_video_indisponivel_nas_duas_formas(texto):
    """ "This video is unavailable" não casa com a substring "video
    unavailable" — o "is" no meio quebra. Caía em DESCONHECIDO."""
    assert classificar(ExtractorError(texto)).motivo is MotivoFalha.INDISPONIVEL


@pytest.mark.parametrize(
    "texto",
    [
        "Could not copy Chrome cookie database. See  https://github.com/...  for more info",
        "Failed to decrypt with DPAPI. See  https://github.com/...  for more info",
        "unknown browser: naoexiste",
        "unsupported platform: win32",
    ],
)
def test_falha_de_cookie_tem_motivo_proprio(texto):
    """Os quatro caminhos de falha de leitura de cookie, todos medidos no
    yt-dlp instalado. Sem isto o usuário via texto cru em inglês."""
    c = classificar(ExtractorError(texto))
    assert c.motivo is MotivoFalha.COOKIES
    assert "cookies" in c.mensagem.lower()
    assert "Ajustes" in c.mensagem
