# Putting the impact token on-chain — open questions

> ⚠ **THIS IS A BRAINSTORM, NOT A DECISION.** Nothing here is settled, nothing
> here is implemented, and no code should be written from it. It lives in
> `docs/ideas/` rather than `docs/design/` on purpose: the design tree is the
> source of truth about what we are building, and this is a record of thinking
> about something we might build.
>
> Its job is to stop good questions being lost between conversations, and to make
> the *consequences* of each fork visible before anyone picks one.
>
> Started 2026-08-03, from the founder's description of the model.

---

## The model, as it stands

A token on an EVM chain, probably **Base**.

```
       SERVICE                                   REDEMPTION
   (the only door in)                         (the only door out)

  volunteer does an hour  ──mint──▶  token  ──burn──▶  business's burn total
                                       │                      │
                                       └──── gift ────▶       │
                                        (to someone who   a public, permanent
                                         needs the goods)  record of what they gave
```

- **Minted** by us, at will, as a reward for service.
- **Burned** when someone redeems it for goods from a business.
- The business **never takes possession**. The burn credits a counter against
  them; there is no moment where they hold a token and could decide to do
  something else with it.
- **No dollar peg.** Settled: this is not a stablecoin and the contract will not
  declare a rate (2026-08-03).
- Businesses get **no money**. What they get is a public, verifiable, comparable
  number: *this is how much charity we did.* Social gravity, and a game to be
  top of.
- Some earners will **give their tokens away** rather than spend them — service
  plus donation, a double good. Others genuinely need the goods, and for them
  this is closer to work than to volunteering.

## Settled so far

| | |
|---|---|
| **No dollar peg, no stablecoin.** | The contract records amounts. Any dollar equivalence is a reporting convention outside it, and a business states the retail value of what it gave. |
| **The burn record is the point.** | It is the part that actually needs a public chain: permanent, tamper-evident, comparable across businesses, and verifiable without trusting us. |
| **A listing is BINDING until withdrawn** (2026-08-03). | The stated goal is to *reliably* reward service, and reliability lives here: a token is worth something only if the thing it buys cannot evaporate at the counter. The business's control is the listing — withdraw it, or bound it with a quantity — not the individual person in front of them. |

## The one property that seems to define everything

**The only way in is service. The token cannot be bought.**

That is unusual and worth protecting deliberately, because it is what makes the
whole thing legible as charity rather than as a currency. Every open question
below is really a question about how much we are willing to spend to keep that
true.

Keeping it off exchanges is not primarily a regulatory dodge — it is what stops
there being a second door in.

---

## Open questions

Not a to-do list. Each is here with what it costs either way, so a decision can
be made once and written down properly.

### 1. Can a business say no? — ANSWERED: no. Binding until withdrawn.

The business's control is the **listing**, not the person standing in front of
them. They can withdraw it, or bound it with a quantity, but they cannot decline
an individual holder of a token.

This is the right call for the stated goal, and it creates four consequences that
are now the live questions:

**1a. It contradicts the catalog we already shipped.** `OVERVIEW.md` D9 has the
poster **accept or decline** each claim — explicitly declinable, which is the
opposite of binding. Either token-priced offers stop going through accept/decline,
or "binding" is only a promise we make in copy. That contradiction should be
resolved deliberately rather than discovered later.

**1b. Quantity stops being optional.** Today `catalog_items.quantity` is nullable
and NULL means unlimited. A binding offer with unlimited quantity is an unbounded
promise; a small business could be cleared out by one enthusiastic holder. Bounded
quantity — and possibly a per-person limit, which does not exist today — is what
makes binding survivable.

**1c. Withdrawal becomes a public act.** If binding is real, the only exit is
withdrawing the listing, and *that* is the thing worth recording. A business that
withdraws the moment redemptions arrive looks very different from one that
doesn't, and on-chain that is visible without anyone having to police it.

**1d. Binding means different things for a coffee and for four hours of dental
work.** One is handed over; the other has to be scheduled. Binding-to-schedule and
binding-to-hand-over may need different mechanics.

### 2. What actually brings a business in?

The founder's own doubt, and worth taking seriously.

Status may not be enough on its own. Two other things might be doing more work:

- **Footfall.** Somebody redeeming a coffee walks into the shop. This is coupon
  economics, and it is the non-altruistic reason a business says yes.
- **Proof.** Plenty of businesses already give things away. Almost none can
  *prove* it in a form that is identical across businesses and impossible to
  inflate. That is the thing we would be selling.

If it becomes a leaderboard: a single global ranking rewards the largest player
and demoralises everyone else. Per-neighbourhood, per-category, or tiers
("gave 100 hours' worth") keep it winnable for a café.

### 3. Is the chain for the volunteer, or for the business?

Asked before, still open, and it decides how big the first version is.

If it is for the **business** (the burn record), then businesses need wallets —
they can manage that — and volunteer balances can stay in Postgres indefinitely.
That version is small and shippable.

If it is for the **volunteer**, every guest needs a wallet, and guest-first
onboarding — the app's best feature — collides head-on with key custody. Passkey
smart accounts plus sponsored gas is the only version worth attempting, and it is
a large piece of work.

### 4. What stops fake service?

Once a token buys real goods, inventing service becomes profitable. This is what
kills systems like this.

The app already distinguishes **asserted** from **attested** presence
(`CHECKIN_PROOF.md`): a peer physically scanned your code. Today that is a pill
in the UI. If only *attested* participation mints, the distinction acquires
economic teeth and the anti-fraud story becomes "a second human was there" —
which is a genuinely strong answer for a local, in-person programme.

Related: who holds the minting key, and what limits it. A single hot key that can
mint without bound is the whole system's risk in one place.

### 5. When does the token actually burn — at claim, or at handover?

Now the sharpest question, because "binding" is only as real as this moment.

- **Burn at claim** → the holder walks in with a receipt for something already
  paid for. Strongest guarantee; the business eats no-shows and holds stock for
  people who never arrive.
- **Burn at handover** → nothing is spent until the goods change hands; no
  no-show cost. But the guarantee weakens to a reservation, and a business that
  wants out can simply be slow.

The existing catalog already leans one way — an accepted claim's screen is
described as "the proof the claimant shows the business" — but that was written
when a poster could still decline.

### 6. Does an unspent token last forever?

Every unredeemed token is a latent claim on somebody's goods. If nothing expires,
that claim grows without limit and the business community carries it.

Expiry is the standard answer and it is also a small betrayal of "you earned
this." Alternatives: no expiry but a public dashboard of outstanding supply, so
the community can see the overhang; or expiry only on tokens that have never
moved in N years.

### 7. What happens where there are no businesses yet?

Someone does service in a town with nothing to redeem against. Their tokens are
real and useless. That is the fastest way to teach a community the token is a
gimmick — and it is most likely exactly where the programme is newest.

Options: seed a region with a national partner; let tokens redeem against the
project's own goods; accept it and market only where there is a business base.

### 8. How does giving a token away actually work?

The founder expects many earners to donate rather than spend, and the app already
has tipping and "register a need." So the real flow may be:

```
earn ──▶ give to someone who needs it ──▶ they redeem ──▶ burn
```

That is lovely, and it is also the strongest argument for closed-loop transfers:
person-to-person movement must exist, but only between people, never onto a
market. Open: does a giver choose the recipient, or is there a pool?

### 9. Closed-loop is enforceable, but only mostly

A transfer allowlist genuinely prevents listing on an exchange. It does not
prevent somebody selling their account, or "send me tokens and I'll Venmo you."

Closed-loop makes a market *inconvenient*, not impossible — which is probably
enough, but should be believed accurately rather than optimistically.

The cost is worth naming too: a non-transferable token gets public verifiability
but none of the composability people expect from "on-chain." Which raises the
honest question underneath all of this — if the token never trades, what is the
chain doing that a published, signed Postgres ledger would not? (Answer, probably:
permanence and independent verifiability of *the burn record*. Which points back
at question 3.)

---

## Prior art worth an hour

This model has decades of history and the failure modes are documented:

- **Time banks** (Time Banks USA, hOurworld) — hours as currency, mutual
  exchange, explicitly non-monetary. Closest cousin, including the tax posture.
- **LETS** (local exchange trading systems) — community currencies; the
  literature on why most die is directly relevant to questions 1, 6 and 7.
- **Ithaca HOURS** — a local currency that worked for a decade and then didn't;
  the wind-down is instructive.
- **Loyalty-point accounting** — how airlines manage exactly the "unredeemed
  liability grows without bound" problem in question 6.

## The smallest first version, if we wanted one

1. Postgres stays the source of truth for volunteer balances.
2. A **burn registry** on Base: businesses register, redemptions are recorded
   on-chain, `burnedFor[business]` is public.
3. No volunteer wallets, no transfers on-chain, no exchange story.

That ships the part with the clearest value (a provable charity record), involves
only the participants who can handle wallets, and leaves every question above
open rather than answering them by accident.

## What would make me nervous

- Minting to a hot key with no cap.
- Launching before one business has said, in writing, what they will honour.
- A leaderboard that a small business can never place on.
- Anything that makes a volunteer hold a token they cannot spend where they live.
