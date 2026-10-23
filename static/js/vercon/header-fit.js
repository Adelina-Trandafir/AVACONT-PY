// A document header gives way to its lines grid: when the grid cannot show `minRows` rows, the labels of the header move beside their
// controls (class `is-labels-inline`, css/vercon-header-inline.css), which takes one text line off every row of fields.
//
// The decision is always taken on the TALLER header (labels above), never on the current one. Without that, going inline would give the grid
// more rows, the next look would find enough rows and go back, and the header would flicker. The class is taken off, the rows are counted
// and the class is put back in the same frame, so nothing is painted in between.
const CLASS = 'is-labels-inline';
const ROW_FALLBACK = 30;   // the row height of the VERCON grids (ui.js grid()), used while the grid shows no row yet

export function headerFit({ box, header, lines, minRows = 10, signal }) {
  let frame = 0;

  function rowsShown() {
    const scroll = lines.querySelector('.dgv__scroll');
    if (!scroll || !scroll.clientHeight) return null;
    const head = scroll.querySelector('.dgv__head'), foot = scroll.querySelector('.dgv__foot');
    const rowHeight = scroll.querySelector('.dgv__row')?.offsetHeight || ROW_FALLBACK;
    const room = scroll.clientHeight - (head?.offsetHeight || 0) - (foot && !foot.hidden ? foot.offsetHeight : 0);
    return Math.floor(room / rowHeight);
  }
  function evaluate() {
    frame = 0;
    if (signal?.aborted) return;
    header.classList.remove(CLASS);
    const rows = rowsShown();
    header.classList.toggle(CLASS, rows !== null && rows < minRows);
  }
  function schedule() { if (!frame && !signal?.aborted) frame = requestAnimationFrame(evaluate); }

  const resize = new ResizeObserver(schedule);
  for (const node of [box, lines]) resize.observe(node);
  // the grid is built after the header (and again at every repaint): a new grid is a new measure
  const mutation = new MutationObserver(schedule);
  mutation.observe(lines, { childList: true });
  signal?.addEventListener('abort', () => { resize.disconnect(); mutation.disconnect(); cancelAnimationFrame(frame); }, { once: true });
  schedule();
  return { refresh: schedule };
}
