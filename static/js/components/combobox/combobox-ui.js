/**
 * COMBOBOX UI MIXIN
 * Gestionează afișarea, ascunderea și poziționarea dropdown-ului
 * ✨ UPDATE: Suport pentru prefix icons (steaguri, avatare, etc.)
 */
import { layoutRect, layoutViewport } from '../../utils/layout-geometry.js';

export const comboboxUIMixin = {
  /**
   * Creează elementele HTML
   * ✨ UPDATE: Adaugă prefix-icon container opțional
   */
  createOverlayElement() {
    if (!this.overlay) {
      this.overlay = document.createElement('div');
      this.overlay.id = `${this.instanceId}_overlay`;
      this.overlay.className = 'combobox-overlay hidden';
      // Modal controls keep their overlay inside the dialog's top layer.
      (this.container.closest('dialog') || document.body).appendChild(this.overlay);
    }
  },

  createElements() {
    this.container.classList.add('combobox-container');

    if (this.options.readonly) {
      this.container.classList.add('readonly');
    }
    const simple = this.options.dropDownStyle === 'simple';
    if (simple) this.container.classList.add('simple');

    // ✨ Adaugă clasă pentru prefix icon
    if (this.options.prefixIcon) {
      this.container.classList.add('has-prefix-icon');
    }

    if (this.options.showOnlyIcon) {
      this.container.parentElement.classList.add('show-only-icon');
    }

    const arrowIcon = simple ? '<div class="combobox-arrow">▾</div>' : this.options.readonly ? '<div class="combobox-arrow">🔽</div>' : this.options.showArrow ? '<button type="button" class="combobox-arrow" tabindex="-1" aria-label="Afișează lista">▾</button>' : '';

    // ✨ Prefix icon container (opțional)
    const prefixIconHtml = this.options.prefixIcon
      ? '<div class="combobox-prefix-icon"></div>'
      : '';

    this.container.innerHTML = `
      ${prefixIconHtml}
      <input type="text" 
             class="combobox-input" 
             placeholder="${this.options.placeholder}"
             autocomplete="off"
             spellcheck="false"
             ${this.options.readonly ? 'readonly' : ''} />
      ${arrowIcon}
      <div class="combobox-loader"></div>
      <div class="combobox-dropdown"></div>
    `;

    this.input = this.container.querySelector('.combobox-input');
    this.loader = this.container.querySelector('.combobox-loader');
    this.dropdown = this.container.querySelector('.combobox-dropdown');
    this.arrow = this.container.querySelector('.combobox-arrow');

    // ✨ Referință la prefix icon container
    if (this.options.prefixIcon) {
      this.prefixIconContainer = this.container.querySelector('.combobox-prefix-icon');
    }
  },

  /**
   * Deschide dropdown-ul
   */
  show() {
    // Cells/forms can be constructed before their dialog is attached.
    const surface = this.container.closest('dialog') || document.body;
    if (this.overlay.parentElement !== surface) surface.append(this.overlay);
    this.overlay.style.zIndex = Math.floor(window.ZIndexManager.getNext() || 1000) + 9999;
    this.dropdown.style.zIndex = Math.floor(window.ZIndexManager.getNext() || 1000) + 10000;
    if (this.options.showOnlyIcon) {
      this.dropdown.style.width = '150px';
    } else {
      this.dropdown.style.width = `${layoutRect(this.input).width}px`;
    }

    this.updateHighlight();
    this.lastShownAt = Date.now();

    this.overlay.classList.add('visible');
    this.overlay.classList.remove('hidden');

    this.dropdown.classList.add('visible');
    this.dropdown.classList.remove('hidden');

    this.isVisible = true;
    this.markMultilineOptions();
    this.positionDropdown();
  },

  /**
   * Închide dropdown-ul
   */
  hide() {
    this.cancelSearch();
    this.overlay.classList.add('hidden');
    this.overlay.classList.remove('visible');

    this.dropdown.classList.add('hidden');
    this.dropdown.classList.remove('visible');

    this.dropdown.style.zIndex = 0;
    this.overlay.style.zIndex = 0;
    this.selectedIndex = -1;
    this.lastShownAt = 0;
    this.isVisible = false;
  },

  /**
   * Calculează poziția dropdown-ului relativ la viewport
   */
  positionDropdown() {
    if (!this.input) {
      console.error('Input element not found for positioning');
      return;
    }

    const rect = layoutRect(this.input);
    const { height: viewportHeight, width: viewportWidth } = layoutViewport();
    const dropdownHeight = 200;

    // --combobox-min-width (set in CSS on the dropdown) keeps a narrow field from giving a cramped list
    const minWidth = parseFloat(getComputedStyle(this.dropdown).getPropertyValue('--combobox-min-width')) || 0;
    const grow = parseFloat(getComputedStyle(this.dropdown).getPropertyValue('--combobox-grow')) || 1;
    const listMin = Math.max(minWidth, this.options.dropDownMinWidth || 0);
    // the stylesheet caps the list at 320px; an explicit minimum width lifts the cap so the list can be wider than its box
    this.dropdown.style.maxWidth = this.options.dropDownMinWidth > 0 ? 'none' : '';
    const width = Math.min(Math.max(rect.width, listMin) * grow, viewportWidth - 16);
    const left = Math.max(8, Math.min(rect.left, viewportWidth - width - 8));
    this.dropdown.style.width = `${width}px`;

    if (rect.bottom + dropdownHeight > viewportHeight - 10) {
      this.dropdown.classList.add('show-above');
      this.dropdown.classList.remove('show-below');

      const topPosition = rect.top - dropdownHeight;
      const finalTop = Math.max(10, topPosition);

      this.dropdown.style.top = `${finalTop}px`;
      this.dropdown.style.left = `${left}px`;
    } else {
      this.dropdown.classList.remove('show-above');
      this.dropdown.classList.add('show-below');

      this.dropdown.style.top = `${rect.bottom}px`;
      this.dropdown.style.left = `${left}px`;
    }
  },

  /**
   * Afișează loader-ul
   */
  showLoader() {
    if (this.options.showLoader) {
      this.loader.classList.add('show');
    }
  },

  /**
   * Ascunde loader-ul
   */
  hideLoader() {
    this.loader.classList.remove('show');
  },

  /**
   * Render rezultatele în dropdown
   * 🆕 MODIFICAT - suport pentru allowHtml
   */
  renderResults(query) {
    if (this.results.length === 0) {
      this.dropdown.innerHTML = '<div class="combobox-no-results">Nu s-au găsit rezultate</div>';
      return;
    }

    const html = this.results
      .slice(0, this.options.maxResults)
      .map((item) => {
        const value = item.value;
        const label = item.label;

        let displayLabel;

        // 🆕 LOGIC NOU - verifică allowHtml
        if (this.options.allowHtml) {
          // Permite HTML - nu face escape
          displayLabel = label;
        } else {
          // Securitate - face escape ca înainte
          const safeLabel = this.escapeHtml(label);
          const safeQuery = this.escapeHtml(query);

          displayLabel =
            this.options.highlightMatches && safeQuery
              ? this.highlightMatch(safeLabel, safeQuery)
              : safeLabel;
        }

        // Valoarea și textul pentru data attributes trebuie să fie escaped întotdeauna
        const safeValue = this.escapeHtml(value);
        const safeText = this.options.allowHtml
          ? this.stripHtml(label) // 🆕 Elimină HTML pentru data-text
          : this.escapeHtml(label);

        return `<div class="combobox-option${item.disabled ? ' is-disabled' : ''}"
                  data-value="${safeValue}" 
                  data-text="${safeText}"> 
                ${displayLabel}
              </div>`;
      })
      .join('');

    this.dropdown.innerHTML = html;
    if (this.isVisible) this.markMultilineOptions();
  },

  /**
   * Marchează cu `is-multiline` opțiunile al căror text se rupe pe mai multe rânduri, ca stilul să poată
   * strânge paddingul vertical al acestora (CSS nu poate detecta ruperea textului). Se măsoară numai cât timp
   * dropdown-ul e vizibil și are lățimea finală; fără layout (dropdown ascuns) nu se marchează nimic.
   */
  markMultilineOptions() {
    this.dropdown.querySelectorAll('.combobox-option').forEach((option) => {
      const range = document.createRange();
      range.selectNodeContents(option);
      const first = range.getClientRects()[0];
      option.classList.toggle(
        'is-multiline',
        !!first && first.height > 0 && range.getBoundingClientRect().height > first.height * 1.5,
      );
    });
  },

  /**
   * Actualizează highlight-ul opțiunii selectate
   */
  updateHighlight() {
    const options = this.dropdown.querySelectorAll('.combobox-option');
    options.forEach((option, index) => {
      option.classList.toggle('highlighted', index === this.selectedIndex);
    });

    if (this.selectedIndex >= 0) {
      const highlighted = options[this.selectedIndex];
      if (highlighted) {
        highlighted.scrollIntoView({ block: 'nearest' });
      }
    }
  },

  /**
   * Evidențiază match-urile în text
   */
  highlightMatch(text, query) {
    if (!query) return this.escapeHtml(text);

    const escapedText = this.escapeHtml(text);
    const escapedQuery = this.escapeHtml(query);
    const regex = new RegExp(`(${escapedQuery})`, 'gi');

    return escapedText.replace(regex, '<span class="combobox-highlight">$1</span>');
  },

  /**
   * Escape HTML
   */
  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  },

  /**
   * 🆕 FUNCȚIE NOUĂ - Elimină tag-uri HTML din text
   * Folosit pentru data-text când allowHtml este true
   */
  stripHtml(html) {
    const div = document.createElement('div');
    div.innerHTML = html;
    return div.textContent || div.innerText || '';
  },

  /**
   * Afișează eroare
   */
  showError(message) {
    this.dropdown.innerHTML = `<div class="combobox-error">${message}</div>`;
    this.show();
  },

  /**
   * Actualizează starea vizuală (enabled/disabled)
   */
  updateState() {
    this.container.classList.toggle('disabled', this.disabled);
  },

  /**
   * Gestionează repoziționarea la scroll/resize
   */
  handleWindowEvents() {
    if (this.isVisible) {
      this.positionDropdown();
    }
  },
};
