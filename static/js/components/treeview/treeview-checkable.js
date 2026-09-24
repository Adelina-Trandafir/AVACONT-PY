/**
 * Opt-in checkbox mode (`checkable: true`, slice 0075-04, decision D21).
 *
 * Only LEAVES carry state, and the result is exactly the ticked leaves. Two rules for
 * a node with children (operator, 22.09.2026), chosen per tree:
 *
 *   branchChecks: false (default) -- the node has NO box; clicking it opens or closes it.
 *   branchChecks: true            -- a node BELOW the top level has a box that speaks for
 *                                    the whole branch: all leaves ticked -> checked, some
 *                                    -> indeterminate, none -> unchecked; ticking it ticks
 *                                    or clears every leaf under it. Its label still opens
 *                                    and closes it. A top-level node never has a box.
 *
 * A branch box always speaks for its leaves in the FULL tree, even while the local
 * search is showing a filtered copy: what the box shows is what a click does.
 *
 * The dropdown stays open while boxes are ticked. The main input is read-only in
 * this mode and the count is shown as its placeholder: `show()` treats a non-empty
 * `input.value` as a search query, so the value itself must stay empty.
 */
export const treeViewCheckableMixin = {
  initCheckable() {
    this.checkedLeaves = new Set();
    this._checkIndex = new Map();
    this._checkIndexSource = null;
    this.checkboxWidth = 0;
    if (!this.options.checkable) return;

    this.basePlaceholder = this.options.placeholder;
    this.input.readOnly = true;
    this.container.classList.add('treeview-checkable');
    this.checkboxWidth =
      Math.max(window.getClassNumericProperty('treeview-check', 'width'), 0) +
      Math.max(window.getClassNumericProperty('treeview-check', 'margin-right'), 0);
  },

  /** Replaces the tree. Checks on codes that no longer exist are dropped. */
  setData(nodes) {
    this.updateResults(nodes || [], '');
    this.isTreeRendered = false;
    this._buildCheckIndex();
    for (const id of [...this.checkedLeaves]) {
      const node = this._checkIndex.get(id);
      if (!node || !this._isLeaf(node)) this.checkedLeaves.delete(id);
    }
    this.updateCheckSummary();
  },

  /** The checked leaves, in tree order. */
  getCheckedLeaves() {
    this._buildCheckIndex();
    const out = [];
    for (const [id, node] of this._checkIndex) {
      if (this._isLeaf(node) && this.checkedLeaves.has(id)) out.push(id);
    }
    return out;
  },

  setCheckedLeaves(ids) {
    this._buildCheckIndex();
    this.checkedLeaves = new Set();
    for (const raw of ids || []) {
      const id = String(raw);
      const node = this._checkIndex.get(id);
      if (node && this._isLeaf(node)) this.checkedLeaves.add(id);
    }
    this._afterCheckChange();
  },

  clearChecks() {
    this.checkedLeaves.clear();
    this._afterCheckChange();
  },

  /**
   * True when this node draws a box: every leaf, and -- under `branchChecks` -- every
   * branch below the top level. Answered from the full tree, not from the filtered copy
   * a local search renders, so a branch keeps its box while its children are hidden.
   */
  hasCheckbox(node) {
    this._buildCheckIndex();
    const full = this._checkIndex.get(String(node.id)) || node;
    if (this._isLeaf(full)) return true;
    return Boolean(this.options.branchChecks) && (full._depth || 1) > 1;
  },

  /** 'checked' | 'indeterminate' | 'unchecked' for any node id. */
  getCheckState(nodeId) {
    this._buildCheckIndex();
    const node = this._checkIndex.get(String(nodeId));
    if (!node) return 'unchecked';
    let on = 0;
    for (const leaf of node._leafIds) if (this.checkedLeaves.has(leaf)) on++;
    if (on === 0) return 'unchecked';
    return on === node._leafIds.length ? 'checked' : 'indeterminate';
  },

  /**
   * Ticks or clears a leaf, or -- under `branchChecks` -- every leaf of a branch.
   * A branch without a box is not a choice: nothing happens.
   */
  toggleCheckNode(nodeId) {
    this._buildCheckIndex();
    const node = this._checkIndex.get(String(nodeId));
    if (!node || !this.hasCheckbox(node) || node._leafIds.length === 0) return;
    const clear = this.getCheckState(node.id) === 'checked';
    for (const leaf of node._leafIds) {
      if (clear) this.checkedLeaves.delete(leaf);
      else this.checkedLeaves.add(leaf);
    }
    this._afterCheckChange();
  },

  /** The box of one row, or nothing for a branch that has none. */
  renderCheckbox(node) {
    if (!this.hasCheckbox(node)) return '';
    const id = String(node.id);
    const state = this.getCheckState(id);
    const aria = state === 'indeterminate' ? 'mixed' : String(state === 'checked');
    return `<span class="treeview-check ${state}" role="checkbox" aria-checked="${aria}"
                  data-check-id="${this.escapeHtml(id)}"></span>`;
  },

  /** Repaints the boxes already on screen instead of re-rendering the tree. */
  refreshCheckboxes() {
    if (!this.treeContainer) return;
    this.treeContainer.querySelectorAll('.treeview-check').forEach((box) => {
      const state = this.getCheckState(box.dataset.checkId);
      box.classList.toggle('checked', state === 'checked');
      box.classList.toggle('indeterminate', state === 'indeterminate');
      box.classList.toggle('unchecked', state === 'unchecked');
      box.setAttribute(
        'aria-checked',
        state === 'indeterminate' ? 'mixed' : String(state === 'checked')
      );
    });
  },

  updateCheckSummary() {
    if (!this.options.checkable || !this.input) return;
    const count = this.checkedLeaves.size;
    const text = this.options.formatCheckSummary
      ? this.options.formatCheckSummary(count)
      : count === 0
      ? this.basePlaceholder
      : `${count} selectate`;
    this.input.value = '';
    this.input.placeholder = text;
    this.container.classList.toggle('has-checks', count > 0);
  },

  _afterCheckChange() {
    this.refreshCheckboxes();
    this.updateCheckSummary();
    if (this.options.onCheckChange) this.options.onCheckChange(this.getCheckedLeaves());
  },

  _isLeaf(node) {
    return !node.children || node.children.length === 0;
  },

  // Every node by id, depth-first (so Map order = tree order), each with the ids of
  // the leaves under it and its depth (1 = top level). Rebuilt only when `this.results`
  // is a different array.
  _buildCheckIndex() {
    if (this._checkIndexSource === this.results) return;
    this._checkIndex = new Map();
    const visit = (node, depth) => {
      const id = String(node.id);
      this._checkIndex.set(id, node);
      node._depth = depth;
      if (this._isLeaf(node)) {
        node._leafIds = [id];
      } else {
        node._leafIds = [];
        for (const child of node.children) node._leafIds.push(...visit(child, depth + 1));
      }
      return node._leafIds;
    };
    (this.results || []).forEach((node) => visit(node, 1));
    this._checkIndexSource = this.results;
  },
};
