export const treeViewSearchMixin = {
  async performSearch(query) {
    if (query.length < this.options.minSearchLength && query !== '') {
      this.hide();
      return;
    }

    this.showLoader();

    try {
      if (this.options.onSearch) {
        const results = await this.options.onSearch(query);
        this.isTreeRendered = false;
        this.updateResults(results, query);
      }
    } catch (error) {
      this.showError('Eroare la căutare');
      console.error('TreeView search error:', error);
    } finally {
      this.hideLoader();
    }
  },

  clearLocalSearch(clearText = true) {
    if (this.searchInput && clearText) {
      this.searchInput.value = '';
    }
    if (this.searchClear) {
      this.searchClear.style.display = 'none';
    }
    this.matchedNodes.clear();
    this.parentOfMatchedNodes.clear();
    this.expandedNodes.clear();

    this.isTreeRendered = false;
    this.filteredResults = [];
    this.localSearchQuery = '';
    this.apiQuery = '';
    this.currentQuery = '';

    // if (this.isVisible) {
    //   this.renderTreeWithData('', false);
    //   this.resizeDropdown();

    //   // Actualizează breadcrumb după clear
    //   setTimeout(() => {
    //     if (this.handleTreeScroll) this.handleTreeScroll();
    //   }, 100);
    // }
  },

  handleLocalSearch(e) {
    if (this.localSearchTimeout) {
      clearTimeout(this.localSearchTimeout);
    }

    this.localSearchTimeout = setTimeout(() => {
      if (e.target.value.length < 3) {
        this.clearLocalSearch(false);
        return;
      }

      const query = e.target.value.toLowerCase();
      this.localSearchQuery = query;

      if (this.searchClear) {
        this.searchClear.style.display = query ? 'block' : 'none';
      }

      if (!query) {
        this.isTreeRendered = false;
        this.filteredResults = this.results;
        this.expandedNodes.clear();
        this.renderTree(this.currentQuery);
        const dropdownHeight = this.getComputedTreeHeight();
        this.treeElement.style.height = dropdownHeight + 'px';

        // Actualizează breadcrumb
        setTimeout(() => {
          if (this.handleTreeScroll) this.handleTreeScroll();
        }, 100);
        return;
      }

      this.filteredResults = this.filterTreeForSearch(this.results, query);

      const expandFiltered = (nodes) => {
        nodes.forEach((node) => {
          if (node._hasMatchedChildren) {
            this.expandedNodes.add(String(node.id));
            if (node.children) {
              expandFiltered(node.children);
            }
          }
        });
      };

      this.expandedNodes.clear();
      expandFiltered(this.filteredResults);

      this.isTreeRendered = false;

      this.renderTree(query);
      const dropdownHeight = this.getComputedTreeHeight();
      this.treeElement.style.height = dropdownHeight + 'px';

      // Actualizează breadcrumb după search
      setTimeout(() => {
        if (this.handleTreeScroll) this.handleTreeScroll();

        const firstMatch = this.treeContainer.querySelector('.treeview-item[data-matched="true"]');
        if (firstMatch) {
          firstMatch.scrollIntoView({
            block: 'center',
            behavior: 'smooth',
          });
        }
      }, 100);
    }, 300);
  },

  updateResults(results, query) {
    const normalizeNodes = (nodes) => {
      return nodes.map((node) => {
        const normalizedNode = {
          ...node,
          label: node.label || node.name || node.text || `Item ${node.id}`,
        };

        if (normalizedNode.children && normalizedNode.children.length > 0) {
          normalizedNode.children = normalizeNodes(normalizedNode.children);
        }

        return normalizedNode;
      });
    };

    this.results = normalizeNodes(results || []);
    this.highlightedIndex = -1;

    if (!this.isVisible) return;

    if (query && this.options.expandOnSearch) {
      this.expandAllNodes(this.results);
    }

    this.clearLocalSearch();

    if (!this.isVisible) return;

    this.renderTree(query);

    if (this.results.length > 0) {
      this.show();
      if (this.searchInput && this.isVisible) {
        setTimeout(() => this.searchInput.focus(), 100);
      }
    } else if (query) {
      this.renderNoResults();
      this.show();
    } else {
      this.hide();
    }
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

  filterTreeForParentOnly(nodes, query) {
    return nodes.filter((node) => String(node.label).toLowerCase() === String(query).toLowerCase());
  },

  filterTreeForSearch(nodes, query) {
    if (!query) return nodes;

    const filterRecursive = (nodeList) => {
      return nodeList.reduce((acc, node) => {
        const nodeLabel = (node.label || node.name || node.text || '').toLowerCase();
        const matches = nodeLabel.includes(query.toLowerCase());

        let filteredChildren = [];
        if (node.children && node.children.length > 0) {
          filteredChildren = filterRecursive(node.children);
        }

        if (matches || filteredChildren.length > 0) {
          acc.push({
            ...node,
            children: filteredChildren,
            _isMatched: matches,
            _hasMatchedChildren: filteredChildren.length > 0,
          });
        }

        return acc;
      }, []);
    };

    return filterRecursive(nodes);
  },

  findMatchingNodes(nodes, query, parentPath = []) {
    nodes.forEach((node) => {
      const nodeLabel = (node.label || node.name || node.text || '').toLowerCase();
      const nodeIdStr = String(node.id);

      if (nodeLabel.includes(query)) {
        this.matchedNodes.add(nodeIdStr);
        parentPath.forEach((pid) => this.parentOfMatchedNodes.add(pid));
      }

      if (node.children && node.children.length > 0) {
        this.findMatchingNodes(node.children, query, [...parentPath, nodeIdStr]);
      }
    });
  },
};
