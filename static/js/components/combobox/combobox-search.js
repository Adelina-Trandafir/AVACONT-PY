/**
 * COMBOBOX SEARCH MIXIN
 * Gestionează logica de căutare (statică și dinamică)
 */
export const comboboxSearchMixin = {
  // Closing, choosing or changing the query invalidates outstanding searches.
  cancelSearch() {
    clearTimeout(this.searchTimeout);
    this.searchTimeout = null;
    this.searchSequence = (this.searchSequence || 0) + 1;
    this.hideLoader();
  },

  /**
   * Execută căutarea
   */
  async performSearch(query) {
    this.cancelSearch();
    if (this.disabled) return;
    const sequence = this.searchSequence;
    // Pentru readonly, acceptă și query gol
    if (!this.options.readonly && query.length < this.options.minSearchLength) {
      this.hide();
      return;
    }

    this.showLoader();

    try {
      if (this.options.onSearch) {
        const results = await this.options.onSearch(query);
        if (sequence !== this.searchSequence) return;
        this.updateResults(results, query);
      }
    } catch (error) {
      if (sequence !== this.searchSequence) return;
      this.showError('Eroare la căutare');
      console.error('Combobox search error:', error);
    } finally {
      if (sequence === this.searchSequence) this.hideLoader();
    }

    // find-as-you-type: once something was typed and found, the first match is the choice (Tab keeps it, no click needed)
    if (!this.options.readonly && query && this.results.length > 0 && !this.results[0].disabled) this.selectedIndex = 0;

    this.renderResults(query);

    if (this.results.length > 0) {
      this.show();
    } else {
      this.hide();
    }
  },
};
