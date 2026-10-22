<script>
  import Box from './Box.svelte';
  import Wave from './Wave.svelte';
  import { css } from '../lib/css.js';
  import { PERSONAS } from '../lib/reduce.js';
  import { hud, live, floorAction } from '../lib/hud.svelte.js';

  let { id } = $props();
  let p = $derived(hud.personas[id]);
  let meta = $derived(PERSONAS[id]);
  let other = $derived(id === 'finch' ? 'root' : 'finch');
  let otherState = $derived(hud.personas[other].state);
  // The listener renders at 40% while the other one holds the floor.
  let dim = $derived(p.state === 'LISTENING' && ['SPEAKING', 'THINKING'].includes(otherState));
  let muted = $derived(live.floor?.muted?.[id]);
  let threshold = $derived(live.floor?.threshold ?? 0.7);
  let cooldown = $derived(Math.round(live.floor?.cooldown_left_s ?? 0));
  let pressure = $derived(p.pressure ?? 0);
</script>

<Box tone={id} {dim} class="card">
  <div class="head">
    <h2 class={id}>{meta.name}</h2>
    <span class="badge {p.state.toLowerCase()} {id}">{p.state}</span>
  </div>
  <div class="traits">{meta.traits}</div>
  {#if muted}<div class="muted-badge">MUTED · {muted} MIN</div>{/if}
  <div class="wave {id}"><Wave active={p.state === 'SPEAKING' || p.state === 'INTERJECT'} /></div>
  <div class="pressure">
    <div class="row">
      <span>LAST PRESSURE{p.source ? ' · ' + p.source : ''}</span>
      <span class={id}>{p.pressure != null ? pressure.toFixed(2) : '–'} / {threshold.toFixed(2)}</span>
    </div>
    <div class="bar" aria-hidden="true">
      <span class="fill {id}" use:css={{ width: `${Math.min(100, pressure * 100)}%` }}></span>
      <span class="mark" use:css={{ left: `${threshold * 100}%` }}></span>
    </div>
  </div>
  <div class="foot">
    <span>voice {p.voice || `${id}-v1`} · turn {p.turn || hud.turn}{p.seconds != null ? ` · ${p.seconds} s` : ''} · cooldown {cooldown > 0 ? cooldown + ' s' : 'ready'}</span>
  </div>
  <div class="actions">
    {#if muted}
      <button onclick={() => floorAction('unmute', { persona: id })}>Unmute</button>
    {:else}
      <button onclick={() => floorAction('mute', { persona: id, minutes: 30 })}>{meta.name.charAt(0) + meta.name.slice(1).toLowerCase()}, not now</button>
    {/if}
  </div>
</Box>

<style>
  .head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
  h2 { margin: 0; font-family: var(--cond); font-weight: 700; font-size: 26px; letter-spacing: 0.18em; }
  .finch { color: var(--finch); }
  .root { color: var(--root); }
  .badge { font-size: var(--label); letter-spacing: 0.1em; padding: 2px 8px; border: 1px solid var(--faint); color: var(--muted); }
  .badge.speaking.finch { background: var(--finch); color: var(--ground); border-color: var(--finch); }
  .badge.speaking.root { background: var(--root); color: var(--ground); border-color: var(--root); }
  .badge.listening.finch { border-color: var(--finch); color: var(--finch); }
  .badge.listening.root { border-color: var(--root); color: var(--root); }
  .badge.thinking { border-color: var(--council); color: var(--council); }
  .badge.interject { border-color: var(--root); background: var(--root); color: var(--ground); }
  .traits { color: var(--muted); margin: 8px 0 12px; }
  .muted-badge { color: var(--alert); text-decoration: line-through; font-size: var(--label); letter-spacing: var(--track); margin-bottom: 8px; }
  .wave { margin: 4px 0 12px; }
  .pressure .row { display: flex; justify-content: space-between; color: var(--muted); font-size: var(--label); letter-spacing: 0.06em; }
  .bar { position: relative; height: 6px; background: var(--rule); margin: 6px 0 10px; }
  .fill { position: absolute; left: 0; top: 0; bottom: 0; background: currentColor; transition: width 200ms; }
  .mark { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--text); }
  .foot { color: var(--muted); }
  .actions { margin-top: 10px; display: flex; gap: 8px; }
  :global(.card) { margin-bottom: 20px; }
</style>
