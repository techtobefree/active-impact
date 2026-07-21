// Service log — the anonymous-first social feed (SERVICE_LOG.md §7).
//   #/       feedView   — photo-forward gallery of everyone's records
//   #/log    logView    — one photo + one caption -> POST /service_records
//   #/r/:id  recordView — a single record (share target / deep link)
// Public UGC: every author-supplied string is escaped (esc) or set via textContent.
import { api, apiBlobURL, currentUser } from '../api.js';
import {
  el, esc, mount, clear, spinner, emptyState, avatarEl,
  toast, toastErr, errMessage, fmtDate, resizeImage,
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
function timeAgo(iso) {
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
function cheerButton(rec) {
  const btn = el('<button class="act cheer" aria-label="Cheer"></button>');
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

// One record card. `linkDetail` makes the photo tap through to #/r/:id (feed).
// `onDeleted` overrides the default (remove the card) — detail navigates home.
export function recordCard(rec, { linkDetail = false, onDeleted } = {}) {
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

  const actions = el('<div class="row"></div>');
  actions.append(cheerButton(rec));
  card.append(actions);
  return card;
}

// ---- feed (home) ------------------------------------------------------------

export async function feedView() {
  const list = el('<div class="stack records"></div>');
  const moreWrap = el('<div class="center hidden" style="margin-top:.5rem"></div>');
  const moreBtn = el('<button class="act">Load more</button>');
  moreWrap.append(moreBtn);

  let offset = 0;
  let loading = false;
  let done = false;

  async function load(initial) {
    if (loading || done) return;
    loading = true;
    if (initial) clear(list).append(spinner());
    else { moreBtn.disabled = true; moreBtn.textContent = '…'; }
    try {
      const rows = await api(`/service_records?scope=all&limit=${PAGE}&offset=${offset}`);
      if (initial) clear(list);
      if (initial && (!rows || !rows.length)) {
        const empty = el('<div class="empty stack center"></div>');
        empty.append(el('<p>No service logged yet. Be the first.</p>'));
        empty.append(el('<a class="act primary" href="#/log">＋ Log a service</a>'));
        list.append(empty);
        done = true;
        return;
      }
      for (const rec of (rows || [])) list.append(recordCard(rec, { linkDetail: true }));
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
  root.append(el('<a class="act primary block" href="#/log">＋ Log a service</a>'), list, moreWrap);
  mount(root);
  await load(true);
}

// ---- log a service ----------------------------------------------------------

export async function logView() {
  let dataB64 = null;

  const intro = el(`<div class="card stack">
    <h1>Log a service</h1>
    <p class="muted">Snap a photo of an act of service and add a caption — it goes straight to the feed.</p>
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

  const post = el('<button class="act primary block" disabled>Post</button>');
  const cancel = el('<a class="act ghost block" href="#/">Cancel</a>');

  const refreshPost = () => { post.disabled = !(dataB64 && cap.value.trim().length); };

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
      await api('/service_records', {
        body: { caption: cap.value.trim(), content_type: 'image/jpeg', data_base64: dataB64 },
      });
      toast('Logged! 🌱');
      location.hash = '#/'; // land on the feed with the new record on top
    } catch (e) {
      toastErr(e);
      post.disabled = false;
      post.textContent = label;
    }
  };

  const root = el('<div class="stack"></div>');
  root.append(intro, picker, capWrap, post, cancel);
  mount(root);
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
  mount(root);
}
