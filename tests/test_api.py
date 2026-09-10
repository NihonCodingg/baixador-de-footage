"""Testes de T6 (rodada 2) — a camada web. Só JSON, zero regra de negócio.

O Pipeline é um dublê: os testes verificam rotas, corpos, códigos de status e
a forma única de erro ({"erro": ...}). Nada sobe worker nem toca a rede.

Referência: SPEC 11.
"""

import json

import pytest
from fastapi.testclient import TestClient

from src.pipeline import Conflito, EntradaInvalida, NaoEncontrado
from src.web.app import HOST, PASTA_WEB, criar_app


class PipelineFalso:
    """Registra chamadas e devolve respostas roteirizadas."""

    def __init__(self):
        self.chamadas = []
        self.encerrado = False
        self.erro_enfileirar = None
        self.erro_abrir = None
        self.erro_projeto = None
        self.pasta_escolhida = "D:/FOOTAGE/escolhida"
        self.explodir = False

    def inspecionar(self, texto):
        self.chamadas.append(("inspecionar", texto))
        if self.explodir:
            raise RuntimeError("bug interno")
        return [
            {
                "ok": True,
                "original": texto,
                "url": "u",
                "e_youtube": True,
                "aviso": None,
                "video": {"titulo": "t"},
                "baixados": {},
            }
        ]

    def enfileirar(self, urls, perfil, projeto=None, forcar=False, pasta=None):
        self.chamadas.append(("enfileirar", list(urls), perfil, projeto, forcar, pasta))
        if self.erro_enfileirar:
            raise self.erro_enfileirar
        return ["id-1", "id-2"][: len(urls)]

    def cookies(self):
        self.chamadas.append(("cookies",))
        return {
            "navegador": None,
            "perfil": None,
            "ativo": False,
            "motivo": None,
            "navegadores": ["chrome", "firefox"],
        }

    def definir_cookies(self, navegador, perfil=None):
        self.chamadas.append(("definir_cookies", navegador, perfil))
        if self.erro_projeto:
            raise self.erro_projeto
        return {
            "navegador": navegador,
            "perfil": perfil,
            "ativo": bool(navegador),
            "motivo": None,
            "navegadores": ["chrome", "firefox"],
        }

    def projetos(self):
        self.chamadas.append(("projetos",))
        return [
            {
                "nome": "cliente_x",
                "rotulo": "Cliente X",
                "pasta": "D:/FOOTAGE/cliente_x",
                "valido": True,
                "motivo": None,
            }
        ]

    def adicionar_projeto(self, nome, caminho, rotulo=None):
        self.chamadas.append(("adicionar_projeto", nome, caminho, rotulo))
        if self.erro_projeto:
            raise self.erro_projeto
        return {
            "nome": nome,
            "rotulo": rotulo or nome,
            "pasta": caminho,
            "valido": True,
            "motivo": None,
        }

    def remover_projeto(self, nome):
        self.chamadas.append(("remover_projeto", nome))
        if self.erro_projeto:
            raise self.erro_projeto
        return True

    def escolher_pasta(self):
        self.chamadas.append(("escolher_pasta",))
        if self.erro_projeto:
            raise self.erro_projeto
        return self.pasta_escolhida

    def estado_fila(self):
        self.chamadas.append(("estado_fila",))
        return [{"id": "id-1", "estado": "na_fila", "progresso": None}]

    def cancelar(self, job_id):
        self.chamadas.append(("cancelar", job_id))
        if job_id == "nao-existe":
            raise NaoEncontrado("Job 'nao-existe' não existe.")
        if job_id == "andamento":
            raise Conflito("Só é possível cancelar um job que ainda não começou.")
        return True

    def historico(self, termo=None, projeto=None, limite=100):
        self.chamadas.append(("historico", termo, projeto, limite))
        return [{"video_id": "x", "status": "concluido"}]

    def config(self):
        self.chamadas.append(("config",))
        return {"ffmpeg": {"disponivel": True}, "perfis": [], "projetos": []}

    def abrir_pasta(self, caminho):
        self.chamadas.append(("abrir_pasta", caminho))
        if self.erro_abrir:
            raise self.erro_abrir
        return "D:/FOOTAGE/cliente_x"

    def encerrar(self):
        self.encerrado = True


@pytest.fixture
def web(tmp_path):
    pasta = tmp_path / "web"
    pasta.mkdir()
    (pasta / "index.html").write_text("<!doctype html><title>Baixador</title>", encoding="utf-8")
    (pasta / "style.css").write_text(":root{}", encoding="utf-8")
    return pasta


@pytest.fixture
def cliente(web):
    pipeline = PipelineFalso()
    app = criar_app(pipeline, pasta_web=web)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, pipeline


# ===========================================================================
# POST /api/inspecionar
# ===========================================================================


def test_inspecionar_devolve_itens(cliente):
    c, p = cliente
    r = c.post("/api/inspecionar", json={"links": "https://youtu.be/x\nhttps://youtu.be/y"})
    assert r.status_code == 200
    assert r.json()["itens"][0]["ok"] is True
    assert p.chamadas == [("inspecionar", "https://youtu.be/x\nhttps://youtu.be/y")]


def test_inspecionar_sem_campo_links_e_422_normalizado(cliente):
    """Erro de validação do corpo também sai na forma {"erro": ...}: a tela
    trata um formato só."""
    c, _ = cliente
    r = c.post("/api/inspecionar", json={})
    assert r.status_code == 422
    assert "erro" in r.json()


def test_inspecionar_links_vazio_e_aceito_e_devolve_lista_vazia(cliente):
    c, p = cliente
    p.inspecionar = lambda texto: []
    r = c.post("/api/inspecionar", json={"links": ""})
    assert r.status_code == 200 and r.json()["itens"] == []


# ===========================================================================
# POST /api/fila
# ===========================================================================


def test_enfileirar_devolve_ids(cliente):
    c, p = cliente
    r = c.post(
        "/api/fila",
        json={"urls": ["https://youtu.be/x"], "perfil": "edicao_1080", "projeto": "pessoal"},
    )
    assert r.status_code == 200
    assert r.json() == {"ids": ["id-1"]}
    assert p.chamadas[-1] == (
        "enfileirar",
        ["https://youtu.be/x"],
        "edicao_1080",
        "pessoal",
        False,
        None,
    )


def test_enfileirar_repassa_forcar(cliente):
    c, p = cliente
    c.post("/api/fila", json={"urls": ["u"], "perfil": "p", "projeto": "j", "forcar": True})
    assert p.chamadas[-1] == ("enfileirar", ["u"], "p", "j", True, None)


def test_enfileirar_entrada_invalida_e_400(cliente):
    c, p = cliente
    p.erro_enfileirar = EntradaInvalida("Perfil 'x' não existe.")
    r = c.post("/api/fila", json={"urls": ["u"], "perfil": "x", "projeto": "j"})
    assert r.status_code == 400
    assert r.json() == {"erro": "Perfil 'x' não existe."}


def test_enfileirar_conflito_e_409(cliente):
    c, p = cliente
    p.erro_enfileirar = Conflito("Já baixado: D:/F/a.mp4")
    r = c.post("/api/fila", json={"urls": ["u"], "perfil": "p", "projeto": "j"})
    assert r.status_code == 409
    assert "D:/F/a.mp4" in r.json()["erro"]


@pytest.mark.parametrize(
    "corpo",
    [
        {},
        {"urls": "nao-e-lista", "perfil": "p", "projeto": "j"},
        {"urls": ["u"], "projeto": "j"},
    ],
)
def test_enfileirar_corpo_malformado_e_422(cliente, corpo):
    c, _ = cliente
    r = c.post("/api/fila", json=corpo)
    assert r.status_code == 422
    assert "erro" in r.json()


# ===========================================================================
# GET /api/fila  e  DELETE /api/fila/{id}
# ===========================================================================


def test_sem_projeto_e_sem_pasta_e_400_nao_422(cliente):
    """Com a pasta avulsa, `projeto` deixou de ser obrigatório no schema.
    Faltar os dois passou a ser regra de negócio — 400, com mensagem — e não
    mais erro de forma."""
    c, p = cliente
    p.erro_enfileirar = EntradaInvalida("Informe o projeto de destino, ou uma pasta avulsa.")
    r = c.post("/api/fila", json={"urls": ["u"], "perfil": "p"})
    assert r.status_code == 400
    assert "pasta avulsa" in r.json()["erro"]


def test_fila_repassa_a_pasta_avulsa(cliente):
    c, p = cliente
    c.post("/api/fila", json={"urls": ["u"], "perfil": "p", "pasta": "D:/FOOTAGE/avulsa"})
    assert ("enfileirar", ["u"], "p", None, False, "D:/FOOTAGE/avulsa") in p.chamadas


def test_estado_da_fila(cliente):
    c, _ = cliente
    r = c.get("/api/fila")
    assert r.status_code == 200
    assert r.json()["jobs"][0]["id"] == "id-1"


def test_cancelar_ok(cliente):
    c, p = cliente
    r = c.delete("/api/fila/id-1")
    assert r.status_code == 200
    assert r.json() == {"cancelado": True}
    assert ("cancelar", "id-1") in p.chamadas


def test_cancelar_inexistente_e_404(cliente):
    c, _ = cliente
    r = c.delete("/api/fila/nao-existe")
    assert r.status_code == 404
    assert "erro" in r.json()


def test_cancelar_em_andamento_e_409(cliente):
    c, _ = cliente
    r = c.delete("/api/fila/andamento")
    assert r.status_code == 409
    assert "começou" in r.json()["erro"]


# ===========================================================================
# GET /api/historico
# ===========================================================================


def test_historico_sem_filtro(cliente):
    c, p = cliente
    r = c.get("/api/historico")
    assert r.status_code == 200
    assert r.json()["registros"][0]["status"] == "concluido"
    assert p.chamadas[-1] == ("historico", None, None, 100)


def test_historico_com_filtros(cliente):
    c, p = cliente
    c.get("/api/historico", params={"termo": "seleção", "projeto": "pessoal", "limite": 5})
    assert p.chamadas[-1] == ("historico", "seleção", "pessoal", 5)


@pytest.mark.parametrize("limite", [0, -1, 10001, "dez"])
def test_historico_limite_invalido_e_422(cliente, limite):
    c, _ = cliente
    r = c.get("/api/historico", params={"limite": limite})
    assert r.status_code == 422


# ===========================================================================
# GET /api/config
# ===========================================================================


def test_config(cliente):
    c, _ = cliente
    r = c.get("/api/config")
    assert r.status_code == 200
    assert r.json()["ffmpeg"]["disponivel"] is True


# ===========================================================================
# Estáticos e erros
# ===========================================================================


def test_raiz_serve_o_index(cliente):
    c, _ = cliente
    r = c.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Baixador" in r.text


def test_arquivo_estatico(cliente):
    c, _ = cliente
    assert c.get("/style.css").status_code == 200


def test_rota_de_api_inexistente_e_404(cliente):
    c, _ = cliente
    assert c.get("/api/nao-existe").status_code == 404


def test_erro_interno_nao_vaza_stack_trace(cliente):
    """Restrição técnica 2: nunca stack trace na interface."""
    c, p = cliente
    p.explodir = True
    r = c.post("/api/inspecionar", json={"links": "x"})
    assert r.status_code == 500
    corpo = r.json()
    assert "erro" in corpo
    assert "Traceback" not in r.text and 'File "' not in r.text


def test_respostas_sao_json(cliente):
    c, _ = cliente
    for r in (c.get("/api/fila"), c.get("/api/config"), c.get("/api/historico")):
        assert r.headers["content-type"].startswith("application/json")
        json.loads(r.text)


def test_encerra_o_pipeline_ao_desligar(web):
    pipeline = PipelineFalso()
    app = criar_app(pipeline, pasta_web=web)
    with TestClient(app):
        assert pipeline.encerrado is False
    assert pipeline.encerrado is True


# ===========================================================================
# POST /api/cookies
# ===========================================================================


def test_liga_cookies(cliente):
    c, p = cliente
    r = c.post("/api/cookies", json={"navegador": "firefox", "perfil": "default"})
    assert r.status_code == 200
    assert r.json()["cookies"]["ativo"] is True
    assert ("definir_cookies", "firefox", "default") in p.chamadas


def test_desliga_cookies_com_navegador_nulo(cliente):
    c, p = cliente
    r = c.post("/api/cookies", json={"navegador": None})
    assert r.status_code == 200
    assert r.json()["cookies"]["ativo"] is False


def test_corpo_vazio_desliga_em_vez_de_422(cliente):
    """Os dois campos são opcionais: corpo vazio quer dizer desligado."""
    assert cliente[0].post("/api/cookies", json={}).status_code == 200


def test_navegador_invalido_e_400(cliente):
    c, p = cliente
    p.erro_projeto = EntradaInvalida("Navegador 'netscape' não é suportado pelo yt-dlp.")
    r = c.post("/api/cookies", json={"navegador": "netscape"})
    assert r.status_code == 400
    assert "não é suportado" in r.json()["erro"]


# ===========================================================================
# Projetos pela tela
# ===========================================================================


def test_lista_projetos(cliente):
    c, _ = cliente
    r = c.get("/api/projetos")
    assert r.status_code == 200
    assert r.json()["projetos"][0]["nome"] == "cliente_x"


def test_adiciona_projeto(cliente):
    c, p = cliente
    r = c.post(
        "/api/projetos",
        json={"nome": "novo", "caminho": "D:/FOOTAGE/novo", "rotulo": "Cliente Novo"},
    )
    assert r.status_code == 200
    assert r.json()["projeto"]["nome"] == "novo"
    assert ("adicionar_projeto", "novo", "D:/FOOTAGE/novo", "Cliente Novo") in p.chamadas


def test_adiciona_projeto_sem_rotulo(cliente):
    c, p = cliente
    c.post("/api/projetos", json={"nome": "novo", "caminho": "D:/FOOTAGE/novo"})
    assert ("adicionar_projeto", "novo", "D:/FOOTAGE/novo", None) in p.chamadas


def test_adiciona_projeto_com_pasta_ruim_e_400(cliente):
    c, p = cliente
    p.erro_projeto = EntradaInvalida("Pasta inválida: a pasta não existe: D:/nao")
    r = c.post("/api/projetos", json={"nome": "novo", "caminho": "D:/nao"})
    assert r.status_code == 400
    assert "não existe" in r.json()["erro"]


def test_adiciona_projeto_com_nome_repetido_e_409(cliente):
    c, p = cliente
    p.erro_projeto = Conflito("Já existe um projeto chamado 'cliente_x'.")
    r = c.post("/api/projetos", json={"nome": "cliente_x", "caminho": "D:/x"})
    assert r.status_code == 409


def test_adiciona_projeto_sem_caminho_e_422(cliente):
    c, _ = cliente
    r = c.post("/api/projetos", json={"nome": "novo"})
    assert r.status_code == 422


def test_remove_projeto(cliente):
    c, p = cliente
    r = c.delete("/api/projetos/cliente_x")
    assert r.status_code == 200 and r.json() == {"removido": True}
    assert ("remover_projeto", "cliente_x") in p.chamadas


def test_remove_projeto_inexistente_e_404(cliente):
    c, p = cliente
    p.erro_projeto = NaoEncontrado("Projeto 'nao_existe' não existe.")
    assert c.delete("/api/projetos/nao_existe").status_code == 404


def test_remove_projeto_com_download_ativo_e_409(cliente):
    c, p = cliente
    p.erro_projeto = Conflito("O projeto 'cliente_x' tem download em andamento.")
    assert c.delete("/api/projetos/cliente_x").status_code == 409


# ===========================================================================
# POST /api/escolher-pasta
# ===========================================================================


def test_escolher_pasta_devolve_o_caminho(cliente):
    c, p = cliente
    r = c.post("/api/escolher-pasta")
    assert r.status_code == 200
    assert r.json() == {"caminho": "D:/FOOTAGE/escolhida"}
    assert ("escolher_pasta",) in p.chamadas


def test_escolher_pasta_cancelado_devolve_nulo(cliente):
    """Cancelar não é erro: a tela só não preenche o campo."""
    c, p = cliente
    p.pasta_escolhida = None
    r = c.post("/api/escolher-pasta")
    assert r.status_code == 200 and r.json() == {"caminho": None}


def test_escolher_pasta_indisponivel_e_400(cliente):
    c, p = cliente
    p.erro_projeto = EntradaInvalida("O seletor de pasta ficou aberto tempo demais e foi fechado.")
    r = c.post("/api/escolher-pasta")
    assert r.status_code == 400
    assert "tempo demais" in r.json()["erro"]


# ===========================================================================
# POST /api/abrir-pasta
# ===========================================================================


def test_abrir_pasta_repassa_o_caminho_e_devolve_a_pasta(cliente):
    c, pipeline = cliente
    r = c.post("/api/abrir-pasta", json={"caminho": "D:/FOOTAGE/cliente_x/v.mp4"})
    assert r.status_code == 200
    assert r.json() == {"aberto": True, "pasta": "D:/FOOTAGE/cliente_x"}
    assert ("abrir_pasta", "D:/FOOTAGE/cliente_x/v.mp4") in pipeline.chamadas


def test_abrir_pasta_fora_dos_projetos_e_400(cliente):
    """A recusa vem do pipeline e chega como a forma única de erro."""
    c, pipeline = cliente
    pipeline.erro_abrir = EntradaInvalida(
        "Só é possível abrir pastas dentro de um projeto configurado. "
        "Fora de todos eles: C:/Windows/System32"
    )
    r = c.post("/api/abrir-pasta", json={"caminho": "C:/Windows/System32"})
    assert r.status_code == 400
    assert "projeto configurado" in r.json()["erro"]
    assert "detalhes" not in r.json()


def test_abrir_pasta_sem_campo_caminho_e_422(cliente):
    c, _ = cliente
    r = c.post("/api/abrir-pasta", json={})
    assert r.status_code == 422
    assert r.json()["erro"] == "Corpo ou parâmetros inválidos."


# ===========================================================================
# Estáticos — a pasta web/ de verdade, a que vai para o usuário
# ===========================================================================


def test_serve_os_arquivos_reais_da_pasta_web():
    """Os testes de estáticos acima usam uma pasta de mentira. Este confere a
    pasta web/ do repositório: sem ela servida, `python -m src.web` abre o
    navegador numa página em branco."""
    app = criar_app(PipelineFalso())  # sem pasta_web: usa a real
    with TestClient(app) as c:
        raiz = c.get("/")
        assert raiz.status_code == 200
        assert "<title>" in raiz.text

        for arquivo, tipo in (("/app.js", "javascript"), ("/style.css", "css")):
            r = c.get(arquivo)
            assert r.status_code == 200, f"{arquivo} não é servido"
            assert tipo in r.headers["content-type"], arquivo


def test_o_front_nao_tem_fonte_de_dados_alem_da_api():
    """T7: o app.js chegou do desenho com um servidor de demonstração que
    substituía a API em silêncio quando ela não respondia — a tela mostrava
    downloads e histórico que nunca existiram, sem dizer nada. Isso não pode
    voltar: quando a API não responde, a tela avisa e fica vazia."""
    js = (PASTA_WEB / "app.js").read_text(encoding="utf-8")
    assert "fetch(" in js, "sanidade: é este o arquivo que fala com a API"
    assert "DEMO" not in js.upper(), "voltou um modo de demonstração no front"


# ===========================================================================
# main() — vincula em 127.0.0.1
# ===========================================================================


def test_host_e_loopback():
    assert HOST == "127.0.0.1"


class TimerFalso:
    """Registra o agendamento em vez de abrir navegador durante o teste."""

    criados: list["TimerFalso"] = []

    def __init__(self, atraso, funcao, args=()):
        self.atraso, self.funcao, self.args = atraso, funcao, args
        self.iniciado = self.cancelado = False
        TimerFalso.criados.append(self)

    def start(self):
        self.iniciado = True

    def cancel(self):
        self.cancelado = True


def test_main_abre_a_pagina_no_navegador(monkeypatch, web):
    """`python -m src.web` abre a página sozinho: o usuário roda um comando,
    não dois. O Timer existe porque uvicorn.run bloqueia — e é cancelado no
    fim para não abrir aba nenhuma se o servidor não subir."""
    import src.web.app as app_mod

    TimerFalso.criados.clear()
    aberto = []
    monkeypatch.setattr(app_mod, "Pipeline", lambda *a, **k: PipelineFalso())
    monkeypatch.setattr(app_mod.uvicorn, "run", lambda app, **kw: None)
    monkeypatch.setattr(app_mod.threading, "Timer", TimerFalso)

    def registrar(url):
        aberto.append(url)

    app_mod.main(abrir_navegador=registrar)

    assert len(TimerFalso.criados) == 1
    agendado = TimerFalso.criados[0]
    assert agendado.funcao is registrar
    assert agendado.args == ("http://127.0.0.1:8000",)
    assert agendado.iniciado and agendado.cancelado
    assert aberto == [], "o dublê não dispara: quem abre é o Timer real"


def test_main_nao_abre_navegador_se_o_servidor_morre(monkeypatch, web):
    """Porta ocupada: o uvicorn levanta, e nenhuma aba deve abrir."""
    import src.web.app as app_mod

    TimerFalso.criados.clear()
    pipeline = PipelineFalso()
    monkeypatch.setattr(app_mod, "Pipeline", lambda *a, **k: pipeline)
    monkeypatch.setattr(app_mod.threading, "Timer", TimerFalso)

    def explodir(app, **kw):
        raise OSError("porta 8000 já está em uso")

    monkeypatch.setattr(app_mod.uvicorn, "run", explodir)
    with pytest.raises(OSError):
        app_mod.main(abrir_navegador=lambda url: None)

    assert TimerFalso.criados[0].cancelado
    assert pipeline.encerrado is True


def test_main_sobe_em_loopback_e_encerra_o_pipeline(monkeypatch, web):
    """Restrição técnica 5. uvicorn e Pipeline são substituídos; nada sobe."""
    import src.web.app as app_mod

    chamadas = {}
    pipeline = PipelineFalso()

    monkeypatch.setattr(app_mod, "Pipeline", lambda *a, **k: pipeline)
    monkeypatch.setattr(app_mod.uvicorn, "run", lambda app, **kw: chamadas.update(kw))
    app_mod.main()
    assert chamadas["host"] == "127.0.0.1"
    assert isinstance(chamadas["port"], int)
    assert pipeline.encerrado is True
