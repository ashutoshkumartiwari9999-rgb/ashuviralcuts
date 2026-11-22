(() => {
  const file = document.getElementById('youtube-cookie-file');
  const status = document.getElementById('youtube-cookie-status');
  const note = document.getElementById('youtube-cookie-note');
  const clear = document.getElementById('youtube-cookie-clear');
  const login = document.getElementById('youtube-cookie-login');
  const uploadLabel = document.querySelector('[data-cookie-upload]');
  if (!file || !status || !note || !clear || !login || !uploadLabel) return;

  function setOwnerAccess(authorized) {
    uploadLabel.hidden = !authorized;
    file.disabled = !authorized;
    login.hidden = authorized;
    clear.disabled = !authorized;
    if (!authorized) clear.hidden = true;
  }

  async function refresh() {
    setOwnerAccess(false);
    status.textContent = 'Checking owner access…';
    try {
      const response = await fetch('/youtube-cookies/status', { cache: 'no-store', credentials: 'same-origin' });
      const data = await response.json();
      if (response.status === 401) {
        status.textContent = 'Owner sign-in required';
        note.textContent = 'Sign in as the owner to upload or remove cookies. Public visitors cannot manage this private setting.';
        clear.hidden = true;
        return;
      }
      if (!response.ok) throw new Error(data.detail || 'Could not check cookie settings.');
      setOwnerAccess(true);
      status.textContent = data.configured ? 'Configured privately' : 'Not configured';
      note.textContent = data.configured
        ? 'Cookies are private and used only for YouTube imports. Replace them whenever YouTube rejects the session.'
        : 'Choose a Netscape-format cookies.txt file (maximum 1 MB). Only the signed-in owner can upload it.';
      clear.hidden = !data.configured;
    } catch (error) {
      status.textContent = 'Status unavailable';
      note.textContent = error.message || 'Could not check cookie settings.';
    }
  }

  file.addEventListener('change', async () => {
    const chosen = file.files && file.files[0];
    if (!chosen) return;
    note.textContent = 'Uploading privately…';
    file.disabled = true;
    try {
      if (chosen.name.toLowerCase() !== 'cookies.txt') throw new Error('Please choose a file named cookies.txt.');
      if (chosen.size > 1024 * 1024) throw new Error('The cookies.txt file must be 1 MB or smaller.');
      const body = new FormData();
      body.append('file', chosen, 'cookies.txt');
      const response = await fetch('/youtube-cookies', { method: 'POST', body, credentials: 'same-origin' });
      const data = await response.json();
      if (response.status === 401) throw new Error('Owner sign-in expired. Sign in again before uploading cookies.');
      if (!response.ok) throw new Error(data.detail || 'Cookie upload failed.');
      await refresh();
      note.textContent = data.message || 'YouTube cookies saved privately.';
    } catch (error) {
      note.textContent = error.message || 'Cookie upload failed. Please try again.';
    } finally {
      file.value = '';
      file.disabled = false;
    }
  });

  clear.addEventListener('click', async () => {
    clear.disabled = true;
    try {
      const response = await fetch('/youtube-cookies', { method: 'DELETE', credentials: 'same-origin' });
      const data = await response.json();
      if (response.status === 401) throw new Error('Owner sign-in expired. Sign in again before changing cookies.');
      if (!response.ok) throw new Error(data.detail || 'Could not remove saved cookies.');
      await refresh();
      note.textContent = data.message || 'Saved YouTube cookies were removed.';
    } catch (error) {
      note.textContent = error.message || 'Could not remove saved cookies.';
    } finally {
      clear.disabled = false;
    }
  });

  refresh();
})();
