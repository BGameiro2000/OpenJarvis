<script>
  import { live } from '../lib/hud.svelte.js';
  import { nav, href, VIEWS } from '../lib/route.svelte.js';
  const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
  const two = (n) => String(n).padStart(2, '0');
  let d = $derived(new Date(live.now));
  let date = $derived(`${two(d.getDate())} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`);
  let time = $derived(`${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`);
  let st = $derived(live.status || {});
  let svc = $derived(st.services || {});
  const ok = (s) => (s ? (s.ok ? 'ok' : 'bad') : 'unk');
</script>

<header class="top">
  <div class="brand">
    <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
      <path d="M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5M12 8v8M8 12h8" fill="none" stroke="currentColor" stroke-width="1.6" />
    </svg>
    <span class="name">THE MACHINE</span>
    <span class="where">/ HOME NODE · UNRAID</span>
    {#if st.mode === 'demo'}<span class="demo">DEMO · SCRIPTED</span>{/if}
  </div>
  <nav aria-label="Views">
    {#each VIEWS as v}
      <a href={href(v)} class:on={nav.view === v} aria-current={nav.view === v ? 'page' : undefined}>{v.toUpperCase()}</a>
    {/each}
  </nav>
  <div class="facts">
    <span><i>ASSET 001</i> {(st.owner || 'Bernardo').toUpperCase()}</span>
    <span><i>{date}</i> {time}</span>
    <span><i>CAMERAS</i> <b class="filed">{(st.cameras || 'off').toUpperCase()}</b> <i>·</i> <b class="filed">KILL SWITCH</b></span>
    <span><i>LINK</i> {(st.link || 'lan').toUpperCase()}</span>
    <span><i>ENGINE</i> {(st.engine || '?').toUpperCase()}</span>
    <span class="svc" title="OpenJarvis · Cognee · floor">
      <b class={ok(svc.openjarvis)}>OJ</b>
      <b class={ok(svc.cognee)}>COGNEE</b>
      <b class={st.floor ? (st.floor.ok ? 'ok' : 'unk') : 'unk'}>FLOOR</b>
    </span>
  </div>
</header>

<style>
  .top {
    display: flex; align-items: center; justify-content: space-between; gap: 24px;
    padding: 8px 24px; min-height: 52px; border-bottom: 1px solid var(--rule); flex-wrap: wrap; row-gap: 4px;
  }
  .brand { display: flex; align-items: center; gap: 12px; color: var(--finch); }
  .name { font-family: var(--cond); font-weight: 700; font-size: 16px; letter-spacing: 0.32em; }
  .where { color: var(--muted); letter-spacing: 0.08em; }
  .demo { color: var(--council); border: 1px solid var(--council); padding: 2px 6px; font-size: var(--label); letter-spacing: var(--track); }
  nav { display: flex; gap: 4px; }
  nav a {
    color: var(--muted); text-decoration: none; letter-spacing: var(--track); font-size: var(--label);
    padding: 0 12px; min-height: 36px; display: inline-flex; align-items: center; border: 1px solid transparent;
  }
  nav a:hover { color: var(--text); }
  nav a.on { color: var(--finch); border-color: var(--rule); }
  .facts { display: flex; gap: 18px; flex-wrap: wrap; letter-spacing: 0.02em; }
  i { font-style: normal; color: var(--muted); }
  b { font-weight: 400; }
  .filed { color: var(--filed); }
  .svc { display: flex; gap: 8px; }
  .svc b { font-size: var(--label); }
  .ok { color: var(--filed); }
  .bad { color: var(--alert); }
  .unk { color: var(--faint); }
  @media (max-width: 1500px) { .where { display: none; } }
  @media (max-width: 900px) {
    .top { padding: 8px 16px; gap: 8px; }
    .name { font-size: 14px; letter-spacing: 0.24em; }
    .facts { gap: 12px; font-size: var(--label); }
    .facts span:nth-child(-n + 2), .facts span:nth-child(5) { display: none; }
    nav { order: 3; width: 100%; overflow-x: auto; }
    nav a { padding: 0 8px; }
  }
</style>
