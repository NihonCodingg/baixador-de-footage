"""Testes de T1 — o adapter do yt-dlp.

A classe YoutubeDL é substituída por um dublê injetado na fábrica. O que se
testa é a lógica do adapter: montar as opções, registrar o hook, aplicar
sanitize_info, extrair o caminho final e traduzir erros. Nada toca a rede.

Referência: RESEARCH 1 e 3, SPEC 5.6.
"""

import pytest
from yt_dlp.utils import DownloadError, ExtractorError

from src.domain.erros import MotivoFalha
from src.download.adapter import Downloader
from src.download.traducao_erros import ErroDeDownload

URL = "https://www.youtube.com/watch?v=LzS8kB6lIm0"


def fabrica(info=None, erro=None):
    """Devolve uma classe-dublê de YoutubeDL com comportamento roteirizado.

    A classe registra cada instância criada em `instancias`, para os testes
    inspecionarem as opções recebidas e se o contexto foi fechado.
    """

    class YDLFalso:
        instancias = []

        def __init__(self, opcoes):
            self.opcoes = opcoes
            self.chamadas = []
            self.fechado = False
            YDLFalso.instancias.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.fechado = True
            return False

        def extract_info(self, url, download=False):
            self.chamadas.append((url, download))
            if erro is not None:
                raise erro
            return dict(info or {})

        def sanitize_info(self, dados):
            return {**dados, "_sanitizado": True}

    return YDLFalso


def embrulhar(original, msg="ERROR: x"):
    return DownloadError(msg, (type(original), original, None))


# ===========================================================================
# inspecionar
# ===========================================================================


def test_inspecionar_nao_baixa():
    F = fabrica(info={"id": "abc", "title": "t"})
    Downloader(fabrica_ydl=F).inspecionar(URL)
    assert F.instancias[0].chamadas == [(URL, False)]


def test_inspecionar_aplica_sanitize_info():
    """Sem sanitize_info o resultado não é serializável em JSON (RESEARCH 1.3)."""
    F = fabrica(info={"id": "abc"})
    r = Downloader(fabrica_ydl=F).inspecionar(URL)
    assert r["_sanitizado"] is True
    assert r["id"] == "abc"


def test_inspecionar_impede_playlist_e_fica_quieto():
    F = fabrica(info={})
    Downloader(fabrica_ydl=F).inspecionar(URL)
    o = F.instancias[0].opcoes
    assert o["noplaylist"] is True, "segunda defesa contra arrastar canal inteiro"
    assert o["quiet"] is True
    assert "progress_hooks" not in o


def test_inspecionar_traduz_erro():
    F = fabrica(erro=embrulhar(ExtractorError("Private video", expected=True)))
    with pytest.raises(ErroDeDownload) as exc:
        Downloader(fabrica_ydl=F).inspecionar(URL)
    assert exc.value.motivo is MotivoFalha.PRIVADO


def test_inspecionar_fecha_o_contexto_mesmo_com_erro():
    """Sem o with, sockets ficam abertos num worker de longa duração."""
    F = fabrica(erro=embrulhar(ExtractorError("x", expected=True)))
    with pytest.raises(ErroDeDownload):
        Downloader(fabrica_ydl=F).inspecionar(URL)
    assert F.instancias[0].fechado is True


def test_inspecionar_nao_engole_keyboard_interrupt():
    """except Exception não pega BaseException. Ctrl+C tem que subir."""
    F = fabrica(erro=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        Downloader(fabrica_ydl=F).inspecionar(URL)


# ===========================================================================
# baixar
# ===========================================================================

OPCOES = {
    "format": "bv*[width<=1080]+ba/b",
    "format_sort": ["res:1080"],
    "merge_output_format": "mp4",
    "postprocessors": [],
    "outtmpl": "D:/F/20260901 - Rush B [LzS8kB6lIm0].mp4",
    "noplaylist": True,
}
INFO_BAIXADO = {"id": "abc", "requested_downloads": [{"filepath": "D:/F/final.mp4"}]}


def test_baixar_chama_com_download_true():
    F = fabrica(info=INFO_BAIXADO)
    Downloader(fabrica_ydl=F).baixar(URL, OPCOES, lambda d: None)
    assert F.instancias[0].chamadas == [(URL, True)]


def test_baixar_devolve_o_caminho_final():
    F = fabrica(info=INFO_BAIXADO)
    caminho = Downloader(fabrica_ydl=F).baixar(URL, OPCOES, lambda d: None)
    assert caminho == "D:/F/final.mp4"
    assert isinstance(caminho, str)


def test_baixar_registra_o_hook_de_progresso():
    F = fabrica(info=INFO_BAIXADO)
    hook = lambda d: None  # noqa: E731
    Downloader(fabrica_ydl=F).baixar(URL, OPCOES, hook)
    assert F.instancias[0].opcoes["progress_hooks"] == [hook]


def test_baixar_repassa_as_opcoes_do_perfil():
    F = fabrica(info=INFO_BAIXADO)
    Downloader(fabrica_ydl=F).baixar(URL, OPCOES, lambda d: None)
    o = F.instancias[0].opcoes
    for chave in ("format", "format_sort", "merge_output_format", "postprocessors"):
        assert o[chave] == OPCOES[chave]


def test_baixar_nao_muta_o_dict_de_opcoes_do_chamador():
    F = fabrica(info=INFO_BAIXADO)
    copia = dict(OPCOES)
    Downloader(fabrica_ydl=F).baixar(URL, copia, lambda d: None)
    assert copia == OPCOES, "o adapter alterou o dict que recebeu"


def test_baixar_nunca_sobrescreve_footage():
    """SPEC 8.4: nunca sobrescrever. O domínio resolve colisão antes, mas entre
    a checagem e a gravação um arquivo pode aparecer. overwrites=False faz o
    yt-dlp recusar em vez de destruir."""
    F = fabrica(info=INFO_BAIXADO)
    Downloader(fabrica_ydl=F).baixar(URL, OPCOES, lambda d: None)
    assert F.instancias[0].opcoes["overwrites"] is False


def test_baixar_escapa_porcento_no_outtmpl():
    """Medido: '50%(x)s' num outtmpl literal vira '50NA'. Um título com '%'
    precisa chegar ao yt-dlp como '%%', que ele reduz a '%' de volta."""
    F = fabrica(info=INFO_BAIXADO)
    opcoes = {**OPCOES, "outtmpl": "D:/F/100% clutch [id].mp4"}
    Downloader(fabrica_ydl=F).baixar(URL, opcoes, lambda d: None)
    assert F.instancias[0].opcoes["outtmpl"] == "D:/F/100%% clutch [id].mp4"


def test_baixar_caminho_vem_de_filepath_quando_nao_ha_requested_downloads():
    F = fabrica(info={"id": "abc", "filepath": "D:/F/unico.mp4"})
    assert Downloader(fabrica_ydl=F).baixar(URL, OPCOES, lambda d: None) == "D:/F/unico.mp4"


def test_baixar_sem_caminho_nenhum_e_erro_classificado():
    """Download 'concluído' sem caminho seria histórico apontando para o nada."""
    F = fabrica(info={"id": "abc"})
    with pytest.raises(ErroDeDownload) as exc:
        Downloader(fabrica_ydl=F).baixar(URL, OPCOES, lambda d: None)
    assert exc.value.motivo is MotivoFalha.DESCONHECIDO
    assert "caminho" in str(exc.value).lower()


def test_baixar_traduz_erro_e_fecha_o_contexto():
    F = fabrica(erro=embrulhar(ExtractorError("This video is DRM protected", expected=True)))
    with pytest.raises(ErroDeDownload) as exc:
        Downloader(fabrica_ydl=F).baixar(URL, OPCOES, lambda d: None)
    assert exc.value.motivo is MotivoFalha.DRM
    assert F.instancias[0].fechado is True


def test_baixar_nao_engole_keyboard_interrupt():
    F = fabrica(erro=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        Downloader(fabrica_ydl=F).baixar(URL, OPCOES, lambda d: None)


def test_baixar_nao_toca_ignoreerrors():
    """Na API o padrão já é False, que faz os erros subirem (RESEARCH 1.4).
    Setar True faria o adapter engolir falhas e o histórico registrar sucesso
    onde não houve."""
    F = fabrica(info=INFO_BAIXADO)
    Downloader(fabrica_ydl=F).baixar(URL, OPCOES, lambda d: None)
    assert F.instancias[0].opcoes.get("ignoreerrors", False) is False


# ===========================================================================
# Sem injeção, a fábrica é o yt-dlp de verdade
# ===========================================================================


def test_fabrica_padrao_e_o_youtubedl_real():
    import yt_dlp

    assert Downloader()._fabrica is yt_dlp.YoutubeDL


# ===========================================================================
# validar_seletor — a checagem de sintaxe injetada na carga dos perfis
# ===========================================================================

from src.download.adapter import validar_seletor  # noqa: E402


def test_validar_seletor_aceita_os_quatro_perfis_reais():
    import yaml
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    perfis = yaml.safe_load((raiz / "config" / "perfis.yaml").read_text(encoding="utf-8"))["perfis"]
    for cfg in perfis.values():
        lim = cfg["limite_dimensao"]
        validar_seletor(cfg["format"].replace("{dim}", f"[height<={lim}]" if lim else ""))


def test_validar_seletor_recusa_colchete_desbalanceado():
    with pytest.raises(Exception):
        validar_seletor("bv*[height<=1080]+ba[[[")


def test_validar_seletor_nao_precisa_de_rede():
    """Só analisa a string: roda em milissegundos e sem sockets."""
    validar_seletor("ba/b")


# ===========================================================================
# Cookies do navegador
# ===========================================================================


def test_sem_cookies_por_padrao():
    """Desligado por padrão: ler cookie de navegador é intrusivo, e a
    maioria dos downloads não precisa."""
    YDL = fabrica(info={})
    d = Downloader(fabrica_ydl=YDL)
    d.inspecionar(URL)
    assert "cookiesfrombrowser" not in YDL.instancias[-1].opcoes
    assert d.cookies is None


def test_cookies_vao_para_a_inspecao():
    """O bloqueio antibot acontece já na inspeção, antes de baixar: os
    cookies precisam ir nas duas chamadas."""
    YDL = fabrica(info={})
    Downloader(fabrica_ydl=YDL, cookies=("firefox", None)).inspecionar(URL)
    assert YDL.instancias[-1].opcoes["cookiesfrombrowser"] == ("firefox",)


def test_cookies_vao_para_o_download():
    YDL = fabrica(info={"filepath": "C:/f/v.mp4"})
    d = Downloader(fabrica_ydl=YDL, cookies=("chrome", "Default"))
    d.baixar(URL, {"outtmpl": "C:/f/v.mp4"}, lambda x: None)
    assert YDL.instancias[-1].opcoes["cookiesfrombrowser"] == ("chrome", "Default")


def test_perfil_vazio_nao_entra_na_tupla():
    d = Downloader(fabrica_ydl=fabrica(info={}), cookies=("edge", None))
    assert d.cookies == ("edge",)


def test_desligar_cookies_em_tempo_de_execucao():
    """Mudar a opção pela tela vale para o próximo download, sem reiniciar."""
    YDL = fabrica(info={})
    d = Downloader(fabrica_ydl=YDL, cookies=("firefox", None))
    d.definir_cookies(None)
    d.inspecionar(URL)
    assert "cookiesfrombrowser" not in YDL.instancias[-1].opcoes


def test_a_lista_de_navegadores_vem_do_ytdlp():
    """Copiar a lista para cá deixaria a validação desatualizar em silêncio
    quando o yt-dlp mudar."""
    import yt_dlp.cookies
    from src.download.adapter import NAVEGADORES

    assert set(NAVEGADORES) == set(yt_dlp.cookies.SUPPORTED_BROWSERS)
    assert "firefox" in NAVEGADORES and "chrome" in NAVEGADORES
