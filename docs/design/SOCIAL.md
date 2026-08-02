# Social — following people, their activity, blocking, notifications (design)

> Until now the only relationship in Active Impact was person → project. This adds
> **person → person**: follow someone, see what they do (log a service, RSVP,
> check in), have that show up in your feed, keep a list of your followers, and
> **block** one so they stop seeing what you do — without losing them as a
> follower. Plus the notification layer the founder asked for: *tell me when the
> people I follow RSVP or check into something.*
>
> Read alongside [DOMAIN.md](./DOMAIN.md), [FEED.md](./FEED.md) (the one-feed
> principle this must not break) and [SERVICE_LOG.md](./SERVICE_LOG.md) (guests
> are first-class people here too).
>
> Source of intent: `../intent.md` § "Connect with others".

---

## 1. Decisions

| # | Decision | Rationale |
|---|---|---|
| **S1** | **Following a person is a new relation**, `user_follows(follower_id, followee_id)` — deliberately separate from the existing project `follows`. | Same English word, different object. Both stay what they are: an interest signal with no powers. Overloading one table to mean two things would poison every query that touches either. |
| **S2** | **Five actions are public activity**: `logged` (a service record), `rsvp`, `checked_in`, and — added 2026-08-02 — `created_project` and `scheduled_event`. Written to an `activities` row **in the same transaction** as the action itself. | The founder named the first three, but the first person you ever tap on is a project's **organizer**, and organizing was not being recorded at all: their page came up blank. Starting a project is the most visible thing anyone does here. Same-tx is the house rule already used for `audit_log`: an activity can never exist without its action, nor an action without its activity. |
| **S2b** | **Creating a project announces its first event too**, so only *later* events produce `scheduled_event`. | One action, one feed item. A project and the event it was created with are the same piece of news. |
| **S3** | **`activities` is a public projection; `audit_log` stays internal.** Two append-only tables on purpose. | An audit row is a reporting record and must never be reshaped for display; an activity row is public and is *deleted with its subject*. Merging them would tie the ledger's shape to the feed's. The duplicated write is two lines. |
| **S4** | **Blocking is a one-way visibility mute that KEEPS the follow.** They stay a follower, stop seeing my activity, and can be unblocked. They are not told. | Verbatim from the founder: *"we can block them so they can't see what we do — they remain our followers, and we can unblock them if we choose to."* Unusual (most apps drop the follow); it is what was asked for, so it is what it does. |
| **S5** | **A block covers the activity surfaces, not public project content.** Blocked people stop seeing my activity feed and my profile's activity; my photo on a project card — content *about a public event* — stays. | My activity stream is about *me*; a project's feed is about the project. Filtering public event content per viewer would also mean no cacheable public read anywhere. **Flagged**: if the founder wants a total block, that is a bigger, slower change and should be decided deliberately (§7). |
| **S6** | **Notifications are DERIVED, not fanned out.** No `notifications` table: unread = activities by people I follow, of notifiable kinds, newer than my `notifications_seen_at` watermark. | One column instead of a row per user per event. Nothing to backfill, nothing to keep in sync, and the badge can never disagree with the list it opens. |
| **S7** | **Notifiable kinds are `rsvp` and `checked_in`.** A logged service — and organizing — appears in the feed but does not ping. | *"…notified of when your friends are going to RSVP or check into things."* Photos are ambient; someone turning up somewhere is the thing worth a nudge. Per-user on/off (`notify_activity`), default **on**. |
| **S8** | **In-app notifications only** — a bell with an unread dot and a screen. Web push (permission prompts, VAPID keys, service-worker push handlers) stays deferred. | OVERVIEW.md already defers push. The bell delivers the value; push is a self-contained follow-on that needs its own pass. |
| **S9** | **Home gains a `Following` tab** rather than mixing activity into the project cards. | FEED.md F2 collapsed two feeds into one and that must hold. Activity items are a different shape from project cards; giving them a tab keeps one screen and one scroll without re-fragmenting the card. |
| **S9b** | **The follower lists expand IN PLACE on the profile card**, capped at the first 100, with the full-list page reached only via "See all N". | The founder's shape, and a good one: the common case (a handful of followers) needs no navigation at all, while the page that holds thousands is a real page with real controls. The 100 is the same cap the API already enforces, so the card never asks for more than one request. |
| **S10** | **Messaging is still not built** (OVERVIEW D8). The profile offers **Follow** and **Tip**. | The founder listed messaging as an example of "things you can do", not a requirement here. It is a whole domain (threads, delivery, moderation, abuse); naming it as still-deferred is more honest than a stub button. |
| **S11** | **Guests participate fully.** A guest can follow, be followed, block, and appear in activity. | SERVICE_LOG.md §4: a guest is a real `users` row. Excluding them would make the social layer invisible to most first-time users. |

## 2. Schema delta

```sql
-- Person → person. Distinct from `follows` (person → project).
CREATE TABLE IF NOT EXISTS user_follows (
  id          SERIAL PRIMARY KEY,
  follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  followee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_user_follows_not_self CHECK (follower_id <> followee_id),
  CONSTRAINT uq_user_follows UNIQUE (follower_id, followee_id)
);
CREATE INDEX IF NOT EXISTS idx_user_follows_followee ON user_follows(followee_id);

-- "This person may not see my activity." The follow row is untouched (S4).
CREATE TABLE IF NOT EXISTS blocks (
  id         SERIAL PRIMARY KEY,
  blocker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- me
  blocked_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- them
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_blocks_not_self CHECK (blocker_id <> blocked_id),
  CONSTRAINT uq_blocks UNIQUE (blocker_id, blocked_id)
);
CREATE INDEX IF NOT EXISTS idx_blocks_blocked ON blocks(blocked_id);

-- APPEND-ONLY public projection of what someone did (S2/S3). CASCADE on the
-- subject: deleting a service record deletes its activity, because the feed must
-- not point at something that no longer exists.
CREATE TABLE IF NOT EXISTS activities (
  id         BIGSERIAL PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- who did it
  kind       TEXT NOT NULL CHECK (kind IN ('logged', 'rsvp', 'checked_in',
                                           'created_project', 'scheduled_event')),
  event_id   BIGINT  REFERENCES events(id) ON DELETE CASCADE,
  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
  record_id  BIGINT  REFERENCES service_records(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activities_user    ON activities(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_activities_created ON activities(created_at DESC);

ALTER TABLE users
  ADD COLUMN notifications_seen_at TIMESTAMPTZ,          -- the unread watermark (S6)
  ADD COLUMN notify_activity BOOLEAN NOT NULL DEFAULT true;  -- the on/off switch (S7)
```

**Back-filled (migration 0013).** 0012 shipped with *no* back-fill, on the
reasoning that stale items would top everybody's first Following feed. **That
reasoning was wrong**, and the founder found it immediately: rows are back-filled
with their **original timestamps**, so they sort into the past exactly where they
belong — while an empty profile for everyone who has ever done anything is a
feature that looks broken.

Unlike FEED.md §8 (which declined to invent a *location* it never recorded), none
of this is guesswork: every row is derived from a fact already in the database —
`projects.created_at`, `events.created_at`, `service_records.created_at`,
`participations.checked_in_at`, `rsvps.created_at`.

Two rules keep it honest:
- an `rsvp` is back-filled only where the person **never checked in**, mirroring
  the live behaviour (a check-in silently ensures an RSVP row and stays quiet
  about it);
- every existing user's `notifications_seen_at` is stamped to `now()`, so nobody
  opens the app to a badge counting months of history.

## 3. Visibility — one rule, applied everywhere

> **Someone I have blocked never sees my activity.**

Every activity read passes through the same filter: drop rows whose author has
blocked the viewer. Concretely, `WHERE NOT EXISTS (SELECT 1 FROM blocks WHERE
blocker_id = activities.user_id AND blocked_id = :viewer)` — one clause, in one
helper, used by the profile feed, the Following feed, and the notification count
alike. A rule enforced in three places is a rule that will be enforced in two.

The viewer always sees **their own** activity (you cannot block yourself — CHECK).

## 4. API

| Endpoint | Notes |
|---|---|
| `POST /api/users/{id}/follow` | Idempotent. → `{is_following: true, follower_count}`. 409 `cannot_follow_self` |
| `DELETE /api/users/{id}/follow` | Idempotent. → `{is_following: false, follower_count}` |
| `GET /api/users/{id}/followers` 📄 | **person_card[]** (`id, display_name, is_guest, is_following` (do *I* follow them), `is_blocked` (have I blocked them — only meaningful on my own list)) |
| `GET /api/users/{id}/following` 📄 | same shape |
| `GET /api/users/{id}/activity` 📄 | **activity_card[]**, newest first, filtered by §3 |
| `GET /api/users/{id}/upcoming` | What they are doing now and next: not-over events they have an RSVP or participation for, soonest first, each with `is_here_now`. Same §3 filter — a blocked viewer gets `[]` |
| `POST /api/users/{id}/block` · `DELETE …/block` | Mine only, idempotent → `{is_blocked}`. Never touches `user_follows` (S4) |
| `GET /api/feed/following` 📄 | **activity_card[]** from everyone I follow, newest first, §3-filtered. Powers the Following tab |
| `GET /api/notifications` 📄 | `{unread, items: activity_card[]}` — items are notifiable kinds from my followees, §3-filtered; `unread` counts those after my watermark |
| `POST /api/notifications/seen` | Sets `notifications_seen_at = now()` → `{unread: 0}` |
| `PATCH /api/me` | Body gains `notify_activity?: bool` |
| `GET /api/users/{id}` | Gains `is_following`, `follower_count`, `following_count`, `is_blocked` |

**activity_card**: `{id, kind, actor {id, display_name, is_guest}, created_at,
event {id, project_id, project_title, starts_at} | null, record: record_card |
null}` — the `event` shape is exactly the one a record_card already carries, so
there is one embedded-event shape in the codebase rather than two that drift. A
`logged` activity embeds the full record card, so the Following feed shows the
photo itself rather than a line about a photo.

**person_card** never carries an email (D3), like every other public shape.

## 5. Screens

| Route | Change |
|---|---|
| `#/u/:id` **Their page** | Three bands, in this order: **information** (avatar, name, bio, joined, the ⏱🪙📋 stats, tappable follower/following counts) → **actions** (**Follow / ✓ Following**, **Tip**) → **Now & next**: where they are checked in *right now* and what they have said they are going to (`GET /users/:id/upcoming`), because that is the current information → a **labelled divider** → **Activity**, their history. |
| Section dividers | `.section-label` renders as a **rule with a label above the section it names**, everywhere in the app — the founder asked for the separation to be obvious, and for it on every page, not just this one. |
| `#/u/:id/followers` · `#/u/:id/following` | **The full-list page**, where the card hands over past 100. Holds the whole list (pages of 100 behind a Load more) and is where **sorting** lives — Recent or Name — because that is what a detail page is for. Each row is a person (tap → their page); on **my own** followers every row carries **Block / Unblock**. |
| `#/me` | The **Followers / Following card**: two tabs carrying their counts, collapsed by default. Tap one to expand its list **inside the same card** (the first 100); tap the other to switch without closing; tap the open one again to collapse back to two tabs. Past 100, the list ends with **See all N** → the full page above. Each list loads once, on first open. |
| `#/` **Home** | Tabs become **Upcoming · Following · Past · Mine**. Following renders activity cards: who, what, when, and for a logged service the photo itself. Empty state points at finding people to follow. |
| `#/notifications` | The bell's screen: notifiable activity newest-first, plus the on/off preference. Opening it marks everything seen. |
| App bar | Gains a **🔔** with an unread dot (polled with the existing version check, not a new timer). |

## 5b. Invites (2026-08-02)

The project page's **Invite** button, wired up. *"Which field to invite other
people we follow, or people that follow us."*

| # | Decision | Rationale |
|---|---|---|
| **S12** | **You may invite exactly the people in your follow graph** — those you follow ∪ those who follow you — and the server enforces it, not just the picker. | It is what was asked, and it is also the anti-abuse boundary: an endpoint that accepts arbitrary user ids is a notification-blast weapon. Your own follow graph is a natural, self-limiting audience. |
| **S13** | **An invite is directed, so it is NOT public activity.** It creates no `activities` row and appears in nobody's feed — only in the invitee's notifications. | Activity answers "what did this person do"; an invite is a message to one person. Putting it in the public stream would leak who is inviting whom to the whole app. |
| **S14** | **Notifications become a union of two sources**: activity from people I follow, and invites addressed to me. The watermark still governs unread for both. | An invite is not something a followee did — I may not even follow the inviter — so the derived-from-activity model (S6) cannot carry it alone. One extra source, same watermark, still nothing fanned out or stored twice. |
| **S15** | **You cannot invite somebody who has blocked you.** | A block means "stop reaching me". An invite is reaching them; honouring the block here costs one clause and would be glaring if it were missing. |
| **S16** | **Invites are idempotent per (project, inviter, invitee)** and the picker shows who you have already invited. Two *different* people may both invite the same person — that is two pieces of news. | Re-tapping must not re-notify, but a second person's invitation is genuinely new information. |
| **S17** | **The invite targets the PROJECT**, which is where the button lives, and the notification links there — the project page already leads with its next events. | Inviting to a specific occurrence is a reasonable future refinement; inviting to the *thing* is what the button on the project page means. |

**Schema**

```sql
CREATE TABLE IF NOT EXISTS invites (
  id         SERIAL PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  inviter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  invitee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_invites_not_self CHECK (inviter_id <> invitee_id),
  CONSTRAINT uq_invites UNIQUE (project_id, inviter_id, invitee_id)
);
CREATE INDEX IF NOT EXISTS idx_invites_invitee ON invites(invitee_id, id DESC);
```

**API**

| Endpoint | Notes |
|---|---|
| `GET /api/projects/{id}/invitable` 📄 | **person_card[]** + `invited` — everyone in my follow graph who has not blocked me, each flagged with whether I have already invited them here |
| `POST /api/projects/{id}/invite` | `{user_ids: [...]}` → `{invited: n}`. Silently skips anyone outside the graph, anyone who blocked me, and anyone already invited by me — an invite button should never error at somebody for a stale list |

**Screen.** Invite opens an inline picker under the button: the people in your
graph, a filter box once there are more than a handful, and one tap per person
that flips to **Invited ✓**. The invitee gets it in their notifications and, if
they have turned their phone on, as a push.

## 6. Invariants

| # | Invariant |
|---|---|
| **S-I1** | Nobody follows or blocks themselves (CHECK, both tables). |
| **S-I2** | Follow and block are idempotent — one row per pair, a repeat is a no-op, not an error. |
| **S-I3** | Blocking someone does **not** remove their follow: they stay in my followers list and in `follower_count`. |
| **S-I4** | A blocked viewer sees none of my activity in *any* surface — profile, Following feed, or notification count — and unblocking restores all of it exactly. |
| **S-I5** | An activity row exists **iff** its action did (same tx), and is deleted with its subject (record/event/project). |
| **S-I6** | The Following feed contains only activity from people I follow, never my own. |
| **S-I7** | `unread` = notifiable activity from my followees, after my watermark, §3-filtered; `POST /notifications/seen` makes it exactly 0. |
| **S-I8** | No follower/following/activity shape ever exposes an email. |
| **S-I9** | Organizing (`created_project`, `scheduled_event`) reaches followers' feeds but never the bell — the badge is for people turning up, not for admin. |
| **S-I11** | An invite reaches only the invitee: it creates no activity row and appears in no feed. |
| **S-I12** | Only people in my follow graph can be invited, enforced server-side; anyone who blocked me cannot be. |
| **S-I13** | Re-inviting the same person to the same project changes nothing and re-notifies nobody. |
| **S-I10** | Back-filled activity carries the timestamp of the thing that actually happened, and re-running the migration adds nothing (every insert is NOT EXISTS-guarded). |

## 7. Deliberately deferred (with the reasoning, so it can be revisited)

- **A total block** (S5) — hiding my *content* (photos on project cards), not just my activity. Bigger and per-viewer; the founder should decide it deliberately rather than inherit it.
- **Web push** (S8) — the bell first; push is its own pass.
- **Messaging** (S10, D8).
- **Mutual-follow "friends"**, follow requests, private accounts — following is one-way and public here.
- **Muting** (I stop seeing *them* without unfollowing) — the inverse of a block. Nobody has asked for it; unfollow covers it.
- **Notification digests / email** — no email addresses are collected for guests, and there is no sender.
