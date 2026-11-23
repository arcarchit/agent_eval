// SQLite persistence (Node's built-in node:sqlite, so there is no native dependency).
// Three tables: configs (immutable, versioned), experiments, trials (one row per task × config × repeat).
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export const DB_PATH = process.env.DB_PATH ?? '../data/evals.db';

export function openDb(path = DB_PATH): DatabaseSync {
  if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true });
  const db = new DatabaseSync(path);
  db.exec(`
    PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS configs (
      id TEXT PRIMARY KEY,            -- "<name>@v<version>", never changes once written
      name TEXT NOT NULL,
      version INTEGER NOT NULL,
      spec TEXT NOT NULL,             -- JSON: harness, model, skills, memory, tools, temperature, budgets
      spec_hash TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE (name, version)
    );
    CREATE TABLE IF NOT EXISTS experiments (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      config_ids TEXT NOT NULL,       -- JSON array
      tasks TEXT NOT NULL,            -- JSON snapshot of every task, so a rerun uses identical tasks
      trials_per_task INTEGER NOT NULL,
      status TEXT NOT NULL,           -- running | done
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS trials (
      id TEXT PRIMARY KEY,
      experiment_id TEXT NOT NULL,
      config_id TEXT NOT NULL,
      task_id TEXT NOT NULL,
      trial_index INTEGER NOT NULL,
      status TEXT NOT NULL,           -- pending | running | done | error | cancelled
      attempts INTEGER NOT NULL DEFAULT 0,
      passed INTEGER, score REAL, failure_category TEXT,
      grader TEXT, trace TEXT, error TEXT,
      input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, judge_cost_usd REAL, latency_ms INTEGER,
      finished_at TEXT,
      llm_calls INTEGER,              -- model calls in the trial (all sessions)
      turns INTEGER,                  -- user turns (sessions)
      UNIQUE (experiment_id, config_id, task_id, trial_index)
    );
  `);
  migrate(db);
  return db;
}

export const now = () => new Date().toISOString();

/** Add columns introduced after a database was created, and backfill them from stored traces. */
function migrate(db: DatabaseSync) {
  const cols = (db.prepare(`PRAGMA table_info(trials)`).all() as { name: string }[]).map((c) => c.name);
  for (const col of ['llm_calls', 'turns']) {
    if (!cols.includes(col)) db.exec(`ALTER TABLE trials ADD COLUMN ${col} INTEGER`);
  }
  const count = (type: string) =>
    `(SELECT COUNT(*) FROM json_each(trials.trace, '$.events') WHERE json_extract(value, '$.type') = '${type}')`;
  db.exec(`UPDATE trials SET llm_calls = ${count('assistant')}, turns = ${count('session_start')}
           WHERE llm_calls IS NULL AND trace IS NOT NULL`);
}
