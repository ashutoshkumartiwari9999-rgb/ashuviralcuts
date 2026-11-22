(() => {
  const $ = id => document.getElementById(id);
  const notice = $('notice');
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status}).`);
    return body;
  }
  function fmtBytes(value) {
    const n = Number(value || 0);
    return n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GB` : `${(n / 1024 ** 2).toFixed(1)} MB`;
  }
  function empty(parent, message) { parent.replaceChildren(el('div', message, 'empty')); }
  function renderJobs(rows, paused) {
    const box = $('jobs'); box.replaceChildren();
    $('pause').textContent = paused ? 'Resume queue' : 'Pause queue';
    if (!rows.length) return empty(box, 'No recent jobs.');
    rows.forEach(job => {
      const item = el('div', null, 'item'), meta = el('div', null, 'meta');
      meta.append(el('b', `${job.kind || 'job'} · ${job.status || 'unknown'}`));
      meta.append(el('small', `${job.id}${job.stage ? ` · ${job.stage}` : ''}${job.error ? ` · ${job.error}` : ''}`));
      item.append(meta);
      if (job.queue_job) {
        const actions = el('div', null, 'row');
        if (job.status === 'queued') {
          const cancel = el('button', 'Cancel');
          cancel.addEventListener('click', () => changeJob(job.id, 'cancel'));
          actions.append(cancel);
        } else if (['error', 'interrupted', 'canceled'].includes(job.status)) {
          const retry = el('button', 'Retry', 'primary');
          retry.addEventListener('click', () => changeJob(job.id, 'retry'));
          actions.append(retry);
        }
        item.append(actions);
      }
      box.append(item);
    });
  }
  function renderUploads(rows) {
    const box = $('uploads'); box.replaceChildren();
    if (!rows.length) return empty(box, 'No saved video files.');
    rows.forEach(file => {
      const item = el('div', null, 'item'), meta = el('div', null, 'meta');
      meta.append(el('b', `${file.name} · ${fmtBytes(file.size_bytes)}`));
      meta.append(el('small', new Date(Number(file.modified) * 1000).toLocaleString()));
      const remove = el('button', 'Delete video', 'danger');
      remove.addEventListener('click', async () => {
        if (!window.confirm(`Delete ${file.name} and its matching metadata? This cannot be undone.`)) return;
        try { await api(`/admin/api/uploads/${encodeURIComponent(file.id)}`, { method: 'DELETE' }); notice.textContent = 'Video deleted.'; await load(); }
        catch (error) { notice.textContent = error.message; }
      });
      item.append(meta, remove); box.append(item);
    });
  }
  function renderErrors(rows) {
    const box = $('errors'); box.replaceChildren();
    if (!rows.length) return empty(box, 'No processing errors recorded.');
    rows.forEach(error => {
      const item = el('div', null, 'item'), meta = el('div', null, 'meta');
      meta.append(el('b', `${error.kind || 'job'} · ${error.id}`));
      meta.append(el('small', error.error || 'Processing failed.'));
      item.append(meta); box.append(item);
    });
  }
  async function load() {
    $('refresh').disabled = true;
    try {
      const data = await api('/admin/api/overview');
      const used = fmtBytes(data.storage.used_bytes), budget = fmtBytes(data.storage.budget_bytes);
      $('stats').replaceChildren();
      [[data.jobs.filter(x => ['running','starting','recording'].includes(x.status)).length, 'Active jobs'], [data.uploads.length, 'Videos'], [used, 'Storage used'], [`${data.storage.percent}%`, 'Storage budget']].forEach(([value, label]) => {
        const card = el('div', null, 'stat'); card.append(el('b', value)); card.append(el('span', label)); $('stats').append(card);
      });
      $('usage-bar').style.width = `${data.storage.percent}%`;
      const physicalFree = data.storage.physical_free_bytes == null ? 'unknown' : fmtBytes(data.storage.physical_free_bytes);
      $('storage-caption').textContent = `${used} of ${budget} · ${physicalFree} physically free · ${data.settings.upload_limit_mb} MB max upload`;
      renderJobs(data.jobs, data.queue_paused); renderUploads(data.uploads); renderErrors(data.errors);
      $('maintenance').checked = Boolean(data.settings.maintenance_mode);
      $('site-notice').value = data.settings.site_notice || '';
      $('upload-limit').value = data.settings.upload_limit_mb;
      $('storage-budget').value = data.settings.storage_budget_mb;
      $('storage-reserve').value = data.settings.storage_reserve_mb;
      $('limits').textContent = `${data.rate_limits.writes_per_ip_per_minute} writes/IP/minute · ${data.rate_limits.upload_import_analysis_make_per_10_minutes} uploads/imports/analyses/clip starts per 10 minutes · ${data.rate_limits.exports_per_10_minutes} exports per 10 minutes · ${data.rate_limits.transcriptions_per_hour} transcriptions per hour.`;
      notice.textContent = '';
    } catch (error) {
      notice.textContent = error.message;
      if (/sign in|owner admin/i.test(error.message)) setTimeout(() => location.replace('/login?next=/admin'), 300);
    } finally { $('refresh').disabled = false; }
  }
  async function changeJob(id, action) {
    try { await api(`/admin/api/jobs/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: '{}' }); notice.textContent = `Job ${action} request saved.`; await load(); }
    catch (error) { notice.textContent = error.message; }
  }
  $('refresh').addEventListener('click', load);
  $('pause').addEventListener('click', async () => {
    try { const paused = $('pause').textContent === 'Pause queue'; await api('/admin/api/queue/pause', { method: 'POST', body: JSON.stringify({ paused }) }); await load(); }
    catch (error) { notice.textContent = error.message; }
  });
  $('cleanup').addEventListener('click', async () => {
    try { const result = await api('/admin/api/storage/cleanup', { method: 'POST', body: '{}' }); notice.textContent = `Removed ${result.removed_files} old temporary files and ${result.removed_preview_folders} preview folders.`; await load(); }
    catch (error) { notice.textContent = error.message; }
  });
  $('settings').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await api('/admin/api/settings', { method: 'POST', body: JSON.stringify({ maintenance_mode: $('maintenance').checked, site_notice: $('site-notice').value, upload_limit_mb: $('upload-limit').value, storage_budget_mb: $('storage-budget').value, storage_reserve_mb: $('storage-reserve').value }) });
      notice.textContent = 'Website settings saved.'; await load();
    } catch (error) { notice.textContent = error.message; }
  });
  $('signout').addEventListener('click', async () => {
    try { await api('/auth/logout', { method: 'POST' }); location.replace('/'); }
    catch (error) { notice.textContent = error.message; }
  });
  load();
  window.setInterval(load, 30000);
})();
