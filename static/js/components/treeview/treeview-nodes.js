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
