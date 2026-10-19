/**
 * DATEPICKER - a calendar that replaces the browser's own date control.
 *
 * `new DatePicker(input, options)` takes an <input> (usually type="date") and keeps it as the single source of
 * truth: it becomes type="hidden", always holds the ISO value (YYYY-MM-DD, or '' when empty) and gets a
 * `change` event whenever the value changes. A visible text field (dd.mm.yyyy, typing allowed) and the
 * calendar popup are built next to it, so code that reads `input.value` keeps working unchanged.
 *
 * options: { startDate: () => Date | null, min, max, years }.
 * Bounds are ISO dates; years optionally restricts the available calendar years.
 */
import { layoutRect, layoutViewport } from '../../utils/layout-geometry.js';

const MONTHS = ['ianuarie', 'februarie', 'martie', 'aprilie', 'mai', 'iunie', 'iulie', 'august', 'septembrie', 'octombrie', 'noiembrie', 'decembrie'];
const WEEKDAYS = ['Lu', 'Ma', 'Mi', 'Jo', 'Vi', 'Sâ', 'Du'];

const pad = (k_n) => String(k_n).padStart(2, '0');
const toIso = (k_date) => `${k_date.getFullYear()}-${pad(k_date.getMonth() + 1)}-${pad(k_date.getDate())}`;
const toText = (k_date) => `${pad(k_date.getDate())}.${pad(k_date.getMonth() + 1)}.${k_date.getFullYear()}`;

function fromIso(k_text) {
  const k_match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(k_text || '');
  if (!k_match) return null;
  const k_date = new Date(Number(k_match[1]), Number(k_match[2]) - 1, Number(k_match[3]));
  return toIso(k_date) === k_text ? k_date : null;
}

// dd.mm.yyyy (also dd/mm/yyyy and dd-mm-yyyy); null when it is not a real date
function fromText(k_text) {
  const k_match = /^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/.exec((k_text || '').trim());
  if (!k_match) return null;
  const k_date = new Date(Number(k_match[3]), Number(k_match[2]) - 1, Number(k_match[1]));
  return k_date.getDate() === Number(k_match[1]) && k_date.getMonth() === Number(k_match[2]) - 1 ? k_date : null;
}

export class DatePicker {
  constructor(input, options = {}) {
    if (!input) throw new Error('DatePicker: input is required');
    this.source = input;
    this.options = options;
    this.value = fromIso(input.value);
    this.view = null; // first day of the month on show
    this.popup = null;
    this.setBounds({ min: options.min || input.min, max: options.max || input.max, years: options.years });

    const k_label = input.getAttribute('aria-label') || '';
    input.type = 'hidden';
    this.wrap = document.createElement('div');
    this.wrap.className = 'kdp';
    this.field = document.createElement('input');
    this.field.type = 'text';
    this.field.className = 'kdp__field';
    this.field.autocomplete = 'off';
    this.field.placeholder = 'zz.ll.aaaa';
    if (k_label) this.field.setAttribute('aria-label', k_label);
    this.button = document.createElement('button');
    this.button.type = 'button';
    this.button.className = 'kdp__button';
    this.button.tabIndex = -1;
    this.button.setAttribute('aria-label', 'Deschide calendarul');
    this.button.textContent = '📅';
    this.wrap.append(this.field, this.button);
    input.after(this.wrap);

    this.onDocDown = (k_event) => { if (this.popup && !this.wrap.contains(k_event.target) && !this.popup.contains(k_event.target)) this.close(); };
    this.field.addEventListener('focus', () => this.field.select());
    this.field.addEventListener('keydown', (k_event) => this.onKey(k_event));
    this.field.addEventListener('change', () => this.commitText());
    this.button.addEventListener('click', () => (this.popup ? this.close() : this.open()));
    this.field.addEventListener('click', () => { if (!this.popup) this.open(); });
    this.render();
  }

  get iso() { return this.value ? toIso(this.value) : ''; }

  setBounds({ min, max, years } = {}) {
    this.min = fromIso(min);
    this.max = fromIso(max);
    this.years = years?.length ? [...new Set(years.map(Number))].sort((a, b) => a - b) : null;
    this.source.min = this.min ? toIso(this.min) : '';
    this.source.max = this.max ? toIso(this.max) : '';
    if (this.popup) { this.draw(); this.place(); }
  }

  allows(date) {
    const iso = toIso(date);
    return (!this.min || iso >= toIso(this.min)) && (!this.max || iso <= toIso(this.max))
      && (!this.years || this.years.includes(date.getFullYear()));
  }

  allowsMonth(date) {
    const first = new Date(date.getFullYear(), date.getMonth(), 1);
    const last = new Date(date.getFullYear(), date.getMonth() + 1, 0);
    return (!this.min || last >= this.min) && (!this.max || first <= this.max)
      && (!this.years || this.years.includes(first.getFullYear()));
  }

  setValue(k_date, k_notify = true) {
    if (k_date && !this.allows(k_date)) { this.field.classList.add('is-invalid'); return false; }
    const previous = this.iso;
    this.value = k_date;
    this.field.classList.remove('is-invalid');   // a date chosen from the calendar (or Today / Clear) is a valid one
    this.source.value = this.iso;
    this.render();
    if (k_notify && previous !== this.iso) this.source.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  render() { this.field.value = this.value ? toText(this.value) : ''; }

  commitText() {
    const k_text = this.field.value.trim();
    if (!k_text) { this.field.classList.remove('is-invalid'); this.setValue(null); return; }
    const k_date = fromText(k_text);
    if (!k_date || !this.allows(k_date)) { this.field.classList.add('is-invalid'); return; }
    this.field.classList.remove('is-invalid');
    this.setValue(k_date);
  }

  onKey(k_event) {
    if (k_event.key === 'Escape' && this.popup) { this.close(); k_event.preventDefault(); return; }
    if (k_event.key === 'ArrowDown' && !this.popup) { this.open(); k_event.preventDefault(); return; }
    if (k_event.key === 'Enter') { this.commitText(); this.close(); }
  }

  open() {
    let k_start = this.value || this.options.startDate?.() || new Date();
    if (this.min && k_start < this.min) k_start = this.min;
    if (this.max && k_start > this.max) k_start = this.max;
    if (this.years && !this.years.includes(k_start.getFullYear())) {
      k_start = new Date(this.years.at(-1), 0, 1);
      if (this.min && k_start < this.min) k_start = this.min;
    }
    this.mode = 'days';
    this.view = new Date(k_start.getFullYear(), k_start.getMonth(), 1);
    this.popup = document.createElement('div');
    this.popup.className = 'kdp__popup';
    this.popup.setAttribute('role', 'dialog');
    this.popup.setAttribute('aria-label', 'Alegeți data');
    this.popup.addEventListener('mousedown', (k_event) => k_event.preventDefault());
    // Keep the calendar in the native modal's top layer when its input is in a dialog.
    (this.source.closest('dialog') || document.body).appendChild(this.popup);
    document.addEventListener('pointerdown', this.onDocDown, true);
    addEventListener('resize', () => this.close(), { once: true });
    this.draw();
    this.place();
  }

  close() {
    if (!this.popup) return;
    this.popup.remove();
    this.popup = null;
    document.removeEventListener('pointerdown', this.onDocDown, true);
  }

  place() {
    const k_box = layoutRect(this.field);
    const k_pop = layoutRect(this.popup);
    const k_viewport = layoutViewport();
    const k_below = k_box.bottom + 2;
    const k_top = k_below + k_pop.height > k_viewport.height ? Math.max(4, k_box.top - k_pop.height - 2) : k_below;
    this.popup.style.top = `${k_top}px`;
    this.popup.style.left = `${Math.max(4, Math.min(k_box.left, k_viewport.width - k_pop.width - 4))}px`;
  }

  shift(k_months) {
    const next = new Date(this.view.getFullYear(), this.view.getMonth() + k_months, 1);
    if (!this.allowsMonth(next)) return;
    this.view = next;
    this.draw();
    this.place();
  }

  drawYears() {
    const current = this.view.getFullYear();
    const first = this.min?.getFullYear() ?? this.years?.[0] ?? 1000;
    const last = this.max?.getFullYear() ?? this.years?.at(-1) ?? 9999;
    const years = this.years || Array.from({ length: last - first + 1 }, (_, index) => first + index);
    const available = years.filter(year => year >= first && year <= last);
    const index = Math.max(0, available.indexOf(current));
    this.yearPage ??= Math.floor(index / 12);
    const page = available.slice(this.yearPage * 12, this.yearPage * 12 + 12);
    this.popup.innerHTML = `<div class="kdp__years-head">
      <button type="button" class="kdp__nav" data-page="-1" aria-label="Anii anteriori" ${this.yearPage === 0 ? 'disabled' : ''}>‹</button>
      <button type="button" class="kdp__title" data-act="days" aria-label="Înapoi la calendar">${page[0]} – ${page.at(-1)}</button>
      <button type="button" class="kdp__nav" data-page="1" aria-label="Anii următori" ${(this.yearPage + 1) * 12 >= available.length ? 'disabled' : ''}>›</button></div>
      <div class="kdp__years" role="group" aria-label="Ani disponibili">${page.map(year =>
        `<button type="button" class="kdp__day${year === current ? ' is-selected' : ''}" data-year="${year}">${year}</button>`).join('')}</div>`;
    this.popup.querySelectorAll('[data-page]').forEach(node => node.addEventListener('click', () => {
      this.yearPage += Number(node.dataset.page); this.draw(); this.place();
    }));
    this.popup.querySelector('[data-act="days"]').addEventListener('click', () => { this.mode = 'days'; this.draw(); this.place(); });
    this.popup.querySelectorAll('[data-year]').forEach(node => node.addEventListener('click', () => {
      const year = Number(node.dataset.year);
      let month = this.view.getMonth();
      if (this.min?.getFullYear() === year) month = Math.max(month, this.min.getMonth());
      if (this.max?.getFullYear() === year) month = Math.min(month, this.max.getMonth());
      this.view = new Date(year, month, 1); this.mode = 'days'; this.draw(); this.place();
    }));
  }

  draw() {
    if (this.mode === 'years') { this.drawYears(); return; }
    const k_year = this.view.getFullYear();
    const k_month = this.view.getMonth();
    const k_lead = (new Date(k_year, k_month, 1).getDay() + 6) % 7; // Monday first
    const k_days = new Date(k_year, k_month + 1, 0).getDate();
    const k_today = toIso(new Date());
    const k_cells = [];
    for (let k_i = 0; k_i < k_lead; k_i += 1) k_cells.push('<span class="kdp__day is-blank"></span>');
    for (let k_day = 1; k_day <= k_days; k_day += 1) {
      const k_iso = `${k_year}-${pad(k_month + 1)}-${pad(k_day)}`;
      const k_classes = ['kdp__day'];
      if (k_iso === this.iso) k_classes.push('is-selected');
      if (k_iso === k_today) k_classes.push('is-today');
      k_cells.push(`<button type="button" class="${k_classes.join(' ')}" data-iso="${k_iso}" ${this.allows(fromIso(k_iso)) ? '' : 'disabled'}>${k_day}</button>`);
    }
    this.popup.innerHTML = `<div class="kdp__head"><button type="button" class="kdp__nav" data-nav="-12" aria-label="Anul anterior">«</button>
      <button type="button" class="kdp__nav" data-nav="-1" aria-label="Luna anterioară">‹</button>
      <div class="kdp__heading"><span>${MONTHS[k_month]}</span> <button type="button" class="kdp__title" data-act="years" aria-label="Alegeți anul" aria-expanded="false">${k_year}</button></div>
      <button type="button" class="kdp__nav" data-nav="1" aria-label="Luna următoare">›</button>
      <button type="button" class="kdp__nav" data-nav="12" aria-label="Anul următor">»</button></div>
      <div class="kdp__week">${WEEKDAYS.map((k_name) => `<span>${k_name}</span>`).join('')}</div>
      <div class="kdp__grid">${k_cells.join('')}</div>
      <div class="kdp__foot"><button type="button" data-act="today">Azi</button><button type="button" data-act="clear">Șterge</button></div>`;
    this.popup.querySelectorAll('[data-nav]').forEach((k_node) => {
      k_node.disabled = !this.allowsMonth(new Date(k_year, k_month + Number(k_node.dataset.nav), 1));
      k_node.addEventListener('click', () => this.shift(Number(k_node.dataset.nav)));
    });
    this.popup.querySelector('[data-act="years"]').addEventListener('click', () => {
      this.mode = 'years'; this.yearPage = null; this.draw(); this.place();
    });
    this.popup.querySelectorAll('[data-iso]').forEach((k_node) => k_node.addEventListener('click', () => {
      this.setValue(fromIso(k_node.dataset.iso));
      this.close();
    }));
    const today = this.popup.querySelector('[data-act="today"]');
    today.disabled = !this.allows(fromIso(k_today));
    today.addEventListener('click', () => { this.setValue(fromIso(k_today)); this.close(); });
    this.popup.querySelector('[data-act="clear"]').addEventListener('click', () => { this.setValue(null); this.close(); });
  }

  destroy() {
    this.close();
    this.wrap.remove();
    this.source.type = 'date';
  }
}
