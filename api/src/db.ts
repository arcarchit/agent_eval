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
      UNIQUE (experiment_id, config_id, task_id, trial_index)
    );
  `);
  return db;
}

export const now = () => new Date().toISOString();
