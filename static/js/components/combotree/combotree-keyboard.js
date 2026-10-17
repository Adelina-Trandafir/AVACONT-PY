// ComboTree owns keyboard focus separately from the shared TreeView behavior.
export function configureComboTreeKeyboard(combo) {
  const tree = combo.treeContainer;
  tree.tabIndex = -1;
  tree.setAttribute('role', 'tree');
  tree.setAttribute('aria-label', 'Opțiuni ComboTree');
  let returningFocus = false;
  const handleFocus = combo.handleFocus.bind(combo);
  combo.handleFocus = event => { if (!returningFocus) handleFocus(event); };

  const render = combo.renderTree.bind(combo);
  combo.renderTree = (...args) => {
    render(...args);
    tree.querySelectorAll('.treeview-item').forEach(row => {
      row.id = `${combo.instanceId}-node-${row.dataset.index}`;
      row.setAttribute('role', 'treeitem');
      row.setAttribute('aria-level', row.dataset.level);
      row.setAttribute('aria-selected', String(row.dataset.value === String(combo.selectedValue)));
      if (row.dataset.hasChildren === 'true') {
        row.setAttribute('aria-expanded', String(combo.expandedNodes.has(row.dataset.value)));
      }
    });
  };

  combo.updateHighlight = () => {
    tree.querySelectorAll('.treeview-item').forEach(row => {
      row.classList.toggle('highlighted', Number(row.dataset.index) === combo.highlightedIndex);
    });
    const row = tree.querySelector(`.treeview-item[data-index="${combo.highlightedIndex}"]`);
    if (row) {
      tree.setAttribute('aria-activedescendant', row.id);
      const bounds = tree.getBoundingClientRect(), target = row.getBoundingClientRect();
      const top = bounds.top + tree.clientTop, bottom = top + tree.clientHeight;
      // Follow each key immediately and scroll only the node list, not the page.
      if (target.top < top) tree.scrollTop -= top - target.top;
      else if (target.bottom > bottom) tree.scrollTop += target.bottom - bottom;
    } else tree.removeAttribute('aria-activedescendant');
  };
  const focusAt = index => {
    if (!combo.flattenedItems.length) return;
    combo.highlightedIndex = Math.max(0, Math.min(index, combo.flattenedItems.length - 1));
    combo.updateHighlight();
    tree.focus({ preventScroll: true });
  };
  const flushSearch = () => {
    clearTimeout(combo.searchTimeout);
    clearTimeout(combo.localSearchTimeout);
    const query = (combo.searchInput || combo.input).value;
    if (query !== combo.currentQuery) combo.performSearch(query);
  };

  // Re-rendering a branch keeps the same active node, or its closest visible ancestor.
  const toggle = combo.toggleNode.bind(combo);
  combo.toggleNode = (...args) => {
    const focused = document.activeElement === tree;
    const path = combo.flattenedItems[combo.highlightedIndex]?.path || [];
    toggle(...args);
    const visible = [...path].reverse().map(node =>
      combo.flattenedItems.findIndex(item => String(item.node.id) === String(node.id))).find(index => index >= 0);
    combo.highlightedIndex = visible ?? -1;
    combo.updateHighlight();
    if (focused) tree.focus({ preventScroll: true });
  };
  const click = combo.handleDropdownClick.bind(combo);
  combo.handleDropdownClick = event => {
    const row = event.target.closest('.treeview-item');
    if (row) {
      clearTimeout(combo.searchTimeout);
      clearTimeout(combo.localSearchTimeout);
      focusAt(Number(row.dataset.index));
    }
    click(event);
  };
  combo.selectHighlighted = () => {
    const row = tree.querySelector(`.treeview-item[data-index="${combo.highlightedIndex}"]`);
    // Enter uses the same path as a click on the row label, including checkboxes/parents.
    if (row) click({ target: row, shiftKey: false, stopPropagation() {} });
  };

  const moveHorizontal = key => {
    const item = combo.flattenedItems[combo.highlightedIndex];
    if (!item) return;
    const id = String(item.node.id), children = !!item.node.children?.length;
    if (key === 'ArrowRight') {
      if (!children) return;
      if (!combo.expandedNodes.has(id)) combo.toggleNode(id);
      else focusAt(combo.highlightedIndex + 1);
    } else if (children && combo.expandedNodes.has(id)) {
      combo.toggleNode(id);
    } else {
      const parent = item.path.at(-2);
      if (parent) focusAt(combo.flattenedItems.findIndex(row => String(row.node.id) === String(parent.id)));
    }
  };
  combo.handleKeydown = event => {
    const inTree = event.target === tree || tree.contains(event.target);
    const key = event.key;
    if (key === 'Tab') {
      combo.hide();
      if (event.target !== combo.input) {
        // The popup lives outside the form. Resume Tab from its original input.
        returningFocus = true;
        try { combo.input.focus({ preventScroll: true }); }
        finally { returningFocus = false; }
      }
      if (combo.options.onTab) combo.options.onTab(event, combo.input);
      if (combo.options.onTab || event.target !== combo.input) event.stopPropagation();
      return;
    }
    event.stopPropagation();
    if (event.isComposing) return;
    if (key === 'ArrowDown' || key === 'ArrowUp') {
      event.preventDefault();
      if (!combo.isVisible) combo.show();
      if (!inTree) flushSearch();
      const current = combo.highlightedIndex;
      focusAt(current < 0 ? (key === 'ArrowDown' ? 0 : combo.flattenedItems.length - 1)
        : current + (key === 'ArrowDown' ? 1 : -1));
    } else if (key === 'ArrowLeft' || key === 'ArrowRight') {
      // In an input the browser moves the caret/selection normally.
      if (inTree) { event.preventDefault(); moveHorizontal(key); }
    } else if (key === 'Enter') {
      event.preventDefault();
      if (!inTree) flushSearch();
      combo.selectHighlighted();
    } else if (key === 'Escape') {
      combo.hide();
    } else if (inTree) {
      const input = combo.searchInput || combo.input;
      if ((event.ctrlKey || event.metaKey) && ['v', 'x', 'a'].includes(key.toLowerCase())) {
        input.focus({ preventScroll: true });
        return; // native paste/cut/select-all now goes to the search input
      }
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (key.length !== 1 && !['Backspace', 'Delete', 'Home', 'End'].includes(key)) return;
      input.focus({ preventScroll: true });
      combo.highlightedIndex = -1;
      combo.updateHighlight();
      if (key === 'Home' || key === 'End') return;
      event.preventDefault();
      let start = input.selectionStart ?? input.value.length, end = input.selectionEnd ?? start;
      if (key === 'Backspace' && start === end) start = Math.max(0, start - 1);
      if (key === 'Delete' && start === end) end = Math.min(input.value.length, end + 1);
      input.setRangeText(key.length === 1 ? key : '', start, end, 'end');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  };
  combo.addDOMListener(tree, 'keydown', event => combo.handleKeydown(event));
  combo.addDOMListener(combo.treeElement, 'keydown', event => {
    if (event.key === 'Tab' && !event.defaultPrevented) combo.handleKeydown(event);
  });
  combo.addDOMListener(tree, 'compositionstart', () => (combo.searchInput || combo.input).focus());
}
