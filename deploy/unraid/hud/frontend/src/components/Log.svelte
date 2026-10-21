<script>
  // Decision log, newest first. Virtualised: only the rows in view are in
  // the DOM, so 2,000 lines cost the same as twenty.
  import { hud, live } from '../lib/hud.svelte.js';

  const ROW = 26;
  let top = $state(0);
  let height = $state(600);
  let first = $derived(Math.max(0, Math.floor(top / ROW) - 5));
  let count = $derived(Math.ceil(height / ROW) + 10);
  let rows = $derived(hud.log.slice(first, first + count));
  import { css } from '../lib/css.js';
</script>

<section class="log">
  <div class="bar">
    <span class="label">DECISION LOG</span>
    <span class="label conn {live.conn}">● {live.conn === 'live' ? 'LIVE' : live.conn === 'down' ? 'RECONNECTING' : 'CONNECTING'}</span>
  </div>
  <div class="view" bind:clientHeight={height} onscroll={(e) => (top = e.currentTarget.scrollTop)}>
    <div class="spacer" use:css={{ height: `${hud.log.length * ROW}px` }}>
      {#each rows as l, i (l.key)}
        <div class="line {l.tone}" class:fresh={first + i === 0} use:css={{ transform: `translateY(${(first + i) * ROW}px)` }}>
          <time>{l.ts}</time><b class="code {l.code.toLowerCase()}">{l.code}</b><span class="actor">{l.actor}</span><span class="facts" title={l.facts}>{l.facts}</span>
        </div>
      {/each}
    </div>
  </div>
</section>

<style>
  .log { display: flex; flex-direction: column; min-height: 0; height: 100%; }
  .bar { display: flex; justify-content: space-between; margin-bottom: 12px; }
  .conn.live { color: var(--filed); }
  .conn.down { color: var(--alert); }
  .view { flex: 1; overflow-y: auto; min-height: 0; position: relative; }
  .spacer { position: relative; }
  .line {
    position: absolute; left: 0; right: 0; top: 0; height: 26px; display: grid;
    grid-template-columns: 66px 52px 64px 1fr; gap: 8px; align-items: center; white-space: nowrap;
  }
  .line.fresh > * { animation: push var(--push) ease-out both; }
  time { color: var(--faint); }
  .code { font-weight: 400; color: var(--text); }
  .code.interj, .line.root .code { color: var(--root); }
  .code.verdict, .code.council, .code.bid { color: var(--council); }
  .code.escal, .code.error, .line.alert .code { color: var(--alert); }
  .code.file, .code.route { color: var(--filed); }
  .code.held { color: var(--faint); }
  .actor { color: var(--muted); overflow: hidden; text-overflow: ellipsis; }
  .facts { overflow: hidden; text-overflow: ellipsis; }
  .line.faint .facts { color: var(--faint); }
</style>
