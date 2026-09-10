"""Testes de T8 — a linha de comando.

O Pipeline é injetado: a maior parte dos testes usa um dublê, e os que
precisam do pipeline de verdade usam o downloader falso das outras suítes.
Nenhum teste toca a rede, e nenhum abre janela de explorador.

Referência: PLAN T8, SPEC 11.2.
"""

import shutil
from pathlib import Path

import pytest
import yaml

from src.cli import construir_parser, fmt_bytes, fmt_data, fmt_duracao, main, relatorio
from src.pipeline import Conflito, EntradaInvalida, Pipeline

RAIZ = Path(__file__).resolve().parent.parent
URL = "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw"

# Título com tudo que quebra console cp1252 e nome de arquivo no Windows:
# acento, emoji, caractere proibido e barra.
TITULO_DIFICIL = 'Seleção 🇧🇷: "final" <2026> | mapa 1/2 — ação'


class Saida:
    """Coletor de linhas, no lugar do print."""

    def __init__(self):
        self.linhas = []

    def __call__(self, texto="", fim="\n"):
        self.linhas.append(str(texto))

    @property
    def texto(self):
        return "\n".join(self.linhas)


class PipelineFalso:
    """Roteiriza as respostas e registra o que a CLI pediu."""

    def __init__(self, *, jobs=None, erro=None, historico=None, simulacao=None):
        self.chamadas = []
        self.encerrado = False
        self._jobs = jobs or []
        self._erro = erro
        self._historico = historico or []
        self._simulacao = simulacao or []

    def config(self):
        self.chamadas.append(("config",))
        return {
            "ffmpeg": {
                "disponivel": True,
                "completo": True,
                "ffmpeg": "C:/x/ffmpeg.exe",
                "ffprobe": "C:/x/ffprobe.exe",
            },
            "perfis": [
                {
                    "nome": "edicao_1080",
                    "descricao": "1080p H.264 + AAC",
                    "disponivel": True,
                    "exige_ffmpeg": True,
                    "limite_dimensao": 1080,
                    "container": "mp4",
                },
                {
                    "nome": "so_audio",
                    "descricao": "Só a trilha de áudio",
                    "disponivel": False,
                    "exige_ffmpeg": True,
                    "limite_dimensao": None,
                    "container": "m4a",
                },
            ],
            "projetos": [
                {
                    "nome": "cliente_x",
                    "rotulo": "Cliente X",
                    "pasta": "D:/FOOTAGE/cliente_x",
                    "valido": True,
                    "motivo": None,
                },
                {
                    "nome": "quebrado",
                    "rotulo": "Quebrado",
                    "pasta": "Z:/nao/existe",
                    "valido": False,
                    "motivo": "a unidade não existe",
                },
            ],
        }

    def historico(self, termo=None, projeto=None, limite=100):
        self.chamadas.append(("historico", termo, projeto, limite))
        return self._historico

    def simular(self, urls, perfil, projeto=None, pasta=None):
        self.chamadas.append(("simular", list(urls), perfil, projeto, pasta))
        if self._erro:
            raise self._erro
        return self._simulacao

    def enfileirar(self, urls, perfil, projeto=None, forcar=False, pasta=None):
        self.chamadas.append(("enfileirar", list(urls), perfil, projeto, forcar, pasta))
        if self._erro:
            raise self._erro
        return [j["id"] for j in self._jobs]

    def estado_fila(self):
        self.chamadas.append(("estado_fila",))
        return self._jobs

    def encerrar(self):
        self.encerrado = True


def job(**campos):
    base = {
        "id": "j1",
        "estado": "concluido",
        "ja_existia": False,
        "perfil": "edicao_1080",
        "projeto": "cliente_x",
        "criado_em": "2026-09-02T19:53:58+00:00",
        "url": URL,
        "video": {
            "id": "LzS8kB6lIm0",
            "titulo": "Camisa azul da Seleção",
            "canal": "Canal Michuruca",
            "duracao_s": 65,
            "thumbnail": None,
        },
        "progresso": None,
        "caminho_final": "D:/FOOTAGE/cliente_x/v.mp4",
        "motivo_falha": None,
        "mensagem_falha": None,
        "aviso": None,
    }
    base.update(campos)
    return base


# ===========================================================================
# Formatação
# ===========================================================================


# Tabela protegida do formatador: entrada e saída esperada lado a lado.
# fmt: off
@pytest.mark.parametrize("segundos,esperado", [
    (None, "--:--"), (0, "0:00"), (65, "1:05"), (3725, "1:02:05"), (59.6, "1:00"),
])
# fmt: on
def test_formata_duracao(segundos, esperado):
    assert fmt_duracao(segundos) == esperado


# Tabela protegida do formatador: entrada e saída esperada lado a lado.
# fmt: off
@pytest.mark.parametrize("bytes_,esperado", [
    (None, "--"), (0, "0 B"), (900, "900 B"), (9_437_184, "9,0 MB"),
    (11_062_598, "10,6 MB"),   # 10,5501... arredonda para cima
])
# fmt: on
def test_formata_bytes_com_virgula_decimal(bytes_, esperado):
    assert fmt_bytes(bytes_) == esperado


def test_formata_data_invalida_nao_explode():
    assert fmt_data(None) == "--"
    assert fmt_data("isso não é data") == "isso não é data"


# ===========================================================================
# Argumentos
# ===========================================================================

def test_parser_le_links_perfil_e_projeto():
    a = construir_parser().parse_args(
        ["--perfil", "edicao_1080", "--projeto", "cliente_x", "URL1", "URL2"])
    assert a.urls == ["URL1", "URL2"]
    assert a.perfil == "edicao_1080" and a.projeto == "cliente_x"
    assert a.dry_run is False and a.forcar is False


def test_parser_aceita_historico_sem_termo():
    assert construir_parser().parse_args(["--historico"]).historico == ""
    assert construir_parser().parse_args(["--historico", "selecao"]).historico == "selecao"


def test_sem_argumento_nenhum_orienta_e_falha():
    saida = Saida()
    assert main([], pipeline=PipelineFalso(), escrever=saida) == 1
    assert "--perfis" in saida.texto


def test_link_sem_perfil_falha_com_mensagem_util():
    saida = Saida()
    assert main([URL], pipeline=PipelineFalso(), escrever=saida) == 1
    assert "--perfil" in saida.texto


# ===========================================================================
# Listagens
# ===========================================================================

def test_perfis_lista_e_marca_o_indisponivel():
    saida = Saida()
    assert main(["--perfis"], pipeline=PipelineFalso(), escrever=saida) == 0
    assert "edicao_1080" in saida.texto
    assert "até 1080p" in saida.texto and ".mp4" in saida.texto
    assert "indisponível" in saida.texto and "ffmpeg" in saida.texto


def test_projetos_lista_pasta_e_motivo_do_invalido():
    saida = Saida()
    assert main(["--projetos"], pipeline=PipelineFalso(), escrever=saida) == 0
    assert "D:/FOOTAGE/cliente_x" in saida.texto
    assert "a unidade não existe" in saida.texto


def test_historico_repassa_termo_projeto_e_limite():
    falso = PipelineFalso(historico=[{
        "titulo": "Camisa azul da Seleção", "perfil": "edicao_1080",
        "projeto": "cliente_x", "caminho": "D:/FOOTAGE/cliente_x/v.mp4",
        "tamanho_bytes": 9_437_184, "resolucao": "1080x1920", "status": "concluido",
        "ja_existia": False, "aviso": None, "motivo_falha": None,
        "mensagem_falha": None, "criado_em": None,
        "concluido_em": "2026-09-02T19:53:58+00:00",
    }])
    saida = Saida()
    codigo = main(["--historico", "selecao", "--projeto", "cliente_x", "--limite", "5"],
                  pipeline=falso, escrever=saida)
    assert codigo == 0
    assert ("historico", "selecao", "cliente_x", 5) in falso.chamadas
    assert "9,0 MB" in saida.texto and "1080x1920" in saida.texto
    assert "D:/FOOTAGE/cliente_x/v.mp4" in saida.texto


def test_historico_mostra_aviso_e_marca_ja_existia():
    falso = PipelineFalso(historico=[{
        "titulo": "T", "perfil": "edicao_1080", "projeto": "cliente_x",
        "caminho": "D:/f/v.mp4", "tamanho_bytes": None, "resolucao": None,
        "status": "concluido", "ja_existia": True,
        "aviso": "O arquivo já existia no destino; o download foi pulado.",
        "motivo_falha": None, "mensagem_falha": None,
        "criado_em": None, "concluido_em": None,
    }])
    saida = Saida()
    main(["--historico"], pipeline=falso, escrever=saida)
    assert "[já existia]" in saida.texto
    assert "já existia no destino" in saida.texto


def test_historico_vazio_distingue_busca_sem_resultado_de_historico_vazio():
    saida = Saida()
    assert main(["--historico", "nada"], pipeline=PipelineFalso(), escrever=saida) == 0
    assert "Nenhum registro para essa busca" in saida.texto

    saida = Saida()
    assert main(["--historico"], pipeline=PipelineFalso(), escrever=saida) == 0
    assert "histórico está vazio" in saida.texto


# ===========================================================================
# Erros do pipeline — a CLI não reescreve a mensagem
# ===========================================================================

def test_perfil_inexistente_sai_com_1_e_a_mensagem_do_pipeline():
    falso = PipelineFalso(erro=EntradaInvalida("Perfil 'nao_existe' não existe."))
    saida = Saida()
    codigo = main(["--perfil", "nao_existe", "--projeto", "cliente_x", URL],
                  pipeline=falso, escrever=saida)
    assert codigo == 1
    assert "Perfil 'nao_existe' não existe." in saida.texto


def test_conflito_de_duplicata_chega_ao_terminal():
    falso = PipelineFalso(erro=Conflito(
        "Já baixado no perfil 'edicao_1080': D:/f/v.mp4. Use forcar=true."))
    saida = Saida()
    codigo = main(["--perfil", "edicao_1080", "--projeto", "cliente_x", URL],
                  pipeline=falso, escrever=saida)
    assert codigo == 1
    assert "Já baixado no perfil" in saida.texto


def test_forcar_e_repassado():
    falso = PipelineFalso(jobs=[job()])
    main(["--perfil", "edicao_1080", "--projeto", "cliente_x", "--forcar", URL],
         pipeline=falso, escrever=Saida())
    assert ("enfileirar", [URL], "edicao_1080", "cliente_x", True, None) in falso.chamadas


def test_pasta_avulsa_dispensa_o_projeto():
    """A CLI não pode ficar atrás da tela: o critério do PLAN é as duas
    produzirem o mesmo resultado para a mesma entrada."""
    falso = PipelineFalso(jobs=[job(projeto="avulso")])
    saida = Saida()
    codigo = main(["--perfil", "edicao_1080", "--pasta", "D:/FOOTAGE/avulsa", URL],
                  pipeline=falso, escrever=saida)
    assert codigo == 0
    assert ("enfileirar", [URL], "edicao_1080", None, False,
            "D:/FOOTAGE/avulsa") in falso.chamadas
    assert "pasta avulsa D:/FOOTAGE/avulsa" in saida.texto


def test_dry_run_aceita_pasta_avulsa():
    falso = PipelineFalso(simulacao=[])
    main(["--dry-run", "--perfil", "edicao_1080", "--pasta", "D:/FOOTAGE/avulsa", URL],
         pipeline=falso, escrever=Saida())
    assert ("simular", [URL], "edicao_1080", None, "D:/FOOTAGE/avulsa") in falso.chamadas


def test_sem_projeto_e_sem_pasta_orienta_os_dois():
    saida = Saida()
    assert main(["--perfil", "edicao_1080", URL],
                pipeline=PipelineFalso(), escrever=saida) == 1
    assert "--pasta" in saida.texto


# ===========================================================================
# Relatório final
# ===========================================================================

def test_relatorio_separa_sucesso_ja_existia_e_falha():
    saida = Saida()
    codigo = relatorio([
        job(id="a"),
        job(id="b", ja_existia=True,
            aviso="O arquivo já existia no destino; o download foi pulado."),
        job(id="c", estado="falhou", motivo_falha="rede",
            mensagem_falha="A conexão caiu.", caminho_final=None),
    ], saida)
    assert codigo == 1, "houve falha: o código de saída tem que ser 1"
    assert "baixados:    1" in saida.texto
    assert "já existiam: 1" in saida.texto
    assert "falhas:      1" in saida.texto
    assert "[já existia]" in saida.texto
    assert "rede: A conexão caiu." in saida.texto
    assert "já existia no destino" in saida.texto, "o aviso não pode sumir"


def test_relatorio_so_de_sucesso_sai_com_zero():
    assert relatorio([job(id="a"), job(id="b")], Saida()) == 0


def test_relatorio_conta_interrompido_como_falha():
    saida = Saida()
    codigo = relatorio([job(estado="interrompido", caminho_final=None,
                            aviso="Há um arquivo parcial no destino.")], saida)
    assert codigo == 1
    assert "outros:      1" in saida.texto
    assert "arquivo parcial" in saida.texto


def test_titulo_com_emoji_e_caractere_proibido_nao_quebra_a_impressao():
    """RESEARCH 7.4: o console do Windows é cp1252. Um título assim derrubava
    o processo antes do reconfigure."""
    saida = Saida()
    relatorio([job(video={"id": "x", "titulo": TITULO_DIFICIL, "canal": None,
                          "duracao_s": None, "thumbnail": None})], saida)
    assert "Seleção" in saida.texto


# ===========================================================================
# Download, com a fila de verdade
# ===========================================================================

def test_acompanha_ate_o_fim_e_relata_o_caminho():
    falso = PipelineFalso(jobs=[job(id="j1"), job(id="j2", ja_existia=True)])
    saida = Saida()
    codigo = main(["--perfil", "edicao_1080", "--projeto", "cliente_x", URL],
                  pipeline=falso, escrever=saida)
    assert codigo == 0
    assert "2 links na fila" in saida.texto
    assert "D:/FOOTAGE/cliente_x/v.mp4" in saida.texto
    assert ("estado_fila",) in falso.chamadas


def test_pipeline_injetado_nao_e_encerrado_pela_cli():
    """Quem cria, encerra. A CLI só fecha o pipeline que ela mesma subiu."""
    falso = PipelineFalso(jobs=[job()])
    main(["--perfis"], pipeline=falso, escrever=Saida())
    assert falso.encerrado is False


# ===========================================================================
# --dry-run — o que ele mostra, e o que ele NÃO faz
# ===========================================================================

@pytest.fixture
def ambiente_real(tmp_path):
    """config/ com os perfis reais e um projeto dentro do tmp_path."""
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(RAIZ / "config" / "perfis.yaml", config / "perfis.yaml")
    footage = tmp_path / "FOOTAGE"
    (config / "projetos.yaml").write_text(yaml.safe_dump({"projetos": {
        "cliente_x": {"nome": "Cliente X", "pasta": str(footage / "cliente_x")},
    }}), encoding="utf-8")
    return config, tmp_path / "data", footage


def test_dry_run_nao_chama_o_downloader_nem_cria_pasta(ambiente_real, info_dict_real,
                                                       downloader_falso):
    """O critério do PLAN: --dry-run não baixa. Verificado pelo dublê, que
    registra toda chamada — e pela pasta do projeto, que continua não
    existindo (SPEC 13.1 decisão 6)."""
    config, dados, footage = ambiente_real
    dublê = downloader_falso(info=info_dict_real)
    pipeline = Pipeline(config, dados, downloader=dublê,
                        detectar_ffmpeg=_ffmpeg_presente)
    try:
        saida = Saida()
        codigo = main(["--dry-run", "--perfil", "edicao_1080",
                       "--projeto", "cliente_x", URL],
                      pipeline=pipeline, escrever=saida)
    finally:
        pipeline.encerrar()

    assert codigo == 0
    assert not any(c[0] == "baixar" for c in dublê.chamadas), \
        "--dry-run não pode baixar nada"
    assert not (footage / "cliente_x").exists(), \
        "--dry-run não pode criar a pasta do projeto"
    assert "DRY-RUN" in saida.texto
    assert "destino:" in saida.texto
    assert str(footage / "cliente_x") in saida.texto
    assert ".mp4" in saida.texto


def test_dry_run_mostra_o_mesmo_destino_que_o_download_usaria(ambiente_real,
                                                              info_dict_real):
    """O --dry-run existe para conferir o nome ANTES de gravar. Se ele
    mostrasse um caminho e o download gravasse em outro, não serviria."""
    config, dados, _ = ambiente_real
    pipeline = Pipeline(config, dados, downloader=DownloaderEco(info_dict_real),
                        detectar_ffmpeg=_ffmpeg_presente)
    try:
        previsto = pipeline.simular([URL], "edicao_1080", "cliente_x")[0]["destino"]
        job_id = pipeline.enfileirar([URL], "edicao_1080", "cliente_x")[0]
        real = _esperar_caminho(pipeline, job_id)
    finally:
        pipeline.encerrar()
    # Compara ARQUIVO, não string: o caminho que volta do download passa pelo
    # sistema e vem com barra invertida, enquanto o da configuração vem como
    # foi escrito no YAML. O contrato §5 já documenta essa diferença.
    assert Path(real) == Path(previsto)


def test_dry_run_com_link_invalido_avisa_que_a_fila_e_tudo_ou_nada(ambiente_real,
                                                                   info_dict_real,
                                                                   downloader_falso):
    config, dados, _ = ambiente_real
    pipeline = Pipeline(config, dados,
                        downloader=downloader_falso(info=info_dict_real),
                        detectar_ffmpeg=_ffmpeg_presente)
    try:
        saida = Saida()
        codigo = main(["--dry-run", "--perfil", "edicao_1080",
                       "--projeto", "cliente_x", URL, "isso não é link"],
                      pipeline=pipeline, escrever=saida)
    finally:
        pipeline.encerrar()

    assert codigo == 1
    assert "link_invalido" in saida.texto
    assert "tudo ou nada" in saida.texto


def test_dry_run_com_perfil_inexistente_sai_com_1(ambiente_real, info_dict_real,
                                                  downloader_falso):
    config, dados, _ = ambiente_real
    pipeline = Pipeline(config, dados,
                        downloader=downloader_falso(info=info_dict_real),
                        detectar_ffmpeg=_ffmpeg_presente)
    try:
        saida = Saida()
        codigo = main(["--dry-run", "--perfil", "nao_existe",
                       "--projeto", "cliente_x", URL],
                      pipeline=pipeline, escrever=saida)
    finally:
        pipeline.encerrar()
    assert codigo == 1
    assert "não existe" in saida.texto


class DownloaderEco:
    """Devolve como caminho final o outtmpl que recebeu — como se o yt-dlp
    tivesse gravado onde mandamos. O DownloaderFalso do conftest devolve um
    caminho fixo, que não serve para comparar destino previsto e real."""

    def __init__(self, info):
        self.info = info
        self.chamadas = []

    def inspecionar(self, url):
        self.chamadas.append(("inspecionar", url))
        return self.info

    def baixar(self, url, opcoes, ao_progredir):
        self.chamadas.append(("baixar", url))
        destino = Path(opcoes["outtmpl"])
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(bytes(1024))
        return str(destino)


def _ffmpeg_presente():
    from src.download.ffmpeg import StatusFFmpeg
    return StatusFFmpeg(ffmpeg="C:/x/ffmpeg.exe", ffprobe="C:/x/ffprobe.exe")


def _esperar_caminho(pipeline, job_id, espera=5.0):
    import time
    prazo = time.monotonic() + espera
    while time.monotonic() < prazo:
        for j in pipeline.estado_fila():
            if j["id"] == job_id and j["estado"] in ("concluido", "falhou"):
                return j["caminho_final"]
        time.sleep(0.02)
    raise AssertionError("o job não terminou")
