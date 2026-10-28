"""Monta o pacote portátil: um zip que roda em outro Windows sem instalar nada.

    python scripts/empacotar.py

Gera dist/Baixador-portatil.zip com o Baixador, o Python portátil oficial
(python.org, "embeddable"), as bibliotecas, o tkinter (seletor de pasta), o
ffmpeg, o ffprobe e o Deno desta máquina. Quem recebe extrai e dá duplo
clique em Baixador.bat.

O pacote vai LIMPO: sem data/ (histórico e cookies do autor) e sem
config/projetos.yaml (as pastas do autor). Na primeira subida, o Baixador
cria um projeto na pasta de vídeos de quem está usando
(projetos_yaml.criar_se_ausente).

Só escreve dentro de dist/, que o .gitignore exclui. Não toca em data/ nem no
Baixador que estiver rodando.
"""

import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIST = RAIZ / "dist"
PACOTE = DIST / "Baixador"
CACHE = DIST / "cache"
ZIP = DIST / "Baixador-portatil.zip"

VERSAO_PY = platform.python_version()  # o mesmo Python que monta: as bibliotecas casam
URL_PY = (
    f"https://www.python.org/ftp/python/{VERSAO_PY}/python-{VERSAO_PY}-embed-amd64.zip"
)

# O que vai do repositório. Nada de data/, tests/, docs/, .git/, spike.
COPIAR = ["src", "web", "config/perfis.yaml", "config/cookies.yaml", "README.md",
          "requirements.txt"]
PROGRAMAS = ["ffmpeg", "ffprobe", "deno"]

LEIA_ME = """BAIXADOR DE FOOTAGE - versao portatil
=====================================

COMO USAR
1. Extraia este zip numa pasta (ex.: Documentos\\Baixador).
2. De duplo clique em Baixador.bat.
   Na primeira vez o Windows pode avisar "O Windows protegeu o computador":
   clique em "Mais informacoes" e depois em "Executar assim mesmo".
3. A janela do Baixador abre sozinha. Cole o link, clique em Inspecionar e
   depois em Enfileirar.

ONDE FICAM OS VIDEOS
Em Videos\\Baixador, na sua pasta de usuario. Para mudar, use Ajustes
(cadastrar outra pasta) ou "Pasta avulsa" no seletor de projeto.

PARA FECHAR
Feche a janela do Baixador e tambem a janela preta "Baixador de Footage"
que fica minimizada na barra de tarefas.

"CONFIRME QUE VOCE NAO E UM ROBO" / VIDEO COM RESTRICAO DE IDADE
O YouTube pede login depois de muitos downloads seguidos. O passo a passo
para dar ao Baixador o login da SUA conta esta no README.md, na secao
"Confirme que voce nao e um robo". Nunca use o arquivo de cookies de outra
pessoa: ele e a chave da conta dela.

O perfil "4K rapido para o Premiere" usa a placa de video NVIDIA. Sem ela,
o Baixador converte pelo processador: funciona, mas demora mais.
"""


def ajustar_pth(texto: str) -> str:
    """O python3XX._pth do Python embutido define o sys.path INTEIRO.

    Acrescenta as bibliotecas (Lib/site-packages), a raiz do pacote ("..",
    onde está src/) e liga o `import site`, que vem comentado. Sem isso,
    `python -m src.web` não acha nem o próprio Baixador.
    """
    linhas = [l for l in texto.splitlines() if l.strip() and l.strip() != "#import site"]
    linhas = [l for l in linhas if l.strip() not in ("import site", "Lib\\site-packages", "..")]
    return "\n".join(linhas + ["Lib\\site-packages", "..", "import site"]) + "\n"


def ignorar(_pasta, nomes):
    return [n for n in nomes if n == "__pycache__" or n.endswith(".pyc")]


def baixar_python() -> Path:
    destino = CACHE / f"python-{VERSAO_PY}-embed-amd64.zip"
    if not destino.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        print(f"Baixando o Python portátil {VERSAO_PY} de python.org...")
        urllib.request.urlretrieve(URL_PY, destino)
    return destino


def montar() -> None:
    if PACOTE.exists():
        shutil.rmtree(PACOTE)  # só a saída deste script, dentro de dist/
    PACOTE.mkdir(parents=True)

    # 1. O Baixador
    for item in COPIAR:
        origem = RAIZ / item
        alvo = PACOTE / item
        alvo.parent.mkdir(parents=True, exist_ok=True)
        if origem.is_dir():
            shutil.copytree(origem, alvo, ignore=ignorar)
        else:
            shutil.copy2(origem, alvo)
    # CRLF: o cmd.exe lê os labels do goto com mais segurança assim.
    bat = (RAIZ / "Baixador.bat").read_text(encoding="ascii").replace("\r\n", "\n")
    (PACOTE / "Baixador.bat").write_bytes(bat.replace("\n", "\r\n").encode("ascii"))
    (PACOTE / "LEIA-ME.txt").write_bytes(LEIA_ME.replace("\n", "\r\n").encode("ascii"))

    # 2. O Python portátil
    py = PACOTE / "python"
    with zipfile.ZipFile(baixar_python()) as z:
        z.extractall(py)
    pth = next(py.glob("python*._pth"))
    pth.write_text(ajustar_pth(pth.read_text(encoding="utf-8")), encoding="utf-8")

    # 3. As bibliotecas, compiladas para ESTE Python (mesma versão)
    pacotes = py / "Lib" / "site-packages"
    print("Instalando as bibliotecas no pacote...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
         "--target", str(pacotes), "-r", str(RAIZ / "requirements.txt"), "pip"],
        check=True,
    )

    # 4. tkinter: o seletor nativo de pasta. O Python embutido não traz.
    base = Path(sys.base_prefix)
    shutil.copytree(base / "Lib" / "tkinter", pacotes / "tkinter", ignore=ignorar)
    # zlib1.dll: o Tcl depende dela, e o Python embutido não a traz. Sem
    # ela, "DLL load failed while importing _tkinter" — medido no pacote.
    for dll in ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll"):
        shutil.copy2(base / "DLLs" / dll, py / dll)
    shutil.copytree(base / "tcl", py / "tcl")

    # 5. ffmpeg, ffprobe e deno desta máquina
    (PACOTE / "bin").mkdir()
    for programa in PROGRAMAS:
        caminho = shutil.which(programa)
        if caminho is None:
            sys.exit(f"{programa} não encontrado no PATH: o pacote ficaria incompleto.")
        shutil.copy2(caminho, PACOTE / "bin" / Path(caminho).name)

    # 6. O zip, com uma pasta Baixador/ dentro
    print("Compactando...")
    ZIP.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for arquivo in sorted(PACOTE.rglob("*")):
            if arquivo.is_file():
                z.write(arquivo, Path("Baixador") / arquivo.relative_to(PACOTE))
    print(f"Pronto: {ZIP} ({ZIP.stat().st_size / 1024**2:.0f} MB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    montar()
