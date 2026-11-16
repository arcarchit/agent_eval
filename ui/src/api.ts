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

export const pct = (x: number) => (Number.isFinite(x) ? (x * 100).toFixed(1) + '%' : '–');
export const usd = (x: number) => (Number.isFinite(x) ? '$' + x.toFixed(4) : '–');
export const secs = (x: number) => (Number.isFinite(x) ? (x / 1000).toFixed(1) + 's' : '–');
