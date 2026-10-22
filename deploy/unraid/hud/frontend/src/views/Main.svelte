<script>
  import PersonaCard from '../components/PersonaCard.svelte';
  import FloorPanel from '../components/FloorPanel.svelte';
  import Feed from '../components/Feed.svelte';
  import Log from '../components/Log.svelte';
  import Footer from '../components/Footer.svelte';
  import { live } from '../lib/hud.svelte.js';
  import { href } from '../lib/route.svelte.js';

  let st = $derived(live.status || {});
  let ik = $derived(live.intake);
</script>

<main>
  <aside class="left">
    <div class="label head">PERSONAS · SHARED MEMORY</div>
    <PersonaCard id="finch" />
    <PersonaCard id="root" />
    <FloorPanel />
    <div class="rows">
      <div><span>MODE</span><span>{(st.mode || '–').toUpperCase()}</span></div>
      <div><span>MODEL</span><span>{st.model || '–'}</span></div>
      <div><span>TOKENS TODAY</span><span>[N]</span></div>
      <div><span>COST TODAY</span><span>[€]</span></div>
    </div>
  </aside>
  <section class="center"><Feed /></section>
  <aside class="right">
    <Log />
    <div class="intake">
      <div class="counts">
        <div><span class="label">INBOX</span><b>{ik?.inbox ?? '–'}</b></div>
        <div><span class="label">FILED TODAY</span><b>{ik?.filed_today ?? '–'}</b></div>
        <div><span class="label alert">ESCALATED</span><b class="alert">{ik?.escalated ?? '–'}</b></div>
      </div>
      <p class="links"><a href={href('intake')}>Review intake</a> <a href={href('memory')}>Search memory</a></p>
    </div>
  </aside>
</main>
<Footer />

<style>
  main { flex: 1; min-height: 0; display: grid; grid-template-columns: 300px 1fr 420px; }
  .left, .center, .right { padding: 20px 24px; min-height: 0; }
  .left { border-right: 1px solid var(--rule); overflow-y: auto; }
  .right { border-left: 1px solid var(--rule); display: flex; flex-direction: column; }
  .center { display: flex; flex-direction: column; }
  .head { margin-bottom: 16px; }
  .rows { margin-top: 16px; border-top: 1px solid var(--rule); padding-top: 12px; }
  .rows div { display: flex; justify-content: space-between; color: var(--muted); letter-spacing: 0.06em; line-height: 2; gap: 12px; }
  .rows div span:last-child { color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .intake { border-top: 1px solid var(--rule); padding-top: 16px; margin-top: 12px; }
  .counts { display: grid; grid-template-columns: repeat(3, 1fr); }
  .counts b { display: block; font-family: var(--cond); font-size: 28px; font-weight: 500; }
  .alert { color: var(--alert); }
  .links { display: flex; gap: 20px; margin: 12px 0 0; }
  @media (max-width: 1280px) { main { grid-template-columns: 280px 1fr 340px; } }
  @media (max-width: 900px) {
    main { display: flex; flex-direction: column; }
    .center { order: -1; min-height: 70vh; padding: 16px; }
    .left, .right { border: 0; border-top: 1px solid var(--rule); padding: 16px; }
    .right { height: 60vh; }
  }
</style>
