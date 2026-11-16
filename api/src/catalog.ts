// Read-only things that live in files: tasks, models, skills, harness names.
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import * as yaml from 'js-yaml';

export const ROOT = resolve(process.env.EVALS_ROOT ?? '..');

// Must match the registry in worker/harnesses/__init__.py.
export const HARNESSES = ['hermes', 'baseline'];
export const TOOLSETS = ['file', 'terminal'];

export interface Task {
  id: string;
  category: string;
  prompt: string;
  needs?: string;
  grader: { type: string; [k: string]: unknown };
  [k: string]: unknown;
}

export function loadTasks(): Task[] {
  const dir = join(ROOT, 'tasks');
  return readdirSync(dir)
    .filter((f) => f.endsWith('.yaml'))
    .sort()
    .map((f) => yaml.load(readFileSync(join(dir, f), 'utf8')) as Task);
}

export function loadModels(): string[] {
  const doc = yaml.load(readFileSync(join(ROOT, 'models.yaml'), 'utf8')) as { models: Record<string, unknown> };
  return Object.keys(doc.models);
}

export function loadSkills(): string[] {
  const dir = join(ROOT, 'skills');
  return existsSync(dir) ? readdirSync(dir).filter((d) => existsSync(join(dir, d, 'SKILL.md'))) : [];
}
