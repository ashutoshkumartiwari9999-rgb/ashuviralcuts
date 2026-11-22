(function () {
  const $ = id => document.getElementById(id);
  let liveId = null, liveTimer = null;
  const request = async (path, body) => {
    const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Request failed.');
    return data;
  };
  function showLogin(message) { $('login').hidden = false; $('studio').hidden = true; if (message) $('login-status').textContent = message; }
  function showStudio() { $('login').hidden = true; $('studio').hidden = false; }
  async function connect() {
    try { await request('/m/login', {pin:$('pin').value}); showStudio(); await Promise.all([refreshLibrary(), checkLive()]); }
    catch (error) { showLogin(error.message); }
  }
  $('login-form').addEventListener('submit', event => { event.preventDefault(); connect(); });
  function appendClip(parent, clip, file) {
    const card = document.createElement('article'); card.className = 'clip';
    const video = document.createElement('video'); video.controls = true; video.preload = 'metadata'; video.playsInline = true; video.src = '/media/' + encodeURIComponent(file);
    const title = document.createElement('b'); title.textContent = clip.title || 'AshuViralCuts clip';
    const link = document.createElement('a'); link.href = video.src; link.download = 'ashuviralcuts-' + file + '.mp4'; link.textContent = 'Download MP4';
    card.append(video, title, link); parent.append(card);
  }
  async function refreshLibrary() {
    const host = $('library'); host.replaceChildren();
    try {
      const clips = await request('/library');
      if (!clips.length) { host.innerHTML = '<div class="empty">No saved clips yet.</div>'; return; }
      clips.slice(0, 12).forEach(clip => appendClip(host, clip, clip.id));
    } catch (error) { host.textContent = error.message; }
  }
  function showLiveClips(clips) {
    const host = $('live-clips'); host.replaceChildren();
    for (const clip of (clips || []).slice(-6).reverse()) if (clip.file) appendClip(host, clip, clip.file);
  }
  async function checkLive() {
    try {
      const active = await request('/live-active');
      if (!active.live) { liveId = null; $('live-status').textContent = 'No live session running.'; $('clip-now').disabled = $('live-stop').disabled = true; return; }
      liveId = active.live; await pollLive();
      if (!liveTimer) liveTimer = setInterval(pollLive, 3000);
    } catch (error) { $('live-status').textContent = error.message; }
  }
  async function pollLive() {
    if (!liveId) return;
    try {
      const state = await request('/live/' + encodeURIComponent(liveId));
      $('live-status').textContent = (state.stage || state.status || 'Live') + ' · ' + (state.elapsed || 0) + ' sec · chat ' + (state.chat || 'connecting') + ' · ' + (state.hype_label || 'quiet');
      $('clip-now').disabled = !['recording'].includes(state.status); $('live-stop').disabled = !['recording','starting'].includes(state.status);
      showLiveClips(state.clips);
      if (!['recording','starting'].includes(state.status)) { liveId = null; clearInterval(liveTimer); liveTimer = null; await refreshLibrary(); }
    } catch (error) { $('live-status').textContent = error.message; }
  }
  $('live-start').addEventListener('click', async () => {
    $('live-start').disabled = true;
    try { const result = await request('/live/start', {url:$('live-url').value, before:24, after:14}); liveId = result.live; $('live-status').textContent = 'Starting live recorder…'; if (!liveTimer) liveTimer = setInterval(pollLive, 3000); await pollLive(); }
    catch (error) { $('live-status').textContent = error.message; }
    finally { $('live-start').disabled = false; }
  });
  $('clip-now').addEventListener('click', async () => { try { await request('/live/' + liveId + '/clip-now', {seconds:30}); $('live-status').textContent = 'Clip requested. It will appear when rendering finishes.'; } catch (error) { $('live-status').textContent = error.message; } });
  $('live-stop').addEventListener('click', async () => { try { await request('/live/' + liveId + '/stop', {}); $('live-status').textContent = 'Stopping the live session…'; } catch (error) { $('live-status').textContent = error.message; } });
  $('make').addEventListener('click', async () => {
    const button = $('make'); button.disabled = true; $('make-status').textContent = 'Starting…'; $('make-clips').replaceChildren();
    try {
      const {job} = await request('/make', {url:$('make-url').value, count:Number($('count').value), min_len:15, max_len:Number($('length').value), layout:'blur', caption_style:'gta'});
      while (true) {
        const state = await request('/status/' + encodeURIComponent(job));
        $('make-status').textContent = state.stage || state.message || state.status || 'Working…';
        for (const clip of state.clips || []) if (clip.file && !$('make-clips').querySelector('[data-file="' + CSS.escape(clip.file) + '"]')) { const before = $('make-clips').children.length; appendClip($('make-clips'), clip, clip.file); $('make-clips').lastElementChild.dataset.file = clip.file; if ($('make-clips').children.length > before) $('make-status').textContent = 'Clip ready · ' + ($('make-status').textContent || ''); }
        if (state.status === 'done') { $('make-status').textContent = state.stage || 'Clips ready.'; await refreshLibrary(); break; }
        if (state.status === 'error') throw new Error(state.error || 'Clip creation failed.');
        await new Promise(resolve => setTimeout(resolve, 1200));
      }
    } catch (error) { $('make-status').textContent = error.message; }
    finally { button.disabled = false; }
  });
  $('reload').addEventListener('click', async () => { if (!$('login').hidden) return connect(); await Promise.all([refreshLibrary(), checkLive()]); });
  $('library-refresh').addEventListener('click', refreshLibrary);
  request('/phone/status').then(status => { if (!status.enabled) showLogin('Enable local phone access on the computer first.'); else connect(); }).catch(() => showLogin('Enter the PIN shown on the computer.'));
})();
