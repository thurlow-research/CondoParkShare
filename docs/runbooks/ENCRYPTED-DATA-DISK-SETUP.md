# Runbook — Encrypted CPS Data Disk (guest LUKS2 + TPM2 auto-unlock)

*Sets up a dedicated, encrypted data disk on a CPS host (opus = prod, faberix = ppe) that holds all CPS data (`/var/lib/docker` → Postgres volume, `audit_logs`, images). This is the **guest-level** at-rest layer — defense-in-depth **on top of** host-level BitLocker on `nexus`. Validated on faberix 2026-06-15; opus follows the same recipe.*

---

## What this achieves & why

| Property | How |
|---|---|
| **Encrypted at rest in the guest** | LUKS2 on a dedicated virtual disk (`/dev/sdb` → `/mnt/cps-data`) |
| **Unattended boot** | TPM2 keyslot sealed to the VM's **vTPM** (`systemd-cryptenroll --tpm2-device=auto`) — auto-unlocks on *this* VM with no passphrase |
| **Copied-VHDX-proof** | the TPM2 key is bound to the vTPM, which does **not** travel with a copied VHDX — so a stolen/exported disk won't unlock |
| **Recoverable** | a **passphrase keyslot** (set at `luksFormat`) is the offline recovery key |
| **Mounted before the app reads it** | `x-systemd.automount` + `RequiresMountsFor=` on `docker.service` — Docker triggers the mount before it starts (a plain boot-mount doesn't reliably fire for a late-unlocking LUKS device; see below) |

**Threat model:** this protects the *offline* disk (copied/exported VHDX, stolen storage). It does **not** protect a running, compromised VM (the volume is decrypted while live) — that's access control + the `age` backup layer. Pairs with: host BitLocker on `nexus` (CPS#104), `age` backup encryption (CPS#91).

---

## Key custody (do this!)

- **Passphrase keyslot (slot 0)** = the offline recovery key. Store it in a password manager / offline — **never** on the VM. If the vTPM state is ever lost (VM rebuilt, host reset), this passphrase is the only way back in.
- **TPM2 keyslot (slot 1)** = the unattended auto-unlock; never leaves the vTPM.
- This is the SPEC-1 §7 requirement: LUKS/recovery keys stored offline, separate from the data.

---

## Procedure (run per host; opus then faberix-parity)

```bash
# 0. TPM2 userspace libs (some hosts ship without them) — MUST list a device before continuing
sudo apt update && sudo apt install -y tpm2-tools
systemd-cryptenroll --tpm2-device=list            # STOP if this lists nothing

# 1. SAFETY: confirm the NEW empty data disk (NOT the OS disk; no FS, no mountpoint)
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
sudo wipefs -n /dev/sdb                            # must show NOTHING  (adjust /dev/sdb per host)

# 2. LUKS2 format — type YES, then a STRONG passphrase (this is your OFFLINE recovery key)
sudo cryptsetup luksFormat --type luks2 /dev/sdb

# 3. enroll the TPM2 auto-unlock keyslot (prompts for the passphrase), then VERIFY two slots
sudo systemd-cryptenroll --tpm2-device=auto /dev/sdb
sudo systemd-cryptenroll /dev/sdb                 # MUST show:  0 password  AND  1 tpm2

# 4. open, MAKE the filesystem, VERIFY it exists, mountpoint
sudo cryptsetup open /dev/sdb cps-data            # prompts for the passphrase
sudo mkfs.ext4 -L cps-data /dev/mapper/cps-data
sudo blkid /dev/mapper/cps-data                   # MUST show TYPE="ext4"  (the step that's easy to skip)
sudo mkdir -p /mnt/cps-data

# 5. crypttab — guarded UUID write (refuses to write a blank one)
LUKS_UUID=$(sudo blkid -s UUID -o value /dev/sdb)
[ -n "$LUKS_UUID" ] && echo "cps-data UUID=$LUKS_UUID none tpm2-device=auto,luks,discard" | sudo tee -a /etc/crypttab || echo "STOP: empty UUID — is /dev/sdb LUKS-formatted?"
grep '^cps-data' /etc/crypttab                    # eyeball: UUID= has a REAL value, exactly ONE line

# 6. fstab — x-systemd.automount (the PROVEN mechanism). A plain/hard fstab
#    boot-mount AND an explicit .mount unit both fail to fire for a TPM2-LUKS
#    device (it unlocks too late for local-fs.target; the mount is skipped and
#    never retried). automount defers the mount to first access — same as the
#    NAS shares. For Docker, RequiresMountsFor= (follow-up) triggers it before
#    dockerd starts, so the data is present before the app reads it.
echo '/dev/mapper/cps-data  /mnt/cps-data  ext4  nofail,x-systemd.automount  0  2' | sudo tee -a /etc/fstab
grep cps-data /etc/fstab                          # exactly ONE line
sudo systemctl daemon-reload

# 6b. trigger + verify now
ls /mnt/cps-data && findmnt /mnt/cps-data         # expect: lost+found, + an ext4 line under an autofs line

# 7. REBOOT — confirm the automount re-establishes and mounts on access
sudo reboot
#    after it returns:
ls /mnt/cps-data ; findmnt /mnt/cps-data          # ls triggers it; expect ext4 mounted
sudo cryptsetup status cps-data                   # active (LUKS2)
```

### The four checkpoints that bit us on faberix (do not skip)
1. `--tpm2-device=list` **lists a device** after the `tpm2-tools` install (else no auto-unlock).
2. `blkid /dev/mapper/cps-data` shows **`TYPE="ext4"`** — i.e. `mkfs` actually ran (skipping it → "bad superblock" mount failures that look like an ordering bug).
3. `systemd-cryptenroll /dev/sdb` shows **both** `0 password` and `1 tpm2` (the enroll can silently no-op).
4. `crypttab`/`fstab` each have **exactly one** correct line (a blank `UUID=` or a duplicated line silently breaks the boot path; `nofail` masks it).

### Mounting: automount, not boot-mount (lesson learned)
A TPM2-unlocked LUKS device becomes available **late** in boot (after `local-fs.target`), so a plain fstab mount — *even without `nofail`* — and an explicit systemd `.mount` unit both **silently fail to fire**: the mount is attempted before the device exists and is never retried, and `nofail` then masks it. Validated on **both** hosts — the disk *unlocks* (`cryptsetup status` → `active`) but never *mounts* at boot.
- **Use `x-systemd.automount`** (step 6) — it defers the mount to first access, the same mechanism that reliably mounts the NAS shares. `findmnt` shows `autofs` until something touches the path, then `ext4`.
- **For the Docker data-root this is equivalent to boot-mount:** once `/var/lib/docker` lives here, `docker.service` gets `RequiresMountsFor=/mnt/cps-data` (follow-up) → Docker **triggers** the automount before it starts, so the encrypted data is guaranteed present before the app reads it — which is the real requirement. The boot-vs-first-use distinction becomes moot.
- Both hosts (opus + faberix) use the **same** automount line — config parity.

---

## Recovery

- **TPM unlock fails at boot** (vTPM reset, VM rebuild, firmware change): you'll get an emergency console / passphrase prompt → enter the **slot-0 passphrase**. Once booted, re-seal: `sudo systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto /dev/sdb`.
- **Lost the passphrase AND the vTPM**: the data is unrecoverable (by design). This is why slot-0 is stored offline.

## Follow-up (separate step)
Migrate **`/var/lib/docker`** onto `/mnt/cps-data` so CPS data actually lands on the encrypted disk, with `docker.service` ordered **after** the mount (`RequiresMountsFor`). Until then, the encrypted disk is provisioned but not yet holding the data.

*Refs: CPS#104 (BitLocker host layer), CPS#91/#48/#63 (backup encryption), SPEC-1 §2/§7, `docs/security/SECURITY-MEASURES.md`.*
