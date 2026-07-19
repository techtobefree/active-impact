// Wallet: balance hero, send-tokens (tip), ledger, and both sides of my claims.
import { api, currentUser } from '../api.js';
import {
  el, esc, mount, addForm, statusPill, emptyState, spinner,
  toast, toastErr, fmtDateTime,
} from '../ui.js';
import { refresh, refreshMe } from '../app.js';

// A small kind chip for a ledger row (earn/tip/spend), colored like a pill.
function kindChip(kind) {
  const cls = kind === 'earn' ? 'green' : kind === 'tip' ? 'amber' : 'muted';
  return `<span class="pill ${cls}">${esc(kind)}</span>`;
}

// One ledger row: direction arrow + amount, kind chip, counterparty, note, time.
function ledgerRow(e) {
  const inbound = e.direction === 'in';
  const cp = e.counterparty;
  const who = cp
    ? `<a href="#/u/${esc(cp.username)}">${esc(cp.display_name || cp.username)}</a>`
    : '<span class="muted">system</span>';
  const note = e.note ? `<div class="muted small">${esc(e.note)}</div>` : '';
  const amtColor = inbound ? 'var(--green)' : 'var(--red)';
  return el(`<div class="card row" style="align-items:flex-start">
    <div class="grow">
      <div class="row wrap" style="gap:.4rem">${kindChip(e.kind)}<span>${who}</span></div>
      ${note}
      <div class="muted small">${esc(fmtDateTime(e.created_at))}</div>
    </div>
    <div style="font-weight:700;white-space:nowrap;color:${amtColor}">${inbound ? '▲ +' : '▼ −'}${esc(e.amount)} 🪙</div>
  </div>`);
}

// My outgoing request on someone else's offer (claimant view).
function myClaimRow(c) {
  const item = c.item || {};
  const row = el(`<div class="card row" style="align-items:flex-start">
    <div class="grow">
      <div><a href="#/catalog/${esc(item.id)}">${esc(item.title || 'Item')}</a></div>
      <div class="muted small">${esc(c.price_tokens)} 🪙</div>
    </div>
    <div class="stack center" style="gap:.4rem">${statusPill(c.status)}</div>
  </div>`);
  if (c.status === 'pending') {
    const actions = row.querySelector('.stack');
    const cancel = el('<button class="act del small">Cancel</button>');
    cancel.onclick = async () => {
      cancel.disabled = true;
      try {
        await api(`/claims/${c.id}/cancel`, { method: 'POST' });
        toast('Request canceled');
        refresh();
      } catch (ex) {
        toastErr(ex);
        // A 409 means the claim changed under us — re-render reality, don't
        // leave a dead button that repeats the same error forever.
        if (ex && ex.status === 409) refresh(); else cancel.disabled = false;
      }
    };
    actions.append(cancel);
  }
  return row;
}

// A pending request on one of my own items (poster view): Accept / Decline.
function incomingRow(c) {
  const item = c.item || {};
  const claimant = c.claimant;
  const who = claimant
    ? `<a href="#/u/${esc(claimant.username)}">${esc(claimant.display_name || claimant.username)}</a>`
    : '<span class="muted">someone</span>';
  const row = el(`<div class="card stack">
    <div class="row" style="align-items:flex-start">
      <div class="grow">
        <div><a href="#/catalog/${esc(item.id)}">${esc(item.title || 'Item')}</a></div>
        <div class="muted small">from <span>${who}</span> · ${esc(c.price_tokens)} 🪙</div>
      </div>
      ${statusPill(c.status)}
    </div>
  </div>`);

  const bar = el('<div class="row"></div>');
  const accept = el('<button class="act primary grow">Accept</button>');
  const decline = el('<button class="act grow">Decline</button>');
  const busy = (on) => { accept.disabled = on; decline.disabled = on; };
  accept.onclick = async () => {
    busy(true);
    try {
      await api(`/claims/${c.id}/accept`, { method: 'POST' });
      toast('Accepted 🎁');
      await refreshMe();
      refresh();
    } catch (ex) {
      // "Not enough tokens" here would read as the POSTER's balance — clarify.
      if (ex && ex.detail === 'insufficient_balance') toast("The claimant doesn't have enough tokens yet.");
      else toastErr(ex);
      if (ex && ex.status === 409 && ex.detail !== 'insufficient_balance') refresh(); else busy(false);
    }
  };
  decline.onclick = async () => {
    busy(true);
    try {
      await api(`/claims/${c.id}/decline`, { method: 'POST' });
      toast('Declined');
      refresh();
    } catch (ex) {
      toastErr(ex);
      if (ex && ex.status === 409) refresh(); else busy(false);
    }
  };
  bar.append(accept, decline);
  row.append(bar);
  return row;
}

function label(text) { return el(`<div class="section-label">${esc(text)}</div>`); }

export async function walletView() {
  mount(spinner());

  const me = (await refreshMe()) || currentUser();
  const balance = (me && me.balance != null) ? me.balance : 0;

  let ledger = [], mine = [], incoming = [];
  try {
    [ledger, mine, incoming] = await Promise.all([
      api('/tokens/ledger'),
      api('/claims?role=claimant'),
      api('/claims?role=poster&status=pending'),
    ]);
  } catch (e) {
    if (e && e.detail === 'unauthorized') throw e; // app.js already redirected
    toastErr(e);
  }

  // ---- balance hero ----
  const hero = el(`<section class="card center stack">
    <div class="section-label" style="margin-top:0">Your balance</div>
    <div style="font-size:2.6rem;font-weight:800;color:var(--green);line-height:1">🪙 ${esc(balance)}</div>
    <div class="muted small">impact tokens</div>
  </section>`);

  // ---- send tokens (tip) ----
  const tipForm = addForm({
    title: 'Send tokens',
    submit: 'Send 🪙',
    fields: [
      { name: 'to_username', label: 'To (username)', required: true, placeholder: 'their username',
        // Handles display as "@name" everywhere — accept a pasted "@name" too.
        transform: (v) => v.replace(/^@/, '').trim().toLowerCase() },
      { name: 'amount', label: 'Amount', type: 'number', required: true, min: 1, step: 1, placeholder: '1' },
      { name: 'note', label: 'Note (optional)', placeholder: 'thanks for the help!' },
    ],
    onSubmit: async (body) => {
      // Errors propagate to addForm, which attributes them to the exact field
      // (unknown user -> To, insufficient balance -> Amount).
      await api('/tokens/tip', { body });
      toast(`Sent ${body.amount} 🪙`);
      await refreshMe();
      refresh();
    },
  });

  // ---- ledger ----
  const ledgerNodes = ledger.length
    ? ledger.map(ledgerRow)
    : [emptyState('Nothing in your ledger yet — volunteer an hour to earn your first token.')];

  // ---- my requests (claimant) ----
  const mineNodes = mine.length
    ? mine.map(myClaimRow)
    : [emptyState('You haven’t requested anything yet.')];

  // ---- requests on my items (poster, pending) ----
  const incomingNodes = incoming.length
    ? incoming.map(incomingRow)
    : [emptyState('No pending requests on your items.')];

  mount(
    hero,
    tipForm,
    label('Ledger'),
    ...ledgerNodes,
    label('My requests'),
    ...mineNodes,
    label('Requests on my items'),
    ...incomingNodes,
  );
}
