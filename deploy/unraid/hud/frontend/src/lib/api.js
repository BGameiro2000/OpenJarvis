// Small fetch helpers. Same origin only; the backend adds every key.
export async function getJSON(url, opts = {}) {
  const r = await fetch(url, { credentials: 'same-origin', ...opts });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `${r.status}`);
  return body;
}

export function postJSON(url, data) {
  return getJSON(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data ?? {}),
  });
}

// POST, then read a server-sent event stream, calling onEvent per object.
export async function postSSE(url, data, onEvent) {
  const r = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!r.ok || !r.body) throw new Error(`${r.status}`);
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
        if (!line.startsWith('data: ')) continue;
        try {
          onEvent(JSON.parse(line.slice(6)));
        } catch {
          /* a malformed line never breaks the screen */
        }
      }
    }
  }
}

export const fmtSize = (n) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n >= 1e3 ? `${Math.round(n / 1e3)} kB` : `${n} B`;
export const fmtTime = (t) => {
  const d = new Date(t * 1000);
  const two = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
};
