<script>
  // Relevance box: corner brackets only, never a full border.
  // tone: finch | root | council | alert | filed | unknown
  let { tone = 'unknown', dim = false, scan = true, children, class: cls = '' } = $props();
</script>

<div class="box {tone} {cls}" class:dim>
  <span class="c tl"></span><span class="c tr"></span><span class="c bl"></span><span class="c br"></span>
  {#if scan}<span class="scan"></span>{/if}
  {@render children?.()}
</div>

<style>
  .box { position: relative; padding: 20px 24px; --tone: var(--muted); transition: opacity 200ms; }
  .finch { --tone: var(--finch); }
  .root { --tone: var(--root); }
  .council { --tone: var(--council); }
  .alert { --tone: var(--alert); }
  .filed { --tone: var(--filed); }
  .unknown { --tone: var(--muted); }
  .dim { opacity: 0.4; }
  .c {
    position: absolute; width: var(--bracket); height: var(--bracket);
    border-color: var(--tone); border-style: solid; border-width: 0;
    animation: snap var(--snap) ease-out both;
  }
  .tl { top: 0; left: 0; border-top-width: var(--bracket-w); border-left-width: var(--bracket-w); }
  .tr { top: 0; right: 0; border-top-width: var(--bracket-w); border-right-width: var(--bracket-w); }
  .bl { bottom: 0; left: 0; border-bottom-width: var(--bracket-w); border-left-width: var(--bracket-w); }
  .br { bottom: 0; right: 0; border-bottom-width: var(--bracket-w); border-right-width: var(--bracket-w); }
  .scan {
    position: absolute; left: 0; right: 0; top: 0; height: 1px; background: var(--tone);
    pointer-events: none; animation: scan 420ms linear both;
  }
  @media (prefers-reduced-motion: reduce) { .scan { display: none; } }
</style>
