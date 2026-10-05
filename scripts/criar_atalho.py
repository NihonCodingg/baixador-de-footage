"""Cria o atalho "Baixador" na área de trabalho, com ícone próprio.

    python scripts/criar_atalho.py

O ícone é o mesmo desenho de web/favicon.svg — a seta de download no verde
de acento da tela —, rasterizado aqui com a biblioteca padrão. Sem Pillow:
seria uma dependência a mais só para desenhar quatro formas.

O .ico vai para data/, que o Git ignora: nada binário entra no repositório,
e rodar o script de novo regenera tudo.

O atalho aponta para o Baixador.bat, abre minimizado (o console não pisca
na frente) e leva o nome "Baixador". Um atalho antigo, como o "Baixador -
Atalho" criado à mão, NÃO é apagado — isso fica com você.
"""

import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ICONE = RAIZ / "data" / "baixador.ico"
BAT = RAIZ / "Baixador.bat"

FUNDO = (0x0A, 0x0B, 0x0D, 255)       # --cor-fundo
ACENTO = (0xB8, 0xF2, 0x4A, 255)      # --cor-acento
TRANSPARENTE = (0, 0, 0, 0)
TAMANHOS = (256, 48, 32, 16)
AMOSTRAS = 4                          # supersampling 4x4: bordas suaves


# ===========================================================================
# O desenho, em coordenadas de 0 a 64 — as mesmas do favicon.svg
# ===========================================================================


def _no_retangulo_arredondado(x, y, x0, y0, x1, y1, raio):
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + raio), x1 - raio)
    cy = min(max(y, y0 + raio), y1 - raio)
    return (x - cx) ** 2 + (y - cy) ** 2 <= raio**2


def _no_triangulo(x, y):
    """Ponta da seta: (15,31), (49,31), (32,47)."""
    if y < 31 or y > 47:
        return False
    meia_largura = 17 * (47 - y) / 16
    return abs(x - 32) <= meia_largura


def cor_em(x: float, y: float) -> tuple[int, int, int, int]:
    """A cor do desenho num ponto (0..64)."""
    if not _no_retangulo_arredondado(x, y, 0, 0, 64, 64, 14):
        return TRANSPARENTE
    if (
        _no_retangulo_arredondado(x, y, 27, 11, 37, 34, 2)
        or _no_triangulo(x, y)
        or _no_retangulo_arredondado(x, y, 14, 51, 50, 56, 2.5)
    ):
        return ACENTO
    return FUNDO


def rasterizar(lado: int) -> bytes:
    """Pixels RGBA, linha a linha, com média das amostras para suavizar."""
    escala = 64 / lado
    pixels = bytearray()
    for py in range(lado):
        for px in range(lado):
            soma = [0, 0, 0, 0]
            for sy in range(AMOSTRAS):
                for sx in range(AMOSTRAS):
                    r, g, b, a = cor_em(
                        (px + (sx + 0.5) / AMOSTRAS) * escala,
                        (py + (sy + 0.5) / AMOSTRAS) * escala,
                    )
                    soma[0] += r * a
                    soma[1] += g * a
                    soma[2] += b * a
                    soma[3] += a
            alfa = soma[3]
            n = AMOSTRAS * AMOSTRAS
            if alfa:
                pixels += bytes((soma[0] // alfa, soma[1] // alfa, soma[2] // alfa, alfa // n))
            else:
                pixels += bytes(4)
    return bytes(pixels)


# ===========================================================================
# PNG e ICO, com struct e zlib
# ===========================================================================


def _bloco(tipo: bytes, dados: bytes) -> bytes:
    return (struct.pack(">I", len(dados)) + tipo + dados
            + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF))


def png(lado: int) -> bytes:
    rgba = rasterizar(lado)
    linhas = b"".join(b"\x00" + rgba[y * lado * 4:(y + 1) * lado * 4] for y in range(lado))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _bloco(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 6, 0, 0, 0))
        + _bloco(b"IDAT", zlib.compress(linhas, 9))
        + _bloco(b"IEND", b"")
    )


def ico(tamanhos=TAMANHOS) -> bytes:
    """ICO com uma imagem PNG por tamanho (formato aceito desde o Vista)."""
    imagens = [png(lado) for lado in tamanhos]
    cabecalho = struct.pack("<HHH", 0, 1, len(imagens))
    deslocamento = 6 + 16 * len(imagens)
    entradas = b""
    for lado, dados in zip(tamanhos, imagens):
        medida = 0 if lado >= 256 else lado   # 0 significa 256 no formato ICO
        entradas += struct.pack("<BBBBHHII", medida, medida, 0, 0, 1, 32,
                                len(dados), deslocamento)
        deslocamento += len(dados)
    return cabecalho + entradas + b"".join(imagens)


# ===========================================================================
# O atalho
# ===========================================================================

POWERSHELL = r"""
$desktop = [Environment]::GetFolderPath('Desktop')
$destino = Join-Path $desktop 'Baixador.lnk'
$atalho = (New-Object -ComObject WScript.Shell).CreateShortcut($destino)
$atalho.TargetPath = $env:BAIXADOR_BAT
$atalho.WorkingDirectory = $env:BAIXADOR_RAIZ
$atalho.IconLocation = "$env:BAIXADOR_ICONE,0"
$atalho.WindowStyle = 7
$atalho.Description = 'Baixador de Footage'
$atalho.Save()
Write-Output $destino
"""


def criar_atalho() -> str:
    """Cria (ou atualiza) Baixador.lnk na área de trabalho. Devolve o caminho.

    Os caminhos vão por variável de ambiente, não colados no comando: a pasta
    do projeto tem acento, e aspas dentro de um -Command são frágeis.
    """
    ambiente = dict(os.environ, BAIXADOR_BAT=str(BAT), BAIXADOR_RAIZ=str(RAIZ),
                    BAIXADOR_ICONE=str(ICONE))
    resultado = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL],
        env=ambiente, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    if resultado.returncode != 0:
        raise RuntimeError(f"o PowerShell não criou o atalho: {resultado.stderr.strip()}")
    return resultado.stdout.strip()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ICONE.parent.mkdir(parents=True, exist_ok=True)
    ICONE.write_bytes(ico())
    print(f"Ícone gerado: {ICONE}")
    try:
        print(f"Atalho criado: {criar_atalho()}")
    except RuntimeError as erro:
        print(f"Erro: {erro}")
        return 1
    print("Se ainda existir um atalho antigo (\"Baixador - Atalho\"), pode apagá-lo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
