// Login + register. On success: store session, refresh self, return-to.
import { api, setSession, popReturn } from '../api.js';
import { el, mount, addForm } from '../ui.js';
import { refreshMe } from '../app.js';

export async function loginView() { renderAuth(true); }
export async function registerView() { renderAuth(false); }

function renderAuth(isLogin) {
  const intro = el(`<div class="card stack">
    <h1>${isLogin ? 'Welcome back' : 'Join Active Impact'}</h1>
    <p class="muted">${isLogin
      ? 'Sign in to find and lead local service projects.'
      : 'Volunteer for local projects, earn impact tokens, and share needs and offers.'}</p>
  </div>`);

  const fields = isLogin
    ? [
        { name: 'username', label: 'Username', required: true },
        { name: 'password', label: 'Password', type: 'password', required: true },
      ]
    : [
        { name: 'username', label: 'Username', required: true, placeholder: '3–30 chars: a–z, 0–9, _ or -' },
        { name: 'display_name', label: 'Display name (optional)' },
        { name: 'password', label: 'Password', type: 'password', required: true, placeholder: 'at least 8 characters' },
      ];

  const form = addForm({
    fields,
    submit: isLogin ? 'Sign in' : 'Create account',
    onSubmit: async (body) => {
      const data = await api('/auth/' + (isLogin ? 'login' : 'register'), { body });
      setSession(data.token, data.user);
      await refreshMe();
      location.hash = popReturn();
    },
  });
  form.querySelector('[name=username]')?.setAttribute('autocomplete', 'username');
  form.querySelector('[name=password]')?.setAttribute('autocomplete', isLogin ? 'current-password' : 'new-password');

  const switcher = el(`<p class="center muted">${isLogin ? 'New here? ' : 'Have an account? '}<a href="#/${isLogin ? 'register' : 'login'}">${isLogin ? 'Create an account' : 'Sign in'}</a></p>`);
  mount(intro, form, switcher);
}
