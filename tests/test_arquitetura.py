"""Teste de arquitetura: as regras duras do SPEC 4.2, verificadas de verdade.

Este arquivo não é documentação. Ele analisa a árvore de src/ com o módulo
`ast` e falha o build se um import violar as regras.

REGRA 1: src/domain/ nunca importa de src/download/, src/storage/ ou src/queue/
REGRA 2: src/web/ nunca importa de src/domain/

A motivação é o risco central do projeto: como o yt-dlp faz o trabalho pesado,
existe o perigo de o domínio virar um wrapper vazio. Uma fronteira que só existe
em comentário não é fronteira — apaga-se sozinha na primeira pressa.

Não importa nenhum módulo do projeto: só lê e parseia os arquivos. Assim ele
funciona mesmo com stubs que levantam NotImplementedError.
"""

import ast
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SRC = RAIZ / "src"


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------


def arquivos_python(pacote: str) -> list[Path]:
    """Todos os .py sob src/<pacote>/, recursivamente."""
    return sorted((SRC / pacote).rglob("*.py"))


def modulo_de(caminho: Path) -> str:
    """Caminho de arquivo -> nome de módulo pontilhado, a partir da raiz.

    src/domain/nomes.py      -> src.domain.nomes
    src/domain/__init__.py   -> src.domain
    """
    relativo = caminho.relative_to(RAIZ).with_suffix("")
    partes = list(relativo.parts)
    if partes[-1] == "__init__":
        partes.pop()
    return ".".join(partes)


def pacote_de(caminho: Path) -> str:
    """Pacote que contém o arquivo. src/domain/nomes.py -> src.domain"""
    modulo = modulo_de(caminho)
    if caminho.name == "__init__.py":
        return modulo
    return modulo.rsplit(".", 1)[0]


def imports_de(caminho: Path) -> list[str]:
    """Nomes de módulo importados, com os relativos já resolvidos.

    Resolver os relativos é essencial: `from ..domain.erros import X` dentro de
    src/download/ tem que ser reconhecido como `src.domain.erros`, senão a
    regra passa batido.
    """
    arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    encontrados: list[str] = []
    pacote = pacote_de(caminho)

    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            encontrados.extend(alias.name for alias in no.names)

        elif isinstance(no, ast.ImportFrom):
            if no.level == 0:
                if no.module:
                    encontrados.append(no.module)
                continue

            # Import relativo: sobe `level` níveis a partir do pacote atual.
            partes = pacote.split(".")
            subir = no.level - 1
            base = partes[: len(partes) - subir] if subir else partes
            alvo = ".".join(base + ([no.module] if no.module else []))
            encontrados.append(alvo)

    return encontrados


def viola(importado: str, proibido: str) -> bool:
    """True se `importado` é o pacote proibido ou algo dentro dele."""
    return importado == proibido or importado.startswith(proibido + ".")


# ---------------------------------------------------------------------------
# REGRA 1 — o domínio é folha
# ---------------------------------------------------------------------------

PROIBIDOS_NO_DOMINIO = ("src.download", "src.storage", "src.queue")


@pytest.mark.parametrize("arquivo", arquivos_python("domain"), ids=modulo_de)
def test_dominio_nao_importa_camadas_externas(arquivo):
    """REGRA 1: src/domain/ é folha dentro de src/."""
    for importado in imports_de(arquivo):
        for proibido in PROIBIDOS_NO_DOMINIO:
            assert not viola(importado, proibido), (
                f"REGRA 1 violada.\n"
                f"  {modulo_de(arquivo)} importa {importado!r}\n"
                f"  O domínio é puro: nada de rede, disco ou yt-dlp.\n"
                f"  Ver SPEC.md 4.2."
            )


@pytest.mark.parametrize("arquivo", arquivos_python("domain"), ids=modulo_de)
def test_dominio_nao_importa_ytdlp(arquivo):
    """Restrição técnica 3: o domínio não conhece o yt-dlp."""
    for importado in imports_de(arquivo):
        assert not viola(importado, "yt_dlp"), (
            f"{modulo_de(arquivo)} importa {importado!r}.\nSó src/download/ pode conhecer o yt-dlp."
        )


@pytest.mark.parametrize("arquivo", arquivos_python("domain"), ids=modulo_de)
def test_dominio_nao_toca_disco_nem_rede(arquivo):
    """O domínio não importa bibliotecas de I/O.

    `pathlib` fica de fora da proibição: ele é usado para MANIPULAR strings de
    caminho, o que é lógica pura. Quem toca o disco é `os`, `shutil`,
    `sqlite3`, `socket`, `requests`, `urllib.request`.
    """
    proibidos = ("os", "shutil", "sqlite3", "socket", "requests", "urllib.request", "http.client")
    for importado in imports_de(arquivo):
        for proibido in proibidos:
            assert not viola(importado, proibido), (
                f"{modulo_de(arquivo)} importa {importado!r}.\n"
                f"O domínio não faz I/O. Injete a dependência em vez disso —\n"
                f"como `resolver_colisao(caminho, existe)` faz com a checagem\n"
                f"de existência de arquivo."
            )


# ---------------------------------------------------------------------------
# REGRA 2 — a web fala com o pipeline, não com o domínio
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("arquivo", arquivos_python("web"), ids=modulo_de)
def test_web_nao_importa_dominio(arquivo):
    """REGRA 2: src/web/ conversa com src/pipeline.py."""
    for importado in imports_de(arquivo):
        assert not viola(importado, "src.domain"), (
            f"REGRA 2 violada.\n"
            f"  {modulo_de(arquivo)} importa {importado!r}\n"
            f"  A camada web só orquestra: ela fala com src.pipeline.\n"
            f"  Ver SPEC.md 4.2."
        )


# ---------------------------------------------------------------------------
# Fronteira do yt-dlp
# ---------------------------------------------------------------------------


def test_apenas_o_adapter_conhece_ytdlp():
    """Restrição técnica 1: `import yt_dlp` só existe em src/download/."""
    infratores = []
    for arquivo in SRC.rglob("*.py"):
        if "download" in arquivo.relative_to(SRC).parts:
            continue
        for importado in imports_de(arquivo):
            if viola(importado, "yt_dlp"):
                infratores.append(f"{modulo_de(arquivo)} -> {importado}")

    assert not infratores, "Só src/download/ pode importar yt_dlp.\n  " + "\n  ".join(infratores)


# ---------------------------------------------------------------------------
# Sanidade do próprio teste
# ---------------------------------------------------------------------------


def test_o_teste_esta_realmente_olhando_arquivos():
    """Guarda contra falso-positivo.

    Se um refactor mover as pastas, os parametrize acima ficam com lista vazia
    e todos os testes "passam" sem verificar nada. Este teste quebra nesse caso.
    """
    assert arquivos_python("domain"), "Nenhum arquivo encontrado em src/domain/"
    assert arquivos_python("web"), "Nenhum arquivo encontrado em src/web/"


@pytest.mark.parametrize("pacote", ["domain", "download", "storage", "queue", "web"])
def test_todo_pacote_de_src_esta_presente(pacote):
    """Cada camada do SPEC 4.1 existe e tem código.

    Nasceu de um incidente: o .gitignore tinha "download/" sem barra inicial,
    que casa em qualquer profundidade, e src/download/ ficou fora do remoto
    por semanas sem nenhum teste reclamar. Um clone limpo passava na suíte
    com o adapter inteiro ausente.
    """
    arquivos = arquivos_python(pacote)
    assert arquivos, f"src/{pacote}/ não existe ou está vazio"
    assert any(a.name != "__init__.py" for a in arquivos), f"src/{pacote}/ só tem __init__.py"


def test_resolucao_de_import_relativo():
    """O resolvedor de import relativo funciona.

    É a parte mais fácil de errar em silêncio: se ele resolvesse errado, um
    `from ..domain import x` passaria despercebido e a REGRA 2 não valeria nada.

    Sem skip: se o arquivo sumir, o teste tem que FALHAR. Um skip aqui foi o
    que deixou src/download/ ausente do remoto passar despercebido.
    """
    arquivo = SRC / "download" / "traducao_erros.py"
    assert arquivo.exists(), "src/download/traducao_erros.py sumiu"

    importados = imports_de(arquivo)
    assert "src.domain.erros" in importados, (
        f"O import relativo `from ..domain.erros import ...` deveria resolver "
        f"para 'src.domain.erros'. Resolvido: {importados}"
    )
