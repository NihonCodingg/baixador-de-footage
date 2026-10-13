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
    from pathlib import Path

    import yaml

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


# ===========================================================================
# Arquivo de cookies (cookies.txt exportado do navegador)
# ===========================================================================
# O Brave/Chrome/Edge no Windows criptografam os cookies de um jeito que o
# yt-dlp não lê ("Failed to decrypt with DPAPI", medido com o Brave fechado).
# A saída é exportar um cookies.txt; os valores aqui são INVENTADOS.

from src.download.adapter import analisar_arquivo_cookies  # noqa: E402

CABECALHO = "# Netscape HTTP Cookie File\n\n"


def cookies_txt(caminho, nomes):
    linhas = [f".youtube.com\tTRUE\t/\tTRUE\t1999999999\t{n}\tvalor-falso-{n}" for n in nomes]
    caminho.write_text(CABECALHO + "\n".join(linhas) + "\n", encoding="utf-8")
    return caminho


LOGADO = ["LOGIN_INFO", "SAPISID", "SID", "VISITOR_INFO1_LIVE"]


def test_arquivo_de_cookies_vai_como_copia_e_tem_prioridade(tmp_path):
    """O yt-dlp REGRAVA o cookiefile ao fechar, sem trava. Inspeção e
    download podem rodar juntos: cada um recebe uma cópia própria, e o
    navegador é ignorado quando há arquivo."""
    arquivo = cookies_txt(tmp_path / "cookies.txt", LOGADO)
    YDL = fabrica(info={"requested_downloads": [{"filepath": "D:/x.mp4"}]})
    d = Downloader(fabrica_ydl=YDL, cookies=("brave", None), arquivo_cookies=arquivo)

    d.inspecionar(URL)
    d.baixar(URL, {"outtmpl": "D:/x.mp4"}, lambda _: None)

    for ydl in YDL.instancias:
        usado = ydl.opcoes["cookiefile"]
        assert usado != str(arquivo), "nunca o arquivo original"
        assert "cookiesfrombrowser" not in ydl.opcoes
    assert YDL.instancias[0].opcoes["cookiefile"] != YDL.instancias[1].opcoes["cookiefile"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cookies.txt"], "a cópia é apagada"


def test_cookies_renovados_pelo_youtube_voltam_para_o_arquivo(tmp_path):
    """O YouTube troca cookies durante o uso; o yt-dlp grava os novos na
    cópia. Sem levá-los de volta, o arquivo envelheceria a cada download."""
    arquivo = cookies_txt(tmp_path / "cookies.txt", LOGADO)
    base = fabrica(info={})

    class Renovador(base):
        def __exit__(self, *exc):
            with open(self.opcoes["cookiefile"], "a", encoding="utf-8") as f:
                f.write(".youtube.com\tTRUE\t/\tTRUE\t1999999999\tNOVO\trenovado\n")
            return super().__exit__(*exc)

    Downloader(fabrica_ydl=Renovador, arquivo_cookies=arquivo).inspecionar(URL)
    assert "renovado" in arquivo.read_text(encoding="utf-8")


def test_sem_o_arquivo_volta_ao_navegador(tmp_path):
    YDL = fabrica(info={})
    Downloader(fabrica_ydl=YDL, cookies=("firefox", None),
               arquivo_cookies=tmp_path / "nao_existe.txt").inspecionar(URL)
    assert YDL.instancias[-1].opcoes["cookiesfrombrowser"] == ("firefox",)
    assert "cookiefile" not in YDL.instancias[-1].opcoes


def test_arquivo_com_login_e_aceito(tmp_path):
    assert analisar_arquivo_cookies(cookies_txt(tmp_path / "c.txt", LOGADO)) is None


def test_arquivo_sem_login_info_e_apontado(tmp_path):
    """O caso real: o YouTube apaga o LOGIN_INFO quando troca os cookies, e
    uma cópia tirada de uma aba em uso fica só com cookies de visitante. O
    yt-dlp exige LOGIN_INFO + SAPISID para se considerar logado."""
    arquivo = cookies_txt(tmp_path / "c.txt", ["VISITOR_INFO1_LIVE", "__Secure-3PAPISID", "YSC"])
    motivo = analisar_arquivo_cookies(arquivo)
    assert motivo and "login" in motivo.lower()


def test_arquivo_que_nao_e_cookies_e_apontado(tmp_path):
    lixo = tmp_path / "c.txt"
    lixo.write_text("isto não é um arquivo de cookies\n", encoding="utf-8")
    assert analisar_arquivo_cookies(lixo)


def test_trecho_vira_download_ranges_sem_recomprimir():
    """O domínio entrega segundos; só o adapter conhece o download_ranges do
    yt-dlp. Sem force_keyframes_at_cuts: corte no keyframe, sem recompressão
    (decisão do autor — rápido, com alguns segundos de folga)."""
    YDL = fabrica(info={"requested_downloads": [{"filepath": "D:/x.mp4"}]})
    Downloader(fabrica_ydl=YDL).baixar(URL, {"outtmpl": "D:/x.mp4", "trecho_s": (90, 165)},
                                       lambda _: None)
    opcoes = YDL.instancias[-1].opcoes
    assert "trecho_s" not in opcoes
    assert list(opcoes["download_ranges"]({}, None)) == [{"start_time": 90, "end_time": 165}]
    assert not opcoes.get("force_keyframes_at_cuts")
