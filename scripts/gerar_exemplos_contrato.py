"""Gera os exemplos do CONTRATO-API.md a partir do spike_meta.json.

Sobe um Pipeline DE VERDADE (domínio, fila, worker, histórico em pasta
temporária) atrás da API DE VERDADE, e bate em cada endpoint com o
TestClient. O único dublê é o downloader: os METADADOS são os reais do
spike_meta.json; o download em si é simulado (progresso e tamanho são
ilustrativos), porque nada aqui toca a rede.

A pasta temporária aparece na saída como D:/FOOTAGE, para o exemplo ficar
legível.

Uso:
    python scripts/gerar_exemplos_contrato.py > exemplos.md
"""

import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.stdout.reconfigure(encoding="utf-8")

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.download.ffmpeg import StatusFFmpeg  # noqa: E402
from src.pipeline import Pipeline  # noqa: E402
from src.web.app import criar_app  # noqa: E402

INFO = json.loads((RAIZ / "spike_meta.json").read_text(encoding="utf-8"))
URL_REAL = INFO["original_url"]
URL_VIMEO = "https://vimeo.com/123456789"


class DownloaderDeExemplo:
    """Metadados reais para o YouTube; um Vimeo mínimo e simulado; download
    simulado com progresso de dois streams, como o yt-dlp reporta."""

    def __init__(self):
        self.entrou = threading.Event()
        self.liberar = threading.Event()

    def inspecionar(self, url):
        if "vimeo" in url:
            return {
                "id": "123456789",
                "extractor_key": "Vimeo",
                "webpage_url": url,
                "title": "Exemplo simulado de outro site",
                "uploader": "alguem",
                "duration": 120,
                "formats": [],
            }
        return dict(INFO)

    def baixar(self, url, opcoes, ao_progredir):
        ao_progredir(
            {
                "status": "downloading",
                "downloaded_bytes": 3_145_728,
                "total_bytes": 8_388_608,
                "speed": 2_621_440.0,
                "eta": 2,
                "info_dict": {"format_id": "137"},
            }
        )
        ao_progredir(
            {
                "status": "downloading",
                "downloaded_bytes": 524_288,
                "total_bytes": 1_048_576,
                "speed": 655_360.0,
                "eta": 1,
                "info_dict": {"format_id": "140"},
            }
        )
        self.entrou.set()
        self.liberar.wait(10)
        # O yt-dlp emite 'finished' POR STREAM (o merge é pós-processamento,
        # sem hook de progresso). O stream de vídeo traz width/height.
        ao_progredir(
            {
                "status": "finished",
                "downloaded_bytes": 8_388_608,
                "total_bytes": 8_388_608,
                "info_dict": {"format_id": "137", "width": 1080, "height": 1920},
            }
        )
        ao_progredir(
            {
                "status": "finished",
                "downloaded_bytes": 1_048_576,
                "total_bytes": 1_048_576,
                "info_dict": {"format_id": "140"},
            }
        )
        destino = Path(opcoes["outtmpl"])
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"\0" * 9_437_184)
        return str(destino)


def bloco(titulo, resposta, truncar_formatos=True):
    corpo = resposta.json()
    if truncar_formatos:
        corpo = _truncar(corpo)
    texto = json.dumps(corpo, ensure_ascii=False, indent=2)
    print(f"### {titulo}\n\n`{resposta.status_code}`\n\n```json\n{texto}\n```\n")


def _truncar(obj):
    """Os 41 formatos do fixture não cabem num exemplo: mostra 3 e avisa."""
    if isinstance(obj, dict):
        if "formatos" in obj and isinstance(obj["formatos"], list) and len(obj["formatos"]) > 3:
            total = len(obj["formatos"])
            obj = {
                **obj,
                "formatos": obj["formatos"][:3]
                + [f"... mais {total - 3} formatos omitidos neste exemplo ..."],
            }
        return {k: _truncar(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncar(x) for x in obj]
    return obj


def esperar(cliente, job_id, estados, espera=10.0):
    prazo = time.monotonic() + espera
    while time.monotonic() < prazo:
        for j in cliente.get("/api/fila").json()["jobs"]:
            if j["id"] == job_id and j["estado"] in estados:
                return j
        time.sleep(0.02)
    raise SystemExit(f"job {job_id} não chegou a {estados}")


def main():
    temp = Path(tempfile.mkdtemp(prefix="contrato-"))
    try:
        config = temp / "config"
        config.mkdir()
        shutil.copy(RAIZ / "config" / "perfis.yaml", config / "perfis.yaml")
        footage = temp / "FOOTAGE"
        (config / "projetos.yaml").write_text(
            yaml.safe_dump(
                {
                    "projetos": {
                        # as_posix(): a config real usa barras normais, como em D:/FOOTAGE/pessoal
                        "pessoal": {
                            "nome": "Canal pessoal",
                            "pasta": (footage / "pessoal").as_posix(),
                        },
                        "cliente_x": {
                            "nome": "Cliente X",
                            "pasta": (footage / "cliente_x").as_posix(),
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        dl = DownloaderDeExemplo()
        pipeline = Pipeline(
            config,
            temp / "data",
            downloader=dl,
            detectar_ffmpeg=lambda: StatusFFmpeg(
                ffmpeg="C:\\ffmpeg\\bin\\ffmpeg.exe", ffprobe="C:\\ffmpeg\\bin\\ffprobe.exe"
            ),
            # espiões: gerar exemplo não abre janela nenhuma
            abrir_no_explorador=lambda caminho: None,
            escolher_pasta=lambda: str(footage / "escolhida"),
        )
        app = criar_app(pipeline, pasta_web=RAIZ / "web")

        with TestClient(app, raise_server_exceptions=False) as c:
            bloco("GET /api/config", c.get("/api/config"))

            bloco(
                "POST /api/inspecionar — três links: real, outro site, lixo",
                c.post(
                    "/api/inspecionar", json={"links": f"{URL_REAL}\n{URL_VIMEO}\nnão é um link"}
                ),
            )

            r = c.post(
                "/api/fila",
                json={"urls": [URL_REAL], "perfil": "edicao_1080", "projeto": "pessoal"},
            )
            bloco("POST /api/fila", r)
            job_id = r.json()["ids"][0]

            dl.entrou.wait(10)
            bloco("GET /api/fila — durante o download", c.get("/api/fila"))

            bloco(
                "POST /api/fila — mesmo vídeo e perfil já na fila (409)",
                c.post(
                    "/api/fila",
                    json={"urls": [URL_REAL], "perfil": "edicao_1080", "projeto": "pessoal"},
                ),
            )
            bloco(
                "POST /api/fila — perfil inexistente (400)",
                c.post(
                    "/api/fila",
                    json={"urls": [URL_REAL], "perfil": "nao_existe", "projeto": "pessoal"},
                ),
            )
            bloco(
                "POST /api/fila — corpo malformado (422)",
                c.post("/api/fila", json={"urls": "isso deveria ser uma lista"}),
            )

            r2 = c.post(
                "/api/fila", json={"urls": [URL_REAL], "perfil": "so_audio", "projeto": "pessoal"}
            )
            segundo = r2.json()["ids"][0]
            bloco("DELETE /api/fila/{id} — job ainda na fila", c.delete(f"/api/fila/{segundo}"))
            bloco("DELETE /api/fila/{id} — job em andamento (409)", c.delete(f"/api/fila/{job_id}"))
            bloco("DELETE /api/fila/{id} — inexistente (404)", c.delete("/api/fila/nao-existe"))

            dl.liberar.set()
            esperar(c, job_id, {"concluido", "falhou"})
            bloco("GET /api/fila — depois: um concluído, um cancelado", c.get("/api/fila"))
            bloco("GET /api/historico", c.get("/api/historico"))
            bloco(
                "GET /api/historico?termo=selecao&projeto=pessoal",
                c.get("/api/historico", params={"termo": "selecao", "projeto": "pessoal"}),
            )
            bloco(
                "POST /api/inspecionar — depois de baixado: `baixados` preenchido",
                c.post("/api/inspecionar", json={"links": URL_REAL}),
            )
            bloco(
                "POST /api/fila — já baixado neste perfil (409)",
                c.post(
                    "/api/fila",
                    json={"urls": [URL_REAL], "perfil": "edicao_1080", "projeto": "pessoal"},
                ),
            )

            baixado = c.get("/api/historico").json()["registros"][0]["caminho"]
            bloco(
                "POST /api/abrir-pasta — arquivo dentro de um projeto",
                c.post("/api/abrir-pasta", json={"caminho": baixado}),
            )
            bloco(
                "POST /api/abrir-pasta — caminho fora dos projetos (400)",
                c.post("/api/abrir-pasta", json={"caminho": "C:\\Windows\\System32"}),
            )

            # --- projetos pela tela ---
            (footage / "escolhida").mkdir(parents=True, exist_ok=True)
            bloco(
                "POST /api/escolher-pasta — seletor nativo do sistema",
                c.post("/api/escolher-pasta"),
            )
            bloco(
                "POST /api/projetos — cadastra",
                c.post(
                    "/api/projetos",
                    json={
                        "nome": "cliente_novo",
                        "rotulo": "Cliente Novo",
                        "caminho": str(footage / "escolhida"),
                    },
                ),
            )
            bloco(
                "POST /api/projetos — pasta que não existe (400)",
                c.post(
                    "/api/projetos", json={"nome": "outro", "caminho": str(footage / "nao_existe")}
                ),
            )
            bloco(
                "POST /api/projetos — nome já usado (409)",
                c.post(
                    "/api/projetos",
                    json={"nome": "cliente_novo", "caminho": str(footage / "escolhida")},
                ),
            )
            bloco("GET /api/projetos", c.get("/api/projetos"))
            bloco("DELETE /api/projetos/{nome}", c.delete("/api/projetos/cliente_novo"))

            # --- destino já ocupado: sucesso com aviso (SPEC 9.3) ---
            # Na vida real este é uma corrida: o arquivo aparece entre a
            # resolução de colisão e a gravação. Para o exemplo ser
            # determinístico, desligamos a resolução de colisão só neste job —
            # o JSON abaixo continua vindo de execução real do pipeline.
            import src.pipeline as _pipe

            _resolver_original = _pipe.resolver_colisao
            _pipe.resolver_colisao = lambda caminho, existe: caminho
            try:
                r3 = c.post(
                    "/api/fila",
                    json={
                        "urls": [URL_REAL],
                        "perfil": "edicao_1080",
                        "projeto": "pessoal",
                        "forcar": True,
                    },
                )
                esperar(c, r3.json()["ids"][0], {"concluido", "falhou"})
            finally:
                _pipe.resolver_colisao = _resolver_original

            bloco(
                "GET /api/fila — job com `ja_existia`: o arquivo já estava no destino",
                c.get("/api/fila"),
            )
            bloco(
                "GET /api/historico — uma linha por tentativa, com `aviso`", c.get("/api/historico")
            )
        # --- reconciliação na subida: interrompido com arquivo no destino ---
        # Simula o programa fechando no meio de um download: uma tentativa fica
        # em `baixando` com o destino já gravado, e o arquivo parcial no disco.
        from src.domain.models import Video
        from src.storage.historico import Historico

        parcial = footage / "pessoal" / "parcial-de-um-download-interrompido.mp4"
        parcial.parent.mkdir(parents=True, exist_ok=True)
        parcial.write_bytes(b"\0" * 3_145_728)

        h = Historico(temp / "data" / "historico.db")
        h.criar_schema()
        reg = h.iniciar(
            Video.de_info_dict(INFO), perfil="edicao_4k", projeto="pessoal", url_original=URL_REAL
        )
        h.registrar_destino(reg.id, str(parcial))
        h.fechar()

        pipeline2 = Pipeline(
            config,
            temp / "data",
            downloader=DownloaderDeExemplo(),
            detectar_ffmpeg=lambda: StatusFFmpeg(
                ffmpeg="C:\\ffmpeg\\bin\\ffmpeg.exe", ffprobe="C:\\ffmpeg\\bin\\ffprobe.exe"
            ),
        )
        app2 = criar_app(pipeline2, pasta_web=RAIZ / "web")
        with TestClient(app2, raise_server_exceptions=False) as c2:
            bloco(
                "GET /api/historico — na subida seguinte: `interrompido` com aviso",
                c2.get("/api/historico", params={"limite": 2}),
            )
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    import io

    buffer = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buffer
    try:
        main()
    finally:
        sys.stdout = real_stdout
    saida = buffer.getvalue()
    # A pasta temporária vira D:/FOOTAGE no texto, para o exemplo ser legível.
    import re

    # Caminhos devolvidos pelo download vêm com barra invertida (nativo do
    # Windows); os da config vêm como escritos. O exemplo preserva a diferença.
    saida = re.sub(
        r"[A-Za-z]:\\\\(?:[^\"\\\\]+\\\\)*contrato-[A-Za-z0-9_]+\\\\FOOTAGE",
        lambda m: "D:" + chr(92) * 2 + "FOOTAGE",
        saida,
    )
    saida = re.sub(r"[A-Za-z]:/(?:[^\"/]+/)*contrato-[A-Za-z0-9_]+/FOOTAGE", "D:/FOOTAGE", saida)
    print(saida)
