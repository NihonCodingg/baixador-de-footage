"""Transcode pós-download com o ffmpeg. Ex.: VP9/AV1 do YouTube -> ProRes HQ.

Fica em src/download/ porque é ferramenta de mídia, como o adapter; o QUE
converter é dado do domínio (perfis.Conversao), e o COMO é este módulo.

Por que não um postprocessor do yt-dlp: o FFmpegVideoConvertor recebe só o
container de destino, e ProRes 422 HQ exige `-profile:v 3` no `prores_ks`.
Não há como passar isso pela API dele.

Ticket: perfil ProRes.
"""

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from ..domain.perfis import Conversao


class ErroDeConversao(Exception):
    """O ffmpeg terminou com erro. O arquivo de origem continua intacto."""


def montar_comando(
    ffmpeg: str, origem: str, destino: str, conversao: Conversao, gpu: bool = False
) -> list[str]:
    """A linha de comando, montada como LISTA: sem shell, sem aspas, e um
    caminho com espaço, acento ou `&` não vira problema.

    `gpu=True` usa os argumentos de placa de vídeo da conversão, quando ela
    os tem.
    """
    usar_gpu = gpu and conversao.video_gpu is not None
    comando = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        # Só erros no stderr: o pipe dele só é lido no FIM, e um log verboso
        # que enchesse o buffer travaria o ffmpeg esperando alguém ler.
        "-loglevel",
        "error",
        # -n e não -y: footage nunca é sobrescrito (SPEC 8.4).
        "-n",
        *(conversao.entrada_gpu if usar_gpu else ()),
        "-i",
        origem,
        "-map",
        "0:v:0",
        # `?`: vídeo sem trilha de áudio converte em vez de falhar.
        "-map",
        "0:a:0?",
        *(conversao.video_gpu if usar_gpu else conversao.video_cpu),
        *conversao.audio,
        # Progresso legível por máquina no stdout, em vez da linha de status
        # que o ffmpeg reescreve no stderr.
        "-progress",
        "pipe:1",
        "-nostats",
        destino,
    ]
    return comando


def segundos_processados(linha: str) -> float | None:
    """Lê `out_time_us=12345678` (microssegundos) de uma linha do -progress.

    `out_time_ms` também existe e, apesar do nome, vem em MICROssegundos em
    todas as versões — bug histórico do ffmpeg. Por isso só `out_time_us`.
    """
    chave, _, valor = linha.strip().partition("=")
    if chave != "out_time_us":
        return None
    try:
        micro = int(valor)
    except ValueError:
        return None
    return micro / 1_000_000 if micro >= 0 else None


def converter(
    ffmpeg: str,
    origem: str,
    destino: str,
    conversao: Conversao,
    ao_progredir: Callable[[float], None] | None = None,
    executar=subprocess.Popen,
) -> str:
    """Converte e devolve o caminho do arquivo convertido.

    `ao_progredir` recebe os segundos já processados. `executar` entra por
    injeção para o teste não depender de um ffmpeg instalado.

    Em falha, apaga o destino PARCIAL (um .mov truncado que abre no Premiere
    é pior que nenhum) e levanta ErroDeConversao. A origem nunca é tocada.
    """
    # Destino ocupado: recusa ANTES de rodar. Medido no ffmpeg desta máquina:
    # com `-n` e o destino existente, ele não grava e SAI COM CÓDIGO 0 — o
    # arquivo antigo pareceria a conversão recém-feita, e o histórico
    # apontaria para um .mov que não é este vídeo. O pipeline já resolve a
    # colisão; isto cobre a corrida entre a resolução e agora.
    if Path(destino).exists():
        raise ErroDeConversao(f"o destino já existe e não será sobrescrito: {destino}")

    # O ffmpeg grava num nome `.parcial` e o arquivo só ganha o nome final no
    # sucesso. Fechar o programa no meio manda o sinal também ao ffmpeg, que
    # FINALIZA o que já escreveu e sai: com o nome final, sobraria um vídeo
    # truncado que abre normal no Premiere — footage incompleto passando por
    # completo. Uma sobra `.parcial` é deste mesmo vídeo e incompleta por
    # definição, então a conversão nova começa do zero.
    parcial = caminho_parcial(destino)
    _apagar_parcial(parcial)

    # Placa de vídeo primeiro; se ela falhar — driver sem Vulkan, ffmpeg sem o
    # codificador, qualquer erro —, a CPU refaz do zero. Um download nunca é
    # perdido porque a GPU não colaborou. O parcial da tentativa na GPU já foi
    # apagado por _rodar, então o destino está livre de novo.
    try:
        if conversao.video_gpu is None:
            raise ErroDeConversao("sem caminho na placa de vídeo")
        _rodar(montar_comando(ffmpeg, origem, parcial, conversao, gpu=True),
               parcial, ao_progredir, executar)
    except ErroDeConversao:
        _rodar(montar_comando(ffmpeg, origem, parcial, conversao), parcial,
               ao_progredir, executar)

    try:
        # rename e não replace: no Windows ele FALHA se o destino apareceu
        # nesse meio-tempo, em vez de sobrescrevê-lo (SPEC 8.4).
        os.rename(parcial, destino)
    except OSError as erro:
        _apagar_parcial(parcial)
        raise ErroDeConversao(f"não foi possível dar o nome final ao arquivo: {erro}") from erro
    return destino


def caminho_parcial(destino: str) -> str:
    """'D:/F/v [id].mp4' -> 'D:/F/v [id].parcial.mp4'. A extensão real fica
    no fim porque é por ela que o ffmpeg escolhe o formato de saída."""
    base, ponto, ext = destino.rpartition(".")
    return f"{base}.parcial.{ext}" if ponto else f"{destino}.parcial"


def _rodar(comando, destino, ao_progredir, executar) -> str:
    """Uma tentativa: roda, reporta progresso, e apaga o parcial se falhar."""
    try:
        processo = executar(
            comando,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            # Prioridade abaixo do normal: a conversão de 4K ocupa a CPU
            # inteira por dezenas de minutos, e o editor está com o Premiere
            # aberto ao lado. Medido: em prioridade normal o ffmpeg tomou 87%
            # da CPU e o PC travou. Assim ela cede a vez quando ele usa a
            # máquina, e corre a toda quando não usa.
            **_prioridade_baixa(),
        )
    except OSError as erro:
        raise ErroDeConversao(f"não foi possível iniciar o ffmpeg: {erro}") from erro

    for linha in processo.stdout:
        segundos = segundos_processados(linha)
        if segundos is not None and ao_progredir is not None:
            try:
                ao_progredir(segundos)
            except Exception:  # noqa: BLE001 — progresso nunca derruba a conversão
                pass

    stderr = processo.stderr.read() if processo.stderr else ""
    codigo = processo.wait()
    if codigo != 0:
        # Seguro apagar: `converter` garante que o arquivo não existia antes
        # de qualquer tentativa, então o que está ali é o parcial desta.
        _apagar_parcial(destino)
        ultimas = " ".join(l.strip() for l in stderr.strip().splitlines()[-3:])
        raise ErroDeConversao(f"o ffmpeg terminou com código {codigo}: {ultimas[:300]}")
    if not Path(destino).is_file():
        raise ErroDeConversao("o ffmpeg terminou sem erro, mas o arquivo convertido não existe")
    return destino


def _prioridade_baixa() -> dict:
    """Argumento do Popen para rodar abaixo do normal. Só no Windows: no
    resto, `nice` exigiria outro mecanismo, e o alvo do projeto é Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.BELOW_NORMAL_PRIORITY_CLASS}
    return {}


def _apagar_parcial(destino: str) -> None:
    try:
        Path(destino).unlink(missing_ok=True)
    except OSError:
        pass
