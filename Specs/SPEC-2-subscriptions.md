# CondoParkShare — Spec 2: Flat-Fee Subscriptions

*v1.0 — June 2026. For Claude Code. **Layers onto Spec 1 (Pilot).** Build only when expanding beyond the free pilot.*

> This module adds **billing** to the Spec 1 core. It does not change any Spec 1 behavior. Bellevue Towers stays `free_forever` (no billing) even after this module ships; billing applies only to tenants configured for it. Assumes everything in Spec 1 exists (tenants, accounts, listing, booking, horizon, admin).

---

## 1. What this adds

A subscription billing layer so a tenant can be funded without per-use charges. **Exchanges stay free to residents** in all modes — billing is a flat subscription, not usage pricing. (Usage/credit pricing is a separate concern — see Spec 3.)

**Payer model is chosen at tenant setup** — three cases:

| `payer_model` | Who provides a card | Who is billed | Residents pay? |
|---|---|---|---|
| **`free_forever`** | nobody | nobody | never (Bellevue Towers) |
| **`building_funded`** | the payer only (HOA / manager / designated) | the payer, **per unit** | never |
| **`resident_funded`** | each resident at registration | each resident, **flat rate** | yes |

## 2. Promo → subscription

- **Promo period** (building_funded & resident_funded): free for everyone first, to build the listing pool.
- Stored as a **nullable `promo_ends_at` timestamp** (set = signup + `promo_free_days`, default **90**). **`free_forever` and `promo_ends_at = NULL` mean no expiry** — the conversion job never computes an end date. Do **not** encode "forever" as a large integer (overflow trap); use null.
- **Conversion at promo end:** notify `conversion_notice_days` (default **7**) before billing starts → begin the configured subscription.
  - `building_funded`: per-unit subscription/invoice to the payer = `unit_count × hoa_subscription_rate` (default **$1/unit/mo** — low end of the $1–5/unit HOA-software market).
  - `resident_funded`: flat `resident_subscription_rate` per resident (predictable; decoupled from unit_count).
- **Card collection:** only from whoever will be billed — the payer in building_funded; each resident in resident_funded; nobody in free_forever. Minimizes signup friction where residents never pay.

## 3. Consent & compliance (card-on-file → later charge)
Regulated (US FTC, EU, Canada). Build all of:
- **Affirmative disclosure** at card capture: "Free until [date], then $[X]/[period]; cancel anytime."
- **Pre-billing notice** before the first charge; **easy cancellation** before conversion (no charge if cancelled during promo).
- `resident_funded` is consumer recurring billing (chargebacks, failed-card dunning, cancellation handling) — the heaviest mode; treat accordingly.

## 4. Payment data handling (PCI) — important
- **Card data is never received or stored server-side.** Collection is via **Stripe-hosted Checkout/Elements** (browser → Stripe directly). PCI scope = lightest self-assessment (SAQ A).
- **Store only Stripe IDs** — `customer` (`cus_…`), `payment_method` (`pm_…`), `subscription` (`sub_…`). These are opaque references, **not secrets**; they need **no field-level encryption** — protected by the existing volume encryption + access control + admin audit log (treat like a username, not a password).
- **Stripe secret & webhook-signing keys** are managed as secrets (`.env`/secrets store, **never** DB/repo) — same regime as the encryption keys in Spec 1.
- Handle Stripe **webhooks** for subscription lifecycle (payment succeeded/failed, cancellation) with signature verification.

## 5. Multi-currency
- `currency` per tenant (ISO 4217; USD default, CAD etc.). Denominates subscription rates and invoices. Stripe handles the currency natively at charge time — **no FX/conversion on your side**; each tenant is single-currency.
- Money stored as **integer minor units, always currency-tagged** (never floats, never bare amounts).
- Reports/totals are **per-currency**; never blend currencies. Cross-currency rollups = future hook.

## 6. Data model additions
- **Organization** (extend): `hoa_subscription_rate`, `resident_subscription_rate`, `promo_ends_at` (nullable), `promo_free_days`, `conversion_notice_days`, `currency`. (`payer_model`, `unit_count` already exist from Spec 1.)
- **Subscription** — tenant or resident; Stripe `sub_…` ref; `currency`; rate; status; `current_period_end`.
- **Invoice / Payment** — billing records; Stripe refs; `currency` + integer minor units.
- **User** (extend): Stripe `customer`/`payment_method` refs (only populated for payers).

## 7. Config additions
| Key | Default | Meaning |
|---|---|---|
| `payer_model` | free_forever | free_forever / building_funded / resident_funded (already in Spec 1 model; now actionable) |
| `currency` | USD | ISO 4217 |
| `hoa_subscription_rate` | $1.00 / unit / mo | building_funded |
| `resident_subscription_rate` | (per tenant) | flat $/resident/mo (resident_funded) |
| `promo_ends_at` | NULL | nullable timestamp; NULL ⇒ no expiry |
| `promo_free_days` | 90 | computes `promo_ends_at` at setup |
| `conversion_notice_days` | 7 | pre-billing warning |

## 8. Flows added / changed
- **Tenant setup:** operator picks `payer_model` + `currency` + rate; sets `promo_ends_at` (or null for free_forever).
- **Promo conversion:** timer reaches `promo_ends_at` → notify residents/payer → begin subscription for the configured payer (Stripe). building_funded → one per-unit subscription to the payer; resident_funded → per-resident subscriptions.
- **Card capture:** building_funded → from the payer at setup; resident_funded → from each resident at registration, with the §3 disclosure. free_forever → no card step.
- **Notifications added:** promo-ending / pre-billing notice; subscription receipt; payment-failure / dunning.
- **Manager portal additions:** billing status, subscription state, invoice history (view/export). Operator console: manage subscriptions/invoices, switch a tenant's `payer_model` (a deliberate change, e.g. HOA stops funding → switch to resident_funded, residents then prompted for cards).

## 9. Build order (this module)
1. Extend `Organization`/`User` with billing fields; add `Subscription`/`Invoice`/`Payment` (currency-tagged minor units).
2. Stripe integration: Checkout/Elements (card off-server), customer + payment-method creation, secret/webhook keys as secrets.
3. Payer-model setup in operator console; nullable `promo_ends_at`.
4. Conversion job (timer → notify → start subscription); webhook handling (success/failure/cancel) with signature verification.
5. building_funded per-unit subscription; resident_funded per-resident flat subscription.
6. Consent disclosure, pre-billing notice, cancellation flow.
7. Billing notifications; manager-portal billing views; operator invoice/subscription management.
8. Per-currency billing reporting.
