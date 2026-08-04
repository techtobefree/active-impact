// Wallet: balance hero, tip form, ledger, and what I have redeemed.
import { api, currentUser } from '../api.js';
import {
  el, esc, mount, addForm, statusPill, emptyState, spinner,
  toast, toastErr, fmtDateTime,
} from '../ui.js';
import { refresh, refreshMe } from '../app.js';

// A small kind chip for a ledger row (earn/tip/burn), colored like a pill.
function kindChip(kind) {
  const cls = kind === 'earn' ? 'green' : kind === 'tip' ? 'amber' : 'muted';
  return `<span class="pill ${cls}">${esc(kind)}</span>`;
}

// One ledger row: direction arrow + amount, kind chip, counterparty, note, time.
// A burn has no counterparty at all — the tokens went out of existence, so the
// row says that rather than naming an innocent bystander as the recipient.
function ledgerRow(e) {
  const inbound = e.direction === 'in';
  const cp = e.counterparty;
  const who = cp
    ? `<a href="#/u/${esc(cp.id)}">${esc(cp.display_name)}</a>`
    : e.kind === 'burn'
      ? '<span class="muted">retired — out of circulation</span>'
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

// Something I redeemed (claimant view). Settled on arrival, so there is nothing
// to chase and nothing to cancel — the row is a receipt (T11).
function myClaimRow(c) {
  const item = c.item || {};
  return el(`<div class="card row" style="align-items:flex-start">
    <div class="grow">
      <div><a href="#/catalog/${esc(item.id)}">${esc(item.title || 'Item')}</a></div>
      <div class="muted small">${c.price_tokens === 0 ? 'free' : esc(c.price_tokens) + ' 🪙 retired'}${c.decided_at ? ' · ' + esc(fmtDateTime(c.decided_at)) : ''}</div>
    </div>
    <div class="stack center" style="gap:.4rem">${statusPill(c.status)}</div>
  </div>`);
}

// Somebody redeeming one of my own items (poster view). No buttons: I cannot
// refuse a claim on a listing I have not withdrawn (T6), and the tokens are
// already gone rather than owed to me (T4).
function incomingRow(c) {
  const item = c.item || {};
  const claimant = c.claimant;
  const who = claimant
    ? `<a href="#/u/${esc(claimant.id)}">${esc(claimant.display_name)}</a>`
    : '<span class="muted">someone</span>';
  return el(`<div class="card row" style="align-items:flex-start">
    <div class="grow">
      <div><a href="#/catalog/${esc(item.id)}">${esc(item.title || 'Item')}</a></div>
      <div class="muted small">${who} · ${c.price_tokens === 0 ? 'free' : esc(c.price_tokens) + ' 🪙 retired'}${c.decided_at ? ' · ' + esc(fmtDateTime(c.decided_at)) : ''}</div>
    </div>
    <div class="stack center" style="gap:.4rem">${statusPill(c.status)}</div>
  </div>`);
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
      api('/claims?role=poster'),
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

  // ---- tip tokens ----
  const tipForm = addForm({
    title: 'Tip tokens',
    submit: 'Tip 🪙',
    fields: [
      { name: 'to_email', label: 'To (email)', required: true, placeholder: 'their email',
        attrs: { autocapitalize: 'none', autocorrect: 'off', spellcheck: 'false', inputmode: 'email' },
        transform: (v) => v.toLowerCase().replace(/\s+/g, '') },
      { name: 'amount', label: 'Amount', type: 'number', required: true, min: 1, step: 1, placeholder: '1' },
      { name: 'note', label: 'Note (optional)', placeholder: 'thanks for the help!' },
    ],
    onSubmit: async (body) => {
      // Errors propagate to addForm, which attributes them to the exact field
      // (unknown user -> To, insufficient balance -> Amount).
      await api('/tokens/tip', { body });
      toast(`Tipped ${body.amount} 🪙`);
      await refreshMe();
      refresh();
    },
  });

  // ---- ledger ----
  const ledgerNodes = ledger.length
    ? ledger.map(ledgerRow)
    : [emptyState('Nothing in your ledger yet — volunteer an hour to earn your first token.')];

  // ---- what I redeemed (claimant) ----
  const mineNodes = mine.length
    ? mine.map(myClaimRow)
    : [emptyState('You haven’t redeemed anything yet.')];

  // ---- what people redeemed from me (poster) ----
  const incomingNodes = incoming.length
    ? incoming.map(incomingRow)
    : [emptyState('Nobody has redeemed one of your offers yet.')];

  const root = el('<div class="stack"></div>');
  root.append(
    hero,
    tipForm,
    label('Ledger'),
    ...ledgerNodes,
    label('My requests'),
    ...mineNodes,
    label('Requests on my items'),
    ...incomingNodes,
  );
  mount(root);
}
