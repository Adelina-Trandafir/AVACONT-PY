// The «Parteneri» page of the portal (menu Nomenclatoare). VIEW ONLY: the web counterpart of the
// desktop ParteneriForm without editing, ANAF lookup or deletion.
//
//   left   the partners of the unit as a searchable list (code + name); «Ascunde partenerii fara
//          activitate» narrows it to the ones used on documents
//   right  the details of the chosen partner and its «Coduri angajament» in a grid

import { DataGrid } from '../dgv/datagrid.js';
import { TreeView } from '../components/treeview/treeview.js';
import { columnsOf } from './columns.js';

const $ = (id) => document.getElementById(id);
const rowHeight = () => (window.matchMedia('(max-width: 900px)').matches ? 20 : 23);
const nameCmp = new Intl.Collator('ro', { sensitivity: 'base', numeric: true });

/**
 * @param {{call: Function, fail: Function, say: Function, hasUnit: () => boolean}} deps
 *   call(method, path) -> {ok, status, data}; fail(r, fallback) shows the server's sentence.
 */
export function createParteneriPage({ call, fail, say, hasUnit }) {
  let tree = null;
  let grid = null;
  let partners = [];
  let byId = new Map();
  let loaded = false;
  let seq = 0;
  let bound = false;

  function nodes() {
    const hideIdle = $('chk-part-activ').checked;
    const several = new Set(partners.map((p) => p.ss)).size > 1;
    return partners
      .filter((p) => p.activ || !hideIdle)
      .sort((a, b) => nameCmp.compare(a.denumire, b.denumire))
      .map((p) => ({
        id: `P|${p.id_partener}`,
        label: `${p.cod_partener}  ${p.denumire}${several ? ` (${p.ss})` : ''}${p.ascuns ? ' — ascuns' : ''}`,
        tooltip: p.cod_fiscal ? `${p.denumire}\nCF ${p.cod_fiscal}` : p.denumire,
        keywords: `${p.cod_partener} ${p.cod_fiscal}`,
      }));
  }

  function paintList() {
    const list = nodes();
    tree.setData(list);
    $('part-count').textContent = `${list.length} din ${partners.length}`;
  }

  function showPartner(p) {
    if (grid) grid.destroy();
    grid = null;
    const set = (id, text) => { $(id).textContent = text || ''; };
    set('part-title', p ? p.denumire : 'Alegeți un partener din listă.');
    set('part-cod', p && p.cod_partener);
    set('part-cf', p && p.cod_fiscal);
    set('part-den', p && p.denumire);
    set('part-iban', p && p.cont_iban);
    set('part-banca', p && p.banca);
    set('part-adresa', p && p.adresa);
    set('part-ascuns', p ? (p.ascuns ? 'Da — nu mai apare în liste' : 'Nu') : '');
    if (!p) return;
    grid = new DataGrid($('grid-part-cod'), {
      columns: columnsOf('parteneri.coduri'),
      layoutId: 'parteneri.coduri',
      rows: p.coduri,
      rowHeight: rowHeight(),
      footer: true,
      footerCaption: '{0} coduri',
      emptyText: 'Partenerul nu are coduri de angajament.',
    });
  }

  async function load() {
    if (!hasUnit()) {
      say('Alegeți unitatea ca să vedeți partenerii.');
      return false;
    }
    const mine = (seq += 1);
    $('part-count').textContent = 'Se încarcă...';
    const r = await call('GET', '/api/portal/date/parteneri');
    if (mine !== seq) return false;
    if (!r.ok) {
      $('part-count').textContent = '';
      fail(r, 'Partenerii nu au putut fi citiți.');
      return false;
    }
    partners = r.data.partners;
    byId = new Map(partners.map((p) => [`P|${p.id_partener}`, p]));
    loaded = true;
    return true;
  }

  function bind() {
    if (bound) return;
    bound = true;
    tree = new TreeView($('tree-part'), {
      inline: true,
      searchPlaceholder: 'Căutați (minimum 3 caractere)…',
      onSelect: (sel) => showPartner(byId.get(String(sel.id))),
    });
    $('chk-part-activ').addEventListener('change', () => { if (loaded) paintList(); });
  }

  async function show() {
    bind();
    say('');
    $('card-app').classList.add('is-part');
    $('part-view').hidden = false;
    if (!loaded) {
      showPartner(null);
      if (await load()) paintList();
    }
  }

  async function reload() {
    loaded = false;
    if ($('part-view').hidden) return;
    await show();
  }

  function hide() {
    $('card-app').classList.remove('is-part');
    $('part-view').hidden = true;
  }

  return { show, reload, hide };
}
