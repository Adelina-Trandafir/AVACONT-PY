// The header lookup is a custom Combobox, independent of the selected group's grid filters.
import { Combobox } from '../components/combobox/combobox.js';

export function bindChildSearch({ container, state, api, chooseGroup, loadSituation, toggleHiddenGroups, report }) {
  const lifetime = new AbortController();
  let request = null, scope = '', selecting = false, disposed = false;
  const contextKey = () => JSON.stringify([state.unit, state.subunit, state.monthId]);
  const ready = () => state.subunit != null && state.monthId != null
    && state.context?.months.some(month => month.IDL === state.monthId);
  const current = key => !disposed && key === contextKey() && ready();
  const groupLabel = value => String(value || '').replace(/\r?\n/g, ' ').trim();

  function cancel() { request?.abort(); request = null; }

  const combo = new Combobox(container, {
    placeholder: 'Nume sau CNP · minimum 3 caractere', minSearchLength: 3,
    searchDelay: 300, maxResults: 101, dropDownMinWidth: 420, autoHighlightMatch: false, highlightMatches: false,
    onSearch: async text => {
      cancel();
      const query = text.trim(), key = contextKey(), month = state.monthId;
      if (!current(key) || query.length < 3) return [];
      const own = new AbortController();
      request = own;
      try {
        const result = await api(`/api/adechit/child-search?IDL=${month}&q=${encodeURIComponent(query)}`, { signal: own.signal });
        if (own.signal.aborted || !current(key)) return [];
        const results = result.rows.map(row => ({
          value: String(row.IDZ),
          label: `${row.Nume} · ${groupLabel(row.Grupa)}${row.CNP_ULTIME ? ` · CNP …${row.CNP_ULTIME}` : ''}`,
          data: { row, key },
        }));
        if (!results.length) results.push({ value: 'empty', label: 'Niciun copil găsit în luna selectată.', disabled: true });
        if (result.has_more) results.push({ value: 'more', label: 'Mai sunt rezultate. Completați numele pentru a restrânge căutarea.', disabled: true });
        return results;
      } catch (error) {
        if (error.name === 'AbortError' || !current(key)) return [];
        throw error;
      } finally { if (request === own) request = null; }
    },
    onSelect: (_value, _label, item) => {
      if (item?.data) void select(item.data).catch(error => { if (!disposed) report(error); });
    },
  });
  combo.input.id = 'ade-search';
  combo.input.maxLength = 100;
  combo.input.setAttribute('aria-label', 'Caută copil în toate grupele lunii selectate, după minimum 3 caractere');
  combo.input.title = 'Caută după nume sau CNP numai copiii din luna selectată, în toate grupele.';
  // Invalidate the visible old choices before the control starts its next debounce.
  combo.input.addEventListener('input', () => {
    cancel(); combo.hide(); combo.results = [];
  }, { capture: true, signal: lifetime.signal });

  async function select({ row, key }) {
    if (selecting || !current(key) || row.IDL !== state.monthId) return;
    selecting = true;
    combo.setEnabled(false);
    const previousGroup = state.groupId;
    try {
      if (state.grid?.hasEdit && !await state.grid.commitEdit()) return;
      if (!current(key) || state.groupId !== previousGroup) return;
      if (state.pending > 0) throw new Error('Așteptați finalizarea salvării înainte de a selecta copilul.');
      // Include hidden groups as well; the result belongs to this month's attendance.
      if (!state.context.groups.some(group => group.IDG === row.IDG)) {
        await toggleHiddenGroups(true);
        if (!current(key) || state.groupId !== previousGroup) return;
      }
      if (!state.context.groups.some(group => group.IDG === row.IDG)) {
        throw new Error('Grupa copilului nu mai este disponibilă în luna selectată. Refaceți căutarea.');
      }
      // Footer filters must not hide the explicitly chosen child.
      Object.assign(state.childFilter, { presence: 'all', payments: false, refunds: false });
      if (state.groupId === row.IDG) await loadSituation();
      else await chooseGroup(row.IDG);
      if (!current(key) || state.groupId !== row.IDG) return;
      if (!state.grid?.selectRow(row.IDZ, { notify: true, reveal: true })) {
        throw new Error('Copilul nu mai este în situația lunii selectate. Refaceți căutarea.');
      }
      combo.setValue(String(row.IDZ), row.Nume);
    } finally {
      selecting = false;
      if (!disposed) combo.setEnabled(Boolean(ready()));
    }
  }

  function syncContext() {
    if (disposed) return;
    const key = contextKey();
    if (key !== scope || !ready()) {
      scope = key;
      cancel(); combo.clear(); combo.results = [];
    }
    combo.input.placeholder = ready() ? 'Nume sau CNP · minimum 3 caractere' : 'Selectați luna pentru căutare';
    combo.setEnabled(!selecting && Boolean(ready()));
  }

  function destroy() {
    if (disposed) return;
    disposed = true;
    cancel(); lifetime.abort(); combo.destroy();
  }
  window.addEventListener('pagehide', destroy, { once: true, signal: lifetime.signal });
  window.addEventListener('app:session-expired', destroy, { once: true, signal: lifetime.signal });
  syncContext();
  return { syncContext, destroy };
}
