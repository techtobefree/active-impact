# A guest who has RSVP'd or checked in cannot MERGE into an existing account

**Status:** open · **Severity:** high (unhandled 500, user-visible, data-losing path)
**Found:** 2026-08-01, while designing CHECKIN_PROOF.md (§3)
**Pre-existing:** yes — not introduced by the attestation work

## What happens

`POST /api/auth/convert` has two paths (`app/auth.py`):

- **ATTACH** (email is free) — the guest row gains credentials. Fine.
- **MERGE** (email already belongs to an account, password matches) — the guest's
  content is re-pointed onto the existing account and the guest row is deleted.

The MERGE path re-points exactly four things: `service_records`, `cheers`,
`reports`, and `images.uploaded_by`. But **seven** tables reference `users.id`
with a plain, un-cascaded FK. Anything the guest did in the *projects* half of the
app is left dangling, so the final `DELETE FROM users` raises.

## Reproduction (verified)

```python
def test_guest_with_participation_can_merge(api, register):
    lead, _, _ = register("leader")
    ev = lead.post("/api/projects", json={
        "title": "Park cleanup", "location_text": "Park",
        "starts_at": "2099-01-01T10:00:00Z", "expected_minutes": 60,
    }).json()["events"][0]["id"]
    register("ana")                                    # the account to merge into

    h = {"Authorization": "Bearer " + api.post("/api/auth/guest").json()["token"]}
    api.post(f"/api/events/{ev}/checkin", headers=h)   # 200 — guest is on site

    r = api.post("/api/auth/convert", headers=h,
                 json={"email": "ana@test.local", "password": "password123"})
    assert r.status_code == 200                        # ← raises instead
```

```
psycopg.errors.ForeignKeyViolation: update or delete on table "users" violates
foreign key constraint "fk_rsvps_user_id_users" on table "rsvps"
DETAIL:  Key (id)=(3) is still referenced from table "rsvps".
```

`rsvps` is simply the first to fail. `participations`, `token_entries`,
`follows`, `projects.owner_id`, `project_leaders`, `catalog_items`,
`catalog_claims`, and now `attestations` are all reachable the same way.

The exception is not caught, so the volunteer sees a generic 500 at the exact
moment they are trying to save their account.

## Why it matters more now

Guests are first-class (`SERVICE_LOG.md` §4) and the app mints one on first run,
so the *typical* new volunteer is a guest. They check in at an event, earn tokens,
then try to "save your account" with an email they already used — and hit a 500.
The peer check-in in `CHECKIN_PROOF.md` adds `attestations` as an eighth FK of the
same shape, widening the same hole.

## Fix sketch (not done — needs its own TDD pass)

Extend the MERGE transaction to re-point every user-referencing table before the
delete, handling the ones with per-user uniqueness the way `cheers`/`reports`
already are (delete the guest's colliding rows first, then re-point the rest):

| Table | Column(s) | Collision rule |
|---|---|---|
| `rsvps` | `user_id` | UNIQUE `(event_id, user_id)` — delete colliding, re-point rest |
| `participations` | `user_id` | partial UNIQUE on open rows — delete/close colliding, re-point rest |
| `attestations` | `scanner_user_id`, `subject_user_id` | UNIQUE triple; also the `scanner <> subject` CHECK can be violated by a merge that makes both sides the same person — delete those rows |
| `token_entries` | `from_user_id`, `to_user_id` | append-only ledger — re-point, then **recompute both balances** (I1) |
| `follows` | `user_id` | UNIQUE `(user_id, project_id)` |
| `projects` | `owner_id` | straight re-point |
| `project_leaders` | `user_id` | PK `(project_id, user_id)` |
| `catalog_items` / `catalog_claims` | `poster_id` / `claimant_id` | claims have a partial UNIQUE on pending |

The ledger is the delicate part: merging two balances must preserve I1 exactly,
and `token_entries` is append-only (I2), so the merge has to re-point rather than
compensate — and then rebuild `users.balance` for the survivor.

A cheaper interim mitigation, if the full merge is deferred: catch the
`ForeignKeyViolation` and return a real error (`409 merge_not_supported` with a
message telling the volunteer to sign in on the existing account directly) so at
least it is not a 500.

## Tests to add with the fix

- guest with an RSVP merges cleanly
- guest with an open participation merges; the participation follows
- guest and target account both RSVP'd to the same event → one row survives
- guest with earned tokens merges; `users.balance` of the survivor = Σ entries (I1)
- guest who attested / was attested merges; no self-referencing attestation survives
