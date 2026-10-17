/**
 * COMBOBOX EVENTS MIXIN
 * Gestionează binding-ul și handling-ul evenimentelor
 * Folosește ListenerTracker pentru tracking automat și cleanup
 */
export const comboboxEventsMixin = {
  /**
   * Bind evenimente cu ListenerTracker
   */
  bindEvents() {
    if (this.options.readonly) {
      this.addDOMListener(this.container, 'click', (event) => {
        // A choice already closes the dropdown; its bubbling click must not reopen it.
        // inside a <label> the browser repeats every click on the input, which would reopen the list right after a choice
        // (or open and close it at once from the arrow): the repeat is cancelled here
        event.preventDefault();
        if (this.dropdown.contains(event.target)) return;
        this.handleReadonlyClick();
      });
      this.addDOMListener(this.input, 'keydown', (e) => this.handleKeydown(e));
      this.addDOMListener(this.input, 'blur', (e) => this.handleBlur(e));
      if (this.options.dropDownStyle === 'simple') {
        // a simple box that gets the focus from the keyboard (Tab, Enter of the form) opens its list; a mouse click opens it by itself
        this.addDOMListener(this.container, 'pointerdown', () => { this._pointerFocus = true; }, true);
        this.addDOMListener(this.input, 'focus', () => {
          const k_byMouse = this._pointerFocus; this._pointerFocus = false;
          if (!k_byMouse && !this.disabled && !this.isVisible) this.showAllOptions();
        });
      }
    } else {
      if (this.arrow) {
        this.addDOMListener(this.arrow, 'mousedown', (event) => event.preventDefault());
        this.addDOMListener(this.arrow, 'click', (event) => {
          event.preventDefault();
          this.handleReadonlyClick();
        });
      }
      this.addDOMListener(this.input, 'input', (e) => this.handleInput(e));
      this.addDOMListener(this.input, 'keydown', (e) => this.handleKeydown(e));
      this.addDOMListener(this.input, 'focus', () => this.handleFocus());
      this.addDOMListener(this.input, 'blur', (e) => this.handleBlur(e));
    }

    this.addDOMListener(this.dropdown, 'click', (e) => this.handleDropdownClick(e));
    this.addDOMListener(this.dropdown, 'mousedown', (e) => e.preventDefault());

    this.addDOMListener(window, 'scroll', () => this.handleWindowEvents(), true);
    this.addDOMListener(window, 'resize', () => this.handleWindowEvents());

    this.addDOMListener(document, 'click', (e) => {
      if (!this.container.contains(e.target) && !this.dropdown.contains(e.target)) {
        if (this.isVisible) this.hide();
      }
    });
  },

  /**
   * Gestionează input-ul utilizatorului
   */
  handleInput(e) {
    const query = e.target.value;
    this.currentQuery = query;

    this.cancelSearch();

    this.searchTimeout = this.addTimeout(() => {
      this.performSearch(query);
    }, this.options.searchDelay);
  },

  /**
   * Gestionează click pe readonly combobox
   */
  handleReadonlyClick() {
    if (this.disabled) return;

    if (this.isVisible) {
      this.hide();
    } else {
      this.showAllOptions();
    }
  },

  /**
   * Afișează toate opțiunile pentru readonly
   */
  showAllOptions() {
    if (this.options.staticData && this.options.staticData.length > 0) {
      this.updateResults(this.options.staticData, '');
      this.highlightCurrent();
      this.renderResults('');
      this.show();
    } else if (this.options.onSearch) {
      return this.performSearch('').then(() => { if (this.isVisible) { this.highlightCurrent(); this.updateHighlight(); } });
    } else {
      console.warn('⚠️ Nu există date pentru readonly combobox');
      this.hide();
    }
  },

  /** A simple list opens with the current choice highlighted. */
  highlightCurrent() {
    if (this.options.dropDownStyle !== 'simple' || this.selectedValue == null || this.selectedValue === '') return;
    const k_at = this.results.findIndex((k_item) => String(typeof k_item === 'string' ? k_item : k_item.value) === String(this.selectedValue));
    if (k_at >= 0) this.selectedIndex = k_at;
  },

  /**
   * Simple list: typing letters jumps to the first item that STARTS with them. Letters typed within 800ms of each other are one
   * search text (two or three letters narrow the jump); the list opens by itself when it is closed.
   */
  async typeAhead(k_key) {
    this._typeBuffer = (this._typeBuffer || '') + k_key.toLocaleLowerCase('ro');
    clearTimeout(this._typeTimer);
    this._typeTimer = this.addTimeout(() => { this._typeBuffer = ''; }, 800);
    if (!this.isVisible) await this.showAllOptions();
    const k_text = (k_item) => { const k_label = typeof k_item === 'string' ? k_item : k_item.label; return String(this.options.allowHtml ? this.stripHtml(k_label) : k_label).toLocaleLowerCase('ro'); };
    const k_at = this.results.findIndex((k_item) => !k_item.disabled && k_text(k_item).startsWith(this._typeBuffer));
    if (k_at >= 0) { this.selectedIndex = k_at; this.updateHighlight(); }
  },

  /**
   * Gestionează tastele
   */
  handleKeydown(e) {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        this.navigateDown();
        break;
      case 'ArrowUp':
        e.preventDefault();
        this.navigateUp();
        break;
      case 'Enter':
        e.preventDefault();
        this.selectHighlighted();
        break;
      case 'Escape':
        this.hide();
        break;
      default:
        if (this.options.dropDownStyle === 'simple' && e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) { e.preventDefault(); this.typeAhead(e.key); }
        break;
      case 'Tab':
        if (this.isVisible && (!this.options.readonly || this.options.dropDownStyle === 'simple') && this.selectedIndex >= 0) this.selectHighlighted();   // the highlighted match is kept
        this.hide();
        break;
    }
  },

  /**
   * Gestionează focus
   */
  handleFocus() {
    if (this.options.readonly) {
      if (this.results.length > 0) {
        this.show();
      } else if (this.input.value.length >= this.options.minSearchLength) {
        this.performSearch(this.input.value);
      }
    } else if (this.options.dropDownOnEnter && !this.disabled && !this.isVisible) {
      this.showAllOptions();   // editable box: the focus opens the whole list
    }
  },

  /**
   * Gestionează blur
   */
  handleBlur(e) {
    this.addTimeout(() => {
      if (!this.container.contains(document.activeElement)) {
        this.hide();
      }
    }, 150);
  },

  /**
   * Gestionează click pe dropdown
   */
  handleDropdownClick(e) {
    const option = e.target.closest('.combobox-option');
    if (!option || option.classList.contains('is-disabled')) return; // an item marked `disabled` is shown but cannot be chosen

    // A list nested in a label must not activate its input again after selection.
    e.preventDefault();

    const value = option.dataset.value;
    const text = option.dataset.text;

    this.setValue(value, text);
    this.selectValue(value, text);
  },

  /**
   * Selectează o valoare
   * 🆕 MODIFICAT - suport pentru allowHtml în callback
   */
  selectValue(value, text) {
    if (this.results.find((r) => r.value === value)?.disabled) return;
    // Pentru afișare în input, folosim textul simplu (fără HTML)
    this.input.value = text;
    this.selectedValue = value;
    this.selectedText = text;
    this.currentQuery = text;
    this.hide();

    if (this.options.onSelect) {
      // Găsește item-ul original cu HTML pentru callback
      const originalItem = this.results.find((r) => r.value === value);
      const data = originalItem ? { data: originalItem.data } : null;

      this.options.onSelect(value, text, data);
    }
  },

  /**
   * Navigare în jos
   */
  navigateDown() {
    if (!this.isVisible) return;

    this.selectedIndex = Math.min(this.results.length - 1, this.selectedIndex + 1);
    this.updateHighlight();
  },

  /**
   * Navigare în sus
   */
  navigateUp() {
    if (!this.isVisible) return;

    this.selectedIndex = Math.max(-1, this.selectedIndex - 1);
    this.updateHighlight();
  },

  /**
   * Selectează opțiunea highlighted
   */
  selectHighlighted() {
    if (this.selectedIndex >= 0 && this.selectedIndex < this.results.length) {
      const item = this.results[this.selectedIndex];
      const value = typeof item === 'string' ? item : item.value;
      const text = typeof item === 'string' ? item : item.label;

      // 🆕 Pentru allowHtml, elimină HTML-ul pentru text
      const displayText = this.options.allowHtml ? this.stripHtml(text) : text;

      this.selectValue(value, displayText);
    }
  },
};
