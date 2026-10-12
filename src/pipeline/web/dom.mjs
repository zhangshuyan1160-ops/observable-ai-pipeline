// Reconcile the small native views without replacing focused controls, open
// details or scroll containers on each poll. Keys identify task/step/attempt IDs.
const key = (node) =>
  node.nodeType === 1 ? node.id || node.getAttribute("data-key") : null;
const compatible = (a, b) =>
  a &&
  a.nodeType === b.nodeType &&
  a.nodeName === b.nodeName &&
  key(a) === key(b);

function patch(target, source) {
  if (target.nodeType !== 1) {
    if (target.nodeValue !== source.nodeValue)
      target.nodeValue = source.nodeValue;
    return;
  }
  for (const attribute of [...target.attributes]) {
    if (
      target.hasAttribute("data-busy") &&
      ["disabled", "data-busy"].includes(attribute.name)
    )
      continue;
    if (target.tagName === "DETAILS" && attribute.name === "open") continue;
    if (!source.hasAttribute(attribute.name))
      target.removeAttribute(attribute.name);
  }
  for (const attribute of source.attributes) {
    if (target.getAttribute(attribute.name) !== attribute.value)
      target.setAttribute(attribute.name, attribute.value);
  }
  if (!source.hasAttribute("data-live-island")) reconcile(target, source);
}

function reconcile(target, source) {
  let cursor = target.firstChild;
  for (const next of [...source.childNodes]) {
    let match = compatible(cursor, next) ? cursor : null;
    if (!match && key(next)) {
      match = [...target.childNodes].find((node) => compatible(node, next));
    }
    if (match) {
      if (match !== cursor) target.insertBefore(match, cursor);
      patch(match, next);
      cursor = match.nextSibling;
    } else {
      target.insertBefore(next.cloneNode(true), cursor);
    }
  }
  while (cursor) {
    const next = cursor.nextSibling;
    cursor.remove();
    cursor = next;
  }
}

export function renderHTML(target, html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  reconcile(target, template.content);
}
