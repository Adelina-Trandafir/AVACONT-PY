import { confirmBox } from '../utils/confirm-box.js';

const $ = (id) => document.getElementById(id);
const fields = [
  ['name', 'Nume plătitor', 'platitor_nume', 'text', 50],
  ['cui', 'Cod fiscal', 'platitor_cui', 'text', 255],
  ['iban', 'Cont plătitor', 'platitor_iban', 'text', 255],
  ['date', 'Data document', 'DataDoc', 'date'],
  ['number', 'Nr. document', 'NrDoc', 'text', 255],
  ['amount', 'Sumă (lei)', 'suma_credit', 'number'],
  ['explanation', 'Explicație document', 'explanation', 'text', 255],
];

export function bindBankPreview({ api, context, month, filters, refresh, run, message }) {
  const host = $('ade-bank-auto-table');
  const save = $('ade-bank-auto-save');
  let drafts = []; let targets = []; let next = null; let dirty = false; let operation = null;
  let queryUsed = null; let loadSequence = 0; let draftCache = new Map();
  const canSave = () => ['collect', 'catalog'].every((right) => context()?.permissions.includes(right));
  const targetOf = (draft) => draft.reviewedTarget?.child_id === Number(draft.select.value)
    ? draft.reviewedTarget : targets.find((item) => item.child_id === Number(draft.select.value));
  function update() {
    const selected = drafts.filter((draft) => draft.check.checked);
    save.disabled = !canSave() || !selected.length || selected.some((draft) => !targetOf(draft));
    save.textContent = `Generează documentele bifate (${selected.length})`;
    $('ade-bank-auto-next').hidden = next == null;
  }
  function invalidate() {
    loadSequence += 1;
    drafts = []; targets = []; next = null; dirty = false; operation = null; queryUsed = null;
    draftCache.clear();
    host.replaceChildren(); update();
  }
  function cell(row, value) {
    const node = document.createElement('td');
    if (value instanceof Node) node.append(value); else node.textContent = String(value ?? '');
    row.append(node); return node;
  }
  function rememberDrafts() {
    if (!dirty) return;
    for (const draft of drafts) draftCache.set(draft.source.IDFXE, {
      source: draft.source, target: targetOf(draft), checked: draft.check.checked,
      values: Object.fromEntries(Object.entries(draft.inputs).map(([key, input]) => [key, input.value])),
    });
  }
  function build(data, preserve = false) {
    const modified = dirty;
    if (preserve) rememberDrafts(); else draftCache.clear();
    drafts = []; targets = data.targets; next = data.next; dirty = preserve && modified;
    const table = document.createElement('table'); table.className = 'ade-bank-review-table';
    const head = document.createElement('thead'); const titles = document.createElement('tr');
    const all = document.createElement('input'); all.type = 'checkbox'; all.setAttribute('aria-label', 'Bifează toate rândurile cu copil ales');
    const first = document.createElement('th'); first.append(all); titles.append(first);
    ['Extras / potrivire', 'Copil · grupă · plătitor activ', ...fields.map((item) => item[1])].forEach((label) => {
      const th = document.createElement('th'); th.textContent = label; titles.append(th);
    });
    head.append(titles); table.append(head);
    const body = document.createElement('tbody'); table.append(body);
    for (const current of data.rows) {
      const cached = preserve ? draftCache.get(current.IDFXE) : null;
      // Retain the fingerprint and reviewed target versions until the operator explicitly reloads.
      const source = cached ? { ...cached.source, status: current.status } : current;
      const row = document.createElement('tr'); const inputs = {};
      const check = document.createElement('input'); check.type = 'checkbox'; check.setAttribute('aria-label', `Înregistrează extrasul ${source.IDFXE}`);
      cell(row, check);
      const status = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = source.platitor_nume || '(fără nume)';
      const detail = document.createElement('p'); detail.textContent = source.status;
      status.append(title, detail); cell(row, status);
      const select = document.createElement('select'); select.setAttribute('aria-label', `Copil pentru extrasul ${source.IDFXE}`);
      select.add(new Option('Alegeți explicit copilul', ''));
      targets.forEach((target) => select.add(new Option(`${target.child} · ${target.group} · ${target.payer}`, String(target.child_id))));
      const reviewedTarget = cached ? cached.target : source.target;
      if (reviewedTarget && !targets.some((target) => target.child_id === reviewedTarget.child_id))
        select.add(new Option(`${reviewedTarget.child} · ${reviewedTarget.group} · ${reviewedTarget.payer}`, String(reviewedTarget.child_id)));
      select.value = reviewedTarget ? String(reviewedTarget.child_id) : ''; cell(row, select);
      fields.forEach(([key, label, field, type, limit]) => {
        const input = document.createElement('input'); input.type = type;
        input.setAttribute('aria-label', `${label}, extras ${source.IDFXE}`);
        input.value = type === 'date' ? String(source[field] || '').slice(0, 10) : String(source[field] ?? '');
        if (cached) input.value = cached.values[key];
        if (limit) input.maxLength = limit;
        if (type === 'number') { input.min = '1'; input.step = '1'; input.max = '2147483647'; }
        if (['name', 'date', 'number', 'amount', 'explanation'].includes(key)) input.required = true;
        inputs[key] = input; cell(row, input);
        input.addEventListener('input', () => { dirty = true; update(); });
      });
      const exact = Number.isInteger(Number(source.suma_credit)) && Number(source.suma_credit) > 0 && Number(source.suma_credit) <= 2147483647;
      const complete = Boolean(source.platitor_nume && source.platitor_nume.length <= 50 && source.NrDoc && source.DataDoc);
      check.checked = cached ? cached.checked : Boolean(source.target && exact && complete);
      check.disabled = !reviewedTarget;
      if (!exact) detail.textContent += ' · suma are zecimale sau depășește câmpul Plata; revizuiți înainte de bifare';
      if (!complete) detail.textContent += ' · revizuiți numele / numărul / data documentului';
      const draft = { source, check, select, inputs, reviewedTarget }; drafts.push(draft);
      select.addEventListener('change', () => {
        dirty = true; draft.reviewedTarget = targets.find((target) => target.child_id === Number(select.value)) || null;
        check.disabled = !targetOf(draft); if (check.disabled) check.checked = false; update();
      });
      check.addEventListener('change', () => { dirty = true; update(); });
      body.append(row);
    }
    all.addEventListener('change', () => {
      drafts.forEach((draft) => { draft.check.checked = all.checked && Boolean(targetOf(draft)); });
      dirty = true; update();
    });
    host.replaceChildren(table);
    if (!data.rows.length) { const empty = document.createElement('p'); empty.textContent = 'Nu există încasări neasociate pentru filtrele alese.'; host.append(empty); }
    update();
  }
  async function discardChanges() {
    if (!dirty) return true;
    return confirmBox('Se vor înlocui bifele și modificările din previzualizare.', {
      title: 'Actualizează previzualizarea', yes: 'Continuă', no: 'Păstrează modificările' });
  }
  async function load(cursor = null, force = false, { background = false } = {}) {
    if (!month()) { invalidate(); message('Alegeți luna înregistrării.', 'error'); return false; }
    if (!background && !force && !await discardChanges()) return false;
    const own = ++loadSequence;
    const query = cursor != null && queryUsed ? new URLSearchParams(queryUsed) : filters();
    query.set('IDL', month().IDL);
    if (cursor != null) query.set('before', cursor); else query.delete('before');
    const result = await api(`/api/adechit/bank-extracts/preview?${query}`);
    if (own !== loadSequence || !$('ade-bank-dialog').open || $('ade-bank-auto-panel').hidden) return false;
    queryUsed = query.toString(); build(result, background);
    message(''); return true;
  }
  function collect() {
    return drafts.filter((draft) => draft.check.checked).map((draft) => {
      const target = targetOf(draft);
      if (!target) throw new Error(`Alegeți copilul pentru extrasul ${draft.source.IDFXE}.`);
      for (const input of Object.values(draft.inputs)) {
        if (!input.reportValidity()) throw new Error(`Corectați datele extrasului ${draft.source.IDFXE}.`);
      }
      const values = Object.fromEntries(Object.entries(draft.inputs).filter(([key]) => key !== 'explanation')
        .map(([key, input]) => [key, key === 'amount' ? Number(input.value) : input.value.trim()]));
      if (values.name.length > 50) throw new Error(`Numele plătitorului pentru extrasul ${draft.source.IDFXE} poate avea maximum 50 de caractere.`);
      return { extract_id: draft.source.IDFXE, fingerprint: draft.source.fingerprint,
        payer_id: target.payer_id, payer_version: target.payer_version, child_version: target.child_version,
        child_id: target.child_id, month_id: target.month_id, month_version: target.month_version,
        values, explanation: draft.inputs.explanation.value.trim() };
    });
  }
  save.addEventListener('click', () => run(async () => {
    loadSequence += 1;
    if (!canSave()) return;
    const items = collect(); if (!items.length) return;
    const body = JSON.stringify({ items });
    if (!operation || operation.body !== body) operation = { body, key: crypto.randomUUID() };
    const amount = items.reduce((sum, item) => sum + item.values.amount, 0).toLocaleString('ro-RO');
    if (!await confirmBox(`${items.length} documente distincte · total ${amount} lei · ${context().subunit.name} · ${month().LunaT} ${month().Anul}.\n`
      + 'Se vor salva datele editate din rândurile bifate și se va păstra istoricul plătitorilor.', {
      title: 'Generează documentele revizuite', yes: 'Generează', no: 'Înapoi la previzualizare' })) return;
    const result = await api('/api/adechit/bank-extracts/selected', { method: 'POST', ...operation });
    operation = null; dirty = false; draftCache.clear();
    // Disable the completed proposals immediately, even if the subsequent reload fails.
    drafts.filter((draft) => draft.check.checked).forEach((draft) => { draft.check.checked = false; draft.check.disabled = true; });
    save.disabled = true;
    try { await refresh(); await load(null, true); }
    catch (error) { invalidate(); throw new Error(`${result.count} documente au fost salvate. Reîncărcarea a eșuat: ${error.message}`); }
    message(`${result.count} documente generate.`, 'info');
  }));
  $('ade-bank-auto-next').addEventListener('click', () => run(() => load(next)));
  return { load, invalidate, discardChanges, cancelLoad: () => { loadSequence += 1; } };
}
