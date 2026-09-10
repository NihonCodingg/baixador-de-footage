"""Testes de T2 — modelos e máquina de estados.

Escritos ANTES da implementação. Nenhum teste toca a rede: os metadados vêm do
spike_meta.json capturado pelo spike.

Referência: SPEC 5.5 e 10.2.
"""

from datetime import datetime

import pytest

from src.domain.erros import TransicaoIlegal
from src.domain.models import (
    ESTADOS_TERMINAIS,
    EstadoJob,
    Formato,
    Job,
    Progresso,
    Video,
    tem_audio,
    tem_video,
)


def job_em(estado: EstadoJob) -> Job:
    video = Video(
        video_id="LzS8kB6lIm0",
        extractor="Youtube",
        url_canonica="https://www.youtube.com/watch?v=LzS8kB6lIm0",
        titulo="t",
        canal=None,
        duracao_s=None,
        thumbnail_url=None,
        data_upload=None,
        formatos=(),
    )
    return Job(
        id="j1",
        video=video,
        perfil="edicao_1080",
        projeto="p",
        estado=estado,
        criado_em=datetime(2026, 9, 1),
    )


# ===========================================================================
# GRUPO C — Máquina de estados (SPEC 10.2)
# ===========================================================================


@pytest.mark.parametrize(
    "origem,destino",
    [
        (EstadoJob.NA_FILA, EstadoJob.BAIXANDO),
        (EstadoJob.NA_FILA, EstadoJob.CANCELADO),
        (EstadoJob.BAIXANDO, EstadoJob.CONCLUIDO),
        (EstadoJob.BAIXANDO, EstadoJob.FALHOU),
        (EstadoJob.BAIXANDO, EstadoJob.INTERROMPIDO),
    ],
)
def test_c1_transicoes_legais(origem, destino):
    j = job_em(origem)
    j.transicionar(destino)
    assert j.estado is destino


def test_c2_na_fila_para_concluido_direto_e_ilegal():
    with pytest.raises(TransicaoIlegal):
        job_em(EstadoJob.NA_FILA).transicionar(EstadoJob.CONCLUIDO)


@pytest.mark.parametrize("terminal", sorted(ESTADOS_TERMINAIS, key=lambda e: e.value))
def test_c3_nao_se_sai_de_estado_terminal(terminal):
    with pytest.raises(TransicaoIlegal):
        job_em(terminal).transicionar(EstadoJob.BAIXANDO)


def test_c4_baixando_nao_pode_ser_cancelado():
    """Só se cancela job que ainda não começou (SPEC 10.5).

    Interromper o yt-dlp no meio deixaria .part órfão e estado ambíguo.
    """
    with pytest.raises(TransicaoIlegal):
        job_em(EstadoJob.BAIXANDO).transicionar(EstadoJob.CANCELADO)


@pytest.mark.parametrize("estado", list(EstadoJob))
def test_c5_mesmo_para_mesmo_estado_e_ilegal(estado):
    """Decisão do autor: se o worker reenviar o estado, tem que aparecer como
    erro, não passar batido."""
    with pytest.raises(TransicaoIlegal):
        job_em(estado).transicionar(estado)


@pytest.mark.parametrize(
    "origem,destino",
    [
        (EstadoJob.NA_FILA, EstadoJob.CONCLUIDO),
        (EstadoJob.BAIXANDO, EstadoJob.CANCELADO),
        (EstadoJob.CONCLUIDO, EstadoJob.BAIXANDO),
        (EstadoJob.NA_FILA, EstadoJob.NA_FILA),
    ],
)
def test_c6_estado_nao_muda_quando_a_transicao_falha(origem, destino):
    """Nenhuma função de domínio pode deixar objeto em estado parcial.

    Se transicionar mutar ANTES de validar, o job fica num estado inválido e a
    exceção vira ruído: quem captura acha que nada mudou.
    """
    j = job_em(origem)
    with pytest.raises(TransicaoIlegal):
        j.transicionar(destino)
    assert j.estado is origem, "o estado foi alterado apesar da exceção"


# ===========================================================================
# Estado parcial — os modelos imutáveis são realmente imutáveis
# ===========================================================================


@pytest.mark.parametrize(
    "obj",
    [
        Formato("1", "mp4", "1x1", 1, 1, None, "avc1", "mp4a", None, None),
        Progresso(baixados=1, total=2, velocidade_bps=None, eta_s=None),
    ],
)
def test_modelos_de_valor_sao_frozen(obj):
    """Guard estrutural: objeto de valor mutável abre porta para estado parcial
    em qualquer ponto do código, não só em transicionar."""
    with pytest.raises(Exception):
        obj.format_id = "outro"


# ===========================================================================
# GRUPO D — Video.de_info_dict
# ===========================================================================


def test_d1_converte_o_fixture_real(info_dict_real):
    v = Video.de_info_dict(info_dict_real)
    assert v.video_id == "LzS8kB6lIm0"
    assert v.extractor == "Youtube"
    assert v.titulo.startswith("Camisa azul da Seleção")
    assert v.canal == "Canal Michuruca"
    assert v.duracao_s == 65
    assert v.data_upload == "20260901"
    assert v.thumbnail_url.startswith("https://i.ytimg.com/")


def test_d2_acodec_ausente_nao_levanta_keyerror(info_dict_real):
    """Os formatos 233 e 234 NÃO TÊM a chave 'acodec'.

    Medido: 'vcodec' está em 45/45 formatos, mas 'acodec' falta em 2. São
    manifests HLS de áudio (protocol=m3u8_native, resolution='audio only') em
    que o yt-dlp ainda não sabe o codec.

    Qualquer acesso por colchete (f['acodec']) levanta KeyError e derruba a
    montagem do preview inteiro. O risco não é classificar errado — é crashar.
    """
    v = Video.de_info_dict(info_dict_real)  # não pode estourar
    assert len(v.formatos) > 0


def test_d3_codec_desconhecido_nao_e_confundido_com_ausente(info_dict_real):
    """Três estados, não dois: tem / não tem / desconhecido.

    Um formato com vcodec='none' e acodec desconhecido é só-áudio. Tratar
    'desconhecido' como 'não tem' o classificaria como nem-vídeo-nem-áudio,
    e ele sumiria da lista.
    """
    v = Video.de_info_dict(info_dict_real)
    por_id = {f.format_id: f for f in v.formatos}

    for fid in ("233", "234"):
        f = por_id[fid]
        assert f.acodec is None, "codec desconhecido é representado por None"
        assert tem_video(f) is False
        assert tem_audio(f) is True, f"{fid} é só-áudio, não pode sumir"


def test_d4_storyboards_ficam_fora(info_dict_real):
    """Os 4 formatos ext=mhtml são as miniaturas da barra de progresso.

    Filtrados na CONVERSÃO, não na exibição: nenhum consumidor os quer, e
    deixá-los obrigaria cada um a lembrar de filtrar.
    """
    v = Video.de_info_dict(info_dict_real)
    assert all(f.ext != "mhtml" for f in v.formatos)
    assert len(info_dict_real["formats"]) - len(v.formatos) == 4


def test_d5_regressao_da_classificacao(info_dict_real):
    """TRAVA OS NÚMEROS MEDIDOS no fixture real.

    Contagem obtida rodando a classificação sobre spike_meta.json:
        45 formatos brutos
        -4 storyboards (ext=mhtml)
        = 41 formatos, sendo 29 só-vídeo e 12 só-áudio

    Os 12 de áudio incluem os 233 e 234, que só entram na conta se
    'codec desconhecido' for tratado como diferente de 'sem codec'.
    Uma implementação que trate ausência como 'não tem' devolve 10 e perde
    dois formatos — é essa regressão que este teste pega.
    """
    v = Video.de_info_dict(info_dict_real)
    so_video = [f for f in v.formatos if tem_video(f) and not tem_audio(f)]
    so_audio = [f for f in v.formatos if tem_audio(f) and not tem_video(f)]

    assert len(v.formatos) == 41
    assert len(so_video) == 29, f"esperado 29 só-vídeo, veio {len(so_video)}"
    assert len(so_audio) == 12, f"esperado 12 só-áudio, veio {len(so_audio)}"
    assert len(so_video) + len(so_audio) == len(v.formatos)


def test_d6_fps_fracionario_preservado(info_dict_real):
    """59.94 não pode virar 59. int() aqui perde informação de cadência."""
    v = Video.de_info_dict(info_dict_real)
    com_fps = [f for f in v.formatos if f.fps]
    assert com_fps
    assert all(isinstance(f.fps, float) for f in com_fps)


def test_d7_largura_e_altura_sao_inteiros_ou_none(info_dict_real):
    v = Video.de_info_dict(info_dict_real)
    for f in v.formatos:
        assert f.largura is None or isinstance(f.largura, int)
        assert f.altura is None or isinstance(f.altura, int)


@pytest.mark.parametrize("chave", ["duration", "thumbnail", "channel", "upload_date"])
def test_d8_campos_ausentes_viram_none(info_dict_real, chave):
    bruto = {k: v for k, v in info_dict_real.items() if k != chave}
    v = Video.de_info_dict(bruto)  # não pode estourar
    assert v is not None


def test_d9_channel_ausente_cai_para_uploader(info_dict_real):
    bruto = {k: v for k, v in info_dict_real.items() if k != "channel"}
    assert Video.de_info_dict(bruto).canal == info_dict_real["uploader"]


def test_d10_url_canonica_vem_de_webpage_url(info_dict_real):
    """O yt-dlp já canonicaliza: a entrada era uma URL de shorts com ?si=,
    e o webpage_url veio limpo. A nossa normalização serve à decisão barata,
    antes da rede; o webpage_url é a verdade (SPEC 5.3)."""
    v = Video.de_info_dict(info_dict_real)
    assert v.url_canonica == info_dict_real["webpage_url"]
    assert v.url_canonica != info_dict_real["original_url"]


def test_d11_formats_vazio():
    v = Video.de_info_dict(
        {
            "id": "x",
            "extractor_key": "Youtube",
            "webpage_url": "https://x",
            "title": "t",
            "formats": [],
        }
    )
    assert v.formatos == ()


def test_d12_sem_a_chave_formats():
    v = Video.de_info_dict(
        {"id": "x", "extractor_key": "Youtube", "webpage_url": "https://x", "title": "t"}
    )
    assert v.formatos == ()


# ===========================================================================
# tem_video / tem_audio isolados — os três estados
# ===========================================================================


def f(vcodec, acodec, ext="mp4"):
    return Formato("x", ext, None, None, None, None, vcodec, acodec, None, None)


@pytest.mark.parametrize(
    "fmt,esperado",
    [
        (f("avc1.64", "mp4a.40.2"), True),
        (f("avc1.64", "none"), True),
        (f("none", "mp4a.40.2"), False),
        (f("none", None), False),
    ],
)
def test_tem_video(fmt, esperado):
    assert tem_video(fmt) is esperado


@pytest.mark.parametrize(
    "fmt,esperado",
    [
        (f("avc1.64", "mp4a.40.2"), True),
        (f("avc1.64", "none"), False),
        (f("none", "mp4a.40.2"), True),
        (f("none", None), True),  # desconhecido + sem vídeo = é áudio
        (f("avc1.64", None), False),  # desconhecido + com vídeo = conservador
    ],
)
def test_tem_audio(fmt, esperado):
    """A linha que importa: vcodec='none' com acodec desconhecido é ÁUDIO.

    É o caso dos formatos 233 e 234 do fixture. Se 'desconhecido' virasse
    'não tem', eles não seriam nem vídeo nem áudio e sumiriam da lista.
    """
    assert tem_audio(fmt) is esperado
