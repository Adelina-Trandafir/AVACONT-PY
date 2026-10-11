// Slice 0110-11 -- the column layouts every visitor gets, per grid id (see columns.js for the ids).
//
// Written from the file the administrator exports from Administrare > Coloane: that file is given
// to the developer, who pastes the chosen positions here. Empty = every grid shows all its columns
// in the order of columns.js, each as wide as its content.
//
//   'ang.sumar': {
//     order:  ['clsf', 'partener', ...],    // the columns in the wanted order (any left out follow, in their own order)
//     hidden: ['cod_indicator'],            // columns that do not show
//     widths: { clsf: 140, partener: 220 }, // px; a column left out is as wide as its content
//     fill:   'partener',                   // the ONE column that takes the free width when the grid is wider than its columns
//   },
export const DEFAULT_LAYOUTS = {
  'ang.sumar': {
    order: [
      'clsf',
      'cod_indicator',
      'partener',
      'credit_bug',
      'total_rezervari',
      'total_receptii',
      'total_plati',
      'total_revizii',
      'total_ordonantari'
    ],
    hidden: [
      'partener',
      'credit_bug'
    ],
    widths: {},
    fill: 'clsf'
  },
  'ang.istoric': {
    order: [
      'data_fx',
      'clsf',
      'tip_rand',
      'cod_indicator',
      'descriere',
      'val_rezervare_i',
      'val_rezervare_d',
      'val_rezervare_dif',
      'val_ang_leg',
      'val_receptie',
      'val_plata',
      'doc',
      'observatii'
    ],
    hidden: [
      'cod_indicator',
      'val_rezervare_i',
      'val_rezervare_d',
      'val_rezervare_dif',
      'val_ang_leg',
      'val_receptie',
      'val_plata',
      'doc',
      'observatii'
    ],
    widths: {},
    fill: 'descriere'
  },
  'ang.rezervari': {
    order: [
      'data_rezervare',
      'clsf',
      'denumire',
      'cod_indicator',
      'r_credit_bug',
      'r_initiala',
      'r_valoare',
      'r_definitiva',
      'are_ddf'
    ],
    hidden: [
      'data_rezervare',
      'denumire',
      'cod_indicator',
      'are_ddf'
    ],
    widths: {},
    fill: 'clsf'
  },
  'ang.receptii': {
    order: [
      'data_r',
      'clsf',
      'nrcrt_r',
      'cod_indicator',
      'descriere_r',
      'data_h',
      'denumire',
      'valoare',
      'dif',
      'suma_antet',
      'este_stergere'
    ],
    hidden: [
      'data_r',
      'nrcrt_r',
      'denumire',
      'dif',
      'suma_antet',
      'este_stergere',
      'cod_indicator'
    ],
    widths: {},
    fill: 'descriere_r'
  },
  'ang.extrase': {
    order: [
      'data_banca',
      'data_extras',
      'clsf',
      'nr_doc',
      'platitor_nume',
      'suma_debit',
      'suma_credit',
      'cod_contract',
      'rand_contract',
      'referinta',
      'explicatii'
    ],
    hidden: [
      'data_banca',
      'data_extras',
      'cod_contract',
      'rand_contract',
      'referinta',
      'explicatii'
    ],
    widths: {},
    fill: 'platitor_nume'
  },
  'ang.plati': {
    order: [
      'data_plata',
      'clsf',
      'nr_op',
      'denumire',
      'cod_indicator',
      'platitor_nume',
      'suma',
      'are_ord',
      'nr_doc_extras',
      'explicatii'
    ],
    hidden: [
      'data_plata',
      'cod_indicator',
      'are_ord',
      'denumire',
      'nr_doc_extras',
      'explicatii'
    ],
    widths: {
      denumire: 203
    },
    fill: 'platitor_nume'
  },
  'ang.fundamentari': {
    order: [
      'rev',
      'clsf',
      'element_fund',
      'val_prec',
      'val_cur',
      'val_tot'
    ],
    hidden: [
      'element_fund'
    ],
    widths: {},
    fill: null
  },
  'ang.fundamentari-lista': {
    order: [
      'nr',
      'data',
      'suma',
      'semn',
      'pdf'
    ],
    hidden: [
      'semn',
      'pdf'
    ],
    widths: {},
    fill: null
  },
  'ang.ordonantari': {
    order: [
      'ord',
      'clsf',
      'descriere',
      'den_bene',
      'total_receptii',
      'plati_ant',
      'valoare',
      'ramas',
      'doc_just'
    ],
    hidden: [
      'ord',
      'descriere',
      'doc_just'
    ],
    widths: {},
    fill: 'den_bene'
  },
  'ang.ordonantari-lista': {
    order: [
      'tip',
      'nr',
      'data',
      'suma',
      'semn',
      'pdf'
    ],
    hidden: [
      'tip',
      'semn',
      'pdf'
    ],
    widths: {},
    fill: null
  },
  'extrase.antete': {
    order: [
      'data_extras',
      'numar_extras',
      'clsf',
      'denumire',
      'cont',
      'cod_iban',
      'sid',
      'sic',
      'rpd',
      'rpc',
      'tsd',
      'tsc',
      'sfd',
      'sfc'
    ],
    hidden: [
      'data_extras',
      'numar_extras',
      'denumire',
      'cont',
      'cod_iban'
    ],
    widths: {},
    fill: null
  },
  'extrase.operatii': {
    order: [
      'data_banca',
      'data_doc',
      'cod_contract',
      'clsf',
      'nr_doc',
      'referinta',
      'platitor_nume',
      'platitor_cui',
      'platitor_iban',
      'suma_debit',
      'suma_credit',
      'rand_contract',
      'cod_program',
      'cod_ai',
      'explicatii'
    ],
    hidden: [
      'data_banca',
      'data_doc',
      'referinta',
      'rand_contract',
      'cod_ai',
      'explicatii',
      'cod_program'
    ],
    widths: {},
    fill: 'platitor_nume'
  },
  'clsf.buget': {
    order: [
      'data_inceput',
      'trim1',
      'trim2',
      'trim3',
      'trim4',
      'total'
    ],
    hidden: [],
    widths: {},
    fill: 'data_inceput'
  },
  'clsf.buget-grup': {
    order: [
      'clsf',
      'trim1',
      'trim2',
      'trim3',
      'trim4',
      'total'
    ],
    hidden: [],
    widths: {},
    fill: 'clsf'
  },
  'clsf.rectificari': {
    order: [
      'document',
      'data',
      'trim1',
      'trim2',
      'trim3',
      'trim4',
      'total'
    ],
    hidden: [],
    widths: {},
    fill: 'document'
  },
  'clsf.rectificari-grup': {
    order: [
      'clsf',
      'trim1',
      'trim2',
      'trim3',
      'trim4',
      'total'
    ],
    hidden: [],
    widths: {},
    fill: 'clsf'
  },
  'clsf.total': {
    order: [
      'eticheta',
      'trim1',
      'trim2',
      'trim3',
      'trim4',
      'total'
    ],
    hidden: [],
    widths: {},
    fill: 'eticheta'
  },
  'clsf.verificare': {
    order: [
      'clsf',
      'denumire',
      'ss',
      'buget_kbot',
      'credit_fx',
      'diferenta'
    ],
    hidden: [],
    widths: {},
    fill: 'denumire'
  },
  'parteneri.coduri': {
    order: [
      'clsf',
      'denumire_clsf',
      'cont_bancar',
      'cod_ang',
      'cod_ind'
    ],
    hidden: [],
    widths: {},
    fill: 'denumire_clsf'
  }
};
