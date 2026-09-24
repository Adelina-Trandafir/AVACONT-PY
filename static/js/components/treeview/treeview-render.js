export const treeViewRenderMixin = {
  renderTreeWithData(data, asParent = false) {
    const query = data.toLowerCase();

    // Auto-expand toate nodurile cu rezultate
    const expandFiltered = (nodes, asParent) => {
      if (!asParent) {
        nodes.forEach((node) => {
          if (node._hasMatchedChildren) {
            this.expandedNodes.add(String(node.id));
            if (node.children) {
              expandFiltered(node.children);
            }
          }
        });
      } else {
        nodes.forEach((node) => {
          this.expandedNodes.add(String(node.id));
        });
      }
    };

    this.localSearchQuery = query;

    if (this.searchClear && this.localSearchQuery) this.searchClear.style.display = 'block';

    // this.expandedNodes.clear();

    // Filtrează arborele
    if (!asParent) {
      this.filteredResults = this.filterTreeForSearch(this.results, query);
    } else {
      this.filteredResults = this.filterTreeForParentOnly(this.results, query);
    }

    expandFiltered(this.filteredResults, asParent);

    this.isTreeRendered = false;

    this.renderTree(query, asParent);
  },

  renderTree(query, asParent = false) {
    if (this.isTreeRendered) return;

    const dataToRender = this.localSearchQuery ? this.filteredResults : this.results;

    if (!dataToRender || dataToRender.length === 0) return;

    this.flattenedItems = [];

    const html = this.renderNodes(dataToRender, query, 1, [], asParent);

    this.treeContainer.innerHTML = html;

    this.isTreeRendered = true;
  },

  renderNodes(nodes, query, level, parentPath, asParent = false) {
    let html = '';

    nodes.forEach((node) => {
      const nodeLabel = node.label || node.name || node.text || `Item ${node.id}`;
      const nodeIdStr = String(node.id);
      const hasChildren = node.children && node.children.length > 0;
      const isExpanded = this.expandedNodes.has(nodeIdStr);
      const currentPath = [...parentPath, { id: node.id, label: nodeLabel }];
      const pathString = currentPath.map((p) => `${p.id},${p.label}`).join(';');
      const isSelectable =
        this.options.selectableLevel === null || this.options.selectableLevel === level;

      // Verifică stilizarea pentru search
      const isMatched = this.matchedNodes.has(nodeIdStr);
      const isParentOfMatched = this.parentOfMatchedNodes.has(nodeIdStr);

      const paddingLeft = (level - 1) * 12;

      // Calculează lățimea reală totală
      const expanderWidth = Number(this.expanderWidth);
      const checkboxWidth =
        this.options.checkable && this.hasCheckbox(node) ? Number(this.checkboxWidth) : 0;
      const textWidth = this.getTextWidth(nodeLabel);
      const totalWidth = paddingLeft + expanderWidth + checkboxWidth + textWidth;

      this.flattenedItems.push({
        node: { ...node, label: nodeLabel },
        level: level,
        path: currentPath,
        index: this.flattenedItems.length,
        elementWidth: totalWidth,
      });

      const itemIndex = this.flattenedItems.length - 1;

      // Clase CSS
      let itemClasses = 'treeview-item';
      if (!isSelectable) itemClasses += ' not-selectable';
      if (this.options.requireDoubleClick) itemClasses += ' dblclick-mode';
      if (hasChildren) itemClasses += ' has-children';
      if (!asParent && isMatched) itemClasses += ' matched';
      if (isParentOfMatched) itemClasses += ' parent-matched';

      html += `
    <div class="treeview-item-wrapper">
      <div class="${itemClasses}" 
           style="padding-left: ${paddingLeft}px;"
           data-value="${this.escapeHtml(nodeIdStr)}"
           data-text="${this.escapeHtml(nodeLabel)}"
           data-level="${level}"
           data-has-children="${hasChildren}"
           data-path='${this.escapeHtml(pathString)}'
           data-index="${itemIndex}"
           data-text-width="${Math.ceil(totalWidth)}">
        ${
          hasChildren
            ? `
          <span class="treeview-expander ${isExpanded ? 'expanded' : ''}" 
                data-node-id="${nodeIdStr}">
            ${isExpanded ? '▼' : '▶'}
          </span>
        `
            : '<span class="treeview-spacer"></span>'
        }
        ${this.options.checkable ? this.renderCheckbox(node) : ''}
        <span class="treeview-label"${
          this.options.checkable ? ` title="${this.escapeHtml(nodeLabel)}"` : ''
        }>
          ${!asParent ? this.highlightText(nodeLabel, query, this.localSearchQuery) : nodeLabel}
        </span>
      </div>
      ${
        hasChildren && isExpanded
          ? `
        <div class="treeview-children">
          ${this.renderNodes(node.children, query, level + 1, currentPath)}
        </div>
      `
          : ''
      }
    </div>
  `;
    });

    this.maxRowWidth = Math.max(
      this.maxRowWidth,
      ...this.flattenedItems.map((item) => item.elementWidth)
    );

    return html;
  },

  renderNoResults() {
    this.treeContainer.innerHTML = '<div class="treeview-no-results">Nu s-au găsit rezultate</div>';
  },

  highlightText(text, apiQuery, localQuery) {
    if (!this.options.highlightMatches) return this.escapeHtml(text);

    let escapedText = this.escapeHtml(text);

    // Highlight pentru local search (prioritate)
    if (localQuery && localQuery.trim()) {
      const escapedQuery = this.escapeRegex(localQuery.trim());
      const regex = new RegExp(`(${escapedQuery})`, 'gi');
      escapedText = escapedText.replace(regex, '<span class="treeview-highlight-local">$1</span>');
    }
    // Highlight pentru API query
    else if (apiQuery && apiQuery.trim()) {
      const escapedQuery = this.escapeRegex(apiQuery.trim());
      const regex = new RegExp(`(${escapedQuery})`, 'gi');
      escapedText = escapedText.replace(regex, '<span class="treeview-highlight">$1</span>');
    }

    return escapedText;
  },

  clearAllHighlights() {
    // 1. Curăță starea internă
    this.matchedNodes.clear();
    this.parentOfMatchedNodes.clear();
    this.localSearchQuery = '';
    this.apiQuery = '';
  },

  highlightMatch(text, query) {
    return this.highlightText(text, query, '');
  },

  expandAllNodes(nodes) {
    const expandRecursive = (nodeList) => {
      nodeList.forEach((node) => {
        if (node.children && node.children.length > 0) {
          this.expandedNodes.add(String(node.id));
          expandRecursive(node.children);
        }
      });
    };
    expandRecursive(nodes);
  },
};
