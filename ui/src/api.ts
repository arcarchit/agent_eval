// Tiny fetch wrapper. Errors from the API are thrown with their message.
export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const init: RequestInit = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  };
  const res = await fetch('/api' + path, init);
  const data = await res.json();
  if (!res.ok) throw new Error(data.message ?? res.statusText);
  return data;
}

/** What each failure category means (rules in worker/failures.py, first match wins). Shown as tooltips. */
export const FAILURE_HELP: Record<string, string> = {
  timeout: 'The trial ran longer than the config timeout and was killed.',
  provider_error: 'The model API failed (safety net: such trials normally end as status "error" and are excluded).',
  step_exhaustion: 'Used all max_steps model calls without finishing.',
  loop: "The harness's loop guard stopped it, or it repeated the same tool call 3+ times.",
  truncated: 'A reply hit max_tokens (output limit) and the turn ended. A config limit, not only the model.',
  hallucinated_tool: 'Called a tool name that was not offered (made up, or a malformed call such as arguments in the name field).',
  bad_tool_args: 'Tool-call arguments were not valid JSON.',
  tool_errors: 'Most of its tool calls returned errors.',
  gave_up: 'Ended with an empty final answer.',
  wrong_answer: 'Finished normally, but the grader failed the answer.',
};

export const pct =(x: number) => (Number.isFinite(x) ? (x * 100).toFixed(1) + '%' : '–');
export const usd = (x: number) => (Number.isFinite(x) ? '$' + x.toFixed(4) : '–');
export const secs = (x: number) => (Number.isFinite(x) ? (x / 1000).toFixed(1) + 's' : '–');
