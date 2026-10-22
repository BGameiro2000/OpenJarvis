<script>
  // Two hop graph with Cytoscape. Breadth-first layout from the centre node,
  // no randomness, so the same note always gives the same picture.
  import { onDestroy, untrack } from 'svelte';
  import cytoscape from 'cytoscape';

  let { data = null, range = null, selected = '', onselect = () => {} } = $props();
  let el = $state();
  let cy;
  let built = $state(0);

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function style() {
    const c = {
      finch: css('--finch'), root: css('--root'), council: css('--council'), muted: css('--muted'),
      faint: css('--faint'), rule: css('--rule'), ground: css('--ground'), text: css('--text'),
    };
    return [
      { selector: 'node', style: {
        'background-color': c.faint, width: 14, height: 14, label: 'data(label)', color: c.muted,
        'font-family': 'IBM Plex Mono, monospace', 'font-size': 10, 'text-valign': 'bottom',
        'text-margin-y': 4, 'text-max-width': 120, 'text-wrap': 'ellipsis', 'min-zoomed-font-size': 7,
      } },
      { selector: 'node[kind = "decision"]', style: { shape: 'rectangle', 'background-color': c.finch } },
      { selector: 'node[kind = "project"]', style: { shape: 'ellipse', 'background-color': c.council } },
      { selector: 'node[kind = "thing"]', style: { shape: 'ellipse', 'background-color': c.muted } },
      { selector: 'node[kind = "event"]', style: { shape: 'rectangle', 'background-color': c.root } },
      { selector: 'node[kind = "god"]', style: { shape: 'diamond', 'background-color': c.council } },
      { selector: 'node[kind = "note"]', style: { shape: 'round-rectangle', 'background-color': c.ground, 'border-width': 1.5, 'border-color': c.muted } },
      { selector: 'node.center', style: { width: 22, height: 22, color: c.text, 'font-size': 11 } },
      { selector: 'node.selected', style: { 'border-width': 2, 'border-color': c.council, color: c.text } },
      { selector: 'node.out', style: { opacity: 0.18 } },
      { selector: 'edge', style: { width: 1, 'line-color': c.rule, 'curve-style': 'straight' } },
      { selector: 'edge.path', style: { width: 2, 'line-color': c.council } },
      { selector: 'node.path', style: { color: c.text } },
    ];
  }

  function pathTo(target) {
    // Breadth-first path from the centre to the target, for the council blue path.
    if (!cy || !data?.center || !target) return;
    cy.elements().removeClass('path');
    const root = cy.getElementById(data.center);
    const goal = cy.getElementById(target);
    if (!root.nonempty() || !goal.nonempty()) return;
    const res = cy.elements().aStar({ root, goal });
    if (res.found) res.path.addClass('path');
  }

  $effect(() => {
    if (!el || !data) return;
    if (cy) cy.destroy();
    const ids = new Set(data.nodes.map((n) => n.id));
    cy = cytoscape({
      container: el,
      elements: [
        ...data.nodes.map((n) => ({ data: { ...n }, classes: n.id === data.center ? 'center' : '' })),
        ...data.edges.filter((e) => ids.has(e.source) && ids.has(e.target))
          .map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, label: e.label } })),
      ],
      style: style(),
      layout: data.center
        ? {
            // Rings by hop count, ordered by id: same input, same picture.
            name: 'concentric', concentric: (n) => 10 - (n.data('depth') || 0), levelWidth: () => 1,
            minNodeSpacing: 46, animate: false, padding: 28, sort: (a, b) => (a.id() < b.id() ? -1 : 1),
          }
        : { name: 'grid', animate: false },
      boxSelectionEnabled: false,
    });
    cy.on('tap', 'node', (e) => onselect(e.target.data()));
    untrack(() => built++);
  });

  $effect(() => {
    built;
    if (!cy) return;
    cy.nodes().removeClass('selected');
    if (selected) {
      cy.getElementById(selected).addClass('selected');
      pathTo(selected);
    }
  });

  $effect(() => {
    built;
    if (!cy) return;
    const [from, to] = range || [];
    cy.nodes().forEach((n) => {
      const d = n.data('date');
      n.toggleClass('out', !!(from && d && (d < from || d > to)));
    });
  });

  onDestroy(() => cy?.destroy());
</script>

<div class="graph" bind:this={el} role="img" aria-label="Two hop graph around the selected note"></div>

<style>
  .graph { position: absolute; inset: 0; background: var(--panel); }
</style>
