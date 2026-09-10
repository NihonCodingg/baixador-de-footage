"""Testes de campo_limite e da resolução do template {dim}.

Parte do T2, adiantada porque a decisão do teto na menor dimensão (SPEC 6.3)
precisou ser resolvida antes de escrever o resto do ticket.

Nenhum teste toca a rede. O teste de regressão usa o motor de seleção real do
yt-dlp, que é computação pura sobre a lista de formatos.
"""

import copy

import pytest
import yaml

from src.domain.models import Formato
from src.domain.perfis import (
    Perfil,
    campo_limite,
    resolver_dim,
    resolver_format,
)


def fmt(largura=None, altura=None, format_id="x", ext="mp4", vcodec="avc1.64", acodec="mp4a.40.2"):
    """Formato mínimo para os testes de orientação."""
    return Formato(
        format_id=format_id,
        ext=ext,
        resolucao=f"{largura}x{altura}" if largura else None,
        largura=largura,
        altura=altura,
        fps=None,
        vcodec=vcodec,
        acodec=acodec,
        tbr=None,
        tamanho_bytes=None,
    )


# ===========================================================================
# campo_limite — a decisão de orientação
# ===========================================================================


def test_vertical_usa_width():
    """Short 1080x1920: a menor dimensão é a largura."""
    assert campo_limite([fmt(1080, 1920), fmt(608, 1080)]) == "width"


def test_horizontal_usa_height():
    """Vídeo comum 1920x1080: a menor dimensão é a altura."""
    assert campo_limite([fmt(1920, 1080), fmt(1280, 720)]) == "height"


def test_quadrado_usa_height():
    """Decisão documentada em SPEC 6.3.

    Num vídeo quadrado os dois filtros selecionam o mesmo conjunto, então a
    escolha é convenção, não correção: "height" porque é assim que se descreve
    qualidade de vídeo ("1080p" = 1080 linhas).

    A regra é ESTRITA: usa "width" apenas quando altura > largura.
    """
    assert campo_limite([fmt(1080, 1080)]) == "height"


def test_lista_so_de_audio_devolve_none():
    """Sem nenhuma dimensão, o filtro é omitido do seletor."""
    sem_dim = [
        fmt(None, None, format_id="140", vcodec="none"),
        fmt(None, None, format_id="251", vcodec="none"),
    ]
    assert campo_limite(sem_dim) is None


def test_lista_vazia_devolve_none():
    assert campo_limite([]) is None


def test_formatos_sem_dimensao_sao_ignorados():
    """Mistos: os só-áudio não podem influenciar a decisão de orientação."""
    mistos = [
        fmt(None, None, format_id="140", vcodec="none"),
        fmt(1080, 1920, format_id="137"),
        fmt(None, None, format_id="251", vcodec="none"),
    ]
    assert campo_limite(mistos) == "width"


def test_decide_pelo_maior_formato_nao_pelo_primeiro():
    """A orientação vem do formato de maior área.

    Guard contra implementação que olhasse só o primeiro item da lista — a
    ordem dos formatos no info_dict não é garantida.
    """
    assert campo_limite([fmt(320, 180), fmt(1080, 1920)]) == "width"
    assert campo_limite([fmt(180, 320), fmt(1920, 1080)]) == "height"


def test_dimensao_zero_e_tratada_como_ausente():
    """width=0 é dado corrompido, não um vídeo de largura zero."""
    assert campo_limite([fmt(0, 1080), fmt(1920, 1080)]) == "height"


# ===========================================================================
# resolver_dim / resolver_format — a substituição do template
# ===========================================================================


def perfil(format_, limite):
    return Perfil(
        nome="t",
        descricao="",
        format=format_,
        limite_dimensao=limite,
        format_sort=(),
        merge_output_format="mp4",
        postprocessors=(),
        exige_ffmpeg=True,
    )


def test_dim_vertical():
    p = perfil("bv*{dim}+ba/b", 1080)
    assert resolver_dim(p, [fmt(1080, 1920)]) == "[width<=1080]"


def test_dim_horizontal():
    p = perfil("bv*{dim}+ba/b", 1080)
    assert resolver_dim(p, [fmt(1920, 1080)]) == "[height<=1080]"


def test_dim_vazio_quando_perfil_nao_usa_dimensao():
    """so_audio tem limite_dimensao: null."""
    p = perfil("ba/b", None)
    assert resolver_dim(p, [fmt(1920, 1080)]) == ""


def test_dim_vazio_quando_nao_ha_formatos_com_dimensao():
    """O filtro é OMITIDO, não preenchido com um padrão."""
    p = perfil("bv*{dim}+ba/b", 1080)
    assert resolver_dim(p, [fmt(None, None, vcodec="none")]) == ""


def test_format_substitui_todas_as_ocorrencias():
    """O template do edicao_1080 tem {dim} três vezes."""
    p = perfil("bv*{dim}[vcodec^=avc1]+ba/bv*{dim}+ba/b{dim}/b", 1080)
    resultado = resolver_format(p, [fmt(1920, 1080)])
    assert "{dim}" not in resultado
    assert resultado.count("[height<=1080]") == 3


def test_format_sem_dimensao_fica_sintaticamente_valido():
    """Omitir o filtro não pode deixar colchete solto nem barra dupla."""
    p = perfil("bv*{dim}+ba/b{dim}/b", 1080)
    resultado = resolver_format(p, [fmt(None, None, vcodec="none")])
    assert resultado == "bv*+ba/b/b"
    assert "[" not in resultado and "]" not in resultado


# ===========================================================================
# REGRESSÃO — trava o resultado medido com o motor real do yt-dlp
# ===========================================================================


def _para_formato(bruto: dict) -> Formato:
    return Formato(
        format_id=bruto.get("format_id"),
        ext=bruto.get("ext"),
        resolucao=bruto.get("resolution"),
        largura=bruto.get("width"),
        altura=bruto.get("height"),
        fps=bruto.get("fps"),
        vcodec=bruto.get("vcodec"),
        acodec=bruto.get("acodec"),
        tbr=bruto.get("tbr"),
        tamanho_bytes=bruto.get("filesize") or bruto.get("filesize_approx"),
    )


def _selecionar(formatos_brutos, seletor, format_sort):
    """Roda o motor de seleção REAL do yt-dlp, offline.

    build_format_selector e a função que ele devolve são computação pura sobre
    a lista de formatos — não tocam a rede.
    """
    import yt_dlp

    ydl = yt_dlp.YoutubeDL(
        {"quiet": True, "no_warnings": True, "format": seletor, "format_sort": list(format_sort)}
    )
    fs = copy.deepcopy(formatos_brutos)
    ydl.sort_formats({"formats": fs, "_format_sort_fields": None})

    ctx = {
        "formats": fs,
        "has_merged_format": any("none" not in (f.get("acodec"), f.get("vcodec")) for f in fs),
        "incomplete_formats": (
            all(f.get("vcodec") == "none" for f in fs) or all(f.get("acodec") == "none" for f in fs)
        ),
    }
    return list(ydl.build_format_selector(seletor)(ctx))


@pytest.fixture
def perfil_real():
    """edicao_1080 lido do config/perfis.yaml de verdade.

    Ler o arquivo real é o ponto: se alguém editar o seletor, este teste
    quebra.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    dados = yaml.safe_load((raiz / "config" / "perfis.yaml").read_text(encoding="utf-8"))
    p = dados["perfis"]["edicao_1080"]
    return perfil(p["format"], p["limite_dimensao"]), tuple(p["format_sort"])


def test_regressao_short_vertical_seleciona_1080x1920(info_dict_real, perfil_real):
    """TRAVA O NÚMERO MEDIDO.

    Com o spike_meta.json real e o perfil edicao_1080, a seleção final tem que
    ser 137+140 em 1080x1920.

    Antes da correção de SPEC 6.3, o filtro fixo [height<=1080] entregava
    480x854 neste mesmo vídeo — o filtro de altura pressupunha vídeo
    horizontal, e o filtro de codec agravava. Esse número foi caro de
    descobrir; se alguém mexer no seletor e a qualidade despencar de novo,
    este teste quebra em vez de o problema aparecer no meio de uma edição.
    """
    p, sort = perfil_real
    brutos = [f for f in info_dict_real["formats"] if f.get("ext") != "mhtml"]

    assert campo_limite([_para_formato(f) for f in brutos]) == "width", (
        "o vídeo do fixture é um Short vertical"
    )

    seletor = resolver_format(p, [_para_formato(f) for f in brutos])
    assert "[width<=1080]" in seletor

    escolhidos = _selecionar(brutos, seletor, sort)

    # O operador `+` devolve UM formato mesclado, com os componentes em
    # `requested_formats` — não dois itens na lista.
    assert len(escolhidos) == 1, f"esperado 1 formato mesclado, veio {len(escolhidos)}"
    merged = escolhidos[0]

    assert merged["format_id"] == "137+140", f"esperado 137+140, veio {merged['format_id']}"
    assert (merged["width"], merged["height"]) == (1080, 1920), (
        f"esperado 1080x1920, veio {merged['width']}x{merged['height']}"
    )
    assert merged["vcodec"].startswith("avc1"), (
        f"edicao_1080 deve preferir H.264, veio {merged['vcodec']}"
    )
    assert merged["acodec"].startswith("mp4a"), (
        f"edicao_1080 deve preferir AAC, veio {merged['acodec']}"
    )

    componentes = [f["format_id"] for f in merged["requested_formats"]]
    assert componentes == ["137", "140"], f"componentes do merge inesperados: {componentes}"


def test_regressao_o_filtro_antigo_de_altura_degradava(info_dict_real):
    """Documenta o bug corrigido, com o motor real.

    Não é teste do nosso código — é a prova de que a correção era necessária.
    Se este teste um dia passar a devolver 1080x1920, o yt-dlp mudou de
    comportamento e vale reavaliar a solução.
    """
    brutos = [f for f in info_dict_real["formats"] if f.get("ext") != "mhtml"]
    antigo = (
        "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b[height<=1080]/b"
    )

    escolhidos = _selecionar(brutos, antigo, ["res:1080", "vcodec:h264", "acodec:aac", "fps"])
    video = next(f for f in escolhidos if f.get("width"))

    assert (video["width"], video["height"]) == (480, 854), (
        "o filtro fixo de altura entregava 480x854 neste Short; "
        f"veio {video['width']}x{video['height']}"
    )


# ===========================================================================
# GRUPO B — Carga e validação de perfis (SPEC 6.2)
# ===========================================================================

from src.domain.erros import PerfilInvalido  # noqa: E402
from src.domain.perfis import (  # noqa: E402
    carregar_perfis,
    disponivel,
    validar_perfil,
)

BOM = {
    "descricao": "ok",
    "limite_dimensao": 1080,
    "format": "bv*{dim}+ba/b",
    "format_sort": ["res:1080"],
    "merge_output_format": "mp4",
    "postprocessors": [],
    "exige_ffmpeg": True,
}


def com(**mudancas):
    d = dict(BOM)
    d.update(mudancas)
    return d


def test_b1_perfil_bom_carrega():
    p = validar_perfil("x", BOM)
    assert p.nome == "x" and p.limite_dimensao == 1080


@pytest.mark.parametrize(
    "bruto",
    [
        com(format=None),
        com(format=""),
        com(format="   "),
    ],
)
def test_b2_format_ausente_ou_vazio(bruto):
    with pytest.raises(PerfilInvalido):
        validar_perfil("x", bruto)


def test_b3_format_com_virgula_e_recusado():
    """`,` baixa vários formatos e quebra a premissa de um arquivo por job."""
    with pytest.raises(PerfilInvalido) as exc:
        validar_perfil("x", com(format="bv*{dim}+ba,ba"))
    assert "," in str(exc.value) or "vírgula" in str(exc.value).lower()


def test_b4_merge_output_format_desconhecido():
    with pytest.raises(PerfilInvalido):
        validar_perfil("x", com(merge_output_format="avi"))


def test_b5_postprocessor_inexistente_falha_na_carga():
    """Melhor falhar ao ler a config do que com KeyError no meio de um job."""
    with pytest.raises(PerfilInvalido):
        validar_perfil("x", com(postprocessors=[{"key": "NaoExiste"}]))


def test_b5b_postprocessor_real_e_aceito():
    p = validar_perfil(
        "x", com(postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}])
    )
    assert p.postprocessors[0]["key"] == "FFmpegExtractAudio"


def test_b6_exige_ffmpeg_sem_ffmpeg_fica_indisponivel_sem_excecao():
    """Perfil indisponível é estado, não erro: a UI mostra desabilitado."""
    p = validar_perfil("x", BOM)
    assert disponivel(p, tem_ffmpeg=True) is True
    assert disponivel(p, tem_ffmpeg=False) is False


def test_b6b_perfil_que_nao_exige_ffmpeg_fica_disponivel():
    p = validar_perfil("x", com(exige_ffmpeg=False))
    assert disponivel(p, tem_ffmpeg=False) is True


def test_b7_seletor_que_nao_parseia_falha_na_carga():
    """Validação sintática entra INJETADA: chamar build_format_selector aqui
    seria importar yt_dlp no domínio, o que a REGRA 1 proíbe."""

    def validador(seletor):
        if "[[[" in seletor:
            raise ValueError("colchete desbalanceado")

    with pytest.raises(PerfilInvalido):
        validar_perfil("x", com(format="bv*{dim}[[[+ba"), validar_seletor=validador)


def test_b7b_sem_validador_a_sintaxe_nao_e_checada():
    """O domínio continua puro por padrão."""
    p = validar_perfil("x", com(format="bv*{dim}[[[+ba"))
    assert p is not None


def test_b8_limite_dimensao_nao_numerico():
    with pytest.raises(PerfilInvalido):
        validar_perfil("x", com(limite_dimensao="mil e oitenta"))


def test_b9_template_com_dim_mas_sem_limite():
    """{dim} sem limite_dimensao ficaria sem valor para substituir."""
    with pytest.raises(PerfilInvalido):
        validar_perfil("x", com(limite_dimensao=None, format="bv*{dim}+ba/b"))


def test_b9b_sem_dim_e_sem_limite_e_valido():
    """É o caso do so_audio."""
    p = validar_perfil("x", com(limite_dimensao=None, format="ba/b"))
    assert p.limite_dimensao is None


@pytest.mark.parametrize("dados", [{}, {"outra_chave": {}}, {"perfis": {}}])
def test_b10_yaml_degenerado(dados):
    with pytest.raises(PerfilInvalido):
        carregar_perfis(dados)


def test_b11_carrega_os_quatro_perfis_reais():
    """Lê o config/perfis.yaml de verdade."""
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    dados = yaml.safe_load((raiz / "config" / "perfis.yaml").read_text(encoding="utf-8"))
    perfis = carregar_perfis(dados)
    assert set(perfis) == {"edicao_1080", "edicao_4k", "so_audio", "preview_leve"}
    assert perfis["so_audio"].limite_dimensao is None
    assert perfis["edicao_1080"].limite_dimensao == 1080


def test_b12_carga_e_tudo_ou_nada():
    """Estado parcial: um perfil inválido não pode deixar os outros carregados.

    Um conjunto meio-carregado faz a UI mostrar alguns perfis e omitir outros
    em silêncio. Melhor falhar na subida e o autor corrigir o YAML.
    """
    dados = {"perfis": {"bom": dict(BOM), "ruim": com(merge_output_format="avi")}}
    with pytest.raises(PerfilInvalido):
        carregar_perfis(dados)


def test_b13_perfil_inexistente_no_conjunto():
    perfis = carregar_perfis({"perfis": {"bom": dict(BOM)}})
    assert "nao_existe" not in perfis


# ===========================================================================
# opcoes_ytdlp — o dict que o adapter recebe
# (acrescentado depois do commit vermelho: a função é stub T2 e ficou fora da
#  lista revisada; é montagem de dict, coberta indiretamente por resolver_format)
# ===========================================================================

from src.domain.perfis import opcoes_ytdlp  # noqa: E402


def test_opcoes_ytdlp_monta_o_dict_com_seletor_resolvido():
    p = validar_perfil("x", BOM)
    opcoes = opcoes_ytdlp(p, [fmt(1080, 1920)], "D:/F/video.mp4")
    assert opcoes["format"] == "bv*[width<=1080]+ba/b"
    assert opcoes["format_sort"] == ["res:1080"]
    assert opcoes["merge_output_format"] == "mp4"
    assert opcoes["outtmpl"] == "D:/F/video.mp4"
    assert opcoes["noplaylist"] is True, "segunda defesa contra playlist"
    assert "," not in opcoes["format"]
