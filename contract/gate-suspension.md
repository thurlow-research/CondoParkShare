# Gate Suspension Register

Human-only register of temporarily suspended oversight gates (HOS#62, ratchet
principle). A gate listed here as `SUSPENDED:` is skipped (exits 0, non-blocking)
by its gate script and reported by `suspension_manager.py`.

**Ratchet principle:** only a human may *add* a `SUSPENDED:` line. The system may
auto-*remove* one after N consecutive passing checks (auto-tighten), never add one.

Authorized by: Scott Thurlow

> ⚠️ Format note: the gate hook (`check_suspension.sh`) matches an **exact**
> `SUSPENDED: <gate>` line with **no trailing flags** (`grep "^SUSPENDED: <gate>$"`).
> `review-by:` / `[pinned]` flags are honored by `suspension_manager.py` but would
> cause the gate hook to *ignore* the suspension. So review-by dates are recorded as
> prose below, not as line flags. (Tracked: HOS field report on the parser mismatch.)

## Currently suspended

SUSPENDED: portability

| Gate | Reason | Authorized | Review by | Tracking |
|------|--------|------------|-----------|----------|

**Re-enable plan:** fix #101 (replace `mapfile` with a portable `while read` loop),
then let `suspension_manager.py --check` record passes; after 3 consecutive passes
the suspension auto-removes (ratchet auto-tighten) and logs to the Re-enable log.

## Re-enable log

<!--
Auto-appended by suspension_manager.py when a gate is auto-removed after
consecutive passing checks. Format:
| gate | date | reason | actor |
-->
