// Projects: list (Upcoming/Past/Mine + search), detail (+ checkout/edit),
// create, and the leader hub (QR, roster, leaders, close).
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
  card.append(el('<a class="act" href="#/">Back to projects</a>'));
  mount(card);
}

// ISO -> value for <input type="datetime-local"> in the viewer's local time.
function toLocalInput(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

// A project_card -> a tappable card node.
function projectCard(p) {
  const card = el(`<a class="card" href="#/projects/${p.id}" style="display:block"></a>`);
  if (p.cover_image_id) {
    const cov = el('<img class="cover" alt="">');
    apiBlobURL(`/images/${p.cover_image_id}`).then((u) => { cov.src = u; }).catch(() => {});
    card.append(cov);
  }
  card.append(el(`<div class="row" style="align-items:flex-start">
    <h3 class="grow">${esc(p.title)}</h3>${statusPill(p.status)}
  </div>`));
  card.append(el(`<div class="tag">📍 ${esc(p.location_text)}</div>`));
  card.append(el(`<div class="tag">🗓 ${esc(fmtDateTime(p.starts_at))}</div>`));
  card.append(el(`<div class="tag">⏱ ${esc(fmtDuration(p.expected_minutes))}</div>`));
  card.append(el(`<div class="tag">👥 ${esc(p.checked_in_count)} checked in</div>`));
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

  const newBtn = el('<a class="act primary block" href="#/projects/new">＋ New project</a>');

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
    title: 'New project',
    submit: 'Create project',
    fields: [
      { name: 'title', label: 'Title', required: true },
      { name: 'description', label: 'Description', type: 'textarea', placeholder: 'What are you doing, and what should volunteers bring?' },
      { name: 'location_text', label: 'Location', required: true, placeholder: 'Where to meet' },
      { name: 'starts_at', label: 'Starts at', type: 'datetime-local', required: true,
        // A finger-slip on the date wheels (wrong year / AM-PM) would create a
        // project that never shows under "Upcoming" — flag it before submit.
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

// ---- detail -----------------------------------------------------------------

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

  // Images
  root.append(imagesStrip('project', id, p.image_ids, { canEdit: p.am_leader, onChange: refresh }));

  // Title + status
  const head = el('<section class="card stack"></section>');
  head.append(el(`<div class="row" style="align-items:flex-start">
    <h1 class="grow">${esc(p.title)}</h1>${statusPill(p.status)}
  </div>`));
  head.append(el(`<div class="tag">📍 ${esc(p.location_text)}</div>`));
  head.append(el(`<div class="tag">🗓 ${esc(fmtDateTime(p.starts_at))}</div>`));
  head.append(el(`<div class="tag">⏱ ${esc(fmtDuration(p.expected_minutes))}</div>`));
  head.append(el(`<div class="tag">👥 ${esc(p.checked_in_count)} on site</div>`));
  if (p.my_hours_here > 0) {
    head.append(el(`<div class="muted small">You've logged ${esc(p.my_hours_here)} hours here.</div>`));
  }
  root.append(head);

  // Checked-in banner + self checkout
  if (p.my_open_participation) {
    const co = el('<section class="card stack"></section>');
    co.append(el(`<div class="banner info">✅ You're checked in${p.my_open_participation.checked_in_at ? ` since ${esc(fmtDateTime(p.my_open_participation.checked_in_at))}` : ''}.</div>`));
    const btn = el('<button class="act primary block">Check out</button>');
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        const row = await api(`/participations/${p.my_open_participation.id}/checkout`, { method: 'POST' });
        toast(`🎉 ＋${row && row.tokens_awarded != null ? row.tokens_awarded : 0} tokens`);
        await refreshMe();
        refresh();
      } catch (e) { btn.disabled = false; toastErr(e); }
    };
    co.append(btn);
    root.append(co);
  }

  // Leader actions
  if (p.am_leader) {
    const bar = el('<div class="row"></div>');
    bar.append(el(`<a class="act primary grow" href="#/projects/${id}/lead">Lead screen</a>`));
    const edit = el('<button class="act grow">Edit</button>');
    edit.onclick = () => openEdit(id, p);
    bar.append(edit);
    root.append(bar);
  }

  // Description
  if (p.description && p.description.trim()) {
    const desc = el('<section class="card"></section>');
    const body = el('<p></p>');
    body.textContent = p.description;
    desc.append(body);
    root.append(desc);
  }

  // Leaders
  if (p.leaders && p.leaders.length) {
    root.append(el('<div class="section-label">Leaders</div>'));
    const wrap = el('<section class="card stack"></section>');
    for (const lead of p.leaders) {
      const row = el('<div class="row"></div>');
      row.append(avatarEl(lead));
      row.append(el(`<a class="grow" href="#/u/${esc(lead.id)}">${esc(lead.display_name)}</a>`));
      wrap.append(row);
    }
    root.append(wrap);
  }

  // Waiver
  if (p.waiver && p.waiver.text) {
    const det = el(`<details class="card"><summary>Waiver${p.waiver.version ? ` (v${esc(p.waiver.version)})` : ''}</summary></details>`);
    const wtext = el('<p class="muted small" style="white-space:pre-wrap"></p>');
    wtext.textContent = p.waiver.text;
    det.append(wtext);
    root.append(det);
  }

  mount(root);
}

// Inline edit form (leaders only). PATCH; a changed waiver_text versions server-side.
function openEdit(id, p) {
  const form = addForm({
    title: 'Edit project',
    submit: 'Save changes',
    fields: [
      { name: 'title', label: 'Title', required: true, value: p.title },
      { name: 'description', label: 'Description', type: 'textarea', value: p.description || '', allowClear: true },
      { name: 'location_text', label: 'Location', required: true, value: p.location_text },
      { name: 'starts_at', label: 'Starts at', type: 'datetime-local', value: toLocalInput(p.starts_at) },
      { name: 'expected_minutes', label: 'Expected minutes', type: 'number', min: 1, step: 1, value: p.expected_minutes },
      { name: 'waiver_text', label: 'Waiver', type: 'textarea', rows: 6, value: (p.waiver && p.waiver.text) || '' },
    ],
    onSubmit: async (body) => {
      if (body.starts_at) body.starts_at = new Date(body.starts_at).toISOString();
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

// ---- lead hub ---------------------------------------------------------------

export async function leadView(id) {
  mount(spinner());
  let p;
  try {
    p = await api('/projects/' + id);
  } catch (e) {
    if (e && e.detail === 'unauthorized') throw e;
    errScreen(e);
    return;
  }

  if (!p.am_leader) {
    const c = el('<div class="card stack center"></div>');
    c.append(el('<p>Only project leaders can open the lead screen.</p>'));
    c.append(el(`<a class="act" href="#/projects/${id}">View project</a>`));
    mount(c);
    return;
  }

  let roster = { participations: [], checked_in_count: 0 };
  try {
    roster = await api(`/projects/${id}/roster`);
  } catch (e) {
    if (e && e.detail === 'unauthorized') throw e;
    toastErr(e);
  }

  const root = el('<div class="stack"></div>');

  root.append(el(`<div class="row"><a class="muted small grow" href="#/projects/${id}">← Back to project</a></div>`));
  root.append(el(`<h1>${esc(p.title)}</h1>`));

  // ---- QR + code ----
  const qrCard = el('<section class="card stack center"></section>');
  const qrBox = el('<div class="qr"></div>');
  const qrImg = el('<img alt="Check-in QR code">');
  apiBlobURL(`/projects/${id}/qr.svg`)
    .then((u) => { qrImg.src = u; })
    .catch(() => { qrBox.append(el('<div class="muted">QR unavailable</div>')); });
  qrBox.append(qrImg);
  qrCard.append(qrBox);
  qrCard.append(el(`<div class="code">${esc(p.checkin_code)}</div>`));
  qrCard.append(el(`<div class="muted small">or open <span class="code">${esc(location.host)}/#/c/${esc(p.checkin_code)}</span></div>`));
  qrCard.append(el(`<a class="act primary block" href="#/c/${esc(p.checkin_code)}">Check in yourself</a>`));

  const regen = el('<button class="act ghost block">Regenerate code</button>');
  regen.onclick = async () => {
    if (!confirm('Regenerate the check-in code? The current QR and code will stop working.')) return;
    try {
      await api(`/projects/${id}/code/regenerate`, { method: 'POST' });
      toast('New code generated');
      refresh();
    } catch (e) { toastErr(e); }
  };
  qrCard.append(regen);
  root.append(qrCard);

  // ---- roster ----
  root.append(el(`<div class="section-label">Roster · ${esc(roster.checked_in_count)} on site</div>`));
  if (!roster.participations.length) {
    root.append(emptyState('No one has checked in yet. Share the QR to get started.'));
  } else {
    for (const r of roster.participations) root.append(rosterRow(id, r));
  }

  // ---- leaders ----
  root.append(el('<div class="section-label">Leaders</div>'));
  const leadWrap = el('<section class="card stack"></section>');
  for (const lead of p.leaders || []) {
    const row = el('<div class="row"></div>');
    row.append(avatarEl(lead));
    row.append(el(`<a class="grow" href="#/u/${esc(lead.id)}">${esc(lead.display_name)}</a>`));
    if (!(p.owner && lead.id === p.owner.id)) {
      const x = el('<button class="act del" title="Remove leader">✕</button>');
      x.onclick = async () => {
        if (!confirm(`Remove ${lead.display_name} as a leader?`)) return;
        try {
          await api(`/projects/${id}/leaders/${encodeURIComponent(lead.id)}`, { method: 'DELETE' });
          toast('Leader removed');
          refresh();
        } catch (e) { toastErr(e); }
      };
      row.append(x);
    }
    leadWrap.append(row);
  }
  const alForm = el('<form class="row" style="gap:.4rem"></form>');
  const alInput = el('<input class="grow" name="email" placeholder="Add leader by email" autocomplete="off" inputmode="email" autocapitalize="none" autocorrect="off" spellcheck="false">');
  alForm.append(alInput, el('<button class="act" type="submit">Add</button>'));
  const alBtn = alForm.querySelector('button');
  alForm.onsubmit = async (e) => {
    e.preventDefault();
    const em = alInput.value.trim().toLowerCase();
    if (!em || alBtn.disabled) return;
    alBtn.disabled = true; // no double-submit race ("added" then "already a leader")
    try {
      await api(`/projects/${id}/leaders`, { body: { email: em } });
      toast('Leader added');
      refresh();
    } catch (ex) { toastErr(ex); } finally { alBtn.disabled = false; }
  };
  leadWrap.append(alForm);
  root.append(leadWrap);

  // ---- images ----
  root.append(el('<div class="section-label">Photos</div>'));
  root.append(imagesStrip('project', id, p.image_ids, { canEdit: true, onChange: refresh }));

  // ---- close ----
  if (p.status === 'open') {
    const closeBtn = el('<button class="act del block" style="margin-top:1rem">Close project</button>');
    closeBtn.onclick = async () => {
      if (!confirm('Close this project? This checks out everyone still on site and marks it completed.')) return;
      closeBtn.disabled = true;
      try {
        await api(`/projects/${id}/close`, { method: 'POST' });
        toast('Project closed');
        await refreshMe();
        refresh();
      } catch (e) { closeBtn.disabled = false; toastErr(e); }
    };
    root.append(closeBtn);
  }

  mount(root);
}

// One roster row: who, times, and a per-row Check out while still on site.
function rosterRow(id, r) {
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
