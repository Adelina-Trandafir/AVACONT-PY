export const treeViewNodesMixin = {
  toggleNode(nodeId, skipAutoCollapse = false) {
    const nodeIdStr = String(nodeId);

    if (this.expandedNodes.has(nodeIdStr)) {
      // Închide nodul
      this.expandedNodes.delete(nodeIdStr);
      // Închide și toți copiii expandați
      this.collapseAllChildren(nodeIdStr);
    } else {
      // Auto-collapse alte noduri de același nivel dacă e activat
      if (this.options.autoCollapse && !skipAutoCollapse) {
        const nodeLevel = this.getNodeLevel(nodeIdStr);
        const nodeSiblings = this.getNodeSiblings(nodeIdStr);

        nodeSiblings.forEach((siblingId) => {
          if (this.expandedNodes.has(siblingId)) {
            this.expandedNodes.delete(siblingId);
            this.collapseAllChildren(siblingId);
          }
        });
      }

      // Deschide nodul
      this.expandedNodes.add(nodeIdStr);
    }

    this.isTreeRendered = false;
    this.renderTree(this.currentQuery);
    this.resizeDropdown();
    this.updateHighlight();

    // Actualizează breadcrumb după expand/collapse
    setTimeout(() => {
      if (this.handleTreeScroll) this.handleTreeScroll();
    }, 100);
  },

  /**
   * Chooses the node with this id from code: opens its ancestors, paints it chosen, brings it into
   * view and calls onSelect as a click would. Returns false when the id is not in the tree.
   */
  selectNodeById(nodeId) {
    const target = String(nodeId);
    const walk = (nodes, trail) => {
      for (const node of nodes || []) {
        const here = [...trail, { id: node.id, label: node.label || node.name || node.text || `Item ${node.id}` }];
        if (String(node.id) === target) return here;
        const found = walk(node.children, here);
        if (found) return found;
      }
      return null;
    };
    const path = walk(this.results, []);
    if (!path) return false;
    if (this.localSearchQuery) this.clearLocalSearch();
    path.slice(0, -1).forEach((p) => this.expandedNodes.add(String(p.id)));
    this.selectedValue = path[path.length - 1].id;
    this.selectedText = path[path.length - 1].label;
    this.selectedPath = path;
    this.isTreeRendered = false;
    this.renderTree(this.currentQuery);
    const row = this.treeContainer.querySelector(`.treeview-item[data-value="${CSS.escape(target)}"]`);
    if (row) row.scrollIntoView({ block: 'center' });
    if (this.options.onSelect) {
      this.options.onSelect({ id: this.selectedValue, label: this.selectedText, path, level: path.length });
    }
    return true;
  },

  collapseAllChildren(nodeId) {
    const findAndCollapse = (nodes, targetId) => {
      for (const node of nodes) {
        if (String(node.id) === targetId && node.children) {
          node.children.forEach((child) => {
            const childIdStr = String(child.id);
            this.expandedNodes.delete(childIdStr);
            if (child.children) {
              this.collapseAllChildren(childIdStr);
            }
          });
          return;
        }
        if (node.children) {
          findAndCollapse(node.children, targetId);
        }
      }
    };

    findAndCollapse(this.results, nodeId);
  },

  getNodeLevel(nodeId) {
    const findLevel = (nodes, targetId, level = 1) => {
      for (const node of nodes) {
        if (String(node.id) === targetId) {
          return level;
        }
        if (node.children) {
          const found = findLevel(node.children, targetId, level + 1);
          if (found) return found;
        }
      }
      return null;
    };

    return findLevel(this.results, nodeId);
  },

  getNodeSiblings(nodeId) {
    const siblings = [];

    const findSiblings = (nodes, targetId, parentNodes = null) => {
      for (const node of nodes) {
        if (String(node.id) === targetId && parentNodes) {
          return parentNodes.filter((n) => String(n.id) !== targetId).map((n) => String(n.id));
        }
        if (node.children) {
          const found = findSiblings(node.children, targetId, node.children);
          if (found) return found;
        }
      }
      return null;
    };

    const isLevel1 = this.results.some((n) => String(n.id) === nodeId);
    if (isLevel1) {
      return this.results.filter((n) => String(n.id) !== nodeId).map((n) => String(n.id));
    }

    return findSiblings(this.results, nodeId) || [];
  },
};
