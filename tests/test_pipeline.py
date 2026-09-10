"""Testes de T6 (rodada 1) — o pipeline, a orquestração que a CLI e a web usam.

Real: domínio, Fila, Worker, Historico (em tmp_path). Dublê: o downloader.
Nenhum teste toca a rede.

Referência: SPEC 4.4, 10.1, 11.
"""

import json
import shutil
import threading
import time
from pathlib import Path

import pytest
import yaml

from src.domain.models import ESTADOS_TERMINAIS, EstadoJob
from src.download.traducao_erros import Classificacao, ErroDeDownload
from src.domain.erros import MotivoFalha
from src.pipeline import Conflito, EntradaInvalida, NaoEncontrado, Pipeline
from src.storage.historico import Historico

RAIZ = Path(__file__).resolve().parent.parent
ESPERA = 5.0


# ===========================================================================
# Dublês e fixtures
# ===========================================================================


class DownloaderEco:
    """Devolve como caminho final exatamente o outtmpl que recebeu — como se
    o yt-dlp tivesse gravado onde mandamos. Cria o arquivo para o tamanho
    existir."""

    def __init__(self, info, erro_inspecionar=None, erro_baixar=None, eventos=None):
        self.info = info
        self.erro_inspecionar = erro_inspecionar
        self.erro_baixar = erro_baixar
        self.eventos = eventos or []
        self.chamadas = []

    def inspecionar(self, url):
        self.chamadas.append(("inspecionar", url))
        if self.erro_inspecionar:
            raise self.erro_inspecionar
        return dict(self.info)

    def baixar(self, url, opcoes, ao_progredir):
        self.chamadas.append(("baixar", url, dict(opcoes)))
        for e in self.eventos:
            ao_progredir(e)
        if self.erro_baixar:
            raise self.erro_baixar
        destino = Path(opcoes["outtmpl"])
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"x" * 10)
        return str(destino)


def ffmpeg_presente():
    from src.download.ffmpeg import StatusFFmpeg

    return StatusFFmpeg(ffmpeg="C:/x/ffmpeg.EXE", ffprobe="C:/x/ffprobe.EXE")


def ffmpeg_ausente():
    from src.download.ffmpeg import StatusFFmpeg

    return StatusFFmpeg(ffmpeg=None, ffprobe=None)


@pytest.fixture
def ambiente(tmp_path):
    """config/ com os perfis REAIS e projetos apontando para tmp_path."""
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(RAIZ / "config" / "perfis.yaml", config / "perfis.yaml")
    footage = tmp_path / "footage"
    (config / "projetos.yaml").write_text(
        yaml.safe_dump(
            {
                "projetos": {
                    "pessoal": {"nome": "Canal pessoal", "pasta": str(footage / "pessoal")},
                    "cliente_x": {"nome": "Cliente X", "pasta": str(footage / "cliente_x")},
                }
            }
        ),
        encoding="utf-8",
    )
    return {"config": config, "data": tmp_path / "data", "footage": footage}


@pytest.fixture
def subir(ambiente, info_dict_real):
    """Fábrica de Pipeline com downloader dublê; encerra no teardown."""
    criados = []

    def _subir(downloader=None, detectar_ffmpeg=ffmpeg_presente):
        dl = downloader or DownloaderEco(info_dict_real)
        p = Pipeline(
            ambiente["config"], ambiente["data"], downloader=dl, detectar_ffmpeg=detectar_ffmpeg
        )
        criados.append(p)
        return p, dl

    yield _subir
    for p in criados:
        p.encerrar()


URL_REAL = "https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw"
CANONICO = "https://www.youtube.com/watch?v=LzS8kB6lIm0"


def esperar_terminal(pipeline, job_id, espera=ESPERA):
    prazo = time.monotonic() + espera
    while time.monotonic() < prazo:
        for j in pipeline.estado_fila():
            if j["id"] == job_id and j["estado"] in {e.value for e in ESTADOS_TERMINAIS}:
                return j
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} não terminou em {espera}s")


# ===========================================================================
# Subida
# ===========================================================================


def test_sobe_e_cria_o_banco(subir, ambiente):
    p, _ = subir()
    assert (ambiente["data"] / "historico.db").exists()


def test_reconcilia_interrompidos_na_subida(ambiente, info_dict_real):
    """SPEC 10.1: o que estava `baixando` quando o programa fechou vira
    `interrompido` na subida seguinte — nunca concluído."""
    from src.domain.models import Video

    h = Historico(ambiente["data"] / "historico.db")
    h.criar_schema()
    h.iniciar(
        Video.de_info_dict(info_dict_real),
        perfil="edicao_1080",
        projeto="pessoal",
        url_original=URL_REAL,
    )
    h.fechar()

    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
    )
    try:
        linhas = p.historico()
        assert linhas[0]["status"] == "interrompido"
    finally:
        p.encerrar()


def test_config_lista_perfis_projetos_e_ffmpeg(subir):
    p, _ = subir()
    c = p.config()
    assert {x["nome"] for x in c["perfis"]} == {
        "edicao_1080",
        "edicao_4k",
        "so_audio",
        "preview_leve",
    }
    assert all(x["disponivel"] for x in c["perfis"])
    assert {x["nome"] for x in c["projetos"]} == {"pessoal", "cliente_x"}
    assert all(x["valido"] for x in c["projetos"])
    assert c["ffmpeg"]["disponivel"] is True and c["ffmpeg"]["completo"] is True


def test_config_sem_ffmpeg_marca_perfis_indisponiveis(subir):
    p, _ = subir(detectar_ffmpeg=ffmpeg_ausente)
    c = p.config()
    assert c["ffmpeg"]["disponivel"] is False
    assert not any(x["disponivel"] for x in c["perfis"] if x["exige_ffmpeg"])


def test_config_e_serializavel_em_json(subir):
    p, _ = subir()
    json.dumps(p.config())


# ===========================================================================
# inspecionar — metadados sem baixar, resultado parcial
# ===========================================================================


def test_inspecionar_link_real(subir):
    p, dl = subir()
    itens = p.inspecionar(URL_REAL)
    assert len(itens) == 1
    item = itens[0]
    assert item["ok"] is True
    assert item["url"] == CANONICO
    assert item["aviso"] is None
    v = item["video"]
    assert v["titulo"].startswith("Camisa azul da Seleção")
    assert v["canal"] == "Canal Michuruca"
    assert v["duracao_s"] == 65
    assert v["thumbnail"].startswith("https://i.ytimg.com/")
    assert len(v["formatos"]) == 41, "41 = 45 do fixture menos 4 storyboards"
    assert dl.chamadas == [("inspecionar", CANONICO)], "inspeciona pelo canônico"


def test_inspecionar_expoe_qualidades_pela_menor_dimensao(subir):
    """Coerente com SPEC 6.3: o teto é na menor dimensão, e é isso que a UI
    mostra como 'qualidades disponíveis' — igual para vertical e horizontal."""
    p, _ = subir()
    v = p.inspecionar(URL_REAL)[0]["video"]
    assert v["qualidades"] == [144, 240, 360, 480, 608, 720, 1080]


def test_inspecionar_formatos_trazem_os_tres_estados_de_audio(subir):
    p, _ = subir()
    formatos = {f["format_id"]: f for f in p.inspecionar(URL_REAL)[0]["video"]["formatos"]}
    assert formatos["233"]["acodec"] is None and formatos["233"]["tem_audio"] is True
    assert formatos["137"]["tem_video"] is True and formatos["137"]["tem_audio"] is False
    assert formatos["140"]["tem_video"] is False and formatos["140"]["tem_audio"] is True


def test_inspecionar_e_serializavel_em_json(subir):
    p, _ = subir()
    json.dumps(p.inspecionar(URL_REAL))


def test_inspecionar_resultado_parcial(subir):
    """Um link ruim numa lista de três não invalida os outros (SPEC 11.1)."""
    p, _ = subir()
    itens = p.inspecionar(f"{URL_REAL}\nnão é url\nhttps://vimeo.com/123456789")
    assert [i["ok"] for i in itens] == [True, False, True]
    assert itens[1]["erro"]
    assert itens[1]["original"] == "não é url"


def test_inspecionar_outro_site_traz_o_aviso(subir):
    p, _ = subir()
    item = p.inspecionar("https://vimeo.com/123456789")[0]
    assert item["ok"] is True
    assert item["aviso"] is not None
    assert item["e_youtube"] is False


def test_inspecionar_erro_do_site_vira_item_com_motivo(subir, info_dict_real):
    erro = ErroDeDownload(Classificacao(MotivoFalha.PRIVADO, "Private video"))
    p, _ = subir(DownloaderEco(info_dict_real, erro_inspecionar=erro))
    item = p.inspecionar(URL_REAL)[0]
    assert item["ok"] is False
    assert item["motivo"] == "privado"
    assert "privado" in item["erro"].lower()


def test_inspecionar_deduplica_e_reaproveita_o_cache(subir):
    p, dl = subir()
    p.inspecionar(f"{URL_REAL}\nhttps://youtu.be/LzS8kB6lIm0")
    p.inspecionar(CANONICO)
    assert dl.chamadas.count(("inspecionar", CANONICO)) == 1, "cache por URL canônica"


def test_inspecionar_informa_o_que_ja_foi_baixado_por_perfil(subir):
    p, _ = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    esperar_terminal(p, ids[0])
    item = p.inspecionar(URL_REAL)[0]
    assert "edicao_1080" in item["baixados"]
    assert item["baixados"]["edicao_1080"]["caminho"].endswith(".mp4")
    assert "so_audio" not in item["baixados"]


def test_inspecionar_texto_vazio(subir):
    p, _ = subir()
    assert p.inspecionar("") == []


# ===========================================================================
# enfileirar — validação e o ciclo completo
# ===========================================================================


def test_enfileirar_e_baixar_ate_o_fim(subir, ambiente):
    p, dl = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    assert len(ids) == 1
    j = esperar_terminal(p, ids[0])
    assert j["estado"] == "concluido", j
    caminho = Path(j["caminho_final"])
    assert caminho.exists()
    assert caminho.parent == ambiente["footage"] / "pessoal"
    assert caminho.name == (
        "20260901 - Camisa azul da Seleção críticas ao design "
        "e lembrança histórica [LzS8kB6lIm0].mp4"
    )


def test_enfileirar_resolve_o_seletor_pela_orientacao(subir):
    """O fixture é um Short vertical: o teto vai em width (SPEC 6.3)."""
    p, dl = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    esperar_terminal(p, ids[0])
    opcoes = next(c[2] for c in dl.chamadas if c[0] == "baixar")
    assert "[width<=1080]" in opcoes["format"]
    assert opcoes["merge_output_format"] == "mp4"
    assert opcoes["noplaylist"] is True


def test_enfileirar_grava_no_historico(subir):
    p, _ = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    esperar_terminal(p, ids[0])
    h = p.historico()
    assert len(h) == 1
    assert h[0]["status"] == "concluido"
    assert h[0]["projeto"] == "pessoal"
    assert h[0]["tamanho_bytes"] == 10


def test_enfileirar_perfil_so_audio_usa_a_extensao_do_perfil(subir):
    p, _ = subir()
    ids = p.enfileirar([URL_REAL], perfil="so_audio", projeto="pessoal")
    j = esperar_terminal(p, ids[0])
    assert j["caminho_final"].endswith(".m4a")


def test_enfileirar_varios_na_ordem(subir, info_dict_real):
    p, _ = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    ids += p.enfileirar([URL_REAL], perfil="so_audio", projeto="pessoal")
    for i in ids:
        esperar_terminal(p, i)
    assert [j["id"] for j in p.estado_fila()] == ids


@pytest.mark.parametrize(
    "perfil,projeto",
    [
        ("nao_existe", "pessoal"),
        ("edicao_1080", "nao_existe"),
    ],
)
def test_enfileirar_perfil_ou_projeto_inexistente(subir, perfil, projeto):
    p, _ = subir()
    with pytest.raises(EntradaInvalida):
        p.enfileirar([URL_REAL], perfil=perfil, projeto=projeto)


def test_enfileirar_perfil_indisponivel_sem_ffmpeg(subir):
    p, _ = subir(detectar_ffmpeg=ffmpeg_ausente)
    with pytest.raises(EntradaInvalida) as exc:
        p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    assert "ffmpeg" in str(exc.value).lower()


def test_enfileirar_link_invalido(subir):
    p, _ = subir()
    with pytest.raises(EntradaInvalida):
        p.enfileirar(["não é url"], perfil="edicao_1080", projeto="pessoal")


def test_enfileirar_lista_vazia(subir):
    p, _ = subir()
    with pytest.raises(EntradaInvalida):
        p.enfileirar([], perfil="edicao_1080", projeto="pessoal")


def test_enfileirar_ja_baixado_e_conflito_salvo_se_forcar(subir):
    """SPEC 1: avisa se já baixei. Conservador: recusa em vez de rebaixar em
    silêncio; `forcar` existe para quando o usuário quer mesmo."""
    p, _ = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    esperar_terminal(p, ids[0])
    with pytest.raises(Conflito) as exc:
        p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    assert ".mp4" in str(exc.value)
    ids2 = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal", forcar=True)
    j = esperar_terminal(p, ids2[0])
    assert j["estado"] == "concluido"
    assert j["caminho_final"].endswith(" (2).mp4"), "colisão resolvida, nunca sobrescreve"


def test_enfileirar_mesmo_video_e_perfil_ja_na_fila_e_conflito(subir, info_dict_real):
    class Lento(DownloaderEco):
        def baixar(self, url, opcoes, ao_progredir):
            time.sleep(0.3)
            return super().baixar(url, opcoes, ao_progredir)

    p, _ = subir(Lento(info_dict_real))
    p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    with pytest.raises(Conflito):
        p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")


def test_enfileirar_mesmo_video_em_perfil_diferente_nao_e_conflito(subir):
    p, _ = subir()
    p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    p.enfileirar([URL_REAL], perfil="so_audio", projeto="pessoal")


def test_enfileirar_sem_inspecionar_antes_funciona(subir):
    """O cache é otimização, não pré-requisito."""
    p, dl = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    assert esperar_terminal(p, ids[0])["estado"] == "concluido"


def test_falha_do_site_vira_job_falhou_com_motivo(subir, info_dict_real):
    erro = ErroDeDownload(Classificacao(MotivoFalha.BLOQUEIO_REGIONAL, "geo", {"paises": ["US"]}))
    p, _ = subir(DownloaderEco(info_dict_real, erro_baixar=erro))
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    j = esperar_terminal(p, ids[0])
    assert j["estado"] == "falhou"
    assert j["motivo_falha"] == "bloqueio_regional"
    assert p.historico()[0]["status"] == "falhou"


def test_pasta_profunda_gera_aviso_no_job(ambiente, info_dict_real):
    pasta = ambiente["footage"] / ("p" * 80)
    (ambiente["config"] / "projetos.yaml").write_text(
        yaml.safe_dump({"projetos": {"fundo": {"nome": "Fundo", "pasta": str(pasta)}}}),
        encoding="utf-8",
    )
    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
    )
    try:
        ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="fundo")
        j = esperar_terminal(p, ids[0])
        assert j["estado"] == "concluido"
        assert j["aviso"] and "profund" in j["aviso"].lower()
    finally:
        p.encerrar()


# ===========================================================================
# estado_fila / cancelar / historico
# ===========================================================================


def test_estado_fila_e_serializavel_e_traz_progresso(subir, info_dict_real):
    eventos = [
        {
            "status": "downloading",
            "downloaded_bytes": 5,
            "total_bytes": 10,
            "info_dict": {"format_id": "137"},
        }
    ]
    p, _ = subir(DownloaderEco(info_dict_real, eventos=eventos))
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    j = esperar_terminal(p, ids[0])
    json.dumps(p.estado_fila())
    assert j["progresso"]["percentual"] == 50.0
    assert j["video"]["titulo"].startswith("Camisa")
    assert j["perfil"] == "edicao_1080" and j["projeto"] == "pessoal"


def test_cancelar_na_fila(subir, info_dict_real):
    class Lento(DownloaderEco):
        def baixar(self, url, opcoes, ao_progredir):
            time.sleep(0.3)
            return super().baixar(url, opcoes, ao_progredir)

    p, _ = subir(Lento(info_dict_real))
    a = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")[0]
    b = p.enfileirar([URL_REAL], perfil="so_audio", projeto="pessoal")[0]
    assert p.cancelar(b) is True
    assert next(j for j in p.estado_fila() if j["id"] == b)["estado"] == "cancelado"
    esperar_terminal(p, a)


def test_cancelar_inexistente(subir):
    p, _ = subir()
    with pytest.raises(NaoEncontrado):
        p.cancelar("nao")


def test_cancelar_em_andamento_e_conflito(subir, info_dict_real):
    class Lento(DownloaderEco):
        def baixar(self, url, opcoes, ao_progredir):
            time.sleep(0.4)
            return super().baixar(url, opcoes, ao_progredir)

    p, _ = subir(Lento(info_dict_real))
    a = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")[0]
    prazo = time.monotonic() + ESPERA
    while time.monotonic() < prazo and p.estado_fila()[0]["estado"] != "baixando":
        time.sleep(0.01)
    with pytest.raises(Conflito):
        p.cancelar(a)
    esperar_terminal(p, a)


def test_historico_busca_e_filtra(subir):
    p, _ = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    esperar_terminal(p, ids[0])
    assert len(p.historico(termo="selecao")) == 1
    assert p.historico(termo="inexistente") == []
    assert p.historico(projeto="cliente_x") == []
    json.dumps(p.historico())


def test_encerrar_e_idempotente(subir):
    p, _ = subir()
    p.encerrar()
    p.encerrar()


# ===========================================================================
# ETAPA 2 — decisoes aplicadas depois do smoke test
# ===========================================================================


def test_decisao6_nao_cria_a_pasta_do_projeto_na_subida(ambiente, info_dict_real):
    """Decisao 6: nada de D:/FOOTAGE/cliente_exemplo aparecer por causa de um
    YAML de exemplo. A pasta so nasce quando um download precisa dela."""
    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
    )
    try:
        assert not (ambiente["footage"] / "pessoal").exists()
        assert not (ambiente["footage"] / "cliente_x").exists()
        # ...mas o projeto continua valido: a pasta e criavel.
        assert all(x["valido"] for x in p.config()["projetos"])
    finally:
        p.encerrar()


def test_decisao6_a_pasta_nasce_ao_baixar(subir, ambiente):
    p, _ = subir()
    assert not (ambiente["footage"] / "pessoal").exists()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    esperar_terminal(p, ids[0])
    assert (ambiente["footage"] / "pessoal").is_dir()
    assert not (ambiente["footage"] / "cliente_x").exists(), "so a pasta usada"


def test_decisao6_pasta_sem_ancestral_gravavel_e_invalida(ambiente, info_dict_real, tmp_path):
    """Um destino impossivel tem que aparecer como projeto invalido, com
    motivo, e nao estourar so na hora de baixar."""
    import yaml as _yaml

    (ambiente["config"] / "projetos.yaml").write_text(
        _yaml.safe_dump(
            {
                "projetos": {
                    "impossivel": {"nome": "X", "pasta": "Z:/nao/existe/esse/disco"},
                }
            }
        ),
        encoding="utf-8",
    )
    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
    )
    try:
        projeto = p.config()["projetos"][0]
        assert projeto["valido"] is False
        assert projeto["motivo"]
        with pytest.raises(EntradaInvalida):
            p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="impossivel")
    finally:
        p.encerrar()


def test_decisao3_redownload_preserva_o_registro_anterior(subir, ambiente):
    """O bug que o smoke test flagrou: 3 arquivos no disco, 2 registros.

    Cada arquivo baixado tem que ter a sua linha no historico.
    """
    p, _ = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    esperar_terminal(p, ids[0])
    ids2 = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal", forcar=True)
    esperar_terminal(p, ids2[0])

    arquivos = sorted(x.name for x in (ambiente["footage"] / "pessoal").iterdir())
    registros = [r for r in p.historico() if r["status"] == "concluido"]
    caminhos = sorted(Path(r["caminho"]).name for r in registros)

    assert len(arquivos) == 2, arquivos
    assert caminhos == arquivos, "arquivo no disco sem linha no historico"


def test_decisao3_falha_no_redownload_nao_apaga_o_arquivo_anterior(subir, ambiente, info_dict_real):
    p, dl = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    j = esperar_terminal(p, ids[0])
    caminho_bom = j["caminho_final"]

    dl.erro_baixar = ErroDeDownload(Classificacao(MotivoFalha.REDE, "timeout"))
    ids2 = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal", forcar=True)
    assert esperar_terminal(p, ids2[0])["estado"] == "falhou"

    assert Path(caminho_bom).exists()
    concluidos = [r for r in p.historico() if r["status"] == "concluido"]
    assert [r["caminho"] for r in concluidos] == [caminho_bom]
    # e o aviso de duplicata volta a apontar para o arquivo que existe
    item = p.inspecionar(URL_REAL)[0]
    assert item["baixados"]["edicao_1080"]["caminho"] == caminho_bom


def test_decisao1_arquivo_ja_existente_e_concluido_com_aviso(subir, ambiente):
    """Quando o destino ja existe (corrida rara), o job conclui com aviso e
    ja_existia, sem baixar de novo."""
    p, dl = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    j = esperar_terminal(p, ids[0])
    original = Path(j["caminho_final"])

    # Recria o cenario: o proximo destino resolvido sera " (2)"; criamos ele
    # antes para o worker encontrar o arquivo ja no lugar.
    segundo = original.with_name(original.stem + " (2)" + original.suffix)
    segundo.write_bytes(b"y" * 2048)

    ids2 = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal", forcar=True)
    j2 = esperar_terminal(p, ids2[0])
    assert j2["estado"] == "concluido"
    if j2["caminho_final"] == str(segundo):
        assert j2["ja_existia"] is True
        assert j2["aviso"]
        assert segundo.read_bytes() == b"y" * 2048, "o arquivo existente foi sobrescrito"


def test_decisao5_interrompido_com_arquivo_no_destino_avisa(ambiente, info_dict_real):
    """Decisao 5: na subida, se o historico diz interrompido e ha um arquivo
    no destino, avisa em vez de duplicar em silencio.

    Nao da para verificar integridade: o tamanho esperado nunca foi gravado.
    Entao o produto avisa e deixa a decisao com o usuario.
    """
    from src.domain.models import Video
    from src.storage.historico import Historico

    pasta = ambiente["footage"] / "pessoal"
    pasta.mkdir(parents=True)
    arquivo = pasta / "interrompido.mp4"
    arquivo.write_bytes(b"z" * 512)

    h = Historico(ambiente["data"] / "historico.db")
    h.criar_schema()
    r = h.iniciar(
        Video.de_info_dict(info_dict_real),
        perfil="edicao_1080",
        projeto="pessoal",
        url_original=URL_REAL,
    )
    h.registrar_destino(r.id, str(arquivo))
    h.fechar()

    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
    )
    try:
        reg = p.historico()[0]
        assert reg["status"] == "interrompido"
        assert reg["aviso"], "sem aviso, o arquivo parcial passa despercebido"
        assert "interromp" in reg["aviso"].lower()
        assert arquivo.exists(), "o arquivo nao pode ser apagado"
    finally:
        p.encerrar()


def test_decisao5_interrompido_sem_arquivo_nao_avisa(ambiente, info_dict_real):
    from src.domain.models import Video
    from src.storage.historico import Historico

    h = Historico(ambiente["data"] / "historico.db")
    h.criar_schema()
    r = h.iniciar(
        Video.de_info_dict(info_dict_real),
        perfil="edicao_1080",
        projeto="pessoal",
        url_original=URL_REAL,
    )
    h.registrar_destino(r.id, str(ambiente["footage"] / "pessoal" / "nao_existe.mp4"))
    h.fechar()

    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
    )
    try:
        reg = p.historico()[0]
        assert reg["status"] == "interrompido"
        assert not reg["aviso"]
    finally:
        p.encerrar()


def test_historico_expoe_aviso_e_ja_existia(subir):
    """Campos novos precisam chegar a API (e ao CONTRATO-API.md)."""
    p, _ = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    esperar_terminal(p, ids[0])
    reg = p.historico()[0]
    assert "aviso" in reg and "ja_existia" in reg
    assert reg["ja_existia"] is False


def test_estado_fila_expoe_ja_existia(subir):
    p, _ = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    j = esperar_terminal(p, ids[0])
    assert j["ja_existia"] is False
    assert "aviso" in j


# ===========================================================================
# Cookies do navegador
# ===========================================================================


def test_cookies_desligados_por_padrao(subir):
    p, _ = subir()
    c = p.cookies()
    assert c["ativo"] is False and c["navegador"] is None
    assert "firefox" in c["navegadores"], "a lista vem do yt-dlp instalado"


def test_config_expoe_os_cookies(subir):
    p, _ = subir()
    assert p.config()["cookies"]["ativo"] is False


def cookies_legiveis(navegador, perfil=None):
    """Dublê da leitura de cookies: nenhum teste abre o navegador do autor,
    e a lista de navegadores instalados varia de máquina para máquina."""
    return None


def test_ligar_cookies_grava_no_yaml_e_no_downloader(ambiente, info_dict_real):
    from src.download.adapter import Downloader

    dl = Downloader(fabrica_ydl=lambda o: None)
    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=dl,
        detectar_ffmpeg=ffmpeg_presente,
        testar_cookies_de=cookies_legiveis,
    )
    try:
        resultado = p.definir_cookies("firefox", "default")
        assert resultado["ativo"] is True
        assert dl.cookies == ("firefox", "default")
        texto = (ambiente["config"] / "cookies.yaml").read_text(encoding="utf-8")
        assert "firefox" in texto
    finally:
        p.encerrar()


def test_desligar_cookies(ambiente, info_dict_real):
    from src.download.adapter import Downloader

    dl = Downloader(fabrica_ydl=lambda o: None)
    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=dl,
        detectar_ffmpeg=ffmpeg_presente,
        testar_cookies_de=cookies_legiveis,
    )
    try:
        p.definir_cookies("firefox")
        assert p.definir_cookies(None)["ativo"] is False
        assert dl.cookies is None
    finally:
        p.encerrar()


def test_navegador_ilegivel_e_recusado_com_a_causa(ambiente, info_dict_real):
    """O yt-dlp embrulha toda falha de cookie em "failed to load cookies" e
    joga a causa fora. Testar a leitura na hora de ESCOLHER é o que preserva a
    causa — e evita ligar uma opção que só falharia no meio do download."""

    def ilegivel(navegador, perfil=None):
        return "Failed to decrypt with DPAPI. See  https://...  for more info"

    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
        testar_cookies_de=ilegivel,
    )
    try:
        with pytest.raises(EntradaInvalida) as erro:
            p.definir_cookies("edge")
        assert "DPAPI" in str(erro.value), "a causa real tem que chegar ao usuário"
        assert "Feche o navegador" in str(erro.value)
        assert p.cookies()["ativo"] is False, "não pode ter ligado"
        assert not (ambiente["config"] / "cookies.yaml").exists(), "não pode ter gravado"
    finally:
        p.encerrar()


def test_desligar_nao_testa_leitura(ambiente, info_dict_real):
    """Desligar tem que funcionar mesmo com o navegador quebrado — é
    justamente a saída de quem ligou e se arrependeu."""

    def sempre_falha(navegador, perfil=None):
        return "qualquer coisa"

    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
        testar_cookies_de=sempre_falha,
    )
    try:
        assert p.definir_cookies(None)["ativo"] is False
    finally:
        p.encerrar()


def test_navegador_desconhecido_e_recusado_na_hora(subir):
    """Validar contra a lista do yt-dlp aqui faz a falha aparecer ao
    escolher, e não no meio de um download."""
    p, _ = subir()
    with pytest.raises(EntradaInvalida) as erro:
        p.definir_cookies("internet_explorer")
    assert "não é suportado" in str(erro.value)


def test_navegador_invalido_no_yaml_nao_derruba_a_subida(ambiente, info_dict_real):
    """Cookies são acessório: um valor errado no arquivo desliga a opção e
    explica na tela, em vez de impedir a aplicação de subir."""
    (ambiente["config"] / "cookies.yaml").write_text('navegador: "netscape"\n', encoding="utf-8")
    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
    )
    try:
        c = p.cookies()
        assert c["ativo"] is False
        assert "netscape" in c["motivo"]
    finally:
        p.encerrar()


def test_yaml_de_cookies_e_lido_na_subida(ambiente, info_dict_real):
    (ambiente["config"] / "cookies.yaml").write_text(
        'navegador: "firefox"\nperfil: "default"\n', encoding="utf-8"
    )
    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
    )
    try:
        assert p.cookies()["navegador"] == "firefox"
        assert p.cookies()["perfil"] == "default"
    finally:
        p.encerrar()


def test_ligar_cookies_limpa_o_cache_de_inspecao(ambiente, info_dict_real):
    """O cache guarda metadados obtidos SEM cookies. Sem limpar, ligar a
    opção não mudaria nada para um link já inspecionado."""
    dl = DownloaderEco(info_dict_real)
    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=dl,
        detectar_ffmpeg=ffmpeg_presente,
        testar_cookies_de=cookies_legiveis,
    )
    p.inspecionar(URL_REAL)
    antes = sum(1 for c in dl.chamadas if c[0] == "inspecionar")

    try:
        p.definir_cookies("firefox")
        p.inspecionar(URL_REAL)
        depois = sum(1 for c in dl.chamadas if c[0] == "inspecionar")
    finally:
        p.encerrar()
    assert depois > antes, "a segunda inspeção tem que ir ao site de novo"


# ===========================================================================
# Projetos gerenciados pela tela
# ===========================================================================


class DownloaderQueTrava:
    """Segura o download até liberarem, para haver job ATIVO na fila."""

    def __init__(self, info):
        self.info = info
        self.entrou = threading.Event()
        self.liberar = threading.Event()

    def inspecionar(self, url):
        return self.info

    def baixar(self, url, opcoes, ao_progredir):
        self.entrou.set()
        self.liberar.wait(10)
        destino = Path(opcoes["outtmpl"])
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(bytes(16))
        return str(destino)


@pytest.fixture
def com_projetos(ambiente, info_dict_real, tmp_path):
    """Pipeline real, mais uma pasta que EXISTE para cadastrar."""
    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
    )
    nova = tmp_path / "pasta_nova"
    nova.mkdir()
    yield p, nova, ambiente["config"] / "projetos.yaml"
    p.encerrar()


def test_adicionar_projeto_entra_na_config_e_no_yaml(com_projetos):
    p, nova, arquivo = com_projetos
    criado = p.adicionar_projeto("cliente_novo", str(nova), "Cliente Novo")

    assert criado["nome"] == "cliente_novo" and criado["valido"] is True
    assert "cliente_novo" in {x["nome"] for x in p.projetos()}
    assert "cliente_novo" in {x["nome"] for x in p.config()["projetos"]}
    assert "cliente_novo" in arquivo.read_text(encoding="utf-8")


def test_adicionar_projeto_ja_serve_de_destino(com_projetos):
    """Cadastrar e não poder usar no mesmo instante seria meio caminho."""
    p, nova, _ = com_projetos
    p.adicionar_projeto("cliente_novo", str(nova))
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="cliente_novo")
    job = esperar_terminal(p, ids[0])
    assert job["estado"] == "concluido"
    assert Path(job["caminho_final"]).parent == nova


def test_adicionar_projeto_recusa_pasta_inexistente(com_projetos, tmp_path):
    p, _, _ = com_projetos
    with pytest.raises(EntradaInvalida) as erro:
        p.adicionar_projeto("x", str(tmp_path / "nao_existe"))
    assert "não existe" in str(erro.value)


def test_adicionar_projeto_recusa_arquivo_no_lugar_de_pasta(com_projetos, tmp_path):
    p, _, _ = com_projetos
    arquivo = tmp_path / "um_arquivo.txt"
    arquivo.write_text("nao sou pasta", encoding="utf-8")
    with pytest.raises(EntradaInvalida) as erro:
        p.adicionar_projeto("x", str(arquivo))
    assert "não é uma pasta" in str(erro.value)


def test_adicionar_projeto_testa_escrita_de_verdade(com_projetos, monkeypatch):
    """os.access mente no Windows: ele ignora ACL e diz que dá para escrever
    onde não dá. Por isso a checagem grava um arquivo de teste."""
    p, nova, _ = com_projetos
    original = Path.write_bytes

    def recusar(self, dados):
        if self.name.startswith(".baixador-escrita-"):
            raise PermissionError(13, "Acesso negado")
        return original(self, dados)

    monkeypatch.setattr(Path, "write_bytes", recusar)
    with pytest.raises(EntradaInvalida) as erro:
        p.adicionar_projeto("x", str(nova))
    assert "não aceita escrita" in str(erro.value)


def test_adicionar_projeto_nao_deixa_o_arquivo_de_teste_para_tras(com_projetos):
    p, nova, _ = com_projetos
    p.adicionar_projeto("cliente_novo", str(nova))
    assert list(nova.iterdir()) == [], "o teste de escrita tem que se limpar"


def test_adicionar_projeto_recusa_nome_repetido_ignorando_caixa(com_projetos):
    p, nova, _ = com_projetos
    with pytest.raises(Conflito):
        p.adicionar_projeto("PESSOAL", str(nova))


def test_adicionar_projeto_recusa_o_nome_reservado(com_projetos):
    """'avulso' é o nome do destino digitado na hora; deixar cadastrar um
    projeto assim confundiria as duas coisas no histórico."""
    p, nova, _ = com_projetos
    with pytest.raises(EntradaInvalida) as erro:
        p.adicionar_projeto("avulso", str(nova))
    assert "reservado" in str(erro.value)


@pytest.mark.parametrize(
    "nome", ["com espaço", "acentuação", "", "-comeca-com-traco", "barra/no/meio", "x" * 41]
)
def test_adicionar_projeto_recusa_nome_malformado(com_projetos, nome):
    p, nova, _ = com_projetos
    with pytest.raises(EntradaInvalida):
        p.adicionar_projeto(nome, str(nova))


def test_remover_projeto_some_da_config_e_do_yaml(com_projetos):
    p, _, arquivo = com_projetos
    p.remover_projeto("pessoal")
    assert "pessoal" not in {x["nome"] for x in p.projetos()}
    assert "pessoal:" not in arquivo.read_text(encoding="utf-8")


def test_remover_projeto_inexistente(com_projetos):
    p, _, _ = com_projetos
    with pytest.raises(NaoEncontrado):
        p.remover_projeto("nao_existe")


def test_remover_projeto_com_download_ativo_e_recusado(ambiente, info_dict_real):
    """Remover o destino de um download em andamento deixaria o worker sem
    para onde gravar, no meio da gravação."""
    dl = DownloaderQueTrava(info_dict_real)
    p = Pipeline(
        ambiente["config"], ambiente["data"], downloader=dl, detectar_ffmpeg=ffmpeg_presente
    )
    try:
        p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
        assert dl.entrou.wait(5), "o download não começou"
        with pytest.raises(Conflito) as erro:
            p.remover_projeto("pessoal")
        assert "andamento" in str(erro.value)
    finally:
        dl.liberar.set()
        p.encerrar()


def test_remover_o_ultimo_projeto_e_recusado(com_projetos):
    """Sem nenhum projeto o carregar_projetos levanta, e a aplicação não sobe
    na próxima vez."""
    p, _, _ = com_projetos
    p.remover_projeto("pessoal")
    with pytest.raises(EntradaInvalida) as erro:
        p.remover_projeto("cliente_x")
    assert "único projeto" in str(erro.value)
    assert {x["nome"] for x in p.projetos()} == {"cliente_x"}


def test_escolher_pasta_e_injetado(ambiente, info_dict_real):
    """O seletor nativo nunca roda em teste: o dublê prova a ligação."""
    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
        escolher_pasta=lambda: "D:/FOOTAGE/escolhida",
    )
    try:
        assert p.escolher_pasta() == "D:/FOOTAGE/escolhida"
    finally:
        p.encerrar()


# ===========================================================================
# Destino avulso — pasta digitada na hora, sem cadastrar projeto
# ===========================================================================


def test_pasta_avulsa_baixa_para_o_caminho_digitado(com_projetos):
    p, nova, arquivo = com_projetos
    antes = arquivo.read_text(encoding="utf-8")

    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", pasta=str(nova))
    job = esperar_terminal(p, ids[0])

    assert job["estado"] == "concluido"
    assert Path(job["caminho_final"]).parent == nova
    assert job["projeto"] == "avulso"
    assert arquivo.read_text(encoding="utf-8") == antes, (
        "destino avulso não pode gravar nada no projetos.yaml"
    )


def test_pasta_avulsa_entra_no_historico_com_o_caminho_exato(com_projetos):
    p, nova, _ = com_projetos
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", pasta=str(nova))
    esperar_terminal(p, ids[0])
    registro = p.historico()[0]
    assert registro["projeto"] == "avulso"
    assert Path(registro["caminho"]).parent == nova


def test_pasta_avulsa_recusa_caminho_que_nao_existe(com_projetos, tmp_path):
    p, _, _ = com_projetos
    with pytest.raises(EntradaInvalida) as erro:
        p.enfileirar([URL_REAL], perfil="edicao_1080", pasta=str(tmp_path / "nao_existe"))
    assert "avulsa" in str(erro.value) and "não existe" in str(erro.value)


def test_projeto_e_pasta_juntos_e_recusado(com_projetos):
    p, nova, _ = com_projetos
    with pytest.raises(EntradaInvalida) as erro:
        p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal", pasta=str(nova))
    assert "não os dois" in str(erro.value)


def test_sem_projeto_e_sem_pasta_e_recusado(com_projetos):
    p, _, _ = com_projetos
    with pytest.raises(EntradaInvalida) as erro:
        p.enfileirar([URL_REAL], perfil="edicao_1080")
    assert "pasta avulsa" in str(erro.value)


def test_simular_aceita_pasta_avulsa(com_projetos):
    p, nova, _ = com_projetos
    item = p.simular([URL_REAL], perfil="edicao_1080", pasta=str(nova))[0]
    assert Path(item["destino"]).parent == nova


# ===========================================================================
# abrir_pasta — a checagem de contenção é a única coisa que separa este
# endpoint local de "abre qualquer coisa do disco"
# ===========================================================================


@pytest.fixture
def com_abridor(ambiente, info_dict_real):
    """Pipeline cujo abridor é um espião: nenhuma janela é aberta."""
    abertas = []
    p = Pipeline(
        ambiente["config"],
        ambiente["data"],
        downloader=DownloaderEco(info_dict_real),
        detectar_ffmpeg=ffmpeg_presente,
        abrir_no_explorador=abertas.append,
    )
    yield p, abertas, ambiente["footage"]
    p.encerrar()


def test_abrir_pasta_aceita_arquivo_dentro_do_projeto(com_abridor):
    p, abertas, footage = com_abridor
    arquivo = footage / "pessoal" / "v.mp4"
    arquivo.parent.mkdir(parents=True)
    arquivo.write_bytes(bytes(10))

    aberta = p.abrir_pasta(str(arquivo))

    assert Path(aberta) == arquivo.parent, "abre a PASTA do arquivo"
    assert [Path(x) for x in abertas] == [arquivo.parent]


def test_abrir_pasta_aceita_a_propria_pasta_do_projeto(com_abridor):
    p, abertas, footage = com_abridor
    (footage / "pessoal").mkdir(parents=True)
    assert Path(p.abrir_pasta(str(footage / "pessoal"))) == footage / "pessoal"
    assert len(abertas) == 1


def test_abrir_pasta_recusa_caminho_fora_de_qualquer_projeto(com_abridor, tmp_path):
    p, abertas, _ = com_abridor
    fora = tmp_path / "fora"
    fora.mkdir()
    with pytest.raises(EntradaInvalida) as erro:
        p.abrir_pasta(str(fora))
    assert "projeto configurado" in str(erro.value)
    assert abertas == [], "nada pode ter sido aberto"


def test_abrir_pasta_recusa_travessia_com_pontos(com_abridor, tmp_path):
    """`..` é o jeito óbvio de sair do projeto, e o resolve() colapsa antes.

    O destino da travessia EXISTE de propósito: se não existisse, quem
    recusaria seria a checagem de existência, e este teste passaria sem provar
    nada sobre a contenção.
    """
    p, abertas, footage = com_abridor
    (footage / "pessoal").mkdir(parents=True)
    fora = tmp_path / "fora"
    fora.mkdir()
    travessia = footage / "pessoal" / ".." / ".." / fora.name
    assert travessia.resolve() == fora.resolve(), "a travessia tem que chegar lá"

    with pytest.raises(EntradaInvalida) as erro:
        p.abrir_pasta(str(travessia))
    assert "projeto configurado" in str(erro.value)
    assert abertas == []


def test_abrir_pasta_recusa_prefixo_parecido(com_abridor):
    """`.../pessoal_secreto` NÃO está dentro de `.../pessoal`: a comparação é
    por segmento de caminho, não por prefixo de string."""
    p, abertas, footage = com_abridor
    vizinho = Path(str(footage / "pessoal") + "_secreto")
    vizinho.mkdir(parents=True)
    with pytest.raises(EntradaInvalida):
        p.abrir_pasta(str(vizinho))
    assert abertas == []


def test_abrir_pasta_recusa_caminho_vazio(com_abridor):
    p, abertas, _ = com_abridor
    for vazio in ("", "   ", None):
        with pytest.raises(EntradaInvalida):
            p.abrir_pasta(vazio)
    assert abertas == []


def test_abrir_pasta_recusa_o_que_nao_existe_mais(com_abridor):
    """Dentro do projeto, mas apagado: abrir daria janela de erro do sistema.
    A mensagem é melhor."""
    p, abertas, footage = com_abridor
    with pytest.raises(EntradaInvalida) as erro:
        p.abrir_pasta(str(footage / "pessoal" / "sumiu.mp4"))
    assert "não existe mais" in str(erro.value)
    assert abertas == []


def test_abrir_pasta_ignora_caixa_no_windows(com_abridor):
    """O Windows não diferencia maiúscula de minúscula; a checagem também
    não pode, ou recusaria um caminho legítimo do próprio histórico."""
    p, abertas, footage = com_abridor
    (footage / "pessoal").mkdir(parents=True)
    p.abrir_pasta(str(footage / "pessoal").upper())
    assert len(abertas) == 1


# ===========================================================================
# simular — o que alimenta o --dry-run
# ===========================================================================


def test_simular_nao_baixa_e_nao_cria_pasta(subir, ambiente):
    p, dl = subir()
    itens = p.simular([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    assert len(itens) == 1 and itens[0]["ok"]
    assert itens[0]["destino"].endswith(".mp4")
    assert not any(c[0] == "baixar" for c in dl.chamadas)
    assert not (ambiente["footage"] / "pessoal").exists()


def test_simular_marca_link_invalido_sem_derrubar_os_outros(subir):
    p, _ = subir()
    itens = p.simular([URL_REAL, "isso não é link"], perfil="edicao_1080", projeto="pessoal")
    assert [i["ok"] for i in itens] == [True, False]
    assert itens[1]["motivo"] == "link_invalido"


def test_simular_recusa_perfil_inexistente(subir):
    p, _ = subir()
    with pytest.raises(EntradaInvalida):
        p.simular([URL_REAL], perfil="nao_existe", projeto="pessoal")


def test_simular_avisa_que_ja_foi_baixado(subir):
    p, _ = subir()
    esperar_terminal(p, p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")[0])
    item = p.simular([URL_REAL], perfil="edicao_1080", projeto="pessoal")[0]
    assert item["ja_baixado"] is not None
    assert item["ja_baixado"]["caminho"]


def test_estado_fila_expoe_a_url_colada(subir):
    """Sem a url no job, "tentar de novo" morre no primeiro F5: a tela só
    saberia refazer o download enquanto a aba que enfileirou continuasse
    aberta. É a url ORIGINAL, não a canônica — é o que o usuário colou."""
    p, _ = subir()
    ids = p.enfileirar([URL_REAL], perfil="edicao_1080", projeto="pessoal")
    j = next(x for x in p.estado_fila() if x["id"] == ids[0])
    assert j["url"] == URL_REAL
