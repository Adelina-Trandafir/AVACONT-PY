// Keep existing node elements when an inline tree changes. Loading a branch
// must not restart transitions, replace focused buttons or redraw its siblings.
function updateAttributes(current, next) {
  for (const attribute of [...current.attributes]) {
    if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
  }
  for (const attribute of next.attributes) {
    if (current.getAttribute(attribute.name) !== attribute.value) current.setAttribute(attribute.name, attribute.value);
  }
}

function patchLevel(container, next) {
  const old = new Map([...container.children].map(wrapper => [wrapper.firstElementChild?.dataset.value, wrapper]));
  const kept = new Set();
  let cursor = container.firstElementChild;
  for (const desired of [...next.children]) {
    const id = desired.firstElementChild?.dataset.value;
    let wrapper = old.get(id);
    if (wrapper && id !== undefined) {
      const row = wrapper.firstElementChild, nextRow = desired.firstElementChild;
      updateAttributes(row, nextRow);
      // Only this changed row's content is replaced; identical sibling content
      // and complete open subtrees retain their DOM objects.
      if (row.innerHTML !== nextRow.innerHTML) row.innerHTML = nextRow.innerHTML;
      const children = wrapper.querySelector(':scope > .treeview-children');
      const nextChildren = desired.querySelector(':scope > .treeview-children');
      if (children && nextChildren) patchLevel(children, nextChildren);
      else if (children) children.remove();
      else if (nextChildren) wrapper.append(nextChildren);
    } else wrapper = desired;
    kept.add(wrapper);
    if (wrapper !== cursor) container.insertBefore(wrapper, cursor);
    cursor = wrapper.nextElementSibling;
  }
  for (const child of [...container.children]) if (!kept.has(child)) child.remove();
}

export function patchTree(container, html, indent, expanderWidth) {
  const template = document.createElement('template');
  template.innerHTML = html;
  for (const row of template.content.querySelectorAll('.treeview-item')) {
    const level = Number(row.dataset.level) || 1;
    const spacer = row.dataset.hasChildren === 'true' ? 0 : expanderWidth;
    row.style.setProperty('--tv-node-start', `${(level - 1) * indent + spacer}px`);
  }
  patchLevel(container, template.content);
}
