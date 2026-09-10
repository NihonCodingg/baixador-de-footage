"""Testes de T3 — nomenclatura e sanitização de arquivo para Windows.

Escritos ANTES da implementação, com a lista de casos de borda confirmada pelo
autor (CLAUDE.md, seção "T2, T3 e T5 exigem TDD com confirmação").

Nenhum teste toca o disco: `existe` entra como callable injetado.

Referência: SPEC 8.1 a 8.4. Evidência das armadilhas: RESEARCH 7.
"""

import unicodedata

import pytest

from src.domain.erros import NomeImpossivel
from src.domain.nomes import (
    MINIMO_TITULO_SEM_AVISO,
    ORCAMENTO_CAMINHO,
    RESERVA_COLISAO,
    TITULO_FALLBACK,
    aplicar_reservado,
    e_reservado,
    montar_caminho,
    montar_nome,
    resolver_colisao,
    sanitizar,
    tem_alfanumerico,
    truncar_titulo,
)

# Dados do vídeo real capturado pelo spike (spike_meta.json).
VIDEO_ID = "LzS8kB6lIm0"  # 11 caracteres, como todo id do YouTube
DATA = "20260901"  # 8 caracteres
EXT = ".mp4"  # 4 caracteres

# Custo fixo do template, fora a pasta (SPEC 8.3):
#   com data:  1 + 8 + 3 + 2 + 11 + 1 + 4 = 30
#   sem data:  1         + 2 + 11 + 1 + 4 = 19
FIXO_COM_DATA = 30
FIXO_SEM_DATA = 19

# TITULO_FALLBACK vem de src.domain.nomes de propósito: importar em vez de
# redefinir impede que teste e implementação divirjam em silêncio.
# Decidido: "video", não "video_{id}" — o [{id}] do template já dá unicidade.


def pasta_de(n: int) -> str:
    """Constrói um caminho de pasta com exatamente n caracteres."""
    assert n >= 3
    return "D:/" + "x" * (n - 3)


def existe_falso(*caminhos):
    """Dublê de os.path.exists, case-insensitive como o Windows.

    A insensibilidade mora aqui de propósito: o domínio não deve manter seu
    próprio conjunto comparado com sensibilidade a caixa (SPEC 8.4).
    """
    normalizados = {c.lower() for c in caminhos}
    return lambda p: p.lower() in normalizados


# ===========================================================================
# GRUPO 1 — Mapeamento de caracteres proibidos (SPEC 8.2, regra 2)
# ===========================================================================


def test_1_1_pipe_vira_separador_com_espacos():
    assert sanitizar("Gameplay|Rush B") == "Gameplay - Rush B"


def test_1_2_pipe_ja_espacado_nao_duplica_espaco():
    assert sanitizar("Gameplay | Rush B") == "Gameplay - Rush B"


def test_1_3_barra_preserva_intervalo():
    """O caso que o autor pediu: 2024/2025 é intervalo, não separador."""
    assert sanitizar("Highlights 2024/2025") == "Highlights 2024-2025"


def test_1_4_barra_invertida_igual_a_barra():
    assert sanitizar("Season 2024\\2025") == "Season 2024-2025"


def test_1_5_dois_pontos_vira_espaco_e_colapsa():
    """Título real do spike_meta.json."""
    assert (
        sanitizar("Camisa azul da Seleção: críticas ao design")
        == "Camisa azul da Seleção críticas ao design"
    )


def test_1_6_dois_pontos_sem_espaco_nao_cola_palavras():
    """Decisão do autor: `:` vira espaço sempre, não string vazia."""
    assert sanitizar("Round1:Final") == "Round1 Final"


def test_1_7_caracteres_mudos_sao_removidos():
    assert sanitizar('Jogada? "INSANO" <clutch> *1v5*') == "Jogada INSANO clutch 1v5"


def test_1_8_nenhum_proibido_sobrevive():
    entrada = 'a<b>c:d"e/f\\g|h?i*j'
    saida = sanitizar(entrada)
    for proibido in '<>:"/\\|?*':
        assert proibido not in saida, f"{proibido!r} sobreviveu em {saida!r}"


def test_1_9_controle_removido_antes_de_colapsar():
    """A ordem importa: remover controle depois do colapso deixaria espaço a mais."""
    assert sanitizar("Video\x00\x01com\x1fcontrole") == "Videocomcontrole"


def test_1_10_acentos_sao_preservados():
    """Acentuado é legal no NTFS e o título real do spike tem ã, ç, í, ó."""
    assert sanitizar("Seleção histórica") == "Seleção histórica"


# ===========================================================================
# GRUPO 2 — Nomes reservados do DOS (SPEC 8.2, regra 5)
# ===========================================================================


# Tabela protegida do formatador: espelha as famílias de NOMES_RESERVADOS.
# fmt: off
@pytest.mark.parametrize("nome", [
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM5", "COM9",
    "LPT1", "LPT5", "LPT9",
])
# fmt: on
def test_2_1_reservados_sao_detectados(nome):
    assert e_reservado(nome) is True
    assert aplicar_reservado(nome) == nome + "_"


def test_2_2_reservado_com_extensao_no_nome_final():
    """Microsoft: NUL.txt é equivalente a NUL. A extensão não salva."""
    nome = montar_nome("NUL", VIDEO_ID, None, EXT)
    base = nome[:-len(EXT)]
    assert not e_reservado(base), f"nome-base {base!r} continua reservado"


@pytest.mark.parametrize("nome", ["con", "Con", "nUl", "LpT1", "aUx"])
def test_2_3_deteccao_e_case_insensitive(nome):
    assert e_reservado(nome) is True


# Tabela protegida do formatador: linha 1 são prefixos parecidos, linha 2 são números fora da faixa.
# fmt: off
@pytest.mark.parametrize("nome", [
    "CONS", "CONTRA", "CONSOLE", "NULO", "AUXILIAR", "PRNT",
    "COM10", "COM0", "LPT0", "LPT10", "COM", "LPT",
])
# fmt: on
def test_2_4_nao_reservados_ficam_intactos(nome):
    """O caso de MAIOR valor do grupo.

    A implementação ingênua usa startswith e estraga CONSOLE, CONTRA e NULO —
    palavras comuns em título de gaming. COM10 e LPT0 entram porque só
    COM1-COM9 e LPT1-LPT9 são reservados.
    """
    assert e_reservado(nome) is False
    assert aplicar_reservado(nome) == nome


# ===========================================================================
# GRUPO 3 — Fallback video_{id} (SPEC 8.2, regra 7)
# ===========================================================================

@pytest.mark.parametrize("titulo", ["", "   ", "...", "!!! ???", "🎮🔥💀", "---"])
def test_3_1_sem_alfanumerico_cai_no_fallback(titulo):
    """Regra única cobrindo vazio, espaços, pontuação e só-emoji.

    Comparação EXATA e não `in`: um título que contivesse a palavra "video"
    faria um teste por substring passar por acidente.
    """
    assert (montar_nome(titulo, VIDEO_ID, DATA, EXT)
            == f"{DATA} - {TITULO_FALLBACK} [{VIDEO_ID}]{EXT}")


def test_3_2_texto_com_emoji_mantem_o_emoji():
    """Decisão do autor: só o caso degenerado perde o emoji."""
    assert (montar_nome("Rush B 🎮", VIDEO_ID, DATA, EXT)
            == f"{DATA} - Rush B 🎮 [{VIDEO_ID}]{EXT}")


def test_3_3_um_unico_digito_ja_evita_o_fallback():
    """Fronteira exata da regra."""
    assert tem_alfanumerico("2") is True
    assert (montar_nome("2", VIDEO_ID, DATA, EXT)
            == f"{DATA} - 2 [{VIDEO_ID}]{EXT}")


def test_3_5_titulo_que_contem_a_palavra_video_nao_e_fallback():
    """Guard: 'video' é palavra comum em título. Não pode ser confundida
    com o fallback."""
    assert (montar_nome("video da final", VIDEO_ID, DATA, EXT)
            == f"{DATA} - video da final [{VIDEO_ID}]{EXT}")


# Tabela protegida do formatador: uma linha de casos válidos, uma de inválidos.
# fmt: off
@pytest.mark.parametrize("texto,esperado", [
    ("abc", True), ("123", True), ("á", True), ("日本", True),
    ("", False), ("   ", False), ("...", False), ("🎮", False), ("-_-", False),
])
# fmt: on
def test_3_4_tem_alfanumerico(texto, esperado):
    assert tem_alfanumerico(texto) is esperado


# ===========================================================================
# GRUPO 4 — Orçamento de caminho (SPEC 8.3)
# ===========================================================================

def test_4_1_caminho_curto_nao_trunca():
    titulo = "Camisa azul da Seleção críticas ao design"
    r = montar_caminho(pasta_de(26), titulo, VIDEO_ID, DATA, EXT)
    assert titulo in r.caminho
    assert r.aviso is None


def test_4_2_titulo_maximo_do_youtube_com_pasta_rasa():
    """100 chars é o teto do YouTube. Com pasta rasa, sobra folga de sobra."""
    titulo = "T" * 100
    r = montar_caminho(pasta_de(26), titulo, VIDEO_ID, DATA, EXT)
    assert titulo in r.caminho
    assert r.aviso is None


def test_4_3_pasta_profunda_trunca_o_titulo():
    """pasta 130 -> orçamento = 240 - 130 - 30 - 5 (reserva de colisão) = 75."""
    esperado = ORCAMENTO_CAMINHO - 130 - FIXO_COM_DATA - RESERVA_COLISAO
    r = montar_caminho(pasta_de(130), "T" * 100, VIDEO_ID, DATA, EXT)
    assert len(r.caminho) <= ORCAMENTO_CAMINHO
    assert "T" * esperado in r.caminho, "título cortado demais"
    assert "T" * (esperado + 1) not in r.caminho, "título cortado de menos"


@pytest.mark.parametrize("tam_pasta", [20, 50, 100, 130, 160, 175])
def test_4_4_caminho_final_nunca_passa_do_orcamento(tam_pasta):
    r = montar_caminho(pasta_de(tam_pasta), "T" * 100, VIDEO_ID, DATA, EXT)
    assert len(r.caminho) <= ORCAMENTO_CAMINHO


def test_4_5_truncamento_preserva_a_extensao():
    """Sem extensão, o Premiere não importa o arquivo."""
    r = montar_caminho(pasta_de(150), "T" * 100, VIDEO_ID, DATA, EXT)
    assert r.caminho.endswith(EXT)


def test_4_6_truncamento_preserva_o_video_id():
    """O [id] é a chave de reconciliação com o histórico (SPEC 9).

    Perdê-lo quebra o "onde está o arquivo", que é metade do produto.
    """
    r = montar_caminho(pasta_de(150), "T" * 100, VIDEO_ID, DATA, EXT)
    assert f"[{VIDEO_ID}]" in r.caminho


def test_4_7_pasta_profunda_emite_aviso_nao_erro():
    """pasta 180 -> sobram 30 para o título, abaixo do mínimo de 40.

    Decisão do autor: aviso, não erro. O download ainda é possível.
    """
    r = montar_caminho(pasta_de(180), "T" * 100, VIDEO_ID, DATA, EXT)
    assert r.aviso is not None
    assert r.caminho
    assert len(r.caminho) <= ORCAMENTO_CAMINHO


def test_4_8_pasta_absurda_levanta_erro_claro():
    """pasta 215 + fixo 30 = 245 > 240. Não existe nome válido."""
    with pytest.raises(NomeImpossivel) as exc:
        montar_caminho(pasta_de(215), "qualquer", VIDEO_ID, DATA, EXT)
    assert "215" in str(exc.value) or "profund" in str(exc.value).lower()


def test_4_9_sem_data_upload_o_template_encolhe():
    """Sem data: `{titulo} [{id}].{ext}`, e o fixo cai de 30 para 19."""
    nome = montar_nome("Rush B", VIDEO_ID, None, EXT)
    assert nome == f"Rush B [{VIDEO_ID}]{EXT}"
    assert not nome.startswith(" - ")


def test_4_10_com_data_o_template_completo():
    nome = montar_nome("Rush B", VIDEO_ID, DATA, EXT)
    assert nome == f"{DATA} - Rush B [{VIDEO_ID}]{EXT}"


def test_4_11_truncar_titulo_respeita_o_limite():
    assert len(truncar_titulo("T" * 100, 80)) <= 80
    assert truncar_titulo("curto", 80) == "curto"


def test_4_12_truncamento_nao_deixa_ponto_nem_espaco_no_fim():
    """Cortar no meio pode deixar espaço ou ponto no fim — que o Windows
    remove em silêncio, tornando o caminho gravado diferente do real."""
    assert not truncar_titulo("palavra final aqui", 8).endswith((" ", "."))


# ===========================================================================
# GRUPO 5 — Colisão (SPEC 8.4)
# ===========================================================================

def test_5_1_caminho_livre_fica_inalterado():
    caminho = "D:/F/20260901 - Rush B [LzS8kB6lIm0].mp4"
    assert resolver_colisao(caminho, existe_falso()) == caminho


def test_5_2_existente_ganha_sufixo_2():
    caminho = "D:/F/video.mp4"
    assert resolver_colisao(caminho, existe_falso(caminho)) == "D:/F/video (2).mp4"


def test_5_3_pula_ate_encontrar_livre():
    caminho = "D:/F/video.mp4"
    ocupados = existe_falso(caminho, "D:/F/video (2).mp4", "D:/F/video (3).mp4")
    assert resolver_colisao(caminho, ocupados) == "D:/F/video (4).mp4"


def test_5_4_sufixo_vai_antes_da_extensao():
    r = resolver_colisao("D:/F/video.mp4", existe_falso("D:/F/video.mp4"))
    assert r.endswith(".mp4")
    assert not r.endswith(") ")
    assert "(2)" in r and r.index("(2)") < r.index(".mp4")


def test_5_5_colisao_ignora_diferenca_de_caixa():
    """O caso A: Windows não diferencia caixa, IDs do YouTube sim.

    Prova que a função DELEGA a `existe` em vez de comparar strings por conta
    própria. Sobrescrita silenciosa de footage é o pior modo de falha do
    projeto.
    """
    caminho = "D:/F/video.mp4"
    r = resolver_colisao(caminho, existe_falso("D:/F/VIDEO.MP4"))
    assert r == "D:/F/video (2).mp4"


@pytest.mark.parametrize("tam_pasta", [26, 100, 160, 175])
def test_5_6_sufixo_de_colisao_nao_estoura_o_orcamento(tam_pasta):
    """Interação entre grupo 4 e 5: o ` (2)` entra DEPOIS do truncamento.

    É o caso que passa em todos os testes isolados e só aparece na composição.
    Resolvido pela reserva de RESERVA_COLISAO no orçamento (SPEC 8.3).
    """
    r = montar_caminho(pasta_de(tam_pasta), "T" * 100, VIDEO_ID, DATA, EXT)
    final = resolver_colisao(r.caminho, existe_falso(r.caminho))
    assert len(final) <= ORCAMENTO_CAMINHO


# ===========================================================================
# GRUPO 6 — Regressão e guarda
# ===========================================================================

@pytest.mark.parametrize("titulo", [
    'a:b<c>d|e*f"g?h',
    "Camisa azul da Seleção: críticas",
    "Highlights 2024/2025",
])
def test_6_1_saida_nunca_contem_homoglifo_de_largura_total(titulo):
    """Quebra se alguém trocar a implementação por sanitize_filename do yt-dlp.

    Ele substitui `:` por U+FF1A, `/` por U+29F8 etc. O arquivo fica válido,
    mas o nome deixa de ser digitável e some da busca (RESEARCH 7.4).
    Protege a decisão SPEC 13.5.
    """
    saida = sanitizar(titulo)
    for ch in saida:
        assert not (0xFF00 <= ord(ch) <= 0xFFEF), f"homóglifo {ch!r} na saída"
        assert ch not in "\u29f8\u29f9", f"barra de largura total {ch!r} na saída"


@pytest.mark.parametrize("titulo", [
    "Melhores momentos.", "Melhores momentos ", "Final...", "Final   ",
    "Melhores momentos. ", "Round 1.",
])
def test_6_2_nome_nunca_termina_em_ponto_ou_espaco(titulo):
    """O Windows remove ponto e espaço finais EM SILÊNCIO.

    Se não tratarmos, o caminho gravado no histórico não é o caminho do
    arquivo, e o botão "abrir pasta" quebra sem erro nenhum.
    """
    assert not sanitizar(titulo).endswith((".", " "))


def test_6_3_ponto_inicial_e_preservado():
    """Legal no Windows, e a Microsoft diz explicitamente que é aceitável.

    Guard contra strip simétrico feito sem pensar.
    """
    assert sanitizar(".hitbox") == ".hitbox"


def test_6_4_saida_e_sempre_nfc():
    """Entrada em NFD tem que sair em NFC.

    A versão anterior deste teste era VACUOSA: usava uma string já em NFC, e
    passava mesmo com a normalização removida (pego por teste de mutação).
    A entrada agora é explicitamente decomposta.
    """
    nfd = unicodedata.normalize("NFD", "Seleção histórica")
    assert nfd != "Seleção histórica", "a entrada precisa estar mesmo em NFD"

    assert sanitizar(nfd) == "Seleção histórica"


def test_6_4b_nfc_e_nfd_geram_o_mesmo_nome():
    """O bug que a normalização evita: duas representações Unicode do mesmo
    título gerando dois arquivos e dois registros no histórico."""
    titulo = "Seleção histórica"
    nome_nfc = montar_nome(unicodedata.normalize("NFC", titulo),
                           VIDEO_ID, DATA, EXT)
    nome_nfd = montar_nome(unicodedata.normalize("NFD", titulo),
                           VIDEO_ID, DATA, EXT)
    assert nome_nfc == nome_nfd


def test_6_5_constantes_batem_com_o_spec():
    """Guard contra o SPEC e o código divergirem em silêncio."""
    assert ORCAMENTO_CAMINHO == 240
    assert MINIMO_TITULO_SEM_AVISO == 40
