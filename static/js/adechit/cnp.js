// vercnp/UltimaCifra, preserving the supplied VBA bounds.
export function cnpCode(value) {
  if (!/^\d{13}$/.test(value)) return 0;
  if (Number(value.slice(3, 5)) > 12) return 2;
  if (Number(value.slice(5, 7)) > 31) return 3;
  if (Number(value.slice(7, 9)) > 52) return 4;
  let check = [...value.slice(0, 12)].reduce((sum, digit, index) => sum + Number(digit) * Number('279146358279'[index]), 0) % 11;
  if (check === 10) check = 1;
  return Number(value[12]) === check ? -1 : 5;
}

export function cnpMessage(value) {
  if (!value) return '';
  return { 0: 'CNP-ul trebuie să conțină 13 cifre.', 2: 'Luna din CNP este invalidă.',
    3: 'Ziua din CNP este invalidă.', 4: 'Codul județului din CNP este invalid.',
    5: 'Cifra de control a CNP-ului este invalidă.' }[cnpCode(value)] || '';
}
