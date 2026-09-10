"""Linha de comando. Usa o MESMO pipeline.py que a web.

    python -m src.cli --perfil edicao_1080 --projeto cliente_x URL [URL...]
    python -m src.cli --perfil edicao_1080 --pasta "D:/FOOTAGE/avulsa" URL
    python -m src.cli --dry-run --perfil edicao_1080 --projeto cliente_x URL
    python -m src.cli --perfis
    python -m src.cli --projetos
    python -m src.cli --historico [TERMO]

Zero regra de negócio aqui: nomenclatura, perfis, dedução de duplicata e
tradução de erro vêm todas do pipeline, iguais às da web. O que este módulo
faz é ler argumentos, imprimir e devolver código de saída.

Código de saída: 0 se tudo deu certo, 1 se qualquer coisa falhou (SPEC 11.2,
PLAN T8).

Ticket: T8.
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from .pipeline import ErroDePedido, Pipeline

RAIZ = Path(__file__).resolve().parent.parent

INTERVALO_PROGRESSO = 0.2  # segundos entre leituras da fila
ESTADOS_VIVOS = {"na_fila", "baixando"}


# ===========================================================================
# Formatação — a API entrega valor cru, como para a web
# ===========================================================================


def fmt_duracao(segundos) -> str:
    """65 -> 1:05 ; 3725 -> 1:02:05 ; None -> --:--"""
    if segundos is None:
        return "--:--"
    segundos = int(round(segundos))
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas}:{minutos:02d}:{seg:02d}"
    return f"{minutos}:{seg:02d}"


def fmt_bytes(quantidade) -> str:
    """9437184 -> 9,0 MB. Vírgula decimal, como o resto da interface."""
    if quantidade is None:
        return "--"
    valor = float(quantidade)
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if valor < 1024 or unidade == "TB":
            casas = 0 if unidade == "B" else 1
            return f"{valor:.{casas}f}".replace(".", ",") + f" {unidade}"
        valor /= 1024
    return "--"


def fmt_data(iso: str | None) -> str:
    """ISO-8601 UTC -> data e hora local."""
    if not iso:
        return "--"
    try:
        momento = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if momento.tzinfo is not None:
        momento = momento.astimezone()
    return momento.strftime("%d/%m/%Y %H:%M")


def encurtar(texto: str, limite: int = 58) -> str:
    texto = " ".join(str(texto or "").split())
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


# ===========================================================================
# Listagens
# ===========================================================================


def largura_do_nome(itens: list[dict], minimo: int = 12) -> int:
    """A coluna acompanha o nome mais longo: um projeto chamado
    `cliente_exemplo` não pode desalinhar a lista inteira."""
    return max([minimo] + [len(i["nome"]) for i in itens])


def listar_perfis(config: dict, escrever) -> int:
    escrever("PERFIS — config/perfis.yaml\n")
    coluna = largura_do_nome(config["perfis"])
    for perfil in config["perfis"]:
        teto = (
            "qualquer" if perfil["limite_dimensao"] is None else f"até {perfil['limite_dimensao']}p"
        )
        marca = "" if perfil["disponivel"] else "   [indisponível: exige ffmpeg]"
        escrever(f"  {perfil['nome']:<{coluna}}  {perfil['descricao']}")
        escrever(f"  {'':<{coluna}}  {teto} · .{perfil['container']}{marca}\n")
    return 0


def listar_projetos(config: dict, escrever) -> int:
    escrever("PROJETOS — config/projetos.yaml\n")
    coluna = largura_do_nome(config["projetos"])
    for projeto in config["projetos"]:
        escrever(f"  {projeto['nome']:<{coluna}}  {projeto['rotulo']}")
        escrever(f"  {'':<{coluna}}  {projeto['pasta']}")
        if not projeto["valido"]:
            escrever(f"  {'':<{coluna}}  [inválido: {projeto['motivo']}]")
        elif not Path(projeto["pasta"]).exists():
            # SPEC 13.1 decisão 6: a pasta nasce no primeiro download.
            escrever(f"  {'':<{coluna}}  (ainda não existe; criada no primeiro download)")
        escrever("")
    return 0


def listar_historico(registros: list[dict], escrever, filtrou: bool = False) -> int:
    if not registros:
        escrever(
            "Nenhum registro para essa busca."
            if filtrou
            else "O histórico está vazio: nada foi baixado ainda."
        )
        return 0

    escrever(f"HISTÓRICO — {len(registros)} tentativa(s), mais recente primeiro\n")
    for reg in registros:
        selo = "já existia" if reg.get("ja_existia") else reg["status"]
        escrever(f"  [{selo}] {encurtar(reg['titulo'], 62)}")
        detalhe = " · ".join(
            filter(
                None,
                [
                    reg["perfil"],
                    reg["projeto"],
                    reg.get("resolucao") or None,
                    fmt_bytes(reg.get("tamanho_bytes")) if reg.get("tamanho_bytes") else None,
                    fmt_data(reg.get("concluido_em") or reg.get("criado_em")),
                ],
            )
        )
        escrever(f"      {detalhe}")
        if reg.get("caminho"):
            escrever(f"      {reg['caminho']}")
        if reg.get("mensagem_falha"):
            escrever(f"      falha: {reg['mensagem_falha']}")
        if reg.get("aviso"):
            escrever(f"      aviso: {reg['aviso']}")
        escrever("")
    return 0


# ===========================================================================
# Dry-run
# ===========================================================================


def mostrar_simulacao(itens: list[dict], projeto: str, escrever) -> int:
    """Mostra o que seria baixado e PARA ONDE, sem baixar nada."""
    escrever(f"DRY-RUN — nada será baixado (projeto {projeto})\n")

    problemas = 0
    for numero, item in enumerate(itens, 1):
        if not item["ok"]:
            problemas += 1
            escrever(f"  {numero}. [erro: {item['motivo']}] {item['original']}")
            escrever(f"      {item['erro']}\n")
            continue

        video = item["video"]
        escrever(f"  {numero}. {encurtar(video['titulo'], 62)}")
        escrever(
            f"      {video['canal'] or 'canal desconhecido'} · "
            f"{fmt_duracao(video['duracao_s'])} · {video['extractor']}"
        )
        escrever(f"      destino: {item['destino']}")
        if item["ja_baixado"]:
            ja = item["ja_baixado"]
            escrever(
                f"      já baixado neste perfil em {fmt_data(ja['concluido_em'])}: {ja['caminho']}"
            )
            escrever("      sem --forcar, este link é recusado com conflito")
        if item["aviso"]:
            escrever(f"      aviso: {item['aviso']}")
        escrever("")

    if problemas:
        # `enfileirar` é tudo ou nada (SPEC 11.1): um link ruim barra a lista
        # inteira. Dizer isso agora evita a surpresa de rodar sem --dry-run e
        # não baixar nada.
        escrever(
            f"{problemas} link(s) com problema. Numa execução de verdade "
            "nada seria enfileirado: a fila é tudo ou nada."
        )
    return 1 if problemas else 0


# ===========================================================================
# Download
# ===========================================================================


def acompanhar(pipeline, ids: list[str], escrever, tty: bool, dormir=time.sleep) -> list[dict]:
    """Segue a fila até todos terminarem e devolve os jobs finais.

    Lê o estado pela mesma `estado_fila()` que a web consulta por polling. O
    progresso nunca é impresso pelo hook do yt-dlp: ele roda em outra thread e
    dispara muitas vezes por segundo, e I/O ali derruba o download
    (RESEARCH 3.4).
    """
    restantes = set(ids)
    ultima_linha = ""

    while restantes:
        jobs = {j["id"]: j for j in pipeline.estado_fila() if j["id"] in restantes}
        if not jobs:
            break

        ativo = next((j for j in jobs.values() if j["estado"] == "baixando"), None)
        if ativo and tty:
            linha = linha_de_progresso(ativo, len(ids) - len(restantes) + 1, len(ids))
            if linha != ultima_linha:
                escrever("\r" + linha.ljust(len(ultima_linha)), fim="")
                ultima_linha = linha

        terminados = [j for j in jobs.values() if j["estado"] not in ESTADOS_VIVOS]
        for job in terminados:
            if ultima_linha:
                escrever("\r" + " " * len(ultima_linha) + "\r", fim="")
                ultima_linha = ""
            escrever(f"  {rotulo(job)} {encurtar(job['video']['titulo'])}")
            restantes.discard(job["id"])

        if restantes:
            dormir(INTERVALO_PROGRESSO)

    return [j for j in pipeline.estado_fila() if j["id"] in set(ids)]


def linha_de_progresso(job: dict, indice: int, total: int) -> str:
    progresso = job.get("progresso") or {}
    percentual = progresso.get("percentual")
    pedaco = "--%" if percentual is None else f"{round(percentual):>3d}%"
    velocidade = progresso.get("velocidade_bps")
    eta = progresso.get("eta_s")
    return (
        f"  [{indice}/{total}] {pedaco}  "
        f"{fmt_bytes(progresso.get('baixados'))} / {fmt_bytes(progresso.get('total'))}  "
        f"{fmt_bytes(velocidade) + '/s' if velocidade else '--'}  "
        f"restam {fmt_duracao(eta)}  {encurtar(job['video']['titulo'], 34)}"
    )


def rotulo(job: dict) -> str:
    """Texto, não cor: o terminal do usuário pode não ter cor nenhuma."""
    if job["estado"] == "concluido":
        return "[já existia]" if job["ja_existia"] else "[ok]        "
    if job["estado"] == "falhou":
        return "[falhou]    "
    if job["estado"] == "cancelado":
        return "[cancelado] "
    return f"[{job['estado']}]"


def relatorio(jobs: list[dict], escrever) -> int:
    """Sucesso, já existia e falha — e o caminho de cada arquivo."""
    concluidos = [j for j in jobs if j["estado"] == "concluido" and not j["ja_existia"]]
    ja_existiam = [j for j in jobs if j["estado"] == "concluido" and j["ja_existia"]]
    falharam = [j for j in jobs if j["estado"] == "falhou"]
    outros = [j for j in jobs if j["estado"] not in ("concluido", "falhou")]

    escrever("\nRESUMO")
    escrever(f"  baixados:    {len(concluidos)}")
    escrever(f"  já existiam: {len(ja_existiam)}")
    escrever(f"  falhas:      {len(falharam)}")
    if outros:
        escrever(f"  outros:      {len(outros)}")
    escrever("")

    for job in concluidos + ja_existiam:
        escrever(f"  {rotulo(job)} {encurtar(job['video']['titulo'])}")
        escrever(f"               {job['caminho_final']}")
        if job["aviso"]:
            escrever(f"               aviso: {job['aviso']}")

    for job in falharam:
        escrever(f"  {rotulo(job)} {encurtar(job['video']['titulo'])}")
        escrever(f"               {job['motivo_falha']}: {job['mensagem_falha']}")

    for job in outros:
        escrever(f"  {rotulo(job)} {encurtar(job['video']['titulo'])}")
        if job["aviso"]:
            escrever(f"               aviso: {job['aviso']}")

    return 1 if (falharam or outros) else 0


def baixar(
    pipeline,
    urls: list[str],
    perfil: str,
    projeto: str | None,
    forcar: bool,
    escrever,
    tty: bool,
    dormir=time.sleep,
    pasta: str | None = None,
) -> int:
    ids = pipeline.enfileirar(urls, perfil, projeto, forcar, pasta)
    plural = "link" if len(ids) == 1 else "links"
    onde = f"projeto {projeto}" if projeto else f"pasta avulsa {pasta}"
    escrever(f"{len(ids)} {plural} na fila — perfil {perfil}, {onde}.")
    escrever("Um download por vez, em sequência.\n")
    jobs = acompanhar(pipeline, ids, escrever, tty, dormir)
    return relatorio(jobs, escrever)


# ===========================================================================
# Argumentos
# ===========================================================================


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="Baixador de footage — a mesma engrenagem da interface web.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "exemplos:\n"
            "  python -m src.cli --perfil edicao_1080 --projeto cliente_x URL\n"
            "  python -m src.cli --dry-run --perfil edicao_1080 --projeto cliente_x URL\n"
            "  python -m src.cli --perfis\n"
            "  python -m src.cli --historico selecao\n"
        ),
    )
    parser.add_argument(
        "urls", nargs="*", metavar="URL", help="um ou mais links, separados por espaço"
    )
    parser.add_argument("--perfil", help="nome do perfil de qualidade")
    parser.add_argument("--projeto", help="nome do projeto de destino; também filtra o --historico")
    parser.add_argument(
        "--pasta",
        help="pasta de destino avulsa, usada só neste download "
        "e não cadastrada; alternativa ao --projeto",
    )
    parser.add_argument(
        "--forcar", action="store_true", help="baixa de novo um vídeo já concluído neste perfil"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="mostra o que seria baixado e para onde, sem baixar",
    )
    parser.add_argument("--perfis", action="store_true", help="lista os perfis do YAML e sai")
    parser.add_argument("--projetos", action="store_true", help="lista os projetos do YAML e sai")
    parser.add_argument(
        "--historico",
        nargs="?",
        const="",
        metavar="TERMO",
        help="consulta o histórico; TERMO busca no título",
    )
    parser.add_argument(
        "--limite", type=int, default=100, help="máximo de registros do --historico (padrão 100)"
    )
    return parser


def main(argv: list[str] | None = None, *, pipeline=None, escrever=None) -> int:
    """`pipeline` e `escrever` entram por injeção para o teste não subir a
    aplicação inteira nem depender de captura de stdout."""
    sys.stdout.reconfigure(encoding="utf-8")  # console cp1252 (RESEARCH 7.4)
    argumentos = construir_parser().parse_args(argv)

    if escrever is None:

        def escrever(texto="", fim="\n"):
            print(texto, end=fim, flush=True)

    consultas = argumentos.perfis or argumentos.projetos or argumentos.historico is not None
    if not consultas and not argumentos.urls:
        escrever(
            "Nada a fazer: informe pelo menos um link, ou use --perfis, --projetos ou --historico."
        )
        return 1
    if not consultas and not (argumentos.perfil and (argumentos.projeto or argumentos.pasta)):
        escrever(
            "Faltou --perfil, e --projeto ou --pasta. Veja as opções com --perfis e --projetos."
        )
        return 1

    proprio = pipeline is None
    if proprio:
        pipeline = Pipeline(RAIZ / "config", RAIZ / "data")
    try:
        return _executar(pipeline, argumentos, escrever)
    except ErroDePedido as erro:
        # A mesma tradução em português que a web mostra: a CLI não reescreve
        # mensagem de erro (SPEC 12).
        escrever(f"Erro: {erro}")
        return 1
    except KeyboardInterrupt:
        escrever(
            "\nInterrompido. O download em andamento continua registrado "
            "no histórico como interrompido."
        )
        return 1
    finally:
        if proprio:
            pipeline.encerrar()


def _executar(pipeline, argumentos, escrever) -> int:
    if argumentos.perfis:
        return listar_perfis(pipeline.config(), escrever)
    if argumentos.projetos:
        return listar_projetos(pipeline.config(), escrever)
    if argumentos.historico is not None:
        return listar_historico(
            pipeline.historico(argumentos.historico or None, argumentos.projeto, argumentos.limite),
            escrever,
            filtrou=bool(argumentos.historico or argumentos.projeto),
        )
    destino = argumentos.projeto or argumentos.pasta
    if argumentos.dry_run:
        return mostrar_simulacao(
            pipeline.simular(
                argumentos.urls, argumentos.perfil, argumentos.projeto, argumentos.pasta
            ),
            destino,
            escrever,
        )
    return baixar(
        pipeline,
        argumentos.urls,
        argumentos.perfil,
        argumentos.projeto,
        argumentos.forcar,
        escrever,
        sys.stdout.isatty(),
        pasta=argumentos.pasta,
    )


if __name__ == "__main__":
    sys.exit(main())
