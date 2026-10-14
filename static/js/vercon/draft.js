// slice VC02-01/03: the draft of one document, kept OUTSIDE the DOM. Rows are found by a stable key (the server id,
// or a client key for a new row), never by position, so scrolling, virtual rows or a repaint cannot lose an edit.
// The draft remembers the context it was started in; a different context needs an explicit keep/discard decision.
let k_counter = 0;
export const newRowKey = () => `n${++k_counter}`;

export function createDraft(k_context) {
  const k_header = {};            // field -> raw text as typed
  const k_rows = new Map();       // rowKey -> { field -> raw text }
  const k_order = [];             // rowKeys in display order
  let k_dirty = false;
  return {
    context: k_context,           // { unit, subunit, gestiune, year, form }
    get dirty() { return k_dirty; },
    setHeader(k_field, k_text) { k_header[k_field] = k_text; k_dirty = true; },
    header() { return { ...k_header }; },
    addRow() { const k_key = newRowKey(); k_rows.set(k_key, {}); k_order.push(k_key); k_dirty = true; return k_key; },
    removeRow(k_key) { k_rows.delete(k_key); const k_at = k_order.indexOf(k_key); if (k_at >= 0) k_order.splice(k_at, 1); k_dirty = true; },
    setCell(k_key, k_field, k_text) {
      const k_row = k_rows.get(k_key);
      if (!k_row) throw new Error(`Randul ${k_key} nu exista in draft.`);
      k_row[k_field] = k_text; k_dirty = true;
    },
    rows() { return k_order.map((k_key) => ({ key: k_key, cells: { ...k_rows.get(k_key) } })); },
    // validated, ready to send: { header, lines } or { errors: [{where, field, message}] }
    collect(k_columns, k_parse) {
      const k_errors = []; const k_out = { header: {}, lines: [] };
      for (const k_col of k_columns.header) {
        const k_r = k_parse(k_header[k_col.key], k_col);
        if (k_r.ok) k_out.header[k_col.key] = k_r.value; else k_errors.push({ where: 'header', field: k_col.key, message: k_r.message });
      }
      for (const k_row of this.rows()) {
        const k_line = { key: k_row.key };
        for (const k_col of k_columns.lines) {
          const k_r = k_parse(k_row.cells[k_col.key], k_col);
          if (k_r.ok) k_line[k_col.key] = k_r.value; else k_errors.push({ where: k_row.key, field: k_col.key, message: k_r.message });
        }
        k_out.lines.push(k_line);
      }
      return k_errors.length ? { errors: k_errors } : k_out;
    },
    markSaved() { k_dirty = false; },
  };
}
