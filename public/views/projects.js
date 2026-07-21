// Service projects: list (Upcoming/Past/Mine + search), detail (durable project
// + its events), create (project + first event), and the per-EVENT lead hub
// (QR, roster, who's-coming, close). A project has many events (occurrences);
// per-user RSVP/check-in/check-out state now lives on the embedded event.
import { api, apiBlobURL } from '../api.js';
import {
  el, esc, mount, clear, addForm, avatarEl, statusPill, emptyState, spinner,
  toast, toastErr, errMessage, fmtDateTime, fmtDuration, imagesStrip,
} from '../ui.js';
import { refresh, refreshMe } from '../app.js';

// ---- shared helpers ---------------------------------------------------------

function errNode(e) {
  return el(`<div class="empty">${esc(errMessage(e))}</div>`);
}

// A full-screen inline error with a way home (used for failed first loads).
function errScreen(e) {
  const card = el('<div class="card stack center"></div>');
  card.append(el(`<p>${esc(errMessage(e))}</p>`));
  card.append(el('<a class="act" href="#/projects">Back to projects</a>'));
  mount(card);
}

// The 📍/🗓/⏱/👥 meta block for one EVENT (occurrence).
function eventMeta(ev) {
  return el(`<div class="meta">
    <div class="tag">📍 ${esc(ev.location_text)}</div>
    <div class="tag">🗓 ${esc(fmtDateTime(ev.starts_at))}</div>
    <div class="tag">⏱ ${esc(fmtDuration(ev.expected_minutes))}</div>
    <div class="tag">👥 ${esc(ev.checked_in_count)} checked in</div>
  </div>`);
}

// A compact action node reflecting one EVENT's is_over / participation / rsvp
// state machine — shared by the feed card, the detail's event rows, and the
// event detail head. Returns ONE node. `onDone()` fires after a successful
// action so the caller can refresh in place. `stopNav` swallows the click so a
// button living inside a card's <a> never navigates into the detail.
function actionEl(evt, onDone, { stopNav = false } = {}) {
  const guard = (e) => { if (stopNav) { e.preventDefault(); e.stopPropagation(); } };

  // Over: no action — a thank-you chip (with hours) or a plain "Ended" chip.
  if (evt.is_over) {
    return el(evt.my_hours_here > 0
      ? `<span class="pill green">🎉 ${esc(evt.my_hours_here)}h</span>`
      : '<span class="pill muted">Ended</span>');
  }

  // Checked in: offer Check out (closes the participation, mints tokens).
  if (evt.my_open_participation) {
    const btn = el('<button class="act primary">Check out</button>');
    btn.onclick = async (e) => {
      guard(e);
      btn.disabled = true;
      try {
        const row = await api(`/participations/${evt.my_open_participation.id}/checkout`, { method: 'POST' });
        const n = row && row.tokens_awarded != null ? row.tokens_awarded : 0;
        toast(n > 0 ? `🎉 ＋${n} tokens` : 'Checked out — thanks!');
        onDone();
      } catch (err) { btn.disabled = false; toastErr(err); }
    };
    return btn;
  }

  // RSVP'd (not yet on site): offer Check in (self-service, event-scoped).
  if (evt.my_rsvp) {
    const btn = el('<button class="act primary">Check in</button>');
    btn.onclick = async (e) => {
      guard(e);
      btn.disabled = true;
      try { await api(`/events/${evt.id}/checkin`, { method: 'POST' }); onDone(); }
      catch (err) { btn.disabled = false; toastErr(err); }
    };
    return btn;
  }

  // Nothing yet: offer RSVP.
  const btn = el('<button class="act primary">RSVP</button>');
  btn.onclick = async (e) => {
    guard(e);
    btn.disabled = true;
    try { await api(`/events/${evt.id}/rsvp`, { method: 'POST' }); onDone(); }
    catch (err) { btn.disabled = false; toastErr(err); }
  };
  return btn;
}

// A project_card -> a tappable card node: cover on top, then a details-left /
// action-right row for the embedded event. The button acts in place (re-fetches
// GET /events/:id) so the current tab and scroll survive, and never navigates
// (stopNav) despite the <a>. A project with no listable event shows a muted note.
function projectCard(p) {
  const card = el(`<a class="card" href="#/projects/${p.id}" style="display:block"></a>`);
  // Prefer the EVENT's own cover (an event with photos shows its cover on the
  // feed); fall back to the durable project cover.
  const coverId = (p.event && p.event.cover_image_id) || p.cover_image_id;
  if (coverId) {
    const cov = el('<img class="cover" alt="">');
    apiBlobURL(`/images/${coverId}`).then((u) => { cov.src = u; }).catch(() => {});
    card.append(cov);
  }
  const body = el('<div></div>');
  card.append(body);
  const renderBody = (evt) => {
    clear(body);
    const row = el('<div class="row" style="align-items:center; gap:.75rem"></div>');
    const details = el('<div class="grow" style="min-width:0"></div>');
    details.append(el(`<h3 class="grow">${esc(p.title)}</h3>`));
    if (evt) {
      details.append(eventMeta(evt));
      row.append(details);
      const cell = el('<div class="action-cell"></div>');
      cell.append(el(statusPill(evt.status)));
      cell.append(actionEl(evt, async () => {
        await refreshMe();
        const fresh = await api('/events/' + evt.id);
        renderBody(fresh);
      }, { stopNav: true }));
      row.append(cell);
    } else {
      details.append(el('<div class="muted small">No upcoming events</div>'));
      row.append(details);
    }
    body.append(row);
  };
  renderBody(p.event);
  return card;
}

// ---- list -------------------------------------------------------------------

export async function listView() {
  let scope = 'upcoming';
  let q = '';

  const results = el('<div class="stack"></div>');

  const tabs = el('<div class="row" style="gap:.4rem"></div>');
  const tabBtns = {};
  for (const [key, txt] of [['upcoming', 'Upcoming'], ['past', 'Past'], ['mine', 'Mine']]) {
    const b = el(`<button class="act grow">${txt}</button>`);
    b.onclick = () => { if (scope === key) return; scope = key; setActive(); load(); };
    tabBtns[key] = b;
    tabs.append(b);
  }
  const setActive = () => { for (const k in tabBtns) tabBtns[k].classList.toggle('primary', k === scope); };
  setActive();

  const search = el('<input type="search" placeholder="Search projects" autocomplete="off">');
  let timer;
  search.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { q = search.value.trim(); load(); }, 250);
  };

  const newBtn = el('<a class="act primary block" href="#/projects/new">＋ New service project</a>');

  const emptyMsg = () => scope === 'mine'
    ? "You haven't joined or led any projects yet."
    : scope === 'past' ? 'No past projects yet.'
    : 'No projects yet. Post the first one.';

  async function load() {
    clear(results).append(spinner());
    let rows;
    try {
      rows = await api(`/projects?scope=${scope}${q ? `&q=${encodeURIComponent(q)}` : ''}`);
    } catch (e) {
      if (e && e.detail === 'unauthorized') throw e;
      clear(results).append(errNode(e));
      return;
    }
    clear(results);
    if (!rows.length) { results.append(emptyState(emptyMsg())); return; }
    for (const p of rows) results.append(projectCard(p));
  }

  const root = el('<div class="stack"></div>');
  root.append(newBtn, search, tabs, results);
  mount(root);
  await load();
}

// ---- create -----------------------------------------------------------------

export async function newView() {
  const banner = el('<div class="banner warn">Leaving the waiver blank uses our standard template — not legal advice. Edit it to fit your project.</div>');

  const form = addForm({
    title: 'New service project',
    submit: 'Create project',
    fields: [
      { name: 'title', label: 'Title', required: true },
      { name: 'description', label: 'Description', type: 'textarea', placeholder: 'What are you doing, and what should volunteers bring?' },
      { name: 'location_text', label: 'Location', required: true, placeholder: 'Where to meet', hint: 'The first event is created here — you can add more events later.' },
      { name: 'starts_at', label: 'Starts at', type: 'datetime-local', required: true, hint: 'When the first event begins.',
        // A finger-slip on the date wheels (wrong year / AM-PM) would create a
        // first event that never shows under "Upcoming" — flag it before submit.
        validate: (v) => (new Date(v).getTime() < Date.now() - 12 * 3600e3
          ? 'This start time is in the past — double-check the date.' : null) },
      { name: 'expected_minutes', label: 'Expected minutes', type: 'number', required: true, min: 1, step: 1, value: 120, placeholder: '120' },
      { name: 'waiver_text', label: 'Waiver', type: 'textarea', rows: 6, placeholder: 'Leave blank to use the standard template.' },
    ],
    onSubmit: async (body) => {
      if (body.starts_at) body.starts_at = new Date(body.starts_at).toISOString();
      const proj = await api('/projects', { body });
      location.hash = '#/projects/' + proj.id;
    },
  });

  const root = el('<div class="stack"></div>');
  root.append(banner, form);
  mount(root);
}

// ---- detail (the durable service project) -----------------------------------

export async function detailView(id) {
  mount(spinner());
  let p;
  try {
    p = await api('/projects/' + id);
  } catch (e) {
    if (e && e.detail === 'unauthorized') throw e;
    errScreen(e);
    return;
  }

  const root = el('<div class="stack"></div>');

  // Cover (primary image) — full-width at the top of the detail.
  if (p.primary_image_id) {
    const cov = el('<img class="cover" alt="">');
    apiBlobURL(`/images/${p.primary_image_id}`).then((u) => { cov.src = u; }).catch(() => {});
    root.append(cov);
  }

  // Images strip (leaders can add/remove/set cover).
  root.append(imagesStrip('project', id, p.image_ids, { canEdit: p.am_leader, onChange: refresh, primaryId: p.primary_image_id }));

  // Title + description.
  const head = el('<section class="card stack"></section>');
  head.append(el(`<h1>${esc(p.title)}</h1>`));
  if (p.description && p.description.trim()) {
    const body = el('<p></p>');
    body.textContent = p.description;
    head.append(body);
  }
  root.append(head);

  // Share / Follow / Invite — available to every signed-in viewer.
  const social = el('<div class="row" style="gap:.5rem"></div>');
  const shareUrl = location.origin + '/#/projects/' + id;

  const shareBtn = el('<button class="act grow">Share</button>');
  shareBtn.onclick = async () => {
    try {
      if (navigator.share) {
        await navigator.share({ title: p.title, url: shareUrl });
      } else {
        await navigator.clipboard.writeText(shareUrl);
        toast('Link copied');
      }
    } catch (e) {
      if (!(e && e.name === 'AbortError')) toastErr(e);
    }
  };

  const followBtn = el('<button class="act grow"></button>');
  const followers = el('<div class="small muted"></div>');
  const plural = (n) => `${n} follower${n === 1 ? '' : 's'}`;
  const paintFollow = () => {
    followBtn.textContent = p.is_following ? '✓ Following' : 'Follow';
    followBtn.classList.toggle('primary', !!p.is_following);
    followers.textContent = plural(p.follower_count || 0);
  };
  followBtn.onclick = async () => {
    followBtn.disabled = true;
    const method = p.is_following ? 'DELETE' : 'POST';
    try {
      const r = await api('/projects/' + id + '/follow', { method });
      p.is_following = r.is_following;
      p.follower_count = r.follower_count;
      paintFollow();
    } catch (e) { toastErr(e); }
    finally { followBtn.disabled = false; }
  };
  paintFollow();

  const inviteBtn = el('<button class="act grow">Invite</button>');
  inviteBtn.onclick = () => toast('Invites are coming soon.');

  social.append(shareBtn, followBtn, inviteBtn);
  root.append(social, followers);

  // Leader: edit the durable project (title / description / waiver).
  if (p.am_leader) {
    const bar = el('<div class="row"></div>');
    const edit = el('<button class="act grow">Edit project</button>');
    edit.onclick = () => openEdit(id, p);
    bar.append(edit);
    root.append(bar);
  }

  // Organizers (project_leaders — project-wide powers). Leaders add/remove here.
  root.append(el('<div class="section-label">Organizers</div>'));
  const orgWrap = el('<section class="card stack"></section>');
  for (const lead of p.leaders || []) {
    const row = el('<div class="row"></div>');
    row.append(avatarEl(lead));
    row.append(el(`<a class="grow" href="#/u/${esc(lead.id)}">${esc(lead.display_name)}</a>`));
    if (p.am_leader && !(p.owner && lead.id === p.owner.id)) {
      const x = el('<button class="act del" title="Remove organizer">✕</button>');
      x.onclick = async () => {
        if (!confirm(`Remove ${lead.display_name} as an organizer?`)) return;
        try {
          await api(`/projects/${id}/leaders/${encodeURIComponent(lead.id)}`, { method: 'DELETE' });
          toast('Organizer removed');
          refresh();
        } catch (e) { toastErr(e); }
      };
      row.append(x);
    }
    orgWrap.append(row);
  }
  if (p.am_leader) {
    const alForm = el('<form class="row" style="gap:.4rem"></form>');
    const alInput = el('<input class="grow" name="email" placeholder="Add organizer by email" autocomplete="off" inputmode="email" autocapitalize="none" autocorrect="off" spellcheck="false">');
    alForm.append(alInput, el('<button class="act" type="submit">Add</button>'));
    const alBtn = alForm.querySelector('button');
    alForm.onsubmit = async (e) => {
      e.preventDefault();
      const em = alInput.value.trim().toLowerCase();
      if (!em || alBtn.disabled) return;
      alBtn.disabled = true; // no double-submit race ("added" then "already a leader")
      try {
        await api(`/projects/${id}/leaders`, { body: { email: em } });
        toast('Organizer added');
        refresh();
      } catch (ex) { toastErr(ex); } finally { alBtn.disabled = false; }
    };
    orgWrap.append(alForm);
  }
  root.append(orgWrap);

  // Waiver (collapsed).
  if (p.waiver && p.waiver.text) {
    const det = el(`<details class="card"><summary>Waiver${p.waiver.version ? ` (v${esc(p.waiver.version)})` : ''}</summary></details>`);
    const wtext = el('<p class="muted small" style="white-space:pre-wrap"></p>');
    wtext.textContent = p.waiver.text;
    det.append(wtext);
    root.append(det);
  }

  // Events (occurrences): upcoming first, then past (server-ordered). Each row
  // carries its own meta + status + action; leaders get a Manage link + Add event.
  root.append(el('<div class="section-label">Events</div>'));
  if (p.am_leader) root.append(addEventControl(id));
  if (!p.events || !p.events.length) {
    root.append(emptyState('No events scheduled yet.'));
  } else {
    for (const ev of p.events) root.append(eventRow(ev, p.am_leader));
  }

  mount(root);
}

// One event row on the project detail: meta-left, status + action-right, and
// (leader) a Manage link into the event lead hub. The action refreshes just this
// row in place (re-fetch GET /events/:id) so scroll + the rest of the page survive.
function eventRow(event, amLeader) {
  const card = el('<section class="card stack"></section>');
  const render = (ev) => {
    clear(card);
    const row = el('<div class="row" style="align-items:center; gap:.75rem"></div>');
    // A small leading thumbnail when the event has its own cover photo.
    if (ev.cover_image_id) {
      const th = el('<img class="thumb" alt="">');
      apiBlobURL(`/images/${ev.cover_image_id}`).then((u) => { th.src = u; }).catch(() => {});
      row.append(th);
    }
    const left = el('<div class="grow" style="min-width:0"></div>');
    left.append(eventMeta(ev));
    if (ev.my_hours_here > 0 && !ev.is_over) {
      left.append(el(`<div class="muted small">You've logged ${esc(ev.my_hours_here)} hours here.</div>`));
    }
    row.append(left);
    const cell = el('<div class="action-cell"></div>');
    cell.append(el(statusPill(ev.status)));
    cell.append(actionEl(ev, async () => {
      await refreshMe();
      const fresh = await api('/events/' + ev.id);
      render(fresh);
    }));
    row.append(cell);
    card.append(row);
    if (amLeader) card.append(el(`<a class="act ghost block" href="#/events/${ev.id}/lead">Manage</a>`));
  };
  render(event);
  return card;
}

// Leader control: "＋ Add event" toggles a small form (schedule another
// occurrence) → POST /projects/:id/events → refresh the detail.
function addEventControl(id) {
  const wrap = el('<div></div>');
  const openBtn = el('<button class="act primary block">＋ Add event</button>');
  openBtn.onclick = () => {
    clear(wrap);
    const form = addForm({
      title: 'Add event',
      submit: 'Add event',
      fields: [
        { name: 'starts_at', label: 'Starts at', type: 'datetime-local', required: true,
          validate: (v) => (new Date(v).getTime() < Date.now() - 12 * 3600e3
            ? 'This start time is in the past — double-check the date.' : null) },
        { name: 'location_text', label: 'Location', required: true, placeholder: 'Where to meet' },
        { name: 'expected_minutes', label: 'Expected minutes', type: 'number', required: true, min: 1, step: 1, value: 120, placeholder: '120' },
      ],
      onSubmit: async (body) => {
        if (body.starts_at) body.starts_at = new Date(body.starts_at).toISOString();
        await api(`/projects/${id}/events`, { body });
        toast('Event added');
        refresh();
      },
    });
    const cancel = el('<button class="act ghost block">Cancel</button>');
    cancel.onclick = () => refresh();
    wrap.append(form, cancel);
  };
  wrap.append(openBtn);
  return wrap;
}

// Inline edit form (leaders only): the DURABLE project fields only. Schedule /
// location are per-event and edited elsewhere. A changed waiver_text versions
// server-side.
function openEdit(id, p) {
  const form = addForm({
    title: 'Edit project',
    submit: 'Save changes',
    fields: [
      { name: 'title', label: 'Title', required: true, value: p.title },
      { name: 'description', label: 'Description', type: 'textarea', value: p.description || '', allowClear: true },
      { name: 'waiver_text', label: 'Waiver', type: 'textarea', rows: 6, value: (p.waiver && p.waiver.text) || '' },
    ],
    onSubmit: async (body) => {
      await api('/projects/' + id, { method: 'PATCH', body });
      toast('Saved');
      refresh();
    },
  });
  const cancel = el('<button class="act ghost block">Cancel</button>');
  cancel.onclick = () => refresh();
  const root = el('<div class="stack"></div>');
  root.append(form, cancel);
  mount(root);
}

// ---- event detail (optional deep-link target: #/events/:id) -----------------

export async function eventDetailView(eventId) {
  mount(spinner());
  let ev;
  try {
    ev = await api('/events/' + eventId);
  } catch (e) {
    if (e && e.detail === 'unauthorized') throw e;
    errScreen(e);
    return;
  }
  const project = ev.project || {};

  const root = el('<div class="stack"></div>');
  // Prefer the event's own cover; fall back to the durable project cover.
  const coverId = ev.cover_image_id || project.cover_image_id;
  if (coverId) {
    const cov = el('<img class="cover" alt="">');
    apiBlobURL(`/images/${coverId}`).then((u) => { cov.src = u; }).catch(() => {});
    root.append(cov);
  }

  const head = el('<section class="card stack"></section>');
  const top = el('<div class="row" style="align-items:center; gap:.75rem"></div>');
  const left = el('<div class="grow" style="min-width:0"></div>');
  left.append(el(`<h1 class="grow">${esc(project.title)}</h1>`));
  left.append(eventMeta(ev));
  top.append(left);
  const cell = el('<div class="action-cell"></div>');
  cell.append(el(statusPill(ev.status)));
  cell.append(actionEl(ev, () => { refreshMe(); refresh(); }));
  top.append(cell);
  head.append(top);
  root.append(head);

  // Event photos: leaders can add/remove/set the event cover right here.
  if (ev.am_leader) {
    root.append(imagesStrip('event', eventId, ev.image_ids, { canEdit: true, onChange: refresh, primaryId: ev.cover_image_id }));
  }

  if (ev.am_leader) {
    root.append(el(`<a class="act primary block" href="#/events/${eventId}/lead">Manage event</a>`));
  }

  // Waiver (collapsed).
  if (ev.waiver && ev.waiver.text) {
    const det = el(`<details class="card"><summary>Waiver${ev.waiver.version ? ` (v${esc(ev.waiver.version)})` : ''}</summary></details>`);
    const wtext = el('<p class="muted small" style="white-space:pre-wrap"></p>');
    wtext.textContent = ev.waiver.text;
    det.append(wtext);
    root.append(det);
  }

  if (project.id != null) {
    root.append(el(`<a class="act ghost block" href="#/projects/${esc(project.id)}">View service project</a>`));
  }
  mount(root);
}

// ---- event lead hub (#/events/:id/lead) -------------------------------------

export async function leadView(eventId) {
  mount(spinner());
  let ev;
  try {
    ev = await api('/events/' + eventId);
  } catch (e) {
    if (e && e.detail === 'unauthorized') throw e;
    errScreen(e);
    return;
  }
  const project = ev.project || {};
  const pid = project.id;

  if (!ev.am_leader) {
    const c = el('<div class="card stack center"></div>');
    c.append(el('<p>Only project leaders can open the lead screen.</p>'));
    c.append(el(`<a class="act" href="#/projects/${esc(pid)}">View project</a>`));
    mount(c);
    return;
  }

  let roster = { participations: [], checked_in_count: 0 };
  try {
    roster = await api(`/events/${eventId}/roster`);
  } catch (e) {
    if (e && e.detail === 'unauthorized') throw e;
    toastErr(e);
  }

  const root = el('<div class="stack"></div>');

  root.append(el(`<div class="row"><a class="muted small grow" href="#/projects/${esc(pid)}">← Back to project</a></div>`));
  root.append(el(`<h1>${esc(project.title)}</h1>`));
  root.append(eventMeta(ev));

  // ---- QR + code ----
  const qrCard = el('<section class="card stack center"></section>');
  const qrBox = el('<div class="qr"></div>');
  const qrImg = el('<img alt="Check-in QR code">');
  apiBlobURL(`/events/${eventId}/qr.svg`)
    .then((u) => { qrImg.src = u; })
    .catch(() => { qrBox.append(el('<div class="muted">QR unavailable</div>')); });
  qrBox.append(qrImg);
  qrCard.append(qrBox);
  qrCard.append(el(`<div class="code">${esc(ev.checkin_code)}</div>`));
  qrCard.append(el(`<div class="muted small">or open <span class="code">${esc(location.host)}/#/c/${esc(ev.checkin_code)}</span></div>`));
  qrCard.append(el(`<a class="act primary block" href="#/c/${esc(ev.checkin_code)}">Check in yourself</a>`));

  const regen = el('<button class="act ghost block">Regenerate code</button>');
  regen.onclick = async () => {
    if (!confirm('Regenerate the check-in code? The current QR and code will stop working.')) return;
    try {
      await api(`/events/${eventId}/code/regenerate`, { method: 'POST' });
      toast('New code generated');
      refresh();
    } catch (e) { toastErr(e); }
  };
  qrCard.append(regen);
  root.append(qrCard);

  // ---- photos ---- (leaders add/set-cover/delete this event's photos)
  root.append(el('<div class="section-label">Photos</div>'));
  root.append(imagesStrip('event', eventId, ev.image_ids, { canEdit: true, onChange: refresh, primaryId: ev.cover_image_id }));

  // ---- roster ----
  root.append(el(`<div class="section-label">Roster · ${esc(roster.checked_in_count)} on site</div>`));
  if (!roster.participations.length) {
    root.append(emptyState('No one has checked in yet. Share the QR to get started.'));
  } else {
    for (const r of roster.participations) root.append(rosterRow(r));
  }

  // ---- who's coming (RSVPs) + per-attendee event-leader toggle ----
  const whoLabel = el("<div class=\"section-label\">Who's coming</div>");
  const whoWrap = el('<section class="card stack"></section>');
  whoWrap.append(spinner());
  root.append(whoLabel, whoWrap);

  // ---- close event ----
  if (ev.status === 'open') {
    const closeBtn = el('<button class="act del block" style="margin-top:1rem">Close event</button>');
    closeBtn.onclick = async () => {
      if (!confirm('Close this event? This checks out everyone still on site and marks it completed.')) return;
      closeBtn.disabled = true;
      try {
        await api(`/events/${eventId}/close`, { method: 'POST' });
        toast('Event closed');
        await refreshMe();
        refresh();
      } catch (e) { closeBtn.disabled = false; toastErr(e); }
    };
    root.append(closeBtn);
  }

  mount(root);
  fillWhosComing(eventId, whoWrap, whoLabel);
}

// Load the RSVP list into the organizer's "Who's coming" section (per event).
async function fillWhosComing(eventId, wrap, label) {
  let rsvps;
  try {
    rsvps = await api(`/events/${eventId}/rsvps`);
  } catch (e) {
    clear(wrap).append(errNode(e));
    return;
  }
  label.textContent = `Who's coming (${rsvps.length})`;
  clear(wrap);
  if (!rsvps.length) { wrap.append(emptyState('No RSVPs yet.')); return; }
  for (const r of rsvps) wrap.append(rsvpRow(eventId, r));
}

// One RSVP row: who, a "checked in" pill, and an event-leader toggle switch.
function rsvpRow(eventId, r) {
  const row = el('<div class="row"></div>');
  row.append(avatarEl(r.user));
  row.append(el(`<a class="grow" href="#/u/${esc(r.user.id)}">${esc(r.user.display_name)}</a>`));
  if (r.is_checked_in) row.append(el('<span class="pill green">checked in</span>'));

  const toggle = el('<label class="switch" title="Event leader"></label>');
  toggle.append(el('<span class="small muted">Leader</span>'));
  const cb = el('<input type="checkbox">');
  cb.checked = !!r.is_leader;
  toggle.append(cb, el('<span class="slider"></span>'));
  cb.onchange = async () => {
    const next = cb.checked;
    cb.disabled = true;
    try {
      await api(`/events/${eventId}/rsvps/${encodeURIComponent(r.user.id)}/leader`, { body: { is_leader: next } });
      r.is_leader = next;
    } catch (e) { cb.checked = !next; toastErr(e); }
    finally { cb.disabled = false; }
  };
  row.append(toggle);
  return row;
}

// One roster row: who, times, and a per-row Check out while still on site.
function rosterRow(r) {
  const row = el('<div class="card row" style="align-items:flex-start"></div>');
  row.append(avatarEl(r.user));
  const mid = el('<div class="grow"></div>');
  mid.append(el(`<a href="#/u/${esc(r.user.id)}">${esc(r.user.display_name)}</a>`));
  mid.append(el(`<div class="muted small">In ${esc(fmtDateTime(r.checked_in_at))}</div>`));
  if (r.checked_out_at) {
    mid.append(el(`<div class="muted small">Out ${esc(fmtDateTime(r.checked_out_at))} · ${esc(fmtDuration(r.minutes))} · ＋${esc(r.tokens_awarded)} 🪙</div>`));
  } else {
    mid.append(el('<div class="small" style="color:var(--green)">● on site</div>'));
  }
  row.append(mid);
  if (!r.checked_out_at) {
    const co = el('<button class="act">Check out</button>');
    co.onclick = async () => {
      co.disabled = true;
      try {
        const out = await api(`/participations/${r.id}/checkout`, { method: 'POST' });
        toast(`🎉 ＋${out && out.tokens_awarded != null ? out.tokens_awarded : 0} tokens`);
        await refreshMe();
        refresh();
      } catch (e) { co.disabled = false; toastErr(e); }
    };
    row.append(co);
  }
  return row;
}
