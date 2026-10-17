// SLICE-AD12: the ADECHIT help. One content file (static/help/adechit.json) feeds three outputs: this dialog (table of
// contents on the left, formatted text on the right), the print window, and the server's PDF (routes/adechit/help_pdf.py).
// Text markup: **bold** and [text](#id); blocks: p, h (sub-title, optional id), ul, ol, note, table {head, rows}.
import { outputReport } from './reports.js';
import { trackSessionWindow } from '../utils/session-exit.js';

const CONTENT_URL = '/static/help/adechit.json';
const TOKEN = /(\*\*[^*]+\*\*|\[[^\]]+\]\(#[a-z0-9-]+\))/;
let loaded = null;

async function loadContent() {
  if (loaded) return loaded;
  try {
    const k_response = await fetch(CONTENT_URL, { headers: { Accept: 'application/json' } });
    if (!k_response.ok) throw new Error(`Ajutorul nu a putut fi încărcat (HTTP ${k_response.status}).`);
    loaded = await k_response.json();
    return loaded;
  } catch (k_error) { console.error('[ADE help] load failed', k_error); throw k_error; }
}

function inline(k_doc, k_parent, k_text) {
  for (const k_part of k_text.split(TOKEN)) {
    if (!k_part) continue;
    if (k_part.startsWith('**')) { const k_b = k_doc.createElement('strong'); k_b.textContent = k_part.slice(2, -2); k_parent.append(k_b); continue; }
    const k_link = /^\[([^\]]+)\]\(#([a-z0-9-]+)\)$/.exec(k_part);
    if (k_link) {
      const k_a = k_doc.createElement('a'); k_a.href = `#${k_link[2]}`; k_a.dataset.helpLink = k_link[2]; k_a.textContent = k_link[1]; k_parent.append(k_a);
    } else k_parent.append(k_doc.createTextNode(k_part));
  }
}

function el(k_doc, k_tag, k_class, k_text) {
  const k_node = k_doc.createElement(k_tag);
  if (k_class) k_node.className = k_class;
  if (k_text != null) k_node.textContent = k_text;
  return k_node;
}

function renderBlock(k_doc, k_block) {
  if (k_block.p) { const k_p = el(k_doc, 'p'); inline(k_doc, k_p, k_block.p); return k_p; }
  if (k_block.h) { const k_h = el(k_doc, 'h3', '', k_block.h); if (k_block.id) k_h.dataset.helpId = k_block.id; return k_h; }
  if (k_block.ul || k_block.ol) {
    const k_list = el(k_doc, k_block.ul ? 'ul' : 'ol');
    for (const k_item of k_block.ul || k_block.ol) { const k_li = el(k_doc, 'li'); inline(k_doc, k_li, k_item); k_list.append(k_li); }
    return k_list;
  }
  if (k_block.note) { const k_n = el(k_doc, 'p', 'ade-help-note'); inline(k_doc, k_n, k_block.note); return k_n; }
  if (k_block.table) {
    const k_table = el(k_doc, 'table'); const k_head = el(k_doc, 'thead'); const k_hr = el(k_doc, 'tr');
    for (const k_cell of k_block.table.head) k_hr.append(el(k_doc, 'th', '', k_cell));
    k_head.append(k_hr); k_table.append(k_head);
    const k_body = el(k_doc, 'tbody');
    for (const k_row of k_block.table.rows) {
      const k_tr = el(k_doc, 'tr');
      for (const k_cell of k_row) { const k_td = el(k_doc, 'td'); inline(k_doc, k_td, k_cell); k_tr.append(k_td); }
      k_body.append(k_tr);
    }
    k_table.append(k_body);
    const k_wrap = el(k_doc, 'div', 'ade-help-table'); k_wrap.append(k_table);
    return k_wrap;
  }
  throw new Error('Bloc de ajutor necunoscut.');
}

// The sections, as elements of `k_doc` (the dialog's document or the print window's).
function renderSections(k_doc, k_content) {
  return k_content.sections.map((k_section) => {
    const k_node = el(k_doc, 'section', 'ade-help-section'); k_node.dataset.helpId = k_section.id;
    k_node.append(el(k_doc, 'h2', '', k_section.title));
    for (const k_block of k_section.blocks) k_node.append(renderBlock(k_doc, k_block));
    return k_node;
  });
}

// Print: a window with the whole help (title, summary, every section); the browser's print dialog does the rest.
function printHelp(k_content) {
  const k_win = trackSessionWindow(window.open('', '_blank'));
  if (!k_win) throw new Error('Browserul a blocat fereastra de listare. Permiteți ferestrele popup pentru această pagină.');
  const k_doc = k_win.document;
  k_doc.title = k_content.title;
  const k_css = k_doc.createElement('link'); k_css.rel = 'stylesheet'; k_css.href = '/static/css/adechit-help.css';
  k_doc.head.append(k_css);
  k_doc.body.className = 'ade-help-print';
  const k_page = el(k_doc, 'main', 'ade-help-doc');
  k_page.append(el(k_doc, 'h1', '', k_content.title), el(k_doc, 'p', 'ade-help-subtitle', k_content.subtitle));
  const k_toc = el(k_doc, 'ol', 'ade-help-print-toc');
  for (const k_section of k_content.sections) {
    const k_li = el(k_doc, 'li'); const k_a = el(k_doc, 'a', '', k_section.title); k_a.href = `#print-${k_section.id}`; k_li.append(k_a); k_toc.append(k_li);
  }
  k_page.append(k_toc);
  for (const k_node of renderSections(k_doc, k_content)) {
    k_node.id = `print-${k_node.dataset.helpId}`;
    k_page.append(k_node);
  }
  // inside the printed copy a link jumps to its target in that same page
  k_page.addEventListener('click', (k_event) => {
    const k_link = k_event.target.closest('[data-help-link]');
    if (!k_link) return;
    k_event.preventDefault();
    const k_target = k_page.querySelector(`[data-help-id="${k_link.dataset.helpLink}"]`);
    k_target?.scrollIntoView();
  });
  k_doc.body.append(k_page);
  let k_done = false;
  const k_print = () => { if (!k_done) { k_done = true; k_win.focus(); k_win.print(); } };
  k_css.addEventListener('load', k_print);
  setTimeout(k_print, 1500);   // the style sheet is cached after the first load: do not wait for an event that already fired
}

export function bindHelp({ fetchOutput, report }) {
  const k_dialog = document.getElementById('ade-help-dialog');
  const k_toc = document.getElementById('ade-help-toc');
  const k_body = document.getElementById('ade-help-content');
  let k_targets = [];   // [{ id, node, link }] in document order, for the active entry of the summary

  const k_find = (k_id) => k_body.querySelector(`[data-help-id="${k_id}"]`);
  function go(k_id) {
    const k_node = k_find(k_id);
    if (!k_node) return;
    k_body.scrollTo({ top: k_node.offsetTop - 8, behavior: 'smooth' });
    markActive(k_id);
  }
  function markActive(k_id) {
    for (const k_item of k_targets) k_item.link.classList.toggle('is-active', k_item.id === k_id);
    k_targets.find((k_item) => k_item.id === k_id)?.link.scrollIntoView({ block: 'nearest' });
  }
  function build(k_content) {
    k_toc.replaceChildren(); k_body.replaceChildren(); k_targets = [];
    const k_head = el(document, 'header', 'ade-help-doc-head'); k_head.append(el(document, 'h1', '', k_content.title), el(document, 'p', 'ade-help-subtitle', k_content.subtitle));
    k_body.append(k_head, ...renderSections(document, k_content));
    for (const k_section of k_content.sections) {
      const k_entry = (k_id, k_label, k_class) => {
        const k_a = el(document, 'a', k_class, k_label); k_a.href = `#${k_id}`; k_a.dataset.helpLink = k_id; k_toc.append(k_a);
        k_targets.push({ id: k_id, node: k_find(k_id), link: k_a });
      };
      k_entry(k_section.id, k_section.title, 'ade-help-toc__section');
      for (const k_block of k_section.blocks) if (k_block.h && k_block.id) k_entry(k_block.id, k_block.h, 'ade-help-toc__sub');
    }
  }
  // Links, in the summary and in the text, all go to the element they name inside the dialog.
  k_dialog.addEventListener('click', (k_event) => {
    const k_link = k_event.target.closest('[data-help-link]');
    if (!k_link) return;
    k_event.preventDefault();
    go(k_link.dataset.helpLink);
  });
  k_body.addEventListener('scroll', () => {
    const k_top = k_body.scrollTop + 16;
    let k_current = k_targets[0];
    for (const k_item of k_targets) if (k_item.node && k_item.node.offsetTop <= k_top) k_current = k_item;
    if (k_current) markActive(k_current.id);
  }, { passive: true });

  async function open() {
    const k_content = await loadContent();
    if (!k_dialog.open) k_dialog.showModal();
    if (!k_targets.length) build(k_content);
    k_body.scrollTop = 0; markActive(k_content.sections[0].id);
  }
  document.getElementById('ade-help').addEventListener('click', () => open().catch(report));
  document.getElementById('ade-help-close').addEventListener('click', () => k_dialog.close());
  document.getElementById('ade-help-print').addEventListener('click', () => {
    // opened from the click so the browser accepts the window; the content is already loaded when the dialog is open
    try { printHelp(loaded); } catch (k_error) { report(k_error); }
  });
  document.getElementById('ade-help-pdf').addEventListener('click', () => {
    outputReport({ fetchOutput, path: '/api/adechit/help/pdf', pdf: true, title: 'Ajutor ADECHIT' }).catch(report);
  });
}
