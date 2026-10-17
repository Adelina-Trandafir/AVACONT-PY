import { reportApplicationButton } from './application-menu.js';
import { format } from './fields.js';

const dateText = value => value ? format(value, { type: 'date' }) : '';

export function reportHeading(title, { documentLabel, date, period } = {}) {
  const when = date ? dateText(date) : period ? [dateText(period.start), dateText(period.end)].filter(Boolean).join(' — ') : '';
  return [title, documentLabel, when].filter(Boolean).join(' • ');
}

export function createReportHeader(title) {
  const header = document.createElement('header');
  header.className = 'vc-report-header';
  const brand = reportApplicationButton();
  const heading = document.createElement('h2');
  heading.textContent = title;
  if (brand) header.append(brand);
  header.append(heading);
  return header;
}
