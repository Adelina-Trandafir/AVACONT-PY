// Shared by report previews and the parent account statement.
(() => {
  const k_dialog = document.getElementById('report-excel-dialog');
  const k_form = document.getElementById('report-excel-form');
  const k_save = document.getElementById('report-excel-save');
  const k_error = document.getElementById('report-excel-error');
  const k_all = document.getElementById('report-excel-all');
  const k_columns = [...k_dialog.querySelectorAll('input[name="column"]')];
  let k_busy = false;

  function syncSelection() {
    const k_count = k_columns.filter((k_column) => k_column.checked).length;
    if (k_all) {
      k_all.checked = k_count === k_columns.length;
      k_all.indeterminate = k_count > 0 && k_count < k_columns.length;
    }
    k_save.disabled = k_busy || (k_columns.length > 0 && k_count === 0);
    k_error.hidden = true;
  }

  document.getElementById('report-excel').addEventListener('click', () => {
    k_columns.forEach((k_column) => { k_column.checked = true; });
    syncSelection();
    k_dialog.showModal();
  });
  k_all?.addEventListener('change', () => {
    k_columns.forEach((k_column) => { k_column.checked = k_all.checked; });
    syncSelection();
  });
  k_columns.forEach((k_column) => k_column.addEventListener('change', syncSelection));
  for (const k_id of ['report-excel-close', 'report-excel-cancel']) {
    document.getElementById(k_id).addEventListener('click', () => { if (!k_busy) k_dialog.close(); });
  }
  k_dialog.addEventListener('cancel', (k_event) => { if (k_busy) k_event.preventDefault(); });

  async function downloadStatement(k_selected) {
    const k_url = new URL(window.location.href);
    k_url.pathname = k_url.pathname.replace(/\/print$/, '/xlsx');
    k_url.searchParams.set('columns', k_selected.join(','));
    const k_response = await fetch(k_url, { credentials: 'same-origin' });
    if (!k_response.ok) {
      const k_body = await k_response.json();
      throw new Error(k_body.error || 'Exportul Excel nu a reușit.');
    }
    const k_object = URL.createObjectURL(await k_response.blob());
    const k_link = document.createElement('a');
    k_link.href = k_object;
    k_link.download = 'Fisa_cont.xlsx';
    document.body.append(k_link); k_link.click(); k_link.remove();
    setTimeout(() => URL.revokeObjectURL(k_object), 60000);
  }

  k_form.addEventListener('submit', async (k_event) => {
    k_event.preventDefault();
    if (k_busy || k_save.disabled) return;
    k_busy = true;
    syncSelection();
    k_form.setAttribute('aria-busy', 'true');
    k_save.textContent = 'Se exportă…';
    try {
      const k_selected = k_columns.filter((k_column) => k_column.checked).map((k_column) => k_column.value);
      if (window.adeExcel) await window.adeExcel(k_selected);
      else if (window.location.pathname.includes('/statement/')) await downloadStatement(k_selected);
      else throw new Error('Fereastra aplicației nu este disponibilă. Redeschideți raportul.');
      k_dialog.close();
    } catch (k_failure) {
      k_error.textContent = k_failure.message;
      k_error.hidden = false;
    } finally {
      k_busy = false;
      k_form.setAttribute('aria-busy', 'false');
      k_save.textContent = 'Exportă Excel';
      k_save.disabled = k_columns.length > 0 && !k_columns.some((k_column) => k_column.checked);
    }
  });
})();
