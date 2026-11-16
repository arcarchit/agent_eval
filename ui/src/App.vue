<template>
  <header>
    <strong>Agent Evals</strong>
    <router-link to="/experiments">Experiments</router-link>
    <router-link to="/configs">Configs</router-link>
  </header>
  <main><router-view /></main>
</template>

<style>
:root { --bg: #fff; --fg: #1d1d1f; --muted: #6b7280; --line: #e5e7eb; --pass: #15803d; --fail: #b91c1c; --warn: #b45309; --accent: #2563eb; --soft: #f6f7f9; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #111318; --fg: #e5e7eb; --muted: #9ca3af; --line: #2a2f3a; --pass: #4ade80; --fail: #f87171; --warn: #fbbf24; --accent: #60a5fa; --soft: #1a1e26; }
}
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.5 system-ui, sans-serif; background: var(--bg); color: var(--fg); }
header { display: flex; gap: 20px; align-items: center; padding: 12px 24px; border-bottom: 1px solid var(--line); }
header a { color: var(--muted); text-decoration: none; }
header a.router-link-active { color: var(--accent); }
main { padding: 16px 24px; max-width: 1300px; }
a { color: var(--accent); }
table { border-collapse: collapse; width: 100%; margin: 8px 0 20px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 500; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.pass { color: var(--pass); } .fail { color: var(--fail); } .warn { color: var(--warn); } .muted { color: var(--muted); }
.card { border: 1px solid var(--line); border-radius: 8px; padding: 12px 16px; margin: 12px 0; background: var(--soft); }
.row { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }
label { display: flex; flex-direction: column; gap: 2px; color: var(--muted); font-size: 12px; }
input, select, button { font: inherit; padding: 5px 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--fg); }
button { cursor: pointer; background: var(--accent); color: #fff; border: none; }
pre { white-space: pre-wrap; word-break: break-word; margin: 4px 0; font-size: 12.5px; }
.error { color: var(--fail); }
.verdict { font-size: 18px; font-weight: 600; }
</style>
