// Profile views: public profile (#/u/:username) + my profile/edit (#/me).
import { api, currentUser, getToken, setSession, clearSession } from '../api.js';
import {
  el, esc, mount, addForm, avatarEl, spinner, toast,
  fmtDate, emptyState, errMessage, isStandalone, doInstall,
} from '../ui.js';
import { refresh, refreshMe } from '../app.js';

// ---- public profile: #/u/:username ----
export async function userView(username) {
  mount(spinner());
  let user;
  try {
    user = await api('/users/' + encodeURIComponent(username));
  } catch (e) {
    mount(emptyState(errMessage(e)));
    return;
  }

  const me = currentUser();
  const isMe = !!(me && me.username &&
    me.username.toLowerCase() === String(user.username).toLowerCase());

  const card = el('<div class="card stack"></div>');

  const head = el('<div class="row"></div>');
  head.append(avatarEl(user, true));
  head.append(el(
    `<div class="grow"><h1 style="margin:0">${esc(user.display_name || user.username)}</h1>` +
    `<p class="muted" style="margin:.2rem 0 0">@${esc(user.username)}</p></div>`,
  ));
  card.append(head);

  if (user.bio) {
    card.append(el(`<p style="white-space:pre-wrap;margin:0">${esc(user.bio)}</p>`));
  }
  card.append(el(`<p class="muted" style="margin:0">Joined ${esc(fmtDate(user.created_at))}</p>`));
  card.append(el(
    `<div class="row wrap muted">⏱ ${esc(user.hours_volunteered)} hours · ` +
    `🪙 ${esc(user.tokens_earned)} earned · 📋 ${esc(user.projects_joined)} projects</div>`,
  ));

  if (!isMe) card.append(tipSection(user));

  mount(card);
}

// A "Send tokens" button that reveals an inline tip form for `user`.
function tipSection(user) {
  const wrap = el('<div></div>');
  const btn = el('<button class="act primary block">Send tokens</button>');
  btn.onclick = () => {
    const form = addForm({
      title: 'Send tokens to @' + user.username,
      fields: [
        { name: 'amount', label: 'Amount (🪙)', type: 'number', required: true, min: 1, step: 1, placeholder: '1' },
        { name: 'note', label: 'Note (optional)', type: 'textarea', rows: 2 },
      ],
      submit: 'Send',
      onSubmit: async (body) => {
        await api('/tokens/tip', {
          body: { to_username: user.username, amount: body.amount, note: body.note },
        });
        toast('Sent ' + body.amount + ' 🪙 to @' + user.username);
        await refreshMe();
        form.replaceWith(btn); // collapse back to the button
      },
    });
    btn.replaceWith(form);
  };
  wrap.append(btn);
  return wrap;
}

// ---- my profile + edit: #/me ----
export async function meView() {
  let me = currentUser();
  if (!me) { mount(spinner()); me = await refreshMe(); }
  if (!me) { mount(emptyState('Please sign in.')); return; }

  // Summary
  const summary = el('<div class="card stack"></div>');
  const head = el('<div class="row"></div>');
  head.append(avatarEl(me, true));
  head.append(el(
    `<div class="grow"><h1 style="margin:0">${esc(me.display_name || me.username)}</h1>` +
    `<p class="muted" style="margin:.2rem 0 0">@${esc(me.username)}</p></div>`,
  ));
  summary.append(head);
  summary.append(el(
    `<div class="row"><span class="grow">Balance</span><strong>🪙 ${esc(me.balance)}</strong></div>`,
  ));
  summary.append(el(`<a class="act ghost block" href="#/u/${esc(me.username)}">View public profile</a>`));

  // Edit form
  const editCard = el('<div class="card"></div>');
  editCard.append(addForm({
    title: 'Edit profile',
    fields: [
      { name: 'display_name', label: 'Display name', value: me.display_name || '' },
      { name: 'bio', label: 'Bio', type: 'textarea', rows: 4, value: me.bio || '', placeholder: 'Tell people a little about yourself' },
    ],
    submit: 'Save changes',
    onSubmit: async (body) => {
      const updated = await api('/me', { method: 'PATCH', body });
      setSession(getToken(), updated);
      toast('Profile updated');
      await refreshMe();
      refresh();
    },
  }));

  // Actions: install + sign out
  const actions = el('<div class="card stack"></div>');
  if (!isStandalone()) {
    const install = el('<button class="act block">📲 Install app</button>');
    install.onclick = () => doInstall();
    actions.append(install);
  }
  const out = el('<button class="act del block">Sign out</button>');
  out.onclick = async () => {
    out.disabled = true;
    try { await api('/auth/logout', { method: 'POST' }); } catch { /* sign out locally regardless */ }
    clearSession();
    location.hash = '#/login';
  };
  actions.append(out);

  mount(summary, editCard, actions);
}
