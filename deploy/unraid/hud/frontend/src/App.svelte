<script>
  import { onMount } from 'svelte';
  import TopBar from './components/TopBar.svelte';
  import Main from './views/Main.svelte';
  import Memory from './views/Memory.svelte';
  import Intake from './views/Intake.svelte';
  import Council from './views/Council.svelte';
  import { connect } from './lib/hud.svelte.js';
  import { nav } from './lib/route.svelte.js';

  onMount(connect);
</script>

<div class="shell">
  <TopBar />
  {#if nav.view === 'memory'}
    <Memory />
  {:else if nav.view === 'intake'}
    <Intake />
  {:else if nav.view === 'council'}
    <Council />
  {:else}
    <Main />
  {/if}
</div>

<style>
  .shell { display: flex; flex-direction: column; height: 100vh; height: 100dvh; }
  @media (max-width: 900px) { .shell { height: auto; min-height: 100dvh; } }
</style>
