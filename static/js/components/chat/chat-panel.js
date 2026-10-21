// SLICE-CHAT-03. All interactive elements are existing components: no page-level native controls.
import { ActionAddButton } from '../action-add-button/action-add-button.js';
import { TextBox } from '../textbox/textbox.js';
import { CheckBox } from '../checkbox/checkbox.js';
import { APPLICATIONS, applicationIcon } from './applications.js';
import { ChatPosition } from './chat-position.js';

function el(tag, className = '', text = '') {
  const item = document.createElement(tag);
  item.className = className;
  item.textContent = text;
  return item;
}

const dateFormat = new Intl.DateTimeFormat('ro-RO', { dateStyle: 'short', timeStyle: 'short' });
const TITLE = 'DISCUȚII';
const ICON = '💬';

export class ChatPanel {
  constructor({ application, onOpen, onClose, onSend, onFilter, onPreference, onOlder, onRead, onRefresh }) {
    this.application = application;
    this.callbacks = { onOpen, onClose, onSend, onFilter, onPreference, onOlder, onRead, onRefresh };
    this.abort = new AbortController();
    this.controls = [];
    this.rows = new Map();
    this.messages = new Map();
    this.filters = new Map();
    this.isOpen = false;
    this.ready = false;
    this.busy = false;
    this.reply = null;
    this.element = el('div', 'kchat');
    this.launcher = this.button(this.element, ICON, TITLE, () => this.isOpen ? this.close() : this.open(), 'kchat__launcher');
    this.launcher.options.title = 'DISCUȚII · Trageți cu mouse-ul pentru a muta bula';
    this.launcher.element.title = this.launcher.options.title;
    this.launcher.element.setAttribute('aria-expanded', 'false');
    this.launcher.element.setAttribute('aria-controls', 'kbot-common-chat');
    this.panel = el('section', 'kchat__panel');
    this.panel.id = 'kbot-common-chat';
    this.panel.hidden = true;
    this.panel.setAttribute('role', 'region');
    this.panel.setAttribute('aria-label', TITLE);
    this.element.append(this.panel);
    this.buildHeader();
    this.buildFilters();
    this.older = this.button(this.panel, '↑', 'Mesaje mai vechi', () => onOlder?.(), 'kchat__older');
    this.older.element.hidden = true;
    this.log = el('div', 'kchat__messages');
    this.log.tabIndex = 0;
    this.log.setAttribute('role', 'log');
    this.log.setAttribute('aria-live', 'polite');
    this.log.setAttribute('aria-relevant', 'additions');
    this.log.setAttribute('aria-label', 'Mesaje');
    this.panel.append(this.log);
    this.empty = el('p', 'kchat__empty', 'Se încarcă mesajele…');
    this.log.append(this.empty);
    this.jump = this.button(this.panel, '↓', 'Mesaje noi', () => this.scrollToBottom(), 'kchat__jump');
    this.jump.element.hidden = true;
    this.status = el('p', 'kchat__status');
    this.status.setAttribute('role', 'status');
    this.status.hidden = true;
    this.panel.append(this.status);
    this.buildComposer();
    document.body.append(this.element);
    this.positioning = new ChatPosition(this);
    this.log.addEventListener('scroll', () => {
      if (this.atBottom()) { this.jump.element.hidden = true; this.callbacks.onRead?.(); }
    }, { signal: this.abort.signal });
    this.panel.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !event.defaultPrevented && !event.isComposing) {
        event.preventDefault(); this.close();
      }
    }, { signal: this.abort.signal });
    document.addEventListener('pointerdown', event => {
      if (this.isOpen && !event.composedPath().includes(this.element)) this.close({ restoreFocus: false });
    }, { capture: true, signal: this.abort.signal });
    this.setBusy(false);
  }

  button(host, icon, label, onClick, className = '') {
    const button = new ActionAddButton(host, { icon, label, onClick, className });
    this.controls.push(button);
    return button;
  }

  buildHeader() {
    const header = el('header', 'kchat__header');
    const title = el('div', 'kchat__heading');
    title.append(el('strong', '', TITLE));
    const icon = el('span', 'kchat__title-icon', ICON);
    icon.setAttribute('aria-hidden', 'true');
    header.append(icon, title);
    this.refresh = this.button(header, '↻', '', () => this.callbacks.onRefresh?.());
    this.refresh.element.title = 'Actualizează discuțiile';
    this.refresh.element.setAttribute('aria-label', 'Actualizează discuțiile');
    const close = this.button(header, '×', '', () => this.close());
    close.element.title = 'Închide discuțiile';
    close.element.setAttribute('aria-label', 'Închide discuțiile');
    this.panel.append(header);
  }

  buildFilters() {
    const area = el('div', 'kchat__filters');
    area.setAttribute('role', 'group');
    area.setAttribute('aria-label', 'Filtrează mesajele după aplicație');
    area.append(el('span', 'kchat__filter-caption', 'Afișează:'));
    for (const [code, app] of Object.entries(APPLICATIONS)) {
      const control = new CheckBox(area, { label: code, checked: true, ariaLabel: app.label,
        onChange: () => {
          const selected = [...this.filters].filter(([, cb]) => cb.checked).map(([key]) => key);
          if (!selected.length) {
            control.checked = true;
            this.setStatus('Păstrați cel puțin o aplicație în filtru.');
            return;
          }
          this.callbacks.onFilter?.(selected);
        } });
      control.text.replaceChildren(applicationIcon(code));
      control.element.title = app.label;
      this.filters.set(code, control);
      this.controls.push(control);
    }
    this.panel.append(area);
  }

  buildComposer() {
    const area = el('div', 'kchat__composer');
    this.replyArea = el('div', 'kchat__reply-draft');
    this.replyText = el('span');
    this.replyArea.append(this.replyText);
    this.cancelReply = this.button(this.replyArea, '×', '', () => this.setReply(null));
    this.cancelReply.element.title = 'Renunță la răspuns';
    this.cancelReply.element.setAttribute('aria-label', 'Renunță la răspuns');
    this.replyArea.hidden = true;
    area.append(this.replyArea);
    const identity = el('div', 'kchat__identity');
    this.anonymous = new CheckBox(identity, { label: 'Cu pseudonim', checked: true,
      onChange: checked => {
        this.paintIdentity();
        this.callbacks.onPreference?.({ anonymous: checked });
      } });
    this.controls.push(this.anonymous);
    this.pseudonym = el('span', 'kchat__pseudonym');
    identity.append(this.pseudonym);
    area.append(identity);
    this.nameHost = el('div', 'kchat__name');
    this.name = new TextBox(this.nameHost, { maxLength: 80, allowCopyPaste: true,
      placeholder: 'Numele afișat la mesajele cu nume', ariaLabel: 'Numele afișat' });
    this.controls.push(this.name);
    identity.append(this.nameHost);
    const entry = el('div', 'kchat__entry');
    this.text = new TextBox(entry, { multiline: true, rows: 3, maxLength: 4000, allowCopyPaste: true,
      placeholder: 'Scrieți un mesaj…', ariaLabel: 'Mesaj',
      onInput: () => { this.text.clearError(); this.paintLength(); } });
    this.controls.push(this.text);
    this.text.input.addEventListener('keydown', event => {
      if (event.key !== 'Enter' || event.defaultPrevented || event.isComposing) return;
      event.preventDefault();
      if (event.ctrlKey || event.metaKey) this.insertNewLine();
      else if (!event.repeat) this.requestSend();
    }, { signal: this.abort.signal });
    area.append(entry);
    const footer = el('div', 'kchat__composer-footer');
    this.length = el('span', 'kchat__length');
    footer.append(this.length);
    footer.append(el('small', 'kchat__hint', 'Enter trimite. Ctrl+Enter adaugă un rând.'));
    this.send = this.button(footer, '➤', 'Trimite', () => this.requestSend(), 'kchat__send');
    area.append(footer);
    this.panel.append(area);
    this.paintIdentity();
    this.paintLength();
  }

  paintLength() { this.length.textContent = `${this.text.text.length}/4000`; }

  insertNewLine() {
    const input = this.text.input;
    if (input.disabled || input.readOnly) return;
    const start = input.selectionStart, end = input.selectionEnd;
    if (input.maxLength >= 0 && input.value.length - (end - start) >= input.maxLength) return;
    input.setRangeText('\n', start, end, 'end');
    input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertLineBreak' }));
  }

  paintIdentity() {
    this.nameHost.hidden = this.anonymous.checked;
    this.pseudonym.hidden = !this.anonymous.checked;
  }

  requestSend() {
    if (!this.ready || this.busy) return;
    if (!this.text.text.trim()) { this.text.setError('Scrieți mesajul.'); this.text.focus(); return; }
    if (!this.anonymous.checked && !this.name.text.trim()) {
      this.name.setError('Completați numele afișat sau alegeți pseudonimul.'); this.name.focus(); return;
    }
    this.callbacks.onSend?.({ text: this.text.text, anonymous: this.anonymous.checked,
      display_name: this.name.text.trim(), reply_to: this.reply?.id || null, application: this.application });
  }

  setState(state, { settings = false } = {}) {
    this.ready = true;
    this.launcher.setLabel(state.unread ? `${TITLE} · ${state.unread > 99 ? '99+' : state.unread}` : TITLE);
    this.launcher.element.setAttribute('aria-label', `${TITLE}, ${state.unread} mesaje necitite`);
    this.pseudonym.textContent = state.self.pseudonym;
    for (const [app, control] of this.filters) {
      control.checked = state.preferences.applications.includes(app);
      control.element.title = `${APPLICATIONS[app].label} · ${state.unread_by_application[app] || 0} necitite`;
    }
    if (settings) {
      this.anonymous.checked = state.preferences.anonymous;
      if (document.activeElement !== this.name.input) this.name.setValue(state.self.display_name);
      this.paintIdentity();
    }
    this.setBusy(this.busy);
  }

  setBusy(busy) {
    this.busy = busy;
    for (const control of [...this.filters.values(), this.anonymous, this.name, this.text, this.send, this.older]) {
      control?.setEnabled(this.ready && !busy);
    }
    this.refresh?.setEnabled(!busy);
    this.cancelReply?.setEnabled(!busy);
    this.panel.setAttribute('aria-busy', String(busy));
  }

  setStatus(text, error = false) {
    this.status.textContent = text;
    this.status.hidden = !text;
    this.status.classList.toggle('kchat__status--error', error);
  }

  setReply(message) {
    this.reply = message;
    this.replyArea.hidden = !message;
    this.replyText.textContent = message ? `Răspuns către ${message.author}: ${message.text.slice(0, 140)}` : '';
    if (message) this.text.focus();
  }

  clearDraft() { this.text.setValue(''); this.text.clearError(); this.setReply(null); this.paintLength(); }

  resetMessages() {
    for (const row of this.rows.values()) row.action.destroy();
    this.rows.clear(); this.messages.clear();
    this.log.replaceChildren(this.empty);
    this.empty.textContent = 'Se încarcă mesajele…';
    this.empty.hidden = false;
    this.older.element.hidden = true;
    this.jump.element.hidden = true;
  }

  setHasOlder(value) { this.older.element.hidden = !value; }

  addMessages(messages, { prepend = false, initial = false } = {}) {
    const wasBottom = initial || this.atBottom();
    const oldHeight = this.log.scrollHeight;
    const fragment = document.createDocumentFragment();
    for (const message of messages) {
      if (this.messages.has(message.id)) continue;
      const article = el('article', `kchat__message${message.mine ? ' kchat__message--mine' : ''}`);
      const header = el('div', 'kchat__message-header');
      const author = el('strong', 'kchat__author', message.author);
      author.title = message.anonymous ? 'Mesaj cu pseudonim' : 'Mesaj cu nume';
      const time = el('time', 'kchat__time', dateFormat.format(new Date(message.created_at)));
      time.dateTime = message.created_at;
      header.append(applicationIcon(message.application), author, time);
      const actions = el('div', 'kchat__message-actions');
      const action = new ActionAddButton(actions, { icon: '↩', label: '', title: 'Răspunde',
        ariaLabel: 'Răspunde la mesaj', onClick: () => this.setReply(message) });
      header.append(actions);
      article.append(header);
      if (message.reply) {
        const quote = el('blockquote', 'kchat__quote');
        quote.append(applicationIcon(message.reply.application), el('span', '', `${message.reply.author}: ${message.reply.text}`));
        article.append(quote);
      }
      article.append(el('p', 'kchat__body', message.text));
      this.messages.set(message.id, message);
      this.rows.set(message.id, { article, action });
      fragment.append(article);
    }
    this.empty.hidden = this.messages.size > 0;
    this.empty.textContent = 'Încă nu sunt mesaje pentru aplicațiile selectate.';
    if (prepend) {
      this.log.prepend(fragment);
      this.log.scrollTop += this.log.scrollHeight - oldHeight;
    } else {
      this.log.append(fragment);
      if (wasBottom) this.scrollToBottom();
      else if (messages.length) this.jump.element.hidden = false;
    }
  }

  atBottom() { return this.log.scrollHeight - this.log.scrollTop - this.log.clientHeight < 32; }

  scrollToBottom() {
    this.log.scrollTop = this.log.scrollHeight;
    this.jump.element.hidden = true;
    this.callbacks.onRead?.();
  }

  open() {
    if (this.isOpen) return;
    this.isOpen = true; this.panel.hidden = false;
    this.launcher.element.setAttribute('aria-expanded', 'true');
    this.setLayer(Math.floor(window.ZIndexManager?.getNext?.() || 1000));
    this.positioning.reflow();
    this.callbacks.onOpen?.();
    if (this.ready) this.text.focus(); else this.refresh.element.focus();
  }

  // Dynamic placement is internal to the component API; application code never writes inline CSS.
  setLayer(value) { this.element.style.zIndex = String(value); }

  close({ restoreFocus = true } = {}) {
    if (!this.isOpen) return;
    this.isOpen = false; this.panel.hidden = true;
    this.launcher.element.setAttribute('aria-expanded', 'false');
    this.callbacks.onClose?.();
    if (restoreFocus) this.launcher.element.focus();
  }

  destroy() {
    this.positioning.destroy();
    this.abort.abort();
    for (const row of this.rows.values()) row.action.destroy();
    for (const control of this.controls) control.destroy();
    this.element.remove();
  }
}
