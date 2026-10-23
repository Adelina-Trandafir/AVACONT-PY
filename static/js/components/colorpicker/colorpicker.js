import { CheckBox } from '../checkbox/checkbox.js';

// Shared color control. Empty value means use the component's/theme's automatic color.
export class ColorPicker {
  constructor(container, { value = '', fallback = '#302600', ariaLabel = 'Culoare', onChange } = {}) {
    this.abort = new AbortController();
    this.onChange = onChange;
    this.fallback = /^#[0-9a-f]{6}$/i.test(fallback) ? fallback : '#302600';
    this.element = document.createElement('div');
    this.element.className = 'kcolor';
    this.element.setAttribute('role', 'group');
    this.element.setAttribute('aria-label', ariaLabel);
    this.input = document.createElement('input');
    this.input.type = 'color';
    this.input.className = 'kcolor__picker';
    this.input.setAttribute('aria-label', ariaLabel);
    this.element.append(this.input);
    this.automatic = new CheckBox(this.element, { label: 'Automat',
      onChange: () => this.onChange?.(this.getValue(), this) });
    this.input.addEventListener('input', () => {
      this.automatic.checked = false;
      this.onChange?.(this.getValue(), this);
    }, { signal: this.abort.signal });
    container.append(this.element);
    this.setValue(value);
  }

  getValue() { return this.automatic.checked ? '' : this.input.value; }

  setValue(value) {
    const valid = typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value);
    this.automatic.checked = !valid;
    this.input.value = valid ? value : this.fallback;
  }

  focus() { this.input.focus(); }

  destroy() {
    this.abort.abort(); this.automatic.destroy(); this.element.remove();
  }
}
