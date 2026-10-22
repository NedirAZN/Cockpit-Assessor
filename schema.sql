-- Cockpit BFR — esquema do banco
-- Roda sozinho no start da aplicação. É idempotente: pode rodar quantas vezes quiser.

-- Carteiras: uma linha por conta, sempre no estado mais recente.
-- O objeto completo do XPerformance fica em `dados` (jsonb), sem perda.
CREATE TABLE IF NOT EXISTS carteiras (
  conta          TEXT PRIMARY KEY,
  ref            DATE,
  assessor       TEXT,
  segmento       TEXT,
  patrimonio     NUMERIC(16,2),
  dados          JSONB NOT NULL,
  atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS carteiras_ref_idx        ON carteiras (ref);
CREATE INDEX IF NOT EXISTS carteiras_patrimonio_idx ON carteiras (patrimonio DESC);

-- Histórico: uma linha por conta por lote. É a série mensal que o arquivo
-- único nunca guardou. Nunca sofre update.
CREATE TABLE IF NOT EXISTS historico (
  id          BIGSERIAL PRIMARY KEY,
  conta       TEXT NOT NULL,
  ref         DATE NOT NULL,
  patrimonio  NUMERIC(16,2),
  rent_mes    NUMERIC(10,4),
  ganho_mes   NUMERIC(16,2),
  rent_24m    NUMERIC(10,4),
  criado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (conta, ref)
);
CREATE INDEX IF NOT EXISTS historico_conta_idx ON historico (conta, ref);

-- Lotes: um registro por importação, com o relatório de divergências.
CREATE TABLE IF NOT EXISTS lotes (
  id         BIGSERIAL PRIMARY KEY,
  ref        DATE,
  arquivos   INTEGER,
  contas     INTEGER,
  novas      INTEGER,
  ausentes   INTEGER,
  aum        NUMERIC(16,2),
  status     TEXT NOT NULL DEFAULT 'ok',
  relatorio  JSONB,
  criado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Estado: substitui o localStorage. Cada chave bfr_* vira uma linha,
-- compartilhada entre todos os dispositivos.
CREATE TABLE IF NOT EXISTS estado (
  chave          TEXT PRIMARY KEY,
  valor          TEXT,
  atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Uploads: PDFs do XPerformance e imagens, no próprio banco. Evita depender de
-- volume, que no Railway monta em um serviço só.
CREATE TABLE IF NOT EXISTS uploads (
  id           BIGSERIAL PRIMARY KEY,
  nome         TEXT NOT NULL,
  tipo         TEXT,
  tamanho      INTEGER,
  conteudo     BYTEA NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pendente',
  erro         TEXT,
  lote_id      BIGINT REFERENCES lotes(id),
  criado_em    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS uploads_status_idx ON uploads (status, criado_em);

-- Backups: snapshot diário de carteiras + estado, retenção de 30 dias.
CREATE TABLE IF NOT EXISTS backups (
  id         BIGSERIAL PRIMARY KEY,
  conteudo   JSONB NOT NULL,
  contas     INTEGER,
  criado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Gatilhos: as regras que viram etiqueta no Radar e aviso na fila.
-- Moram no banco, não no navegador, senão você cria no PC e não vê no celular.
--
-- origem = 'fabrica' são as 16 regras que já vinham escritas no cockpit. A linha
-- aqui guarda só o que você mudou nelas (ligar/desligar, limite, texto); apagar
-- a linha devolve o comportamento original. origem = 'propria' são as suas.
CREATE TABLE IF NOT EXISTS gatilhos (
  id          BIGSERIAL PRIMARY KEY,
  nome        TEXT NOT NULL,
  tipo        TEXT NOT NULL DEFAULT 'carteira',   -- carteira | preco | agenda
  origem      TEXT NOT NULL DEFAULT 'propria',    -- fabrica | propria
  tag         TEXT,                               -- etiqueta de fábrica que esta linha sobrescreve
  ativo       BOOLEAN NOT NULL DEFAULT true,
  cor         TEXT NOT NULL DEFAULT 'gold',       -- gold | sky | red | mut
  texto       TEXT,                               -- o texto de conversa
  condicao    JSONB NOT NULL DEFAULT '{}'::jsonb,
  aplica      JSONB NOT NULL DEFAULT '{"escopo":"todos"}'::jsonb,
  produtos    JSONB NOT NULL DEFAULT '[]'::jsonb,
  ordem       INTEGER NOT NULL DEFAULT 100,
  criado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
  alterado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS gatilhos_tag_idx ON gatilhos (tag) WHERE tag IS NOT NULL;

-- Avisos: a fila do que disparou. Preenchida pelo cron e pela própria tela.
CREATE TABLE IF NOT EXISTS avisos (
  id          BIGSERIAL PRIMARY KEY,
  gatilho_id  BIGINT REFERENCES gatilhos(id) ON DELETE CASCADE,
  conta       TEXT,
  titulo      TEXT NOT NULL,
  detalhe     TEXT,
  valor       NUMERIC(16,2),
  situacao    TEXT NOT NULL DEFAULT 'aberto',     -- aberto | adiado | resolvido
  adiado_ate  DATE,
  criado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS avisos_situacao_idx ON avisos (situacao, criado_em DESC);

-- Consultas salvas da Lista de Atuação. Não são gatilhos: são perguntas que
-- você repete ("quem tem fundo de liquidez") e não quer remontar toda vez.
CREATE TABLE IF NOT EXISTS consultas (
  id         BIGSERIAL PRIMARY KEY,
  nome       TEXT NOT NULL,
  condicoes  JSONB NOT NULL DEFAULT '[]'::jsonb,
  acao       JSONB NOT NULL DEFAULT '{}'::jsonb,
  criado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);
