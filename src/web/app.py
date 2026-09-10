"""Back-end FastAPI. Só JSON, zero regra de negócio.

REGRA DURA 2: este módulo NUNCA importa de src.domain. Fala com src.pipeline,
e tudo que recebe dele já é dict serializável.

Rotas em SPEC 11. Vincula em 127.0.0.1 — sem exposição na rede.

Toda resposta de erro tem UMA forma: {"erro": "mensagem"} — inclusive o 422
de validação e o 500 interno. A tela trata um formato só, e nunca recebe
stack trace (restrição técnica 2).

Ticket: T6.
"""

import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..pipeline import Conflito, EntradaInvalida, NaoEncontrado, Pipeline

RAIZ = Path(__file__).resolve().parent.parent.parent
PASTA_WEB = RAIZ / "web"
HOST = "127.0.0.1"
PORTA = 8000
ENDERECO = f"http://{HOST}:{PORTA}"
ESPERA_NAVEGADOR = 1.0  # segundos até o uvicorn estar ouvindo


class CorpoInspecionar(BaseModel):
    links: str  # um link por linha, como colado no textarea


class CorpoFila(BaseModel):
    urls: list[str]
    perfil: str
    # Um OU outro: projeto cadastrado, ou pasta avulsa digitada na hora. Qual
    # dos dois falta é regra de negócio, e vira 400 no pipeline — não 422.
    projeto: str | None = None
    pasta: str | None = None
    forcar: bool = False  # rebaixar um vídeo já concluído neste perfil


class CorpoCookies(BaseModel):
    navegador: str | None = None  # None ou "" desliga
    perfil: str | None = None


class CorpoProjeto(BaseModel):
    nome: str  # identificador: letras, números, - e _
    caminho: str  # pasta existente e gravável
    rotulo: str | None = None  # texto para a tela; o nome, se ausente


class CorpoAbrirPasta(BaseModel):
    caminho: str  # arquivo ou pasta, sempre dentro de um projeto


def _erro(status: int, mensagem: str, **extras) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": mensagem, **extras})


def criar_app(pipeline, pasta_web: Path | None = None) -> FastAPI:
    """Monta a aplicação FastAPI sobre um Pipeline já construído.

    `pipeline` entra por injeção: os testes usam um dublê e não sobem worker
    nem tocam a rede. Os handlers são `def` (não `async`): o FastAPI os roda
    num threadpool, e a inspeção — que espera a rede — não trava o servidor.
    """

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        yield
        pipeline.encerrar()  # para o worker e fecha o histórico

    app = FastAPI(
        title="Baixador de Footage", lifespan=ciclo_de_vida, docs_url=None, redoc_url=None
    )

    # ----------------------------------------------------------- erros

    @app.exception_handler(EntradaInvalida)
    async def _entrada_invalida(request: Request, exc: EntradaInvalida):
        return _erro(400, str(exc))

    @app.exception_handler(NaoEncontrado)
    async def _nao_encontrado(request: Request, exc: NaoEncontrado):
        return _erro(404, str(exc))

    @app.exception_handler(Conflito)
    async def _conflito(request: Request, exc: Conflito):
        return _erro(409, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validacao(request: Request, exc: RequestValidationError):
        return _erro(422, "Corpo ou parâmetros inválidos.", detalhes=jsonable_encoder(exc.errors()))

    @app.exception_handler(Exception)
    async def _interno(request: Request, exc: Exception):
        # Mensagem sim, traceback nunca.
        return _erro(500, f"Erro interno: {type(exc).__name__}: {exc}")

    # ----------------------------------------------------------- rotas

    @app.post("/api/inspecionar")
    def inspecionar(corpo: CorpoInspecionar):
        return {"itens": pipeline.inspecionar(corpo.links)}

    @app.post("/api/fila")
    def enfileirar(corpo: CorpoFila):
        ids = pipeline.enfileirar(
            corpo.urls, corpo.perfil, corpo.projeto, corpo.forcar, corpo.pasta
        )
        return {"ids": ids}

    @app.get("/api/fila")
    def fila():
        return {"jobs": pipeline.estado_fila()}

    @app.delete("/api/fila/{job_id}")
    def cancelar(job_id: str):
        pipeline.cancelar(job_id)
        return {"cancelado": True}

    @app.get("/api/historico")
    def historico(
        termo: str | None = None,
        projeto: str | None = None,
        limite: int = Query(100, ge=1, le=1000),
    ):
        return {"registros": pipeline.historico(termo, projeto, limite)}

    @app.post("/api/cookies")
    def definir_cookies(corpo: CorpoCookies):
        """Liga ou desliga o --cookies-from-browser e grava no YAML."""
        return {"cookies": pipeline.definir_cookies(corpo.navegador, corpo.perfil)}

    @app.get("/api/projetos")
    def listar_projetos():
        return {"projetos": pipeline.projetos()}

    @app.post("/api/projetos")
    def adicionar_projeto(corpo: CorpoProjeto):
        """Cadastra e grava no config/projetos.yaml, preservando comentários."""
        return {"projeto": pipeline.adicionar_projeto(corpo.nome, corpo.caminho, corpo.rotulo)}

    @app.delete("/api/projetos/{nome}")
    def remover_projeto(nome: str):
        pipeline.remover_projeto(nome)
        return {"removido": True}

    @app.post("/api/escolher-pasta")
    def escolher_pasta():
        """Abre o seletor NATIVO na máquina do servidor — que é a mesma do
        navegador, por vincular em 127.0.0.1. `caminho` nulo = cancelado."""
        return {"caminho": pipeline.escolher_pasta()}

    @app.post("/api/abrir-pasta")
    def abrir_pasta(corpo: CorpoAbrirPasta):
        """Abre a pasta no explorador. Só dentro de um projeto configurado —
        a validação é do pipeline, e caminho de fora vira 400."""
        return {"aberto": True, "pasta": pipeline.abrir_pasta(corpo.caminho)}

    @app.get("/api/config")
    def config():
        return pipeline.config()

    # Estáticos por último: o mount em "/" pega tudo que as rotas não pegaram.
    app.mount("/", StaticFiles(directory=str(pasta_web or PASTA_WEB), html=True), name="web")
    return app


def main(abrir_navegador=webbrowser.open) -> None:
    """Sobe o Pipeline real, o uvicorn em 127.0.0.1 e a página no navegador.

    O navegador abre por um Timer porque `uvicorn.run` bloqueia: abrir antes
    daria uma aba na porta ainda fechada. O `cancel` no finally garante que
    nada abre se o servidor morrer na subida — por porta ocupada, por
    exemplo.

    RESEARCH 7: o console do Windows é cp1252, e uma mensagem de erro com
    título de vídeo derrubaria o processo na hora de imprimir.
    """
    sys.stdout.reconfigure(encoding="utf-8")
    pipeline = Pipeline(RAIZ / "config", RAIZ / "data")
    print(f"Baixador de Footage em {ENDERECO} — Ctrl+C para encerrar.")

    temporizador = threading.Timer(ESPERA_NAVEGADOR, abrir_navegador, (ENDERECO,))
    temporizador.daemon = True
    temporizador.start()
    try:
        uvicorn.run(criar_app(pipeline), host=HOST, port=PORTA, log_level="warning")
    finally:
        temporizador.cancel()
        pipeline.encerrar()
