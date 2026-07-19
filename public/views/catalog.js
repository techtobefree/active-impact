// Catalog: offers|needs list, create (offer/need), detail with role-aware actions.
import { api, apiBlobURL, currentUser } from '../api.js';
import {
  el, esc, mount, clear, spinner, emptyState, statusPill,
  addForm, imagesStrip, toast, toastErr, errMessage,
} from '../ui.js';
import { refresh, refreshMe } from '../app.js';

// ---- shared card bits -------------------------------------------------------

function coverEl(imageId) {
  const im = el('<img class="cover" alt="" />');
  apiBlobURL(`/images/${imageId}`).then((u) => { im.src = u; }).catch(() => {});
  return im;
}

// Price for an offer (🪙 N or "free"); a "need" badge otherwise.
function priceEl(item) {
  if (item.kind === 'offer') {
    const txt = item.price_tokens === 0 ? 'free' : `🪙 ${item.price_tokens}`;
    return el(`<span class="tokens">${esc(txt)}</span>`);
  }
  return el('<span class="pill amber">need</span>');
}

function quantityTag(item) {
  return item.quantity != null ? el(`<span class="tag">${esc('×' + item.quantity)}</span>`) : null;
}

function posterLine(item) {
  const p = item.poster;
  if (!p) return null;
  return el(`<p class="small muted">by <a href="#/u/${esc(p.username)}">${esc(p.display_name || p.username)}</a></p>`);
}

// A list card: cover, linked title, status pill, price/need + quantity, poster.
function listCardEl(item) {
  const card = el('<div class="card"></div>');
  if (item.cover_image_id) card.append(coverEl(item.cover_image_id));
  const titleRow = el('<div class="row"></div>');
  titleRow.append(el(`<h3 class="grow"><a href="#/catalog/${item.id}">${esc(item.title)}</a></h3>`));
  titleRow.append(el(statusPill(item.status)));
  card.append(titleRow);
  const meta = el('<div class="row wrap"></div>');
  meta.append(priceEl(item));
  const q = quantityTag(item);
  if (q) meta.append(q);
  card.append(meta);
  const pl = posterLine(item);
  if (pl) card.append(pl);
  return card;
}

function inlineError(e) {
  const card = el('<div class="card stack center"></div>');
  card.append(el(`<p>${esc(errMessage(e))}</p>`));
  card.append(el('<a class="act" href="#/catalog">Back to catalog</a>'));
  return card;
}

// ---- list -------------------------------------------------------------------

export async function listView() {
  let kind = 'offer';
  let items = [];
  let query = '';

  const offerTab = el('<button class="act grow">Offers</button>');
  const needTab = el('<button class="act grow">Needs</button>');
  const tabs = el('<div class="row"></div>');
  tabs.append(offerTab, needTab);

  const search = el('<input class="grow" type="search" placeholder="Search…" aria-label="Search catalog" />');
  const post = el('<button class="act primary">＋ Post</button>');
  post.onclick = () => { location.hash = '#/catalog/new'; };
  const controls = el('<div class="row"></div>');
  controls.append(search, post);

  const results = el('<div class="stack"></div>');

  function renderResults() {
    clear(results);
    if (!items.length) {
      results.append(emptyState(
        query.trim() ? 'Nothing matches your search.'
          : kind === 'offer' ? 'No offers yet. Post the first one.'
          : 'No needs yet. Post the first one.',
      ));
      return;
    }
    for (const it of items) results.append(listCardEl(it));
  }

  async function load() {
    offerTab.classList.toggle('primary', kind === 'offer');
    needTab.classList.toggle('primary', kind === 'need');
    clear(results);
    results.append(spinner());
    try {
      // Server-side search: client filtering would only ever see the first page.
      const q = query.trim() ? `&q=${encodeURIComponent(query.trim())}` : '';
      items = await api(`/catalog?kind=${kind}${q}`);
      renderResults();
    } catch (e) {
      clear(results);
      results.append(emptyState(errMessage(e)));
      toastErr(e);
    }
  }

  let searchTimer;
  offerTab.onclick = () => { if (kind !== 'offer') { kind = 'offer'; load(); } };
  needTab.onclick = () => { if (kind !== 'need') { kind = 'need'; load(); } };
  search.oninput = () => {
    query = search.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(load, 250);
  };

  const wrap = el('<div class="stack"></div>');
  wrap.append(tabs, controls, results);
  mount(wrap);
  await load();
}

// ---- create -----------------------------------------------------------------

export async function newView() {
  let kind = 'offer';

  const intro = el(`<div class="card stack">
    <h1>Post to the catalog</h1>
    <p class="muted">Share an offer others can claim, or a need others can support with tokens.</p>
  </div>`);

  const offerBtn = el('<button class="act grow">🎁 Offer</button>');
  const needBtn = el('<button class="act grow">🙏 Need</button>');
  const toggle = el('<div class="row"></div>');
  toggle.append(offerBtn, needBtn);

  const helper = el('<p class="muted small"></p>');
  const formBox = el('<div></div>');

  function buildForm() {
    offerBtn.classList.toggle('primary', kind === 'offer');
    needBtn.classList.toggle('primary', kind === 'need');
    helper.textContent = kind === 'offer'
      ? 'Set a token price (0 = free). Claimants pay you when you accept.'
      : 'People can send you tokens from your post.';

    // Toggling offer/need rebuilds the form — carry the user's text across.
    const prev = formBox.querySelector('form');
    const keep = (n) => (prev && prev.elements[n] ? prev.elements[n].value : '');

    const fields = [
      {
        name: 'title', label: 'Title', required: true, value: keep('title'),
        placeholder: kind === 'offer' ? 'Homemade sourdough loaf' : 'Need a ride to the food bank',
      },
      {
        name: 'description', label: 'Description', type: 'textarea', rows: 5, value: keep('description'),
        placeholder: 'Pickup details, contact, coupon / redemption terms…',
      },
    ];
    if (kind === 'offer') {
      fields.push({ name: 'price_tokens', label: 'Price 🪙 (0 = free)', type: 'number', required: true, min: 0, placeholder: '0' });
      fields.push({ name: 'quantity', label: 'Quantity (optional)', type: 'number', min: 1, placeholder: 'e.g. 3' });
    }

    const form = addForm({
      fields,
      submit: 'Post',
      onSubmit: async (body) => {
        body.kind = kind;
        // Offers must carry price_tokens (0 allowed); needs must omit it.
        if (kind === 'need') delete body.price_tokens;
        const item = await api('/catalog', { body });
        location.hash = `#/catalog/${item.id}`;
      },
    });
    clear(formBox);
    formBox.append(form);
  }

  offerBtn.onclick = () => { if (kind !== 'offer') { kind = 'offer'; buildForm(); } };
  needBtn.onclick = () => { if (kind !== 'need') { kind = 'need'; buildForm(); } };
  buildForm();

  const wrap = el('<div class="stack"></div>');
  wrap.append(intro, toggle, helper, formBox);
  mount(wrap);
}

// ---- detail -----------------------------------------------------------------

export async function detailView(id) {
  mount(spinner());
  let detail;
  try {
    detail = await api(`/catalog/${id}`);
  } catch (e) {
    mount(inlineError(e));
    return;
  }

  const me = currentUser();
  const isPoster = !!(detail.poster && me && detail.poster.id === me.id);

  const wrap = el('<div class="stack"></div>');

  // Header card
  const head = el('<div class="card stack"></div>');
  if (detail.cover_image_id) head.append(coverEl(detail.cover_image_id));
  head.append(el(`<h1>${esc(detail.title)}</h1>`));
  const meta = el('<div class="row wrap"></div>');
  meta.append(priceEl(detail));
  const q = quantityTag(detail);
  if (q) meta.append(q);
  meta.append(el(statusPill(detail.status)));
  head.append(meta);
  const pl = posterLine(detail);
  if (pl) head.append(pl);
  if (detail.description) {
    const desc = el('<p style="white-space:pre-wrap"></p>');
    desc.textContent = detail.description;
    head.append(desc);
  }
  head.append(imagesStrip('catalog_item', Number(id), detail.image_ids, { canEdit: isPoster, onChange: refresh }));
  wrap.append(head);

  if (isPoster) {
    // Pending claims (loaded async below)
    wrap.append(el('<div class="section-label">Pending claims</div>'));
    const claimsBox = el('<div class="stack"></div>');
    wrap.append(claimsBox);

    // Manage: quick close + edit form
    wrap.append(el('<div class="section-label">Manage listing</div>'));
    if (detail.status === 'active') {
      const close = el('<button class="act block">Close listing</button>');
      close.onclick = async () => {
        if (!confirm('Close this listing? People can no longer claim it.')) return;
        try {
          await api(`/catalog/${id}`, { method: 'PATCH', body: { status: 'closed' } });
          toast('Listing closed');
          refresh();
        } catch (e) { toastErr(e); }
      };
      wrap.append(close);
    }
    wrap.append(buildEditForm(id, detail));

    mount(wrap);
    fillClaims(claimsBox, id);
    return;
  }

  if (detail.kind === 'offer') {
    wrap.append(offerViewerCard(id, detail));
  } else {
    wrap.append(needTipCard(id, detail));
  }
  mount(wrap);
}

function buildEditForm(id, detail) {
  const fields = [
    { name: 'title', label: 'Title', required: true, value: detail.title },
    { name: 'description', label: 'Description', type: 'textarea', rows: 5, value: detail.description || '', allowClear: true },
  ];
  if (detail.kind === 'offer') {
    fields.push({ name: 'price_tokens', label: 'Price 🪙 (0 = free)', type: 'number', required: true, min: 0, value: detail.price_tokens });
  }
  if (detail.quantity != null || detail.kind === 'offer') {
    fields.push({ name: 'quantity', label: 'Quantity (optional)', type: 'number', min: 1, value: detail.quantity });
  }
  fields.push({
    name: 'status', label: 'Status', type: 'select', value: detail.status,
    options: [{ value: 'active', text: 'Active' }, { value: 'closed', text: 'Closed' }],
  });

  return addForm({
    title: 'Edit listing',
    fields,
    submit: 'Save changes',
    onSubmit: async (body) => {
      await api(`/catalog/${id}`, { method: 'PATCH', body });
      toast('Listing updated');
      refresh();
    },
  });
}

async function fillClaims(box, itemId) {
  clear(box);
  box.append(spinner());
  try {
    const claims = await api('/claims?role=poster&status=pending');
    const mine = (claims || []).filter((c) => c.item_id === Number(itemId));
    clear(box);
    if (!mine.length) {
      box.append(el('<p class="muted small">No pending claims yet.</p>'));
      return;
    }
    for (const claim of mine) box.append(pendingClaimRow(claim));
  } catch (e) {
    clear(box);
    box.append(el(`<p class="muted small">${esc(errMessage(e))}</p>`));
  }
}

function pendingClaimRow(claim) {
  const row = el('<div class="card row wrap"></div>');
  const c = claim.claimant || {};
  const info = el('<div class="grow"></div>');
  info.append(el(`<div><a href="#/u/${esc(c.username)}">${esc(c.display_name || c.username)}</a></div>`));
  info.append(el(`<div class="small muted">${esc('wants this for ' + (claim.price_tokens === 0 ? 'free' : claim.price_tokens + ' 🪙'))}</div>`));
  row.append(info);

  const accept = el('<button class="act primary">Accept</button>');
  accept.onclick = async () => {
    accept.disabled = true;
    try {
      await api(`/claims/${claim.id}/accept`, { method: 'POST' });
      toast('Claim accepted');
      await refreshMe();
      refresh();
    } catch (e) {
      if (e && e.detail === 'insufficient_balance') { accept.disabled = false; toast("Claimant doesn't have enough tokens yet"); }
      else { toastErr(e); if (e && e.status === 409) refresh(); else accept.disabled = false; }
    }
  };

  const decline = el('<button class="act">Decline</button>');
  decline.onclick = async () => {
    decline.disabled = true;
    try {
      await api(`/claims/${claim.id}/decline`, { method: 'POST' });
      toast('Claim declined');
      refresh();
    } catch (e) { toastErr(e); if (e && e.status === 409) refresh(); else decline.disabled = false; }
  };

  row.append(accept, decline);
  return row;
}

function offerViewerCard(id, detail) {
  const card = el('<div class="card stack"></div>');
  const mc = detail.my_claim;

  if (mc && mc.status === 'pending') {
    card.append(el(`<p>Your claim ${statusPill('pending')}</p>`));
    const cancel = el('<button class="act block">Cancel claim</button>');
    cancel.onclick = async () => {
      cancel.disabled = true;
      try { await api(`/claims/${mc.id}/cancel`, { method: 'POST' }); toast('Claim canceled'); refresh(); }
      catch (e) { toastErr(e); if (e && e.status === 409) refresh(); else cancel.disabled = false; }
    };
    card.append(cancel);
    return card;
  }

  if (mc && mc.status === 'accepted') {
    card.append(el(`<p>Your claim ${statusPill('accepted')}</p>`));
    card.append(el('<div class="banner info">✅ Show this screen as proof.</div>'));
    return card;
  }

  // No live claim (none, declined, or canceled).
  if (mc) card.append(el(`<p class="muted small">Your last claim ${statusPill(mc.status)}</p>`));
  if (detail.status === 'active') {
    const label = detail.price_tokens === 0 ? 'Claim (free)' : `Claim (${detail.price_tokens} 🪙)`;
    const claim = el(`<button class="act primary block">${esc(label)}</button>`);
    claim.onclick = async () => {
      claim.disabled = true;
      try { await api(`/catalog/${id}/claim`, { method: 'POST' }); toast('Claim sent'); refresh(); }
      catch (e) { toastErr(e); if (e && e.status === 409) refresh(); else claim.disabled = false; }
    };
    card.append(claim);
  } else {
    card.append(el('<p class="muted">This listing is closed.</p>'));
  }
  return card;
}

function needTipCard(id, detail) {
  const card = el('<div class="card stack"></div>');
  card.append(el('<h3>Send tokens</h3>'));
  card.append(el('<p class="muted small">Support this need — tokens go straight to the poster.</p>'));

  const form = addForm({
    fields: [
      { name: 'amount', label: 'Amount 🪙', type: 'number', required: true, min: 1, placeholder: '5' },
      { name: 'note', label: 'Note (optional)', placeholder: 'Thanks for posting this!' },
    ],
    submit: 'Send tokens',
    onSubmit: async (body) => {
      body.to_username = detail.poster.username;
      body.catalog_item_id = Number(id);
      await api('/tokens/tip', { body });
      toast('Tokens sent!');
      form.reset();
      await refreshMe();
    },
  });
  card.append(form);
  return card;
}
