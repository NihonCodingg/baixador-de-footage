"""Agregação de progresso por stream. SPEC 10.4.

No caminho DASH multi-formato, vídeo e áudio baixam em threads separadas e
cada uma chama o hook com os SEUS bytes (RESEARCH 3.4, caso 4). Sem somar por
format_id, a barra de progresso pula entre streams.

Ticket: T5.
"""

import threading

from ..domain.models import Progresso

_SEM_ID = "?"


class AgregadorProgresso:
    """Guarda o último Progresso de cada stream e soma. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._por_stream: dict[str, Progresso] = {}

    def atualizar(self, format_id: str | None, progresso: Progresso) -> None:
        """Substitui (não acumula) o progresso do stream."""
        chave = format_id or _SEM_ID
        with self._lock:
            self._por_stream[chave] = progresso

    def total(self) -> Progresso:
        """Soma dos streams.

        total: None se QUALQUER stream não souber o seu — somar só os
        conhecidos daria uma barra que passa de 100%.
        velocidade: soma das conhecidas.
        eta: o MAIOR — o job termina quando o stream mais lento terminar.
        """
        with self._lock:
            itens = list(self._por_stream.values())

        if not itens:
            return Progresso(baixados=0, total=None, velocidade_bps=None, eta_s=None)

        baixados = sum(p.baixados for p in itens)
        total = sum(p.total for p in itens) if all(p.total is not None for p in itens) else None
        velocidades = [p.velocidade_bps for p in itens if p.velocidade_bps is not None]
        etas = [p.eta_s for p in itens if p.eta_s is not None]

        return Progresso(
            baixados=baixados,
            total=total,
            velocidade_bps=sum(velocidades) if velocidades else None,
            eta_s=max(etas) if etas else None,
        )
