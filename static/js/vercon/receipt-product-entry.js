import { mountCpvPicker } from './cpv-picker.js';
import { confirmBox } from '../utils/confirm-box.js';
import { ModalFrame } from '../components/modal-frame/modal-frame.js';
import { el, field, scope } from './ui.js';

const text = value => String(value ?? '').trim().toLocaleLowerCase('ro');
const num = value => Number(String(value ?? '').replace(',', '.'));

export function mfMonthly(price, percent, duration) {
  const decimal = value => {
    const match = /^(\d+)(?:[.,](\d+))?$/.exec(String(value ?? ''));
    return match ? [BigInt(match[1] + (match[2] || '')), 10n ** BigInt((match[2] || '').length)] : null;
  };
  const p = decimal(price), vat = decimal(percent);
  if (!p || !vat || !/^\d+$/.test(String(duration)) || BigInt(duration) <= 0n) return '';
  const numerator = p[0] * (100n * vat[1] + vat[0]) * 10000n;
  const denominator = p[1] * 100n * vat[1] * BigInt(duration);
  let value = numerator / denominator;
  const remainder = numerator % denominator;
  if (remainder * 2n > denominator || remainder * 2n === denominator && value % 2n !== 0n) ++value;
  return `${value / 10000n}.${String(value % 10000n).padStart(4, '0')}`;
}

export const mfColumns = [
  { key: 'code', label: 'Nr. Inv.', editable: true, required: true },
  { key: 'data_intrarii', label: 'Data MF', type: 'date', editable: true, required: true },
  { key: 'DNF', label: 'DNF', type: 'int', editable: true, required: true, min: 1,
    tooltip: 'Durata Normala de Folosire - in luni - (se citeste implicit din Clasificari, dar poate fi modificata)' },
  { key: 'DF', label: 'DF', type: 'int', editable: true, required: true, min: 0,
    tooltip: 'Durata Folosita a mijlocului fix in momentul intrarii in unitate - in luni' },
  { key: 'ValoareLuna', label: 'V. Luna', type: 'decimal', tooltip: 'Valoarea Lunara Amortizabila.' },
].map(spec => ({ ...spec, mfOnly: true }));

// All new product identities are local, negative numbers. Only saving the NIR creates them.
export function receiptProductEntry({ host, root, allocation, draft, api, selection, changed }) {
  let popup = null, busy = false, disposed = false;
  const sameRoot = (a, b) => allocation.product(a.product)?.destination === allocation.product(b.product)?.destination;
  function nextCode(row, after = null) {
    const codes = [...allocation.inventory(row), ...draft().lines.filter(other => sameRoot(row, other)).map(other => other.code)];
    const used = new Set(codes.map(text));
    let value = after != null && /^\d+$/.test(String(after)) ? BigInt(after) + 1n : codes.reduce((max, code) => {
      const candidate = /^\d+$/.test(String(code ?? '').trim()) ? BigInt(String(code).trim()) : 0n;
      return candidate > max ? candidate : max;
    }, 0n) + 1n;
    while (used.has(String(value))) ++value;
    return String(value);
  }
  function defaults(row) {
    if (!allocation.isMF(row)) return;
    const product = allocation.product(row.product);
    row.Cant = '1'; row.DNF ??= product?.dnf ?? '';
    row.DF ??= '0'; row.data_intrarii ||= draft().header.Data;
    row.code ||= nextCode(row);
  }
  function selectProduct(value, row) {
    let item;
    if (value?.newName != null) {
      const name = value.newName.trim();
      if (!name || name.length > 255) throw new Error('Introduceți o denumire de produs de maximum 255 de caractere.');
      const matches = allocation.selectedAccounts().flatMap(account => allocation.productsForAccount(account.id))
        .filter(product => text(product.name) === text(name));
      if (matches.length > 1) throw new Error('Mai multe produse au această denumire. Selectați produsul din contul dorit.');
      item = matches[0];
      if (!item) {
        const accounts = allocation.selectedAccounts(), account = accounts.length === 1 ? accounts[0] : null;
        const destination = account?.destination || `${selection().subunit}:${selection().gestiune}`;
        const type = allocation.destination(destination)?.type || selection().type;
        const values = { DenumireP: name, UM: '', IdMasina: account?.id ?? null, CPV: null, CC: null, DNF: null };
        item = allocation.addProduct({ id: allocation.nextProductId(), name, um: '', category: account?.id ?? null,
          destination, type, new_product: values });
      }
    } else item = allocation.product(value);
    if (!item) throw new Error('Alegeți un produs sau introduceți denumirea produsului nou.');
    Object.assign(row, { product: item.id, variant: null, name: item.name, um: item.um, _productReady: !!item.ready,
      destination: item.destination, new_product: item.new_product ? { ...item.new_product } : undefined });
    if (allocation.isMF(row)) { row.DNF = item.dnf ?? ''; defaults(row); }
    return item;
  }
  function issues(row) {
    const result = [];
    if (row.new_product && (!row.um || !row.new_product.IdMasina || !row._productReady)) result.push('Produs nou / UM / clasificare / CPV');
    if (!allocation.isMF(row)) return result;
    const product = allocation.product(row.product), low = num(product?.dnf_min) * 12, high = num(product?.dnf_max) * 12;
    if (!product?.cc || !Number.isFinite(low) || !Number.isFinite(high) || low <= 0 || high < low) result.push('Alegeți o clasificare MF cu limite de durată.');
    else if (!Number.isInteger(num(row.DNF)) || num(row.DNF) < low || num(row.DNF) > high) result.push(`Pentru clasificarea ${product.cc} durata (in luni) de folosire minima este ${low} si maxima ${high}.`);
    if (num(row.Cant) !== 1) result.push('Cantitate = 1');
    if (row.data_intrarii && row.data_intrarii > draft().header.Data) result.push('Data MF ≤ data NIR');
    if (row.code && draft().lines.some(other => other.key !== row.key && sameRoot(row, other) && text(other.code) === text(row.code))) result.push('Nr. Inv. unic în intrare');
    // Unchanged saved codes are already owned by that asset. The server rechecks ownership on every change.
    if ((!row.id || text(row.code) !== text(row._savedCode)) && row.code && allocation.inventory(row).some(code => text(code) === text(row.code))) result.push('Nr. Inv. deja folosit');
    return result;
  }
  function chooseAccount(row) {
    const accounts = allocation.selectedAccounts();
    if (accounts.length === 1) return Promise.resolve(accounts[0]);
    return new Promise(resolve => {
      const owner = scope(), node = el('section', { className: 'vc-receipt-product-account' }); host.append(node);
      const close = value => { owner.clear(); frame.destroy(); node.remove(); popup = null; resolve(value); };
      const frame = new ModalFrame(node, { title: 'Contul produsului nou', width: 440, height: 200, onRequestClose: () => close(null) });
      popup = { destroy: () => close(null) };
      const control = field({ key: 'account', label: 'Cont', type: 'ref', required: true,
        options: accounts.map(account => ({ value: account.id, label: `${account.label} · ${allocation.destination(account.destination)?.name || ''}` })) },
      null, true, value => close(accounts.find(account => String(account.id) === String(value))), owner);
      node.append(control.node); frame.open(); control.input._combo.input.focus(); control.input._combo.showAllOptions();
    });
  }
  function pick(row, classification = false) {
    return new Promise(resolve => {
      let picked = null;
      popup = mountCpvPicker(host, { api, sel: () => allocation.selectionForProduct(row.product), name: row.name,
        ...(classification ? { catalogName: 'Clasificare MF', catalogEndpoint: '/api/vercon/mf/classifications' } : {}),
        onPick: (code, detail) => {
          if (classification && (!(num(detail.dnf_min) > 0) || num(detail.dnf_max) < num(detail.dnf_min))) throw new Error('Alegeți o clasificare cu limite DNF valide.');
          picked = { code, detail };
        },
        onRequestClose: () => classification ? true : confirmBox('Produsul NU va avea CPV. Continuați fără CPV?',
          { title: 'Produs fără CPV', yes: 'Continuă fără CPV', no: 'Alege CPV', warning: true }),
        onClose: () => { popup = null; resolve(picked); },
      });
    });
  }
  async function finishProduct(row) {
    if (!row.new_product) return;
    if (!String(row.um ?? '').trim()) throw new Error('UM este obligatorie pentru produsul nou.');
    if (row._productReady) {
      const item = allocation.product(row.product); item.um = row.um; item.new_product.UM = row.um;
      for (const other of draft().lines.filter(other => other.product === row.product)) {
        other.um = row.um; other.new_product = { ...item.new_product };
      }
      return;
    }
    busy = true; root.inert = true;
    try {
      const account = allocation.account(row.new_product.IdMasina) || await chooseAccount(row);
      if (!account || disposed) throw new Error('Alegeți contul produsului nou înainte de continuare.');
      const item = allocation.product(row.product), destination = allocation.destination(account.destination);
      Object.assign(item, { category: account.id, destination: account.destination, type: destination.type, um: row.um });
      Object.assign(row.new_product, { IdMasina: account.id, UM: row.um }); row.destination = account.destination;
      if (destination.type === 'MF') {
        const choice = await pick(row, true);
        if (!choice || disposed) throw new Error('Clasificarea MF este obligatorie. Reluați câmpul UM pentru a o alege.');
        Object.assign(item, { cc: choice.code, dnf_min: choice.detail.dnf_min, dnf_max: choice.detail.dnf_max, dnf: num(choice.detail.dnf_min) * 12 });
        Object.assign(row.new_product, { CC: item.cc, DNF: item.dnf }); row.DNF = item.dnf; defaults(row);
      }
      const cpv = await pick(row);
      if (disposed) return;
      row.new_product.CPV = cpv?.code ?? null; row._productReady = true;
      item.new_product = { ...row.new_product }; item.ready = true; changed();
    } finally { busy = false; if (!disposed) root.inert = false; }
  }
  return { defaults, selectProduct, issues, nextCode, finishProduct,
    get busy() { return busy; },
    destroy() { disposed = true; popup?.destroy(); popup = null; },
  };
}
