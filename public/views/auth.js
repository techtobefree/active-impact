// "Save your log / Sign in" — a SINGLE convert flow (SERVICE_LOG.md §4, §7).
// The app is always signed in (guest or real). This screen attaches credentials
// to the current guest, OR — if the email already exists and the password is
// right — signs into that account and MERGES the guest's logs into it.
// POST /auth/convert: 401 = wrong password for an existing email; 409 not_a_guest
// (already real) -> just go home; 422 = bad shape (surfaced per field by addForm).
import { api, setSession, currentUser, getToken, popReturn, peekReturn } from '../api.js';
import { el, mount, addForm } from '../ui.js';
import { refreshMe } from '../app.js';

// Live guards for the traps real users hit: stray spaces (often re-inserted by
// autofill) and phone auto-capitalization in the email field.
const emailField = {
  name: 'email', label: 'Email', required: true, placeholder: 'you@example.com',
  attrs: { autocapitalize: 'none', autocorrect: 'off', spellcheck: 'false', inputmode: 'email' },
  transform: (v) => v.toLowerCase().replace(/\s+/g, ''),
  validate: (v) => {
    if (v.length > 254) return 'At most 254 characters.';
    if (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) return null;
    return "That doesn't look like an email address — e.g. jordan@example.com.";
  },
};
const displayField = {
  name: 'display_name', label: 'Display name (optional)', placeholder: 'e.g. Jordan Kay',
  hint: 'Leave blank to keep your current handle.',
  validate: (v) => ([...v.trim()].length > 60 ? 'At most 60 characters.' : null),
};
const passwordField = {
  name: 'password', label: 'Password', type: 'password', required: true, placeholder: 'your password',
  validate: (v) => {
    const chars = [...v].length; // code points — what the server counts
    if (chars < 1) return 'Password is required.';
    if (chars > 72) return 'At most 72 characters.';
    if (v !== v.trim()) return 'Starts or ends with a space — that’s easy to mistype later. Remove it.';
    return null;
  },
};

// The convert form node. Reused by #/login|#/register AND the Me "create account"
// card. `onSuccess(data)` overrides the default (return-to navigation).
export function convertForm({ title, onSuccess } = {}) {
  const form = addForm({
    title,
    fields: [emailField, displayField, passwordField],
    submit: 'Create account',
    onSubmit: async (body) => {
      try {
        const data = await api('/auth/convert', { body, authChallenge: true });
        setSession(data.token, data.user);
        await refreshMe();
        if (onSuccess) onSuccess(data); else location.hash = popReturn();
      } catch (e) {
        // Already a real account (a rare race) — nothing to convert; go home.
        if (e && e.detail === 'not_a_guest') { await refreshMe(); location.hash = '#/'; return; }
        throw e; // let addForm surface invalid_credentials / 422 per field
      }
    },
  });
  form.querySelector('[name=email]')?.setAttribute('autocomplete', 'email');
  form.querySelector('[name=password]')?.setAttribute('autocomplete', 'current-password');
  return form;
}

// #/login and #/register both render this. A real user is redirected home by the
// router; a guest stays and sees the form.
export async function convertView() {
  const me = currentUser();
  if (getToken() && me && me.is_guest === false) { location.hash = '#/'; return; }

  const intro = el(`<div class="card stack">
    <h1>Save your log</h1>
    <p class="muted">New here? We’ll create your account. Already have one? We’ll sign you in and bring your logs with you.</p>
  </div>`);
  // Arrived via a scanned check-in QR? Reassure them the scan worked.
  if ((peekReturn() || '').startsWith('#/c/')) {
    intro.append(el('<p class="banner info">✅ Your check-in code was scanned — save your account and we’ll take you straight to the waiver.</p>'));
  }

  const root = el('<div class="stack"></div>');
  root.append(intro, convertForm());
  mount(root);
}

// Back-compat aliases (the router imports convertView directly).
export const loginView = convertView;
export const registerView = convertView;
