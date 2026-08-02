// The social layer (SOCIAL.md): activity cards, people lists, notifications.
//   #/u/:id/followers · #/u/:id/following   the two lists (Block lives on mine)
//   #/notifications                          the bell's screen
// The Following tab on home and the activity on a person's page both render
// `activityCard` from here, so one action looks the same everywhere it appears.
import { api, currentUser } from '../api.js';
import {
  el, esc, mount, clear, spinner, emptyState, avatarEl,
  toast, toastErr, errMessage, fmtDateTime,
} from '../ui.js';
import { recordCard, timeAgo } from './records.js';

const PAGE = 20;

// What each kind reads as. The verb carries the meaning; the event carries the
// link — "checked in at Riverside Park Cleanup".
const VERB = {
  rsvp: 'is going to',
  checked_in: 'checked in at',
  logged: 'logged a service at',
  created_project: 'started',
  scheduled_event: 'scheduled a new event at',
};
const MARK = {
  checked_in: ['green', '📍 here'],
  rsvp: ['muted', '🗓 going'],
  created_project: ['green', '🌱 new'],
  scheduled_event: ['muted', '🗓 added'],
};

// One activity -> a card. A `logged` activity IS its record card (the photo is
// the point, and that card already names its author, time and event), so the
// feed never wraps a card in a card.
export function activityCard(a) {
  if (a.kind === 'logged' && a.record) return recordCard(a.record, { linkDetail: true });

  const card = el('<article class="card activity"></article>');
  const row = el('<div class="row"></div>');
  row.append(avatarEl(a.actor));
  const ev = a.event;
  const what = ev
    ? `<a href="#/events/${esc(ev.id)}">${esc(ev.project_title)}</a>`
    : 'a service project';
  const body = el(
    `<div class="grow"><div><a class="record-author" href="#/u/${esc(a.actor.id)}">${esc(a.actor.display_name)}</a> ` +
    `<span class="muted">${esc(VERB[a.kind] || 'did something at')}</span> ${what}</div>` +
    `<div class="small muted">${esc(timeAgo(a.created_at))}${ev ? ` · ${esc(fmtDateTime(ev.starts_at))}` : ''}</div></div>`,
  );
  row.append(body);
  const [tone, label] = MARK[a.kind] || ['muted', ''];
  if (label) row.append(el(`<span class="pill ${tone}">${label}</span>`));
  card.append(row);
  return card;
}

// A paginated activity list from any endpoint. Returns the node immediately and
// fills it in — a view must only await what it needs in order to mount
// (issues/STALE_VIEW_RACE.md).
export function activityFeed(path, { empty = 'Nothing here yet.', pick } = {}) {
  const list = el('<div class="stack"></div>');
  const moreWrap = el('<div class="center hidden" style="margin-top:.5rem"></div>');
  const moreBtn = el('<button class="act">Load more</button>');
  moreWrap.append(moreBtn);
  let offset = 0, loading = false, done = false;

  async function load(initial) {
    if (loading || done) return;
    loading = true;
    if (initial) clear(list).append(spinner());
    else { moreBtn.disabled = true; moreBtn.textContent = '…'; }
    try {
      const sep = path.includes('?') ? '&' : '?';
      const data = await api(`${path}${sep}limit=${PAGE}&offset=${offset}`);
      const rows = pick ? pick(data) : data;
      if (initial) clear(list);
      if (initial && (!rows || !rows.length)) { list.append(emptyState(empty)); done = true; return; }
      for (const a of (rows || [])) list.append(activityCard(a));
      offset += (rows ? rows.length : 0);
      if (!rows || rows.length < PAGE) done = true;
    } catch (e) {
      if (initial) clear(list).append(emptyState(errMessage(e)));
      else toastErr(e);
    } finally {
      loading = false;
      moreBtn.disabled = false;
      moreBtn.textContent = 'Load more';
      moreWrap.classList.toggle('hidden', done);
    }
  }
  moreBtn.onclick = () => load(false);

  const root = el('<div class="stack"></div>');
  root.append(list, moreWrap);
  load(true);
  return root;
}

// What this person is doing now and next (SOCIAL.md §5). Sits directly under the
// buttons on their page, because it is the CURRENT information — the history
// belongs below it, behind its own divider.
export function upcomingSection(userId) {
  const wrap = el('<div class="stack"></div>');
  api(`/users/${encodeURIComponent(userId)}/upcoming`)
    .then((rows) => {
      if (!rows || !rows.length || !wrap.isConnected) return;
      wrap.append(el('<div class="section-label">Now &amp; next</div>'));
      for (const r of rows) {
        const card = el(`<a class="card row" href="#/events/${esc(r.event_id)}"></a>`);
        card.append(el(
          `<div class="grow"><strong>${esc(r.project_title)}</strong>` +
          `<div class="small muted">${esc(fmtDateTime(r.starts_at))} · ${esc(r.location_text)}</div></div>`,
        ));
        card.append(el(r.is_here_now
          ? '<span class="pill green">📍 here now</span>'
          : '<span class="pill muted">🗓 going</span>'));
        wrap.append(card);
      }
    })
    .catch(() => { /* their plans are a bonus — never break the page for them */ });
  return wrap;
}

// ---- people lists -----------------------------------------------------------

// One person: avatar, name, and (on MY followers list) the Block control — the
// screen the founder asked for it on. Blocking deliberately keeps the follow, so
// the row stays exactly where it is and only the button changes.
function personRow(p, { canBlock = false } = {}) {
  const row = el('<div class="card row"></div>');
  row.append(avatarEl(p));
  row.append(el(
    `<a class="grow record-author" href="#/u/${esc(p.id)}">${esc(p.display_name)}</a>`,
  ));
  if (canBlock) {
    const btn = el('<button class="act"></button>');
    const paint = () => {
      btn.textContent = p.is_blocked ? 'Unblock' : 'Block';
      btn.classList.toggle('del', !p.is_blocked);
    };
    paint();
    btn.onclick = async () => {
      if (!p.is_blocked && !confirm(`Block ${p.display_name}? They stay a follower but stop seeing what you do.`)) return;
      btn.disabled = true;
      try {
        const r = await api(`/users/${p.id}/block`, { method: p.is_blocked ? 'DELETE' : 'POST' });
        p.is_blocked = r.is_blocked;
        paint();
        toast(p.is_blocked ? `${p.display_name} can no longer see your activity` : `${p.display_name} can see your activity again`);
      } catch (e) { toastErr(e); }
      finally { btn.disabled = false; }
    };
    row.append(btn);
  }
  return row;
}

// The profile card's Followers / Following, as two tabs that expand IN PLACE.
//
// Tap a tab to open its list under the card; tap the other to switch without
// closing; tap the open one again to collapse back to just the two tabs. Each
// list loads once, on first open, and shows the first 100 — past that, "See
// more" hands over to the full page, which is where sorting lives.
export function followTabs(me) {
  const LIMIT = 100;
  const card = el('<div class="card stack follow-card"></div>');
  const tabs = el('<div class="row tabs"></div>');
  const panel = el('<div class="follow-panel hidden"></div>');
  const counts = { followers: me.follower_count || 0, following: me.following_count || 0 };
  const cache = {};
  let open = null;                                  // which tab is expanded

  const btns = {};
  for (const key of ['followers', 'following']) {
    const label = key === 'followers' ? 'Followers' : 'Following';
    const b = el(`<button class="act grow"><span>${label}</span> <strong>${esc(counts[key])}</strong></button>`);
    b.onclick = () => (open === key ? collapse() : expand(key));
    btns[key] = b;
    tabs.append(b);
  }

  function paint() {
    for (const k of Object.keys(btns)) btns[k].classList.toggle('primary', open === k);
    panel.classList.toggle('hidden', open === null);
  }

  function collapse() { open = null; paint(); }

  async function expand(key) {
    open = key;
    paint();
    if (cache[key]) { render(key); return; }
    clear(panel).append(spinner());
    try {
      cache[key] = await api(`/users/${me.id}/${key}?limit=${LIMIT}`);
    } catch (e) {
      clear(panel).append(emptyState(errMessage(e)));
      return;
    }
    if (open === key) render(key);                  // they may have moved on
  }

  function render(key) {
    const rows = cache[key] || [];
    clear(panel);
    if (!rows.length) {
      panel.append(emptyState(key === 'followers'
        ? 'Nobody follows you yet.' : "You aren't following anyone yet."));
      return;
    }
    // Block belongs on my FOLLOWERS — it is about who sees me.
    for (const p of rows) panel.append(personRow(p, { canBlock: key === 'followers' }));
    if (counts[key] > rows.length) {
      panel.append(el(
        `<a class="act ghost block" href="#/u/${esc(me.id)}/${key}">` +
        `See all ${esc(counts[key])}</a>`,
      ));
    }
  }

  card.append(tabs, panel);
  return card;
}

async function peopleView(userId, which) {
  mount(spinner());
  const me = currentUser() || {};
  const mine = String(me.id) === String(userId);
  let person;
  try {
    person = await api(`/users/${encodeURIComponent(userId)}`);
  } catch (e) {
    mount(emptyState(errMessage(e)));
    return;
  }

  const root = el('<div class="stack"></div>');
  root.append(el(`<a class="small muted" href="#/u/${esc(userId)}">← ${esc(person.display_name)}</a>`));
  root.append(el(`<h1>${which === 'followers' ? 'Followers' : 'Following'}</h1>`));

  // This is the whole-list page, so it is where sorting lives.
  let sort = 'recent';
  const list = el('<div class="stack"></div>');
  const sorter = el('<div class="row tabs"></div>');
  const sortBtns = {};
  for (const [key, label] of [['recent', 'Recent'], ['name', 'Name']]) {
    const b = el(`<button class="act grow">${label}</button>`);
    b.onclick = () => { if (sort === key) return; sort = key; paintSort(); load(true); };
    sortBtns[key] = b;
    sorter.append(b);
  }
  const paintSort = () => {
    for (const k of Object.keys(sortBtns)) sortBtns[k].classList.toggle('primary', sort === k);
  };
  // (load is defined below; the sort buttons call it after repainting.)
  paintSort();

  // This page holds the WHOLE list, however long it is — the card upstairs is
  // the one that stops at 100.
  const PER = 100;
  const moreWrap = el('<div class="center hidden" style="margin-top:.5rem"></div>');
  const moreBtn = el('<button class="act">Load more</button>');
  moreWrap.append(moreBtn);

  let seq = 0;
  let offset = 0;
  async function load(reset) {
    const token = ++seq;
    if (reset) { offset = 0; clear(list).append(spinner()); }
    else { moreBtn.disabled = true; moreBtn.textContent = '…'; }
    let people;
    try {
      people = await api(
        `/users/${encodeURIComponent(userId)}/${which}?limit=${PER}&offset=${offset}&sort=${sort}`);
    } catch (e) {
      if (token === seq && reset) clear(list).append(emptyState(errMessage(e)));
      else if (token === seq) toastErr(e);
      return;
    }
    if (token !== seq) return;             // a newer sort already owns this
    if (reset) clear(list);
    if (reset && !people.length) {
      list.append(emptyState(which === 'followers'
        ? (mine ? 'Nobody follows you yet.' : 'No followers yet.')
        : 'Not following anyone yet.'));
      moreWrap.classList.add('hidden');
      return;
    }
    // Block only makes sense on MY OWN followers: it is about who sees me.
    for (const p of people) list.append(personRow(p, { canBlock: mine && which === 'followers' }));
    offset += people.length;
    moreBtn.disabled = false;
    moreBtn.textContent = 'Load more';
    moreWrap.classList.toggle('hidden', people.length < PER);
  }
  moreBtn.onclick = () => load(false);

  root.append(sorter, list, moreWrap);
  mount(root);
  load(true);
}

export const followersView = (id) => peopleView(id, 'followers');
export const followingView = (id) => peopleView(id, 'following');

// ---- notifications ----------------------------------------------------------

export async function notificationsView() {
  const root = el('<div class="stack"></div>');
  root.append(el('<h1>Notifications</h1>'));
  root.append(prefCard());
  root.append(activityFeed('/notifications', {
    empty: 'Nothing yet — follow people to hear when they RSVP or check in.',
    pick: (d) => d.items,          // this endpoint carries the count alongside
  }));
  mount(root);

  // Opening the bell IS reading it (SOCIAL.md S6): the watermark moves, the
  // items stay. Detached — the list must not wait on it.
  api('/notifications/seen', { method: 'POST' })
    .then(() => import('../app.js').then((m) => m.refreshUnread()))
    .catch(() => {});
}

// The founder's "you can ask to be notified": one switch, and opening this
// screen is what marks everything read.
function prefCard() {
  const me = currentUser() || {};
  const card = el('<div class="card row"></div>');
  card.append(el('<span class="grow">Tell me when people I follow RSVP or check in</span>'));
  const toggle = el('<label class="switch"></label>');
  const cb = el('<input type="checkbox">');
  cb.checked = me.notify_activity !== false;
  toggle.append(cb, el('<span class="slider"></span>'));
  cb.onchange = async () => {
    const next = cb.checked;
    cb.disabled = true;
    try {
      await api('/me', { method: 'PATCH', body: { notify_activity: next } });
      const { refreshMe, refreshUnread } = await import('../app.js');
      await refreshMe();
      refreshUnread();
      toast(next ? 'Notifications on' : 'Notifications off');
    } catch (e) { cb.checked = !next; toastErr(e); }
    finally { cb.disabled = false; }
  };
  card.append(toggle);
  return card;
}
