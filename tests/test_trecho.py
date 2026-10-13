"""Trecho do vídeo: ler os tempos digitados e validar contra a duração.

Puro: nada de rede nem disco. Os casos são os da lista combinada com o autor
antes da implementação (SPEC 12g).
"""

import pytest

from src.domain.erros import TrechoInvalido
from src.domain.nomes import montar_caminho
from src.domain.trecho import Trecho, ler_tempo, montar_trecho

# ===========================================================================
# 1. Ler um tempo
# ===========================================================================


@pytest.mark.parametrize(
    "texto,segundos",
    [("90", 90), ("0", 0), ("1:30", 90), ("01:30", 90), ("1:02:03", 3723),
     ("  2:45 ", 165), ("10:00", 600), ("0:05", 5)],
)
def test_le_segundos_minutos_e_horas(texto, segundos):
    assert ler_tempo(texto) == segundos


@pytest.mark.parametrize(
    "texto",
    ["abc", "-5", "1:75", "1:-3", "1::30", "1:2:3:4", "1,5", "1.30", "1:30s", ":30", "1:"],
)
def test_recusa_o_que_nao_e_tempo(texto):
    with pytest.raises(TrechoInvalido):
        ler_tempo(texto)


def test_minuto_acima_de_59_so_vale_sem_hora():
    """'75:00' é natural de digitar para 1h15; '1:75:00' não é tempo."""
    assert ler_tempo("75:00") == 4500
    with pytest.raises(TrechoInvalido):
        ler_tempo("1:75:00")


# ===========================================================================
# 2 a 5. Montar o trecho contra a duração do vídeo
# ===========================================================================


def test_inicio_e_fim():
    assert montar_trecho("1:30", "2:45", 962) == Trecho(90, 165)


def test_inicio_vazio_e_o_comeco():
    assert montar_trecho("", "2:45", 962) == Trecho(0, 165)
    assert montar_trecho(None, "2:45", 962) == Trecho(0, 165)


def test_fim_vazio_e_o_final_do_video():
    assert montar_trecho("15:00", "", 962) == Trecho(900, 962)


def test_os_dois_vazios_e_o_video_inteiro():
    assert montar_trecho("", "", 962) is None
    assert montar_trecho(None, None, 962) is None


def test_trecho_que_cobre_o_video_todo_vira_inteiro():
    """Do 0 ao fim é o vídeo inteiro: baixar como trecho só daria um nome
    estranho e escaparia do aviso de 'já baixado'."""
    assert montar_trecho("0", "16:02", 962) is None


@pytest.mark.parametrize("inicio,fim", [("2:45", "1:30"), ("1:30", "1:30")])
def test_inicio_tem_que_vir_antes_do_fim(inicio, fim):
    with pytest.raises(TrechoInvalido, match="antes"):
        montar_trecho(inicio, fim, 962)


def test_fim_depois_da_duracao_diz_a_duracao():
    with pytest.raises(TrechoInvalido, match="16:02"):
        montar_trecho("1:00", "20:00", 962)


def test_inicio_depois_da_duracao_e_recusado():
    with pytest.raises(TrechoInvalido, match="16:02"):
        montar_trecho("17:00", "", 962)


def test_sem_duracao_conhecida_exige_o_fim():
    """Sem a duração não dá para saber onde é 'o final'."""
    assert montar_trecho("1:00", "2:00", None) == Trecho(60, 120)
    with pytest.raises(TrechoInvalido, match="fim"):
        montar_trecho("1:00", "", None)


def test_tempo_invalido_diz_qual_campo():
    with pytest.raises(TrechoInvalido, match="[Ii]nício"):
        montar_trecho("abc", "2:00", 962)
    with pytest.raises(TrechoInvalido, match="[Ff]im"):
        montar_trecho("1:00", "xyz", 962)


# ===========================================================================
# Rótulos
# ===========================================================================


def test_duracao_e_rotulos():
    t = Trecho(90, 165)
    assert t.duracao_s == 75
    assert t.para_nome() == "trecho 1m30s-2m45s"
    assert t.para_tela() == "1:30–2:45"
    assert Trecho(3723, 3730).para_nome() == "trecho 1h02m03s-1h02m10s"


def test_ida_e_volta_pelo_texto_do_historico():
    assert Trecho.de_texto(Trecho(90, 165).para_texto()) == Trecho(90, 165)
    assert Trecho.de_texto(None) is None


# ===========================================================================
# 6. Nome do arquivo
# ===========================================================================


def test_nome_do_arquivo_marca_o_trecho():
    montado = montar_caminho("D:/F", "Zelda", "wuFfiTEr2yc", "20260908", ".mp4",
                             sufixo=Trecho(90, 165).para_nome())
    assert montado.caminho == "D:/F/20260908 - Zelda [wuFfiTEr2yc] trecho 1m30s-2m45s.mp4"


def test_sufixo_do_trecho_entra_no_orcamento_do_caminho():
    """O sufixo custa caracteres: o título encolhe, o caminho não estoura."""
    titulo = "x" * 300
    com = montar_caminho("D:/F", titulo, "wuFfiTEr2yc", "20260908", ".mp4",
                         sufixo=Trecho(3723, 3730).para_nome())
    sem = montar_caminho("D:/F", titulo, "wuFfiTEr2yc", "20260908", ".mp4")
    assert len(com.caminho) == len(sem.caminho)
    assert com.caminho.endswith("trecho 1h02m03s-1h02m10s.mp4")
