<script>
  // Intake board (guide section 7). Everything shown comes from the vault
  // (read-only) or the sorter; every action is a call to the sorter.
  import { onMount } from 'svelte';
  import Box from '../components/Box.svelte';
  import { getJSON, postJSON, fmtSize, fmtTime } from '../lib/api.js';
  import { hud } from '../lib/hud.svelte.js';

  const ROUTES = ['project_shaped', 'gtd_action', 'idea_capture', 'reference', 'escalate'];
  let board = $state(null);
  let plan = $state(null);
  let busy = $state('');
  let msg = $state('');
  let err = $state('');
  let note = $state('');
  let files = $state();
  let dragging = $state(false);
  let editing = $state({}); // id -> 'edit' | 'list'
  let drafts = $state({}); // id -> envelope JSON text or new list fields

  let sorter = $derived(board?.sorter);
  let dry = $derived(sorter ? sorter.dry_run : true);
  let items = $derived(plan?.items || sorter?.pending || []);
  let routesToday = $derived(board?.summary?.routes_today || {});

  async function load() {
    try {
      board = await getJSON('/api/intake');
      err = '';
    } catch (x) {
      err = String(x.message || x);
    }
  }
  onMount(load);
  // Reload when the sorter reports routes, escalations or files.
  $effect(() => {
    if (hud.intakeTick) load();
  });

  async function scan(apply) {
    if (apply && !confirm('Apply: file everything that passes validation and move the originals to inbox/archive. Continue?')) return;
    busy = apply ? 'apply' : 'dry';
    msg = '';
    try {
      plan = await postJSON('/api/intake/scan', { apply });
      msg = `${apply ? 'Applied' : 'Dry run'}: ` + Object.entries(plan.summary).map(([k, v]) => `${k} ${v}`).join(' · ');
      await load();
    } catch (x) {
      err = String(x.message || x);
    } finally {
      busy = '';
    }
  }

  async function decide(it, action) {
    const body = { id: it.id, action };
    try {
      if (action === 'edit') body.envelope = JSON.parse(drafts[it.id]);
      if (action === 'add_list') body.list = drafts[it.id];
    } catch {
      err = 'The envelope is not valid JSON.';
      return;
    }
    busy = it.id;
    try {
      const r = await postJSON('/api/intake/decide', body);
      msg = `${it.source}: ${r.decision}`;
      editing = { ...editing, [it.id]: '' };
      plan = null;
      await load();
    } catch (x) {
      err = `${it.source}: ${x.message || x}`;
    } finally {
      busy = '';
    }
  }

  function startEdit(it) {
    drafts[it.id] = JSON.stringify(it.envelope, null, 1);
    editing = { ...editing, [it.id]: 'edit' };
  }
  function startList(it) {
    drafts[it.id] = { name: '', route: it.envelope.route !== 'escalate' ? it.envelope.route : 'reference', scope: '' };
    editing = { ...editing, [it.id]: 'list' };
  }

  async function upload(list) {
    if (!list?.length) return;
    const fd = new FormData();
    for (const f of list) fd.append('file', f, f.name);
    busy = 'upload';
    try {
      const r = await fetch('/api/intake/upload', { method: 'POST', body: fd, credentials: 'same-origin' });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(b.detail || r.status);
      msg = `Captured: ${b.saved.join(', ')}`;
      await load();
    } catch (x) {
      err = String(x.message || x);
    } finally {
      busy = '';
      if (files) files.value = '';
    }
  }

  async function capture(e) {
    e.preventDefault();
    if (!note.trim()) return;
    busy = 'note';
    try {
      const r = await postJSON('/api/intake/note', { text: note });
      msg = `Captured: ${r.saved.join(', ')}`;
      note = '';
      await load();
    } catch (x) {
      err = String(x.message || x);
    } finally {
      busy = '';
    }
  }
  const ok = (c) => (c.ok ? '✓' : '✗');
  // The envelope as is, one key per line, tags kept on one line.
  const envText = (env) =>
    JSON.stringify({ ...env, tags: '\u0000' }, null, 1).replace('"\\u0000"', JSON.stringify(env.tags ?? []));
</script>

<div class="intake">
  <header class="head">
    <div>
      <div class="label">INTAKE · CAPTURE, CLASSIFY, VALIDATE, FILE</div>
      <div class="status">
        {#if sorter}
          <span class="badge" class:dry>{dry ? 'DRY RUN · NOTHING MOVES UNTIL APPLY' : 'LIVE · SCHEDULE ON'}</span>
          <span>transcription {sorter.transcription?.available ? 'on (' + sorter.transcription.backend + ')' : 'off'}</span>
          {#if sorter.last_scan}<span>last scan {fmtTime(sorter.last_scan.at)}{sorter.last_scan.dry_run ? ' (dry run)' : ''}</span>{/if}
        {:else if board}
          <span class="badge alert">SORTER NOT REACHABLE</span><span>{board.sorter_reason}. The board below is read from the vault.</span>
        {/if}
      </div>
    </div>
    <div class="acts">
      <button disabled={!sorter || !!busy} onclick={() => scan(false)}>{busy === 'dry' ? 'Running' : 'Dry run now'}</button>
      <button class="primary" disabled={!sorter || !!busy} onclick={() => scan(true)}>{busy === 'apply' ? 'Filing' : 'Apply'}</button>
    </div>
  </header>
  {#if msg}<p class="msg">{msg}</p>{/if}
  {#if err}<p class="msg alert">{err}</p>{/if}

  <div class="top">
    <section
      class="drop"
      class:dragging
      role="region"
      aria-label="Add files to the inbox"
      ondragover={(e) => { e.preventDefault(); dragging = true; }}
      ondragleave={() => (dragging = false)}
      ondrop={(e) => { e.preventDefault(); dragging = false; upload(e.dataTransfer.files); }}
    >
      <div class="label">CAPTURE</div>
      <p>Drop notes, chat exports (WhatsApp, Telegram), AI exports (Claude, ChatGPT), zips or voice memos. They land in <code>inbox/</code> untouched.</p>
      <label class="pick">
        <input bind:this={files} type="file" multiple onchange={(e) => upload(e.currentTarget.files)} />
        <span>{busy === 'upload' ? 'Uploading' : 'Choose files'}</span>
      </label>
      <form onsubmit={capture}>
        <textarea bind:value={note} rows="3" maxlength="100000" placeholder="Quick note: one thought, one line why" aria-label="Quick note"></textarea>
        <button disabled={!note.trim() || !!busy}>Capture note</button>
      </form>
    </section>
    <section class="counts">
      <div class="label">ROUTES TODAY</div>
      <div class="routes">
        {#each ROUTES as r}<div class="r {r}"><b>{routesToday[r] || 0}</b><span>{r}</span></div>{/each}
      </div>
      <div class="sum">
        <span>inbox <b>{board?.summary?.inbox ?? '–'}</b></span>
        <span>filed today <b>{board?.summary?.filed_today ?? '–'}</b></span>
        <span class="alert">escalated <b>{board?.summary?.escalated ?? '–'}</b></span>
      </div>
    </section>
  </div>

  <section class="pipe" aria-label="Pipeline">
    <div class="row hdr"><span class="label">CAPTURE</span><span class="label">CLASSIFY</span><span class="label">VALIDATE</span><span class="label">FILE</span></div>
    {#if !items.length}
      <p class="empty">{plan ? 'Nothing to do.' : 'Run a dry run to see what the sorter would do with each file.'} Inbox: {(board?.inbox || []).map((i) => i.name).join(', ') || 'empty'}.</p>
    {/if}
    {#each items as it (it.id)}
      <div class="row" class:esc={it.decision === 'escalate'}>
        <div>
          <b class="src">{it.source}</b>
          <span class="kind">{it.kind}{it.count > 1 ? ` · ${it.count} items` : ''}</span>
          <p class="prev">{it.preview}</p>
        </div>
        <div>
          <span class="by">{it.decided_by}</span>
          <pre class="env">{envText(it.envelope)}</pre>
        </div>
        <div class="checks">
          {#each it.checks as c}<div class:bad={!c.ok}><span>{ok(c)}</span> {c.name}{c.detail ? ` (${c.detail})` : ''}</div>{/each}
        </div>
        <div>
          {#if it.decision === 'escalate'}
            <span class="alert">ESCALATE</span>
            <p class="reason">{it.reason}</p>
          {:else}
            <span class="filed">{it.decision.toUpperCase()}</span>
            {#each it.actions as a}<div class="act"><i>{a.op}</i> {a.path}</div>{/each}
          {/if}
        </div>
      </div>
    {/each}
  </section>

  {#if sorter?.pending?.length}
    <section class="escal">
      <div class="label alert">ESCALATIONS · NEED A HUMAN</div>
      <div class="cards">
        {#each sorter.pending as it (it.id)}
          <Box tone="alert">
            <b class="src">{it.source}</b>
            <p class="reason">{it.reason}</p>
            <p class="prev">{it.preview}</p>
            {#if editing[it.id] === 'edit'}
              <textarea class="code" rows="8" bind:value={drafts[it.id]} aria-label="Envelope"></textarea>
              <div class="btns"><button class="primary" disabled={busy === it.id} onclick={() => decide(it, 'edit')}>File with this envelope</button><button onclick={() => (editing[it.id] = '')}>Cancel</button></div>
            {:else if editing[it.id] === 'list'}
              <label>List name <input bind:value={drafts[it.id].name} placeholder="travel.md" /></label>
              <label>Route <select bind:value={drafts[it.id].route}>{#each ROUTES.slice(0, 4) as r}<option>{r}</option>{/each}</select></label>
              <label>Scope (one line, the classifier sees it word for word) <input bind:value={drafts[it.id].scope} /></label>
              <div class="btns"><button class="primary" disabled={busy === it.id} onclick={() => decide(it, 'add_list')}>Add list and file</button><button onclick={() => (editing[it.id] = '')}>Cancel</button></div>
            {:else}
              <div class="btns">
                <button class="primary" disabled={busy === it.id || it.envelope.route === 'escalate'} onclick={() => decide(it, 'file')}>File as proposed</button>
                <button onclick={() => startList(it)}>Add a list</button>
                <button onclick={() => startEdit(it)}>Edit envelope</button>
                <button onclick={() => decide(it, 'leave')}>Leave in inbox</button>
              </div>
            {/if}
          </Box>
        {/each}
      </div>
    </section>
  {/if}

  <div class="bottom">
    <section>
      <div class="label">CLOSED LIST MENU · WHAT THE CLASSIFIER SEES</div>
      <table>
        <thead><tr><th>list</th><th>route</th><th>folder</th><th>scope</th></tr></thead>
        <tbody>
          {#each sorter?.menu || [] as m}<tr><td>{m.list}</td><td>{m.route}</td><td>{m.folder}</td><td class="scope">{m.scope}</td></tr>{/each}
        </tbody>
      </table>
    </section>
    <section>
      <div class="label">DECISION LOG · inbox/.decision-log.jsonl</div>
      <table>
        <thead><tr><th>time</th><th>source</th><th>decision</th><th>route</th><th>conf</th><th>by</th></tr></thead>
        <tbody>
          {#each board?.log || [] as r}
            <tr class:dryrow={r.dry_run}>
              <td>{String(r.ts || '').replace('T', ' ').slice(5, 16)}</td>
              <td class="scope" title={r.why}>{r.source}{r.items > 1 ? ` (${r.items})` : ''}</td>
              <td class={r.decision === 'escalated' ? 'alert' : r.decision === 'filed' ? 'filed' : ''}>{r.decision}{r.dry_run ? ' (dry)' : ''}</td>
              <td>{r.route || ''}{r.target_list ? ' → ' + r.target_list : ''}</td>
              <td>{typeof r.confidence === 'number' ? r.confidence.toFixed(2) : ''}</td>
              <td>{r.by || ''}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </section>
  </div>
</div>

<style>
  .intake { flex: 1; min-height: 0; overflow-y: auto; padding: 16px 24px 32px; display: flex; flex-direction: column; gap: 16px; }
  .head { display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
  .status { display: flex; gap: 14px; flex-wrap: wrap; color: var(--muted); margin-top: 6px; align-items: center; }
  .badge { border: 1px solid var(--filed); color: var(--filed); padding: 2px 8px; font-size: var(--label); letter-spacing: var(--track); }
  .badge.dry { border-color: var(--root); color: var(--root); }
  .badge.alert { border-color: var(--alert); }
  .acts { display: flex; gap: 8px; }
  .msg { margin: 0; color: var(--filed); }
  .alert { color: var(--alert); }
  .filed { color: var(--filed); }
  .top { display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px; }
  .drop { border: 1px dashed var(--rule); padding: 14px; display: flex; flex-direction: column; gap: 8px; }
  .drop.dragging { border-color: var(--council); }
  .drop p { margin: 0; color: var(--muted); }
  .pick input { position: absolute; opacity: 0; width: 1px; height: 1px; }
  .pick span { display: inline-flex; align-items: center; min-height: 44px; padding: 0 16px; border: 1px solid var(--council); color: var(--council); text-transform: uppercase; letter-spacing: 0.08em; cursor: pointer; }
  .pick:focus-within span { outline: 2px solid var(--council); outline-offset: 2px; }
  form { display: flex; gap: 8px; align-items: stretch; }
  textarea { flex: 1; background: var(--panel); border: 1px solid var(--rule); padding: 8px; font: inherit; color: inherit; resize: vertical; }
  .routes { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; margin: 8px 0; }
  .r { border: 1px solid var(--rule); padding: 6px; display: flex; flex-direction: column; font-size: var(--label); color: var(--muted); }
  .r b { font-family: var(--cond); font-size: 24px; color: var(--text); font-weight: 500; }
  .r.escalate b { color: var(--alert); }
  .r.reference b { color: var(--reference); }
  .sum { display: flex; gap: 16px; color: var(--muted); }
  .sum b { color: var(--text); }
  .pipe { display: flex; flex-direction: column; border-top: 1px solid var(--rule); }
  .row { display: grid; grid-template-columns: 1.2fr 1.2fr 1fr 1fr; gap: 14px; padding: 10px 0; border-bottom: 1px solid var(--rule); animation: push var(--push) ease-out both; }
  .row.hdr { padding: 8px 0; animation: none; }
  .row.esc { background: color-mix(in srgb, var(--alert) 5%, transparent); }
  .src { overflow-wrap: anywhere; }
  .kind, .by { display: block; color: var(--council); font-size: var(--label); letter-spacing: 0.06em; }
  .prev { color: var(--muted); margin: 4px 0 0; overflow-wrap: anywhere; max-height: 5.5em; overflow: hidden; }
  .env { margin: 4px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--text); font-size: 11px; background: var(--panel); padding: 6px; }
  .checks div { color: var(--filed); }
  .checks div.bad { color: var(--alert); }
  .act { color: var(--muted); overflow-wrap: anywhere; }
  .act i { font-style: normal; color: var(--faint); }
  .reason { color: var(--text); margin: 4px 0; }
  .empty { color: var(--faint); }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; margin-top: 10px; }
  .btns { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .code { width: 100%; font-family: var(--mono); font-size: 11px; }
  label { display: flex; flex-direction: column; gap: 2px; color: var(--muted); margin-top: 6px; }
  label input, label select { min-height: 40px; background: var(--panel); border: 1px solid var(--rule); padding: 0 8px; }
  .bottom { display: grid; grid-template-columns: 1fr 1.3fr; gap: 24px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  th { text-align: left; color: var(--faint); font-weight: 400; font-size: var(--label); letter-spacing: 0.1em; border-bottom: 1px solid var(--rule); padding: 4px 6px; }
  td { padding: 4px 6px; border-bottom: 1px solid var(--rule); vertical-align: top; }
  .scope { color: var(--muted); overflow-wrap: anywhere; }
  .dryrow td { color: var(--faint); }
  code { color: var(--text); }
  @media (max-width: 1000px) {
    .top, .bottom { grid-template-columns: 1fr; }
    .row { grid-template-columns: 1fr 1fr; }
    .row.hdr { display: none; }
  }
</style>
