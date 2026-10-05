"""Transcode pós-download com o ffmpeg. Ex.: VP9/AV1 do YouTube -> ProRes HQ.

Fica em src/download/ porque é ferramenta de mídia, como o adapter; o QUE
converter é dado do domínio (perfis.Conversao), e o COMO é este módulo.

Por que não um postprocessor do yt-dlp: o FFmpegVideoConvertor recebe só o
container de destino, e ProRes 422 HQ exige `-profile:v 3` no `prores_ks`.
Não há como passar isso pela API dele.

Ticket: perfil ProRes.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

from ..domain.perfis import Conversao


class ErroDeConversao(Exception):
    """O ffmpeg terminou com erro. O arquivo de origem continua intacto."""


def montar_comando(ffmpeg: str, origem: str, destino: str, conversao: Conversao) -> list[str]:
    """A linha de comando, montada como LISTA: sem shell, sem aspas, e um
    caminho com espaço, acento ou `&` não vira problema."""
    comando = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        # -n e não -y: footage nunca é sobrescrito (SPEC 8.4). Se o destino
        # aparecer entre a resolução de colisão e agora, o ffmpeg recusa.
        "-n",
        "-i",
        origem,
        "-map",
        "0:v:0",
        # `?`: vídeo sem trilha de áudio converte em vez de falhar.
        "-map",
        "0:a:0?",
        "-c:v",
        conversao.vcodec,
    ]
    if conversao.perfil_video is not None:
        comando += ["-profile:v", conversao.perfil_video]
    comando += [
        # apl0 marca o arquivo como gerado pela Apple; alguns programas usam
        # isso para escolher o decodificador nativo de ProRes.
        "-vendor",
        "apl0",
        "-pix_fmt",
        conversao.pix_fmt,
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

    comando = montar_comando(ffmpeg, origem, destino, conversao)
    try:
        processo = executar(
            comando,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
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
        # Seguro apagar: a checagem acima garante que o arquivo não existia
        # antes, então qualquer coisa ali é o parcial desta conversão.
        _apagar_parcial(destino)
        ultimas = " ".join(l.strip() for l in stderr.strip().splitlines()[-3:])
        raise ErroDeConversao(f"o ffmpeg terminou com código {codigo}: {ultimas[:300]}")
    if not Path(destino).is_file():
        raise ErroDeConversao("o ffmpeg terminou sem erro, mas o arquivo convertido não existe")
    return destino


def _apagar_parcial(destino: str) -> None:
    try:
        Path(destino).unlink(missing_ok=True)
    except OSError:
        pass
