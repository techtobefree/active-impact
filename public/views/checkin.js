// The #/c/{code} QR landing — the core on-site moment.
// Native camera opened SITE/#/c/{code}; the router already ensured we're logged
// in (return-to). We resolve the code, show the project + full waiver, and drive
// I-agree (check-in) → checked-in → check-out (mint) with warm, reassuring states.
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
    mount(problemCard(code, e));
    return;
  }

  const { project, waiver, my_open_participation } = data;
  const action = el('<div class="card stack center"></div>');
  if (my_open_participation) {
    renderCheckedIn(action, project, my_open_participation.id, my_open_participation.checked_in_at);
  } else {
    renderAgree(action, code, project);
  }
  mount(summaryCard(project), waiverBox(waiver), action);
}

// ---- pieces ----------------------------------------------------------------

function summaryCard(p) {
  const c = el('<div class="card stack"></div>');
  c.append(el(`<h2>${esc(p.title)}</h2>`));
  if (p.location_text) c.append(el(`<div class="tag">📍 ${esc(p.location_text)}</div>`));
  if (p.starts_at) c.append(el(`<div class="tag">🗓 ${esc(fmtDateTime(p.starts_at))}</div>`));
  if (p.expected_minutes != null) c.append(el(`<div class="tag">⏱ ${esc(fmtDuration(p.expected_minutes))} expected</div>`));
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

function renderCheckedIn(container, project, participationId, checkedInAt) {
  container.replaceChildren();
  container.append(el(
    `<div class="banner info center"><strong>✅ You're checked in</strong>${checkedInAt ? ' — ' + esc(fmtDateTime(checkedInAt)) : ''}</div>`,
  ));
  container.append(el('<p class="muted center">You\'re all set. Find the leader if you need anything.</p>'));

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

function invalidCard() {
  const c = el('<div class="card stack center"></div>');
  c.append(el("<h2>That code didn't work</h2>"));
  c.append(el('<p class="muted">This check-in code is invalid or the project has ended.</p>'));
  c.append(el('<a class="act primary" href="#/">Back to projects</a>'));
  return c;
}

function problemCard(code, e) {
  const c = el('<div class="card stack center"></div>');
  c.append(el(`<p>${esc(errMessage(e))}</p>`));
  const retry = el('<button class="act primary">Try again</button>');
  retry.onclick = () => checkinView(code);
  c.append(retry);
  c.append(el('<a class="act ghost" href="#/">Home</a>'));
  return c;
}
