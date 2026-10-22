// Set CSS properties through the CSSOM. The CSP has no 'unsafe-inline' for
// styles, and CSSOM writes are allowed where style attributes are not.
export function css(node, props) {
  const set = (p) => {
    for (const [k, v] of Object.entries(p || {})) node.style.setProperty(k, v);
  };
  set(props);
  return { update: set };
}
