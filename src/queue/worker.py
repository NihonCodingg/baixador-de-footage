"""Thread de trabalho. UM download por vez. SPEC 10.4.

O worker é deliberadamente burro: retira um job, registra o início no
histórico, pede ao `preparar` injetado as opções e o destino, chama o adapter
com um hook thread-safe, e registra o desfecho. Nenhuma regra de negócio
mora aqui — perfil, nome e caminho vêm resolvidos do pipeline.

Ticket: T5.
"""

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..domain.erros import MotivoFalha, TransicaoIlegal
from ..domain.models import Job, Progresso
from ..domain.perfis import Conversao
from ..download.traducao_erros import ErroDeDownload
from .progresso import AgregadorProgresso

AVISO_JA_EXISTIA = "O arquivo já existia no destino; o download foi pulado e nada foi sobrescrito."
AVISO_HISTORICO = "O download terminou, mas o histórico não pôde ser atualizado: {erro}"
AVISO_CONVERSAO = (
    "A conversão para {rotulo} falhou ({erro}). O arquivo baixado está intacto "
    "em {origem}."
)
FASE_CONVERTENDO = "convertendo"
AVISO_ORIGINAL = (
    "A conversão deu certo, mas o arquivo original não pôde ser apagado ({erro}). "
    "Pode apagá-lo à mão: {origem}"
)

# Intervalo em que o laço acorda para checar o pedido de parada.
_PASSO_S = 0.05


@dataclass(frozen=True)
class Preparacao:
    """O que o worker precisa para chamar o adapter: resolvido pelo pipeline
    (perfil -> opções, projeto + nome -> destino) e injetado como callable."""

    url: str
    opcoes: dict
    destino: str
    # Preenchidos só em perfil com conversão: o que fazer depois do download
    # e onde fica o arquivo final. O histórico aponta para ESTE caminho.
    conversao: Conversao | None = None
    destino_convertido: str | None = None


def estimar_restante(feitos: float, total: float | None, decorrido: float) -> int | None:
    """Segundos até o fim da conversão, pelo ritmo médio até agora.

    A conversão de 4K leva dezenas de minutos; sem estimativa, "--" no
    "restante" parece travamento. Só estima depois de ter base: com menos de
    1 s de vídeo ou 5 s de relógio, o ritmo ainda é ruído de arranque.
    """
    if not total or feitos < 1 or decorrido < 5 or feitos >= total:
        return None
    ritmo = feitos / decorrido               # segundos de vídeo por segundo de relógio
    return int((total - feitos) / ritmo)


def _tamanho_arquivo(caminho: str) -> int | None:
    try:
        return os.path.getsize(caminho)
    except OSError:
        return None


def _texto(erro: Exception) -> str:
    return str(erro).strip() or type(erro).__name__


class Worker:
    """Consome a fila numa única thread daemon.

    Downloader, histórico e `preparar` entram por injeção: os testes usam
    dublês e não tocam a rede.
    """

    def __init__(
        self,
        fila,
        downloader,
        historico,
        preparar: Callable[[Job], Preparacao],
        avaliar_resolucao: Callable[[Job, str | None], str | None] | None = None,
        converter: Callable[..., str] | None = None,
    ):
        self._fila = fila
        self._downloader = downloader
        self._historico = historico
        self._preparar = preparar
        # Injetado, como o `preparar`: decidir se a resolução entregue frustra
        # o perfil é regra de negócio, e nenhuma mora aqui. O worker só sabe
        # qual resolução chegou.
        self._avaliar_resolucao = avaliar_resolucao
        # (origem, destino, conversao, ao_progredir) -> caminho convertido.
        # Injetado: o teste não depende de um ffmpeg instalado.
        self._converter = converter
        self._thread: threading.Thread | None = None
        self._parar = threading.Event()

    @property
    def vivo(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def iniciar(self) -> None:
        """Idempotente: uma segunda chamada com a thread viva não cria outra."""
        if self.vivo:
            return
        self._parar.clear()
        self._thread = threading.Thread(target=self._laco, name="worker-download", daemon=True)
        self._thread.start()

    def parar(self, timeout: float = 5.0) -> None:
        """Sinaliza parada e aguarda até `timeout`.

        Um download em andamento não pode ser cancelado (SPEC 10.5). Se a
        thread não terminar a tempo, o job vira INTERROMPIDO na fila e no
        histórico — nunca concluído. Se o download terminar depois disso, a
        conclusão tardia é ignorada (a transição é ilegal).
        """
        self._parar.set()
        thread = self._thread
        if thread is None:
            return
        if thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout)
        if thread.is_alive():
            self._fila.interromper_em_andamento()
            try:
                self._historico.marcar_interrompidos()
            except Exception:  # noqa: BLE001 — já estamos encerrando
                pass

    # -------------------------------------------------------------- o laço

    def _laco(self) -> None:
        while not self._parar.is_set():
            job = self._fila.proximo(timeout=_PASSO_S)
            if job is None:
                continue
            try:
                self._processar(job)
            except Exception as erro:  # noqa: BLE001 — a thread nunca morre
                self._falhar(
                    job, MotivoFalha.DESCONHECIDO.value, f"erro interno do worker: {_texto(erro)}"
                )

    def _processar(self, job: Job) -> None:
        # 1. A linha `baixando` no histórico, ANTES do download: é o que
        #    permite marcar `interrompido` se o programa fechar no meio.
        #    Conservador: sem registro, sem download. Um arquivo sem linha no
        #    histórico contradiz "sei onde cada arquivo está".
        try:
            registro = self._historico.iniciar(
                job.video,
                perfil=job.perfil,
                projeto=job.projeto,
                url_original=job.url_original or job.video.url_canonica,
            )
        except Exception as erro:  # noqa: BLE001
            self._falhar(job, MotivoFalha.DISCO.value, f"histórico indisponível: {_texto(erro)}")
            return
        registro_id = getattr(registro, "id", None)

        # 2. Opções e destino, resolvidos pelo pipeline. Pasta profunda demais
        #    (NomeImpossivel) ou perfil inválido são falha DESTE job.
        try:
            preparacao = self._preparar(job)
        except Exception as erro:  # noqa: BLE001
            self._falhar(job, MotivoFalha.DESCONHECIDO.value, _texto(erro), registro_id=registro_id)
            return

        # O caminho pretendido vai para o histórico ANTES do download: sem
        # ele, um `interrompido` não tem onde ser procurado na subida
        # seguinte (decisão 5).
        if registro_id is not None:
            try:
                self._historico.registrar_destino(registro_id, preparacao.destino)
            except Exception:  # noqa: BLE001
                pass

        # O arquivo já está lá (corrida entre a resolução de colisão e agora).
        # Não é falha: o footage está no destino. Mas o usuário precisa saber
        # que nada foi baixado (decisão 1).
        if os.path.exists(preparacao.destino):
            self._concluir(job, preparacao.destino, None, registro_id, ja_existia=True)
            return

        # 3. O hook: pode vir de outra thread (RESEARCH 3.4), dispara muitas
        #    vezes por segundo, e NUNCA pode levantar — uma exceção aqui
        #    derruba o download do yt-dlp. Só calcula e substitui.
        agregador = AgregadorProgresso()
        resolucao: dict[str, str | None] = {"valor": None}

        def ao_progredir(d: dict) -> None:
            try:
                info = d.get("info_dict") if isinstance(d, dict) else None
                info = info if isinstance(info, dict) else {}
                if (
                    isinstance(d, dict)
                    and d.get("status") == "finished"
                    and info.get("width")
                    and info.get("height")
                ):
                    # O 'finished' do formato mesclado traz a resolução REAL:
                    # é o que denuncia um fallback abaixo do perfil.
                    resolucao["valor"] = f"{info['width']}x{info['height']}"
                progresso = Progresso.de_hook(d)
                if progresso is None:
                    return
                agregador.atualizar(info.get("format_id"), progresso)
                self._fila.atualizar_progresso(job.id, agregador.total())
            except Exception:  # noqa: BLE001 — o hook nunca levanta
                pass

        # 4. O download.
        try:
            caminho = self._downloader.baixar(preparacao.url, preparacao.opcoes, ao_progredir)
        except ErroDeDownload as erro:
            self._falhar(
                job,
                erro.motivo.value,
                erro.classificacao.mensagem_original,
                registro_id=registro_id,
            )
            return
        except Exception as erro:  # noqa: BLE001 — bug no adapter não mata a thread
            self._falhar(job, MotivoFalha.DESCONHECIDO.value, _texto(erro), registro_id=registro_id)
            return

        # 5. A conversão, se o perfil pedir. Falhar aqui NÃO falha o job: o
        #    footage baixado existe e é bom. Conclui apontando para ele, e o
        #    aviso diz o que aconteceu.
        if preparacao.conversao is not None and preparacao.destino_convertido:
            caminho = self._converter_baixado(job, caminho, preparacao)

        self._concluir(job, caminho, resolucao["valor"], registro_id)

    def _converter_baixado(self, job: Job, origem: str, preparacao: Preparacao) -> str:
        """Devolve o caminho convertido, ou a ORIGEM se a conversão falhar."""
        conversao = preparacao.conversao
        if self._converter is None:
            self._fila.avisar(job.id, AVISO_CONVERSAO.format(
                rotulo=conversao.rotulo, erro="nenhum conversor configurado", origem=origem))
            return origem

        self._fila.marcar_fase(job.id, FASE_CONVERTENDO)
        duracao = job.video.duracao_s

        inicio = time.monotonic()

        def ao_progredir(segundos: float) -> None:
            # Progresso em SEGUNDOS de mídia, não bytes: o tamanho final do
            # ProRes não é conhecido antes, e a duração é.
            self._fila.atualizar_progresso(job.id, Progresso(
                baixados=int(segundos), total=duracao or None,
                velocidade_bps=None,
                eta_s=estimar_restante(segundos, duracao, time.monotonic() - inicio)))

        try:
            convertido = self._converter(origem, preparacao.destino_convertido,
                                         conversao, ao_progredir)
        except Exception as erro:  # noqa: BLE001 — conversão não derruba o worker
            self._fila.avisar(job.id, AVISO_CONVERSAO.format(
                rotulo=conversao.rotulo, erro=_texto(erro), origem=origem))
            return origem
        finally:
            self._fila.marcar_fase(job.id, None)

        self._apagar_original(job, origem, convertido)
        return convertido

    def _apagar_original(self, job: Job, origem: str, convertido: str) -> None:
        """Apaga o download original depois de uma conversão BEM-SUCEDIDA.

        Pedido do autor: o .mkv ao lado do arquivo convertido só confundia
        ("por que estão sendo baixados dois?"). Só apaga com o convertido no
        disco, não vazio e diferente da origem; qualquer dúvida, mantém.
        Falha ao apagar vira aviso — o arquivo que importa já está pronto.
        """
        try:
            if (os.path.normcase(os.path.abspath(convertido))
                    == os.path.normcase(os.path.abspath(origem))):
                return
            if not os.path.isfile(convertido) or os.path.getsize(convertido) == 0:
                return
            os.remove(origem)
        except FileNotFoundError:
            pass
        except OSError as erro:
            self._fila.avisar(job.id, AVISO_ORIGINAL.format(origem=origem, erro=_texto(erro)))

    # ------------------------------------------------------------ desfechos

    def _falhar(self, job: Job, motivo: str, mensagem: str, registro_id: int | None = None) -> None:
        try:
            self._fila.falhar(job.id, motivo=motivo, mensagem=mensagem)
        except TransicaoIlegal:
            return  # já interrompido por parar(): não mexe
        if registro_id is None:
            return
        try:
            self._historico.falhar(registro_id, motivo=motivo, mensagem=mensagem)
        except Exception as erro:  # noqa: BLE001
            # Decisão 4: não pode ser silenciosa. A fila mostra o estado
            # certo; o aviso conta que o histórico ficou para trás.
            self._fila.avisar(job.id, AVISO_HISTORICO.format(erro=_texto(erro)))

    def _concluir(
        self,
        job: Job,
        caminho: str,
        resolucao: str | None,
        registro_id: int | None = None,
        *,
        ja_existia: bool = False,
    ) -> None:
        try:
            self._fila.concluir(job.id, caminho, ja_existia=ja_existia)
        except TransicaoIlegal:
            # Conclusão tardia depois de parar(): o job já é INTERROMPIDO e
            # fica assim. O arquivo pode existir no disco — a subida seguinte
            # avisa sobre ele (decisão 5).
            return
        if ja_existia:
            self._fila.avisar(job.id, AVISO_JA_EXISTIA)

        # A resolução do 'finished' é a REAL. Ela já era gravada; o que
        # faltava era alguém compará-la com o que o perfil pedia. "Pedi 4K e
        # veio 1080p" é indistinguível de um acerto enquanto ninguém compara.
        abaixo = None
        if not ja_existia and self._avaliar_resolucao is not None:
            abaixo = self._avaliar_resolucao(job, resolucao)
            if abaixo:
                self._fila.avisar(job.id, abaixo)

        if registro_id is None:
            return
        try:
            self._historico.concluir(
                registro_id,
                caminho=caminho,
                tamanho_bytes=_tamanho_arquivo(caminho),
                resolucao=resolucao,
                ja_existia=ja_existia,
            )
            if abaixo:
                self._historico.avisar(registro_id, abaixo)
        except Exception as erro:  # noqa: BLE001
            self._fila.avisar(job.id, AVISO_HISTORICO.format(erro=_texto(erro)))
