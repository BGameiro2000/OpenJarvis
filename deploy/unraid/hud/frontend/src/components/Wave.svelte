<script>
  // Decorative waveform: live while the persona speaks, flat otherwise.
  import { css } from '../lib/css.js';
  let { active = false } = $props();
  const bars = Array.from({ length: 28 }, (_, i) => 0.35 + 0.65 * Math.abs(Math.sin(i * 1.7) * Math.cos(i * 0.6)));
  const scale = bars.map((h) => `scaleY(${h.toFixed(2)})`);
</script>

<div class="wave" class:active aria-hidden="true">
  {#each scale as s, i}
    <span use:css={{ transform: s, 'animation-delay': `${(i % 7) * 90}ms` }}></span>
  {/each}
</div>

<style>
  .wave { display: flex; align-items: center; gap: 3px; height: 32px; }
  span { width: 4px; height: 100%; background: currentColor; opacity: 0.25; transform-origin: center; }
  .active span { opacity: 1; animation: talk 700ms ease-in-out infinite alternate; }
  @keyframes talk { from { transform: scaleY(0.2); } to { transform: scaleY(1); } }
</style>
