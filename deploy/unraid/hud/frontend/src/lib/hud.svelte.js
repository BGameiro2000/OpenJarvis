// Live HUD state: events from the backend, status and floor snapshot.
// The browser never sees a key; every call goes to this origin's /api.
import { apply, initial } from './reduce.js';

export const hud = $state(initial());
export const live = $state({
  conn: 'connecting',
  status: null,
  floor: null,
  now: Date.now(),
  sending: false,
  error: '',
  draft: '',
  intake: null,
});

function handle(ev) {
  apply(hud, ev, live.floor?.threshold ?? 0.7);
  if (['route', 'escalate', 'file'].includes(ev.kind)) refreshIntakeSoon();
  if (ev.kind === 'config' || ev.kind === 'hand' || (ev.kind === 'turn' && ev.data?.phase === 'end')) refreshFloor();
}

async function getJSON(url, opts) {
  const r = await fetch(url, { credentials: 'same-origin', ...opts });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `${r.status}`);
  return body;
}

let intakeTimer;
function refreshIntakeSoon() {
  clearTimeout(intakeTimer);
  intakeTimer = setTimeout(refreshIntake, 600);
}

export async function refreshIntake() {
  try {
    const b = await getJSON('/api/intake');
    live.intake = b.summary;
  } catch {
    live.intake = null;
  }
}

export async function refreshStatus() {
  try {
    live.status = await getJSON('/api/status');
    if (live.status.floor?.state) live.floor = live.status.floor.state;
  } catch (e) {
    live.status = { ...(live.status || {}), unreachable: true };
  }
}

export async function refreshFloor() {
  try {
    live.floor = await getJSON('/api/floor/state');
  } catch {
    live.floor = null;
  }
}

export function connect() {
  let es;
  let retry = 1000;
  const open = () => {
    es = new EventSource('/api/events?replay=300');
    es.onopen = () => {
      live.conn = 'live';
      retry = 1000;
    };
    es.onmessage = (m) => {
      try {
        handle(JSON.parse(m.data));
      } catch {
        /* a malformed line never breaks the screen */
      }
    };
    es.onerror = () => {
      live.conn = 'down';
      es.close();
      setTimeout(open, retry);
      retry = Math.min(retry * 2, 15000);
    };
  };
  open();
  setInterval(() => (live.now = Date.now()), 1000);
  refreshStatus();
  setInterval(refreshStatus, 10000);
  refreshIntake();
  setInterval(refreshIntake, 30000);
}

// The chat response streams the same events; they are applied too and
// deduplicated, so the turn still shows if the live feed is reconnecting.
export async function send(text) {
  text = text.trim();
  if (!text || live.sending) return;
  live.sending = true;
  live.error = '';
  try {
    const r = await fetch('/api/chat', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!r.ok || !r.body) throw new Error(`chat ${r.status}`);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        for (const line of block.split('\n')) {
          if (line.startsWith('data: ')) {
            try {
              handle(JSON.parse(line.slice(6)));
            } catch {
              /* ignore */
            }
          }
        }
      }
    }
  } catch (e) {
    live.error = String(e.message || e);
  } finally {
    live.sending = false;
  }
}

export async function floorAction(action, body = {}) {
  try {
    live.floor = await getJSON(`/api/floor/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    live.error = '';
  } catch (e) {
    live.error = String(e.message || e);
  }
  if (!live.floor?.profile) refreshFloor();
}
