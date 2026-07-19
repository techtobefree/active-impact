# Active Impact — Interpreted MVP

> **Claude's interpretation** of the user's intent — for discussion and subject to
> correction. The authoritative, word-for-word source is
> [`intent.md`](./intent.md). Domain terms defined here are the language we build
> in; the UI can relabel anything later.
>
> **Status:** the open questions at the bottom are now **resolved** — see the
> binding-decisions table in [`design/OVERVIEW.md`](./design/OVERVIEW.md) (D4–D10).

---

## Mission

**Active Impact** is a service-project non-profit and platform. It brings together
two sides:

- **Those with a need or a service project** — people or groups who need **labor,
  goods, or services**.
- **Those who want to give** — volunteers offering their **time, expertise, or
  goods**.

Focus is **local service projects**, typically. Key reframe: *having a service
project can simply mean having a need.* So at its core the platform matches
**needs/projects** with **people willing to help**.

## Guiding philosophy — essentialist

- **Simple, minimalistic, basic.** Build the scaffold; improve it over time **as
  needs arise**.
- **No premature complexity.** Don't add handling or assumptions we don't have to
  right now.
- **Continually reduce complexity** as we go.
- MVP should **look good, feel inviting, and be slick** — but **not
  over-engineered**. Branding, animations, and polish come **later** ("skin it
  nicely later"). Right now we want the basic structure with all basic needs met
  efficiently and effectively.

---

## Domain model (the language we use)

- **User** — a person (or group) on the platform. Depending on context a user may
  be a volunteer, a project host/leader, a catalog poster, or someone in need.
- **Impact Project** — a service project. Has a **time**, a **place**, and an
  **expected duration**. Named "impact project" because the non-profit is *Active
  Impact*. A project can be as simple as a stated **need**.
- **Impact Token** — a token of appreciation. Just **points in our Postgres
  database**, tracked by us. This is the domain term (UI may relabel).
  - **Earning rate: 1 token per 1 hour** of participation.
  - **Flat for everyone (for now):** skilled or unskilled labor, volunteers of any
    kind, and leaders all earn the **same** rate. (Changeable later — no
    complexity now.)
  - **No monetary value at start.** May gain one someday, but only if the market
    decides; any such value would reflect how the populace values charity work and
    **social capital**. This is beside the point of the system.
  - **Internal only for now** — no blockchain, no money. (May move to a blockchain
    or gain other properties later; not now, because internal is simpler.)
- **Leader** — a user designated as running/leading a project (the host, or a
  volunteer acting as leader). Volunteers check in with a leader.
- **Waiver** — legal text a project specifies in the app. Agreeing to it is the
  volunteer's **signature** for now.
- **Check-in / Check-out** — records participation and **time**.
- **Catalog** — a separate domain of **needs and offers** for goods/services (see
  below).

---

## Three core capabilities (this is the MVP)

The app does three things, roughly in three layers.

### 1. Social platform (foundation)

- Users, **registration**, and **login**.
- Auth is simple: **username + password**.
- The basic foundation of a social app.

### 2. Service projects + check-in flow (the heart)

- **Browse/experience projects** — kept simple: time, place, expected duration.
- **Post a service project**, including **images**.
- **Check-in flow:**
  1. At the project, a volunteer checks in with a **leader**.
  2. They **scan a QR code** on their phone, then tap **"I agree."**
  3. "I agree" = agreeing to (signing) the project's **waiver** — that is the
     signature for now.
  4. This **checks the volunteer in**.
- **Check-out flow** — so we can record **how long** they participated.
- **Time recording matters:** it is how we price the token reward (1 token/hour).

### 3. Catalog (goods & services)

A different domain: posting **goods/services** rather than service projects.

- **Register a need** OR **post an offer.**
  - e.g. a dentist offers "4 hours of charity work a month" — it sits in the
    catalog; they **earn impact tokens** for doing it.
  - e.g. a business with extra food posts it, **prices it in impact tokens**;
    people **spend tokens** to receive it.
- Items can have a **price in impact tokens**.
- A price can also be a **coupon** — e.g. "use your impact tokens for 50% off
  product XYZ."
- If someone has **no tokens**, they can **register as needing tokens** to
  participate in the catalog.
- People who earn tokens on service projects but don't need them often **donate**
  them so those in need can get food, etc.

### (plus) Token tipping — small feature

- Users can **tip / donate impact tokens** to other holders.
- It is **basic accounting**.

---

## Impact-token accounting (summary)

A ledger of points in Postgres:

- **+1 token** per hour of verified participation (check-in → check-out).
- **Transfers:** tip/donate between users; spend on catalog items; donate to those
  registered as "in need."
- **Internal only** for now — no external value, no blockchain.

---

## Tech direction (deliberately simple)

- **PWA** — so it is **immediately available to anybody** (installable web app, no
  app-store friction).
- **Frontend:** a lightweight **JS web app** (specific framework TBD — pick the
  lightest option that cleanly supports a PWA).
- **Backend:** **Python + FastAPI** — chosen because it's simple and easy.
- **Database:** **PostgreSQL**, in its **own Docker** container.
- **Reverse proxy / web server:** **Caddy**.
- **Containers:** web server and database each **dockerized** (separate
  containers).
- **Hosting:** a **VM** we will provision.
- Overall: the simplest thing that works — **scaffold first**.

---

## Deliberately deferred (not now)

- Impact tokens on a blockchain or with any external / monetary value.
- Heavy branding, animations, and visual polish.
- Any differentiated token pricing (skilled vs. unskilled, leader vs. volunteer).
- Anything that adds complexity we don't strictly need for the MVP.

---

## Open questions for the design phase

*(Not scope additions — just genuine ambiguities to resolve when we write
`docs/design/`.)*

1. **QR direction at check-in:** does the volunteer scan the leader's/project's
   code, or does the leader scan the volunteer's code?
2. **Check-out mechanism:** a second scan, a tap in the app, or auto after the
   expected duration?
3. **Need vs. project vs. catalog need:** a "need" can be an impact project *or* a
   catalog entry — how do these relate / where's the boundary?
4. **Groups:** are groups/organizations a first-class entity, or just users, for
   the MVP?
5. **Catalog settlement:** how is a token "purchase" confirmed (does the poster
   confirm handover before tokens transfer)?
6. **Frontend framework:** the description leaves the exact JS stack open — decide
   during design, favoring the lightest PWA-friendly option.
