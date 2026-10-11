// Slice 0110-11 -- the column sets of EVERY grid of the web area, in one place.
//
// Each page takes its columns from here and gives its grid the same id (`layoutId`), so the
// «Coloane» editor of the Administrare page can list all the grids and a saved layout can be applied
// to each of them. The columns are plain data: {key, title, ...} as the DataGrid reads them. A
// layout (static/js/portal/layouts.js) only says which of these show, in which order and how wide.

const money = { valueType: 'number', format: 'standard', aggregate: 'sum' };
const plain = { valueType: 'number', format: 'standard' };
const count = { valueType: 'number', format: 'fixed', decimals: 0 };
const countSum = { ...count, aggregate: 'sum' };

const QUARTERS = [
  { key: 'trim1', title: 'Trim. 1' },
  { key: 'trim2', title: 'Trim. 2' },
  { key: 'trim3', title: 'Trim. 3' },
  { key: 'trim4', title: 'Trim. 4' },
];

/** The four quarters and their total; `extra` is added to each (e.g. an aggregate). */
export const quarterColumns = (extra = {}) => [
  ...QUARTERS.map((q) => ({ ...q, ...plain, ...extra })),
  { key: 'total', title: 'Total', ...plain, ...extra },
];

export { QUARTERS, money, plain };

const GRIDS = [
  // ------------------------------------------------------------ the angajament card: one grid per tab
  {
    id: 'ang.sumar', group: 'Angajament', title: 'Sumar',
    columns: [
      { key: 'clsf', title: 'Clasificație' },
      { key: 'cod_indicator', title: 'Indicator' },
      { key: 'partener', title: 'Partener' },
      { key: 'credit_bug', title: 'Credit bugetar', ...money },
      { key: 'total_rezervari', title: 'Rezervări', ...money },
      { key: 'total_receptii', title: 'Recepții', ...money },
      { key: 'total_plati', title: 'Plăți', ...money },
      { key: 'total_revizii', title: 'Revizii', ...money },
      { key: 'total_ordonantari', title: 'Ordonanțări', ...money },
    ],
  },
  {
    id: 'ang.istoric', group: 'Angajament', title: 'Istoric',
    columns: [
      { key: 'data_fx', title: 'Data', valueType: 'datetime', format: 'generalDate' },
      { key: 'clsf', title: 'Clasificație' },
      { key: 'tip_rand', title: 'Tip' },
      { key: 'cod_indicator', title: 'Indicator' },
      { key: 'descriere', title: 'Descriere', width: 240 },
      { key: 'val_rezervare_i', title: 'Rez. inițială', ...money },
      { key: 'val_rezervare_d', title: 'Rez. definitivă', ...money },
      { key: 'val_rezervare_dif', title: 'Diferență', ...money },
      { key: 'val_ang_leg', title: 'Angajament legal', ...money },
      { key: 'val_receptie', title: 'Recepție', ...money },
      { key: 'val_plata', title: 'Plată', ...money },
      { key: 'doc', title: 'Document' },
      { key: 'observatii', title: 'Observații', width: 240 },
    ],
  },
  {
    id: 'ang.istoric-valori', group: 'Angajament', title: 'Istoric (valorile rândului ales, sub grilă)',
    columns: [
      { key: 'tip', title: 'Tip', width: 150 },
      { key: 'valoare', title: 'Valoare', valueType: 'number', format: 'standard' },
    ],
  },
  {
    id: 'ang.rezervari', group: 'Angajament', title: 'Rezervări',
    columns: [
      { key: 'data_rezervare', title: 'Data', valueType: 'datetime', format: 'shortDate' },
      { key: 'clsf', title: 'Clasificație' },
      { key: 'denumire', title: 'Denumire', width: 240 },
      { key: 'cod_indicator', title: 'Indicator' },
      { key: 'r_credit_bug', title: 'Credit bugetar', ...money },
      { key: 'r_initiala', title: 'Inițială', ...money },
      { key: 'r_valoare', title: 'Valoare', ...money },
      { key: 'r_definitiva', title: 'Definitivă', ...money },
      { key: 'are_ddf', title: 'DDF', valueType: 'boolean', format: 'yesNo' },
    ],
  },
  {
    id: 'ang.receptii', group: 'Angajament', title: 'Recepții',
    columns: [
      { key: 'data_r', title: 'Data recepției', valueType: 'datetime', format: 'shortDate' },
      { key: 'nrcrt_r', title: 'Nr.', valueType: 'number' },
      { key: 'descriere_r', title: 'Descriere', width: 220 },
      { key: 'data_h', title: 'Versiune antet', valueType: 'datetime', format: 'generalDate' },
      { key: 'clsf', title: 'Clasificație' },
      { key: 'denumire', title: 'Denumire', width: 220 },
      { key: 'cod_indicator', title: 'Indicator' },
      { key: 'valoare', title: 'Valoare', ...money },
      { key: 'dif', title: 'Diferență', ...money },
      { key: 'suma_antet', title: 'Suma antet', valueType: 'number', format: 'standard' },
      { key: 'este_stergere', title: 'Ștergere', valueType: 'boolean', format: 'yesNo' },
    ],
  },
  {
    id: 'ang.extrase', group: 'Angajament', title: 'Extrase',
    columns: [
      { key: 'data_banca', title: 'Data banca', valueType: 'datetime', format: 'shortDate' },
      { key: 'data_extras', title: 'Data extras', valueType: 'datetime', format: 'shortDate' },
      { key: 'clsf', title: 'Clasificație' },
      { key: 'nr_doc', title: 'Nr. doc.' },
      { key: 'platitor_nume', title: 'Plătitor', width: 220 },
      { key: 'suma_debit', title: 'Debit', ...money },
      { key: 'suma_credit', title: 'Credit', ...money },
      { key: 'cod_contract', title: 'Cod angajament' },
      { key: 'rand_contract', title: 'Indicator' },
      { key: 'referinta', title: 'Referință', width: 160 },
      { key: 'explicatii', title: 'Explicații', width: 280 },
    ],
  },
  {
    id: 'ang.plati', group: 'Angajament', title: 'Plăți',
    columns: [
      { key: 'data_plata', title: 'Data plății', valueType: 'datetime', format: 'shortDate' },
      { key: 'nr_op', title: 'Nr. OP' },
      { key: 'clsf', title: 'Clasificație' },
      { key: 'denumire', title: 'Denumire', width: 220 },
      { key: 'cod_indicator', title: 'Indicator' },
      { key: 'suma', title: 'Suma', ...money },
      { key: 'are_ord', title: 'Ordonanțat', valueType: 'boolean', format: 'yesNo' },
      { key: 'platitor_nume', title: 'Plătitor', width: 200 },
      { key: 'nr_doc_extras', title: 'Nr. document' },
      { key: 'explicatii', title: 'Explicații', width: 260 },
    ],
  },
  {
    id: 'ang.fundamentari', group: 'Angajament', title: 'Fundamentări (liniile revizilor)',
    columns: [
      { key: 'rev', title: 'Revizie' },
      { key: 'clsf', title: 'Clasificație' },
      { key: 'element_fund', title: 'Element fundamentare', width: 260 },
      { key: 'val_prec', title: 'Valoare precedentă', ...money },
      { key: 'val_cur', title: 'Valoare curentă', ...money },
      { key: 'val_tot', title: 'Valoare totală', ...money },
    ],
  },
  {
    id: 'ang.fundamentari-lista', group: 'Angajament', title: 'Fundamentări (lista reviziilor, pe telefon)',
    columns: [
      { key: 'nr', title: 'Revizie' },
      { key: 'data', title: 'Data', valueType: 'datetime', format: 'shortDate' },
      { key: 'suma', title: 'Total', valueType: 'number', format: 'standard' },
      { key: 'semn', title: 'Semnături' },
      { key: 'pdf', title: 'PDF semnat', valueType: 'boolean', format: 'yesNo' },
    ],
  },
  {
    id: 'ang.ordonantari', group: 'Angajament', title: 'Ordonanțări (liniile ordonanțărilor)',
    columns: [
      { key: 'ord', title: 'Ordonanțare' },
      { key: 'clsf', title: 'Clasificație' },
      { key: 'descriere', title: 'Descriere', width: 240 },
      { key: 'den_bene', title: 'Beneficiar', width: 200 },
      { key: 'total_receptii', title: 'Total recepții', ...money },
      { key: 'plati_ant', title: 'Plăți anterioare', ...money },
      { key: 'valoare', title: 'Valoare', ...money },
      { key: 'ramas', title: 'Rămas', ...money },
      { key: 'doc_just', title: 'Document justificativ', width: 200 },
    ],
  },
  {
    id: 'ang.ordonantari-lista', group: 'Angajament', title: 'Ordonanțări (lista, pe telefon)',
    columns: [
      { key: 'tip', title: 'Document' },
      { key: 'nr', title: 'Număr' },
      { key: 'data', title: 'Data', valueType: 'datetime', format: 'shortDate' },
      { key: 'suma', title: 'Suma', valueType: 'number', format: 'standard' },
      { key: 'semn', title: 'Semnături' },
      { key: 'pdf', title: 'PDF semnat', valueType: 'boolean', format: 'yesNo' },
    ],
  },

  // ------------------------------------------------------------ the Extrase page
  {
    id: 'extrase.antete', group: 'Extrase', title: 'Extrase de cont (antete)',
    columns: [
      { key: 'data_extras', title: 'Data', valueType: 'datetime', format: 'shortDate' },
      { key: 'numar_extras', title: 'Nr.' },
      { key: 'clsf', title: 'Clasificație' },
      { key: 'denumire', title: 'Denumire', width: 220 },
      { key: 'cont', title: 'Cont' },
      { key: 'cod_iban', title: 'IBAN', width: 200 },
      { key: 'sid', title: 'Sold inițial D', ...plain },
      { key: 'sic', title: 'Sold inițial C', ...plain },
      { key: 'rpd', title: 'Rulaj D', ...plain },
      { key: 'rpc', title: 'Rulaj C', ...plain },
      { key: 'tsd', title: 'Total sume D', ...plain },
      { key: 'tsc', title: 'Total sume C', ...plain },
      { key: 'sfd', title: 'Sold final D', ...plain },
      { key: 'sfc', title: 'Sold final C', ...plain },
    ],
  },
  {
    id: 'extrase.operatii', group: 'Extrase', title: 'Extrase de cont (operațiuni)',
    columns: [
      { key: 'data_banca', title: 'Data banca', valueType: 'datetime', format: 'shortDate' },
      { key: 'data_doc', title: 'Data doc.', valueType: 'datetime', format: 'shortDate' },
      { key: 'clsf', title: 'Clasificație' },
      { key: 'nr_doc', title: 'Nr. doc.' },
      { key: 'referinta', title: 'Referință', width: 160 },
      { key: 'platitor_nume', title: 'Plătitor', width: 220 },
      { key: 'platitor_cui', title: 'CUI' },
      { key: 'platitor_iban', title: 'IBAN plătitor', width: 200 },
      { key: 'suma_debit', title: 'Debit', ...money },
      { key: 'suma_credit', title: 'Credit', ...money },
      { key: 'cod_contract', title: 'Cod angajament' },
      { key: 'rand_contract', title: 'Indicator' },
      { key: 'cod_program', title: 'Cod program' },
      { key: 'cod_ai', title: 'Cod AI' },
      { key: 'explicatii', title: 'Explicații', width: 300 },
    ],
  },

  // ------------------------------------------------------------ Nomenclatoare
  {
    id: 'clsf.buget', group: 'Nomenclatoare', title: 'Clasificații: buget (o clasificație)',
    columns: [{ key: 'data_inceput', title: 'Început', valueType: 'datetime', format: 'shortDate' }, ...quarterColumns()],
  },
  {
    id: 'clsf.buget-grup', group: 'Nomenclatoare', title: 'Clasificații: buget (un grup)',
    columns: [{ key: 'clsf', title: 'Clsf', width: 150 }, ...quarterColumns()],
  },
  {
    id: 'clsf.rectificari', group: 'Nomenclatoare', title: 'Clasificații: rectificări (o clasificație)',
    columns: [
      { key: 'document', title: 'Nr. doc.' },
      { key: 'data', title: 'Data', valueType: 'datetime', format: 'shortDate' },
      ...quarterColumns(money),
    ],
  },
  {
    id: 'clsf.rectificari-grup', group: 'Nomenclatoare', title: 'Clasificații: rectificări (un grup)',
    columns: [{ key: 'clsf', title: 'Clsf', width: 150 }, ...quarterColumns()],
  },
  {
    id: 'clsf.total', group: 'Nomenclatoare', title: 'Clasificații: buget + rectificări',
    columns: [{ key: 'eticheta', title: '', width: 160 }, ...quarterColumns()],
  },
  {
    id: 'clsf.verificare', group: 'Nomenclatoare', title: 'Clasificații: verificare buget FOREXE',
    columns: [
      { key: 'clsf', title: 'Clsf' },
      { key: 'denumire', title: 'Denumire', width: 260 },
      { key: 'ss', title: 'Sursa' },
      { key: 'buget_kbot', title: 'Buget K-BOT', ...plain },
      { key: 'credit_fx', title: 'Credit FOREXE', ...plain },
      { key: 'diferenta', title: 'Diferență', ...plain },
    ],
  },
  {
    id: 'parteneri.coduri', group: 'Nomenclatoare', title: 'Parteneri: coduri angajament',
    columns: [
      { key: 'clsf', title: 'Clasificație', width: 160 },
      { key: 'denumire_clsf', title: 'Denumire clasificație', width: 240 },
      { key: 'cont_bancar', title: 'Cont bancar asociat', width: 220 },
      { key: 'cod_ang', title: 'Cod ang.' },
      { key: 'cod_ind', title: 'Cod ind.' },
    ],
  },

];

/** Every grid of the web area: [{id, group, title, columns}]. */
export const GRID_CATALOG = GRIDS;

const BY_ID = new Map(GRIDS.map((g) => [g.id, g]));
if (BY_ID.size !== GRIDS.length) throw new Error('Duplicate grid id in the column catalog');

/** The column set of one grid. A copy: the DataGrid adds its own fields to what it is given. */
export function columnsOf(id) {
  const grid = BY_ID.get(id);
  if (!grid) throw new Error(`Unknown grid id: ${id}`);
  return grid.columns.map((c) => ({ ...c }));
}
