// Profile views: public profile (#/u/:id) + my profile/edit (#/me).
import { api, currentUser, getToken, setSession, clearSession, clearReturn } from '../api.js';
import {
  el, esc, mount, addForm, avatarEl, spinner, toast, toastErr,
  fmtDate, emptyState, errMessage, isStandalone, doInstall,
} from '../ui.js';
import { refresh, refreshMe } from '../app.js';
import { convertForm } from './auth.js';
import { myRecords } from './records.js';
import { activityFeed, upcomingSection } from './social.js';

// "My log" — everything I've logged, including the entries that matched no event
// and therefore live only here (FEED.md F7).
// Followers / Following for me — the founder's "a list of our followers on our
// profile", and the way to reach the Block control.
function socialSection(me) {
  const card = el('<div class="card row"></div>');
  card.append(el(`<a class="grow" href="#/u/${esc(me.id)}/followers">👥 Followers</a>`));
  card.append(el(`<a href="#/u/${esc(me.id)}/following">Following</a>`));
  return card;
}

function myLogSection() {
  const wrap = el('<div class="stack"></div>');
  wrap.append(el('<div class="section-label">My log</div>'));
  wrap.append(el('<a class="act ghost block" href="#/log">＋ Log a service</a>'));
  wrap.append(myRecords());
  return wrap;
}

// ---- public profile: #/u/:id ----
export async function userView(id) {
  mount(spinner());
  let user;
  try {
    user = await api('/users/' + encodeURIComponent(id));
  } catch (e) {
    mount(emptyState(errMessage(e)));
    return;
  }

  const me = currentUser();
  const isMe = !!(me && me.id != null && me.id === user.id);

  const card = el('<div class="card stack"></div>');

  const head = el('<div class="row"></div>');
  head.append(avatarEl(user, true));
  head.append(el(
    `<div class="grow"><h1 style="margin:0">${esc(user.display_name)}</h1></div>`,
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

  // Follower / following counts, tappable through to the lists.
  card.append(el(
    `<div class="row wrap"><a class="muted" href="#/u/${esc(user.id)}/followers">` +
    `<strong>${esc(user.follower_count)}</strong> followers</a>` +
    `<a class="muted" href="#/u/${esc(user.id)}/following">` +
    `<strong>${esc(user.following_count)}</strong> following</a></div>`,
  ));

  const root = el('<div class="stack"></div>');
  root.append(card);
  if (!isMe) root.append(followButton(user), tipSection(user));

  // Current information first: where they are right now, what they are going to.
  root.append(upcomingSection(user.id));

  // …and then the thing their page actually IS: what they have been doing.
  root.append(el('<div class="section-label">Activity</div>'));
  root.append(activityFeed(`/users/${encodeURIComponent(user.id)}/activity`, {
    empty: isMe ? "You haven't logged, RSVP'd or checked in yet."
      : `Nothing from ${user.display_name} yet.`,
  }));
  mount(root);
}

// Follow / Following, repainting in place. Following someone is what puts their
// activity in my feed and my notifications — nothing else.
function followButton(user) {
  const btn = el('<button class="act block"></button>');
  const paint = () => {
    btn.textContent = user.is_following ? '✓ Following' : 'Follow';
    btn.classList.toggle('primary', !!user.is_following);
  };
  paint();
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const r = await api(`/users/${user.id}/follow`, { method: user.is_following ? 'DELETE' : 'POST' });
      user.is_following = r.is_following;
      user.follower_count = r.follower_count;
      paint();
    } catch (e) { toastErr(e); }
    finally { btn.disabled = false; }
  };
  return btn;
}

// A "Tip" button that reveals an inline tip form for `user`.
function tipSection(user) {
  const wrap = el('<div></div>');
  const btn = el('<button class="act primary block">Tip tokens</button>');
  btn.onclick = () => {
    const form = addForm({
      title: 'Tip ' + user.display_name,
      fields: [
        { name: 'amount', label: 'Amount (🪙)', type: 'number', required: true, min: 1, step: 1, placeholder: '1' },
        { name: 'note', label: 'Note (optional)', type: 'textarea', rows: 2 },
      ],
      submit: 'Tip 🪙',
      onSubmit: async (body) => {
        await api('/tokens/tip', {
          body: { to_user_id: user.id, amount: body.amount, note: body.note },
        });
        toast('Tipped ' + body.amount + ' 🪙 to ' + user.display_name);
        await refreshMe();
        form.replaceWith(btn); // collapse back to the button
      },
    });
    btn.replaceWith(form);
  };
  wrap.append(btn);
  return wrap;
}

// ---- my profile: #/me ----
export async function meView() {
  let me = currentUser();
  if (!me) { mount(spinner()); me = await refreshMe(); }
  if (!me) { mount(emptyState('Please sign in.')); return; }
  return me.is_guest ? guestMe(me) : realMe(me);
}

// Shared appearance + install controls (both guest and real profiles).
function appearanceCard() {
  const actions = el('<div class="card stack"></div>');
  if (!isStandalone()) {
    const install = el('<button class="act block">📲 Install app</button>');
    install.onclick = () => doInstall();
    actions.append(install);
  }
  // Light/white is the default; dark is an opt-in toggle.
  const themeRow = el('<div class="row"><span class="grow">Dark mode</span></div>');
  const themeSwitch = el('<label class="switch" title="Toggle dark mode"></label>');
  const themeCb = el('<input type="checkbox">');
  themeCb.checked = localStorage.getItem('ai_theme') === 'dark';
  themeSwitch.append(themeCb, el('<span class="slider"></span>'));
  themeCb.onchange = () => {
    if (themeCb.checked) { localStorage.setItem('ai_theme', 'dark'); document.documentElement.dataset.theme = 'dark'; }
    else { localStorage.setItem('ai_theme', 'light'); delete document.documentElement.dataset.theme; }
  };
  themeRow.append(themeSwitch);
  actions.append(themeRow);
  return actions;
}

// GUEST: the auto handle (renameable) + a prominent "save your service" convert
// card. No sign-out (a guest signing out would just mint another guest).
function guestMe(me) {
  const summary = el('<div class="card stack"></div>');
  const head = el('<div class="row"></div>');
  head.append(avatarEl(me, true));
  head.append(el(
    `<div class="grow"><h1 style="margin:0">${esc(me.display_name)}</h1>` +
    '<p class="muted" style="margin:.2rem 0 0">You’re browsing as a guest</p></div>',
  ));
  summary.append(head);
  summary.append(el('<a class="act ghost block" href="#/u/' + esc(me.id) + '">View public profile</a>'));

  // Rename the auto handle (PATCH /me) — guests and real users alike may rename.
  summary.append(addForm({
    fields: [{
      name: 'display_name', label: 'Your name', value: me.display_name || '',
      validate: (v) => {
        const c = [...v.trim()].length;
        if (c < 1) return 'Name is required.';
        if (c > 60) return 'At most 60 characters.';
        return null;
      },
    }],
    submit: 'Rename',
    onSubmit: async (body) => {
      const updated = await api('/me', { method: 'PATCH', body });
      setSession(getToken(), updated);
      toast('Name updated');
      await refreshMe();
      refresh();
    },
  }));

  // The convert card — the heart of the guest Me screen.
  const save = el('<div class="card stack"></div>');
  save.append(el('<h2 style="margin:0">Create an account to save your service</h2>'));
  save.append(el('<p class="muted" style="margin:0">Your logs, cheers and handle come with you. Already have an account? We’ll sign you in and bring them along.</p>'));
  save.append(convertForm({ onSuccess: () => { toast('Account saved 🎉'); refresh(); } }));

  const root = el('<div class="stack"></div>');
  root.append(summary, socialSection(me), save, appearanceCard(), myLogSection());
  mount(root);
}

// REAL account: today's profile + edit + sign out (unchanged behaviour).
function realMe(me) {
  const summary = el('<div class="card stack"></div>');
  const head = el('<div class="row"></div>');
  head.append(avatarEl(me, true));
  head.append(el(
    `<div class="grow"><h1 style="margin:0">${esc(me.display_name)}</h1>` +
    `<p class="muted" style="margin:.2rem 0 0">${esc(me.email)} · only you can see this</p></div>`,
  ));
  summary.append(head);
  summary.append(el(
    `<div class="row"><span class="grow">Balance</span><strong>🪙 ${esc(me.balance)}</strong></div>`,
  ));
  summary.append(el(`<a class="act ghost block" href="#/u/${esc(me.id)}">View public profile</a>`));

  const editCard = el('<div class="card"></div>');
  editCard.append(addForm({
    title: 'Edit profile',
    fields: [
      { name: 'display_name', label: 'Display name', value: me.display_name || '' },
      { name: 'bio', label: 'Bio', type: 'textarea', rows: 4, value: me.bio || '', placeholder: 'Tell people a little about yourself', allowClear: true },
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

  const actions = appearanceCard();
  const out = el('<button class="act del block">Sign out</button>');
  out.onclick = async () => {
    out.disabled = true;
    try { await api('/auth/logout', { method: 'POST' }); } catch { /* sign out locally regardless */ }
    clearSession();
    clearReturn();   // a deliberate sign-out ends wherever we were headed
    // Stay "always signed in": drop back to a fresh guest, then to the convert
    // screen so the just-signed-out user can sign back in (SERVICE_LOG.md §4/C5).
    try { const d = await api('/auth/guest', { method: 'POST' }); setSession(d.token, d.user); } catch { /* offline */ }
    await refreshMe();
    location.hash = '#/login';
  };
  actions.append(out);

  const root = el('<div class="stack"></div>');
  root.append(summary, socialSection(me), editCard, actions, myLogSection());
  mount(root);
}
