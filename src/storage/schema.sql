-- Histórico de downloads. SPEC 9.
--
-- UMA LINHA POR TENTATIVA de download, não por (extractor, video_id, perfil).
--
-- A versão anterior tinha UNIQUE nessa tripla e fazia upsert destrutivo: um
-- re-download apagava o caminho do arquivo anterior, que continuava no disco
-- sem registro nenhum. Um smoke test flagrou 3 arquivos e 2 registros.
-- O histórico não pode mentir sobre onde o arquivo está, e footage não pode
-- sumir em silêncio — então cada arquivo baixado tem a sua linha.
--
-- `ja_baixado` consulta a tentativa CONCLUÍDA mais recente da tripla; é o
-- índice idx_historico_chave que sustenta essa consulta.
--
-- status: baixando | concluido | falhou | interrompido
--   `baixando` é gravado no INÍCIO. É o que permite, na subida seguinte,
--   marcar como `interrompido` o que estava rodando quando o programa fechou
--   (SPEC 10.1). `caminho` é preenchido já em `baixando` (registrar_destino),
--   para a reconciliação saber onde procurar o arquivo.
--
-- ja_existia: o arquivo já estava no destino e o download foi pulado. É
--   sucesso, mas o usuário precisa saber que não baixou.
--
-- aviso: texto não-bloqueante acumulado (arquivo já existia, histórico
--   indisponível, interrompido com arquivo no destino).
--
-- trecho: '90-165' (segundos) quando só um pedaço do vídeo foi baixado; NULL
--   é o vídeo inteiro. `ja_baixado` só olha o inteiro (SPEC 12g). Bancos
--   anteriores ganham a coluna em criar_schema(), sem perder nada.
--
-- titulo_busca: título normalizado (minúsculas, sem acento) para a busca.
--   O LIKE do SQLite só ignora caixa em ASCII: 'Ç' não casa com 'ç'.

CREATE TABLE IF NOT EXISTS historico (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    extractor       TEXT    NOT NULL,
    video_id        TEXT    NOT NULL,
    perfil          TEXT    NOT NULL,
    url_original    TEXT    NOT NULL,
    url_canonica    TEXT    NOT NULL,
    titulo          TEXT    NOT NULL,
    titulo_busca    TEXT    NOT NULL DEFAULT '',
    canal           TEXT,
    duracao_s       INTEGER,
    projeto         TEXT    NOT NULL,
    trecho          TEXT,
    caminho         TEXT,
    tamanho_bytes   INTEGER,
    resolucao       TEXT,
    status          TEXT    NOT NULL,
    ja_existia      INTEGER NOT NULL DEFAULT 0,
    aviso           TEXT,
    motivo_falha    TEXT,
    mensagem_falha  TEXT,
    criado_em       TEXT    NOT NULL,
    concluido_em    TEXT
);

CREATE INDEX IF NOT EXISTS idx_historico_chave   ON historico (extractor, video_id, perfil, criado_em DESC);
CREATE INDEX IF NOT EXISTS idx_historico_busca   ON historico (titulo_busca);
CREATE INDEX IF NOT EXISTS idx_historico_projeto ON historico (projeto);
CREATE INDEX IF NOT EXISTS idx_historico_criado  ON historico (criado_em DESC);
