<script>
  // Search snippet with highlights. The backend marks matches with \u0001 and
  // \u0002; they become <mark> elements. Text is always text, never HTML.
  let { text = '' } = $props();
  let parts = $derived.by(() => {
    const out = [];
    for (const seg of text.split('\u0001')) {
      const [hit, rest] = seg.includes('\u0002') ? seg.split('\u0002') : [null, seg];
      if (hit !== null) out.push({ hit: true, t: hit });
      if (rest) out.push({ hit: false, t: rest });
    }
    return out;
  });
</script>

<span class="snip">{#each parts as p}{#if p.hit}<mark>{p.t}</mark>{:else}{p.t}{/if}{/each}</span>

<style>
  mark { background: transparent; color: var(--finch); border-bottom: 1px solid var(--council); }
  .snip { color: var(--muted); }
</style>
