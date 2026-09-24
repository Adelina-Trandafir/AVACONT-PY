export const treeViewUtilsMixin = {
  // Output lands inside attributes too (data-text="…", data-path='…'), so quotes are
  // escaped as well; textContent/innerHTML alone leaves them raw. Diverges from JS_COMPONENTS.
  escapeHtml(text) {
    return String(text ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  },

  escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  },

  // Masoara latimea textului luand in considerare si indentarea
  // https://stackoverflow.com/questions/118241/calculate-text-width-with-javascript
  getTextWidth(text) {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    context.font = this.containerFont;
    const metrics = context.measureText(text);
    return metrics.width;
  },

  getComputedRowHeight() {
    if (!this.isVisible) {
      this.treeElement.style.visibility = 'hidden';
      this.treeElement.classList.remove('hidden');
    }
    
    let treeHeight = 0;

    const leafRow = this.treeContainer.querySelector(
      '.treeview-item-wrapper .treeview-item[data-has-children="false"]'
    );

    this.currentRowHeight = leafRow ? leafRow.offsetHeight : this.options.defaultRowHeight || 24;

    if (!this.isVisible) {
      this.treeElement.classList.add('hidden');
      this.treeElement.style.visibility = '';
    }
  },
  
  // Daca dropdown-ul nu incape in spatiul de sub input, valoarea
  // returneaza o valoare negativa (inaltimea dropdown-ului va fi
  // ajustata in functie de spatiul disponibil)
  getComputedTreeHeight() {
    if (!this.isVisible) {
      this.treeElement.style.visibility = 'hidden';
      this.treeElement.classList.remove('hidden');
    }
    let treeHeight = 0;

    const searchWrapperHeight = this.searchWrapper.offsetHeight;
    const viewportHeight = window.innerHeight;
    const rect = this.container.getBoundingClientRect();
    const spaceBelow = viewportHeight - rect.top - searchWrapperHeight - 10;
    // With only collapsed parents on screen there is no leaf to measure; any row is
    // closer to the truth than the 24px fallback. Diverges from JS_COMPONENTS.
    const leafRow =
      this.treeContainer.querySelector(
        '.treeview-item-wrapper .treeview-item[data-has-children="false"]'
      ) || this.treeContainer.querySelector('.treeview-item-wrapper .treeview-item');

    this.currentRowHeight = leafRow ? leafRow.offsetHeight : this.options.defaultRowHeight || 24;

    if (!this.isVisible) {
      this.treeElement.classList.add('hidden');
      this.treeElement.style.visibility = '';
    }
    treeHeight = Number(
      this.currentRowHeight * Math.min(this.flattenedItems.length, this.maxVisibleRows) +
        searchWrapperHeight +
        2
    );

    if (spaceBelow < treeHeight) {
      treeHeight =
        Math.floor(spaceBelow / this.currentRowHeight) * this.currentRowHeight +
        searchWrapperHeight +
        2;
      return -1 * treeHeight;
    } else {
      return treeHeight;
    }
  },
};
