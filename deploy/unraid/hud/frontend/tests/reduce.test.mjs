import { test } from 'node:test';
import assert from 'node:assert/strict';
import { apply, initial, LOG_CAP } from '../src/lib/reduce.js';

let n = 0;
const ev = (kind, extra = {}) => ({ id: `e${++n}`, t: 1000 + n, ts: '14:31:40.000', kind, turn: 1, ...extra });

test('a turn drives persona states and the feed', () => {
  const st = initial();
  apply(st, ev('turn', { text: 'A2000 or RAM?', data: { phase: 'start' } }));
  apply(st, ev('state', { speaker: 'finch', data: { state: 'THINKING' } }));
  assert.equal(st.personas.finch.state, 'THINKING');
  apply(st, ev('state', { speaker: 'finch', data: { state: 'SPEAKING' } }));
  apply(st, ev('say', { speaker: 'finch', text: 'One.' }));
  apply(st, ev('say', { speaker: 'finch', text: 'Two.' }));
  apply(st, ev('interject', { speaker: 'root', text: 'Wait.', pressure: 0.74, source: 'council:ate', data: { dropped: ['Three.'] } }));
  apply(st, ev('yield', { speaker: 'finch' }));
  apply(st, ev('turn', { data: { phase: 'end', seconds: 1.4 } }));
  assert.deepEqual(st.feed.map((r) => r.tag), ['BERNARDO', 'FINCH', 'ROOT ▸ CUT IN']);
  assert.equal(st.feed[1].text, 'One. Two.');
  assert.deepEqual(st.feed[2].dropped, ['Three.']);
  assert.equal(st.personas.root.pressure, 0.74);
  assert.equal(st.subject.text, 'A2000 or RAM?');
  assert.deepEqual(st.log.map((l) => l.code), ['TURN', 'FLOOR', 'INTERJ', 'TURN']);
  assert.match(st.log[2].facts, /pressure 0.74 ≥ 0.70 · council:ate · dropped 1/);
});

test('tool end logs a duration; duplicates are ignored', () => {
  const st = initial();
  const e = ev('tool', { speaker: 'finch', text: 'cognee_recall', data: { phase: 'end', tool: 'cognee_recall', latency: 0.31 } });
  assert.equal(apply(st, e), true);
  assert.equal(apply(st, { ...e }), false);
  assert.equal(st.log.length, 1);
  assert.equal(st.log[0].facts, 'cognee_recall · 0.31 s');
});

test('hand waits; comment is quiet; held acts are logged', () => {
  const st = initial();
  apply(st, ev('hand', { speaker: 'root', text: 'Can I add?', pressure: 0.6 }));
  assert.equal(st.hand.persona, 'root');
  apply(st, ev('comment', { speaker: 'root', text: 'aside' }));
  assert.equal(st.feed[1].tone, 'quiet');
  apply(st, ev('suppressed', { speaker: 'root', data: { action: 'interject', why: 'cooldown' } }));
  assert.equal(st.log[0].code, 'HELD');
  apply(st, ev('turn', { text: 'next', data: { phase: 'start' } }));
  assert.equal(st.hand, null);
});

test('log is capped', () => {
  const st = initial();
  for (let i = 0; i < LOG_CAP + 50; i++) apply(st, ev('bid', { speaker: 'finch', data: { scores: { finch: 1 } } }));
  assert.equal(st.log.length, LOG_CAP);
});

test('codes are six characters at most', () => {
  const st = initial();
  for (const k of ['interject', 'cut', 'build', 'add', 'correct', 'comment', 'hand', 'yield', 'suppressed', 'config', 'error'])
    apply(st, ev(k, { speaker: 'root', text: 'x', data: {} }));
  for (const l of st.log) assert.ok(l.code.length <= 6, l.code);
});

test('council and intake events reach the log and the chips', () => {
  const st = initial();
  apply(st, ev('consult', { text: 'q', data: { sim: 's1', called: ['tyche', 'ate'] } }));
  apply(st, ev('position', { speaker: 'tyche', data: { round: 1, stance: 'for', confidence: 0.62 } }));
  apply(st, ev('position', { speaker: 'ate', data: { round: 1, stance: 'flag', confidence: 0.74 } }));
  apply(st, ev('verdict', { text: 'Wait.', data: { decision: 'WAIT', confidence: 0.78, dissent: [{ god: 'tyche' }] } }));
  assert.deepEqual(st.council.stances, { tyche: 'for', ate: 'flag' });
  assert.equal(st.council.verdict.decision, 'WAIT');
  assert.deepEqual(st.council.dissent, ['tyche']);
  apply(st, ev('route', { text: 'gtd_action · 0.91 · next-actions.md', data: { source: 'a.md' } }));
  apply(st, ev('escalate', { text: 'needs a transcript', data: { source: 'v.m4a' } }));
  apply(st, ev('file', { text: 'gtd/next-actions.md', data: { source: 'a.md' } }));
  assert.deepEqual(st.log.slice(0, 3).map((l) => l.code), ['FILE', 'ESCAL', 'ROUTE']);
  assert.equal(st.intakeTick, 3);
});
