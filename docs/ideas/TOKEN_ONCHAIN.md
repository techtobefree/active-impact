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
> **How this document is built:** one question at a time, at the broadest level
> still unresolved. Detail questions are not asked until the fork above them is
> settled — most of them turn out not to exist.
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
| **T10** | **A real ERC-20, not just a burn log.** The token exists on-chain from day one, held custodially in one treasury address while T9 holds; mint on service, burn on redemption. | 2026-08-03 | *Optionality.* If we ever decide to mint directly to volunteers, it becomes a change of custody rather than a migration — the same contract, the same balances, the same burn totals, nothing ported. Starting with a burn log and upgrading later would mean carrying historical totals into a new contract, which forfeits the permanence that was the whole reason to be on a chain. It also makes T1 publicly legible: **total supply IS service done and not yet honoured**, readable by anyone without us reporting it. |
| **T11** ✅ | **Claiming an offer settles immediately — accept/decline is removed.** The claimant claims, the price is taken, the quantity decrements, done. The poster's only control is withdrawing or bounding the listing. | 2026-08-03 | The shipped catalog (`OVERVIEW.md` **D9**) let a poster accept or decline each claim, which is the exact opposite of T6. This supersedes D9 for offers. **Note:** every offer in this app is already priced (0 = free), and needs are never claimable — so "token-priced offers" is all offers, and there is no subset to carve out. **Built 2026-08-04** — D9 struck, replaced by D9b; migration 0017. |
| **T12** ✅ | **Redemption BURNS the price. It does not pay the business.** This is how tokens leave existence (T1) and it supersedes the shipped `transfer(claimant → poster)`. **How the burn is attributed ON-CHAIN is still open — see Q2c.** | 2026-08-03 | *"It is a burn, we've decided that, this is how the tokens leave existence."* Paying the business would give them a balance to spend or tip onward, which contradicts T4, and would leave no burn total to publish — the number the whole model exists to produce. **Built 2026-08-04** — `tokens.burn()`, nullable `to_user_id`, invariant I1b. In Postgres the attribution is a join to the item's poster; the on-chain shape stays open. |
| **T13** | **There is no "business" account type. You burn FOR AN ADDRESS.** Anyone may receive burn credit; a business is just an address that happens to belong to a business. Names are claimed socially — the business tells people, publishes it, and we can show it — not granted by us. If there is no address, the tokens burn to nothing. | 2026-08-03 | *"I'm trying to remove all the barriers… you don't need to go get a business account and ask us pretty please."* Consistent with everything else here: guest-first, no email required, anyone can post a project. A gated business account would be the first place in the app where somebody has to ask permission. Address-as-identity is also the chain's own primitive, so no parallel id registry has to be kept in sync — and the record stops being captive to this platform, because another app burning to the same address adds to the same total. |
| **T14** | **Every user gets an address.** Not just businesses — every account has one, from the start. | 2026-08-04 | Keeps T13 whole: attribution always has a target, so no burn is ever orphaned and the leaderboard has no holes. It also means "business" never has to become a thing, since the only thing that distinguishes one is what its address has burned. |
| **T15** | **Users hold their own keys, in their own wallet.** Non-custodial. | 2026-08-04 | The address is genuinely theirs and outlives this platform, which is what makes T13's portability real rather than a slogan. Keeps T10's option open: minting straight to volunteers is reachable without us ever having been a custodian. |
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

**Parked, not forgotten.** The broad strokes are settled as far as the founder
wants to take them for now (2026-08-04). What is below is recorded so it is here
when it is wanted, and is not to be raised unprompted.

Each carries what the fork *costs*, so it can be answered once and promoted to §1.

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

### Q2. Does accept/decline survive T6? — ANSWERED by T11: no, it is removed. **BUILT 2026-08-04.**

Superseded `OVERVIEW.md` D9, which is now struck through and replaced by **D9b**
rather than left to contradict T6 — a decision the design tree still asserts is
worse than no decision at all.

`POST /catalog/{id}/claim` does the whole settlement and returns a claim already
`redeemed`; `/claims/{id}/accept|decline|cancel` are gone from the routing table.
Migration 0017 resolved the claims caught mid-flight: `accepted → redeemed`, and
`pending → canceled` (nothing could ever settle them once the endpoint went).

### Q2b. Does the price go TO the business, or out of existence? — ANSWERED by T12: it burns. **BUILT 2026-08-04.**

It touched the same lines as T11, which is why they were settled together.

Built as `tokens.burn()` — a debit with no credit half, sitting beside `mint()`
and `transfer()` in the one module allowed to write the ledger. The schema change
it needed: `token_entries.to_user_id` became nullable, `burn` joined the kinds,
and a CHECK pins the two together — `(kind = 'burn') = (to_user_id IS NULL)` — so
no other kind can ever be written half-formed.

The per-business burn counter is **derived, not stored**: join
`token_entries → catalog_claims → catalog_items.poster_id`. A stored counter is a
second copy of a fact that can drift from the ledger, and the ledger is the one
thing in this codebase that must never be wrong. The item page shows the poster
their tally today; the public leaderboard is Q3c and still open.

Invariant I1 (`users.balance` = Σ in − Σ out) survived unchanged; a burn is
simply an out-entry with no in. **I1b** was added to say the other half out loud:
circulating supply = Σ `earn` − Σ `burn`.

### Q2c. How is a burn attributed to a particular business? — OPEN

The founder's three options, recorded with what each costs:

| | How | Cost |
|---|---|---|
| **1. One burn address + events** | Everything burns to a single recognised address; an event names the business. | Circulating supply is obvious. Per-business totals require **indexing events** — a service to run and keep correct. |
| **2. A burn address per business** | Each business gets its own sink address. | No indexing: read the address balance. But addresses must be generated, and **no indexer knows those addresses are burns**, so circulating supply looks wrong everywhere. |
| **3. Counters in the contract** | `redeem(business, amount)` burns and increments `burnedFor[business]`. | One extra storage write. Per-business totals are a **contract call**, not an indexing job, and supply stays obvious. |

**The recommendation, if it helps: option 3, with three refinements.**

- **Use a real `_burn`, not a send to a dead address.** ERC-20 burning reduces
  `totalSupply`, so `totalSupply()` *becomes* circulating supply — exactly T1,
  readable with no subtraction and no special knowledge. Sending to `0x…dead`
  leaves the tokens counted, which is precisely the "indexers won't know" problem
  option 2 has. A true burn deletes that problem rather than managing it.
- **Emit the event as well — they are not alternatives.** The counter answers
  *"what is the total now?"* in one call with no infrastructure. The event answers
  *"what happened, and when?"* Both are wanted; events cost little, and having
  them means we are never *dependent* on an indexer for the headline number.
- **Key the counter by the business's ADDRESS, and make the registry
  enumerable.** A mapping cannot be listed — `burnedFor[x]` is readable only if
  you already know `x`. T5's league table needs *all* businesses, so the registry
  should hold an enumerable list. Otherwise the leaderboard, which is the product,
  is the one thing you cannot build from the chain alone.

Gas, so it is not a mystery: on Base a new counter slot is ~20k gas and an update
~2.9k, which at typical Base prices is a fraction of a US cent. The founder's
instinct that this is affordable is right by about three orders of magnitude.

**Still undecided.** Nothing here is chosen; the burn itself (T12) is.

### Q3. Does quantity become mandatory, and is there a per-person cap?

**Created by T6, and sharpened by shipping T11.** `catalog_items.quantity` is
nullable today and NULL means unlimited — which under T6 is an unbounded promise.
A per-person limit does not exist at all, so one holder can clear a shelf.

Building T11 made this concrete rather than theoretical: the old partial unique
index (`one live claim per item per person`) guarded the `pending` state, so when
pending went, so did the index. Redeeming the same offer twice is now simply two
redemptions, each burning real tokens. That is consistent — quantity is the
poster's bound — but it is the shelf-clearing case, now reachable. Recorded, not
raised: a per-person cap is one column and one check whenever it is wanted.

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

### Q4b. ERC-20 or only a burn log? — ANSWERED by T10: a real ERC-20.

**The design rule this implies: separate the LEDGER from the POLICY.**

T10 was chosen for flexibility, and flexibility is only preserved if the parts
that must never change are kept apart from the parts that should be free to.

- **The ledger is permanent and immutable** — balances, total supply, and
  `burnedFor[business]`. This is the thing whose continuity T10 exists to
  protect. It should not be behind an upgradeable proxy, because "permanent
  record we could rewrite" is not a permanent record.
- **The policy is swappable** — who may mint, who may hold, who may transfer,
  read through a registry contract the token points at. Today: only the treasury
  holds, and only the app mints. The day volunteers get wallets, that is a new
  registry, not a new token.

Mint must therefore be able to target **any** address from day one, even though
today it only ever targets the treasury. Otherwise the future T10 was bought to
keep open is closed by an argument list.

### Q3a. Does every user get an address? — ANSWERED by T14: yes, everyone.

Dissolves the unattributed-burn problem entirely: T13's "burns to nothing if there
is no address" becomes a case that cannot arise.

### Q3a-next. Who holds the key? — ANSWERED by T15: they do, non-custodially.

### Q3b. If names are permissionless, what stops impersonation? — OPEN

**Created by T13, and the one place it has a sharp edge.** If a business is only
an address and names are claimed socially, then name-claiming is unauthenticated:
somebody labels their address "Starbucks", and *our* leaderboard is what displays
it. Two addresses can also claim the same café.

The fix does not require gatekeeping — it requires **proof instead of
permission**, which is the same move option 3 made for burns:

- **Domain proof.** The business publishes their address at a `.well-known` path
  or a DNS TXT record on their own domain. Whoever controls `riversidecafe.com`
  proves the pairing; we approve nothing.
- **ENS.** The address has a name already; we display it. Self-service, exists
  today, no gate.
- **A signature.** Cheap, but only proves control of the *address* — not of the
  *business name*, so it does not actually solve impersonation on its own.

**And the display rule follows an idiom this app already has.** Show the claimed
name, and mark whether it is **asserted** or **proven** — exactly the
asserted-vs-attested distinction `CHECKIN_PROOF.md` already uses for presence.
Anyone may claim; a proof upgrades it; nobody is ever refused. The concept is
already in the product's vocabulary, so it costs no new idea.

### Q3c. How does the leaderboard enumerate, with no registry to join? — OPEN

**Also created by T13.** `burnedFor[address]` is readable only if you already know
the address, and there is no registry anyone signs up to.

Cheap answer that stays permissionless: the contract keeps an **append-only array
of every address that has ever received burn credit**, pushed on that address's
first burn. One extra ~20k-gas write per address, once, ever — and the league
table remains buildable from the chain alone, with nobody joining anything.

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
