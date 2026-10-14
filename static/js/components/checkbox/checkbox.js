/**
 * CHECKBOX - a check box with its caption, in one element.
 *
 * `new CheckBox(container, options)` appends `<label class="cb"><input type="checkbox" class="cb__input"><span class="cb__text">` to
 * `container`. The real <input> is `checkBox.input`: code that reads `.checked` / listens to `change` keeps working.
 *
 * options:
 *   label       the caption (text; never HTML)
 *   labelSide   'right' (default) | 'left' - the side of the caption relative to the box
 *   checked, disabled
 *   name, id, ariaLabel
 *   className   extra class(es) for the label element, so a screen can keep its own styling hooks
 *   onChange(checked, checkBox)   when the operator ticks / unticks it (the DOM `change`)
 */
export class CheckBox {
  constructor(container, options = {}) {
    if (!container) throw new Error('CheckBox: container is required');
    const k_side = options.labelSide || 'right';
    if (!['left', 'right'].includes(k_side)) throw new Error(`CheckBox: unknown labelSide "${k_side}"`);
    this.options = { label: '', checked: false, disabled: false, ...options, labelSide: k_side };
    this._abort = new AbortController();

    this.element = document.createElement('label');
    this.element.className = `cb cb--${k_side}${options.className ? ` ${options.className}` : ''}`;
    this.input = document.createElement('input');
    this.input.type = 'checkbox';
    this.input.className = 'cb__input';
    if (options.name) this.input.name = options.name;
    if (options.id) this.input.id = options.id;
    if (options.ariaLabel) this.input.setAttribute('aria-label', options.ariaLabel);
    this.text = document.createElement('span');
    this.text.className = 'cb__text';
    this.text.textContent = this.options.label;
    this.element.append(...(k_side === 'left' ? [this.text, this.input] : [this.input, this.text]));
    container.append(this.element);

    this.checked = this.options.checked;
    this.setEnabled(!this.options.disabled);
    this.input.addEventListener('change', () => this.options.onChange?.(this.input.checked, this), { signal: this._abort.signal });
  }

  get checked() { return this.input.checked; }
  set checked(k_on) { this.input.checked = !!k_on; }

  setEnabled(k_on) {
    this.options.disabled = !k_on;
    this.input.disabled = !k_on;
    this.element.classList.toggle('is-disabled', !k_on);
  }

  destroy() {
    this._abort.abort();
    this.element.remove();
  }
}
