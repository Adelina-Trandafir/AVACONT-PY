// SLICE-CHAT-08. Positioning belongs to the chat control, including application zoom.
import { applicationScale, layoutRect, layoutViewport } from '../../utils/layout-geometry.js';

const STORAGE_KEY = 'kbot-chat-position';
const GUTTER = 8;
const GAP = 10;
const DRAG_THRESHOLD = 6;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export class ChatPosition {
  constructor(owner) {
    this.owner = owner;
    this.element = owner.element;
    this.button = owner.launcher.element;
    this.drag = null;
    this.suppressClick = false;
    this.frame = 0;
    this.position = null;
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
        this.position = { x: clamp(saved.x, 0, 1), y: clamp(saved.y, 0, 1) };
      }
    } catch { /* The bubble can still be moved when browser storage is unavailable. */ }

    const options = { signal: owner.abort.signal };
    this.button.addEventListener('pointerdown', event => this.start(event), options);
    this.button.addEventListener('pointermove', event => this.move(event), options);
    this.button.addEventListener('pointerup', event => this.finish(event), options);
    this.button.addEventListener('pointercancel', event => this.finish(event), options);
    this.button.addEventListener('lostpointercapture', event => this.finish(event), options);
    this.button.addEventListener('click', event => {
      if (!this.suppressClick || event.detail === 0) return;
      this.suppressClick = false;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, { capture: true, ...options });
    this.button.addEventListener('dragstart', event => event.preventDefault(), options);
    window.addEventListener('resize', () => this.schedule(), options);
    window.addEventListener('blur', () => this.finish(), options);
    if (typeof ResizeObserver !== 'undefined') {
      this.observer = new ResizeObserver(() => this.schedule());
      this.observer.observe(this.button);
    }
    this.reflow();
  }

  bounds() {
    const viewport = layoutViewport();
    const button = layoutRect(this.button);
    return { viewport, button,
      maxX: Math.max(GUTTER, viewport.width - button.width - GUTTER),
      maxY: Math.max(GUTTER, viewport.height - button.height - GUTTER) };
  }

  // Public positioning API; application/controller code never writes inline CSS.
  setPosition(x, y) {
    const { maxX, maxY } = this.bounds();
    x = clamp(x, GUTTER, maxX);
    y = clamp(y, GUTTER, maxY);
    this.element.style.setProperty('--kchat-left', `${x}px`);
    this.element.style.setProperty('--kchat-top', `${y}px`);
    this.element.classList.add('kchat--positioned');
    this.position = {
      x: maxX > GUTTER ? (x - GUTTER) / (maxX - GUTTER) : 0,
      y: maxY > GUTTER ? (y - GUTTER) / (maxY - GUTTER) : 0,
    };
    this.placePanel();
  }

  start(event) {
    if (!event.isPrimary || event.button !== 0 || this.drag) return;
    this.suppressClick = false;
    const origin = layoutRect(this.element);
    this.drag = { id: event.pointerId, clientX: event.clientX, clientY: event.clientY,
      x: origin.left, y: origin.top, moved: false };
    this.button.setPointerCapture(event.pointerId);
  }

  move(event) {
    const drag = this.drag;
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.clientX, dy = event.clientY - drag.clientY;
    if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    drag.moved = true;
    this.suppressClick = true;
    event.preventDefault();
    this.element.classList.add('kchat--dragging');
    const scale = applicationScale();
    this.setPosition(drag.x + dx / scale, drag.y + dy / scale);
  }

  finish(event, remember = true) {
    const drag = this.drag;
    if (!drag || event && event.pointerId !== drag.id) return;
    this.drag = null;
    this.element.classList.remove('kchat--dragging');
    if (this.button.hasPointerCapture(drag.id)) this.button.releasePointerCapture(drag.id);
    if (drag.moved && remember) {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(this.position)); }
      catch { /* Keep the selected position for the current page. */ }
    }
  }

  schedule() {
    cancelAnimationFrame(this.frame);
    this.frame = requestAnimationFrame(() => { this.frame = 0; this.reflow(); });
  }

  reflow() {
    if (this.drag) { this.placePanel(); return; }
    if (this.position) {
      const { maxX, maxY } = this.bounds();
      this.setPosition(GUTTER + this.position.x * (maxX - GUTTER),
        GUTTER + this.position.y * (maxY - GUTTER));
    } else this.placePanel();
  }

  placePanel() {
    if (!this.owner.isOpen) return;
    const { viewport, button } = this.bounds();
    const origin = layoutRect(this.element);
    const width = Math.max(0, Math.min(440, viewport.width - GUTTER * 2));
    const height = Math.max(0, Math.min(680, viewport.height - 96));
    let left = button.right - width;
    let top = button.top - height - GAP;
    if (top < GUTTER) {
      if (button.bottom + GAP + height <= viewport.height - GUTTER) {
        top = button.bottom + GAP;
      } else if (button.left - GAP - width >= GUTTER) {
        left = button.left - GAP;
        top = button.top;
      } else if (button.right + GAP + width <= viewport.width - GUTTER) {
        left = button.right + GAP;
        top = button.top;
      }
    }
    left = clamp(left, GUTTER, Math.max(GUTTER, viewport.width - width - GUTTER));
    top = clamp(top, GUTTER, Math.max(GUTTER, viewport.height - height - GUTTER));
    this.element.style.setProperty('--kchat-panel-left', `${left - origin.left}px`);
    this.element.style.setProperty('--kchat-panel-top', `${top - origin.top}px`);
    this.element.style.setProperty('--kchat-panel-width', `${width}px`);
    this.element.style.setProperty('--kchat-panel-height', `${height}px`);
    this.element.classList.add('kchat--panel-placed');
  }

  destroy() {
    cancelAnimationFrame(this.frame);
    this.observer?.disconnect();
    this.finish(undefined, false);
  }
}
