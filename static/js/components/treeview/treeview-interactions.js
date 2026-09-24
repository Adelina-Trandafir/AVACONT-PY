export const treeviewInteractionsMixin = {
  handleInput(e) {
    const query = e.target.value;
    this.currentQuery = query;

    if (this.searchTimeout) {
      clearTimeout(this.searchTimeout);
    }

    this.searchTimeout = setTimeout(() => {
      this.performSearch(query);
    }, this.options.searchDelay);
  },

  handleKeydown(e) {
    e.stopPropagation();
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        this.navigateDown();
        break;
      case 'ArrowUp':
        e.preventDefault();
        this.navigateUp();
        break;
      case 'ArrowRight':
        e.preventDefault();
        this.expandHighlighted();
        break;
      case 'ArrowLeft':
        e.preventDefault();
        this.collapseHighlighted();
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

  handleFocus(e) {
    e.preventDefault();
    e.stopPropagation();
    this.lastFocusTimestamp = Date.now();

    if (!this.isVisible && this.results.length > 0) {
      this.show();
    } else if (this.currentQuery.length >= this.options.minSearchLength) {
      this.performSearch(this.currentQuery);
    }
  },

  handleBlur(e) {
    setTimeout(() => {
      if (!this.treeElement.contains(document.activeElement)) {
        // Nu închide dacă focusul e în dropdown
      }
    }, 200);
  },

  toggleDropdown() {
    if (this.isVisible) {
      this.hide();
    } else {
      if (this.results.length === 0) {
        this.performSearch('');
      } else {
        this.show();
      }
    }
  },

  handleDropdownClick(e) {
    const target = e.target;
    e.stopPropagation();

    // Ignoră click-uri pe search box
    if (
      this.searchInput &&
      (target === this.searchInput || target.closest('.treeview-search-wrapper'))
    ) {
      return;
    }

    // Click pe expander
    if (target.classList.contains('treeview-expander')) {
      const nodeId = target.dataset.nodeId;
      this.toggleNode(nodeId, e.shiftKey); // Shift+Click dezactivează auto-collapse
      return;
    }

    // Checkbox mode: the box toggles; nothing here closes the dropdown.
    if (this.options.checkable) {
      const box = target.closest('.treeview-check');
      if (box) {
        this.toggleCheckNode(box.dataset.checkId);
        return;
      }
    }

    // Click pe item
    const item = target.closest('.treeview-item');
    if (!item) return;

    const nodeId = item.dataset.value;
    const nodeLevel = parseInt(item.dataset.level || '1');
    const hasChildren = item.dataset.hasChildren === 'true';

    // Checkbox mode: the label of a leaf is a larger target for its box; the label of
    // a parent keeps expanding it, as without boxes.
    if (this.options.checkable) {
      if (hasChildren) this.toggleNode(nodeId, e.shiftKey);
      else this.toggleCheckNode(nodeId);
      return;
    }

    // Logică pentru selectableLevel
    if (this.options.selectableLevel !== null) {
      if (nodeLevel === this.options.selectableLevel) {
        this.selectFromItem(item);
      } else if (hasChildren) {
        this.toggleNode(nodeId, e.shiftKey);
      }
    } else {
      if (hasChildren && !e.ctrlKey && !e.metaKey) {
        this.toggleNode(nodeId, e.shiftKey);
      } else {
        this.selectFromItem(item);
      }
    }
  },

  handleDropdownDoubleClick(e) {
    const target = e.target;
    e.stopPropagation();
    e.preventDefault();

    // Ignoră pe search box
    if (this.searchInput && target.closest('.treeview-search-wrapper')) {
      return;
    }

    if (target.classList.contains('treeview-expander')) {
      return;
    }

    const item = target.closest('.treeview-item');
    if (!item) return;

    const nodeLevel = parseInt(item.dataset.level || '1');
    const hasChildren = item.dataset.hasChildren === 'true';

    if (this.options.selectableLevel !== null) {
      if (nodeLevel === this.options.selectableLevel) {
        this.selectFromItem(item);
      } else if (hasChildren) {
        this.toggleNode(item.dataset.value, e.shiftKey);
      }
    } else {
      this.selectFromItem(item);
    }
  },

  selectFromItem(item) {
    const value = item.dataset.value;
    const text = item.dataset.text || `Item ${value}`;

    // Parse data-path în formatul nou: "123,Nume;234,AltNume"
    const pathString = item.dataset.path;
    let path = [];
    if (pathString) {
      path = pathString.split(';').map((part) => {
        const [id, label] = part.split(',');
        return { id, label };
      });
    }

    const level = parseInt(item.dataset.level || '1');

    this.selectValue(value, text, path, level);
  },

  navigateDown() {
    if (!this.isVisible) {
      this.show();
      return;
    }

    const maxIndex = this.flattenedItems.length - 1;
    if (this.highlightedIndex < maxIndex) {
      this.highlightedIndex++;
      this.updateHighlight();
    }
  },

  navigateUp() {
    if (!this.isVisible) return;

    if (this.highlightedIndex > 0) {
      this.highlightedIndex--;
    } else {
      this.highlightedIndex = -1;
    }
    this.updateHighlight();
  },

  expandHighlighted() {
    if (this.highlightedIndex < 0) return;

    const item = this.flattenedItems[this.highlightedIndex];
    if (item && item.node.children && item.node.children.length > 0) {
      const nodeIdStr = String(item.node.id);
      if (!this.expandedNodes.has(nodeIdStr)) {
        this.toggleNode(nodeIdStr);
      }
    }
  },

  collapseHighlighted() {
    if (this.highlightedIndex < 0) return;

    const item = this.flattenedItems[this.highlightedIndex];
    if (item && item.node.children) {
      const nodeIdStr = String(item.node.id);
      if (this.expandedNodes.has(nodeIdStr)) {
        this.toggleNode(nodeIdStr);
      }
    }
  },

  updateHighlight() {
    const items = this.treeElement.querySelectorAll('.treeview-item');
    items.forEach((item) => {
      const itemIndex = parseInt(item.dataset.index);
      if (itemIndex === this.highlightedIndex) {
        item.classList.add('highlighted');
        item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      } else {
        item.classList.remove('highlighted');
      }
    });
  },

  selectHighlighted() {
    if (this.highlightedIndex < 0) return;

    const item = this.flattenedItems[this.highlightedIndex];
    if (!item) return;

    // Checkbox mode: Enter ticks what has a box (a leaf, or a branch under
    // `branchChecks`) and opens or closes a branch that has none.
    if (this.options.checkable) {
      const node = item.node;
      if (this.hasCheckbox(node)) this.toggleCheckNode(String(node.id));
      else this.toggleNode(String(node.id));
      return;
    }

    const level = item.level;

    if (this.options.selectableLevel !== null && this.options.selectableLevel !== level) {
      if (item.node.children && item.node.children.length > 0) {
        this.toggleNode(String(item.node.id));
      }
      return;
    }

    const nodeLabel = item.node.label || item.node.name || item.node.text || `Item ${item.node.id}`;
    this.selectValue(item.node.id, nodeLabel, item.path, level);
  },

  selectValue(value, text, path, level) {
    this.selectedValue = value;
    this.selectedText = text;
    this.selectedPath = path;

    this.input.value = text;

    if (this.options.showTwoRowsInInput) this.miniHeader.innerText = path[0].label || '';
    // Daca vreau sa afisez calea in input
    // if (path && path.length > 1) {
    //   this.input.value = path.map((p) => p.label).join(' > ');
    // } else {
    //   this.input.value = text;
    // }

    this.hide();

    if (this.options.onSelect) {
      this.options.onSelect({
        id: value,
        label: text,
        path: path,
        level: level,
      });
    }
  },
};
