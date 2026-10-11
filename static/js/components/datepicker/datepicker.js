/**
 * DATEPICKER - a calendar that replaces the browser's own date control.
 *
 * `new DatePicker(input, options)` takes an <input> (usually type="date") and keeps it as the single source of
 * truth: it becomes type="hidden", always holds the ISO value (YYYY-MM-DD, or '' when empty) and gets a
 * `change` event whenever the value changes. A visible text field (dd.mm.yyyy, typing allowed) and the
 * calendar popup are built next to it, so code that reads `input.value` keeps working unchanged.
 *
 * options: { startDate: () => Date | null }  the month shown when nothing is picked yet (default: today).
 */
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

  setValue(k_date, k_notify = true) {
    this.value = k_date;
    this.source.value = this.iso;
    this.render();
    if (k_notify) this.source.dispatchEvent(new Event('change', { bubbles: true }));
  }

  render() { this.field.value = this.value ? toText(this.value) : ''; }

  commitText() {
    const k_text = this.field.value.trim();
    if (!k_text) { this.field.classList.remove('is-invalid'); this.setValue(null); return; }
    const k_date = fromText(k_text);
    if (!k_date) { this.field.classList.add('is-invalid'); return; }
    this.field.classList.remove('is-invalid');
    this.setValue(k_date);
  }

  onKey(k_event) {
    if (k_event.key === 'Escape' && this.popup) { this.close(); k_event.preventDefault(); return; }
    if (k_event.key === 'ArrowDown' && !this.popup) { this.open(); k_event.preventDefault(); return; }
    if (k_event.key === 'Enter') { this.commitText(); this.close(); }
  }

  open() {
    const k_start = this.value || this.options.startDate?.() || new Date();
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
    const k_box = this.field.getBoundingClientRect();
    const k_pop = this.popup.getBoundingClientRect();
    const k_below = k_box.bottom + 2;
    const k_top = k_below + k_pop.height > innerHeight ? Math.max(4, k_box.top - k_pop.height - 2) : k_below;
    this.popup.style.top = `${k_top}px`;
    this.popup.style.left = `${Math.max(4, Math.min(k_box.left, innerWidth - k_pop.width - 4))}px`;
  }

  shift(k_months) {
    this.view = new Date(this.view.getFullYear(), this.view.getMonth() + k_months, 1);
    this.draw();
  }

  draw() {
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
      k_cells.push(`<button type="button" class="${k_classes.join(' ')}" data-iso="${k_iso}">${k_day}</button>`);
    }
    this.popup.innerHTML = `<div class="kdp__head"><button type="button" class="kdp__nav" data-nav="-12" aria-label="Anul anterior">«</button>
      <button type="button" class="kdp__nav" data-nav="-1" aria-label="Luna anterioară">‹</button>
      <strong>${MONTHS[k_month]} ${k_year}</strong>
      <button type="button" class="kdp__nav" data-nav="1" aria-label="Luna următoare">›</button>
      <button type="button" class="kdp__nav" data-nav="12" aria-label="Anul următor">»</button></div>
      <div class="kdp__week">${WEEKDAYS.map((k_name) => `<span>${k_name}</span>`).join('')}</div>
      <div class="kdp__grid">${k_cells.join('')}</div>
      <div class="kdp__foot"><button type="button" data-act="today">Azi</button><button type="button" data-act="clear">Șterge</button></div>`;
    this.popup.querySelectorAll('[data-nav]').forEach((k_node) => k_node.addEventListener('click', () => this.shift(Number(k_node.dataset.nav))));
    this.popup.querySelectorAll('[data-iso]').forEach((k_node) => k_node.addEventListener('click', () => {
      this.setValue(fromIso(k_node.dataset.iso));
      this.close();
    }));
    this.popup.querySelector('[data-act="today"]').addEventListener('click', () => { this.setValue(fromIso(toIso(new Date()))); this.close(); });
    this.popup.querySelector('[data-act="clear"]').addEventListener('click', () => { this.setValue(null); this.close(); });
  }

  destroy() {
    this.close();
    this.wrap.remove();
    this.source.type = 'date';
  }
}
