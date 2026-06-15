# CondoParkShare — Security Measures (for posterity)

*A living record of the security controls we put in place and **why** — so a future maintainer can understand the intent, not just the config. This is a posture/rationale document; the authoritative mechanics live in the referenced ADRs, runbooks, and code.*

Last updated: 2026-06-14.

---

## Cross-cutting principles

These shape every decision below:

1. **Maximum hardening regardless of network exposure.** "It's only on the LAN / not on the internet" is **not** a reason to relax. A LAN-only box still faces lateral movement (an attacker already inside), insider/supply-chain risk, and "today's LAN-only box is one misconfigured route away from exposed." LAN-only is *one layer among many*, never grounds to downgrade a finding.
2. **Defense-in-depth.** No single control is load-bearing. Each asset sits behind several independent layers (access control + transport encryption + at-rest encryption + least-privilege perms), so the failure of any one doesn't expose the asset.
3. **Least privilege.** Dedicated accounts per purpose, restricted file modes, credential scoping — each component holds only the secrets and access it actually needs.
4. **Encrypt in transit *and* at rest.** Transport encryption (`seal`, TLS) and content encryption (`age`) are different protections at different layers; we use both.
5. **Fail-closed on security; fail-open only where availability demands it — and then with recovery.** Security-relevant errors stop. The one deliberate fail-*open* (audit logging) is paired with a durable, reconcilable recovery trail.
6. **Secret custody.** Secrets never live in the repo, in process argv, or in logs. Key material is `0600` root-only at minimum; the most sensitive (the backup decryption key) is held **offline**.
7. **Human approval + disclosure.** Security-relevant changes (firewall, auth, permissions, crypto) require human approval; AI-authored PRs carry explicit disclosure.

---

## 1. Audit logging — fail-open with recovery (CPS#78 / PR #88)

**Measure:** Operator-impersonation actions are audited to `AdminAuditLog`. If that DB write fails, the action **proceeds** (fail-open, so audit-system trouble can't block operators), **but** a structured JSON **recovery record** is emitted to a durable JSONL sink and later reconciled by the idempotent `backfill_audit_log` command.

**Why:** A fail-*closed* audit would turn any audit/DB hiccup into an operator outage. Fail-open keeps operators working; the recovery trail + reconciliation means **no privileged action goes permanently unaudited**. The reconstruction is anti-forgery validated (superuser actor, allow-listed action, original timestamp preserved, and same-org **when the recovery record carries a resolvable organization** — records with a null `organization_id` are reconciled without the tenant check) so the recovery path can't be used to fabricate audit history. Double-fault hardening (nested `try/except`) guarantees the request always proceeds even if the recovery emit itself fails.

---

## 2. Backups → NAS (jukebox) (CPS#91 / #48 / #63)

The backups carry PII (`pg_dump`: names, emails, units, phones; audit-recovery JSONL: actor/org IDs + request paths), so this stack is layered deliberately:

| Layer | Measure | Why |
|---|---|---|
| **Who can reach the share** | **Dedicated app-only SMB account** (`cps`); no shared/personal accounts — admin-only otherwise | Least privilege; a leaked personal credential can't reach CPS backups, and the CPS account is designed to reach **only the CPS share** (enforced at NAS configuration time, not by this repo) |
| **Environment isolation** | Separate shares per env: `CondoParkShare` (prod→opus), `CondoParkSharePPE` (ppe→faberix) | Prod/ppe blast-radius separation; no cross-env exposure |
| **No comingling** | CPS backups go only under `/mnt/cps-backup`, **never** `/mnt/nas/backups/parkshare` (personal) | Keeps app data and personal data on separate trust boundaries |
| **In transit** | SMB **3.1.1 + `seal`** (SMB3 encryption) | Encrypts the dumps on the wire across the LAN |
| **Resolution integrity** | NAS pinned in `/etc/hosts` (IPv4 + IPv6 ULA), mounted by name; **no mDNS/DNS in the path** | mDNS/DNS are spoofable on a LAN — pinning prevents a rogue responder from redirecting the *backup target* to a capture host |
| **Local access** | Root-only mount: `uid=0`, `file_mode=0600`, `dir_mode=0700` | Only root (the backup runner) can read the mounted backups locally |
| **Credential at rest** | SMB creds in `/etc/cps-backup.cred`, `0600` root-only | The share password is protected on the host |
| **At-rest contents** | **`age` (asymmetric, encrypt-only):** `pg_dump` + audit-recovery JSONL piped through `age -r <pubkey>`; **private identity held offline**, never on opus/faberix | Closes the gap the ACL structurally cannot: a **compromised app host already holds the `cps` share creds**, so only encrypt-only-with-a-public-key keeps it from reading the backups it produces. Also covers NAS-admin, disk theft, and PII-at-rest (GDPR). |
| **Resilience** | fstab `nofail` + `x-systemd.automount` | A NAS that's down can't hang the host's boot; the share mounts on first access |

**Critical operational control:** the **private `age` identity must be stored offline** (password manager + an offline/printed copy). Encrypt-only means *nobody* — including us — can decrypt without it; lose it and every backup is unrecoverable ciphertext. **Key custody is the real work here, not the encryption.**

---

## 2a. Live data at rest — disk encryption (CPS#104)

The PII the app *serves* (the live Postgres DB + `audit_logs` volume) is protected at rest in **two layers**:

| Layer | Measure | Protects against |
|---|---|---|
| **Host (owned hardware)** | **BitLocker on `nexus`** — encrypts the whole VHDX at rest, transparent to the VM (CPS#104) | physical theft of nexus's disk |
| **Guest (defense-in-depth)** | **LUKS2 on a dedicated data disk** (`/dev/sdb` → `/mnt/cps-data`), **TPM2 auto-unlock** sealed to the VM's vTPM, holding `/var/lib/docker` | a **copied/exported VHDX** (the vTPM key doesn't travel with the copy) — and the off-owned-hardware case where host encryption isn't ours to trust |

**Key model:** a **passphrase keyslot = offline recovery key** (password manager / offline, never on the VM); a **TPM2 keyslot = unattended auto-unlock** (boots with no passphrase, but a copied disk won't unlock). Both hosts use **`x-systemd.automount`** (a plain boot-mount doesn't reliably fire for a late-unlocking LUKS device); for the Docker data-root, `docker.service`'s `RequiresMountsFor=` triggers the automount before Docker starts, so the encrypted data is present before the app reads it. PPE (`faberix`) mirrors the config (no real PII).

**Why this protects what BitLocker can't:** BitLocker covers offline-disk theft, but a VHDX **copied or exported while nexus is running** is decrypted at the host layer — the guest LUKS keeps that copy encrypted (TPM-bound, doesn't travel). It's also the mechanism that becomes *mandatory* if CPS ever moves off owned hardware (SPEC-1 §2). Threat-model caveat: like all at-rest encryption, this protects the **offline** disk, not a running compromised VM.

**Full procedure + recovery:** `docs/runbooks/ENCRYPTED-DATA-DISK-SETUP.md`.

---

## 3. Host ingress + audit monitoring (ADR-002)

**Measures:**
- **Metrics ride the existing `node_exporter` textfile collector on `:9100`** — no new port, no new firewall rule, no new exporter. The `audit_healthcheck` cron writes a `.prom` file that the already-running, already-scraped node_exporter publishes.
- **Scrape source restricted to the single monitoring host** (`monitrix`, `192.168.1.7`, IPv4) via the existing host UFW rule — not a subnet.
- **Host-process exposure model, not container-published**, for anything metrics-related.

**Why:** Docker **bypasses UFW** for published ports (`ports:` mappings install iptables rules that skip UFW's INPUT chain), so a container-published metrics port couldn't be firewall-restricted. Riding node_exporter (a host process) means the existing, effective `:9100`/single-host rule already governs it — the smallest, already-proven ingress surface, and zero new openings. (Full rationale + the rejected alternatives are in ADR-002.)

---

## 4. SMS pager relay (monitrix) (CPS#98 / PR #99)

The relay holds a live Twilio API credential and takes network input, so it's hardened to the strict bar even though monitrix is LAN-only:

- **Localhost-only bind + shared-secret webhook auth** (constant-time `hmac.compare_digest`); rejects missing/short secrets (**fail-closed**).
- **Bounded input** (64 KB body cap; malformed → 400) and **bounded sends** (recipient/retry caps) so a single trigger can't become an SMS-cost amplifier.
- **No secret in argv/logs** — the shared secret is passed to `curl` via a `0600` temp file (not the command line, which is world-readable in `/proc`); the Twilio token is read only from a `0600` `EnvironmentFile`, never logged.
- **Least-privilege env split** — the relay's environment carries Twilio + webhook vars only. **SMTP credentials are configured in Grafana's own `/etc/grafana/grafana.ini` `[smtp]`** (Grafana-native config, *not* an env file). Only the **webhook shared secret** + `SMTP_TO` go to Grafana via `/etc/grafana/grafana-pager.env` (`0600`, `EnvironmentFile=`) — **never** inline systemd `Environment=`, which is world-readable. *(So: to rotate SMTP creds, edit `grafana.ini`, not the env file.)*
- **Maximum systemd sandboxing** — `ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, `ProtectHome`, `RestrictNamespaces`, `SystemCallArchitectures=native`, `UMask=0077`, `InaccessiblePaths=/etc/grafana /etc/ssh /root /home`, all capabilities dropped, syscall filtered.
- **Self-testing** — a daily synthetic heartbeat SMS proves the pager path itself is alive ("who pages you when the pager is down?").

**Why:** A pager whose *own delivery path can fail silently* is worse than no pager, and a relay that can be triggered by anything local is an SMS-spam/cost weapon. The two HIGH findings the strict review caught — a world-readable systemd `Environment=` secret and a secret leaked in `curl` argv — are exactly the LAN-local-attacker class the strict bar exists to close.

---

## 5. Web ingress `:8001` (CPS#95)

**Measure (pending Track A build):** `:8001` is **interface-bound** to each host's `.1` LAN address — no `0.0.0.0` wildcard. Per the CPS#95 decision this is **dual-stack** (IPv4 `${WEB_BIND_IP}` + the `.1` IPv6 ULA `${WEB_BIND_ULA}`); this **refines ADR-002 Decision C** (currently IPv4-only in the ADR) and lands with the Track A build — *not yet implemented.*

**Policy:** direct `:8001` from `192.168.1.0/24` + its ULA (debug); **public only via Nexus** (the TLS front proxy); `192.168.2.0/24` and external **via Nexus only**. Inter-subnet enforcement is trusted to the router (gateways at `.X.1`); a host-level `DOCKER-USER` rule was considered and deliberately **not** added — the network layer owns that boundary (accepted, documented residual).

**Why:** Interface-binding to a **private** address removes the `0.0.0.0` wildcard and **confines reachability to the private LAN segment** — it is a *destination/interface* control, **not** a source-IP filter. Any host that can route to `192.168.1.x` can still reach `:8001`; today only the LAN can, by router/segmentation. True source-restriction to Nexus's single IP is **deferred** (a `DOCKER-USER` `/32` rule) — dropped from MVP because direct `/24` debug access is intended and the router owns the `.1`/`.2` boundary.

---

## Reference map

| Area | Authoritative source |
|---|---|
| Audit fail-open + recovery | CPS#78, PR #88; `parkshare/middleware.py`, `accounts/management/commands/backfill_audit_log.py` |
| Host ingress + monitoring security | `docs/architecture/ADR-002-host-ingress-monitoring-security.md` |
| MVP monitoring scope | `docs/design/AUDIT-MONITORING-MVP-SCOPE.md` |
| SMS pager design + hardening | `docs/design/MONITRIX-SMS-PAGER.md`, `docs/runbooks/MONITRIX-SMS-PAGER-SETUP.md`, PR #99 |
| Backup / NAS | CPS#91, #48, #63; `scripts/backup.sh` (pending the `age` + path change) |
| Web ingress | CPS#95 (folds into ADR-002 §C) |

*Pending implementation at time of writing: the `backup.sh` `age`-encryption + `/mnt/cps-backup` path change, and the `audit_healthcheck` / `:8001` interface-bind build (Track A).*
