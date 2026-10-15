/** Shared native add-action button. Load action-add-button.css alongside this module.
 * new ActionAddButton(containerOrNull, { icon, label, iconPosition, title, ariaLabel, disabled, className, onClick }).
 * The button is available as .element; null container leaves it detached for composition.
 */
export class ActionAddButton {
  constructor(container, options = {}) {
    this.options = { icon: '➕', label: '', iconPosition: 'left', title: '', ariaLabel: '', disabled: false, ...options };
    this._abort = new AbortController();
    this.element = document.createElement('button');
    this.element.type = 'button';
    this.element.className = 'k-action-add-button';
    for (const name of (options.className || '').split(/\s+/).filter(Boolean)) this.element.classList.add(name);
    this.icon = document.createElement('span');
    this.icon.className = 'k-action-add-button__icon';
    this.icon.setAttribute('aria-hidden', 'true');
    this.label = document.createElement('span');
    this.label.className = 'k-action-add-button__label';
    this.element.append(...(this.options.iconPosition === 'right' ? [this.label, this.icon] : [this.icon, this.label]));
    this.setIcon(this.options.icon);
    this.setLabel(this.options.label);
    this.setEnabled(!this.options.disabled);
    this.element.addEventListener('click', (event) => this.options.onClick?.(event, this), { signal: this._abort.signal });
    container?.append(this.element);
  }

  setIcon(icon) {
    this.options.icon = String(icon ?? '');
    this.icon.textContent = this.options.icon;
    this.icon.hidden = !this.options.icon;
  }

  setLabel(label) {
    this.options.label = String(label ?? '');
    this.label.textContent = this.options.label;
    this.label.hidden = !this.options.label;
    this.element.classList.toggle('k-action-add-button--icon-only', !this.options.label);
    this.element.title = this.options.title || this.options.label || 'Adaugă';
    this.element.setAttribute('aria-label', this.options.ariaLabel || this.element.title);
  }

  setEnabled(enabled) {
    this.options.disabled = !enabled;
    this.element.disabled = !enabled;
  }

  destroy() {
    this._abort.abort();
    this.element.remove();
  }
}
