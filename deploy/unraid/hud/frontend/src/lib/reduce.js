// Event reducer: one HUD event in, screen state out. Pure, so it is tested
// with node --test (tests/reduce.test.mjs). The HUD never infers state from
// text; every change on screen comes from one of these events.

export const PERSONAS = {
  finch: { name: 'FINCH', traits: 'analytical · cautious · admin voice' },
  root: { name: 'ROOT', traits: 'direct · protective · analog interface' },
};
export const LOG_CAP = 2000;
export const FEED_CAP = 300;

const ACT_TAG = {
  interject: 'CUT IN',
  cut: 'STOP',
  build: 'BUILDS',
  add: 'ADDS',
  correct: 'CORRECTS',
  comment: 'ASIDE',
  hand: 'HAND UP',
};
const ACT_CODE = {
  interject: 'INTERJ',
  cut: 'INTERJ',
  build: 'BUILD',
  add: 'ADD',
  correct: 'CORR',
  comment: 'ASIDE',
  hand: 'HAND',
};

export function initial() {
  const persona = () => ({ state: 'IDLE', turn: 0, seconds: null, pressure: null, source: '', voice: '' });
  return {
    personas: { finch: persona(), root: persona() },
    feed: [],
    log: [],
    seen: new Set(),
    turn: 0,
    turnStart: null,
    subject: null,
    holder: 'finch',
    hand: null,
    lastConfig: null,
  };
}

const up = (s) => (s || '').toUpperCase();
const pct = (x) => (typeof x === 'number' ? x.toFixed(2) : '');

function addLog(st, ev, code, actor, facts, tone = '') {
  st.log.unshift({ key: `${ev.id}@${ev.t}:${code}`, ts: (ev.ts || '').slice(0, 8), code, actor, facts, tone });
  if (st.log.length > LOG_CAP) st.log.length = LOG_CAP;
}

function addFeed(st, row) {
  st.feed.push(row);
  if (st.feed.length > FEED_CAP) st.feed.splice(0, st.feed.length - FEED_CAP);
}

export function apply(st, ev, threshold = 0.7) {
  const key = `${ev.id}@${ev.t}`;
  if (ev.id) {
    if (st.seen.has(key)) return false;
    st.seen.add(key);
    if (st.seen.size > 5000) st.seen = new Set([...st.seen].slice(-2500));
  }
  const d = ev.data || {};
  const who = ev.speaker || '';
  const ts = (ev.ts || '').slice(0, 8);

  switch (ev.kind) {
    case 'turn': {
      if (d.phase === 'start') {
        st.turn = ev.turn || st.turn + 1;
        st.turnStart = ev.t;
        st.subject = { text: ev.text || '', turn: st.turn, profile: d.profile || '', at: ts };
        st.hand = null;
        addFeed(st, { key, ts, who: 'user', tag: 'BERNARDO', text: ev.text || '', tone: 'user' });
        addLog(st, ev, 'TURN', 'bernardo', `start · turn ${st.turn}${d.profile ? ' · ' + d.profile : ''}`);
      } else if (d.phase === 'end') {
        const secs = d.seconds ?? (st.turnStart ? +(ev.t - st.turnStart).toFixed(1) : null);
        const h = st.personas[st.holder];
        if (h) h.seconds = secs;
        addLog(st, ev, 'TURN', st.holder, `end${secs != null ? ' · ' + secs + ' s' : ''}`);
      } else if (d.phase === 'error') {
        addFeed(st, { key, ts, who: 'machine', tag: 'MACHINE', text: ev.text, tone: 'alert' });
        addLog(st, ev, 'ERROR', 'floor', ev.text, 'alert');
      }
      return true;
    }
    case 'state': {
      const p = st.personas[who];
      if (p) {
        p.state = d.state || p.state;
        p.turn = st.turn;
      }
      if (d.state === 'SPEAKING' || d.state === 'THINKING') st.holder = who;
      return true;
    }
    case 'bid': {
      const s = d.scores || {};
      const facts = Object.entries(s).map(([k, v]) => `${k} ${pct(v)}`).join(' · ');
      addLog(st, ev, 'BID', who, `${facts}${d.named ? ' · named ' + d.named : ''}`);
      st.holder = who;
      return true;
    }
    case 'say': {
      const last = st.feed[st.feed.length - 1];
      const granted = !!d.granted;
      if (!granted && last && last.who === who && last.kind === 'say' && last.turn === ev.turn) {
        last.text += ' ' + (ev.text || '');
      } else {
        addFeed(st, { key, ts, who, kind: 'say', turn: ev.turn, tag: up(PERSONAS[who]?.name || who) + (granted ? ' ▸ GRANTED' : ''), text: ev.text || '', tone: who });
      }
      if (st.personas[who] && ev.voice) st.personas[who].voice = ev.voice;
      if (granted) addLog(st, ev, 'HAND', who, 'granted · spoke');
      return true;
    }
    case 'tool': {
      if (d.phase === 'end') {
        const ok = d.ok === false ? ' · failed' : '';
        addLog(st, ev, 'TOOL', who, `${d.tool || ev.text} · ${d.latency ?? '?'} s${ok}`, d.ok === false ? 'alert' : '');
      }
      return true;
    }
    case 'interject':
    case 'cut':
    case 'build':
    case 'add':
    case 'correct':
    case 'comment':
    case 'hand': {
      const name = PERSONAS[who]?.name || up(who);
      const spoken = !['comment', 'hand'].includes(ev.kind);
      if (d.duet) {
        addFeed(st, { key, ts, who, kind: 'say', turn: ev.turn, tag: name, text: ev.text || '', tone: who });
        return true;
      }
      addFeed(st, {
        key, ts, who, kind: ev.kind, turn: ev.turn,
        tag: `${name} ▸ ${ACT_TAG[ev.kind]}`,
        text: ev.text || '',
        tone: ev.kind === 'cut' ? 'alert' : spoken ? who : 'quiet',
        dropped: d.dropped || [],
        exchange: !!d.exchange,
      });
      const p = st.personas[who];
      if (p && typeof ev.pressure === 'number') {
        p.pressure = ev.pressure;
        p.source = ev.source || '';
      }
      let facts;
      if (ev.kind === 'interject') {
        facts = `pressure ${pct(ev.pressure)} ≥ ${pct(threshold)} · ${ev.source || 'review'}${d.dropped?.length ? ' · dropped ' + d.dropped.length : ''}`;
      } else if (ev.kind === 'cut') {
        facts = `hard cut · ${ev.source || 'safety'}`;
      } else if (d.exchange) {
        facts = 'builds back';
      } else {
        facts = `${ev.source || 'review'}${typeof ev.pressure === 'number' ? ' · ' + pct(ev.pressure) : ''}${d.reason ? ' · ' + d.reason : ''}`;
      }
      addLog(st, ev, ACT_CODE[ev.kind], who, facts, ev.kind === 'cut' ? 'alert' : ev.kind === 'interject' ? 'root' : '');
      if (ev.kind === 'hand') st.hand = { persona: who, text: ev.text, key };
      return true;
    }
    case 'yield': {
      addLog(st, ev, 'FLOOR', who, 'yields the floor');
      return true;
    }
    case 'suppressed': {
      addLog(st, ev, 'HELD', who, `${d.action || 'act'} · ${d.why || 'blocked'}`, 'faint');
      return true;
    }
    case 'config': {
      const code = d.mute || d.unmute || 'minutes' in d ? 'MUTE' : 'FLOOR';
      addLog(st, ev, code, who || d.who || 'hud', ev.text || `profile ${d.profile}`);
      st.lastConfig = key;
      return true;
    }
    case 'error': {
      addFeed(st, { key, ts, who: 'machine', tag: 'MACHINE', text: ev.text || 'error', tone: 'alert' });
      addLog(st, ev, 'ERROR', 'hud', ev.text || 'error', 'alert');
      for (const p of Object.values(st.personas)) if (p.state !== 'IDLE') p.state = 'LISTENING';
      return true;
    }
    default:
      return false;
  }
}
