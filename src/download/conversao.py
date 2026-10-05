"""Transcode pós-download com o ffmpeg. Ex.: VP9/AV1 do YouTube -> ProRes HQ.

Fica em src/download/ porque é ferramenta de mídia, como o adapter; o QUE
converter é dado do domínio (perfis.Conversao), e o COMO é este módulo.

Por que não um postprocessor do yt-dlp: o FFmpegVideoConvertor recebe só o
container de destino, e ProRes 422 HQ exige `-profile:v 3` no `prores_ks`.
Não há como passar isso pela API dele.

Ticket: perfil ProRes.
"""

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

    `gpu=True` usa o codificador de placa de vídeo da conversão (Vulkan). A
    DECODIFICAÇÃO do VP9 continua na CPU de propósito: medido, decodificar
    na GPU e devolver cada quadro 4K para o filtro de formato custou mais do
    que economizou (0,25x e 0,19x contra 0,36x).
    """
    usar_gpu = gpu and conversao.vcodec_gpu is not None
    comando = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        # Só erros no stderr: o pipe dele só é lido no FIM, e um log verboso
        # que enchesse o buffer travaria o ffmpeg esperando alguém ler.
        "-loglevel",
        "error",
        # -n e não -y: footage nunca é sobrescrito (SPEC 8.4). Se o destino
        # aparecer entre a resolução de colisão e agora, o ffmpeg recusa.
        "-n",
    ]
    if usar_gpu:
        comando += ["-init_hw_device", "vulkan=vk:0", "-filter_hw_device", "vk"]
    comando += [
        "-i",
        origem,
        "-map",
        "0:v:0",
        # `?`: vídeo sem trilha de áudio converte em vez de falhar.
        "-map",
        "0:a:0?",
        "-c:v",
        conversao.vcodec_gpu if usar_gpu else conversao.vcodec,
    ]
    if conversao.perfil_video is not None:
        comando += ["-profile:v", conversao.perfil_video]
    comando += [
        # apl0 marca o arquivo como gerado pela Apple; alguns programas usam
        # isso para escolher o decodificador nativo de ProRes.
        "-vendor",
        "apl0",
    ]
    if usar_gpu:
        # O quadro vira 10 bits 4:2:2 na CPU e sobe para a placa já pronto.
        comando += ["-vf", f"format={conversao.pix_fmt},hwupload", "-async_depth", "4"]
    else:
        comando += ["-pix_fmt", conversao.pix_fmt]
    comando += [
        "-c:a",
        conversao.acodec,
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

    # Placa de vídeo primeiro; se ela falhar — driver sem Vulkan, ffmpeg sem o
    # codificador, qualquer erro —, a CPU refaz do zero. Um download nunca é
    # perdido porque a GPU não colaborou. O parcial da tentativa na GPU já foi
    # apagado por _rodar, então o destino está livre de novo.
    if conversao.vcodec_gpu is not None:
        try:
            return _rodar(montar_comando(ffmpeg, origem, destino, conversao, gpu=True),
                          destino, ao_progredir, executar)
        except ErroDeConversao:
            pass
    return _rodar(montar_comando(ffmpeg, origem, destino, conversao), destino,
                  ao_progredir, executar)


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
