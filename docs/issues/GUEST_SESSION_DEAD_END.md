# A guest whose session dies mid-use is told to "sign in again" — with no account

**Status:** open · **Severity:** low (self-heals on reload; needs a real expiry to hit)
**Found:** 2026-08-01, while verifying the FEED.md work (a wiped dev DB reproduced it)
**Pre-existing:** yes — unrelated to the feed merge

## What happens

When an in-flight request 401s (a 30-day session expiry, or a token whose user no
longer exists), `api.js` clears the token and the view renders its error card:

> Your session expired — sign in again.

For a **real** account that is correct advice. For a **guest** it is a dead end:
they have no email or password to sign in with. The app self-heals on the next
reload — boot's `ensureSession()` finds no token and mints a fresh guest — but
until then the screen offers an action the guest cannot take.

## Why it is not urgent

- It needs an actual expiry (30 days) or server-side deletion; a normal session
  refreshes on every `/me`.
- Any reload fixes it, and the PWA reloads on every new deploy anyway.
- Guests are anonymous by construction, so nothing of theirs is *lost* — their
  records still belong to the old guest row; they are simply unreachable, which
  is already true the moment they clear their browser storage.

## Fix sketch (needs its own pass)

On a 401 where the cleared session was a **guest** (`me.is_guest === true` in the
cached user), mint a fresh guest and re-render instead of showing the card — the
router already waits for `ensureSession()`, so the plumbing exists. Guard against
a loop: one silent re-mint per page load, then show the card.

The honest copy for the remaining case is also different per identity: "Sign in
again" for a real account, "Something went wrong — reopen the app" for a guest.
