// SLICE-WB01-01: choose the application only after the unit role is known.
import { Combobox } from '../components/combobox/combobox.js';
import { canEnterKbot } from './access.js';

export function createApplicationChoice({ call, say, onOpen, onUnauthorized }) {
  const $ = (id) => document.getElementById(id);
  let me;
  let selected = '';
  let busy = false;
  const applications = [
    ['adechit', 'A', 'ADECHIT'], ['avacont', 'C', 'Avacont · în curând'],
    ['kbot', 'K', 'K-BOT'], ['vercon', 'V', 'VERCON'],
  ].map(([value, letter, name]) => ({
    value, name, sortValue: name, disabled: value === 'avacont',
    label: `<span class="p-app" data-letter="${letter}">${name}</span>`,
  }));
  const appCombo = new Combobox($('p-app-combo'), {
    readonly: true, placeholder: 'Aplicația', allowHtml: true, staticData: [],
    onSelect: (value) => { selected = value; },
  });
  appCombo.input.id = 'p-app-combo-input';

  function fillApplications(preferred) {
    const items = applications.filter((item) => item.value !== 'kbot'
      || (me.db_name && canEnterKbot(me.role)));
    appCombo.hide();
    appCombo.options.staticData = items;
    const hit = items.find((item) => item.value === preferred && !item.disabled)
      || items.find((item) => !item.disabled);
    selected = hit?.value || '';
    appCombo.setValue(selected, hit?.name || '');
  }

  function failure(result) {
    if (result.status === 401) onUnauthorized(result.data.error);
    else say('msg-choice', result.data.error || 'Datele contului nu au putut fi citite.');
  }

  const unitCombo = new Combobox($('p-unit-choice'), {
    readonly: true, placeholder: 'Alegeți unitatea', staticData: [],
    onSelect: async (value) => {
      if (busy || value === me.db_name) return;
      busy = true;
      $('btn-app-choice').disabled = true;
      say('msg-choice', '');
      const opened = await call('POST', '/api/portal/unit', { db_name: value });
      const current = opened.ok ? await call('GET', '/api/portal/me') : opened;
      busy = false;
      $('btn-app-choice').disabled = false;
      if (!current.ok) {
        const unit = (me.units || []).find((row) => row.DC === me.db_name);
        unitCombo.setValue(unit?.DC || '', unit?.NumeUnitate || '');
        failure(current);
        return;
      }
      update(current.data, selected);
    },
  });
  unitCombo.input.id = 'p-unit-choice-input';

  function update(data, preferred) {
    me = data;
    unitCombo.options.staticData = (me.units || []).map((unit) => ({ value: unit.DC, label: unit.NumeUnitate }));
    const active = (me.units || []).find((unit) => unit.DC === me.db_name);
    unitCombo.setValue(active?.DC || '', active?.NumeUnitate || '');
    fillApplications(preferred);
  }

  $('form-app-choice').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy) return;
    say('msg-choice', '');
    busy = true;
    $('btn-app-choice').disabled = true;
    try {
      const current = await call('GET', '/api/portal/me');
      if (!current.ok) { failure(current); return; }
      const wanted = selected;
      update(current.data, wanted);
      if (!me.db_name) { say('msg-choice', 'Alegeți unitatea.'); return; }
      if (wanted === 'kbot' && !canEnterKbot(me.role)) {
        say('msg-choice', 'K-BOT este disponibil doar pentru rolurile Contabil și Director.');
        return;
      }
      if (selected) await onOpen(me, selected);
    } finally {
      busy = false;
      $('btn-app-choice').disabled = false;
    }
  });
  return { update };
}
