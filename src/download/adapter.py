"""Adapter do yt-dlp. Único módulo do projeto que faz `import yt_dlp`.

O que ele faz: monta as opções, registra o hook, aplica sanitize_info, extrai
o caminho final e traduz erros. O que ele NÃO faz: regra de negócio. Perfil,
nome de arquivo e destino chegam prontos do domínio.

Ticket: T1.
"""

from collections.abc import Callable

import yt_dlp
import yt_dlp.cookies

from ..domain.erros import MotivoFalha
from .traducao_erros import Classificacao, ErroDeDownload, traduzir

# A lista vem do próprio yt-dlp instalado, não de uma cópia nossa: quando ele
# ganhar ou perder um navegador, a validação acompanha sozinha.
NAVEGADORES = tuple(sorted(yt_dlp.cookies.SUPPORTED_BROWSERS))


def _caminho_final(info: dict) -> str | None:
    """Onde o yt-dlp deixou o arquivo.

    Depois do merge, `requested_downloads[0]['filepath']` é o caminho final
    (verificado no spike). Sem merge, alguns caminhos deixam só `filepath`.
    """
    pedidos = info.get("requested_downloads") or []
    if pedidos and isinstance(pedidos[0], dict) and pedidos[0].get("filepath"):
        return pedidos[0]["filepath"]
    return info.get("filepath") or None


class Downloader:
    """Fachada sobre o yt-dlp.

    A interface é deliberadamente pequena para que a fila (T5) possa ser
    testada com um dublê que implemente estes dois métodos, sem rede.

    `fabrica_ydl` é a classe YoutubeDL (ou um dublê nos testes). Injeção em
    vez de import direto no método, para o adapter ser testável sem rede.
    """

    def __init__(self, fabrica_ydl=None, cookies=None):
        self._fabrica = fabrica_ydl or yt_dlp.YoutubeDL
        self._cookies = None
        self.definir_cookies(cookies)

    def definir_cookies(self, navegador_e_perfil) -> None:
        """Liga ou desliga o --cookies-from-browser. `None` desliga.

        Aceita a tupla (navegador, perfil) que o pipeline lê da config. A
        troca vale para o PRÓXIMO download: o valor é lido a cada chamada, e
        não há estado do yt-dlp preso entre elas.
        """
        if not navegador_e_perfil:
            self._cookies = None
            return
        navegador, perfil = navegador_e_perfil
        if not navegador:
            self._cookies = None
            return
        # A tupla do yt-dlp é (navegador, perfil, keyring, container); os dois
        # últimos ficam de fora porque não têm uso aqui.
        self._cookies = (navegador, perfil) if perfil else (navegador,)

    @property
    def cookies(self):
        return self._cookies

    def _com_cookies(self, opcoes: dict) -> dict:
        """Acrescenta o cookiesfrombrowser, quando ligado.

        Vai nas DUAS chamadas: o bloqueio antibot acontece já na inspeção,
        antes de qualquer download.
        """
        if self._cookies:
            opcoes["cookiesfrombrowser"] = self._cookies
        return opcoes

    def inspecionar(self, url: str) -> dict:
        """extract_info(download=False) + sanitize_info.

        Devolve o info_dict serializável. Não baixa mídia.
        Levanta ErroDeDownload já classificado.
        """
        opcoes = {
            "quiet": True,
            "no_warnings": True,
            # Segunda defesa contra arrastar um canal inteiro. A primeira é a
            # validação de link no domínio (SPEC 5.3).
            "noplaylist": True,
            "skip_download": True,
            # ignoreerrors fica no padrão da API (False): os erros SOBEM, que é
            # o que queremos capturar (RESEARCH 1.4). Não mexer.
        }
        try:
            # `with` fecha o cache de conexões ao sair. Sem ele, sockets ficam
            # abertos num worker de longa duração (RESEARCH 1.1).
            with self._fabrica(self._com_cookies(opcoes)) as ydl:
                bruto = ydl.extract_info(url, download=False)
                # Sem sanitize_info o resultado não é serializável em JSON
                # (RESEARCH 1.3), e a API devolve JSON.
                return ydl.sanitize_info(bruto)
        except ErroDeDownload:
            raise
        except Exception as erro:  # nunca BaseException: Ctrl+C sobe
            raise traduzir(erro) from erro

    def baixar(self, url: str, opcoes: dict, ao_progredir: Callable[[dict], None]) -> str:
        """Baixa e devolve o caminho final do arquivo.

        `ao_progredir` é registrado como progress_hook. ATENÇÃO: pode ser
        chamado de outra thread (RESEARCH 3.4). Quem passa o callback é
        responsável por torná-lo thread-safe.

        `opcoes` é o dict montado por opcoes_ytdlp no domínio. Não é mutado.
        """
        efetivas = dict(opcoes)
        efetivas["progress_hooks"] = [ao_progredir]
        efetivas.setdefault("quiet", True)
        efetivas.setdefault("no_warnings", True)
        efetivas.setdefault("noplaylist", True)

        # Footage nunca é sobrescrito (SPEC 8.4). O domínio resolve colisão
        # antes, mas entre a checagem e a gravação um arquivo pode aparecer;
        # com overwrites=False o yt-dlp recusa em vez de destruir.
        # Se o arquivo já existir aqui, o yt-dlp dispara 'finished' sem baixar
        # (RESEARCH 3.2). O worker checa o destino ANTES e trata o caso como
        # sucesso com aviso (SPEC 9.3), então esta linha é a última defesa:
        # nunca destruir footage.
        efetivas["overwrites"] = False

        # continuedl fica no padrão do yt-dlp (retoma .part parcial), por
        # decisão registrada no SPEC 13.2 como dívida conhecida: se aparecer
        # arquivo corrompido sem explicação, é o primeiro suspeito.

        # O outtmpl chega como caminho LITERAL, já sanitizado pelo domínio. O
        # yt-dlp o trata como template: um "%(" no título viraria "NA"
        # (medido). Escapar "%" como "%%" faz o yt-dlp devolver "%" literal.
        if isinstance(efetivas.get("outtmpl"), str):
            efetivas["outtmpl"] = efetivas["outtmpl"].replace("%", "%%")

        try:
            with self._fabrica(self._com_cookies(efetivas)) as ydl:
                info = ydl.extract_info(url, download=True)
        except ErroDeDownload:
            raise
        except Exception as erro:
            raise traduzir(erro) from erro

        caminho = _caminho_final(info or {})
        if not caminho:
            # Download "concluído" sem caminho seria histórico apontando para
            # o nada. Falha explícita é melhor que sucesso falso.
            raise ErroDeDownload(
                Classificacao(
                    MotivoFalha.DESCONHECIDO,
                    "o yt-dlp concluiu sem informar o caminho do arquivo baixado",
                )
            )
        return str(caminho)


def testar_cookies(navegador: str, perfil: str | None = None) -> str | None:
    """Tenta LER os cookies agora. None se deu certo, o detalhe se não deu.

    Existe porque o yt-dlp, no caminho normal, embrulha toda falha de cookie
    em CookieLoadError('failed to load cookies') e joga fora a causa
    (cookies.py:113). Chamando o extrator direto, a causa real aparece: banco
    não encontrado, navegador aberto travando o arquivo, ou App-Bound
    Encryption do Chrome 127+ no Windows.

    Fica no adapter porque é o único módulo que conhece o yt-dlp (REGRA 1).
    """
    try:
        yt_dlp.cookies.extract_cookies_from_browser(navegador, perfil)
    except Exception as erro:  # noqa: BLE001 — qualquer falha
        texto = str(erro).strip() or type(erro).__name__
        return texto.split("\n")[0][:300]
    return None


def validar_seletor(seletor: str) -> None:
    """Levanta se a sintaxe do seletor de formato for inválida. Offline.

    É o `validar_seletor` injetado em carregar_perfis(): o domínio não pode
    importar yt_dlp (REGRA 1), então a checagem mora aqui.

    build_format_selector só analisa a string — não toca a rede.
    """
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
    try:
        ydl.build_format_selector(seletor)
    finally:
        ydl.close()
