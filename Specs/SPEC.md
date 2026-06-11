# CondoParkShare — Specification Set (Index)

*v1.0 — June 2026. Pilot tenant: Bellevue Towers HOA (admin: Columbia Hospitality).*

The build is split into **three stacked specs** so concerns aren't comingled. Each layers onto the previous **without changing its behavior**; build them in order, only as far as you need.

| # | Spec | Adds | Build when |
|---|---|---|---|
| 1 | **[SPEC-1-pilot.md](SPEC-1-pilot.md)** | The whole working app: accounts, listing, hourly booking, the listing→prebooking alignment incentive, admin, notifications, PII. **No money anywhere.** | **Now** — ship for Bellevue Towers (free forever). |
| 2 | **[SPEC-2-subscriptions.md](SPEC-2-subscriptions.md)** | Flat-fee **billing**: payer models (building- or resident-funded), promo→subscription, Stripe (cards off-server, store only IDs), multi-currency. Exchanges still free to residents. | On expansion to paying buildings. |
| 3 | **[SPEC-3-exchange-economy.md](SPEC-3-exchange-economy.md)** | The spendable **credit economy**: burn/earn, FIFO lots, expiry/decay, resident-pay-per-use, and an audit-grade hash-chained ledger. | Only if usage-metered pricing is wanted. |

**Pairing:** all three pair with the **design pack** (`DESIGN.md` + `tokens.css` + logos) for the visual/UX layer.

**Key principle across the set:** Bellevue Towers runs **`payer_model = free_forever`** with `credit_economy_enabled = false` — so the pilot (Spec 1) is the complete product for BT, and Specs 2–3 are dormant/unbuilt until a real reason to add them appears. The multi-tenant foundation is built in Spec 1 so later tenants/modules need no rewrite.

**Supporting documents (separate):** HOA info sheet, resident info sheet, Columbia conversation guide, and the design notes.
