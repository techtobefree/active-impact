// UI helpers: DOM building, escaping, formatting, widgets, images, toast, install.
import { api, apiBlobURL } from './api.js';

// ---- DOM ----
export function el(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}
// Escape user content for safe interpolation into template literals (public UGC!).
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
export function clear(node) { node.replaceChildren(); return node; }
export function mount(...nodes) { const v = document.getElementById('view'); clear(v); v.append(...nodes.filter(Boolean)); return v; }

// ---- formatting ----
export function fmtDateTime(iso) {
  const d = new Date(iso);
  return isNaN(d) ? '' : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}
export function fmtDate(iso) {
  const d = new Date(iso);
  return isNaN(d) ? '' : d.toLocaleDateString([], { dateStyle: 'medium' });
}
export function fmtDuration(mins) {
  if (mins == null) return '';
  const h = Math.floor(mins / 60), m = mins % 60;
  return h ? (m ? `${h}h ${m}m` : `${h}h`) : `${m}m`;
}

// ---- avatar (deterministic color from user id) ----
const AV_COLORS = ['#2e7d5b', '#b8860b', '#3b6ea5', '#8e5aa8', '#b4452f', '#2a8a8a', '#a1662f'];
export function avatarEl(user, big = false) {
  const name = (user && user.display_name) || '?';
  const key = (user && user.id != null) ? String(user.id) : name;
  let h = 0; for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const color = AV_COLORS[h % AV_COLORS.length];
  const initials = name.trim().split(/\s+/).slice(0, 2).map((w) => [...w][0]).join('').toUpperCase();
  const a = el(`<div class="avatar${big ? ' lg' : ''}">${esc(initials)}</div>`);
  a.style.background = color;
  return a;
}

// ---- status pills ----
const PILL = {
  open: 'green', active: 'green', completed: 'muted', closed: 'red',
  redeemed: 'green',
  // Pre-T11 claim states. Nothing writes these any more; old rows still render.
  pending: 'amber', accepted: 'green', declined: 'red', canceled: 'muted',
};
export function statusPill(status) {
  return `<span class="pill ${PILL[status] || 'muted'}">${esc(status)}</span>`;
}

// ---- states ----
export function emptyState(msg) { return el(`<div class="empty">${esc(msg)}</div>`); }
export function spinner() { return el('<div class="spinner">Loading…</div>'); }

// ---- error messages ----
const ERRORS = {
  offline: "You're offline — check your connection.",
  unauthorized: 'Your session expired — sign in again.',
  email_taken: 'An account with this email already exists — sign in instead.',
  invalid_credentials: 'Wrong email or password.',
  auth_required: 'Please sign in.',
  invalid_token: 'Your session expired — sign in again.',
  insufficient_balance: 'Not enough tokens.',
  cannot_tip_self: "You can't tip yourself.",
  user_not_found: 'No account with that email.',
  not_found: 'Not found.',
  invalid_code: 'That check-in code is invalid or the project has ended.',
  invalid_qr: "That code didn't work — ask them to open their code again.",
  self_scan: "That's your own code — scan somebody else's.",
  not_attending: 'RSVP or check in first, then you can show your code.',
  event_over: 'This event has ended.',
  already_checked_in: "You're already checked in here.",
  already_checked_out: 'Already checked out.',
  not_allowed: "You can't do that.",
  not_a_leader: 'Only project leaders can do that.',
  not_yours: "That's not yours to change.",
  project_not_open: 'This project is no longer open.',
  project_over: 'This event has ended.',
  already_leader: 'Already a leader.',
  cannot_remove_owner: "The owner can't be removed.",
  not_claimable: "This can't be claimed.",
  own_item: "That's your own listing.",
  item_closed: 'This listing is closed.',
  already_claimed: 'You already have a pending claim here.',
  claim_not_pending: 'This claim was already decided.',
  quantity_exhausted: 'Sold out.',
  price_required: 'Offers need a token price (0 for free).',
  price_on_need: "Needs don't have a price.",
  image_too_large: 'Image is too large (max 10 MB).',
  bad_content_type: 'Only JPEG, PNG or WebP images.',
  rate_limited: 'You’re posting a bit fast — take a short break and try again.',
  not_a_guest: 'You already have an account.',
};
export function errMessage(e) {
  if (e && e.offline) return ERRORS.offline;
  const d = e && e.detail;
  // FastAPI 422 returns detail as an array of field errors — surface the first
  // one's message (e.g. "password must be 8-72 characters") instead of a generic.
  if (Array.isArray(d)) {
    const msg = d[0] && d[0].msg ? String(d[0].msg).replace(/^value error,\s*/i, '') : '';
    return msg ? msg.charAt(0).toUpperCase() + msg.slice(1) : 'Please check the form and try again.';
  }
  return (d && ERRORS[d]) || 'Something went wrong. Please try again.';
}

// ---- toast ----
let toastTimer;
export function toast(msg) {
  document.getElementById('toast')?.remove();
  const t = el(`<div id="toast">${esc(msg)}</div>`);
  document.body.append(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), 2600);
}
export function toastErr(e) { toast(errMessage(e)); }

// ---- generic add/edit form ----
// fields: [{name,label,type,required,value,options,placeholder,rows,hint,
//           validate: v => errorString|null   (client-side, runs on blur + submit),
//           transform: v => v                 (live input normalization),
//           attrs: {k: v}}]
// Errors are FIELD-ATTRIBUTED: shown under the exact field, red border, focused.
// Server errors map back to fields too (422 loc, and known detail codes below).

// Type-ahead on a text field, via a native <datalist>: the browser owns the
// dropdown, the keyboard handling and the mobile behaviour, and a browser
// without it just shows a plain input (LOCATIONS.md §5). `fetcher(q)` returns an
// array of strings. Suggestions are a nicety — a failure never blocks typing.
let suggestSeq = 0;
function attachSuggest(input, fetcher, wrap) {
  const id = 'suggest-' + (++suggestSeq);
  const list = el(`<datalist id="${id}"></datalist>`);
  input.setAttribute('list', id);
  input.setAttribute('autocomplete', 'off'); // ours, not the browser's saved values
  wrap.append(list);

  let seq = 0;
  let timer;
  const paint = async () => {
    const mine = ++seq;
    try {
      const rows = await fetcher(input.value.trim());
      if (mine !== seq) return;            // a newer keystroke already won
      list.replaceChildren(...(rows || []).map((r) => el(`<option value="${esc(r)}"></option>`)));
    } catch { /* offline, or nothing to suggest — leave the field alone */ }
  };
  // Focus fills it before a single keystroke, so the venues already in use are
  // one tap away.
  input.addEventListener('focus', paint, { once: false });
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(paint, 250); });
}

// Which known server error codes belong to which form field.
const FIELD_FOR_CODE = {
  email_taken: 'email',
  user_not_found: 'to_email',
  cannot_tip_self: 'to_email',
  insufficient_balance: 'amount',
  price_required: 'price_tokens',
  price_on_need: 'price_tokens',
};

function cleanServerMsg(msg) {
  const m = String(msg || '').replace(/^value error,\s*/i, '');
  return m ? m.charAt(0).toUpperCase() + m.slice(1) : '';
}

export function addForm({ title, fields, submit = 'Save', onSubmit }) {
  const form = el(`<form class="add stack">${title ? `<h2>${esc(title)}</h2>` : ''}</form>`);
  const reg = {}; // name -> {input, msg, f}

  for (const f of fields) {
    const wrap = el('<div></div>');
    wrap.append(el(`<label>${esc(f.label)}</label>`));
    let input;
    if (f.type === 'textarea') {
      input = el(`<textarea name="${esc(f.name)}" rows="${f.rows || 4}"${f.required ? ' required' : ''} placeholder="${esc(f.placeholder || '')}"></textarea>`);
      if (f.value != null) input.value = f.value;
    } else if (f.type === 'select') {
      input = el(`<select name="${esc(f.name)}"></select>`);
      input.innerHTML = (f.options || []).map((o) =>
        `<option value="${esc(o.value)}"${String(o.value) === String(f.value) ? ' selected' : ''}>${esc(o.text)}</option>`).join('');
    } else {
      input = el(`<input name="${esc(f.name)}" type="${f.type || 'text'}"${f.required ? ' required' : ''} placeholder="${esc(f.placeholder || '')}" />`);
      if (f.value != null) input.value = f.value;
      if (f.min != null) input.min = f.min;
      if (f.step != null) input.step = f.step;
    }
    for (const [k, v] of Object.entries(f.attrs || {})) input.setAttribute(k, v);
    const msg = el('<div class="field-msg hidden"></div>');
    wrap.append(input);
    if (f.suggest) attachSuggest(input, f.suggest, wrap);
    if (f.hint) wrap.append(el(`<div class="small muted" style="margin-top:.25rem">${esc(f.hint)}</div>`));
    wrap.append(msg);
    form.append(wrap);
    reg[f.name] = { input, msg, f };

    input.addEventListener('input', () => {
      if (f.transform) {
        const t = f.transform(input.value);
        if (t !== input.value) {
          // Preserve the caret: a mid-field rewrite must not snap it to the end.
          const pos = input.selectionStart;
          const delta = t.length - input.value.length;
          input.value = t;
          if (pos != null && input.setSelectionRange) {
            const p = Math.max(0, pos + delta);
            input.setSelectionRange(p, p);
          }
        }
      }
      input.classList.remove('invalid');
      msg.classList.add('hidden');
    });
    if (f.validate) {
      input.addEventListener('blur', () => {
        if (input.value !== '') checkField(f.name); // live feedback once they leave the field
      });
    }
  }

  function showFieldError(name, text) {
    const r = reg[name];
    if (!r) return false;
    r.input.classList.add('invalid');
    r.msg.textContent = text;
    r.msg.classList.remove('hidden');
    return true;
  }
  function checkField(name) {
    const r = reg[name];
    const bad = r.f.validate ? r.f.validate(r.input.value) : null;
    if (bad) showFieldError(name, bad);
    return !bad;
  }
  // Map a server error onto its field; false -> caller shows the general line.
  function applyServerError(ex) {
    const d = ex && ex.detail;
    if (Array.isArray(d)) { // FastAPI 422: [{loc: ["body", field], msg}]
      let first = null;
      for (const item of d) {
        const name = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : null;
        if (name && reg[name] && showFieldError(name, cleanServerMsg(item.msg)) && !first) first = name;
      }
      if (first) reg[first].input.focus();
      return !!first;
    }
    const name = FIELD_FOR_CODE[d];
    if (name && reg[name]) {
      showFieldError(name, errMessage(ex));
      reg[name].input.focus();
      return true;
    }
    return false;
  }

  const err = el('<div class="field-error hidden"></div>');
  const btn = el(`<button type="submit" class="act primary block">${esc(submit)}</button>`);
  form.append(err, btn);
  form.onsubmit = async (e) => {
    e.preventDefault();
    err.classList.add('hidden');

    // Normalize first (covers autofill, which sets values WITHOUT input events —
    // an autofilled "Jordan_Kay" must pass, not fail on a technicality)...
    for (const f of fields) {
      const input = form.elements[f.name];
      if (f.transform && input.value !== '') input.value = f.transform(input.value);
    }
    // ...then validate — bad input never even reaches the server.
    let firstBad = null;
    for (const f of fields) {
      if (f.validate && form.elements[f.name].value !== '' && !checkField(f.name) && !firstBad) firstBad = f.name;
    }
    if (firstBad) { reg[firstBad].input.focus(); return; }

    const body = {};
    for (const f of fields) {
      const v = form.elements[f.name].value;
      if (v !== '') body[f.name] = f.type === 'number' ? Number(v) : v;
      // Deliberately cleared a previously-filled field? That's an edit too —
      // send the empty value (opt-in per field, e.g. bio/description).
      else if (f.allowClear && f.value != null && f.value !== '') body[f.name] = '';
    }
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = '…';         // visible progress on slow connections
    try {
      await onSubmit(body);
    } catch (ex) {
      if (!applyServerError(ex)) {
        err.textContent = errMessage(ex);
        err.classList.remove('hidden');
      }
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  };
  return form;
}

// ---- where am I? ----
// Best-effort device position, used to work out which event a logged service
// belongs to and to pin an event's own coordinates (FEED.md §4/F5). NEVER blocks
// or nags: a denial, a timeout, or a browser without geolocation all resolve to
// null, and the check-in / RSVP signals carry the match instead.
export function getPosition({ timeout = 8000 } = {}) {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v); } };
    setTimeout(() => done(null), timeout + 500);
    navigator.geolocation.getCurrentPosition(
      (p) => done({ lat: p.coords.latitude, lon: p.coords.longitude }),
      () => done(null),
      { enableHighAccuracy: true, timeout, maximumAge: 60_000 },
    );
  });
}

// "📍 Use my location" — pins coordinates onto an event being created or edited,
// so photos logged there can find it by distance. onSet({lat, lon}) on success.
export function locationControl(onSet, { pinned = false } = {}) {
  const wrap = el('<div class="stack"></div>');
  const btn = el(`<button type="button" class="act ghost block">${pinned ? '📍 Location pinned — update' : '📍 Use my location'}</button>`);
  const note = el(`<div class="small muted">${pinned
    ? 'Photos logged nearby will attach to this event automatically.'
    : 'Optional. Pins where this event is, so photos logged there find it.'}</div>`);
  btn.onclick = async () => {
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = 'Locating…';
    const pos = await getPosition();
    btn.disabled = false;
    if (!pos) {
      btn.textContent = label;
      note.textContent = "Couldn't get your location — you can still save without it.";
      return;
    }
    onSet(pos);
    btn.textContent = '📍 Location pinned ✓';
    note.textContent = 'Photos logged nearby will attach to this event automatically.';
  };
  wrap.append(btn, note);
  return wrap;
}

// ---- images ----
// Resize an image file to <=1600px JPEG (q0.8) and return base64 (no data: prefix).
export function resizeImage(file, maxDim = 1600, quality = 0.8) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
      const w = Math.round(img.width * scale), h = Math.round(img.height * scale);
      const canvas = document.createElement('canvas');
      canvas.width = w; canvas.height = h;
      canvas.getContext('2d').drawImage(img, 0, 0, w, h);
      resolve(canvas.toDataURL('image/jpeg', quality).split(',')[1]);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('bad image')); };
    img.src = url;
  });
}

// A strip of an entity's images (authed blob URLs) with optional add/delete.
// primaryId (the cover) gets a ★ badge; non-primary thumbs get "Make primary".
export function imagesStrip(entity, entityId, imageIds, { canEdit = false, onChange, primaryId = null } = {}) {
  const strip = el('<div class="strip"></div>');
  for (const id of imageIds || []) {
    const isPrimary = primaryId != null && id === primaryId;
    const im = el('<img class="thumb" alt="photo" />');
    apiBlobURL(`/images/${id}`).then((u) => { im.src = u; }).catch(() => {});
    if (canEdit || isPrimary) {
      const wrap = el('<div class="thumb-wrap"></div>');
      wrap.append(im);
      if (isPrimary) wrap.append(el('<span class="primary-badge" title="Cover photo">★</span>'));
      if (canEdit) {
        const x = el('<button class="thumb-btn thumb-del" title="Remove photo">✕</button>');
        x.onclick = async () => {
          if (!confirm('Remove this photo?')) return; // destructive, like every other confirm
          try { await api(`/images/${id}`, { method: 'DELETE' }); onChange && onChange(); }
          catch (e) { toastErr(e); }
        };
        wrap.append(x);
        if (!isPrimary) {
          const mk = el('<button class="thumb-btn thumb-star" title="Make this the cover photo">☆</button>');
          mk.onclick = async () => {
            try { await api(`/images/${id}/primary`, { method: 'POST' }); onChange && onChange(); }
            catch (e) { toastErr(e); }
          };
          wrap.append(mk);
        }
      }
      strip.append(wrap);
    } else {
      strip.append(im);
    }
  }
  if (canEdit) {
    // No capture attr: mobile browsers then offer BOTH camera and photo library.
    const add = el('<label class="thumb-add" title="Add photo"><span class="big">📷</span><span>Add</span><input type="file" accept="image/*" hidden></label>');
    add.querySelector('input').onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      try {
        const data = await resizeImage(file);
        await api('/images', { body: { entity, entity_id: entityId, content_type: 'image/jpeg', data_base64: data } });
        onChange && onChange();
      } catch (ex) {
        if (ex && ex.message === 'bad image') {
          toast("That file isn't a supported image — use a JPEG, PNG or WebP photo.");
        } else {
          toastErr(ex);
        }
      }
    };
    strip.append(add);
  }
  return strip;
}

// ---- PWA install ----
let deferredPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); deferredPrompt = e; });
const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
export const isStandalone = () =>
  window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
export async function doInstall() {
  if (deferredPrompt) {
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
  } else if (isIOS()) {
    alert('To install Active Impact:\n\n1. Tap the Share button\n2. Choose "Add to Home Screen".');
  } else {
    alert('To install: open your browser menu and choose "Install app" / "Add to Home Screen".');
  }
}
