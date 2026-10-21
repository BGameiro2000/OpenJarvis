<script>
  // Live switches for the floor manager: profile and interaction modes.
  import { live, floorAction } from '../lib/hud.svelte.js';

  const MODES = [
    ['bidding', 'Bid', 'who answers first'],
    ['interrupt', 'Interrupt', 'cut in at a sentence'],
    ['hard_cut', 'Hard cut', 'safety words stop at once'],
    ['build', 'Build', 'extend, back and forth'],
    ['addendum', 'Add', 'one caveat after'],
    ['correct', 'Correct', 'fix a wrong fact'],
    ['hand', 'Raise hand', 'wait for permission'],
    ['comment', 'Aside', 'shown, never spoken'],
    ['duet', 'Duet', 'one script, two voices'],
  ];
  let f = $derived(live.floor);
  let enabled = $derived(new Set(f?.enabled || []));

  function toggle(m) {
    if (enabled.has(m)) floorAction('config', { disable: [m] });
    else floorAction('config', { enable: [m] });
  }
</script>

<section class="floor">
  <div class="label">FLOOR · HOW THEY TALK</div>
  {#if f}
    <label class="profile">
      <span class="label">Profile</span>
      <select value={f.profile} onchange={(e) => floorAction('config', { profile: e.currentTarget.value })}>
        {#each f.profiles || [] as p}<option value={p}>{p}</option>{/each}
      </select>
    </label>
    <div class="modes">
      {#each MODES as [id, name, hint]}
        <button class="mode" class:on={enabled.has(id)} aria-pressed={enabled.has(id)} title={hint} onclick={() => toggle(id)}>
          <span class="dot"></span>{name}
        </button>
      {/each}
    </div>
    <div class="rows">
      <div><span>HOLDER</span><span>{(f.holder || '–').toUpperCase()}</span></div>
      <div><span>SPOKEN ACTS TODAY</span><span>{f.spoken_today ?? 0} / {f.daily_cap ?? '–'}</span></div>
      <div><span>THRESHOLD</span><span>{(f.threshold ?? 0.7).toFixed(2)}</span></div>
    </div>
  {:else}
    <p class="off">Floor package not loaded. One persona (Finch) answers through plain chat. Load <code>home_jarvis</code> in OpenJarvis to get both voices.</p>
  {/if}
</section>

<style>
  .floor { border-top: 1px solid var(--rule); padding-top: 16px; }
  .profile { display: flex; align-items: center; justify-content: space-between; margin: 12px 0; gap: 12px; }
  select { min-height: 44px; background: var(--panel); border: 1px solid var(--rule); padding: 0 10px; flex: 1; }
  .modes { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
  .mode { display: flex; align-items: center; gap: 8px; text-transform: none; letter-spacing: 0.02em; padding: 0 10px; color: var(--muted); }
  .mode .dot { width: 8px; height: 8px; border: 1px solid var(--faint); flex: none; }
  .mode.on { color: var(--text); border-color: var(--muted); }
  .mode.on .dot { background: var(--filed); border-color: var(--filed); }
  .rows { margin-top: 14px; }
  .rows div { display: flex; justify-content: space-between; color: var(--muted); letter-spacing: 0.06em; line-height: 2; }
  .rows div span:last-child { color: var(--text); }
  .off { color: var(--muted); }
  code { color: var(--text); }
</style>
