// Slice 0110-11 -- made-up rows for the preview of the «Coloane» editor, so a grid the administrator has not
// opened yet can still be judged: the values look like the real ones (codes, amounts, dates, long texts).
// Nothing here is sent anywhere or stored.

const TEXT = {
  clsf: '65.02.04.01', ss: '01A', cod_indicator: 'IND1', rand_contract: 'IND1', cod_ind: 'IND1',
  denumire: 'Alte cheltuieli de funcționare', denumire_clsf: 'Alte cheltuieli de funcționare',
  descriere: 'Furnizare materiale de curățenie', descriere_r: 'Recepție factură de materiale de curățenie',
  partener: 'Furnizor Exemplu SRL', explicatii: 'Plată factură nr. 123/2026 conform contractului de furnizare servicii',
  observatii: 'Observație despre operațiunea înregistrată pe acest rând', element_fund: 'Materiale de curățenie și întreținere',
  referinta: 'REF20260305-0042', platitor_nume: 'Primăria Municipiului Exemplu', platitor_cui: 'RO1234567',
  cod_iban: 'RO49TREZ0000000000123456', platitor_iban: 'RO49TREZ0000000000123456', cont_bancar: 'RO49TREZ0000000000123456',
  cont: '50.01.01', numar_extras: '57', nr_op: 'OP1234', nr_doc: '4021', nr_doc_extras: '4021', doc: 'D1234',
  tip_rand: 'Rezervare', den_bene: 'Furnizor Exemplu SRL', doc_just: 'Factura 12/2026', ord: '14 - 07.04.2026',
  rev: 'Rev. 1', tip: 'DDF', nr: '3', semn: 'AB,CD,Ordonator', cod_ang: 'ANG-1001', cod_contract: 'ANG-1001',
  cod_program: '0001', cod_ai: 'AI01', document: '1234', eticheta: 'Buget + rectificări', dc: '000_DEMO',
  name: 'Unitatea Demo', cf: '12345678', exists_label: 'Da', big_tables: 'FX_Istoric, FX_Extrase, FX_Indicatori', years: '2025–2026',
  file: 'api_server.log', level: 'INFO', user: 'utilizator@exemplu.ro', db_label: '000_DEMO', ip: '203.0.113.25', session: 'a1b2c3',
  msg: 'GET /api/portal/date/sumar 200 in 41 ms', country_label: 'România (RO)', device: 'Calculator', top_label: 'Acasă',
  sections_text: 'Acasă 25 s · Tutoriale 40 s · Integrare 12 s', referrer: 'google.com', screen: '1920x1080', lang: 'ro', robot: '',
  label: 'Acasă', ss_: '01A',
};

const DATES = ['2026-01-15', '2026-02-03', '2026-02-27', '2026-03-12', '2026-04-08', '2026-05-19', '2026-06-02', '2026-07-21'];
const AMOUNTS = [1250.5, 48000, 310.25, 15200.8, 99.99, 7300, 560.4, 22150];

function cellValue(col, i) {
  const k = col.key;
  if (col.valueType === 'datetime') {
    const withTime = /generalDate|longTime|shortTime/.test(col.format || '');
    return withTime ? `${DATES[i % DATES.length]}T09:30:00` : DATES[i % DATES.length];
  }
  if (col.valueType === 'boolean') return i % 2 === 0;
  if (col.valueType === 'number') {
    if (/^nr|nrcrt|tables|users|logins|visits|seconds|err|warn|schema|average|rows_est|size_mb/.test(k) || col.format === 'fixed') return (i + 1) * 3;
    return AMOUNTS[i % AMOUNTS.length];
  }
  if (Object.prototype.hasOwnProperty.call(TEXT, k)) return TEXT[k];
  return `Exemplu ${col.title || k}`;
}

/** n made-up rows for these columns. */
export function sampleRows(columns, n = 8) {
  return Array.from({ length: n }, (_, i) => Object.fromEntries(columns.map((c) => [c.key, cellValue(c, i)])));
}
