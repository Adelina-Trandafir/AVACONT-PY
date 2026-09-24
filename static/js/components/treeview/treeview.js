/**
 * ========== TREEVIEW COMPONENT - Componentă pentru selecție arborescentă ==========
 * Versiune avansată cu auto-collapse, auto-resize, search integrat și breadcrumb
 *
 * CARACTERISTICI:
 * ✅ Structură arborescentă cu NIVELURI NELIMITATE
 * ✅ Auto-collapse ramuri (închide alte ramuri când deschizi una)
 * ✅ Auto-resize container
 * ✅ Search box integrat cu highlighting avansat
 * ✅ Breadcrumb header pentru navigare (afișează părintele când nu e vizibil)
 * ✅ Control nivel selecție (selectableLevel)
 * ✅ Mod double-click pentru selecție (requireDoubleClick)
 *
 * @version 4.1.0
 */
import ListenerTracker from '../../listener-tracker/listener-tracker-mixin.js';
import { treeViewUtilsMixin } from './treeview-utils.js';
import { treeViewUIMixin } from './treeview-ui.js';
import { treeViewSearchMixin } from './treeview-search.js';
import { treeViewRenderMixin } from './treeview-render.js';
import { treeViewNodesMixin } from './treeview-nodes.js';
import { treeviewInteractionsMixin } from './treeview-interactions.js';
import { treeViewBreadcrumbMixin } from './treeview-breadcrumb.js';
import { treeViewCheckableMixin } from './treeview-checkable.js';

export class TreeView {
  static instanceCounter = 0;

  constructor(container, options = {}) {
    // Generează ID unic pentru această instanță
    TreeView.instanceCounter++;
    this.instanceId = `treeview-${TreeView.instanceCounter}`;
    this.debugMode = true;

    // Elemente UI
    this.container = container;

    this.overlayElement = null;
    this.treeElement = null;
    this.treeContainer = null;
    this.tree = null;
    this.input = null;
    this.arrow = null;
    this.loader = null;
    this.miniHeader = null;

    this.searchWrapper = null;
    this.searchInput = null;
    this.searchClear = null;

    // Breadcrumb
    this.breadcrumbElement = null;
    this.breadcrumbInitialized = false;
    this.currentBreadcrumbParentId = null;
    this.currentBreadcrumbPath = null;
    this.currentBreadcrumbPathKey = null;

    this.maxRowWidth = 0;
    this.maxVisibleRows = 10;
    this.currentRowHeight = 0;
    this.containerFont = window.getComputedStyle(this.container).font;

    // Măsoară lățimea expander-ului
    this.expanderWidth =
      Math.max(window.getClassNumericProperty('treeview-expander', 'width'), 0) +
      Math.max(window.getClassNumericProperty('treeview-expander', 'margin-right'), 0) +
      Math.max(window.getClassNumericProperty('treeview-expander', 'margin-left'), 0);

    // Setează ID pe container dacă nu are deja
    if (!this.container.id) {
      this.container.id = `${this.instanceId}-container`;
    }

    // Aplică ListenerTracker mixin pentru cleanup automat
    ListenerTracker.applyTo(this, {
      debugMode: this.debugMode || false,
      logPrefix: `TreeView-${TreeView.instanceCounter}`,
      trackPerformance: true,
    });

    Object.assign(this, treeViewUtilsMixin);
    Object.assign(this, treeViewUIMixin);
    Object.assign(this, treeViewSearchMixin);
    Object.assign(this, treeViewRenderMixin);
    Object.assign(this, treeViewNodesMixin);
    Object.assign(this, treeviewInteractionsMixin);
    Object.assign(this, treeViewBreadcrumbMixin);
    Object.assign(this, treeViewCheckableMixin);

    this.options = {
      placeholder: options.placeholder || 'Selectați...',
      searchPlaceholder: options.searchPlaceholder || 'Căutare...',
      searchDelay: options.searchDelay || 300,
      onSearch: options.onSearch || null,
      onSelect: options.onSelect || null, // callback la selecție
      showLoader: options.showLoader !== false,
      minSearchLength: options.minSearchLength || 0,
      maxResults: options.maxResults || 100,
      highlightMatches: options.highlightMatches !== false,
      expandOnSearch: options.expandOnSearch !== false,
      dropdownHeight: options.dropdownHeight || 300,

      // OPȚIUNI NOI/ACTUALIZATE
      selectableLevel: options.selectableLevel || null,
      requireDoubleClick: options.requireDoubleClick || false,
      autoCollapse: options.autoCollapse !== false, // true by default
      autoResize: options.autoResize !== false, // true by default
      showSearchBox: options.showSearchBox !== false, // true by default
      showTwoRowsInInput: options.showTwoRowsInInput || false, // false by default
      checkable: options.checkable || false, // slice 0075-04: tri-state boxes, see treeview-checkable.js
      onCheckChange: options.onCheckChange || null, // (checkedLeafIds) => void
      formatCheckSummary: options.formatCheckSummary || null, // (count) => placeholder text
      ...options,
    };

    // State
    this.searchTimeout = null;
    this.currentQuery = '';
    this.localSearchQuery = '';
    this.selectedValue = null;
    this.selectedText = '';
    this.selectedValueSecondary = null;
    this.selectedTextSecondary = '';
    this.selectedPath = [];
    this.results = [];
    this.filteredResults = [];
    this.expandedNodes = new Set();
    this.matchedNodes = new Set();
    this.parentOfMatchedNodes = new Set();
    this.isVisible = false;
    this.disabled = false;
    this.highlightedIndex = -1;
    this.flattenedItems = [];
    this.localSearchTimeout = null;

    this.rect = null;
    this.fitsBelowContainer = false;

    // Pentru gestionare click/dblclick
    this.clickTimeout = null;
    this.clickDelay = 250;

    this.lastFocusTimestamp = 0;
    this.lastShownAt = 0;
    this.lastHiddenAt = 0;
    this.isAttachedToContainer = false;
    this.areEventsBound = false;
    this.areMiniHeaderEventsBound = false;
    this.isTreeRendered = false;
    this.isBreadCrumbVisible = false;
    this.init();
  }

  init() {
    this.attachToContainer();
    this.initCheckable();
    this.createOverlay();
  }

  attachToContainer() {
    if (this.isAttachedToContainer) return;

    this.container.innerHTML = `
    <input type="text" 
           id="${this.instanceId}-input"
           class="treeview-input" 
           placeholder="${this.options.placeholder}"
           autocomplete="off"
           spellcheck="false" />
    <span class="treeview-arrow" id="${this.instanceId}-arrow">🔽</span>
    <div class="treeview-loader" id="${this.instanceId}-loader"></div>
  `;

    this.container.classList.add('treeview-container');

    this.input = document.getElementById(`${this.instanceId}-input`);
    this.arrow = document.getElementById(`${this.instanceId}-arrow`);
    this.loader = document.getElementById(`${this.instanceId}-loader`);

    this.bindContainerEvents();
    if (this.options.showTwoRowsInInput) this.createMiniHeader();
    this.isAttachedToContainer = true;
  }

  createOverlay() {
    if (this.overlayElement) return;

    this.overlayElement = document.createElement('div');
    this.overlayElement.id = `${this.instanceId}-overlay`;
    this.overlayElement.className = 'treeview-overlay';
    this.overlayElement.classList.add('hidden');
    document.body.appendChild(this.overlayElement);

    this.addDOMListener(this.overlayElement, 'click', (e) => {
      e.stopPropagation();
      e.preventDefault();
      this.hide();
    });
  }

  createMiniHeader() {
    if (this.miniHeader) return;
    this.miniHeader = document.createElement('div');
    this.miniHeader.className = 'treeview-input-mini-header hidden';
    this.container.insertBefore(this.miniHeader, this.input);
    this.bindMiniHeaderEvents();
  }

  createElements() {
    const searchBoxHtml = this.options.showSearchBox
      ? `
      <div id="${this.instanceId}-search-wrapper" class="treeview-search-wrapper">
        <input type="text" 
               id="${this.instanceId}-search-input"
               class="treeview-search-input" 
               placeholder="${this.options.searchPlaceholder}"
               autocomplete="off" 
               style="font: ${this.containerFont};" />
        <span class="treeview-search-clear" 
              id="${this.instanceId}-search-clear"
              style="display: none;">✕</span>
      </div>
    `
      : '';

    this.treeElement = document.createElement('div');
    this.treeElement.id = `${this.instanceId}`;
    this.treeElement.className = 'treeview hidden';
    this.treeElement.innerHTML = `
    ${searchBoxHtml}
    <div id="${this.instanceId}-nodes-wrapper" class="treeview-dropdown">
    </div>
  `;
    this.treeElement.style.font = this.containerFont;
    document.body.appendChild(this.treeElement);

    this.tree = document.getElementById(`${this.instanceId}-tree`);

    if (this.options.showSearchBox) {
      this.searchWrapper = document.getElementById(`${this.instanceId}-search-wrapper`);
      this.searchInput = document.getElementById(`${this.instanceId}-search-input`);
      this.searchClear = document.getElementById(`${this.instanceId}-search-clear`);
      this.treeContainer = document.getElementById(`${this.instanceId}-nodes-wrapper`);
    } else {
      this.treeContainer = document.getElementById(`${this.instanceId}-nodes-wrapper`);
    }

    // Inițializează breadcrumb
    this.initBreadcrumb();
  }

  bindMiniHeaderEvents() {
    if (this.areMiniHeaderEventsBound || !this.miniHeader) return;
    this.addDOMListener(this.miniHeader, 'click', (e) => {
      e.stopPropagation();
      e.preventDefault();
      if (!this.isVisible) this.show(true);
    });
    this.areMiniHeaderEventsBound = true;
  }

  bindContainerEvents() {
    this.addDOMListener(this.input, 'input', (e) => this.handleInput(e));
    this.addDOMListener(this.input, 'keydown', (e) => this.handleKeydown(e));
    this.addDOMListener(this.input, 'focus', (e) => this.handleFocus(e));

    this.addDOMListener(this.arrow, 'click', () => {
      if (!this.isVisible) this.show();
    });
  }

  bindEvents() {
    if (this.areEventsBound) return;

    if (this.options.showSearchBox && this.searchInput) {
      this.addDOMListener(this.searchInput, 'input', (e) => this.handleLocalSearch(e));

      this.addDOMListener(this.searchInput, 'keydown', (e) => {
        if (
          ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Enter', 'Escape'].includes(e.key)
        ) {
          if (e.key === 'Escape' && this.localSearchQuery) {
            e.preventDefault();
            e.stopPropagation();
            this.clearLocalSearch();
            return;
          }
          this.handleKeydown(e);
        }
      });

      this.addDOMListener(this.searchClear, 'click', () => {
        this.clearLocalSearch();
        this.renderTreeWithData('', false);
        this.resizeDropdown();
      });
    }

    if (this.options.requireDoubleClick) {
      this.addDOMListener(this.treeElement, 'click', (e) => {
        if (e.target.classList.contains('treeview-expander')) {
          e.stopPropagation();
          const nodeId = e.target.dataset.nodeId;
          this.toggleNode(nodeId);
        } else if (e.target.parentElement.dataset.hasChildren == 'false') {
          this.handleDropdownClick(e);
        } else if (e.target.parentElement.dataset.hasChildren == 'true') {
          this.handleDropdownClick(e);
        }
      });
      this.addDOMListener(this.treeElement, 'dblclick', (e) => this.handleDropdownDoubleClick(e));
    } else {
      this.addDOMListener(this.treeElement, 'click', (e) => this.handleDropdownClick(e));
    }

    this.areEventsBound = true;
  }

  destroy() {
    if (this.searchTimeout) {
      clearTimeout(this.searchTimeout);
    }
    if (this.clickTimeout) {
      clearTimeout(this.clickTimeout);
    }
    if (this.localSearchTimeout) {
      clearTimeout(this.localSearchTimeout);
    }

    // Cleanup breadcrumb
    this.cleanupBreadcrumb();

    if (this.cleanup) {
      this.cleanupAllListeners();
      this.cleanup = null;
    }

    if (this.treeElement && this.treeElement.parentNode) {
      this.treeElement.parentNode.removeChild(this.treeElement);
    }

    this.container.innerHTML = '';
    this.container.classList.remove('treeview-container');

    if (this.overlayElement && this.overlayElement.parentNode) {
      this.overlayElement.parentNode.removeChild(this.overlayElement);
    }
  }

  getSelection() {
    if (!this.showTwoRowsInInput) {
      return {
        id: this.selectedValue,
        label: this.selectedText,
        path: this.selectedPath,
        fullPath: this.selectedPath ? this.selectedPath.map((p) => p.label).join(' > ') : '',
      };
    } else {
      return {
        primary: {
          id: this.selectedValue,
          label: this.selectedText,
        },
        secondary: {
          id: this.selectedValueSecondary,
          label: this.selectedTextSecondary,
        },
      };
    }
  }

  getSelectedValue() {
    return this.selectedValue;
  }

  getSelectedText() {
    return this.selectedText;
  }

  clear() {
    this.selectedValue = null;
    this.selectedText = '';
    this.selectedPath = [];
    this.input.value = '';
    this.hide();
  }

  setEnabled(enabled) {
    this.disabled = !enabled;
    this.input.disabled = !enabled;
    if (!enabled) {
      this.hide();
    }
    this.updateState();
  }

  setOptions(options) {
    this.options = { ...this.options, ...options };
    this.updateState();
  }

  setValue(value, text, path = null) {
    this.input.value = '';
    this.input.innerHTML = ''; // curăță conținutul

    this.selectedValue = value;
    this.selectedText = text;
    this.selectedPath = path || [{ id: value, label: text }];

    if (path && path.length > 1) {
      this.input.value = path.map((p) => p.label).join(' > ');
    } else {
      this.input.value = text;
    }
  }

  set2Values(value1, text1, value2, text2, path = null) {
    this.selectedValue = value1;
    this.selectedText = text1;
    this.selectedValueSecondary = value2;
    this.selectedTextSecondary = text2;
    this.selectedPath = path || [{ id: value1, label: text1 }];

    // Daca se trimite path și are mai mult de un nivel, afișează-l
    if (path && path.length > 1 && (!this.selectedValueSecondary || !this.selectedTextSecondary)) {
      this.input.value = path.map((p) => p.label).join(' > ');
    } else this.input.value = text1;

    if (
      this.options.showTwoRowsInInput &&
      this.selectedValueSecondary &&
      this.selectedTextSecondary
    ) {
      this.miniHeader.textContent = text2;
      this.miniHeader.classList.remove('hidden');
      this.miniHeader.dataset.value = value2;
    }
  }

  updateState() {
    this.container.classList.toggle('disabled', this.disabled);
  }

  showLoader() {
    if (this.options.showLoader) {
      this.loader.classList.add('show');
    }
  }

  hideLoader() {
    this.loader.classList.remove('show');
  }

  showError(message) {
    this.treeContainer.innerHTML = `<div class="treeview-error">${message}</div>`;
    this.show();
  }

  renderNoResults() {
    this.treeContainer.innerHTML = '<div class="treeview-no-results">Nu s-au găsit rezultate</div>';
  }

  log = (() => {
    const fn = (message, data = null) => {
      if (this.debugMode) {
        const now = new Date();
        const ts = `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now
          .getMilliseconds()
          .toString()
          .padStart(3, '0')}`;
        const CPN = 'TreeView'.padEnd(15);
        console.log(
          `%c[${ts}] [${CPN}] ${message}`,
          'color: #bd0075ff; font-weight: bold;',
          data ?? ''
        );
      }
    };

    fn.error = (message, data = null) => {
      const now = new Date();
      const ts = `${now.toLocaleTimeString('en-GB', { hour12: false })}.${now
        .getMilliseconds()
        .toString()
        .padStart(3, '0')}`;
      const CPN = 'TreeView'.padEnd(15);
      console.error(
        `%c[${ts}] [${CPN}] ${message}`,
        'color: #e74c3c; font-weight: bold;',
        data ?? ''
      );
    };

    return fn;
  })();
}
