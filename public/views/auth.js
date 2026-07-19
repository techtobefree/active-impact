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

  // Live guards for the exact traps real users hit: an email (often re-inserted
  // by the browser's autofill) or phone auto-capitalization in the handle field.
  const usernameField = {
    name: 'username', label: 'Username', required: true,
    placeholder: 'e.g. jordan_kay',
    hint: 'Your public handle — letters, numbers, _ or - (not an email).',
    attrs: { autocapitalize: 'none', autocorrect: 'off', spellcheck: 'false' },
    transform: (v) => v.toLowerCase().replace(/\s+/g, ''),
    validate: (v) => {
      if (/^[a-z0-9_-]{3,30}$/.test(v)) return null;
      if (v.includes('@')) return 'This is your public handle, not an email — try something like jordan_kay.';
      if (v.length < 3) return 'At least 3 characters.';
      if (v.length > 30) return 'At most 30 characters.';
      return 'Only letters, numbers, _ and - (no spaces or symbols).';
    },
  };
  const fields = isLogin
    ? [
        // Keep the email guard at login too: an autofilled email would otherwise
        // dead-end as "wrong username or password" while the user retries passwords.
        { ...usernameField, placeholder: '', hint: undefined,
          validate: (v) => (v.includes('@')
            ? 'You signed up with a handle, not an email — e.g. jordan_kay.' : null) },
        { name: 'password', label: 'Password', type: 'password', required: true },
      ]
    : [
        usernameField,
        { name: 'display_name', label: 'Display name (optional)', placeholder: 'Shown to others, e.g. Jordan Kay' },
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
  form.querySelector('[name=username]')?.setAttribute('autocomplete', 'username');
  form.querySelector('[name=password]')?.setAttribute('autocomplete', isLogin ? 'current-password' : 'new-password');

  const switcher = el(`<p class="center muted">${isLogin ? 'New here? ' : 'Have an account? '}<a href="#/${isLogin ? 'register' : 'login'}">${isLogin ? 'Create an account' : 'Sign in'}</a></p>`);
  mount(intro, form, switcher);
}
