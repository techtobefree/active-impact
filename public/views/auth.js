// Login + register. On success: store session, refresh self, return-to.
import { api, setSession, popReturn, peekReturn } from '../api.js';
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
  // Arrived via a scanned check-in QR? Reassure them the scan worked.
  if ((peekReturn() || '').startsWith('#/c/')) {
    intro.append(el(`<p class="banner info">✅ Your check-in code was scanned — ${isLogin ? 'sign in' : 'create an account'} and we’ll take you straight to the waiver.</p>`));
  }

  // Live guards for the traps real users hit: stray spaces (often re-inserted
  // by the browser's autofill) or phone auto-capitalization in the email field.
  const emailField = {
    name: 'email', label: 'Email', required: true,
    placeholder: 'you@example.com',
    attrs: { autocapitalize: 'none', autocorrect: 'off', spellcheck: 'false', inputmode: 'email' },
    transform: (v) => v.toLowerCase().replace(/\s+/g, ''),
    validate: (v) => {
      if (v.length > 254) return 'At most 254 characters.';
      if (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) return null;
      return "That doesn't look like an email address — e.g. jordan@example.com.";
    },
  };
  const fields = isLogin
    ? [
        { ...emailField, placeholder: '' },
        { name: 'password', label: 'Password', type: 'password', required: true },
      ]
    : [
        emailField,
        { name: 'display_name', label: 'Display name', required: true, placeholder: 'e.g. Jordan Kay',
          hint: 'Your public identity — shown to others instead of your email.',
          validate: (v) => {
            const chars = [...v.trim()].length;
            if (chars < 1) return 'Display name is required.';
            if (chars > 60) return 'At most 60 characters.';
            return null;
          } },
        { name: 'password', label: 'Password', type: 'password', required: true, placeholder: 'at least 8 characters',
          validate: (v) => {
            const chars = [...v].length; // code points — what the server counts
            if (chars < 8) return `At least 8 characters (this has ${chars}).`;
            if (chars > 72) return 'At most 72 characters.';
            if (v !== v.trim()) return 'Starts or ends with a space — that’s easy to mistype later. Remove it.';
            return null;
          } },
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
  form.querySelector('[name=email]')?.setAttribute('autocomplete', 'email');
  form.querySelector('[name=password]')?.setAttribute('autocomplete', isLogin ? 'current-password' : 'new-password');

  const switcher = el(`<p class="center muted">${isLogin ? 'New here? ' : 'Have an account? '}<a href="#/${isLogin ? 'register' : 'login'}">${isLogin ? 'Create an account' : 'Sign in'}</a></p>`);
  mount(intro, form, switcher);
}
