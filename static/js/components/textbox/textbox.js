/**
 * TEXTBOX - the project's text field: one place for what every screen used to repeat on a bare <input>
 * (type and keyboard hints, maximum length, "required" marking, error state, amount formatting, casing, selecting on focus).
 *
 * `new TextBox(container, options)` appends `<div class="tb"><input class="tb__input"></div>` to `container`.
 * The real <input> is `textBox.input`: code that reads `.value` / listens to DOM events keeps working.
 *
 * options (an unknown value of type / textAlign / casing throws):
 *   type          'text' | 'int' | 'decimal' | 'money' | 'search'
 *                   int / decimal: canonical value = integer / "1234.50" (field-format.js), the text shows a decimal comma;
 *                   money: like decimal, and OUT of focus the text is the amount for reading, 1.234,50 (2 decimals, thousands
 *                   mark); the exact digits typed are kept and come back from getValue(); 'search': a text field with a clear button.
 *   value         the canonical value to show (see setValue)
 *   required      empty means "not valid" (shown only by the error state, never before the field was left or validated)
 *   readOnly, disabled
 *   maxLength     characters (the box stops typing at it, and it is also validated)
 *   min           the smallest number (int / decimal / money)
 *   placeholder, name, id, ariaLabel
 *   textAlign     'left' | 'right' | 'center'  (numbers default to 'right')
 *   casing        'none' | 'upper' | 'lower'
 *   selectOnFocus select the whole text when the field gets focus
 *   mouseCursor   'ibeam' (default, the text cursor) | 'arrow' (the normal arrow). Anything else is taken as 'ibeam'. A disabled field always
 *                 shows the arrow.
 *   allowCopyPaste  default FALSE: copying, cutting, pasting and dropping text into the field are blocked (keyboard, context menu and
 *                 drag and drop alike). true: the browser's normal behaviour. Can be changed later with setAllowCopyPaste().
 *   clearable     a clear button while there is text (default: true for 'search')
 *   errorColor    CSS colour of the error state; default: the shared tokens --err-control-backcolor / --err-control-bordercolor
 *   errorTooltip  show the error message as the field's tooltip (default true). A tooltip the field already had is REPLACED while
 *                 the error lasts, and comes back when the error is gone.
 *   onInput(text, textBox)   at every change of the text
 *   onCommit(text, textBox)  when the text was changed and the field is left, or Enter is pressed (the DOM `change`)
 *   onError(message | null, textBox)  the error state changed or its message did
 *   onLeave(text, textBox, event)     the field was left, after the box judged itself (the place for a check the box cannot do, e.g. on the server)
 *
 * Validation. Nothing is judged while the operator types, and an untouched required box is NOT marked. A box is judged:
 *   - when the field is LEFT (lost focus) and it is required, or has text, or is already in the error state (so a text that does not
 *     pass shows the error colour; an empty field that is not required is never judged);
 *   - on the validation event: validate() is the ATTEMPT (the screen's scope calls it on every control when saving).
 * validate() parses the text; a failure puts the box in the error state (`is-invalid`, aria-invalid, errorColor, onError(message)),
 * a pass leaves it. A box in the error state judges itself again when the field is left, so the colour follows the text.
 * Read-only / disabled boxes are never marked.
 */
import { parse, format, formatMoney } from '../../utils/field-format.js';

const TYPES = ['text', 'int', 'decimal', 'money', 'search'];
const ALIGNS = ['left', 'right', 'center'];
const CASINGS = ['none', 'upper', 'lower'];
const CURSORS = ['ibeam', 'arrow'];
const NUMERIC = ['int', 'decimal', 'money'];

// Opt-in money display: numberLocale: 'en-US' uses 1,234.56 and parses it
// back to canonical decimal text. Other TextBoxes keep their existing format.
export class TextBox {
  constructor(container, options = {}) {
    if (!container) throw new Error('TextBox: container is required');
    const k_type = options.type || 'text';
    if (!TYPES.includes(k_type)) throw new Error(`TextBox: unknown type "${k_type}"`);
    const k_align = options.textAlign || (NUMERIC.includes(k_type) ? 'right' : 'left');
    if (!ALIGNS.includes(k_align)) throw new Error(`TextBox: unknown textAlign "${k_align}"`);
    const k_casing = options.casing || 'none';
    if (!CASINGS.includes(k_casing)) throw new Error(`TextBox: unknown casing "${k_casing}"`);
    const k_cursor = CURSORS.includes(options.mouseCursor) ? options.mouseCursor : 'ibeam';

    this.options = {
      required: false, readOnly: false, disabled: false, placeholder: '', selectOnFocus: false, clearable: k_type === 'search',
      errorColor: '', errorTooltip: true, allowCopyPaste: false, ...options, type: k_type, textAlign: k_align, casing: k_casing, mouseCursor: k_cursor,
    };
    this._abort = new AbortController();
    this._inError = false;
    this._message = '';
    this._title0 = null;    // the tooltip the field had before the error took it over (null: none taken)
    this._exact = null;     // money: the canonical value behind the text shown for reading
    this._shown = null;     // money: that text

    const k_o = this.options;
    this.element = document.createElement('div');
    this.element.className = `tb tb--${k_type} tb--align-${k_align} tb--cursor-${k_cursor}`;
    this.input = document.createElement('input');
    this.input.className = 'tb__input';
    this.input.type = k_type === 'search' ? 'search' : 'text';
    this.input.autocomplete = 'off';
    this.input.spellcheck = false;
    if (NUMERIC.includes(k_type)) this.input.inputMode = k_type === 'int' ? 'numeric' : 'decimal';
    if (k_o.placeholder) this.input.placeholder = k_o.placeholder;
    if (k_o.name) this.input.name = k_o.name;
    if (k_o.id) this.input.id = k_o.id;
    if (k_o.ariaLabel) this.input.setAttribute('aria-label', k_o.ariaLabel);
    if (k_o.maxLength) this.input.maxLength = k_o.maxLength;
    this.element.append(this.input);
    if (k_o.clearable) {
      this.clearButton = document.createElement('button');
      this.clearButton.type = 'button';
      this.clearButton.className = 'tb__clear';
      this.clearButton.tabIndex = -1;
      this.clearButton.setAttribute('aria-label', 'Șterge textul');
      this.clearButton.textContent = '×';
      this.element.append(this.clearButton);
    }
    if (k_o.errorColor) this.setErrorColor(k_o.errorColor);
    container.append(this.element);

    this.setValue(k_o.value);
    this.setReadOnly(k_o.readOnly);
    this.setEnabled(!k_o.disabled);
    this._bind();
  }

  // ---- reading ---------------------------------------------------------------------------------------------------------
  get text() { return this.input.value; }

  /** The column contract the text is judged against (see field-format.js). */
  _spec() {
    const k_o = this.options;
    return { type: k_o.type === 'search' ? 'text' : k_o.type === 'money' ? 'decimal' : k_o.type, required: k_o.required, min: k_o.min, max: k_o.maxLength };
  }

  /** { ok: true, value } | { ok: false, message }; does not touch the error state (validate() does). */
  result() {
    if (this.options.type === 'money' && this._shown !== null && this.input.value === this._shown) return { ok: true, value: this._exact };
    if (this.options.type === 'money' && this.options.numberLocale === 'en-US') {
      const text = this.input.value.trim();
      if (text && !/^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$/.test(text)) {
        return { ok: false, message: 'Introduceți valoarea în formatul 1,234.56.' };
      }
      return parse(text.replace(/,/g, ''), this._spec());
    }
    return parse(this.input.value, this._spec());
  }

  /** The canonical value, or null when the box is empty or its text is not valid. */
  getValue() {
    const k_result = this.result();
    return k_result.ok ? k_result.value : null;
  }

  // ---- writing ---------------------------------------------------------------------------------------------------------
  /** Shows a canonical value (null / '' = empty). The error state is left as it is. */
  setValue(k_value) {
    this._exact = null; this._shown = null;
    const k_o = this.options;
    if (k_value === null || k_value === undefined || k_value === '') this.input.value = '';
    else if (k_o.type === 'money') this._showMoney(String(k_value));
    else this.input.value = NUMERIC.includes(k_o.type) ? format(k_value, { type: k_o.type === 'int' ? 'int' : 'decimal' }) : String(k_value);
    this._refresh();
  }

  _showMoney(k_canonical) {
    const text = formatMoney(k_canonical);
    this.input.value = this.options.numberLocale === 'en-US'
      ? text.replace(/[.,]/g, separator => separator === '.' ? ',' : '.') : text;
    this._exact = k_canonical;
    this._shown = this.input.value;
  }

  setRequired(k_on) { this.options.required = !!k_on; this._refresh(); }
  setMouseCursor(k_value) {
    const k_cursor = CURSORS.includes(k_value) ? k_value : 'ibeam';
    this.element.classList.remove(`tb--cursor-${this.options.mouseCursor}`);
    this.options.mouseCursor = k_cursor;
    this.element.classList.add(`tb--cursor-${k_cursor}`);
  }
  setAllowCopyPaste(k_on) { this.options.allowCopyPaste = !!k_on; }
  setReadOnly(k_on) { this.options.readOnly = !!k_on; this.input.readOnly = !!k_on; this._refresh(); }
  setEnabled(k_on) { this.options.disabled = !k_on; this.input.disabled = !k_on; this._refresh(); }
  setErrorColor(k_color) {
    this.options.errorColor = k_color || '';
    if (k_color) {
      this.element.style.setProperty('--tb-error-color', k_color);
      this.element.style.setProperty('--tb-error-back', `color-mix(in srgb, ${k_color} 12%, var(--card))`);
    } else {
      this.element.style.removeProperty('--tb-error-color');
      this.element.style.removeProperty('--tb-error-back');
    }
  }
  focus() { this.input.focus(); }

  // ---- validation ------------------------------------------------------------------------------------------------------
  get inError() { return this._inError; }
  get message() { return this._message; }

  /** The attempt: judges the text now; a failure enters the error state. Returns the result() object. */
  validate() {
    const k_result = this.result();
    if (this.options.readOnly || this.options.disabled || k_result.ok) this.clearError(); else this.setError(k_result.message);
    return k_result;
  }

  /** Enters (or updates) the error state with a message, e.g. one that came back from the server. */
  setError(k_message) {
    const k_changed = !this._inError || this._message !== (k_message || '');
    this._inError = true;
    this._message = k_message || '';
    this.element.classList.add('is-invalid');
    this.input.setAttribute('aria-invalid', 'true');
    if (this.options.errorTooltip) {
      if (this._title0 === null) this._title0 = this.input.title;
      this._setTitle(this._message || this._title0);
    }
    if (k_changed) this.options.onError?.(this._message, this);
  }

  clearError() {
    if (!this._inError) return;
    this._inError = false;
    this._message = '';
    this.element.classList.remove('is-invalid');
    this.input.removeAttribute('aria-invalid');
    if (this._title0 !== null) { this._setTitle(this._title0); this._title0 = null; }
    this.options.onError?.(null, this);
  }

  _setTitle(k_text) {
    if (k_text) this.input.title = k_text; else this.input.removeAttribute('title');
  }

  // ---- internals -------------------------------------------------------------------------------------------------------
  _refresh() {
    const k_o = this.options;
    this.element.classList.toggle('is-readonly', !!k_o.readOnly);
    this.element.classList.toggle('is-empty', this.input.value === '');
  }

  _bind() {
    const k_signal = { signal: this._abort.signal };
    const k_o = this.options;
    // Windows maps the numpad decimal key to ',' on Romanian keyboards.
    // Use the field's separator, including '.' for the NIR's en-US amount.
    this.input.addEventListener('keydown', (k_event) => {
      if (!['decimal', 'money'].includes(k_o.type) || this.input.readOnly || this.input.disabled
        || k_event.defaultPrevented || k_event.isComposing || k_event.ctrlKey || k_event.altKey || k_event.metaKey
        || k_event.code !== 'NumpadDecimal' || !['.', ',', 'Decimal'].includes(k_event.key)) return;
      // With Num Lock off the same physical key is Delete; leave that action alone.
      const k_separator = k_o.numberLocale === 'en-US' ? '.' : ',';
      k_event.preventDefault();
      this.input.setRangeText(k_separator, this.input.selectionStart, this.input.selectionEnd, 'end');
      this.input.dispatchEvent(new InputEvent('input', {
        bubbles: true, inputType: 'insertText', data: k_separator,
      }));
    }, k_signal);
    this.input.addEventListener('input', () => {
      if (k_o.casing !== 'none') {
        const k_start = this.input.selectionStart, k_end = this.input.selectionEnd;
        const k_text = k_o.casing === 'upper' ? this.input.value.toLocaleUpperCase('ro') : this.input.value.toLocaleLowerCase('ro');
        if (k_text !== this.input.value) { this.input.value = k_text; this.input.setSelectionRange(k_start, k_end); }
      }
      this._refresh();
      k_o.onInput?.(this.input.value, this);
    }, k_signal);
    this.input.addEventListener('change', () => k_o.onCommit?.(this.input.value, this), k_signal);
    // allowCopyPaste off: nothing goes into the field from outside and nothing is taken out of it (checked at the event, so the switch can change)
    for (const k_name of ['copy', 'cut', 'paste', 'drop']) this.input.addEventListener(k_name, (k_event) => { if (!this.options.allowCopyPaste) k_event.preventDefault(); }, k_signal);
    this.input.addEventListener('focus', () => {
      if (k_o.type === 'money' && k_o.numberLocale !== 'en-US' && this._shown !== null && this.input.value === this._shown) this.input.value = format(this._exact, { type: 'decimal' });
      if (k_o.selectOnFocus) this.input.select();
    }, k_signal);
    this.input.addEventListener('blur', (k_event) => {
      if (k_o.type === 'money') {
        const k_result = this.result();
        if (k_result.ok && k_result.value !== null) this._showMoney(k_result.value);
        else { this._exact = null; this._shown = null; }
      }
      if (this._inError || k_o.required || this.input.value.trim() !== '') this.validate();   // left: judged now when required, with text, or in error; the colour follows
      this._refresh();
      k_o.onLeave?.(this.input.value, this, k_event);
    }, k_signal);
    if (this.clearButton) {
      this.clearButton.addEventListener('click', () => {
        if (k_o.readOnly || k_o.disabled || this.input.value === '') return;
        this.input.value = ''; this._exact = null; this._shown = null; this._refresh();
        this.input.dispatchEvent(new Event('input', { bubbles: true }));
        this.input.dispatchEvent(new Event('change', { bubbles: true }));
        this.input.focus();
      }, k_signal);
    }
  }

  destroy() {
    this._abort.abort();
    this.element.remove();
  }
}
