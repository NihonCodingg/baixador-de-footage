"""Trecho do vídeo: ler os tempos digitados e validar contra a duração. Puro.

O yt-dlp sabe baixar um intervalo (`download_ranges`), mas não sabe o que o
editor digitou: "1:30", "90" e "1:02:03" são o mesmo tipo de coisa para ele,
um campo vazio quer dizer "do começo" ou "até o fim", e um fim depois da
duração do vídeo precisa ser recusado com a duração na mensagem. Isso é o
domínio; a chamada ao yt-dlp fica no adapter (SPEC 12g).
"""

import re
from dataclasses import dataclass

from .erros import TrechoInvalido

_SO_DIGITOS = re.compile(r"\d+")


@dataclass(frozen=True)
class Trecho:
    """Intervalo em segundos inteiros, do início ao fim."""

    inicio_s: int
    fim_s: int

    @property
    def duracao_s(self) -> int:
        return self.fim_s - self.inicio_s

    def para_nome(self) -> str:
        """'trecho 1m30s-2m45s' — vai no nome do arquivo. Sem ':' (proibido
        no Windows) e legível na busca do explorador."""
        return f"trecho {_tempo_nome(self.inicio_s)}-{_tempo_nome(self.fim_s)}"

    def para_tela(self) -> str:
        """'1:30–2:45', como a duração aparece no resto da interface."""
        return f"{_tempo_tela(self.inicio_s)}–{_tempo_tela(self.fim_s)}"

    def para_texto(self) -> str:
        """'90-165' — a forma que o histórico grava."""
        return f"{self.inicio_s}-{self.fim_s}"

    @classmethod
    def de_texto(cls, texto: str | None) -> "Trecho | None":
        if not texto:
            return None
        inicio, _, fim = texto.partition("-")
        return cls(int(inicio), int(fim))


def ler_tempo(texto: str) -> int:
    """'90' -> 90 ; '1:30' -> 90 ; '1:02:03' -> 3723. TrechoInvalido se não
    for tempo.

    Só o primeiro campo pode passar de 59: '75:00' é um jeito natural de
    escrever 1h15, mas '1:75' não é tempo nenhum.
    """
    partes = (texto or "").strip().split(":")
    if len(partes) > 3 or not all(_SO_DIGITOS.fullmatch(p) for p in partes):
        raise TrechoInvalido(f"{texto.strip()!r} não é um tempo. Use 90, 1:30 ou 1:02:03.")
    numeros = [int(p) for p in partes]
    if any(n > 59 for n in numeros[1:]):
        raise TrechoInvalido(f"{texto.strip()!r} não é um tempo: minutos e segundos vão até 59.")
    total = 0
    for n in numeros:
        total = total * 60 + n
    return total


def montar_trecho(inicio: str | None, fim: str | None, duracao_s: int | None) -> Trecho | None:
    """O trecho pedido, ou None para o vídeo INTEIRO.

    Campo vazio: início = começo do vídeo, fim = final do vídeo. Os dois
    vazios, ou um trecho que cobre o vídeo todo, são o vídeo inteiro — como
    trecho, ele ganharia um nome estranho e escaparia do "já baixado".
    """
    inicio = (inicio or "").strip()
    fim = (fim or "").strip()
    if not inicio and not fim:
        return None

    try:
        comeco = ler_tempo(inicio) if inicio else 0
    except TrechoInvalido as erro:
        raise TrechoInvalido(f"Início do trecho: {erro}") from erro
    try:
        final = ler_tempo(fim) if fim else duracao_s
    except TrechoInvalido as erro:
        raise TrechoInvalido(f"Fim do trecho: {erro}") from erro
    if final is None:
        raise TrechoInvalido(
            "Informe o fim do trecho: a duração deste vídeo é desconhecida, então "
            "não dá para saber onde é o final."
        )

    if duracao_s is not None:
        tem = f"o vídeo tem {_tempo_tela(duracao_s)}"
        if comeco >= duracao_s:
            raise TrechoInvalido(f"O início do trecho passa do fim do vídeo ({tem}).")
        if final > duracao_s:
            raise TrechoInvalido(f"O fim do trecho passa da duração do vídeo ({tem}).")
    if comeco >= final:
        raise TrechoInvalido("O início do trecho tem que vir antes do fim.")

    if comeco == 0 and duracao_s is not None and final >= duracao_s:
        return None
    return Trecho(comeco, final)


def _tempo_nome(segundos: int) -> str:
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def _tempo_tela(segundos: int) -> str:
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
