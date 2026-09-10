"""Histórico persistente em SQLite. SPEC 9 e 10.1.

Uma conexão, um lock. O worker (T5) grava enquanto a web (T6) lê; uma
conexão SQLite não pode ser compartilhada entre threads sem serialização,
então cada operação pública adquire o lock.

**Uma linha por TENTATIVA de download.** A versão anterior tinha chave única
em (extractor, video_id, perfil) e fazia upsert: um re-download apagava o
caminho do arquivo anterior, que continuava no disco sem registro. Cada
arquivo baixado tem a sua linha, e as operações de desfecho identificam a
tentativa pelo `id`.

Ticket: T4 (revisto na etapa 2, decisão 3).
"""

import re
import sqlite3
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

from ..domain.models import Video

SCHEMA = Path(__file__).with_name("schema.sql")

AVISO_JA_EXISTIA = "O arquivo já existia no destino; o download foi pulado e nada foi sobrescrito."


class RegistroNaoEncontrado(Exception):
    """Operação sobre um id de tentativa que não existe."""


@dataclass(frozen=True)
class RegistroHistorico:
    """Uma linha da tabela, espelhando o schema.sql."""

    id: int
    extractor: str
    video_id: str
    perfil: str
    url_original: str
    url_canonica: str
    titulo: str
    canal: str | None
    duracao_s: int | None
    projeto: str
    caminho: str | None
    tamanho_bytes: int | None
    resolucao: str | None
    status: str
    ja_existia: bool
    aviso: str | None
    motivo_falha: str | None
    mensagem_falha: str | None
    criado_em: str
    concluido_em: str | None


_COLUNAS = [f.name for f in fields(RegistroHistorico)]


def _agora_utc() -> str:
    """ISO-8601 em UTC. TEXT que ordena corretamente como texto (SPEC 9.1)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalizar_busca(texto: str) -> str:
    """Minúsculas, sem acento, espaços colapsados.

    Aplicado ao gravar (coluna titulo_busca) e ao buscar, para 'selecao',
    'SELEÇÃO' e 'Seleção' serem a mesma coisa. O LIKE do SQLite só ignora caixa
    em ASCII: 'Ç' não casa com 'ç'.
    """
    if not texto:
        return ""
    decomposto = unicodedata.normalize("NFD", texto)
    sem_acento = "".join(c for c in decomposto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sem_acento).strip().casefold()


def _escapar_like(texto: str) -> str:
    """'%' e '_' são curingas do LIKE. Um termo com eles não pode virar 'tudo'."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _juntar_avisos(atual: str | None, novo: str) -> str:
    """Acumula sem duplicar: perder um aviso é o mesmo problema de perder um
    registro."""
    partes = [p for p in (atual or "").split(" | ") if p]
    if novo not in partes:
        partes.append(novo)
    return " | ".join(partes)


class Historico:
    """Uma conexão, um lock. O worker grava enquanto a web lê."""

    def __init__(self, caminho_db: Path | str, agora: Callable[[], str] | None = None):
        self._caminho = Path(caminho_db)
        self._agora = agora or _agora_utc
        self._lock = threading.RLock()
        self._fechado = False

        # data/ pode não existir na primeira execução.
        self._caminho.parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False porque a conexão é usada pelo worker e pela
        # web; a serialização é o lock desta classe, não o do sqlite3.
        self._con = sqlite3.connect(str(self._caminho), check_same_thread=False)
        self._con.row_factory = sqlite3.Row

    # ------------------------------------------------------------------ infra

    def _exigir_aberto(self) -> None:
        if self._fechado:
            raise RuntimeError("Histórico já fechado; abra uma nova instância.")

    def _executar(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            self._exigir_aberto()
            cursor = self._con.execute(sql, params)
            self._con.commit()
            return cursor

    @staticmethod
    def _linha(row: sqlite3.Row | None) -> RegistroHistorico | None:
        if row is None:
            return None
        dados = {c: row[c] for c in _COLUNAS}
        dados["ja_existia"] = bool(dados["ja_existia"])
        return RegistroHistorico(**dados)

    def criar_schema(self) -> None:
        """Executa schema.sql. Idempotente (CREATE ... IF NOT EXISTS)."""
        with self._lock:
            self._exigir_aberto()
            self._con.executescript(SCHEMA.read_text(encoding="utf-8"))
            self._con.commit()

    def fechar(self) -> None:
        with self._lock:
            if not self._fechado:
                self._con.close()
                self._fechado = True

    def _atualizar(self, registro_id: int, sql: str, params: tuple) -> RegistroHistorico:
        with self._lock:
            cursor = self._executar(sql, params)
            if cursor.rowcount == 0:
                raise RegistroNaoEncontrado(f"tentativa {registro_id} não existe")
            return self.obter_por_id(registro_id)

    # --------------------------------------------------------------- escrita

    def iniciar(
        self, video: Video, *, perfil: str, projeto: str, url_original: str
    ) -> RegistroHistorico:
        """Grava uma NOVA linha `baixando` e devolve o registro criado.

        Nunca substitui uma tentativa anterior: o registro do arquivo que já
        está no disco continua lá.
        """
        with self._lock:
            cursor = self._executar(
                """
                INSERT INTO historico (
                    extractor, video_id, perfil, url_original, url_canonica,
                    titulo, titulo_busca, canal, duracao_s, projeto,
                    caminho, tamanho_bytes, resolucao, status, ja_existia,
                    aviso, motivo_falha, mensagem_falha, criado_em, concluido_em
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          NULL, NULL, NULL, 'baixando', 0, NULL, NULL, NULL, ?, NULL)
                """,
                (
                    video.extractor,
                    video.video_id,
                    perfil,
                    url_original,
                    video.url_canonica,
                    video.titulo,
                    normalizar_busca(video.titulo),
                    video.canal,
                    video.duracao_s,
                    projeto,
                    self._agora(),
                ),
            )
            return self.obter_por_id(cursor.lastrowid)

    def registrar_destino(self, registro_id: int, caminho: str) -> RegistroHistorico:
        """Grava o caminho PRETENDIDO, antes do download.

        Sem isto, um registro `interrompido` não tem onde ser procurado na
        subida seguinte (decisão 5). Não muda o status.
        """
        return self._atualizar(
            registro_id,
            "UPDATE historico SET caminho = ? WHERE id = ?",
            (caminho, registro_id),
        )

    def concluir(
        self,
        registro_id: int,
        *,
        caminho: str,
        tamanho_bytes: int | None,
        resolucao: str | None = None,
        ja_existia: bool = False,
    ) -> RegistroHistorico:
        """`ja_existia` marca que o arquivo já estava no destino e o download
        foi pulado — sucesso, mas com aviso (decisão 1)."""
        with self._lock:
            atual = self.obter_por_id(registro_id)
            aviso = atual.aviso if atual else None
            if ja_existia:
                aviso = _juntar_avisos(aviso, AVISO_JA_EXISTIA)
            return self._atualizar(
                registro_id,
                """
                UPDATE historico
                   SET status = 'concluido', caminho = ?, tamanho_bytes = ?,
                       resolucao = ?, ja_existia = ?, aviso = ?, concluido_em = ?
                 WHERE id = ?
                """,
                (
                    caminho,
                    tamanho_bytes,
                    resolucao,
                    int(ja_existia),
                    aviso,
                    self._agora(),
                    registro_id,
                ),
            )

    def falhar(self, registro_id: int, *, motivo: str, mensagem: str) -> RegistroHistorico:
        """Caminho volta a NULL: um caminho preenchido com status diferente de
        concluido apontaria para um arquivo que esta tentativa não produziu."""
        return self._atualizar(
            registro_id,
            """
            UPDATE historico
               SET status = 'falhou', caminho = NULL, tamanho_bytes = NULL,
                   resolucao = NULL, motivo_falha = ?, mensagem_falha = ?,
                   concluido_em = ?
             WHERE id = ?
            """,
            (motivo, mensagem, self._agora(), registro_id),
        )

    def avisar(self, registro_id: int, texto: str) -> RegistroHistorico:
        """Acrescenta um aviso não-bloqueante, sem mudar o status."""
        with self._lock:
            atual = self.obter_por_id(registro_id)
            if atual is None:
                raise RegistroNaoEncontrado(f"tentativa {registro_id} não existe")
            return self._atualizar(
                registro_id,
                "UPDATE historico SET aviso = ? WHERE id = ?",
                (_juntar_avisos(atual.aviso, texto), registro_id),
            )

    def marcar_interrompidos(self) -> list[RegistroHistorico]:
        """Na subida: toda tentativa `baixando` vira `interrompido`.

        É o que impede um job morto no meio de ser lido como concluído
        (SPEC 10.1). Devolve os registros marcados — com o `caminho`
        pretendido, para o pipeline verificar se há arquivo no destino.
        """
        with self._lock:
            ids = [
                row["id"]
                for row in self._executar("SELECT id FROM historico WHERE status = 'baixando'")
            ]
            if not ids:
                return []
            self._executar(
                f"UPDATE historico SET status = 'interrompido', concluido_em = ? "
                f"WHERE id IN ({','.join('?' * len(ids))})",
                (self._agora(), *ids),
            )
            return [self.obter_por_id(i) for i in ids]

    # --------------------------------------------------------------- leitura

    def obter_por_id(self, registro_id: int) -> RegistroHistorico | None:
        cursor = self._executar("SELECT * FROM historico WHERE id = ?", (registro_id,))
        return self._linha(cursor.fetchone())

    def ja_baixado(self, extractor: str, video_id: str, perfil: str) -> RegistroHistorico | None:
        """A tentativa CONCLUÍDA mais recente da tripla, ou None.

        Alimenta o aviso de duplicata. Uma falha posterior não apaga o
        arquivo que está no disco, então continua devolvendo a conclusão
        anterior.
        """
        cursor = self._executar(
            """
            SELECT * FROM historico
             WHERE extractor = ? AND video_id = ? AND perfil = ? AND status = 'concluido'
             ORDER BY criado_em DESC, id DESC LIMIT 1
            """,
            (extractor, video_id, perfil),
        )
        return self._linha(cursor.fetchone())

    def buscar(
        self, termo: str | None = None, projeto: str | None = None, limite: int = 100
    ) -> list[RegistroHistorico]:
        """Mais recente primeiro. Termo casa por substring no título
        normalizado; projeto casa por igualdade."""
        condicoes: list[str] = []
        params: list = []

        termo_normalizado = normalizar_busca(termo or "")
        if termo_normalizado:
            condicoes.append("titulo_busca LIKE ? ESCAPE '\\'")
            params.append(f"%{_escapar_like(termo_normalizado)}%")
        if projeto:
            condicoes.append("projeto = ?")
            params.append(projeto)

        sql = "SELECT * FROM historico"
        if condicoes:
            sql += " WHERE " + " AND ".join(condicoes)
        sql += " ORDER BY criado_em DESC, id DESC LIMIT ?"
        params.append(int(limite))

        cursor = self._executar(sql, tuple(params))
        return [self._linha(row) for row in cursor.fetchall()]
