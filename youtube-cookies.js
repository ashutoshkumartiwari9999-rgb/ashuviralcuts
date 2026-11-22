(() => {
  const file = document.getElementById('youtube-cookie-file');
  const status = document.getElementById('youtube-cookie-status');
  const note = document.getElementById('youtube-cookie-note');
  const choose = document.getElementById('youtube-cookie-choose');
  const filename = document.getElementById('youtube-cookie-filename');
  const upload = document.getElementById('youtube-cookie-upload');
  const clear = document.getElementById('youtube-cookie-clear');
  const login = document.getElementById('youtube-cookie-login');
  if (!file || !status || !note || !choose || !filename || !upload || !clear || !login) return;

  function setOwnerAccess(authorized) {
    choose.hidden = !authorized;
    file.disabled = !authorized;
    login.hidden = authorized;
    clear.disabled = !authorized;
    if (!authorized) {
      clear.hidden = true;
      upload.hidden = true;
      upload.disabled = true;
      filename.hidden = true;
    }
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

  choose.addEventListener('click', () => {
    if (!file.disabled) file.click();
  });

  file.addEventListener('change', () => {
    const chosen = file.files && file.files[0];
    if (!chosen) return;
    filename.textContent = chosen.name;
    filename.hidden = false;
    upload.hidden = false;
    upload.disabled = false;
    note.textContent = chosen.size > 1024 * 1024
      ? 'The cookies.txt file must be 1 MB or smaller.'
      : chosen.name.toLowerCase() !== 'cookies.txt'
        ? 'Choose a file named cookies.txt.'
        : 'Selected file is ready. Press “Upload cookies” to save it privately.';
  });

  upload.addEventListener('click', async () => {
    const chosen = file.files && file.files[0];
    if (!chosen) {
      note.textContent = 'Choose cookies.txt before uploading.';
      return;
    }
    if (chosen.name.toLowerCase() !== 'cookies.txt') {
      note.textContent = 'Please choose a file named cookies.txt.';
      return;
    }
    if (chosen.size > 1024 * 1024) {
      note.textContent = 'The cookies.txt file must be 1 MB or smaller.';
      return;
    }

    note.textContent = 'Uploading privately…';
    choose.disabled = true;
    upload.disabled = true;
    clear.disabled = true;
    try {
      const body = new FormData();
      body.append('file', chosen, 'cookies.txt');
      const response = await fetch('/youtube-cookies', { method: 'POST', body, credentials: 'same-origin' });
      const data = await response.json();
      if (response.status === 401) throw new Error('Owner sign-in expired. Sign in again before uploading cookies.');
      if (!response.ok) throw new Error(data.detail || 'Cookie upload failed.');
      file.value = '';
      filename.textContent = '';
      filename.hidden = true;
      upload.hidden = true;
      await refresh();
      note.textContent = data.message || 'YouTube cookies saved privately.';
    } catch (error) {
      note.textContent = error.message || 'Cookie upload failed. Please try again.';
    } finally {
      choose.disabled = false;
      upload.disabled = false;
      clear.disabled = false;
    }
  });

  clear.addEventListener('click', async () => {
    clear.disabled = true;
    choose.disabled = true;
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
      choose.disabled = false;
    }
  });

  refresh();
})();
