"""Testes de T2 — validação e normalização de link.

Escritos ANTES da implementação, com a lista confirmada pelo autor.
Nenhum teste toca a rede: normalização é manipulação de string.

Referência: SPEC 5.3.
"""

import pytest

from src.domain.erros import LinkInvalido
from src.domain.validacao import (
    LinkNormalizado,
    e_playlist_ou_canal,
    extrair_id,
    normalizar_link,
    normalizar_lote,
)

# Id real do fixture. 11 caracteres, com maiúsculas e minúsculas — de propósito,
# porque ids do YouTube diferenciam caixa e um lower() em lugar errado quebraria.
ID = "LzS8kB6lIm0"
CANONICO = f"https://www.youtube.com/watch?v={ID}"


# ===========================================================================
# A.1 — Formas equivalentes
# ===========================================================================


@pytest.mark.parametrize(
    "url",
    [
        f"https://youtu.be/{ID}",
        f"https://www.youtube.com/watch?v={ID}",
        f"https://youtube.com/watch?v={ID}",
        f"https://m.youtube.com/watch?v={ID}",
        f"https://www.youtube.com/shorts/{ID}",
        f"https://www.youtube.com/embed/{ID}",
        f"https://www.youtube.com/live/{ID}",
        f"https://www.youtube.com/v/{ID}",
    ],
)
def test_a1_formas_equivalentes_dao_o_mesmo_canonico(url):
    r = normalizar_link(url)
    assert r.ok
    assert r.url == CANONICO
    assert r.video_id == ID
    assert r.e_youtube is True


# ===========================================================================
# A.2 / A.3 — O parâmetro ?si= do botão compartilhar
# ===========================================================================


def test_a2_remove_parametro_si_do_compartilhar():
    """O caso confirmado nos dados reais.

    O original_url do spike_meta.json é:
        https://youtube.com/shorts/LzS8kB6lIm0?si=0RP8BxS-q-XGH4Dw

    O `si` muda a cada clique em Compartilhar. Sem removê-lo, colar o mesmo
    vídeo duas vezes gera duas entradas no histórico e dois downloads.
    """
    r = normalizar_link(f"https://youtube.com/shorts/{ID}?si=0RP8BxS-q-XGH4Dw")
    assert r.url == CANONICO
    assert "si=" not in r.url


def test_a3_dois_si_diferentes_sao_o_mesmo_video():
    a = normalizar_link(f"https://youtu.be/{ID}?si=AAAAAAAAAAAAAAAA")
    b = normalizar_link(f"https://youtu.be/{ID}?si=BBBBBBBBBBBBBBBB")
    assert a.url == b.url == CANONICO


# ===========================================================================
# A.4 / A.5 / A.6 — Outros parâmetros e esquema
# ===========================================================================


def test_a4_remove_timestamp():
    assert normalizar_link(f"https://www.youtube.com/watch?v={ID}&t=42").url == CANONICO


def test_a5_descarta_playlist_em_url_de_video():
    """URL de vídeo que carrega playlist: fica o vídeo, some a lista."""
    r = normalizar_link(f"https://www.youtube.com/watch?v={ID}&list=PLabcdef123")
    assert r.url == CANONICO


def test_a6_http_vira_https():
    assert normalizar_link(f"http://youtu.be/{ID}").url == CANONICO


def test_a6b_ordem_dos_parametros_nao_importa():
    a = normalizar_link(f"https://www.youtube.com/watch?t=9&v={ID}&si=X")
    assert a.url == CANONICO


# ===========================================================================
# A.7 — Canal e playlist são rejeitados
# ===========================================================================


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/@Canal_michuruca",
        "https://www.youtube.com/@Canal_michuruca/videos",
        "https://www.youtube.com/@Canal_michuruca/shorts",
        "https://www.youtube.com/playlist?list=PLabcdef123",
        "https://www.youtube.com/c/AlgumCanal",
        "https://www.youtube.com/channel/UCabcdefghijklmnopqrstu",
        "https://www.youtube.com/user/alguem",
        "https://www.youtube.com/feed/subscriptions",
    ],
)
def test_a7_canal_e_playlist_sao_rejeitados(url):
    """Download em massa está fora de escopo (SPEC 2.2).

    A recusa é ANTES da rede: extract_info percorreria a lista inteira.
    """
    assert e_playlist_ou_canal(url) is True
    with pytest.raises(LinkInvalido):
        normalizar_link(url)


def test_a7b_url_de_video_nao_e_confundida_com_canal():
    """Guard: as duas checagens não podem se atropelar."""
    assert e_playlist_ou_canal(CANONICO) is False
    assert e_playlist_ou_canal(f"https://www.youtube.com/shorts/{ID}") is False


# ===========================================================================
# A.8 / A.9 — Entradas rejeitadas
# ===========================================================================


def test_a8_id_nu_e_rejeitado():
    """Decisão do autor: 11 caracteres soltos é mais provável erro de colagem
    do que intenção. O yt-dlp aceitaria; nós não."""
    with pytest.raises(LinkInvalido):
        normalizar_link(ID)


# Tabela protegida do formatador: linha 1 é lixo textual, linha 2 são esquemas perigosos ou incompletos.
# fmt: off
@pytest.mark.parametrize("entrada", [
    "", "   ", "não é url", "isso aqui é uma frase", "ftp://exemplo.com/x",
    "javascript:alert(1)", "youtube", "https://",
])
# fmt: on
def test_a9_lixo_e_rejeitado(entrada):
    with pytest.raises(LinkInvalido):
        normalizar_link(entrada)


# ===========================================================================
# A.10 / A.11 — Outros sites passam adiante, com aviso
# ===========================================================================

@pytest.mark.parametrize("url", [
    "https://vimeo.com/123456789",
    "https://www.twitch.tv/videos/123456789",
    "https://www.dailymotion.com/video/x8abcde",
])
def test_a10_outro_site_passa_sem_normalizar(url):
    """Não rejeitamos: o yt-dlp suporta centenas de extractors (SPEC 5.3)."""
    r = normalizar_link(url)
    assert r.ok
    assert r.url == url, "URL de outro site não deve ser reescrita"
    assert r.e_youtube is False
    assert r.video_id is None


def test_a11_outro_site_traz_aviso_no_resultado():
    """O aviso viaja no resultado, não em print nem log.

    É o que permite o T7 exibi-lo no cartão de preview em vez de ele sumir.
    """
    r = normalizar_link("https://vimeo.com/123456789")
    assert r.aviso is not None
    assert r.erro is None


def test_a11b_youtube_nao_traz_aviso():
    assert normalizar_link(CANONICO).aviso is None


# ===========================================================================
# A.12 a A.15 — O lote
# ===========================================================================

def test_a12_lote_de_bloco_de_notas():
    """Linhas vazias, espaços em volta e CRLF do Windows."""
    texto = f"  https://youtu.be/{ID}  \r\n\r\n   \n https://vimeo.com/1 \r\n"
    r = normalizar_lote(texto)
    assert len(r) == 2
    assert r[0].url == CANONICO


def test_a13_lote_deduplica_formas_diferentes():
    texto = "\n".join([
        f"https://youtu.be/{ID}",
        f"https://www.youtube.com/watch?v={ID}",
        f"https://www.youtube.com/shorts/{ID}?si=XXXXXXXXXXXXXXXX",
    ])
    r = normalizar_lote(texto)
    assert len(r) == 1, "as três formas são o mesmo vídeo"
    assert r[0].url == CANONICO


def test_a14_lote_preserva_ordem_de_aparicao():
    ids = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]
    texto = "\n".join(f"https://youtu.be/{i}" for i in ids)
    assert [x.video_id for x in normalizar_lote(texto)] == ids


def test_a15_lote_misto_youtube_e_outro_site():
    texto = f"https://youtu.be/{ID}\nhttps://vimeo.com/123456789"
    r = normalizar_lote(texto)
    assert len(r) == 2
    assert r[0].e_youtube is True and r[0].aviso is None
    assert r[1].e_youtube is False and r[1].aviso is not None


def test_a16_lote_nunca_levanta_e_marca_a_linha_ruim():
    """Resultado parcial é requisito (SPEC 11.1): um link ruim numa lista de
    dez não invalida os outros nove."""
    texto = f"https://youtu.be/{ID}\nnão é url\nhttps://vimeo.com/1"
    r = normalizar_lote(texto)
    assert len(r) == 3
    assert [x.ok for x in r] == [True, False, True]
    ruim = r[1]
    assert ruim.erro is not None
    assert ruim.url is None
    assert ruim.original == "não é url"


def test_a17_lote_vazio():
    assert normalizar_lote("") == ()
    assert normalizar_lote("\n\n   \n") == ()


def test_a18_lote_deduplica_linha_invalida_repetida():
    r = normalizar_lote("não é url\nnão é url")
    assert len(r) == 1


# ===========================================================================
# extrair_id isolado
# ===========================================================================

def test_extrair_id_de_url_de_video():
    assert extrair_id(f"https://youtu.be/{ID}") == ID


def test_extrair_id_preserva_a_caixa():
    """Ids do YouTube diferenciam maiúsculas. Um lower() aqui juntaria vídeos
    diferentes no histórico."""
    assert extrair_id(f"https://youtu.be/{ID}") == "LzS8kB6lIm0"


def test_extrair_id_de_url_nao_youtube_devolve_none():
    assert extrair_id("https://vimeo.com/123456789") is None


# ===========================================================================
# Estado parcial — LinkNormalizado é imutável
# ===========================================================================

def test_link_normalizado_e_imutavel():
    r = LinkNormalizado(original="x", url="y", video_id=None, e_youtube=False)
    with pytest.raises(Exception):
        r.url = "outro"
