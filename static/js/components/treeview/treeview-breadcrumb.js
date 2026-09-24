/**
 * ========== TREEVIEW BREADCRUMB MIXIN ==========
 * Gestionează breadcrumb header-ul cu întreg path-ul de părinți
 */

export const treeViewBreadcrumbMixin = {
  /**
   * Extrage inițialele din label (prima literă din fiecare cuvânt)
   */
  getInitials(label) {
    if (!label) return '';
    return label
      .split(/\s+/)
      .filter((word) => word.length > 0)
      .map((word) => word[0].toUpperCase())
      .slice(0, 2)
      .join('');
  },

  /**
   * Creează elementul breadcrumb în dropdown
   */
  createBreadcrumb() {
    if (this.breadcrumbElement) return;

    this.breadcrumbElement = document.createElement('div');
    this.breadcrumbElement.className = 'treeview-breadcrumb';
    this.breadcrumbElement.innerHTML = `
      <div class="treeview-breadcrumb-content">
        <div class="treeview-breadcrumb-parents"></div>
        <div class="treeview-breadcrumb-current">
          <span class="treeview-breadcrumb-icon">📁</span>
          <span class="treeview-breadcrumb-text"></span>
        </div>
      </div>
    `;

    this.treeElement.insertBefore(this.breadcrumbElement, this.treeContainer);
  },

  /**
   * Actualizează breadcrumb-ul cu path-ul complet de părinți
   */
  updateBreadcrumb(parentPath, currentParent) {
    if (!this.breadcrumbElement || !parentPath || parentPath.length === 0) return;

    const parentsContainer = this.breadcrumbElement.querySelector('.treeview-breadcrumb-parents');
    const currentTextElement = this.breadcrumbElement.querySelector('.treeview-breadcrumb-text');

    // Clear previous parents
    parentsContainer.innerHTML = '';

    // Calculează padding pentru aliniere cu tree-ul
    // Nivelul părintelui = lungimea path-ului (fără ultimul element care e currentul)
    const parentLevel = parentPath.length;
    const paddingLeft = (parentLevel - 1) * 12;

    // Setează padding-ul pentru aliniere
    //this.breadcrumbElement.querySelector('.treeview-breadcrumb-content').style.paddingLeft = `${paddingLeft}px`;

    // Creează span-uri pentru toți părinții (MINUS ultimul care e current)
    const allParentsExceptCurrent = parentPath.slice(0, -2);

    allParentsExceptCurrent.forEach((parent, index) => {
      // const initials = this.getInitials(parent.label);
      const level = index + 1;
      
      const parentSpan = document.createElement('span');
      parentSpan.className = 'treeview-breadcrumb-parent';
      parentSpan.textContent = level;
      parentSpan.title = parent.label; // Tooltip cu numele complet
      parentSpan.dataset.parentId = parent.id;

      // Click handler pentru fiecare părinte
      parentSpan.addEventListener('click', (e) => {
        e.stopPropagation();
        this.scrollToParentById(parent.id);
      });

      parentsContainer.appendChild(parentSpan);
    });

    // Actualizează textul părintelui curent (cu icon 📁)
    if (currentTextElement && currentParent) {
      currentTextElement.textContent = currentParent.label;
    }
  },

  /**
   * Afișează breadcrumb-ul cu fade in
   */
  showBreadcrumb() {
    if (!this.breadcrumbElement) return;
    this.breadcrumbElement.classList.add('visible');
  },

  /**
   * Ascunde breadcrumb-ul cu fade out
   */
  hideBreadcrumb() {
    if (!this.breadcrumbElement) return;
    this.breadcrumbElement.classList.remove('visible');
    this.currentBreadcrumbPath = null;
  },

  /**
   * Găsește primul item vizibil din dropdown
   */
  findFirstVisibleItem() {
    const items = this.treeContainer.querySelectorAll('.treeview-item');
    const containerRect = this.treeContainer.getBoundingClientRect();

    const breadcrumbHeight =
      this.breadcrumbElement && this.breadcrumbElement.classList.contains('visible')
        ? this.breadcrumbElement.offsetHeight
        : 0;

    const topOffset = containerRect.top;

    for (let item of items) {
      const itemRect = item.getBoundingClientRect();

      if (itemRect.top >= topOffset && itemRect.bottom <= containerRect.bottom) {
        return item;
      }
    }

    return null;
  },

  /**
   * Extrage ÎNTREGUL path de părinți din data-path
   */
  getFullParentPath(item) {
    if (!item) return null;

    try {
      const pathData = item.dataset.path;
      if (!pathData) return null;

      // Parse formatul: "123,Nume;234,AltNume;345,Current"
      const parts = pathData.split(';');

      return parts.map((part) => {
        const [id, label] = part.split(',');
        return { id, label };
      });
    } catch (e) {
      this.log.error('Error parsing path data:', e);
    }

    return null;
  },

  /**
   * Găsește elementul DOM al unui părinte după ID
   */
  findParentElement(parentId) {
    if (!parentId) return null;

    const items = this.treeContainer.querySelectorAll('.treeview-item');
    for (let item of items) {
      if (item.dataset.value === String(parentId)) {
        return item;
      }
    }

    return null;
  },

  /**
   * Verifică dacă un element părinte este vizibil în viewport
   */
  isParentVisible(parentElement) {
    if (!parentElement) return false;

    const containerRect = this.treeContainer.getBoundingClientRect();
    const parentRect = parentElement.getBoundingClientRect();

    const searchHeight = this.searchWrapper ? this.searchWrapper.offsetHeight : 0;
    const topOffset = containerRect.top + searchHeight;

    return parentRect.top >= topOffset && parentRect.bottom <= containerRect.bottom;
  },

  /**
   * Handler pentru scroll - detectează și afișează breadcrumb
   */
  handleTreeScroll() {
    if (!this.isVisible) return;

    const firstVisibleItem = this.findFirstVisibleItem();

    if (!firstVisibleItem) {
      this.hideBreadcrumb();
      return;
    }

    const fullPath = this.getFullParentPath(firstVisibleItem);

    if (!fullPath || fullPath.length <= 1) {
      // Nu are părinți sau e direct sub root
      this.hideBreadcrumb();
      return;
    }

    // Părintele direct = penultimul element din path
    const directParent = fullPath[fullPath.length - 2];
    const parentElement = this.findParentElement(directParent.id);

    if (!parentElement) {
      this.hideBreadcrumb();
      return;
    }

    // Verifică dacă părintele e vizibil
    if (this.isParentVisible(parentElement)) {
      this.hideBreadcrumb();
      return;
    }

    this.isBreadCrumbVisible = true;

    // Părintele nu e vizibil - afișează breadcrumb cu ÎNTREG path-ul
    const pathKey = fullPath.map((p) => p.id).join('-');

    if (this.currentBreadcrumbPathKey !== pathKey) {
      this.currentBreadcrumbPathKey = pathKey;
      this.currentBreadcrumbPath = fullPath;
      this.updateBreadcrumb(fullPath, directParent);
      this.showBreadcrumb();
    }
  },

  /**
   * Scroll smooth către un părinte specific după ID
   */
  scrollToParentById(parentId) {
    const parentElement = this.findParentElement(parentId);

    if (parentElement) {
      parentElement.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      });

      // Highlight temporar
      parentElement.classList.add('highlighted');
      setTimeout(() => {
        parentElement.classList.remove('highlighted');
      }, 1000);
    }
  },

  /**
   * Scroll smooth către părintele direct (click pe partea cu icon + text)
   */
  scrollToParent() {
    if (!this.currentBreadcrumbPath || this.currentBreadcrumbPath.length < 2) return;

    // Părintele direct = penultimul element
    const directParent = this.currentBreadcrumbPath[this.currentBreadcrumbPath.length - 2];
    this.scrollToParentById(directParent.id);
  },

  /**
   * Inițializează breadcrumb system
   */
  initBreadcrumb() {
    if (this.breadcrumbInitialized) return;

    this.createBreadcrumb();
    this.currentBreadcrumbPath = null;
    this.currentBreadcrumbPathKey = null;

    this.hideBreadcrumb();

    // Scroll listener
    this.addDOMListener(this.treeContainer, 'scroll', () => {
      this.handleTreeScroll();
    });

    // Wheel listener pentru smooth scroll
    this.addDOMListener(
      this.treeContainer,
      'wheel',
      (e) => {
        e.preventDefault();

        const rowHeight = this.currentRowHeight || 24;
        const delta = Math.sign(e.deltaY);

        this.treeContainer.scrollBy({
          top: delta * rowHeight,
          behavior: 'smooth',
        });
      },
      { passive: false }
    );

    // Click pe current parent (📁 + text)
    const currentDiv = this.breadcrumbElement.querySelector('.treeview-breadcrumb-current');
    if (currentDiv) {
      this.addDOMListener(currentDiv, 'click', () => {
        this.scrollToParent();
      });
    }

    this.breadcrumbInitialized = true;
  },

  /**
   * Curăță breadcrumb-ul
   */
  cleanupBreadcrumb() {
    if (this.breadcrumbElement && this.breadcrumbElement.parentNode) {
      this.breadcrumbElement.parentNode.removeChild(this.breadcrumbElement);
      this.breadcrumbElement = null;
    }
    this.breadcrumbInitialized = false;
    this.currentBreadcrumbPath = null;
    this.currentBreadcrumbPathKey = null;
  },
};
