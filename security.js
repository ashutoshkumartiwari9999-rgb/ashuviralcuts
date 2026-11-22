// Adds owner links and a public maintenance/notice banner.
(async function () {
  try {
    const state = await fetch('/auth/status', { credentials: 'same-origin' }).then(r => r.json());
    const host = document.querySelector('.social');
    if (host && state.owner_admin_required) {
      const admin = document.createElement('a');
      admin.href = '/admin';
      admin.className = 'secondary';
      admin.textContent = 'Owner Admin';
      host.append(admin);
    }
    if (host && state.cookie_admin_required && !state.cookie_admin_authenticated) {
      const link = document.createElement('a');
      link.href = '/login?next=/';
      link.className = 'secondary';
      link.textContent = 'Manage private YouTube cookies';
      host.append(link);
    }
    if (!host || !state.cookie_admin_required || !state.cookie_admin_authenticated) return;
    const button = document.createElement('button');
    button.className = 'secondary';
    button.type = 'button';
    button.textContent = 'Sign out';
    button.addEventListener('click', async () => {
      await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' });
      location.replace(state.required ? '/login' : '/');
    });
    host.append(button);
  } catch (_) { /* The app remains usable when local authentication is disabled. */ }
})();

(async function () {
  try {
    const settings = await fetch('/public-settings', { cache: 'no-store' }).then(r => r.json());
    const banner = document.getElementById('site-notice');
    if (!banner || (!settings.maintenance_mode && !settings.site_notice)) return;
    banner.hidden = false;
    banner.textContent = settings.maintenance_mode
      ? `Maintenance mode is on. New processing is paused.${settings.site_notice ? ` ${settings.site_notice}` : ''}`
      : settings.site_notice;
  } catch (_) { /* Public settings are optional. */ }
})();
