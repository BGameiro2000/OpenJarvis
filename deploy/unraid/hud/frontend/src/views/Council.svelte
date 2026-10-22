<script>
  // Council simulations (guide section 6): router, round 1 positions, round 2
  // rebuttals, verdict with confidence and recorded dissent, branches.
  // Saved simulations replay from the vault; a new one streams in live.
  import { onMount, untrack } from 'svelte';
  import Box from '../components/Box.svelte';
  import { css } from '../lib/css.js';
  import { getJSON, postSSE } from '../lib/api.js';
  import { live } from '../lib/hud.svelte.js';
  import { nav, go } from '../lib/route.svelte.js';

  let sims = $state([]);
  let gods = $state([]);
  let rec = $state(null);
  let running = $state(false);
  let question = $state('');
  let err = $state('');
  let typed = $state('');

  const PAIR = new Set(['ananke', 'ate']);
  let router = $derived(rec?.router || []);
  let reasonOf = $derived(Object.fromEntries(router.map((r) => [r.god, r])));
  let cards = $derived(
    gods.filter((g) => g.id !== 'ate').map((g) => {
      const ids = g.id === 'ananke' ? ['ananke', 'ate'] : [g.id];
      const rs = ids.map((i) => reasonOf[i]).filter(Boolean);
      return {
        id: g.id,
        name: g.id === 'ananke' ? 'Ananke · Ate' : g.name,
        domain: g.domain.replace(' (pair)', ''),
        called: rs.some((r) => r.called),
        reason: rs.find((r) => r.called)?.reason || rs[0]?.reason || '',
      };
    }),
  );
  let r1 = $derived(rec?.rounds?.[0] || []);
  let r2 = $derived(rec?.rounds?.[1] || []);
  let panels = $derived.by(() => {
    // Ananke and Ate render as one paired panel.
    const out = [];
    const pair = r1.filter((p) => PAIR.has(p.god));
    for (const p of r1) if (!PAIR.has(p.god)) out.push({ key: p.god, items: [p] });
    if (pair.length) out.push({ key: 'pair', items: pair });
    return out;
  });
  let dissent = $derived(new Set((rec?.verdict?.dissent || []).map((d) => d.god)));

  async function loadList() {
    try {
      const r = await getJSON('/api/council');
      sims = r.sims;
      gods = r.gods;
    } catch (x) {
      err = String(x.message || x);
    }
  }

  async function open(sim) {
    err = '';
    try {
      rec = await getJSON('/api/council/' + encodeURIComponent(sim));
    } catch (x) {
      err = `Simulation ${sim}: ${x.message || x}`;
    }
  }

  async function run(q) {
    q = (q || question).trim();
    if (!q || running) return;
    running = true;
    err = '';
    rec = { sim: '', question: q, router: [], rounds: [[], []], verdict: null, branches: [] };
    try {
      await postSSE('/api/council/run', { question: q }, (ev) => {
        const d = ev.data || {};
        if (ev.kind === 'consult') rec.router = d.router || [];
        else if (ev.kind === 'position') rec.rounds[(d.round || 1) - 1].push(d);
        else if (ev.kind === 'verdict') rec.verdict = d;
        else if (ev.kind === 'council_saved') rec.sim = d.sim;
        else if (ev.kind === 'error') err = ev.text;
      });
      if (rec.sim) {
        await loadList();
        go('council', rec.sim);
      }
    } catch (x) {
      err = String(x.message || x);
    } finally {
      running = false;
    }
  }

  onMount(loadList);
  $effect(() => {
    const a = nav.arg;
    // Only the address drives this; reading rec here would re-run it on every event.
    untrack(() => {
      if (a.startsWith('ask:')) {
        question = a.slice(4);
        live.draft = '';
        run(question);
      } else if (a && a !== rec?.sim) {
        open(a);
      }
    });
  });

  // The verdict types out at 40 characters a second; reduced motion shows it at once.
  $effect(() => {
    const text = rec?.verdict?.summary || '';
    typed = '';
    if (!text) return;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
      typed = text;
      return;
    }
    let i = 0;
    const t = setInterval(() => {
      i += 1;
      typed = text.slice(0, i);
      if (i >= text.length) clearInterval(t);
    }, 25);
    return () => clearInterval(t);
  });

  const stanceTone = (s, god) => (dissent.has(god) ? 'alert' : s === 'flag' ? 'root' : s === 'against' ? 'unknown' : 'council');
</script>

<div class="council">
  <aside class="list">
    <form onsubmit={(e) => { e.preventDefault(); run(); }}>
      <div class="label">ASK THE COUNCIL</div>
      <textarea rows="3" bind:value={question} maxlength="2000" placeholder="A decision to simulate" aria-label="Question for the council"></textarea>
      <button class="council-btn" disabled={running || !question.trim()}>{running ? 'Simulating' : 'Run simulation'}</button>
    </form>
    <div class="label">SAVED SIMULATIONS</div>
    {#each sims as s (s.sim)}
      <a class="sim" class:on={rec?.sim === s.sim} href={'#/council/' + s.sim}>
        <span class="id">{s.sim}{s.path ? '' : ' · live'}</span>
        <span class="q">{s.question}</span>
        <span class="v">{s.verdict} · {s.confidence?.toFixed?.(2) ?? ''}</span>
      </a>
    {:else}
      <p class="empty">No simulations in council/ yet.</p>
    {/each}
  </aside>

  <section class="main">
    {#if err}<p class="alert">{err}</p>{/if}
    {#if !rec}
      <p class="empty">The council is not user facing: the gods advise, Finch and Root speak. Pick a saved simulation or run a new one.</p>
    {:else}
      <div class="label">SIMULATION {rec.sim || '· running'} {rec.created ? '· ' + rec.created.replace('T', ' ') : ''}</div>
      <h1>{rec.question}</h1>

      <div class="label">ROUTER · WHO WAS CALLED AND WHY</div>
      <div class="router">
        {#each cards as c (c.id)}
          <div class="god" class:called={c.called}>
            <b>{c.name}</b><small>{c.domain}</small>
            <span class="why">{c.called ? 'called: ' : 'not called: '}{c.reason || (running ? '…' : '')}</span>
          </div>
        {/each}
      </div>

      <div class="label">ROUND 1 · POSITIONS</div>
      <div class="panels">
        {#each panels as p (p.key)}
          <Box tone={p.items.some((i) => dissent.has(i.god)) ? 'alert' : p.key === 'pair' ? 'root' : 'council'} class="panel">
            {#each p.items as pos}
              <div class="pos">
                <div class="ph"><b>{pos.god.charAt(0).toUpperCase() + pos.god.slice(1)}</b><span class="stance {stanceTone(pos.stance, pos.god)}">{pos.stance}{dissent.has(pos.god) ? ' · dissent' : ''}</span></div>
                <div class="conf"><span use:css={{ width: `${(pos.confidence || 0) * 100}%` }}></span></div>
                <p>{pos.argument}</p>
                {#if pos.sources?.length}<div class="src">{#each pos.sources as s}<a href={'#/memory'} title="search memory for this note">{s}</a>{/each}</div>{/if}
              </div>
            {/each}
          </Box>
        {/each}
      </div>

      {#if r2.length}
        <div class="label">ROUND 2 · REBUTTALS</div>
        <div class="r2">
          {#each r2 as pos}
            <div class="reb"><b>{pos.god}</b> <span class="stance {stanceTone(pos.stance, pos.god)}">{pos.stance}</span> {pos.argument}</div>
          {/each}
        </div>
      {/if}

      {#if rec.verdict}
        <Box tone="council" class="verdict">
          <div class="label">VERDICT</div>
          <div class="vh"><span class="dec">{rec.verdict.decision}</span><span class="vc">confidence {rec.verdict.confidence?.toFixed(2)}</span></div>
          <p class="sum" aria-label={rec.verdict.summary}>{typed}<span class="caret" aria-hidden="true"></span></p>
          {#if rec.verdict.dissent?.length}
            <div class="dis">{#each rec.verdict.dissent as d}<div><span class="alert">DISSENT</span> {d.god}: {d.argument}</div>{/each}</div>
          {/if}
          {#if rec.verdict.flags?.length}
            <div class="dis">{#each rec.verdict.flags as f}<div><span class="rootc">FLAG</span> {f.god}: {f.text} · pressure {f.pressure}</div>{/each}</div>
          {/if}
        </Box>
      {/if}

      {#if rec.branches?.length}
        <div class="label">SCENARIO BRANCHES</div>
        <div class="branches">
          {#each rec.branches as b}
            <div class="br"><span class="bn">{b.name}</span><span class="bar"><span use:css={{ width: `${b.score * 100}%` }}></span></span><span class="bs">{b.score.toFixed(2)}</span><span class="note">{b.note}</span></div>
          {/each}
        </div>
      {/if}
    {/if}
  </section>
</div>

<style>
  .council { flex: 1; min-height: 0; display: grid; grid-template-columns: 300px 1fr; }
  .list { border-right: 1px solid var(--rule); padding: 16px 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
  form { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
  textarea { background: var(--panel); border: 1px solid var(--rule); padding: 8px; font: inherit; color: inherit; resize: vertical; }
  .council-btn { border-color: var(--council); color: var(--council); }
  .sim { display: flex; flex-direction: column; border: 1px solid var(--rule); padding: 8px 10px; text-decoration: none; color: var(--text); }
  .sim.on { border-color: var(--council); }
  .sim .id { color: var(--faint); font-size: var(--label); }
  .sim .q { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sim .v { color: var(--council); font-size: var(--label); }
  .main { padding: 16px 24px 32px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
  h1 { font-family: var(--cond); font-size: 30px; margin: 0 0 6px; overflow-wrap: anywhere; }
  .router { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
  .god { border: 1px solid var(--rule); padding: 8px 10px; display: flex; flex-direction: column; opacity: 0.4; }
  .god.called { opacity: 1; border-color: var(--council); }
  .god small { color: var(--muted); }
  .god .why { color: var(--muted); font-size: var(--label); margin-top: 4px; }
  .panels { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
  :global(.panel) { padding: 16px 18px !important; }
  .pos + .pos { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--rule); }
  .ph { display: flex; justify-content: space-between; align-items: baseline; }
  .ph b { font-family: var(--cond); font-size: 18px; }
  .stance { font-size: var(--label); letter-spacing: var(--track); text-transform: uppercase; }
  .stance.council { color: var(--council); }
  .stance.root { color: var(--root); }
  .stance.alert { color: var(--alert); }
  .stance.unknown { color: var(--muted); }
  .conf { height: 4px; background: var(--rule); margin: 6px 0; }
  .conf span { display: block; height: 100%; background: var(--council); }
  .pos p { margin: 0; }
  .src { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; font-size: var(--label); }
  .r2 { display: flex; flex-direction: column; gap: 6px; }
  .reb b { font-family: var(--cond); text-transform: capitalize; }
  :global(.verdict) { margin-top: 8px; }
  .vh { display: flex; gap: 16px; align-items: baseline; }
  .dec { font-family: var(--cond); font-weight: 700; font-size: 34px; color: var(--council); letter-spacing: 0.08em; }
  .vc { color: var(--muted); }
  .sum { font-size: var(--feed); margin: 6px 0; min-height: 1.5em; }
  .caret { display: inline-block; width: 8px; height: 1em; background: var(--council); vertical-align: text-bottom; margin-left: 2px; animation: blink 1s steps(1) infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .dis { margin-top: 6px; color: var(--muted); }
  .alert { color: var(--alert); }
  .rootc { color: var(--root); }
  .branches { display: flex; flex-direction: column; gap: 6px; }
  .br { display: grid; grid-template-columns: 220px 1fr 50px 1fr; gap: 10px; align-items: center; }
  .bar { height: 6px; background: var(--rule); }
  .bar span { display: block; height: 100%; background: var(--council); }
  .bs { color: var(--council); }
  .note { color: var(--muted); }
  .empty { color: var(--faint); }
  @media (max-width: 900px) {
    .council { grid-template-columns: 1fr; }
    .list { border-right: 0; border-bottom: 1px solid var(--rule); max-height: 40vh; }
    .br { grid-template-columns: 1fr 60px; }
    .br .bar, .br .note { grid-column: 1 / -1; }
  }
</style>
