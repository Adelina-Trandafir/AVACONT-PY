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
      this.addDOMListener(this.container, 'click', () => this.handleReadonlyClick());
      this.addDOMListener(this.input, 'keydown', (e) => this.handleKeydown(e));
      this.addDOMListener(this.input, 'blur', (e) => this.handleBlur(e));
    } else {
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

    if (this.searchTimeout) {
      clearTimeout(this.searchTimeout);
    }

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
      this.renderResults('');
      this.show();
    } else if (this.options.onSearch) {
      this.performSearch('');
    } else {
      console.warn('⚠️ Nu există date pentru readonly combobox');
      this.hide();
    }
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
      case 'Tab':
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
    if (!option) return;

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
    // Pentru afișare în input, folosim textul simplu (fără HTML)
    this.input.value = text;
    this.selectedValue = value;
    this.selectedText = text;
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
