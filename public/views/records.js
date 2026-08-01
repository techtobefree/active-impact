// Service records — one logged act of service, belonging to an EVENT (FEED.md).
//   #/log · #/log/:eventId   logView    — photo + caption + which event
//   #/r/:id                  recordView — a single record (share target / deep link)
// The FEED itself is the projects list (views/projects.js): this module owns the
// record CARD, which that list, the project page and the event page all embed.
// Public UGC: every author-supplied string is escaped (esc) or set via textContent.
import { api, apiBlobURL, currentUser } from '../api.js';
import {
  el, esc, mount, clear, spinner, emptyState, avatarEl,
  toast, toastErr, errMessage, fmtDate, fmtDateTime, resizeImage, getPosition,
} from '../ui.js';

const PAGE = 20; // feed page size (server caps limit at 100)

// ---- shared bits ------------------------------------------------------------

function photoEl(imageId, { link } = {}) {
  const im = el('<img class="record-photo" alt="Service photo" />');
  apiBlobURL(`/images/${imageId}`).then((u) => { im.src = u; }).catch(() => {});
  if (!link) return im;
  const a = el(`<a href="${esc(link)}" class="record-photo-link"></a>`);
  a.append(im);
  return a;
}

// A short relative time ("3m ago"), falling back to a local date for older logs.
export function timeAgo(iso) {
  const t = new Date(iso).getTime();
  if (isNaN(t)) return '';
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 45) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return fmtDate(iso);
}

// The 🙌 cheer toggle: optimistic flip, then reconcile with the server count.
function cheerButton(rec, { compact = false } = {}) {
  const btn = el(`<button class="act cheer${compact ? ' small' : ''}" aria-label="Cheer"></button>`);
  const paint = () => {
    btn.classList.toggle('primary', !!rec.i_cheered);
    btn.innerHTML = `🙌 <span class="cheer-count">${esc(rec.cheer_count)}</span>`;
  };
  paint();
  btn.onclick = async (e) => {
    e.preventDefault(); e.stopPropagation();
    const wasCheered = rec.i_cheered;
    const wasCount = rec.cheer_count;
    rec.i_cheered = !wasCheered;                    // optimistic
    rec.cheer_count = wasCount + (rec.i_cheered ? 1 : -1);
    paint();
    try {
      const r = await api(`/service_records/${rec.id}/cheer`, { method: rec.i_cheered ? 'POST' : 'DELETE' });
      rec.i_cheered = r.cheered;                    // reconcile
      rec.cheer_count = r.cheer_count;
      paint();
    } catch (err) {
      rec.i_cheered = wasCheered;                   // revert
      rec.cheer_count = wasCount;
      paint();
      toastErr(err);
    }
  };
  return btn;
}

// The "⋯" overflow: Report (anyone) + Delete (author only). `onDeleted(card)`
// runs after a successful delete so the caller can remove the node or navigate.
function recordMenu(rec, card, onDeleted) {
  const me = currentUser();
  const mine = !!(me && rec.author && me.id === rec.author.id);

  const wrap = el('<div class="menu-wrap"></div>');
  const btn = el('<button class="act ghost menu-btn" aria-label="More">⋯</button>');
  const menu = el('<div class="menu hidden"></div>');

  const report = el('<button class="menu-item">🚩 Report</button>');
  report.onclick = async (e) => {
    e.stopPropagation(); menu.classList.add('hidden');
    try {
      await api(`/service_records/${rec.id}/report`, { method: 'POST', body: {} });
      toast('Reported — thanks for keeping the feed kind.');
    } catch (err) { toastErr(err); }
  };
  menu.append(report);

  if (mine) {
    const del = el('<button class="menu-item del">🗑 Delete</button>');
    del.onclick = async (e) => {
      e.stopPropagation(); menu.classList.add('hidden');
      if (!confirm('Delete this record? This cannot be undone.')) return;
      try {
        await api(`/service_records/${rec.id}`, { method: 'DELETE' });
        toast('Record deleted');
        if (onDeleted) onDeleted(card); else card.remove();
      } catch (err) { toastErr(err); }
    };
    menu.append(del);
  }

  btn.onclick = (e) => {
    e.preventDefault(); e.stopPropagation();
    const opening = menu.classList.contains('hidden');
    document.querySelectorAll('.menu').forEach((m) => m.classList.add('hidden'));
    if (opening) {
      menu.classList.remove('hidden');
      // Close on the next outside click (deferred so this click doesn't trip it).
      setTimeout(() => document.addEventListener('click', function h() {
        menu.classList.add('hidden'); document.removeEventListener('click', h);
      }, { once: true }), 0);
    }
  };
  wrap.append(btn, menu);
  return wrap;
}

// One record card. `linkDetail` makes the photo tap through to #/r/:id.
// `onDeleted` overrides the default (remove the card) — detail navigates home.
// `showEvent` adds the "at <project>" line (off inside an event's own feed,
// where every record is by definition from that event).
export function recordCard(rec, { linkDetail = false, onDeleted, showEvent = true } = {}) {
  const card = el('<article class="card record"></article>');
  const a = rec.author || {};

  if (rec.photo_image_id != null) {
    card.append(photoEl(rec.photo_image_id, { link: linkDetail ? `#/r/${rec.id}` : null }));
  }

  const head = el('<div class="row"></div>');
  head.append(avatarEl(a));
  head.append(el(
    `<div class="grow"><a class="record-author" href="#/u/${esc(a.id)}">${esc(a.display_name)}</a>` +
    `<div class="small muted">${esc(timeAgo(rec.created_at))}</div></div>`,
  ));
  head.append(recordMenu(rec, card, onDeleted));
  card.append(head);

  const cap = el('<p class="record-caption"></p>');
  cap.textContent = rec.caption; // author text — never interpolated as HTML
  card.append(cap);

  if (showEvent && rec.event) card.append(eventLine(rec.event));

  const actions = el('<div class="row"></div>');
  actions.append(cheerButton(rec));
  card.append(actions);
  return card;
}

// "🌱 at Riverside Cleanup · Sat 10:00" — the record's home, one tap away.
function eventLine(ev) {
  return el(
    `<a class="record-event small" href="#/events/${esc(ev.id)}">🌱 at ${esc(ev.project_title)}` +
    ` · ${esc(fmtDateTime(ev.starts_at))}</a>`,
  );
}

// The inline photo strip a PROJECT or EVENT card carries: the newest one or two
// records, in the SAME card (FEED.md F3 — no nested card chrome). Photo taps
// through to the record; the 🙌 acts in place.
export function recordStrip(records) {
  const rows = (records || []).filter((r) => r && r.photo_image_id != null);
  if (!rows.length) return null;
  const strip = el(`<div class="record-strip${rows.length === 1 ? ' one' : ''}"></div>`);
  for (const rec of rows) {
    const cell = el('<div class="record-mini"></div>');
    const link = el(`<a class="record-mini-photo" href="#/r/${esc(rec.id)}" aria-label="Open this log"></a>`);
    const im = el('<img alt="Service photo" />');
    apiBlobURL(`/images/${rec.photo_image_id}`).then((u) => { im.src = u; }).catch(() => {});
    link.append(im);
    cell.append(link);
    const cap = el('<div class="record-mini-cap"></div>');
    cap.textContent = rec.caption;  // author text — never HTML
    cell.append(cap);
    const foot = el('<div class="row record-mini-foot"></div>');
    foot.append(el(`<span class="small muted grow">${esc((rec.author || {}).display_name)} · ${esc(timeAgo(rec.created_at))}</span>`));
    foot.append(cheerButton(rec, { compact: true }));
    cell.append(foot);
    strip.append(cell);
  }
  return strip;
}

// A paginated list of records for one event — the event page's feed.
export function recordFeed(eventId) {
  const list = el('<div class="stack records"></div>');
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
      const rows = await api(`/service_records?event_id=${encodeURIComponent(eventId)}&limit=${PAGE}&offset=${offset}`);
      if (initial) clear(list);
      if (initial && (!rows || !rows.length)) {
        const empty = el('<div class="empty stack center"></div>');
        empty.append(el('<p>Nothing logged here yet. Be the first.</p>'));
        empty.append(el(`<a class="act primary" href="#/log/${esc(eventId)}">＋ Log a service</a>`));
        list.append(empty);
        done = true;
        return;
      }
      for (const rec of (rows || [])) list.append(recordCard(rec, { linkDetail: true, showEvent: false }));
      offset += (rows ? rows.length : 0);
      if (!rows || rows.length < PAGE) done = true;
    } catch (e) {
      if (initial) clear(list).append(emptyState(errMessage(e)));
      toastErr(e);
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

// A paginated list of MY records (the Me page's "My log"), attached or not.
export function myRecords() {
  const list = el('<div class="stack records"></div>');
  clear(list).append(spinner());
  api(`/service_records?scope=mine&limit=${PAGE}`)
    .then((rows) => {
      clear(list);
      if (!rows || !rows.length) {
        list.append(emptyState("You haven't logged any service yet."));
        return;
      }
      for (const rec of rows) list.append(recordCard(rec, { linkDetail: true }));
    })
    .catch((e) => { clear(list).append(emptyState(errMessage(e))); });
  return list;
}

// ---- where am I? ------------------------------------------------------------

const REASON_TEXT = {
  checked_in: "you're checked in here",
  participated: 'you were here',
  rsvp: "you're going",
  nearby: 'nearby',
};

function candidateLabel(c) {
  const bits = [];
  if (c.reason && REASON_TEXT[c.reason]) bits.push(REASON_TEXT[c.reason]);
  if (c.distance_km != null) bits.push(`${c.distance_km < 1 ? `${Math.round(c.distance_km * 1000)} m` : `${c.distance_km.toFixed(1)} km`} away`);
  return bits.join(' · ');
}

// The event picker: the ranked candidates plus an explicit "not at an event".
// `onPick(candidateOrNull)` fires on choice. Shared by the log screen and the
// record detail's "attach" affordance.
function eventPicker(candidates, onPick) {
  const wrap = el('<div class="card stack picker"></div>');
  wrap.append(el('<div class="section-label" style="margin:0">Which event?</div>'));
  if (!candidates.length) {
    wrap.append(el('<p class="muted small">Nothing is happening near you right now.</p>'));
  }
  for (const c of candidates) {
    const b = el('<button class="picker-item"></button>');
    b.append(el(`<div class="grow"><strong>${esc(c.project_title)}</strong>` +
      `<div class="small muted">${esc(fmtDateTime(c.starts_at))} · ${esc(c.location_text)}</div>` +
      (candidateLabel(c) ? `<div class="small muted">${esc(candidateLabel(c))}</div>` : '') +
      '</div>'));
    b.onclick = () => onPick(c);
    wrap.append(b);
  }
  const none = el('<button class="picker-item muted">Not at an event</button>');
  none.onclick = () => onPick(null);
  wrap.append(none);
  return wrap;
}

// ---- log a service ----------------------------------------------------------

export async function logView(eventId) {
  let dataB64 = null;
  let pos = null;                       // device coordinates, once we have them
  let target = null;                    // the chosen/auto-matched candidate
  let candidates = [];
  const locked = eventId != null;       // came from an event page — don't second-guess

  const intro = el(`<div class="card stack">
    <h1>Log a service</h1>
    <p class="muted">Snap a photo of an act of service and add a caption — it goes straight to the event's feed.</p>
  </div>`);

  // Photo picker: NO 'capture' attr (mobile then offers camera AND library — the
  // existing imagesStrip fix). One image, resized to JPEG via the shared pipeline.
  const picker = el('<div class="card stack"></div>');
  const preview = el('<img class="record-photo hidden" alt="Chosen photo" />');
  const pick = el('<label class="photo-pick"><span class="big">📷</span><span class="pick-label">Add a photo</span><input type="file" accept="image/*" hidden></label>');
  const fileInput = pick.querySelector('input');
  picker.append(preview, pick);

  const capWrap = el('<div class="card stack"></div>');
  capWrap.append(el('<label>Caption</label>'));
  const cap = el('<textarea maxlength="280" rows="3" placeholder="What did you do?"></textarea>');
  const counter = el('<div class="small muted">280 left</div>');
  capWrap.append(cap, counter);

  // Where this is going — shown BEFORE posting, changeable in one tap (F8).
  const targetCard = el('<div class="card stack target"></div>');
  const pickerSlot = el('<div></div>');

  const post = el('<button class="act primary block" disabled>Post</button>');
  const cancel = el('<a class="act ghost block" href="#/">Cancel</a>');

  const refreshPost = () => { post.disabled = !(dataB64 && cap.value.trim().length); };

  function paintTarget(state) {
    clear(targetCard);
    if (state === 'locating') {
      targetCard.append(el('<div class="small muted">Finding your event…</div>'));
      return;
    }
    const row = el('<div class="row" style="align-items:center; gap:.5rem"></div>');
    if (target) {
      row.append(el(`<div class="grow"><span class="small muted">Posting to</span>` +
        `<div><strong>${esc(target.project_title)}</strong></div>` +
        `<div class="small muted">${esc(fmtDateTime(target.starts_at))}${candidateLabel(target) ? ` · ${esc(candidateLabel(target))}` : ''}</div></div>`));
    } else {
      row.append(el('<div class="grow"><span class="small muted">Not linked to an event</span>' +
        '<div class="small muted">It will be saved to your own log.</div></div>'));
    }
    if (!locked) {
      const change = el(`<button class="act ghost">${target ? 'Change' : 'Choose'}</button>`);
      change.onclick = () => {
        if (pickerSlot.firstChild) { clear(pickerSlot); return; }
        clear(pickerSlot).append(eventPicker(candidates, (c) => {
          target = c;
          clear(pickerSlot);
          paintTarget();
        }));
      };
      row.append(change);
    }
    targetCard.append(row);
  }

  cap.addEventListener('input', () => {
    counter.textContent = `${280 - cap.value.length} left`;
    refreshPost();
  });

  fileInput.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      dataB64 = await resizeImage(file); // -> JPEG base64 (no data: prefix)
      preview.src = 'data:image/jpeg;base64,' + dataB64;
      preview.classList.remove('hidden');
      pick.querySelector('.pick-label').textContent = 'Change photo';
      refreshPost();
    } catch (ex) {
      if (ex && ex.message === 'bad image') toast("That file isn't a supported image — use a JPEG, PNG or WebP photo.");
      else toastErr(ex);
    }
  };

  post.onclick = async () => {
    if (post.disabled) return;
    post.disabled = true;
    const label = post.textContent;
    post.textContent = '…';
    try {
      const body = {
        caption: cap.value.trim(), content_type: 'image/jpeg', data_base64: dataB64,
      };
      if (target) body.event_id = target.event_id;
      if (pos) { body.lat = pos.lat; body.lon = pos.lon; }
      const rec = await api('/service_records', { body });
      if (rec.event) {
        toast(`Logged to ${rec.event.project_title} 🌱`);
        location.hash = `#/events/${rec.event.id}`;   // their photo, top of that feed
      } else {
        toast('Logged! 🌱');
        location.hash = `#/r/${rec.id}`;             // …with an "attach" affordance
      }
    } catch (e) {
      toastErr(e);
      post.disabled = false;
      post.textContent = label;
    }
  };

  const root = el('<div class="stack"></div>');
  root.append(intro, picker, capWrap, targetCard, pickerSlot, post, cancel);
  mount(root);
  paintTarget('locating');

  // Resolve the target in the background — the photo and caption never wait on it.
  if (locked) {
    try {
      const ev = await api('/events/' + eventId);
      target = {
        event_id: Number(eventId), project_id: (ev.project || {}).id,
        project_title: (ev.project || {}).title, starts_at: ev.starts_at,
        location_text: ev.location_text, distance_km: null, reason: null,
      };
    } catch { /* fall back to an unlinked post */ }
    paintTarget();
  } else {
    pos = await getPosition();
    try {
      const q = pos ? `?lat=${pos.lat}&lon=${pos.lon}` : '';
      const data = await api('/events/candidates' + q);
      candidates = data.candidates || [];
      target = data.match;
    } catch { /* offline: post unlinked rather than block the photo */ }
    paintTarget();
  }
}

// ---- record detail ----------------------------------------------------------

export async function recordView(id) {
  mount(spinner());
  let rec;
  try {
    rec = await api(`/service_records/${id}`);
  } catch (e) {
    const card = el('<div class="card stack center"></div>');
    const msg = (e && e.status === 404) ? "That record isn't here anymore." : errMessage(e);
    card.append(el(`<p>${esc(msg)}</p>`));
    card.append(el('<a class="act primary" href="#/">Back to the feed</a>'));
    mount(card);
    return;
  }
  const root = el('<div class="stack"></div>');
  root.append(el('<a class="small muted" href="#/">← Feed</a>'));
  root.append(recordCard(rec, { onDeleted: () => { location.hash = '#/'; } }));

  // Mine and unattached? Offer it a home (FEED.md F7/F8).
  const me = currentUser();
  if (!rec.event && me && rec.author && me.id === rec.author.id) {
    root.append(attachCard(rec));
  }
  mount(root);
}

// "This isn't linked to an event yet" + the same picker the log screen uses.
function attachCard(rec) {
  const wrap = el('<div class="stack"></div>');
  const card = el('<div class="card stack"></div>');
  card.append(el('<div class="small muted">This log isn\'t linked to an event, so it only shows on your own page.</div>'));
  const btn = el('<button class="act primary block">Attach to an event</button>');
  const slot = el('<div></div>');
  btn.onclick = async () => {
    if (slot.firstChild) { clear(slot); return; }
    btn.disabled = true;
    const pos = await getPosition();
    try {
      const q = pos ? `?lat=${pos.lat}&lon=${pos.lon}` : '';
      const data = await api('/events/candidates' + q);
      clear(slot).append(eventPicker(data.candidates || [], async (c) => {
        if (!c) { clear(slot); return; }
        try {
          await api(`/service_records/${rec.id}`, { method: 'PATCH', body: { event_id: c.event_id } });
          toast(`Added to ${c.project_title} 🌱`);
          location.hash = `#/events/${c.event_id}`;
        } catch (e) { toastErr(e); }
      }));
    } catch (e) { toastErr(e); }
    finally { btn.disabled = false; }
  };
  card.append(btn, slot);
  wrap.append(card);
  return wrap;
}
