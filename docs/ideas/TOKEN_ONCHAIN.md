# The impact token, on-chain — domain decisions and open questions

> ⚠ **NOT YET A DESIGN.** No code follows from this document, and it lives in
> `docs/ideas/` rather than `docs/design/` deliberately: the design tree is the
> source of truth about what we are *building*, and we are still deciding what
> this should be.
>
> **But the decisions in §1 ARE decisions.** Every one was made explicitly by the
> founder, is dated, and carries the reasoning that produced it. When this moves
> into `docs/design/`, §1 moves with it unchanged. Nothing in a conversation
> about the domain should have to be asked twice.
>
> Started 2026-08-03.

---

## 1. Decision log

Numbered `T`, in the style of the design tree's `D` / `F` / `S` / `L` / `P`
decisions, so they can be cited from code and from other docs.

| # | Decision | Date | Why |
|---|---|---|---|
| **T1** | **The token comes into existence through service and goes out of existence through purchase.** Minted as a reward for service; burned when redeemed for goods. Nothing else creates or destroys it. | 2026-08-03 | The founder's whole model in one line. It makes outstanding supply mean something exact: *service done, not yet honoured.* No reserve to manage, no treasury to defend. |
| **T2** | **The only door in is service — the token cannot be bought.** | 2026-08-03 | *"It's charity that nobody earns the token."* This is what makes the thing legible as charity rather than as currency. Most other questions here are really "what are we willing to spend to keep T2 true?" |
| **T3** | **No dollar peg. Not a stablecoin.** The contract records amounts only; it never declares a rate. | 2026-08-03 | A contract that declares a dollar value has written a liability. Any dollar equivalence is a reporting convention outside the chain, and the retail value of goods given is the business's own statement. |
| **T4** | **The business never takes possession of tokens.** Redemption burns from the holder and credits a counter against the business in the same transaction. | 2026-08-03 | *"As automated as possible so nobody gets to make any decisions that might be negative."* Achieved by removing the step rather than policing it: there is no moment where a business holds a token and could do something else with it. |
| **T5** | **Businesses are paid in social gravity, not money.** The public, comparable, tamper-evident burn total is the entire return. | 2026-08-03 | They get no revenue from honouring tokens. What they get is proof — of a kind that is identical across businesses and impossible to inflate — plus the footfall of people coming in to redeem. |
| **T6** | **A listing is BINDING until withdrawn.** A business cannot decline an individual token holder. Their control is the listing: withdraw it, or bound it with a quantity. | 2026-08-03 | The stated goal is to *reliably* reward service. A token is worth something only if what it buys cannot evaporate at the counter, and one refusal in front of a friend does more damage than ten smooth redemptions repair. |
| **T7** | **The token burns at CLAIM, not at handover.** | 2026-08-03 | Binding is only as real as this moment. Burning at claim means the holder walks in with a receipt for something already paid for, and a business that wants out cannot simply be slow. The cost is no-shows — accepted in T8. |
| **T9** | **The chain is for the BUSINESS, not the volunteer.** The burn record goes on-chain; volunteer balances stay in Postgres. No volunteer wallets, no keys for guests, no change to onboarding. | 2026-08-03 | The burn total is what a business is actually paid in (T5), and it is the only part that needs to be permanent and publicly checkable. Businesses can manage a wallet; a guest who has not even given us an email cannot, and guest-first onboarding is the best thing about the app. |
| **T8** | **An uncollected claim stays burned.** No expiry, no refund, no pickup confirmation. The business keeps the credit even if the goods never left the shelf. | 2026-08-03 | *"We're essentialists here — we're doing the simplest solution and we can evolve into stuff like that later."* Every alternative buys accuracy with a mechanism: expiry means re-minting and a counter that can go **down**, so "burned" would stop being final; confirmation adds a step to every redemption to fix a case that may be rare. Ship the simple thing, watch the drift, and only pay for accuracy if it turns out to matter. |

### Leaning, but NOT decided

Recorded separately so nobody mistakes a preference for a decision.

| | Where it stands |
|---|---|
| **Closed-loop transfers (no exchanges)** | The founder likes it — *"that's kind of cool… maybe that's not a good idea, I don't know."* It follows naturally from T2, since an exchange is a second door in. Not decided. |
| **Base as the chain** | *"probably Base."* Nothing depends on it yet. |

---

## 2. The model these decisions describe

```
       SERVICE                                   REDEMPTION
   (the only door in — T2)                    (the only door out)

  volunteer does an hour  ──mint──▶  token  ──burn──▶  business's burn total
         (T1)                          │        (T4)          │
                                       └──── gift ────▶       │
                                        (to someone who   public, permanent,
                                         needs the goods)  comparable (T5)
```

Some earners will give tokens away rather than spend them — service plus
donation, a double good. Others genuinely need the goods, and for them this is
closer to work than to volunteering. Both paths end in a burn.

---

## 3. Open questions

Each with what the fork *costs*, so it can be answered once and promoted to §1.

### Q1. What happens to a claim that is never collected? — ANSWERED by T8: nothing.

**The accepted cost, stated plainly:** a business's burn total can drift above the
goods it actually handed over, and that total is the product (T5).

**Why the drift is smaller than it looks, and self-limiting:** inflating it costs
real tokens, and under T2 the only way to get a token is to do service. A business
wanting to fake a big number would have to find people willing to burn genuinely
earned hours on goods they never collect. The economics defend themselves — which
is what makes the essentialist answer the right one here rather than merely the
cheap one.

**Revisit if:** the gap between claims and collections becomes visible enough that
businesses complain about each other's numbers, or somebody is caught farming it.
Both are observable without building anything now — the claim and the collection
are already separate events in Postgres.

### Q2. Does the existing accept/decline flow survive T6?

**A live contradiction with shipped code.** `OVERVIEW.md` D9 has the poster
**accept or decline** each claim; T6 says they cannot decline. Either
token-priced offers bypass that step entirely, or "binding" is only copy.

### Q3. Does quantity become mandatory, and is there a per-person cap?

**Created by T6.** `catalog_items.quantity` is nullable today and NULL means
unlimited — which under T6 is an unbounded promise. A per-person limit does not
exist at all, so one holder can clear a shelf.

### Q4. Is the chain for the volunteer, or for the business? — ANSWERED by T9: the business.

Three consequences, one of them a free win and one of them a thing to be honest
about.

**4a. It protects T2 for free.** With no volunteer wallets there is no way for a
volunteer to list tokens on an exchange, because there is nothing to list. The
closed-loop transfer allowlist we were leaning towards stops being necessary
machinery and becomes a property of the architecture. *(This nearly moots the
"closed-loop" item in the Leaning table — see Q10.)*

**4b. Only the app can write to the chain.** A volunteer has no key, and a
business must not hold one for this purpose either: if the business had to sign
the redemption, they could decline to sign, which would undo T6 and T7 and put a
negative decision back in exactly the place T4 removed it from.

So the app's server key writes every record. **Be accurate about what that
buys:** the chain gives permanence, timestamping, and tamper-evidence — nobody,
including us, can quietly rewrite history — and it lets each business verify its
own total against what it actually gave. It does **not** make the record
trustless. We could write a false entry; we just could not un-write it.

That is still worth having, and it is worth saying out loud rather than letting
"on-chain" imply more than it delivers.

**4c. The volunteer-side questions get much cheaper.** Expiry (Q6), donation
mechanics (Q8) and per-person caps (Q3) are all now ordinary Postgres features
that can change whenever we like, because no contract encodes them.

### Q4b. Is there actually an ERC-20, or only a burn log?

**Created by T9, and it decides what the contract IS.**

- **A burn log only** → one contract, `burnedFor[business]`, plus events. No
  token exists on-chain at all; "the token" is a Postgres integer and the chain
  is a public, permanent record of redemptions. The most essentialist version.
- **A real ERC-20, custodially held** → the app holds one treasury address;
  service mints on-chain, redemption burns and credits the business. Postgres
  still maps who owns what. Costs little more, and buys one genuinely nice
  property: **total supply becomes the public measure of T1** — service done and
  not yet honoured, visible to anyone, without us reporting it.

### Q5. What stops fake service?

Once a token buys real goods, inventing service becomes profitable — this is what
kills systems like this. The app already separates **asserted** from **attested**
presence (`CHECKIN_PROOF.md`): a peer physically scanned your code. If only
*attested* participation mints, that distinction gains economic teeth and the
anti-fraud story becomes "a second human was there."

Related: who holds the minting key and what bounds it. One unlimited hot key is
the whole system's risk in a single place.

### Q6. Does an unspent token last forever?

Every unredeemed token is a latent claim on somebody's goods, growing without
limit. Expiry is the standard answer and a small betrayal of "you earned this."
Alternatives: no expiry but a public dashboard of the overhang; or expiry only for
tokens that have never moved in N years.

### Q7. What happens where no business accepts them yet?

Someone does service in a town with nothing to redeem against — real tokens, no
use — and that is most likely exactly where the programme is newest. Seed with a
national partner, redeem against the project's own goods, or only launch where
there is a business base.

### Q8. Who chooses the recipient when a token is given away?

The donation path may be the common one: earn → give to someone who needs it →
they redeem → burn. Open whether the giver picks a person, or gives into a pool.

### Q9. How binding is "binding" for something scheduled?

A coffee is handed over; four hours of dental work has to be booked.
Binding-to-hand-over and binding-to-schedule may need different mechanics.

### Q10. Closed-loop is enforceable, but only mostly

A transfer allowlist genuinely prevents listing on an exchange. It does not
prevent selling an account, or "send me tokens and I'll Venmo you." It makes a
market inconvenient, not impossible — worth believing accurately.

The honest question underneath: if the token never trades, what is the chain doing
that a published, signed Postgres ledger would not? Probably permanence and
independent verifiability *of the burn record* — which points back at Q4.

---

## 4. Prior art worth an hour

Decades of history here, and the failure modes are documented.

- **Time banks** (Time Banks USA, hOurworld) — hours as currency, explicitly
  non-monetary. Closest cousin, including the tax posture.
- **LETS** (local exchange trading systems) — the literature on why most die
  speaks directly to Q6 and Q7.
- **Ithaca HOURS** — a local currency that worked for a decade and then didn't.
- **Loyalty-point accounting** — how airlines manage the unredeemed-liability
  problem in Q6.

## 5. The smallest first version, if we wanted one

1. Postgres stays the source of truth for volunteer balances.
2. A **burn registry** on Base: businesses register, redemptions are recorded,
   `burnedFor[business]` is public.
3. No volunteer wallets, no on-chain transfers, no exchange story.

Ships the part with the clearest value, involves only the participants who can
handle wallets, and leaves every question above open rather than answering them
by accident.

## 6. What would make me nervous

- A minting key with no cap.
- Launching before one business has said in writing what it will honour.
- A leaderboard a small business can never place on.
- A volunteer holding tokens they cannot spend where they live.
