<script>
  import { live, send } from '../lib/hud.svelte.js';

  // The gods are not user facing; they show here only as chips.
  const GODS = ['Dionysus', 'Prometheus', 'Daedalus', 'Ananke · Ate', 'Tyche', 'Hermes', 'Orpheus'];
  let text = $state('');
  function submit(e) {
    e.preventDefault();
    const t = text;
    text = '';
    send(t);
  }
</script>

<footer class="foot">
  <div class="council">
    <div class="label">COUNCIL · NOT USER FACING</div>
    <div class="chips">
      {#each GODS as g}<span class="chip" title="council not wired yet (build step 7)"><i></i>{g} <small>idle</small></span>{/each}
    </div>
  </div>
  <form class="input" onsubmit={submit}>
    <input
      bind:value={text}
      placeholder="Speak or type to the Machine"
      aria-label="Message"
      maxlength="4000"
      autocomplete="off"
    />
    <button type="button" disabled title="push to talk comes with voice (build step 8)" aria-label="Push to talk">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3" fill="none" stroke="currentColor" stroke-width="1.6" /></svg>
    </button>
    <button type="button" class="council-btn" disabled title="council simulations come in build step 7">Council</button>
    <button type="submit" class="primary" disabled={live.sending || !text.trim()}>{live.sending ? 'Wait' : 'Send'}</button>
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
  .chip small { font-size: var(--label); }
  .input { display: flex; gap: 10px; flex: 1; min-width: 320px; max-width: 820px; }
  input {
    flex: 1; min-height: 52px; background: var(--panel); border: 1px solid var(--rule);
    padding: 0 16px; font-size: var(--feed); min-width: 0;
  }
  input::placeholder { color: var(--faint); }
  .input button { min-height: 52px; }
  .council-btn { border-color: var(--council); color: var(--council); }
  .council-btn:disabled { border-color: var(--rule); }
  @media (max-width: 900px) {
    .council { display: none; }
    .input { min-width: 0; }
    .council-btn { display: none; }
  }
</style>
