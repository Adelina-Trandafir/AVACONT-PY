// A local, paginated snapshot of the displayed values; official report APIs remain separate.
// Canvas preserves Romanian glyphs without adding a font/PDF dependency to the application.
import { readReportIdentity } from './report-identity.js';

export async function snapshotPdf(title, sections) {
  const identity = await readReportIdentity();
  await document.fonts.ready;
  const width = 1684, height = 1190, margin = 48, bottom = height - 65, pages = [];
  let canvas, context, y;
  function startPage() {
    canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height; context = canvas.getContext('2d');
    context.fillStyle = '#fff'; context.fillRect(0, 0, width, height);
    context.fillStyle = '#202936'; context.font = '19px Arial';
    const leftWidth = (width - margin * 2) * .6, rightWidth = (width - margin * 2) * .4;
    const left = [identity.name, identity.fiscal_code ? `Cod fiscal: ${identity.fiscal_code}` : '', identity.address]
      .filter(Boolean).flatMap(text => wrap(text, leftWidth - 16));
    const right = wrap(`Subunitatea: ${identity.subunit_name}`, rightWidth - 16);
    left.forEach((line, index) => context.fillText(line, margin, 38 + index * 24));
    context.textAlign = 'right';
    right.forEach((line, index) => context.fillText(line, width - margin, 38 + index * 24));
    context.textAlign = 'left';
    y = 38 + Math.max(left.length, right.length) * 24 + 18;
    context.fillStyle = '#17345a'; context.font = 'bold 26px Arial';
    context.fillText(title, margin, y, width - margin * 2); y += 35;
  }
  function finishPage() {
    context.fillStyle = '#697386'; context.font = '18px Arial'; context.fillText('Previzualizare • pagina ' + (pages.length + 1), margin, height - 25);
    const jpeg = atob(canvas.toDataURL('image/jpeg', 0.94).split(',')[1]); pages.push(Uint8Array.from(jpeg, (char) => char.charCodeAt(0)));
  }
  function wrap(raw, limit) {
    const text = String(raw ?? ''), lines = [];
    for (const paragraph of text.split(/\r?\n/)) {
      let line = '';
      for (const char of paragraph) {
        if (line && context.measureText(line + char).width > limit) { lines.push(line); line = ''; }
        line += char;
      }
      lines.push(line);
    }
    return lines.length ? lines : [''];
  }
  function heading(section, columns, widths) {
    context.fillStyle = '#17345a'; context.font = 'bold 23px Arial'; context.fillText(section.title, margin, y + 22, width - margin * 2); y += 38;
    let x = margin; context.font = 'bold 19px Arial';
    const labels = columns.map((col, index) => wrap(col.title || col.key, widths[index] - 14));
    const rowHeight = Math.max(...labels.map((lines) => lines.length), 1) * 24 + 12;
    for (let index = 0; index < columns.length; index++) {
      context.fillStyle = '#eaf0f8'; context.fillRect(x, y, widths[index], rowHeight); context.strokeStyle = '#c4cfdd'; context.strokeRect(x, y, widths[index], rowHeight);
      context.fillStyle = '#17345a'; labels[index].forEach((line, at) => context.fillText(line, x + 7, y + 25 + at * 24)); x += widths[index];
    }
    y += rowHeight;
  }
  startPage();
  for (const section of sections) {
    const columns = section.columns.filter((col) => col.visible !== false && col.display !== 'button'); if (!columns.length) continue;
    const weights = columns.map((col) => Math.max(70, col.width || 120)), sum = weights.reduce((total, weight) => total + weight, 0), widths = weights.map((weight) => (width - margin * 2) * weight / sum);
    if (y > bottom - 160) { finishPage(); startPage(); } heading(section, columns, widths);
    for (const row of section.rows) {
      context.font = '19px Arial'; const cells = columns.map((col, index) => wrap(col.formatter ? col.formatter(row[col.key]) : typeof row[col.key] === 'object' && row[col.key] != null ? JSON.stringify(row[col.key]) : row[col.key], widths[index] - 14));
      let offset = 0, remaining = Math.max(...cells.map((lines) => lines.length), 1);
      // A long note can occupy several pages without clipping its text.
      while (remaining > 0) {
        if (y > bottom - 42) { finishPage(); startPage(); heading(section, columns, widths); context.font = '19px Arial'; }
        const count = Math.min(remaining, Math.max(1, Math.floor((bottom - y - 12) / 24))), rowHeight = count * 24 + 12;
        let x = margin; context.strokeStyle = '#c4cfdd'; context.fillStyle = '#202936';
        cells.forEach((lines, index) => { context.strokeRect(x, y, widths[index], rowHeight); lines.slice(offset, offset + count).forEach((line, at) => context.fillText(line, x + 7, y + 25 + at * 24)); x += widths[index]; });
        y += rowHeight; offset += count; remaining -= count;
      }
    }
    y += 24; await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  finishPage();
  const encoder = new TextEncoder(), chunks = [], offsets = [0]; let length = 0;
  const append = (bytes) => { const chunk = typeof bytes === 'string' ? encoder.encode(bytes) : bytes; chunks.push(chunk); length += chunk.length; };
  const object = (id, text) => { offsets[id] = length; append(id + ' 0 obj\n' + text + '\nendobj\n'); };
  const stream = (id, dictionary, bytes) => { offsets[id] = length; append(id + ' 0 obj\n<< ' + dictionary + ' /Length ' + bytes.length + ' >>\nstream\n'); append(bytes); append('\nendstream\nendobj\n'); };
  append('%PDF-1.4\n'); object(1, '<< /Type /Catalog /Pages 2 0 R >>');
  object(2, '<< /Type /Pages /Count ' + pages.length + ' /Kids [' + pages.map((_, index) => (3 + index * 3) + ' 0 R').join(' ') + '] >>');
  pages.forEach((jpeg, index) => {
    const id = 3 + index * 3; object(id, '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /XObject << /Im0 ' + (id + 1) + ' 0 R >> >> /Contents ' + (id + 2) + ' 0 R >>');
    stream(id + 1, '/Type /XObject /Subtype /Image /Width ' + width + ' /Height ' + height + ' /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode', jpeg);
    stream(id + 2, '', encoder.encode('q 842 0 0 595 0 0 cm /Im0 Do Q'));
  });
  const xref = length, count = 3 + pages.length * 3; append('xref\n0 ' + count + '\n0000000000 65535 f \n');
  for (let id = 1; id < count; id++) append(String(offsets[id]).padStart(10, '0') + ' 00000 n \n');
  append('trailer\n<< /Size ' + count + ' /Root 1 0 R >>\nstartxref\n' + xref + '\n%%EOF\n');
  const pdf = new Uint8Array(length); let at = 0; for (const chunk of chunks) { pdf.set(chunk, at); at += chunk.length; } return pdf;
}
