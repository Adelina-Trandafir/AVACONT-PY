/**
 * MODAL FRAME - turns an element that is already on the page into a fixed-size modal window over a blurred application.
 *
 * `new ModalFrame(element, { width, height, onRequestClose })`, then `open()` / `close()` / `destroy()`.
 * The element is NOT moved in the DOM (its controls, listeners and popups keep working as they are): it gets the class `kmodal`
 * (fixed, centred, `width` x `height` px but never larger than the window) and a backdrop with the same blur as the confirmation
 * box is put behind it. Esc asks `onRequestClose()` (the page decides: leave, or ask about unsaved work), unless Esc was meant for an
 * open list / calendar / menu. Tab and Shift+Tab wrap inside the window, so the keyboard never reaches the page behind.
 *
 * options: { width: 1100, height: 760, title: '', caption: true, showClose: true, onRequestClose: () => void }
 * caption can also be an existing header element; the frame adds its close button there without moving existing controls.
 */
import { registerDebugTool } from '../../utils/debug-tools.js';

const FOCUSABLE = 'input:not([type="hidden"]):not(:disabled), button:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';
const POPUPS = '.combobox-dropdown.visible, .kdp__popup, .ade-receipt-menu, .kmenu-popup';

const frames = new Set();   // the open frames (for the debug tool)
let sizeOverride = null;     // set from the console in debug mode: { width, height } for every frame

export class ModalFrame {
  constructor(element, options = {}) {
    if (!element) throw new Error('ModalFrame: element is required');
    this.element = element;
    this.options = { width: 1100, height: 760, title: '', caption: true, showClose: true, onRequestClose: null, ...options };
    this.backdrop = null;
    this.placeholder = null;
    this.isOpen = false;
    this._abort = null;
    this._escapeForPopup = false;
    this.caption = null;
    this.closeButton = null;
    this._ownsCaption = false;
  }

  open() {
    if (this.isOpen) return;
    this.isOpen = true;
    const k_z = Math.floor(window.ZIndexManager?.getNext?.() || 1000);
    // the element leaves the page flow (position: fixed): an invisible copy of it keeps its place, so nothing around it moves
    this.placeholder = this.element.cloneNode(false);
    this.placeholder.removeAttribute('id');
    this.placeholder.setAttribute('aria-hidden', 'true');
    this.placeholder.inert = true;
    this.placeholder.style.visibility = 'hidden';
    this.placeholder.style.height = `${this.element.getBoundingClientRect().height}px`;
    this.element.before(this.placeholder);
    this.backdrop = document.createElement('div');
    this.backdrop.className = 'kmodal-backdrop';
    this.backdrop.style.zIndex = String(k_z);
    document.body.append(this.backdrop);
    this.element.classList.add('kmodal');
    this.element.style.zIndex = String(k_z + 1);
    this.setSize(this.options.width, this.options.height);
    frames.add(this);
    this.element.setAttribute('role', 'dialog');
    this.element.setAttribute('aria-modal', 'true');
    this._abort = new AbortController();
    const { signal } = this._abort;
    this._openCaption(signal);
    // capture: remember whether an open list / calendar / menu is about to take this Esc (it closes itself before the event bubbles here)
    this.element.addEventListener('keydown', (k_event) => { if (k_event.key === 'Escape') this._escapeForPopup = !!document.querySelector(POPUPS); }, { capture: true, signal });
    this.element.addEventListener('keydown', (k_event) => this._onKey(k_event), { signal });
  }

  /** Size in px (the window never gets larger than 96% x 94% of the screen). The debug override, when set, wins. */
  setSize(k_width, k_height) {
    const k_w = sizeOverride?.width ?? k_width, k_h = sizeOverride?.height ?? k_height;
    this.element.style.setProperty('--kmodal-w', `${k_w}px`);
    this.element.style.setProperty('--kmodal-h', `${k_h}px`);
  }

  _openCaption(signal) {
    if (!this.options.caption) return;
    this._ownsCaption = this.options.caption === true;
    this.caption = this._ownsCaption ? document.createElement('header') : this.options.caption;
    this.caption.classList.add('kmodal__caption');
    if (this._ownsCaption) {
      const title = document.createElement('span');
      title.className = 'kmodal__title';
      title.textContent = this.options.title || this.element.getAttribute('aria-label') || '';
      this.caption.append(title);
      this.element.prepend(this.caption);
    }
    if (!this.options.showClose || typeof this.options.onRequestClose !== 'function') return;
    this.closeButton = document.createElement('button');
    this.closeButton.type = 'button';
    this.closeButton.className = 'kmodal__close';
    this.closeButton.setAttribute('aria-label', 'Închide');
    this.closeButton.title = 'Închide (Esc)';
    const icon = document.createElement('span');
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = '×';
    this.closeButton.append(icon);
    this.closeButton.addEventListener('click', () => this.options.onRequestClose?.(), { signal });
    this.caption.append(this.closeButton);
  }

  _onKey(k_event) {
    if (k_event.key === 'Escape') {
      if (k_event.defaultPrevented || this._escapeForPopup) return;
      k_event.preventDefault();
      this.options.onRequestClose?.();
      return;
    }
    if (k_event.key !== 'Tab' || k_event.defaultPrevented) return;
    const k_items = [...this.element.querySelectorAll(FOCUSABLE)].filter((k_node) => k_node.getClientRects().length > 0 && getComputedStyle(k_node).visibility !== 'hidden');
    if (!k_items.length) return;
    const k_first = k_items[0], k_last = k_items[k_items.length - 1];
    if (k_event.shiftKey && document.activeElement === k_first) { k_event.preventDefault(); k_last.focus(); }
    else if (!k_event.shiftKey && document.activeElement === k_last) { k_event.preventDefault(); k_first.focus(); }
  }

  close() {
    if (!this.isOpen) return;
    this.isOpen = false;
    frames.delete(this);
    this._abort?.abort();
    this.closeButton?.remove();
    this.closeButton = null;
    if (this._ownsCaption) this.caption?.remove();
    else this.caption?.classList.remove('kmodal__caption');
    this.caption = null;
    this.backdrop?.remove();
    this.backdrop = null;
    this.placeholder?.remove();
    this.placeholder = null;
    this.element.classList.remove('kmodal');
    this.element.style.zIndex = '';
    this.element.style.removeProperty('--kmodal-w');
    this.element.style.removeProperty('--kmodal-h');
    this.element.removeAttribute('role');
    this.element.removeAttribute('aria-modal');
  }

  destroy() { this.close(); }
}

// Debug mode only: try sizes from the console, e.g. kbotDebug.modalSize(1300, 900); kbotDebug.modalSize() puts the real sizes back.
registerDebugTool('modalSize', (k_width, k_height) => {
  sizeOverride = k_width && k_height ? { width: Number(k_width), height: Number(k_height) } : null;
  for (const k_frame of frames) k_frame.setSize(k_frame.options.width, k_frame.options.height);
  // sizes are CSS pixels (like the page): with Windows scaling at 150% one CSS px is 1.5 screen pixels, so the numbers already follow it
  const k_shown = [...frames][0]?.element.getBoundingClientRect();
  const k_info = `window ${innerWidth} x ${innerHeight} CSS px, scaling ${Math.round(devicePixelRatio * 100)}%${k_shown ? `, shown ${Math.round(k_shown.width)} x ${Math.round(k_shown.height)}` : ''}`;
  return `${sizeOverride ? `modal: ${sizeOverride.width} x ${sizeOverride.height}` : 'modal: the sizes of the pages'} (${k_info})`;
}, 'modalSize(width, height) resizes every modal window (and the next ones); modalSize() resets');
