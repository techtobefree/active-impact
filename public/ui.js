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

// ---- avatar (deterministic color from username) ----
const AV_COLORS = ['#2e7d5b', '#b8860b', '#3b6ea5', '#8e5aa8', '#b4452f', '#2a8a8a', '#a1662f'];
export function avatarEl(user, big = false) {
  const name = (user && (user.display_name || user.username)) || '?';
  const key = (user && user.username) || name;
  let h = 0; for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const color = AV_COLORS[h % AV_COLORS.length];
  const initials = name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase();
  const a = el(`<div class="avatar${big ? ' lg' : ''}">${esc(initials)}</div>`);
  a.style.background = color;
  return a;
}

// ---- status pills ----
const PILL = {
  open: 'green', active: 'green', completed: 'muted', closed: 'red',
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
  username_taken: 'That username is taken.',
  invalid_credentials: 'Wrong username or password.',
  auth_required: 'Please sign in.',
  invalid_token: 'Your session expired — sign in again.',
  insufficient_balance: 'Not enough tokens.',
  cannot_tip_self: "You can't send tokens to yourself.",
  user_not_found: 'No user by that name.',
  not_found: 'Not found.',
  invalid_code: 'That check-in code is invalid or the project has ended.',
  already_checked_in: "You're already checked in here.",
  already_checked_out: 'Already checked out.',
  not_allowed: "You can't do that.",
  not_a_leader: 'Only project leaders can do that.',
  not_yours: "That's not yours to change.",
  project_not_open: 'This project is no longer open.',
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
// fields: [{name,label,type,required,value,options:[{value,text}],placeholder,rows}]
export function addForm({ title, fields, submit = 'Save', onSubmit }) {
  const form = el(`<form class="add stack">${title ? `<h2>${esc(title)}</h2>` : ''}</form>`);
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
    wrap.append(input);
    if (f.hint) wrap.append(el(`<div class="small muted" style="margin-top:.25rem">${esc(f.hint)}</div>`));
    form.append(wrap);
  }
  const err = el('<div class="field-error hidden"></div>');
  const btn = el(`<button type="submit" class="act primary block">${esc(submit)}</button>`);
  form.append(err, btn);
  form.onsubmit = async (e) => {
    e.preventDefault();
    err.classList.add('hidden');
    const body = {};
    for (const f of fields) {
      const v = form.elements[f.name].value;
      if (v !== '') body[f.name] = f.type === 'number' ? Number(v) : v;
    }
    btn.disabled = true;
    try {
      await onSubmit(body);
    } catch (ex) {
      err.textContent = errMessage(ex);
      err.classList.remove('hidden');
    } finally {
      btn.disabled = false;
    }
  };
  return form;
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
export function imagesStrip(entity, entityId, imageIds, { canEdit = false, onChange } = {}) {
  const strip = el('<div class="strip"></div>');
  for (const id of imageIds || []) {
    const im = el('<img class="thumb" alt="photo" />');
    apiBlobURL(`/images/${id}`).then((u) => { im.src = u; }).catch(() => {});
    if (canEdit) {
      const wrap = el('<div style="position:relative"></div>');
      const x = el('<button class="act del" style="position:absolute;top:2px;right:2px;padding:.1rem .4rem" title="Remove">✕</button>');
      x.onclick = async () => {
        try { await api(`/images/${id}`, { method: 'DELETE' }); onChange && onChange(); }
        catch (e) { toastErr(e); }
      };
      wrap.append(im, x); strip.append(wrap);
    } else {
      strip.append(im);
    }
  }
  if (canEdit) {
    const add = el('<label class="act ghost" style="cursor:pointer">📷 Add<input type="file" accept="image/*" capture="environment" hidden></label>');
    add.querySelector('input').onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      try {
        const data = await resizeImage(file);
        await api('/images', { body: { entity, entity_id: entityId, content_type: 'image/jpeg', data_base64: data } });
        onChange && onChange();
      } catch (ex) { toastErr(ex); }
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
