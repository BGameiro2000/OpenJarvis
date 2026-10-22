<script>
  import Box from './Box.svelte';
  import { hud, live, floorAction } from '../lib/hud.svelte.js';
  import { href } from '../lib/route.svelte.js';

  let scroller = $state();
  let subject = $derived(hud.subject);
  let holder = $derived(hud.holder);
  let session = $derived(new Date(live.now).toISOString().slice(0, 10));

  // Keep the newest line in view while a turn streams in.
  $effect(() => {
    hud.feed.length;
    hud.feed.at(-1)?.text;
    if (scroller) queueMicrotask(() => (scroller.scrollTop = scroller.scrollHeight));
  });
</script>

<section class="feed">
  <div class="bar">
    <span class="label">PRIMARY FEED</span>
    <span class="label">SESSION {session} · TURN {hud.turn}</span>
  </div>

  {#key subject?.turn}
    <Box tone={subject ? holder : 'unknown'} class="subject">
      <div class="label sub">SUBJECT · {subject ? 'ACTIVE' : 'IDLE'}</div>
      <h1>{subject ? subject.text : 'Waiting for Bernardo'}</h1>
      <div class="meta">
        {#if subject}held by {holder.toUpperCase()} · turn {subject.turn}{subject.profile ? ` · profile ${subject.profile}` : ''}{/if}
        {#if hud.council.sim}
          <span class="council">
            COUNCIL {new Set(hud.council.called.map((g) => (g === 'ate' ? 'ananke' : g))).size} / 7 CONSULTED{#if hud.council.verdict} · VERDICT {hud.council.verdict.decision} · {hud.council.verdict.confidence?.toFixed(2)}{/if}
            · <a href={href('council', hud.council.sim)}>Open simulation</a>
          </span>
        {:else}
          <span class="council faint">COUNCIL NOT CONSULTED</span>
        {/if}
      </div>
    </Box>
  {/key}

  <div class="rows" bind:this={scroller} aria-live="polite">
    {#each hud.feed as row (row.key)}
      <div class="row {row.tone}" class:exchange={row.exchange}>
        <time>{row.ts}</time>
        <span class="tag {row.tone}">{row.tag}</span>
        <div class="text">
          <p>{row.text}</p>
          {#if row.dropped?.length}
            <p class="dropped" title="what the speaker would have said">unsaid: {row.dropped.join(' ')}</p>
          {/if}
          {#if row.kind === 'comment'}<p class="note">shown, not spoken</p>{/if}
          {#if row.kind === 'hand' && hud.hand?.key === row.key}
            <div class="grant">
              <button class="primary" onclick={() => floorAction('grant')}>Go on, {row.who === 'root' ? 'Root' : 'Finch'}</button>
              <button onclick={() => { hud.hand = null; floorAction('feedback', { persona: row.who, accepted: false }); }}>Not now</button>
            </div>
          {/if}
        </div>
      </div>
    {/each}
    {#if live.error}
      <div class="row alert"><time></time><span class="tag alert">MACHINE</span><div class="text"><p>{live.error}</p></div></div>
    {/if}
  </div>
</section>

<style>
  .feed { display: flex; flex-direction: column; min-height: 0; height: 100%; }
  .bar { display: flex; justify-content: space-between; margin-bottom: 16px; }
  :global(.subject) { margin-bottom: 20px; --scan-h: 140px; }
  .sub { color: var(--council); }
  h1 {
    font-family: var(--cond); font-weight: 700; font-size: 34px; line-height: 1.15; margin: 8px 0 10px;
    letter-spacing: 0.02em; overflow-wrap: anywhere;
  }
  .meta { color: var(--muted); display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
  .council { color: var(--council); font-size: var(--label); letter-spacing: var(--track); }
  .council.faint { color: var(--faint); }
  .rows { flex: 1; overflow-y: auto; min-height: 0; padding-right: 8px; }
  .row {
    display: grid; grid-template-columns: 84px 170px 1fr; gap: 16px; padding: 10px 0;
    font-size: var(--feed); animation: push var(--push) ease-out both;
  }
  .row.exchange { padding-left: 24px; }
  time { color: var(--faint); }
  .tag {
    align-self: start; justify-self: start; border: 1px solid currentColor; padding: 1px 8px;
    font-size: 12px; letter-spacing: 0.12em; color: var(--muted);
  }
  .tag.finch { color: var(--finch); }
  .tag.root { color: var(--root); }
  .tag.alert { color: var(--alert); }
  .tag.quiet { color: var(--faint); border-style: dashed; }
  .text p { margin: 0; overflow-wrap: anywhere; }
  .row.root .text p:first-child { color: var(--root); }
  .row.alert .text p:first-child { color: var(--alert); }
  .row.quiet .text p:first-child { color: var(--muted); }
  .dropped { color: var(--faint); text-decoration: line-through; margin-top: 4px !important; font-size: 12px; }
  .note { color: var(--faint); font-size: var(--label); letter-spacing: var(--track); text-transform: uppercase; margin-top: 4px !important; }
  .grant { display: flex; gap: 8px; margin-top: 10px; }
  @media (max-width: 900px) {
    .row { grid-template-columns: 1fr; gap: 4px; }
    h1 { font-size: 24px; }
  }
</style>
