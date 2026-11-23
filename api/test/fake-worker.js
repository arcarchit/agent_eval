// Fake worker for runner tests. Behaviour depends on the task id.
const fs = require('fs');
let input = '';
process.stdin.on('data', (d) => (input += d));
process.stdin.on('end', () => {
  const { task } = JSON.parse(input);
  const ok = (passed) => {
    console.log(JSON.stringify({ status: 'ok', passed, score: passed ? 1 : 0, failure_category: passed ? null : 'wrong_answer',
      grader: { type: 'exact', reasoning: 'fake' }, trace: { events: [] }, input_tokens: 10, output_tokens: 5,
      cost_usd: 0.001, judge_cost_usd: 0, latency_ms: 5 }));
    process.exit(0);
  };
  if (task.id === 'arith-01') ok(true);
  if (task.id === 'arith-02') ok(false);
  if (task.id === 'logic-01') {            // rate limited on the first attempt, then fine
    const flag = `${process.env.FAKE_STATE_DIR}/flaky`;
    if (!fs.existsSync(flag)) { fs.writeFileSync(flag, '1'); console.log(JSON.stringify({ status: 'retryable_error', error: '429' })); process.exit(2); }
    ok(true);
  }
  if (task.id === 'text-01') setTimeout(() => {}, 60_000); // hangs -> timeout
  if (task.id === 'date-01') { console.log(JSON.stringify({ status: 'error', error: 'boom' })); process.exit(1); }
});
