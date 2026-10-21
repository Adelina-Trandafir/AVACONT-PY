// Searchable tree selector with viewport positioning shared by forms and grid cells.
import { TreeView } from '../treeview/treeview.js';
import { layoutRect, layoutViewport } from '../../utils/layout-geometry.js';
import { configureComboTreeKeyboard } from './combotree-keyboard.js';

export class ComboTree extends TreeView {
  constructor(host, options = {}) {
    super(host, { autoCollapse: false, autoResize: false, selectableLevel: null, selectParents: false, includeDescendantsOnMatch: true, ...options });
    this.createElements();
    this.container.classList.add('combotree');
    this.arrow.textContent = '▾';
    this.treeElement.classList.add('combotree-popup');
    this.dropdown = this.treeElement;
    this.overlay = this.overlayElement;
    // Keep the search field over the closed control, independently of popup width.
    this.searchInput?.setAttribute('aria-label', this.options.searchPlaceholder);
    this.addDOMListener(this.treeElement, 'mousedown', event => {
      if (event.button === 0 && !event.target.closest('input, textarea, select, button, a[href]')) {
        // A tree row is not focusable. Without this, DGV commits on blur before click.
        event.preventDefault();
      }
    });
    this.handleDropdownClick = event => {
      event.stopPropagation();
      const expander = event.target.closest('.treeview-expander');
      if (expander) { this.toggleNode(expander.dataset.nodeId, event.shiftKey); return; }
      const item = event.target.closest('.treeview-item');
      if (!item) return;
      const id = item.dataset.value;
      if (this.options.checkable) {
        const box = event.target.closest('.treeview-check');
        if (box) this.toggleCheckNode(box.dataset.checkId);
        else if (item.dataset.hasChildren === 'true') this.toggleNode(id, event.shiftKey);
        else if (item.dataset.selectable !== 'false') this.toggleCheckNode(id);
        return;
      }
      if (this.canSelectComboItem(item)) this.selectFromItem(item);
      else if (item.dataset.hasChildren === 'true') this.toggleNode(id, event.shiftKey);
    };
    this.handleDropdownDoubleClick = event => {
      event.stopPropagation();
      if (this.options.checkable) return;
      if (event.target.closest('.treeview-expander')) return;
      const item = event.target.closest('.treeview-item');
      if (item && this.canSelectComboItem(item)) this.selectFromItem(item);
    };
    const surface = host.closest('dialog') || document.body;
    surface.append(this.treeElement, this.overlayElement);
    this.setData(options.nodes || []);
    if (options.checkable) {
      const hasCheckbox = this.hasCheckbox.bind(this);
      this.hasCheckbox = node => hasCheckbox(node) && node._depth > 1;
    }
    this.show = () => {
      if (this.disabled || this.isVisible) return;
      const surface = this.container.closest('dialog') || document.body;
      if (this.treeElement.parentElement !== surface) surface.append(this.treeElement, this.overlayElement);
      this.expandedNodes = new Set((this.options.expanded || []).map(String));
      this.currentQuery = this.localSearchQuery = '';
      this.highlightedIndex = -1;
      this.isTreeRendered = false;
      if (this.searchInput) this.searchInput.value = '';
      if (this.searchClear) this.searchClear.style.display = 'none';
      this.renderTree('');
      if (!this.areEventsBound) this.bindEvents();
      this.positionDropdown();
      this.treeElement.classList.toggle('show-below', this.fitsBelowContainer);
      this.treeElement.classList.toggle('show-above', !this.fitsBelowContainer);
      this.treeElement.classList.remove('hidden');
      this.treeElement.classList.add('visible');
      this.overlayElement.classList.remove('hidden');
      this.overlayElement.classList.add('visible');
      this.isVisible = true;
      this.lastShownAt = Date.now();
      // Synchronous focus: a delayed focus must not interrupt keyboard navigation.
      this.searchInput?.focus({ preventScroll: true });
    };
    this.positionDropdown = () => {
      const rect = layoutRect(this.input), gap = 6, margin = 8;
      const viewport = layoutViewport();
      this.rect = rect;
      const below = Math.max(0, viewport.height - rect.bottom - gap - margin);
      const above = Math.max(0, rect.top - gap - margin);
      const minimum = options.minHeight || 220, desired = options.dropdownHeight || 320;
      const down = below >= minimum || below >= above;
      const height = Math.min(Math.max(minimum, desired), down ? below : above);
      const width = Math.min(Math.max(rect.width, options.minWidth ?? 300), viewport.width - 2 * margin);
      Object.assign(this.treeElement.style, { height: `${height}px`, width: `${width}px`,
        left: `${Math.max(margin, Math.min(rect.left, viewport.width - width - margin))}px`,
        top: `${down ? rect.bottom + gap : rect.top - gap - height}px`,
        zIndex: String(Number(window.ZIndexManager?.getMax?.() || 1000) + 10000) });
      this.treeElement.style.setProperty('--combotree-input-left', `${rect.left}px`);
      this.treeElement.style.setProperty('--combotree-input-top', `${rect.top}px`);
      this.treeElement.style.setProperty('--combotree-input-width', `${rect.width}px`);
      this.treeElement.style.setProperty('--combotree-input-height', `${rect.height}px`);
      this.overlayElement.style.zIndex = String(Number(this.treeElement.style.zIndex) - 1);
      this.fitsBelowContainer = down;
    };
    this.resizeDropdown = () => { if (this.isVisible) this.positionDropdown(); };
    this.performSearch = async query => {
      if (!this.isVisible) this.show();
      this.currentQuery = query;
      if (this.searchInput) this.searchInput.value = query;
      this.treeContainer.replaceChildren();
      this.highlightedIndex = -1;
      this.flattenedItems = [];
      if (!query) this.expandedNodes = new Set((this.options.expanded || []).map(String));
      this.renderTreeWithData(query);
      if (!this.flattenedItems.length) this.renderNoResults();
      if (this.searchClear) this.searchClear.style.display = query ? 'block' : 'none';
      this.positionDropdown();
    };
    this.handleLocalSearch = event => {
      clearTimeout(this.localSearchTimeout);
      const query = event.target.value;
      this.localSearchTimeout = setTimeout(() => this.performSearch(query), this.options.searchDelay);
    };
    const clearSearch = this.clearLocalSearch.bind(this);
    this.clearLocalSearch = (...args) => {
      clearSearch(...args);
      this.expandedNodes = new Set((this.options.expanded || []).map(String));
    };
    const hide = this.hide.bind(this);
    this.hide = () => {
      clearTimeout(this.searchTimeout);
      clearTimeout(this.localSearchTimeout);
      hide();
    };
    configureComboTreeKeyboard(this);
    this._popupAbort = new AbortController();
    for (const event of ['resize', 'scroll']) window.addEventListener(event, () => {
      if (this.isVisible) this.positionDropdown();
    }, { capture: true, signal: this._popupAbort.signal });
  }

  setInputAppearance({ fontFamily, fontSize, fontWeight, fontStyle, lineHeight, letterSpacing, textAlign, fontVariantNumeric }) {
    // Computed `font` can be empty when tabular numerals are enabled.
    const font = `${fontStyle} ${fontWeight} ${fontSize}/${lineHeight} ${fontFamily}`;
    this.treeElement.style.setProperty('--combotree-input-font', font);
    this.treeElement.style.setProperty('--combotree-input-spacing', letterSpacing);
    this.treeElement.style.setProperty('--combotree-input-align', textAlign);
    this.treeElement.style.setProperty('--combotree-input-numerals', fontVariantNumeric);
  }

  // Parent selection is opt-in; expanders always only expand/collapse.
  canSelectComboItem(item) {
    const node = this._checkIndex.get(item.dataset.value);
    const hasChildren = node ? !!node.children?.length : item.dataset.hasChildren === 'true';
    return item.dataset.selectable !== 'false'
      && (this.options.selectParents || (Number(item.dataset.level) > 1 && !hasChildren))
      && (this.options.selectableLevel === null || Number(item.dataset.level) === this.options.selectableLevel);
  }

  destroy() { this._popupAbort.abort(); super.destroy(); }
}
