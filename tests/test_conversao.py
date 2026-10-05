"""Conversão pós-download para ProRes 422 HQ (src/download/conversao.py).

A maioria dos testes usa um Popen falso: a suíte não pode depender de um ffmpeg
instalado. Um teste roda o ffmpeg DE VERDADE sobre um clipe sintético gerado na
hora — sem rede — e é pulado se não houver ffmpeg no PATH.
"""

import io
import json
import shutil
import subprocess

import pytest

from src.domain.erros import PerfilInvalido
from src.domain.perfis import CONVERSOES, caminho_convertido, validar_perfil
from src.download.conversao import (
    ErroDeConversao,
    converter,
    montar_comando,
    segundos_processados,
)

PRORES = CONVERSOES["prores_422_hq"]


class PopenFalso:
    """Imita o subprocess.Popen: roteiriza stdout, stderr, código e o arquivo."""

    def __init__(self, linhas=(), codigo=0, stderr="", cria_arquivo=True):
        self.linhas, self.codigo, self.stderr_txt = list(linhas), codigo, stderr
        self.cria_arquivo = cria_arquivo
        self.comando = None

    def __call__(self, comando, **kw):
        self.comando = comando
        if self.cria_arquivo:
            with open(comando[-1], "wb") as f:
                f.write(b"mov parcial ou completo")
        self.stdout = iter(self.linhas)
        self.stderr = io.StringIO(self.stderr_txt)
        return self

    def wait(self):
        return self.codigo


# ===========================================================================
# A linha de comando
# ===========================================================================


def test_comando_e_prores_422_hq_com_pcm_24():
    cmd = montar_comando("ffmpeg", "in.mkv", "out.mov", PRORES)
    texto = " ".join(cmd)
    assert "-c:v prores_ks" in texto
    assert "-profile:v 3" in texto, "profile 3 do prores_ks é o 422 HQ"
    assert "-pix_fmt yuv422p10le" in texto
    assert "-c:a pcm_s24le" in texto
    assert cmd[-1] == "out.mov"


def test_comando_nunca_sobrescreve():
    cmd = montar_comando("ffmpeg", "in.mkv", "out.mov", PRORES)
    assert "-n" in cmd and "-y" not in cmd


def test_audio_e_opcional_no_mapeamento():
    """Vídeo sem trilha de áudio converte em vez de falhar."""
    assert "0:a:0?" in montar_comando("ffmpeg", "in.mkv", "out.mov", PRORES)


def test_caminho_com_espaco_e_acento_vai_inteiro():
    """Lista, não shell: o caminho é UM argumento, sem aspas."""
    origem = "D:/VÍDEOS/REMO/VIDEO 1/COISAS DO VIDEO/a & b.mkv"
    assert origem in montar_comando("ffmpeg", origem, "o.mov", PRORES)


@pytest.mark.parametrize(
    "linha,esperado",
    [("out_time_us=2500000", 2.5), ("out_time_us=0", 0.0), ("out_time_ms=2500000", None),
     ("frame=10", None), ("out_time_us=N/A", None), ("out_time_us=-5", None), ("", None)],
)
def test_le_o_progresso_do_ffmpeg(linha, esperado):
    assert segundos_processados(linha) == esperado


def test_caminho_convertido_troca_so_a_extensao():
    assert caminho_convertido("D:/F/v [id].mkv", PRORES) == "D:/F/v [id].mov"
    assert caminho_convertido("D:/F/v.1.mkv", PRORES) == "D:/F/v.1.mov"


# ===========================================================================
# O que acontece com os arquivos
# ===========================================================================


def test_sucesso_devolve_o_destino_e_reporta_progresso(tmp_path):
    marcas = []
    falso = PopenFalso(linhas=["out_time_us=1000000\n", "progress=continue\n", "out_time_us=2000000\n"])
    final = converter("ffmpeg", str(tmp_path / "in.mkv"), str(tmp_path / "out.mov"),
                      PRORES, marcas.append, executar=falso)
    assert final == str(tmp_path / "out.mov")
    assert marcas == [1.0, 2.0]


def test_falha_apaga_o_mov_parcial(tmp_path):
    """Um .mov truncado que abre no Premiere é pior que nenhum."""
    falso = PopenFalso(codigo=1, stderr="Error while encoding\nConversion failed!")
    with pytest.raises(ErroDeConversao) as erro:
        converter("ffmpeg", "in.mkv", str(tmp_path / "out.mov"), PRORES, executar=falso)
    assert "Conversion failed" in str(erro.value)
    assert not (tmp_path / "out.mov").exists()


def test_destino_existente_e_recusado_sem_rodar_e_sem_apagar(tmp_path):
    """Medido: com -n e destino existente, o ffmpeg desta máquina sai com 0 SEM
    gravar. Sem a recusa prévia, o .mov antigo passaria por conversão nova e o
    histórico apontaria para um arquivo que não é este vídeo."""
    antigo = tmp_path / "out.mov"
    antigo.write_bytes(b"footage de outro dia")
    falso = PopenFalso()
    with pytest.raises(ErroDeConversao) as erro:
        converter("ffmpeg", "in.mkv", str(antigo), PRORES, executar=falso)
    assert "já existe" in str(erro.value)
    assert falso.comando is None, "nem chegou a chamar o ffmpeg"
    assert antigo.read_bytes() == b"footage de outro dia"


def test_progresso_que_levanta_nao_derruba_a_conversao(tmp_path):
    def explode(_):
        raise RuntimeError("tela fechou")
    falso = PopenFalso(linhas=["out_time_us=1000000\n"])
    assert converter("ffmpeg", "in.mkv", str(tmp_path / "o.mov"), PRORES, explode, executar=falso)


def test_ffmpeg_ausente_vira_erro_de_conversao(tmp_path):
    def sem_ffmpeg(*a, **k):
        raise FileNotFoundError("ffmpeg")
    with pytest.raises(ErroDeConversao):
        converter("ffmpeg", "in.mkv", str(tmp_path / "o.mov"), PRORES, executar=sem_ffmpeg)


def test_codigo_zero_sem_arquivo_e_erro(tmp_path):
    falso = PopenFalso(cria_arquivo=False)
    with pytest.raises(ErroDeConversao):
        converter("ffmpeg", "in.mkv", str(tmp_path / "o.mov"), PRORES, executar=falso)


# ===========================================================================
# O perfil no YAML
# ===========================================================================

BASE = {"format": "bv*+ba/b", "merge_output_format": "mkv", "limite_dimensao": None}


def test_conversao_desconhecida_e_recusada_na_carga():
    with pytest.raises(PerfilInvalido):
        validar_perfil("x", {**BASE, "conversao": "dnxhr_qualquer"})


def test_conversao_exige_ffmpeg():
    with pytest.raises(PerfilInvalido):
        validar_perfil("x", {**BASE, "conversao": "prores_422_hq", "exige_ffmpeg": False})


# ===========================================================================
# O ffmpeg de verdade — sem rede, clipe sintético
# ===========================================================================


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg não está no PATH")
def test_ffmpeg_real_gera_prores_hq_que_o_premiere_abre(tmp_path):
    """VP9 em .mkv, como o YouTube entrega 4K, e o resultado conferido pelo
    ffprobe: é isto que o Premiere vai ler. Pequeno de propósito (320x180,
    meio segundo): o que se prova é o formato, não o 4K."""
    origem = tmp_path / "origem.mkv"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=0.5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
         "-c:v", "libvpx-vp9", "-deadline", "realtime", "-c:a", "libopus", str(origem)],
        check=True,
    )
    final = converter("ffmpeg", str(origem), str(tmp_path / "saida.mov"), PRORES)
    sondagem = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,profile,pix_fmt",
         "-of", "json", final], capture_output=True, text=True, check=True).stdout)
    video, audio = sondagem["streams"]
    assert (video["codec_name"], video["profile"], video["pix_fmt"]) == ("prores", "HQ", "yuv422p10le")
    assert audio["codec_name"] == "pcm_s24le"
    assert origem.exists(), "o .mkv baixado fica"


# ===========================================================================
# Placa de vídeo primeiro, CPU se ela falhar
# ===========================================================================


class PopenSequencia:
    """Cada chamada usa o próximo roteiro: (código, cria_arquivo)."""

    def __init__(self, *roteiros):
        self.roteiros = list(roteiros)
        self.comandos = []

    def __call__(self, comando, **kw):
        self.comandos.append(comando)
        codigo, cria = self.roteiros.pop(0)
        if cria:
            with open(comando[-1], "wb") as f:
                f.write(b"mov")
        falso = PopenFalso(codigo=codigo, cria_arquivo=False, stderr="erro da gpu")
        falso.stdout, falso.stderr = iter([]), io.StringIO("erro da gpu")
        return falso


def test_comando_da_gpu_usa_vulkan_e_o_mesmo_prores_hq():
    texto = " ".join(montar_comando("ffmpeg", "in.mkv", "out.mov", PRORES, gpu=True))
    assert "-c:v prores_ks_vulkan" in texto
    assert "-profile:v 3" in texto, "o mesmo 422 HQ"
    assert "-init_hw_device vulkan=vk:0" in texto
    assert "format=yuv422p10le,hwupload" in texto
    assert "-pix_fmt" not in texto, "na GPU o formato vai pelo filtro, antes do upload"


def test_tenta_a_gpu_primeiro():
    falso = PopenSequencia((0, True))
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        converter("ffmpeg", "in.mkv", os.path.join(d, "o.mov"), PRORES, executar=falso)
    assert len(falso.comandos) == 1
    assert "prores_ks_vulkan" in falso.comandos[0]


def test_gpu_que_falha_cai_na_cpu_e_o_download_nao_se_perde(tmp_path):
    """Driver sem Vulkan, ffmpeg antigo sem o codificador: a CPU refaz."""
    falso = PopenSequencia((1, True), (0, True))
    final = converter("ffmpeg", "in.mkv", str(tmp_path / "o.mov"), PRORES, executar=falso)
    assert final == str(tmp_path / "o.mov")
    assert "prores_ks_vulkan" in falso.comandos[0]
    assert "prores_ks" in falso.comandos[1] and "prores_ks_vulkan" not in falso.comandos[1]


def test_falha_nas_duas_levanta_e_nao_deixa_parcial(tmp_path):
    falso = PopenSequencia((1, True), (1, True))
    with pytest.raises(ErroDeConversao):
        converter("ffmpeg", "in.mkv", str(tmp_path / "o.mov"), PRORES, executar=falso)
    assert not (tmp_path / "o.mov").exists()
