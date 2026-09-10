"""
spike.py — protótipo DESCARTÁVEL (Fase 3).

Este arquivo não tem estrutura, não tem testes e não vai virar produção.
Ele existe para responder uma pergunta: o que o yt-dlp realmente devolve e
faz, quando chamado como biblioteca, com um vídeo de verdade.

O único artefato que sobrevive daqui é o spike_meta.json, que vira fixture
dos testes de T2/T3/T5.

Uso:
    python spike.py <URL>              # metadados + json + download
    python spike.py <URL> --so-meta    # para antes do download

O que ele faz:
    1. verifica ffmpeg/ffprobe e reporta
    2. busca SÓ os metadados (sem baixar) e imprime título, canal, duração,
       URL da thumbnail e a tabela de formatos disponíveis
    3. salva os metadados crus em spike_meta.json
    4. baixa um vídeo em 1080p com áudio, imprimindo o progresso
    5. reporta tamanho final e caminho do arquivo
    6. reporta de quais THREADS os callbacks de progresso vieram
"""

import json
import shutil
import sys
import threading
import time
from pathlib import Path

import yt_dlp


# ---------------------------------------------------------------------------
# Sem isto, imprimir o título de um vídeo quebra no console do Windows.
#
# Motivo (RESEARCH.md, Seção 7.4): o console usa cp1252, e o sanitizador do
# yt-dlp gera caracteres Unicode de largura total (U+FF1A etc.) para substituir
# os proibidos. Bati nesse erro durante a própria pesquisa:
#     UnicodeEncodeError: 'charmap' codec can't encode character '：'
# ---------------------------------------------------------------------------
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


PASTA_SAIDA = Path("saida")  # já está no .gitignore
ARQUIVO_META = Path("spike_meta.json")

# Perfil "edicao_1080" da Seção 2.2 do RESEARCH.md.
# Preferimos H.264 (avc1) + AAC (mp4a) porque é o que a timeline do Premiere
# aguenta sem sofrer. Os ramos depois de cada "/" são fallback: se o site não
# oferecer H.264, aceitamos qualquer codec em vez de falhar.
FORMATO_1080 = (
    "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b[height<=1080]/b"
)


# ===========================================================================
# Utilidades de formatação
# ===========================================================================


def humanizar_bytes(n):
    if not n:
        return "?"
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unidade}"
        n /= 1024
    return f"{n:.1f} PB"


def humanizar_duracao(segundos):
    if not segundos:
        return "?"
    segundos = int(segundos)
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def titulo_secao(texto):
    print()
    print("=" * 78)
    print(texto)
    print("=" * 78)


# ===========================================================================
# 1. ffmpeg
# ===========================================================================


def verificar_ffmpeg():
    """Detecta ffmpeg e ffprobe via PATH.

    Usamos shutil.which e não o FFmpegPostProcessor do yt-dlp porque which é
    instantâneo, não cria processo e não depende de API interna da biblioteca
    (RESEARCH.md, Seção 5.2). No Windows ele já resolve o .EXE via PATHEXT,
    então NÃO se escreve 'ffmpeg.exe' na mão.
    """
    titulo_secao("1. DEPENDÊNCIA EXTERNA: ffmpeg")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")

    print(f"  ffmpeg  : {ffmpeg or 'NÃO ENCONTRADO'}")
    print(f"  ffprobe : {ffprobe or 'NÃO ENCONTRADO'}")

    if not ffmpeg:
        print()
        print("  AVISO: sem ffmpeg o operador '+' do seletor não funciona.")
        print("  Isso limita o download aos formatos pré-combinados (~720p).")
        print("  Instalar com:  winget install Gyan.FFmpeg")

    return bool(ffmpeg)


# ===========================================================================
# 2. Metadados, sem baixar nada
# ===========================================================================


def buscar_metadados(url):
    """extract_info com download=False: toca a rede, mas não o stream de mídia."""
    titulo_secao("2. METADADOS (sem baixar)")

    opcoes = {
        "quiet": True,
        "no_warnings": True,
        # Impede que uma URL de playlist arraste o canal inteiro.
        # Download em massa está fora de escopo (README).
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(opcoes) as ydl:
        bruto = ydl.extract_info(url, download=False)
        # sanitize_info deixa o resultado serializável em JSON.
        # Sem isto, o json.dump lá embaixo quebra (RESEARCH.md, Seção 1.3).
        info = ydl.sanitize_info(bruto)

    print(f"  título    : {info.get('title')}")
    print(f"  canal     : {info.get('channel') or info.get('uploader')}")
    print(f"  duração   : {humanizar_duracao(info.get('duration'))}  ({info.get('duration')} s)")
    print(f"  id        : {info.get('id')}")
    print(f"  extractor : {info.get('extractor_key')}")
    print(f"  upload    : {info.get('upload_date')}")
    print(f"  thumbnail : {info.get('thumbnail')}")
    print(f"  qtd thumbs: {len(info.get('thumbnails') or [])}")

    return info


def imprimir_formatos(info):
    """Tabela de formatos: resolução, codec, fps e bitrate."""
    titulo_secao("3. FORMATOS DISPONÍVEIS")

    formatos = info.get("formats") or []
    if not formatos:
        print("  (nenhum formato retornado)")
        return

    cab = (
        f"  {'ID':<10} {'EXT':<5} {'RESOLUÇÃO':<12} {'FPS':>5} "
        f"{'TBR':>8} {'VCODEC':<16} {'ACODEC':<12} {'TAMANHO':>10}"
    )
    print(cab)
    print("  " + "-" * (len(cab) - 2))

    for f in formatos:
        tam = f.get("filesize") or f.get("filesize_approx")
        tbr = f.get("tbr")
        fps = f.get("fps")
        print(
            f"  {str(f.get('format_id')):<10} "
            f"{str(f.get('ext')):<5} "
            f"{str(f.get('resolution') or '-'):<12} "
            f"{(f'{fps:.0f}' if fps else '-'):>5} "
            f"{(f'{tbr:.0f}k' if tbr else '-'):>8} "
            f"{str(f.get('vcodec') or '-')[:16]:<16} "
            f"{str(f.get('acodec') or '-')[:12]:<12} "
            f"{humanizar_bytes(tam):>10}"
        )

    print(f"\n  total: {len(formatos)} formatos")

    # Contagem por tipo — ajuda a ver que 1080p é video-only e precisa de merge.
    so_video = sum(1 for f in formatos if f.get("vcodec") != "none" and f.get("acodec") == "none")
    so_audio = sum(1 for f in formatos if f.get("vcodec") == "none" and f.get("acodec") != "none")
    combinado = sum(1 for f in formatos if f.get("vcodec") != "none" and f.get("acodec") != "none")
    print(f"  só-vídeo: {so_video}   só-áudio: {so_audio}   combinados: {combinado}")


def salvar_meta(info):
    """Grava o JSON que vira fixture dos testes."""
    titulo_secao("4. SALVANDO spike_meta.json")

    with ARQUIVO_META.open("w", encoding="utf-8") as fp:
        json.dump(info, fp, ensure_ascii=False, indent=2)

    print(f"  arquivo : {ARQUIVO_META.resolve()}")
    print(f"  tamanho : {humanizar_bytes(ARQUIVO_META.stat().st_size)}")


# ===========================================================================
# 3. Download com progresso
# ===========================================================================


class Monitor:
    """Coleta o progresso e registra de qual thread cada callback veio.

    A pergunta das threads (RESEARCH.md, Seção 3.4) foi respondida lendo o
    código do yt-dlp: existem caminhos em que o hook é chamado de dentro de um
    ThreadPoolExecutor. Aqui a gente MEDE isso no vídeo real, em vez de
    confiar na leitura.
    """

    def __init__(self):
        self.threads_vistas = {}  # nome da thread -> nº de chamadas
        self.thread_principal = threading.current_thread().name
        self.ultimo_print = 0.0
        self.chamadas = 0
        self.eventos_finished = 0

    def hook(self, d):
        # Regra de ouro: NUNCA usar d['chave'] direto. Quase tudo é opcional,
        # e um KeyError aqui derruba o download inteiro.
        self.chamadas += 1
        nome = threading.current_thread().name
        self.threads_vistas[nome] = self.threads_vistas.get(nome, 0) + 1

        status = d.get("status")

        if status == "downloading":
            # Throttle: o hook dispara muitas vezes por segundo. Sem isto o
            # terminal vira uma cachoeira ilegível.
            agora = time.time()
            if agora - self.ultimo_print < 0.2:
                return
            self.ultimo_print = agora

            baixado = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            velocidade = d.get("speed")
            eta = d.get("eta")
            pct = (baixado / total * 100) if total else None

            linha = (
                f"  [{nome:<16}] "
                f"{(f'{pct:5.1f}%' if pct is not None else '  ?  ')} "
                f"{humanizar_bytes(baixado):>9} / {humanizar_bytes(total):>9}  "
                f"{humanizar_bytes(velocidade) + '/s' if velocidade else '   ?   ':>12}  "
                f"ETA {eta if eta is not None else '?'}s"
            )
            print(linha.ljust(100), end="\r")

        elif status == "finished":
            self.eventos_finished += 1
            print()
            print(f"  [{nome}] stream concluído: {d.get('filename')}")

        elif status == "error":
            print()
            print(f"  [{nome}] ERRO no download")


def baixar(url, monitor):
    titulo_secao("5. DOWNLOAD (perfil edicao_1080)")

    PASTA_SAIDA.mkdir(exist_ok=True)
    print(f"  seletor : {FORMATO_1080}")
    print(f"  destino : {PASTA_SAIDA.resolve()}")
    print()

    caminhos = []

    opcoes = {
        "format": FORMATO_1080,
        # format_sort é PREFERÊNCIA, não filtro: nunca elimina formato,
        # só decide quem é melhor (RESEARCH.md, Seção 2.1, camada 5).
        "format_sort": ["res:1080", "vcodec:h264", "acodec:aac", "fps"],
        "merge_output_format": "mp4",
        "outtmpl": str(PASTA_SAIDA / "%(title)s [%(id)s].%(ext)s"),
        "progress_hooks": [monitor.hook],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # Não mexer em ignoreerrors: na API o padrão já é False, ou seja,
        # os erros sobem como exceção — que é o que a gente quer capturar.
    }

    with yt_dlp.YoutubeDL(opcoes) as ydl:
        info = ydl.extract_info(url, download=True)
        caminho = info.get("requested_downloads", [{}])[0].get("filepath")
        if caminho:
            caminhos.append(Path(caminho))

    return caminhos


def relatar_arquivo(caminhos):
    titulo_secao("6. RESULTADO")

    if not caminhos:
        print("  (nenhum caminho reportado pelo yt-dlp)")
        # Fallback: lista o que apareceu na pasta.
        for p in sorted(PASTA_SAIDA.glob("*")):
            print(f"  encontrado: {p.name}  ({humanizar_bytes(p.stat().st_size)})")
        return

    for p in caminhos:
        existe = p.exists()
        print(f"  caminho : {p.resolve()}")
        print(f"  existe  : {existe}")
        if existe:
            print(f"  tamanho : {humanizar_bytes(p.stat().st_size)}  ({p.stat().st_size} bytes)")
            print(f"  extensão: {p.suffix}")


def relatar_threads(monitor):
    titulo_secao("7. THREADS DOS CALLBACKS  (a medição que interessa)")

    print(f"  thread que chamou download() : {monitor.thread_principal}")
    print(f"  total de callbacks           : {monitor.chamadas}")
    print(f"  eventos 'finished'           : {monitor.eventos_finished}")
    print()
    print("  threads que executaram o hook:")
    for nome, qtd in sorted(monitor.threads_vistas.items(), key=lambda kv: -kv[1]):
        marca = "  <-- principal" if nome == monitor.thread_principal else ""
        print(f"    {nome:<24} {qtd:>6} chamadas{marca}")

    outras = [n for n in monitor.threads_vistas if n != monitor.thread_principal]
    print()
    if outras:
        print("  >>> CONFIRMADO: o hook foi chamado de OUTRA(S) thread(s).")
        print("  >>> O estado da fila no T5 PRECISA de lock.")
    else:
        print("  >>> Neste vídeo, só a thread principal chamou o hook.")
        print("  >>> Isso NÃO prova que sempre será assim: o caminho DASH")
        print("  >>> multi-formato usa ThreadPoolExecutor. O lock continua")
        print("  >>> sendo requisito.")


# ===========================================================================
# Tratamento de erro
# ===========================================================================


def explicar_erro(err):
    """Desembrulha a exceção real de dentro do DownloadError.

    O yt-dlp embrulha quase tudo em DownloadError e guarda a exceção original
    em .exc_info (RESEARCH.md, Seção 6.2). Quem olha só o DownloadError acha
    que tudo é "erro genérico".
    """
    titulo_secao("ERRO")

    original = None
    if isinstance(err, yt_dlp.utils.DownloadError) and err.exc_info:
        original = err.exc_info[1]

    print(f"  tipo (embrulho) : {type(err).__name__}")
    print(f"  tipo (original) : {type(original).__name__ if original else '(nenhum)'}")
    print(f"  mensagem        : {err}")

    alvo = original or err

    if isinstance(alvo, yt_dlp.utils.GeoRestrictedError):
        print(f"  -> BLOQUEIO REGIONAL. países: {getattr(alvo, 'countries', None)}")
    elif isinstance(alvo, yt_dlp.utils.UnsupportedError):
        print(f"  -> SITE NÃO SUPORTADO: {getattr(alvo, 'url', None)}")
    else:
        texto = str(alvo).lower()
        if "private video" in texto:
            print("  -> VÍDEO PRIVADO")
        elif "video unavailable" in texto:
            print("  -> INDISPONÍVEL / REMOVIDO")
        elif "age-restricted" in texto or "confirm your age" in texto:
            print("  -> RESTRIÇÃO DE IDADE")
        elif "drm" in texto:
            print("  -> DRM. Fora de escopo desta ferramenta.")
        else:
            print("  -> não classificado (o fallback mostra a mensagem original)")


# ===========================================================================


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    url = sys.argv[1]
    so_meta = "--so-meta" in sys.argv

    print(f"URL      : {url}")
    print(f"yt-dlp   : {yt_dlp.version.__version__}")
    print(f"Python   : {sys.version.split()[0]}")

    tem_ffmpeg = verificar_ffmpeg()

    try:
        info = buscar_metadados(url)
        imprimir_formatos(info)
        salvar_meta(info)

        if so_meta:
            titulo_secao("PARADO EM --so-meta (nada foi baixado)")
            return 0

        if not tem_ffmpeg:
            print()
            print("  AVISO: sem ffmpeg, o merge não acontece.")
            print("  O download vai cair no fallback de formato combinado.")

        monitor = Monitor()
        inicio = time.time()
        caminhos = baixar(url, monitor)
        decorrido = time.time() - inicio

        relatar_arquivo(caminhos)
        print(f"  tempo   : {decorrido:.1f} s")
        relatar_threads(monitor)

    except yt_dlp.utils.DownloadError as err:
        explicar_erro(err)
        return 1
    except KeyboardInterrupt:
        print("\n\nInterrompido pelo usuário.")
        return 130

    titulo_secao("FIM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
