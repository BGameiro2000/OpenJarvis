<script>
  import { hud, live, send } from '../lib/hud.svelte.js';
  import { go } from '../lib/route.svelte.js';

  // The gods are not user facing; they show here only as chips.
  const GODS = [
    ['dionysus', 'Dionysus'], ['prometheus', 'Prometheus'], ['daedalus', 'Daedalus'],
    ['ananke', 'Ananke · Ate'], ['tyche', 'Tyche'], ['hermes', 'Hermes'], ['orpheus', 'Orpheus'],
  ];
  let input = $state();
  function chip(id) {
    const c = hud.council;
    const ids = id === 'ananke' ? ['ananke', 'ate'] : [id];
    if (ids.some((g) => c.dissent.includes(g))) return 'dissent';
    const stance = ids.map((g) => c.stances[g]).find(Boolean);
    if (stance) return stance;
    if (ids.some((g) => c.called.includes(g))) return 'called';
    return 'idle';
  }
  $effect(() => {
    if (live.draft && input) input.focus();
  });
  function submit(e) {
    e.preventDefault();
    const t = live.draft;
    live.draft = '';
    send(t);
  }
</script>

<footer class="foot">
  <div class="council">
    <div class="label">COUNCIL · NOT USER FACING</div>
    <div class="chips">
      {#each GODS as [id, name]}
        {@const state = chip(id)}
        <span class="chip {state}"><i></i>{name} <small>{state}</small></span>
      {/each}
    </div>
  </div>
  <form class="input" onsubmit={submit}>
    <input
      bind:this={input}
      bind:value={live.draft}
      placeholder="Speak or type to the Machine"
      aria-label="Message"
      maxlength="4000"
      autocomplete="off"
    />
    <button type="button" disabled title="push to talk comes with voice (build step 8)" aria-label="Push to talk">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3" fill="none" stroke="currentColor" stroke-width="1.6" /></svg>
    </button>
    <button type="button" class="council-btn" disabled={!live.draft.trim()} title="Put the question to the council" onclick={() => go('council', 'ask:' + live.draft.trim())}>Council</button>
    <button type="submit" class="primary" disabled={live.sending || !live.draft.trim()}>{live.sending ? 'Wait' : 'Send'}</button>
  </form>
</footer>

<style>
  .foot {
    display: flex; justify-content: space-between; align-items: flex-end; gap: 24px;
    padding: 14px 24px 18px; border-top: 1px solid var(--rule); flex-wrap: wrap;
  }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; max-width: 760px; }
  .chip {
    display: inline-flex; align-items: center; gap: 8px; border: 1px solid var(--rule);
    padding: 6px 12px; color: var(--faint);
  }
  .chip i { width: 8px; height: 8px; background: var(--faint); }
  .chip.called { color: var(--council); border-color: var(--council); }
  .chip.for, .chip.wait { color: var(--text); border-color: var(--council); }
  .chip.for i, .chip.wait i, .chip.called i { background: var(--council); }
  .chip.against { color: var(--text); border-color: var(--muted); }
  .chip.against i { background: var(--muted); }
  .chip.flag { color: var(--text); border-color: var(--root); }
  .chip.flag i { background: var(--root); }
  .chip.dissent { color: var(--text); border-color: var(--alert); }
  .chip.dissent i { background: var(--alert); }
  .chip small { font-size: var(--label); }
  .input { display: flex; gap: 10px; flex: 1; min-width: 320px; max-width: 820px; }
  input {
    flex: 1; min-height: 52px; background: var(--panel); border: 1px solid var(--rule);
    padding: 0 16px; font-size: var(--feed); min-width: 0;
  }
  input::placeholder { color: var(--faint); }
  .input button { min-height: 52px; }
  .council-btn { border-color: var(--council); color: var(--council); }
  .council-btn:disabled { border-color: var(--rule); color: var(--faint); }
  @media (max-width: 900px) {
    .council { display: none; }
    .input { min-width: 0; }
    .council-btn { display: none; }
  }
</style>
