// The two on-site landings.
//
// #/c/{code} — the EVENT code. Native camera opened SITE/#/c/{code}; the router
// already ensured we're logged in (return-to). We resolve the code, show the
// project + full waiver, and drive I-agree (check-in) → checked-in → check-out
// (mint) with warm, reassuring states. This is an ASSERTION: knowing a code that
// is printed on a sign proves you saw the sign, nothing more.
//
// #/s/{qr_token}/{event_id} — a PERSON's code, reached from the in-app scanner or
// any native camera. Confirming it records BOTH of us as present, attributed to
// me, the one holding the camera. That is the ATTESTED layer (CHECKIN_PROOF.md).
import { api } from '../api.js';
import {
  el, mount, esc, spinner, toast, toastErr, errMessage, fmtDateTime, fmtDuration,
} from '../ui.js';
import { refresh, refreshMe } from '../app.js';

export async function checkinView(code) {
  mount(spinner());
  let data;
  try {
    data = await api('/checkin/' + encodeURIComponent(code));
  } catch (e) {
    if (e && e.status === 404) { mount(invalidCard()); return; }
    mount(problemCard(() => checkinView(code), e));
    return;
  }

  // The code now resolves an EVENT (occurrence): its project card, that event's
  // schedule, the project's current waiver, and my open participation (if any).
  const { event, project, waiver, my_open_participation } = data;
  const action = el('<div class="card stack center"></div>');
  if (my_open_participation) {
    renderCheckedIn(action, project, my_open_participation.id,
      my_open_participation.checked_in_at, my_open_participation.attested);
  } else {
    renderAgree(action, code, project);
  }
  const root = el('<div class="stack"></div>');
  root.append(summaryCard(project, event), waiverBox(waiver), action);
  mount(root);
}

// ---- #/s/{qr_token}/{event_id} — the peer check-in -------------------------

export async function scanView(qrToken, eventId) {
  mount(spinner());
  const path = '/scan/' + encodeURIComponent(qrToken) + '/' + encodeURIComponent(eventId);
  let data;
  try {
    data = await api(path);
  } catch (e) {
    if (e && e.status === 404) { mount(invalidCard(true)); return; }
    mount(problemCard(() => scanView(qrToken, eventId), e));
    return;
  }

  const { person, is_self: isSelf, event, project, waiver, my_open_participation: mine } = data;
  const action = el('<div class="card stack center"></div>');
  if (isSelf) {
    renderOwnCode(action, project);
  } else if (mine && mine.attested) {
    // Already verified here — nothing to gain from confirming again.
    renderCheckedIn(action, project, mine.id, mine.checked_in_at, true);
  } else {
    renderConfirm(action, path, person, project);
  }

  const root = el('<div class="stack"></div>');
  root.append(withWhoCard(project, event, person, isSelf), waiverBox(waiver), action);
  mount(root);
}

// The summary card, plus who this code belongs to — the whole point of the screen.
function withWhoCard(project, event, person, isSelf) {
  const c = summaryCard(project, event);
  c.prepend(el(isSelf
    ? '<div class="banner warn center">This is <strong>your own</strong> code</div>'
    : `<div class="banner info center">Checking in with <strong>${esc(person.display_name)}</strong></div>`));
  return c;
}

function renderConfirm(container, path, person, project) {
  container.replaceChildren();
  container.append(el(
    `<p class="muted">${esc(person.display_name)} is here with you. Confirming records you both as present — and signs the waiver above for you.</p>`,
  ));
  const btn = el("<button class=\"act primary block big\">Confirm — we're both here</button>");
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const res = await api(path + '/confirm', { method: 'POST' });
      const p = res.participation || {};
      toast('✅ Verified');
      renderCheckedIn(container, project, p.id, p.checked_in_at, true);
    } catch (e) {
      btn.disabled = false;
      if (e && (e.status === 409 || e.status === 404)) { toast(errMessage(e)); refresh(); return; }
      toastErr(e);
    }
  };
  container.append(btn);
}

function renderOwnCode(container, project) {
  container.replaceChildren();
  container.append(el('<p class="muted center">Hold it up so somebody else can scan it — a code only counts when another person reads it.</p>'));
  container.append(links(project));
}

// ---- pieces ----------------------------------------------------------------

// Project title + THIS event's schedule (location/date/duration live on the event now).
function summaryCard(project, event) {
  const c = el('<div class="card stack"></div>');
  c.append(el(`<h2>${esc(project.title)}</h2>`));
  if (event && event.location_text) c.append(el(`<div class="tag">📍 ${esc(event.location_text)}</div>`));
  if (event && event.starts_at) c.append(el(`<div class="tag">🗓 ${esc(fmtDateTime(event.starts_at))}</div>`));
  if (event && event.expected_minutes != null) c.append(el(`<div class="tag">⏱ ${esc(fmtDuration(event.expected_minutes))} expected</div>`));
  return c;
}

function waiverBox(waiver) {
  const c = el('<div class="card stack"></div>');
  c.append(el('<div class="section-label">Volunteer waiver</div>'));
  const box = el('<div class="small"></div>');
  box.style.maxHeight = '40vh';
  box.style.overflowY = 'auto';
  box.style.whiteSpace = 'pre-wrap';
  box.style.lineHeight = '1.5';
  box.style.padding = '.2rem .1rem';
  // Public UGC → assign as text so any markup renders inert.
  box.textContent = (waiver && waiver.text) || 'No waiver text was provided for this project.';
  c.append(box);
  c.append(el('<p class="muted small">By checking in you agree to the waiver above.</p>'));
  return c;
}

function renderAgree(container, code, project) {
  container.replaceChildren();
  container.append(el('<p class="muted">Ready when you are — tap below to sign the waiver and check in.</p>'));
  const btn = el('<button class="act primary block big">I agree — check me in</button>');
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const row = await api('/checkin/' + encodeURIComponent(code) + '/agree', { method: 'POST' });
      renderCheckedIn(container, project, row.id, row.checked_in_at);
    } catch (e) {
      btn.disabled = false;
      if (e && e.status === 409) { toast(errMessage(e)); refresh(); return; } // re-fetch → checked-in state
      toastErr(e);
    }
  };
  container.append(btn);
}

function renderCheckedIn(container, project, participationId, checkedInAt, attested = false) {
  container.replaceChildren();
  container.append(el(
    `<div class="banner info center"><strong>${attested ? '✅ Verified — you\'re checked in' : "✅ You're checked in"}</strong>${checkedInAt ? ' — ' + esc(fmtDateTime(checkedInAt)) : ''}</div>`,
  ));
  container.append(el(attested
    ? '<p class="muted center">Somebody here confirmed it. You\'re all set.</p>'
    : '<p class="muted center">You\'re all set. Find the leader if you need anything.</p>'));

  const out = el('<button class="act block big">Check out</button>');
  out.onclick = async () => {
    out.disabled = true;
    try {
      const row = await api('/participations/' + participationId + '/checkout', { method: 'POST' });
      const n = (row && row.tokens_awarded) || 0;
      toast(n > 0 ? `＋${n} tokens` : 'Checked out — thanks!');
      await refreshMe();
      renderDone(container, project, n);
    } catch (e) {
      out.disabled = false;
      if (e && e.status === 409) { toast(errMessage(e)); refresh(); return; }
      toastErr(e);
    }
  };
  container.append(out, links(project));
}

function renderDone(container, project, tokens) {
  container.replaceChildren();
  container.append(el('<div class="banner info center"><strong>🎉 Checked out — thanks for showing up!</strong></div>'));
  container.append(tokens > 0
    ? el(`<p class="center"><span class="tokens">＋${esc(tokens)} tokens</span> added to your wallet.</p>`)
    : el('<p class="center muted">Every minute counts — thanks for volunteering.</p>'));
  container.append(links(project));
}

function links(project) {
  const row = el('<div class="row wrap" style="justify-content:center"></div>');
  if (project && project.id != null) {
    row.append(el(`<a class="act ghost" href="#/projects/${encodeURIComponent(project.id)}">View project</a>`));
  }
  row.append(el('<a class="act ghost" href="#/">Home</a>'));
  return row;
}

function invalidCard(peer = false) {
  const c = el('<div class="card stack center"></div>');
  c.append(el("<h2>That code didn't work</h2>"));
  c.append(el(`<p class="muted">${peer
    ? "This personal code is no longer valid, or the event has ended. Ask them to open their code again."
    : 'This check-in code is invalid or the project has ended.'}</p>`));
  c.append(el('<a class="act primary" href="#/projects">Back to projects</a>'));
  return c;
}

function problemCard(retryFn, e) {
  const c = el('<div class="card stack center"></div>');
  c.append(el(`<p>${esc(errMessage(e))}</p>`));
  const retry = el('<button class="act primary">Try again</button>');
  retry.onclick = retryFn;
  c.append(retry);
  c.append(el('<a class="act ghost" href="#/">Home</a>'));
  return c;
}
