"""Estado da fila, protegido por lock. SPEC 10.

O lock não é precaução: o progress hook pode ser chamado de threads criadas
pelo próprio yt-dlp, sem configuração especial (RESEARCH 3.4, caso 4).

Regra do hook: adquire o lock apenas para SUBSTITUIR o objeto Progresso do
job. Nunca muta campo a campo, nunca faz I/O.

Ticket: T5.
"""

import copy
import queue
import threading

from ..domain.models import EstadoJob, Job, Progresso


class Fila:
    """Dicionário de jobs sob lock + fila FIFO de ids pendentes.

    Os jobs guardados aqui são os originais; tudo que sai (obter, instantaneo,
    proximo) é CÓPIA, para ninguém mutar o estado por fora do lock.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._ordem: list[str] = []
        self._pendentes: queue.Queue[str] = queue.Queue()

    # ---------------------------------------------------------------- entrada

    def adicionar(self, job: Job) -> str:
        """Levanta ValueError se o id já existe."""
        with self._lock:
            if job.id in self._jobs:
                raise ValueError(f"Job {job.id!r} já está na fila.")
            self._jobs[job.id] = job
            self._ordem.append(job.id)
        self._pendentes.put(job.id)
        return job.id

    def proximo(self, timeout: float | None = None) -> Job | None:
        """Retira o próximo job pendente e o coloca em BAIXANDO, sob o mesmo
        lock — para um cancelar() não entrar na fresta. Descarta os
        cancelados. None se nada chegar dentro do timeout."""
        while True:
            try:
                job_id = self._pendentes.get(timeout=timeout)
            except queue.Empty:
                return None
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job.estado is not EstadoJob.NA_FILA:
                    continue  # cancelado depois de enfileirado: descarta
                job.transicionar(EstadoJob.BAIXANDO)
                return copy.copy(job)

    # ----------------------------------------------------------- transições

    def atualizar_progresso(self, job_id: str, progresso: Progresso) -> None:
        """Chamado pelo progress hook, possivelmente de outra thread.

        NUNCA levanta: uma exceção aqui derruba o download. Ignora job
        inexistente ou que já saiu de BAIXANDO — hook atrasado não ressuscita
        job terminal.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.estado is not EstadoJob.BAIXANDO:
                return
            job.progresso = progresso  # substitui o objeto; nunca muta

    def transicionar(self, job_id: str, novo: EstadoJob) -> None:
        """KeyError se o job não existe; TransicaoIlegal se a regra proíbe."""
        with self._lock:
            self._jobs[job_id].transicionar(novo)

    def concluir(self, job_id: str, caminho: str, *, ja_existia: bool = False) -> None:
        """Valida a transição ANTES de preencher o caminho: sem estado parcial.

        `ja_existia` marca que o arquivo já estava no destino e o download foi
        pulado — é sucesso, mas a tela precisa mostrar que não baixou.
        """
        with self._lock:
            job = self._jobs[job_id]
            job.transicionar(EstadoJob.CONCLUIDO)
            job.caminho_final = caminho
            job.ja_existia = ja_existia

    def avisar(self, job_id: str, texto: str) -> None:
        """Acrescenta um aviso não-bloqueante ao job, sem mudar o estado.

        NUNCA levanta: é chamado de caminhos de erro que não podem quebrar
        (falha ao gravar no histórico, arquivo já existente). Acumula sem
        duplicar — perder um aviso é o mesmo problema de perder um registro.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            partes = [p for p in (job.aviso or "").split(" | ") if p]
            if texto not in partes:
                partes.append(texto)
            job.aviso = " | ".join(partes)

    def falhar(self, job_id: str, *, motivo: str, mensagem: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.transicionar(EstadoJob.FALHOU)
            job.motivo_falha = motivo
            job.mensagem_falha = mensagem

    def cancelar(self, job_id: str) -> bool:
        """Só cancela job em `na_fila`. SPEC 10.5.

        Devolve False se o job já começou, é terminal ou não existe — a API
        traduz isso em 409/404. O id continua na fila de pendentes; proximo()
        o descarta ao retirá-lo.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.estado is not EstadoJob.NA_FILA:
                return False
            job.transicionar(EstadoJob.CANCELADO)
            return True

    def interromper_em_andamento(self) -> list[str]:
        """Todo job em BAIXANDO vira INTERROMPIDO. Devolve os ids."""
        with self._lock:
            ids = [j.id for j in self._jobs.values() if j.estado is EstadoJob.BAIXANDO]
            for job_id in ids:
                self._jobs[job_id].transicionar(EstadoJob.INTERROMPIDO)
            return ids

    # ---------------------------------------------------------------- leitura

    def obter(self, job_id: str) -> Job | None:
        """Cópia do job, ou None."""
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.copy(job) if job is not None else None

    def instantaneo(self) -> list[Job]:
        """Cópias de todos os jobs, na ordem de chegada, sob lock."""
        with self._lock:
            return [copy.copy(self._jobs[job_id]) for job_id in self._ordem]
