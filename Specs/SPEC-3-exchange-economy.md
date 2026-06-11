# CondoParkShare — Spec 3: Exchange Economy (Credits)

*v1.0 — June 2026. For Claude Code. **Layers onto Specs 1 & 2.** Build only if usage-metered pricing / resident-pay-per-use is wanted.*

> This module adds a spendable **credit** economy on top of the Spec 1 core (and coexists with Spec 2 billing). It turns "free exchanges" into "usage-priced exchanges," with an audit-grade ledger. Gated behind `credit_economy_enabled` (default **off**); when off, none of this exists and booking behaves exactly as in Spec 1. Assumes Specs 1–2 exist.

---

## 1. Concept
Adds **credits** that meter usage: booking **burns** credits, lending **earns** them. The Spec 1 listing→horizon alignment is unchanged (still non-spendable privilege); credits are a second, *spendable* track. Enables **resident-pay-per-use** as an alternative/addition to flat subscriptions.

Two distinct reward tracks, never merged:
- **Credits** (spendable) — earned from *consumed booked hours*, spent on booking.
- **Horizon/leaderboard** (non-spendable, from Spec 1) — earned from *elapsed listed hours*.

## 2. Burn / earn (mode-tied)
- `booking_cost_per_hour` (default **10**); whole hours.
- `owner_earn_per_hour`, **accrued per consumed (elapsed) hour**:
  - **full (10/hr)** when building-funded / free → community wash (operator revenue, if any, is the flat fee).
  - **half (5/hr)** when resident-pay → 50% operator spread is the revenue.
  - Overridable per tenant.
- `signup_bonus` (default **10**); `credit_floor` gates bookings (`balance − cost ≥ credit_floor`): deep (e.g. −500) in free mode, tight (0/small −) when paid.

## 3. Purchases & resident-pay-per-use
- `credit_price` $/credit (0 ⇒ disabled). Packs, hour-scaled (e.g. $5/$10/$25). **Never per-booking card charges** — bookings only decrement existing credits.
- Optional opt-in **auto-charge** (needs `credit_price > 0` + saved card): when `balance < autocharge_threshold`, buy `autocharge_pack`.
- Card handling identical to Spec 2 §4 (Stripe-hosted, store only IDs, keys as secrets, SAQ A).

## 4. FIFO lots, expiry, decay
- Credits tracked as **FIFO lots** (date + source stamped); spend oldest-first.
- **Credit expiry — FREE MODE ONLY:** `credit_expiry_enabled` auto-follows price (ON when `credit_price = 0`, **OFF when `credit_price > 0`** — avoids stored-value/gift-card expiry law, esp. Canada). A lot remainder older than `credit_expiry_days` (180) expires via an attributed `credit_expiry` entry; warn `credit_expiry_warn_days` (14) before. *Example: credits earned in May expire end of November if unused.*
- **Negative-balance decay:** a balance continuously negative for `negative_balance_decay_days` (180) resets to zero via an attributed `overage_decay` entry. Default on in free / off in paid; overridable. Any return to ≥0 clears the timer; partial-but-still-negative does not. Prevents perma-banning non-contributors.

## 5. Refunds (credit-based)
- Pre-start cancel → full refund.
- Early release → each unused whole future hour returned refunds `early_release_refund_per_hour` (default **5**); remainder forfeited → operator. Released hours re-enter inventory (rebookable; owner earns again). Keep the 5/hr haircut even in free mode (observe overbooking).
- Owner cancels booked slot → borrower **full refund, always** + owner penalty.

## 6. Free→paid conversion reset (credits)
When `credit_price` flips 0 → >0: **zero all balances** (`conversion_zeroing`) + grant `min( max(conversion_bonus_flat, conversion_bonus_pct × max(0, prior_balance)), conversion_bonus_cap )` (`conversion_bonus`); set `owner_earn` → half; turn `credit_expiry` off; enable Stripe credit purchases; notify residents ahead. Defaults: flat = `signup_bonus` (10), pct = 20%, cap = 50. Clean ledger boundary (no pre-flip credits persist).

## 7. Audit-grade credit ledger
Two ledgers, never conflated: **credit ledger** (`CreditTransaction`) and **cash ledger** (`Payment`/`Invoice` from Spec 2). Bridge: a `purchase` credit entry links to its `Payment`.
- **Append-only** `CreditTransaction` (no UPDATE/DELETE; enforce via DB perms/trigger). Corrections = new compensating entries.
- **Balance = SUM(amount)**; `balance_after` snapshot per row (per-user write serialization via row lock). Cached balances reconcile nightly.
- **Types:** `signup_bonus, purchase, booking_charge, booking_refund, early_release_refund, owner_earn, credit_expiry, overage_decay, conversion_zeroing, conversion_bonus, admin_adjustment`.
- **Hash chain:** `prev_hash` + `entry_hash = hash(content + prev_hash)` chain all entries — tamper-evidence even against a privileged DB actor; a verification job re-walks and asserts.
- **Signed anchors:** periodically (nightly + period-close) sign the chain head with the operator key (**YubiKey-held**), timestamp, store in `LedgerAnchor`, archive to NAS. RFC 3161 external timestamping = deferred hook.
- **Framing:** tamper-**evidence** + non-repudiation (detect & prove state), **not** literal immutability. State this accurately; don't over-claim.
- Admin credit actions appear as attributed `admin_adjustment` entries (no silent changes).

## 8. Reporting (credit, per-currency)
Per tenant/period, deterministic & reproducible (immutable ledger), archived to NAS with `pg_dump` + anchors:
- **Transaction register** → CSV.
- **Summary** (opening balance, totals by type, minted vs purchased vs spent, operator spread, forfeits, expired, decayed, per-user rollups, closing) → MD + CSV.
- **Reconciliation** (entry sums == balances; credits-in == out + Δ; cash collected == purchases; chain verifies) → flags discrepancies.

## 9. Data model additions
- **Organization** (extend): `credit_economy_enabled` + all §10 credit config.
- **CreditTransaction** — append-only, FIFO-lot, hash-chained ledger (fields per §7; `amount` in integer credit sub-units; `lot_ref`, `prev_hash`, `entry_hash`, `balance_after`, `actor`).
- **LedgerAnchor** — `organization`, period/timestamp, chain-head hash, signature.
- **(extend) Payment** — `purchase` credit entries link here.

## 10. Config additions
`credit_economy_enabled` (false), `booking_cost_per_hour` (10), `owner_earn_per_hour` (full 10 / paid 5), `signup_bonus` (10), `credit_floor` (deep free / tight paid), `early_release_refund_per_hour` (5), `credit_price` (0), `pack_options` ([50,100,250]), `autocharge_enabled/threshold/pack`, `credit_expiry_enabled` (auto = price==0), `credit_expiry_days` (180), `credit_expiry_warn_days` (14), `negative_balance_decay_enabled` (on free/off paid), `negative_balance_decay_days` (180), `conversion_bonus_flat` (=signup_bonus), `conversion_bonus_pct` (20%), `conversion_bonus_cap` (50).

## 11. Build order (this module)
1. **Append-only hash-chained FIFO-lot `CreditTransaction` from the first credit write** (chain, lots, `balance_after` are foundational — never retrofit). Per-user write serialization.
2. Burn/earn + `credit_floor` + `signup_bonus`; mode-tied `owner_earn` accrual (scheduled hourly job). Refunds become credit-based (replacing Spec 1's free-the-spot semantics when enabled).
3. Credit purchases + packs + opt-in auto-charge; resident-pay-per-use.
4. Credit expiry (free-mode, FIFO, warning) + negative-balance decay — scheduled jobs posting attributed entries.
5. Free→paid credit conversion (zeroing + bonus + mode flips + notice).
6. Signed `LedgerAnchor` job; chain-verification job.
7. Per-currency credit reporting (register/summary/reconciliation) → NAS archive.
