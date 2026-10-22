<script>
  import Box from '../components/Box.svelte';
  import Snippet from '../components/Snippet.svelte';
  import { getJSON } from '../lib/api.js';
  import { live } from '../lib/hud.svelte.js';
  import { go } from '../lib/route.svelte.js';

  // Cytoscape is big: load the graph only when this view needs it.
  const graphModule = import('../components/Graph.svelte');
  const TYPES = ['decision', 'project', 'person', 'idea', 'note', 'reference', 'council', 'system', 'inbox'];
  let q = $state('');
  let mode = $state('hybrid');
  let types = $state([]);
  let from = $state('');
  let to = $state('');
  let exclude = $state('');
  let res = $state(null);
  let busy = $state(false);
  let err = $state('');
  let sel = $state(null); // selected hit
  let note = $state(null);
  let graph = $state(null);
  let graphBusy = $state(false);
  let selNode = $state('');
  let range = $state([0, 0]); // indices into dates

  let dates = $derived.by(() => {
    const ds = new Set();
    for (const h of res?.hits || []) if (h.date) ds.add(h.date);
    for (const n of graph?.nodes || []) if (n.date) ds.add(n.date);
    return [...ds].sort();
  });
  $effect(() => {
    dates;
    range = [0, Math.max(0, dates.length - 1)];
  });
  let window_ = $derived(dates.length ? [dates[range[0]], dates[range[1]]] : null);
  let shown = $derived(
    (res?.hits || []).filter((h) => !window_ || !h.date || (h.date >= window_[0] && h.date <= window_[1])),
  );

  async function search(e) {
    e?.preventDefault();
    if (!q.trim()) return;
    busy = true;
    err = '';
    const p = new URLSearchParams({ q, mode, type: types.join(','), date_from: from, date_to: to, exclude });
    try {
      res = await getJSON('/api/memory/search?' + p);
      if (res.hits[0]?.path) pick(res.hits[0]);
    } catch (x) {
      err = String(x.message || x);
    } finally {
      busy = false;
    }
  }

  async function pick(hit) {
    sel = hit;
    selNode = '';
    note = null;
    if (!hit.path) {
      graph = null;
      return;
    }
    graphBusy = true;
    const [n, g] = await Promise.allSettled([
      getJSON('/api/memory/note?path=' + encodeURIComponent(hit.path)),
      getJSON('/api/graph?path=' + encodeURIComponent(hit.path) + '&q=' + encodeURIComponent(q)),
    ]);
    note = n.status === 'fulfilled' ? n.value : null;
    graph = g.status === 'fulfilled' ? g.value : { nodes: [], edges: [], reason: String(g.reason?.message || g.reason) };
    graphBusy = false;
  }

  async function openPath(path) {
    const hit = { path, title: path, key: path };
    await pick(hit);
  }

  function onNode(n) {
    selNode = n.id;
    if (n.path) openPath(n.path).then(() => (selNode = n.id));
  }

  function ask() {
    live.draft = `About ${note.title} (${note.path}): `;
    go('main');
  }

  function toggleType(t) {
    types = types.includes(t) ? types.filter((x) => x !== t) : [...types, t];
  }
</script>

<div class="memory">
  <form class="bar" onsubmit={search}>
    <input bind:value={q} placeholder="Search memory: notes, decisions, people, the graph" aria-label="Search memory" maxlength="300" />
    <div class="modes" role="radiogroup" aria-label="Search mode">
      {#each ['hybrid', 'vault', 'graph'] as m}
        <button type="button" role="radio" aria-checked={mode === m} class:on={mode === m} onclick={() => (mode = m)}>{m === 'vault' ? 'Vault text' : m}</button>
      {/each}
    </div>
    <button class="primary" type="submit" disabled={busy || !q.trim()}>{busy ? 'Searching' : 'Search'}</button>
  </form>
  {#if res}
    <div class="lat">
      {res.hits.length} hits · {res.ms} ms · no model call
      {#if res.sources.vault}· vault {res.sources.vault.ms} ms{/if}
      {#if res.sources.graph}· graph {#if res.sources.graph.ok}{res.sources.graph.ms} ms{#if res.sources.graph.via === 'demo'} (demo){/if}{:else}<span class="alert">unavailable: {res.sources.graph.reason}</span>{/if}{/if}
      · {res.notes} notes indexed
    </div>
  {/if}
  {#if err}<div class="lat alert">{err}</div>{/if}

  <div class="grid">
    <aside class="filters">
      <div class="label">TYPE</div>
      <div class="types">
        {#each TYPES as t}
          <button class="chip" class:on={types.includes(t)} aria-pressed={types.includes(t)} onclick={() => toggleType(t)}>{t}</button>
        {/each}
      </div>
      <label class="label" for="from">FROM</label>
      <input id="from" type="date" bind:value={from} />
      <label class="label" for="to">TO</label>
      <input id="to" type="date" bind:value={to} />
      <label class="label" for="ex">EXCLUDE FOLDERS</label>
      <input id="ex" bind:value={exclude} placeholder="daily, reference/chats" />
      <p class="hint">Filters apply on the next search.</p>
    </aside>

    <section class="results" aria-label="Results">
      {#if !res}
        <p class="empty">Hybrid search runs the vault text index and Cognee in parallel and merges them. Hits are tagged VAULT, GRAPH or BOTH.</p>
      {:else if !shown.length}
        <p class="empty">No hits{window_ ? ' in this date window' : ''}.</p>
      {/if}
      {#each shown as h (h.key)}
        <button class="hit" class:sel={sel?.key === h.key} onclick={() => pick(h)}>
          <span class="tag {h.tag.toLowerCase()}">{h.tag}</span>
          <span class="t">{h.title}</span>
          <span class="meta">{h.path || 'not in the vault'} · {h.type}{h.date ? ' · ' + h.date : ''}</span>
          {#if h.snippet}<Snippet text={h.snippet} />{/if}
          {#if h.chunk}<span class="chunk">graph: {h.chunk}</span>{/if}
        </button>
      {/each}
    </section>

    <section class="side">
      <div class="gbox">
        <div class="ghead">
          <span class="label">GRAPH · TWO HOPS</span>
          {#if graph}<span class="label">{graph.via === 'cognee' ? 'COGNEE' : 'WIKILINKS'} · {graph.nodes.length} NODES</span>{/if}
        </div>
        {#if graph?.reason}<p class="why">{graph.reason}</p>{/if}
        <div class="gwrap">
          {#if graph?.nodes?.length}
            {#await graphModule then G}
              <G.default data={graph} range={window_} selected={selNode} onselect={onNode} />
            {/await}
          {:else}
            <p class="empty">{graphBusy ? 'Loading the graph' : sel ? 'No graph for this hit.' : 'Pick a hit to see its neighbourhood.'}</p>
          {/if}
        </div>
        <div class="legend">
          <span><i class="sq decision"></i>decision</span><span><i class="ci project"></i>project, system</span>
          <span><i class="ci thing"></i>thing</span><span><i class="sq event"></i>event, pattern</span>
          <span><i class="di god"></i>god</span><span><i class="sq note"></i>note</span>
        </div>
        {#if dates.length > 1}
          <div class="scrub">
            <span class="label">TIMELINE {window_[0]} → {window_[1]}</span>
            <input type="range" min="0" max={dates.length - 1} bind:value={range[0]} aria-label="From date" oninput={() => { if (range[0] > range[1]) range[1] = range[0]; }} />
            <input type="range" min="0" max={dates.length - 1} bind:value={range[1]} aria-label="To date" oninput={() => { if (range[1] < range[0]) range[0] = range[1]; }} />
          </div>
        {/if}
      </div>

      <div class="insp">
        {#if note}
          {#key note.path}
            <Box tone={note.type === 'decision' ? 'finch' : 'council'}>
              <div class="label">{note.type.toUpperCase()} · {note.date}</div>
              <h2>{note.title}</h2>
              <div class="path">{note.path}</div>
              <div class="actions">
                <a class="btn" href={note.obsidian}>Open in Obsidian</a>
                <button onclick={ask}>Ask Finch</button>
              </div>
              <pre class="body">{note.current}</pre>
              {#if note.history}
                <details>
                  <summary class="label">DATED HISTORY</summary>
                  <pre class="body hist">{note.history}</pre>
                </details>
              {/if}
              {#if note.truncated}<p class="why">Long note: only the first 500 kB are shown.</p>{/if}
              {#if note.backlinks.length}
                <div class="label">LINKED FROM</div>
                <div class="links">{#each note.backlinks as b}<button class="link" onclick={() => openPath(b)}>{b}</button>{/each}</div>
              {/if}
            </Box>
          {/key}
        {:else}
          <p class="empty">The inspector shows the selected note: current truth on top, dated history below.</p>
        {/if}
      </div>
    </section>
  </div>
</div>

<style>
  .memory { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 16px 24px; gap: 8px; }
  .bar { display: flex; gap: 10px; flex-wrap: wrap; }
  .bar input { flex: 1; min-width: 220px; min-height: 48px; background: var(--panel); border: 1px solid var(--rule); padding: 0 14px; font-size: var(--feed); }
  .modes { display: flex; }
  .modes button { text-transform: uppercase; }
  .modes button.on { border-color: var(--council); color: var(--council); }
  .lat { color: var(--faint); font-size: var(--label); letter-spacing: 0.06em; }
  .alert { color: var(--alert); }
  .grid { flex: 1; min-height: 0; display: grid; grid-template-columns: 200px minmax(280px, 1fr) minmax(360px, 1.15fr); gap: 20px; }
  .filters { display: flex; flex-direction: column; gap: 6px; overflow-y: auto; }
  .filters input { min-height: 40px; background: var(--panel); border: 1px solid var(--rule); padding: 0 8px; }
  .types { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }
  .chip { min-height: 32px; padding: 0 8px; font-size: var(--label); text-transform: none; color: var(--muted); }
  .chip.on { color: var(--text); border-color: var(--council); }
  .hint { color: var(--faint); font-size: var(--label); }
  .results { overflow-y: auto; min-height: 0; display: flex; flex-direction: column; gap: 6px; }
  .hit {
    display: flex; flex-direction: column; align-items: flex-start; gap: 4px; text-align: left; text-transform: none;
    letter-spacing: 0; padding: 10px 12px; min-height: 0; border: 1px solid var(--rule); animation: push var(--push) ease-out both;
    flex: none;
  }
  .hit.sel { border-color: var(--council); }
  .tag { font-size: var(--label); letter-spacing: 0.12em; border: 1px solid currentColor; padding: 0 6px; }
  .tag.vault { color: var(--muted); }
  .tag.graph { color: var(--council); }
  .tag.both { color: var(--filed); }
  .t { font-family: var(--cond); font-weight: 700; font-size: 16px; color: var(--text); }
  .meta { color: var(--faint); font-size: var(--label); }
  .chunk { color: var(--council); opacity: 0.85; }
  .side { display: grid; grid-template-rows: minmax(380px, 1.1fr) minmax(220px, 1fr); gap: 16px; min-height: 0; }
  .gbox { display: flex; flex-direction: column; min-height: 0; }
  .ghead { display: flex; justify-content: space-between; margin-bottom: 4px; }
  .gwrap { flex: 1; min-height: 220px; position: relative; overflow: hidden; }
  .why { color: var(--root); margin: 2px 0 6px; font-size: var(--label); }
  .legend { display: flex; flex-wrap: wrap; gap: 12px; color: var(--faint); font-size: var(--label); margin-top: 6px; }
  .legend i { display: inline-block; width: 9px; height: 9px; margin-right: 5px; vertical-align: middle; }
  .ci { border-radius: 50%; }
  .di { transform: rotate(45deg) scale(0.8); }
  .decision { background: var(--finch); }
  .project { background: var(--council); }
  .thing { background: var(--muted); }
  .event { background: var(--root); }
  .god { background: var(--council); }
  .note { border: 1.5px solid var(--muted); }
  .scrub { display: grid; grid-template-columns: 1fr; gap: 2px; margin-top: 8px; }
  .scrub input { width: 100%; accent-color: var(--council); }
  .insp { overflow-y: auto; min-height: 0; }
  h2 { font-family: var(--cond); font-size: 22px; margin: 4px 0; }
  .path { color: var(--faint); margin-bottom: 8px; overflow-wrap: anywhere; }
  .actions { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
  .btn { display: inline-flex; align-items: center; min-height: 44px; padding: 0 16px; border: 1px solid var(--council); color: var(--council); text-decoration: none; text-transform: uppercase; letter-spacing: 0.08em; }
  .body { white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--mono); font-size: var(--panel-size); margin: 0 0 10px; color: var(--text); }
  .hist { color: var(--muted); }
  details summary { cursor: pointer; margin: 6px 0; }
  .links { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
  .link { min-height: 32px; text-transform: none; letter-spacing: 0; color: var(--council); padding: 0 8px; }
  .empty { color: var(--faint); }
  @media (max-width: 1100px) {
    .grid { grid-template-columns: 1fr; overflow-y: auto; }
    .filters { flex-direction: row; flex-wrap: wrap; align-items: center; }
    .side { grid-template-rows: 460px auto; }
  }
</style>
