// Adds the sign-out control only when the server has authentication enabled.
(async function () {
  try {
    const state = await fetch('/auth/status', { credentials: 'same-origin' }).then(r => r.json());
    if (!state.required || !state.authenticated) return;
    const host = document.querySelector('.social');
    if (!host) return;
    const button = document.createElement('button');
    button.className = 'secondary';
    button.type = 'button';
    button.textContent = 'Sign out';
    button.addEventListener('click', async () => {
      await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' });
      location.replace('/login');
    });
    host.append(button);
  } catch (_) { /* The app remains usable when local authentication is disabled. */ }
})();
