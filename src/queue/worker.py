"""Thread de trabalho. UM download por vez. SPEC 10.4.

O worker é deliberadamente burro: retira um job, registra o início no
histórico, pede ao `preparar` injetado as opções e o destino, chama o adapter
com um hook thread-safe, e registra o desfecho. Nenhuma regra de negócio
mora aqui — perfil, nome e caminho vêm resolvidos do pipeline.

Ticket: T5.
"""

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass

from ..domain.erros import MotivoFalha, TransicaoIlegal
from ..domain.models import Job, Progresso
from ..download.traducao_erros import ErroDeDownload
from .progresso import AgregadorProgresso

AVISO_JA_EXISTIA = "O arquivo já existia no destino; o download foi pulado e nada foi sobrescrito."
AVISO_HISTORICO = "O download terminou, mas o histórico não pôde ser atualizado: {erro}"

# Intervalo em que o laço acorda para checar o pedido de parada.
_PASSO_S = 0.05


@dataclass(frozen=True)
class Preparacao:
    """O que o worker precisa para chamar o adapter: resolvido pelo pipeline
    (perfil -> opções, projeto + nome -> destino) e injetado como callable."""

    url: str
    opcoes: dict
    destino: str


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

    def __init__(self, fila, downloader, historico, preparar: Callable[[Job], Preparacao]):
        self._fila = fila
        self._downloader = downloader
        self._historico = historico
        self._preparar = preparar
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

        self._concluir(job, caminho, resolucao["valor"], registro_id)

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
        except Exception as erro:  # noqa: BLE001
            self._fila.avisar(job.id, AVISO_HISTORICO.format(erro=_texto(erro)))
