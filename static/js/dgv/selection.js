// Range order comes from the filtered, sorted grid, including virtualized rows.
export function selectRows(selected, ordered, row, anchor, { shiftKey = false, ctrlKey = false, metaKey = false, toggle = false } = {}) {
  const next = new Set(shiftKey || ctrlKey || metaKey || toggle ? selected : []);
  const first = ordered.indexOf(anchor), last = ordered.indexOf(row);
  if (shiftKey && first >= 0 && last >= 0) {
    ordered.slice(Math.min(first, last), Math.max(first, last) + 1).forEach((item) => next.add(item));
  } else if ((ctrlKey || metaKey || toggle) && next.has(row)) next.delete(row);
  else next.add(row);
  return next;
}
