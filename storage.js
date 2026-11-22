// Small storage indicator and safe cleanup control for disposable processing leftovers.
(function () {
  const panel = document.querySelector('.import-panel');
  if (!panel) return;
  const box = document.createElement('div');
  box.className = 'storage-note import-note';
  box.setAttribute('role', 'status');
  const label = document.createElement('span');
  const cleanup = document.createElement('button');
  cleanup.type = 'button';
  cleanup.className = 'chip';
  cleanup.textContent = 'Clear old temporary files';
  cleanup.addEventListener('click', async () => {
    cleanup.disabled = true;
    try {
      const response = await fetch('/storage/cleanup', { method: 'POST' });
      if (!response.ok) throw new Error('Could not clear temporary files.');
      await refresh();
      const result = await response.json();
      label.textContent += ' · Cleared ' + format(result.freed_bytes) + ' of old temporary data.';
    } catch (error) { label.textContent = error.message; }
    finally { cleanup.disabled = false; }
  });
  box.append(label, cleanup);
  panel.append(box);
  function format(value) {
    const mb = Number(value || 0) / (1024 * 1024);
    return mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : Math.round(mb) + ' MB';
  }
  async function refresh() {
    const response = await fetch('/storage');
    if (!response.ok) return;
    const state = await response.json();
    label.textContent = 'Storage: ' + format(state.used_bytes) + ' of ' + format(state.budget_bytes) + ' managed · Video uploads up to ' + format(state.upload_limit_bytes) + '. Your clips and sources are never automatically deleted.';
  }
  const loadStatus=()=>refresh().catch(() => { label.textContent = 'Storage status is unavailable.'; });
  if('requestIdleCallback' in window)window.requestIdleCallback(loadStatus,{timeout:5000});else window.setTimeout(loadStatus,1200);
})();
