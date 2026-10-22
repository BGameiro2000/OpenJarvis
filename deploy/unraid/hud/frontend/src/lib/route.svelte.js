// Hash routing: #/ (main), #/memory, #/intake, #/council[/sim].
export const VIEWS = ['main', 'memory', 'intake', 'council'];
export const nav = $state({ view: 'main', arg: '' });

function parse() {
  const h = location.hash.replace(/^#\/?/, '');
  const [v, ...rest] = h.split('/');
  nav.view = VIEWS.includes(v) ? v : 'main';
  nav.arg = rest.length ? decodeURIComponent(rest.join('/')) : '';
}
window.addEventListener('hashchange', parse);
parse();

export function href(view, arg = '') {
  return '#/' + (view === 'main' ? '' : view) + (arg ? '/' + encodeURIComponent(arg) : '');
}
export function go(view, arg = '') {
  location.hash = href(view, arg);
}
