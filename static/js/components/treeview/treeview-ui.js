export const treeViewUIMixin = {
  show(useSecondaryValue = false) {
    if (this.disabled) return;
    if (this.isVisible) return;

    if (!this.treeContainer) this.createElements();

    if (this.input.value) {
      if (!useSecondaryValue) {
        this.currentQuery = this.input.value;
        this.searchInput.value = this.input.value;
      } else if (this.selectedValueSecondary) {
        this.currentQuery = this.selectedTextSecondary;
        this.searchInput.value = this.selectedTextSecondary;
      }
      if (!this.isTreeRendered) this.renderTreeWithData(this.currentQuery, useSecondaryValue);
      this.searchInput.focus();
      setTimeout(() => {
        if (this.searchInput) this.searchInput.setSelectionRange(0, this.input.value.length);
      }, 100);
    } else {
      if (!this.isTreeRendered) this.renderTree();
    }

    if (!this.areEventsBound) this.bindEvents();

    this.positionDropdown();

    this.treeElement.classList.add(`${this.fitsBelowContainer ? 'show-below' : 'show-above'}`);
    this.treeElement.classList.remove(`${this.fitsBelowContainer ? 'show-above' : 'show-below'}`);

    this.treeElement.classList.add('visible');
    this.treeElement.classList.remove('hidden');

    this.overlayElement.classList.remove('hidden');
    this.overlayElement.classList.add('visible');
    this.isVisible = true;

    if (this.searchInput) setTimeout(() => this.searchInput.focus(), 100);

    // Verifică breadcrumb după ce dropdown-ul devine vizibil
    // setTimeout(() => {
    //   if (this.handleTreeScroll) this.handleTreeScroll();
    // }, 150);

    this.lastShownAt = Date.now();
  },

  hide() {
    if (this.treeElement) {
      this.treeElement.classList.add('hidden');
      this.treeElement.classList.remove('visible');

      this.treeElement.style.left = '';
      this.treeElement.style.top = '';
      this.treeElement.style.width = '';
      this.treeElement.style.height = '';

      this.treeElement.style.zIndex = 0;
    }

    if (this.overlayElement) {
      this.overlayElement.classList.add('hidden');
      this.overlayElement.classList.remove('visible');
      this.overlayElement.style.zIndex = 0;
    }

    // Ascunde breadcrumb când se închide dropdown-ul
    if (this.hideBreadcrumb) this.hideBreadcrumb();

    this.rect = null;
    this.isVisible = false;
    this.highlightedIndex = -1;
    this.flattenedNodes = [];
    this.expandedNodes = new Set();

    if (this.searchInput) this.searchInput.value = '';

    this.currentQuery = '';
    if (this.localSearchQuery) this.clearLocalSearch();

    this.isTreeRendered = false;
    this.lastHiddenAt = Date.now();
  },

  positionDropdown() {
    const rect = this.container.getBoundingClientRect();
    const dropdownHeight = Number(this.getComputedTreeHeight());
    const containerWidth = parseFloat(window.getComputedStyle(this.container).width);

    const width = Math.max(containerWidth, Math.floor(this.maxRowWidth));
    const height = Math.abs(dropdownHeight);
    const left = rect.left;
    const top = dropdownHeight < 0 ? rect.bottom - height : rect.top;

    if (this.treeElement.style.width !== `${width}px`) this.treeElement.style.width = `${width}px`;
    if (this.treeElement.style.height !== `${height}px`)
      this.treeElement.style.height = `${height}px`;
    if (this.treeElement.style.left !== `${left}px`) this.treeElement.style.left = `${left}px`;
    if (this.treeElement.style.top !== `${top}px`) this.treeElement.style.top = `${top}px`;

    this.treeElement.style.zIndex = Number(window.ZIndexManager.getMax()) + 10000;
    this.overlayElement.style.zIndex = Number(window.ZIndexManager.getMax()) + 9999;

    this.rect = this.treeElement.getBoundingClientRect();
    this.fitsBelowContainer = dropdownHeight > 0;
  },

  resizeDropdown() {
    if (!this.isVisible) return;
    const rect = this.container.getBoundingClientRect();
    const dropdownHeight = Number(this.getComputedTreeHeight());
    const containerWidth = parseFloat(window.getComputedStyle(this.container).width);
    const width = Math.max(containerWidth, Math.floor(this.maxRowWidth));
    const height = Math.abs(dropdownHeight);

    if (this.treeElement.style.width !== `${width}px`) this.treeElement.style.width = `${width}px`;
    if (this.treeElement.style.height !== `${height}px`)
      this.treeElement.style.height = `${height}px`;
    this.rect = this.treeElement.getBoundingClientRect();
    this.fitsBelowContainer = dropdownHeight > 0;

    // Verifică breadcrumb după resize
    // setTimeout(() => {
    //   if (this.handleTreeScroll) {
    //     this.handleTreeScroll();
    //     if (this.isBreadCrumbVisible)
    //       this.treeElement.style.height = `${height + Number(this.getRowHeight)}px`;
    //   }
    // }, 100);
  },
};
