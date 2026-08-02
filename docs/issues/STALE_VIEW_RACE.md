# A slow view could paint over the screen you navigated to

**Status:** fixed (2026-08-01) · **Severity:** medium (wrong screen, silently)
**Found:** while writing the FEED.md e2e tests — two of them failed on the *app*, not the test
**Pre-existing:** yes — as old as the router; the feed work just navigated fast enough to expose it

## What happened

Every view in `public/views/` has the same shape:

```js
export async function eventDetailView(id) {
  mount(spinner());
  const ev = await api('/events/' + id);   // ← the gap
  ...
  mount(root);                             // ← paints whatever it built
}
```

`render()` invoked views immediately on every `hashchange`, so two views could be
in flight at once. If the first one's fetch finished *after* the second one had
already mounted, the first still called `mount()` — and painted the screen the
user had just navigated away from, over the one they had chosen.

Reproduced by hand: open a project card and immediately tap a bottom-nav tab; the
project page wins. In the e2e suite it showed up as `#/me` displaying an event
page, and as a 45-second timeout hunting for a form that was never going to appear.

## Why the obvious fix is worse

The first attempt corrected it *after the fact* — if the hash had moved by the
time a view finished, re-render the route that is actually current:

```js
await view(...groups);
if (location.hash !== hash) return render();   // DON'T
```

That fixed the wrong-screen symptom and immediately broke project creation: the
projects list (still loading when the user tapped "New service project") finished,
saw a moved hash, and re-rendered the *form* — throwing away everything typed into
it. A correction that wipes user input is not a correction.

## The fix

Serialize the view calls. `render()` keeps doing its synchronous work — route
match, auth gate, chrome, scroll — the moment the hash changes, so the UI still
feels instant; only the async view body is queued:

```js
let queue = Promise.resolve();
queue = queue.then(() => runView(view, groups), () => runView(view, groups));
```

The newest render therefore always mounts last, and no view's screen is ever
replaced after the fact. Both failure paths (rejected renders, redirects) pass the
baton on, so the chain cannot stall.

**Precondition, worth keeping true:** nothing may `await render()` (or
`await refresh()`) from inside a view — that would wait on the queue the view is
itself blocking. Nothing does today; `refresh()` is always called fire-and-forget.
A future `await refresh()` would deadlock the router, so this is the one rule to
remember.

## The rule serialization creates (it bit immediately)

A view that keeps awaiting delays the *next* view. The log screen awaited
`getPosition()` before returning, so a phone whose owner ignores the location
prompt would have frozen navigation for the full 8-second geolocation timeout —
caught by the peer-check-in e2e test, which navigates away from `#/log` and then
waited on it.

**So: a view awaits only what it needs in order to mount.** Anything slower —
geolocation, a target lookup, a paginated feed — runs detached and repaints when
it lands, guarded by `node.isConnected` so a departed screen paints nothing. That
is now the pattern in `logView` and `recordFeed`.

## The same family, found later (2026-08-01, all fixed)

Serializing the views fixed *view vs view*. Three more paints from the same
family turned up when the e2e suite was run against the **live site**, where the
guest mint takes ~150ms instead of ~1ms:

| Where | What happened | Fix |
|---|---|---|
| **Boot vs a live screen** | Boot awaits the guest mint, so a hashchange could paint first — then boot's own `render()` rebuilt that screen from scratch, blanking a half-typed form. The failing screenshot was a register form with "Please fill out this field". | Boot renders only `if (!hasRendered)`. |
| **The auth gate vs the mint** | A QR deep link arriving before the mint hit `!getToken()` and bounced to the convert screen — asking someone to sign up because the network was slow. | The gate `await`s `ensureSession()` (single-flight) and only falls back when genuinely offline. |
| **A form submit vs the mint** | `POST /auth/convert` needs a session; submitting a filled form during a cold open 401'd. | The convert submit awaits `ensureSession()` too. |

The through-line: **anything that paints or posts must wait for the session, and
nothing may repaint a screen the user is already using.** Every one of these was
invisible on localhost and reproducible against production, which is the argument
for running the browser suite against the deployed site as a matter of course.

**A fifth, inside a single view (2026-08-02).** The same shape one level down: the
home feed's `load()` is called by tab taps *and* by the search box, so two can be
in flight at once — and the slower one won the paint. Tapping **Following** while
the projects request was still running filled that tab with project cards. The
race had always been there between Upcoming/Past/Mine and was simply invisible,
because all three render the same card shape; adding a tab with a *different*
shape made it obvious.

Fixed the same way as the suggestion box: a sequence number captured before the
await, and whoever started last owns the container.

```js
const mine = ++loadSeq;
const rows = await fetchIt();
if (mine !== loadSeq) return;   // a newer tab or keystroke already owns this
```

**The generalization worth remembering: any handler that awaits and then writes to
a shared node needs either a sequence guard or a queue.** The router got the
queue; in-view loaders get the guard.

## Residual limitation

A view whose *mounting* fetch hangs still delays the next view (never the chrome).
Every such fetch is same-origin and behind a healthcheck, and the previous
behaviour — navigate instantly, then get repainted by the page you left — was
worse. If it ever bites, the next step is an abort signal per render passed into
`api()`.

## Covered by

`e2e/tests/service_log.spec.js` (navigates between an event page and `#/me`
immediately after a redirect) and `e2e/tests/projects.spec.js` (fills the create
form while the list behind it is still loading). Both failed before the fix.
