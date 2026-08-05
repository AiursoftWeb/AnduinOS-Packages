# AnduinOS Secure Boot Module Signing Architecture

> Implementation ownership has moved to `anduinos-secureboot-toolkit`. This
> document preserves the original OOBE design rationale; the toolkit README is
> authoritative for current scope, dependencies, helper actions, and shared UI
> architecture. OOBE remains the first-run entry point and embeds the shared
> toolkit panel.

## Overview

AnduinOS ships third-party kernel modules (xpadneo, NVIDIA, IPU6 camera drivers, etc.) that must pass Secure Boot validation.

### Credit Where Credit Is Due: What Ubuntu Already Does

The vast majority of the Secure Boot infrastructure is built by Ubuntu. We stand on their shoulders:

| Component | Provided by | What it does |
|-----------|------------|--------------|
| Shim (`shimx64.efi`) | Ubuntu / Microsoft | Signed by Microsoft, boots GRUB, maintains MOK list |
| Kernel signing | Ubuntu / Canonical | `vmlinuz` is signed by Canonical's key, trusted by Shim |
| `update-secureboot-policy` | Ubuntu (`shim-signed`) | Generates MOK key pairs, handles enrollment prompts |
| Ubiquity `copy_mok()` | Ubuntu (installer) | Copies MOK keys from live environment to target system |
| MOKManager blue screen | Shim | Firmware-level UI for enrolling keys on reboot |
| DKMS framework | Ubuntu (`dkms`) | Builds and signs kernel modules from source |
| `ubuntu-drivers` | Ubuntu | Detects GPU, installs the correct driver metapackage |

**Ubuntu already does the heavy lifting.** On a fresh Ubuntu install with "third-party software" checked, Ubiquity generates a MOK key, the user enrolls it on first reboot, and `ubuntu-drivers install` triggers DKMS to build and sign NVIDIA modules. In theory, everything works.

In practice, things go wrong. And when they do, Ubuntu's answer is a pink-screen debconf terminal prompt or a multi-page Wiki article. Most users have no idea what is happening and either give up or disable Secure Boot entirely.

### What AnduinOS OOBE Adds

AnduinOS OOBE is a **graphical state inspector and repair tool**, not a reinvention of the signing chain. It adds three things Ubuntu does not provide:

1. **A five-layer health check** that reads the actual state of the trust chain and displays it as colored indicators (green/yellow/red) — so the user knows exactly what is wrong.
2. **A DKMS signing configuration file** (`/etc/dkms/framework.conf.d/anduinos-sb-sign.conf`) — Ubuntu's DKMS can auto-detect MOK keys, but that fallback is unreliable in production. Explicit configuration eliminates the ambiguity.
3. **One-click repair buttons** — "Create & Enroll Certificate" and "Fix & Reinstall Driver" — that do what Ubuntu's CLI tools can already do, but wrapped in a GUI no-tutorial-required experience.

**Ubuntu built the engine. AnduinOS OOBE adds the dashboard and the roadside assistance.**

---

## The Trust Chain: From Silicon to Kernel Module

Understanding *why* a button-click in user-space convinces the motherboard hardware to trust a third-party driver requires tracing the full chain of trust:

```
┌─────────────────────────────────────────────────────────────────┐
│  UEFI Firmware (Motherboard)                                    │
│  Trusts: Microsoft Corporation UEFI CA                          │
│  "I only boot code signed by keys I know."                     │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  shimx64.efi (Shim)                                             │
│  Signed by: Microsoft → trusted by UEFI firmware              │
│  Role: Chain-loads GRUB, maintains the MOK List                │
│  "I trust binaries signed by Ubuntu AND keys the owner added." │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  GRUB → Linux Kernel (vmlinuz)                                  │
│  Signed by: Canonical Ltd. → trusted by Shim                   │
│  Role: Boots the OS, receives MOK List from Shim               │
│  Kernel loads MOK keys into .machine → .secondary_trusted_keys │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Kernel Module (.ko) — e.g. hid-xpadneo.ko, nvidia.ko           │
│  Signed by: MOK.priv (owner-controlled key)                    │
│  Kernel checks: is this module's signature in my keyring?      │
│  If MOK was enrolled and module was signed with it → LOAD ✓   │
└─────────────────────────────────────────────────────────────────┘
```

Every step in the OOBE — generating the key, enrolling it in MOKManager, configuring DKMS to use it — is about making the final row of this diagram succeed, silently, for every third-party module on the system.

---

## The Genesis: Ubiquity and the Installation-Time Trust Establishment

The trust chain does not start in the OOBE. It starts during system installation.

### What Ubiquity Does

When the user installs AnduinOS with "Install third-party software" checked and Secure Boot enabled, the Ubiquity installer (`ubiquity-dm` plugin) offers to set a Secure Boot password. Behind the scenes:

1. The live environment runs `update-secureboot-policy --new-key`, generating `MOK.priv` and `MOK.der` at `/var/lib/shim-signed/mok/`.
2. `mokutil --import` queues the certificate for enrollment.
3. Ubiquity's `copy_mok()` copies the key material from the live environment to the target system at `/target/var/lib/shim-signed/mok/`.
4. On first reboot, `MOKManager` (shim's blue-screen firmware interface) appears and asks the user to enroll the key.

### Where Ubiquity Falls Short — And Where OOBE Takes Over

Ubiquity's flow has several failure modes that affect real users:

| Scenario | Ubiquity result | OOBE defense |
|----------|----------------|--------------|
| User unchecks "third-party software" | No MOK generated | OOBE detects missing MOK, shows "Create & Enroll" button |
| User checks the box, generates MOK, but **misses the MOKManager blue screen** (10-second timeout) | MOK exists but not enrolled | OOBE detects `mokutil --test-key` failure, shows yellow warnings, offers re-enrollment |
| MOK is generated and enrolled, but **DKMS module was built before DKMS signing config existed** | Module signed with wrong key | OOBE detects `sig_key ≠ cert_serial`, shows "Fix & Reinstall" button |
| MOK is generated, enrolled, DKMS config exists, module signed correctly | Everything works | OOBE shows all green — no action needed |

**Ubiquity is the primary pathway. OOBE is the safety net.** Together they form a two-layer defense: Ubiquity tries to get it right at install time; OOBE detects and repairs any deviation from the correct state during first boot.

---

## Architecture: The OOBE Signing Infrastructure

```
                   ┌──────────────────────────────────────┐
                   │   OOBE Secure Boot Page              │
                   │   "Create & Enroll Certificate"      │
                   └────────────────┬─────────────────────┘
                                    │
                    update-secureboot-policy --new-key
                    mokutil --import MOK.der
                    cat > anduinos-sb-sign.conf
                    dkms autoinstall
                                    │
                                    ▼
                   ┌──────────────────────────────────────┐
                   │   /var/lib/shim-signed/mok/          │
                   │   MOK.priv  +  MOK.der               │
                   └────────────────┬─────────────────────┘
                                    │
                   ┌──────────────────────────────────────┐
                   │   /etc/dkms/framework.conf.d/        │
                   │   anduinos-sb-sign.conf              │
                   │                                      │
                   │   mok_signing_key=.../MOK.priv       │
                   │   mok_certificate=.../MOK.der        │
                   └────────┬──────────────────┬──────────┘
                            │                  │
              ┌─────────────▼──────┐  ┌────────▼──────────┐
              │  xpadneo (DKMS)    │  │  NVIDIA (DKMS)    │
              │  hid-xpadneo.ko    │  │  nvidia.ko        │
              │  Signed: MOK key ✓ │  │  Signed: MOK key ✓│
              └────────────────────┘  └───────────────────┘
```

---

## Key Design Decisions

### 1. Global DKMS signing config — not per-package

We write signing configuration to `/etc/dkms/framework.conf.d/anduinos-sb-sign.conf`, not into individual DKMS module configs. Every DKMS module on the system automatically inherits the same MOK signing key:

- **xpadneo** — Xbox controller driver
- **NVIDIA** — proprietary graphics driver
- **IPU6** — Intel camera drivers (common on CHUWI and other Atom tablets)
- **Any future DKMS package** — zero additional code required

### 2. Only key paths — no sign-file path

The config contains ONLY the key locations:

```ini
mok_signing_key="/var/lib/shim-signed/mok/MOK.priv"
mok_certificate="/var/lib/shim-signed/mok/MOK.der"
```

We deliberately **do not** specify `sign_file`. DKMS automatically locates the correct `scripts/sign-file` binary for each target kernel. This survives kernel upgrades without maintenance — when the user installs kernel 7.0.0-29, the correct sign-file binary is found automatically.

### 3. Dual-track defense: proactive + reactive

**Track A — Proactive (Secure Boot page):** When the user clicks "Create & Enroll Certificate," the OOBE writes `anduinos-sb-sign.conf` before running `dkms autoinstall`. Every module built from that moment forward is correctly signed with the MOK key.

**Track B — Reactive (Xbox page "Fix & Reinstall" button):** For machines already in a broken state (driver installed but signed with the wrong key, typically because the DKMS signing config was missing when the driver was first built), the Xbox page detects the signature mismatch and offers a one-click repair. The repair writes the config, then reinstalls the driver. After rebuild, the page auto-refreshes to confirm all green.

### 4. Idempotent and safe

Both tracks use the same heredoc pattern. Running either path multiple times produces the same correct result. The config file is overwritten, not appended — ensuring a single source of truth and no stale entries.

---

## The OOBE Xbox Page State Machine

The Xbox controller page in OOBE implements a three-row health check that translates invisible kernel security state into user-visible colored indicators:

| Row | What | Green | Yellow | Red |
|-----|------|-------|--------|-----|
| R1 | Driver installed? | Installed | — | Not installed |
| R2 | Signature trusted? | Signed with current MOK | Signed but not enrolled / unknown cert | Not signed |
| R3 | Module status | Loaded / standing by | — | Blocked by Secure Boot |

### Detection method

The state machine uses five independent detection mechanisms:

1. `mokutil --sb-state` — is Secure Boot enabled at all?
2. `mokutil --test-key MOK.der` — is the MOK certificate enrolled in firmware?
3. `openssl x509 -serial` on `MOK.der` — what is the certificate's serial number?
4. `modinfo hid-xpadneo | grep sig_key` — what key actually signed the module?
5. **String comparison** between (3) and (4) — this catches the ghost state where a module is signed, but with a different key than the one the firmware trusts

This five-layer check is what makes the state machine reliable. It does not assume anything — it verifies every link in the chain.

The shared toolkit distinguishes enabled, disabled, explicitly unsupported,
and unknown detection results. Unsupported firmware is a known non-enforcing
state. Probe failures and contradictory output are unknown and block driver
trust operations instead of being silently treated as disabled.

### Button visibility logic

| State | Button shown | Action |
|-------|-------------|--------|
| Not installed + SB on + no MOK | Install (grayed out) | Guide to Secure Boot page first |
| Not installed + can install | Install (active) | `apt install anduinos-xbox-controller-driver` |
| Installed + signature mismatch | **Fix & Reinstall** | Write DKMS config → `apt reinstall` → auto-refresh |
| Installed + all green | None (Pair/Test visible) | Ready to play |

---

## The Reinstall Fix Script

```bash
# 1. Ensure DKMS global signing config exists (idempotent)
if [ ! -f /etc/dkms/framework.conf.d/anduinos-sb-sign.conf ] && \
   [ -f /var/lib/shim-signed/mok/MOK.priv ]; then
    mkdir -p /etc/dkms/framework.conf.d
    cat << 'EOF' > /etc/dkms/framework.conf.d/anduinos-sb-sign.conf
mok_signing_key="/var/lib/shim-signed/mok/MOK.priv"
mok_certificate="/var/lib/shim-signed/mok/MOK.der"
EOF
fi

# 2. Rebuild the driver (triggers DKMS → reads config → signs with MOK)
apt update && apt reinstall -y anduinos-xbox-controller-driver
```

The heredoc delimiter `'EOF'` is quoted to prevent shell variable expansion — the config file is written as a literal, exactly as intended.

---

## Edge Cases & Defensive Design

### The "Missed the Blue Screen" Scenario

During OOBE, the user clicks "Create & Enroll Certificate" and reboots. MOKManager appears with a 10-second timeout. The user is making coffee. The timeout expires. The certificate was never enrolled.

**System response:** On next boot, OOBE re-runs the full health check. `mokutil --test-key` reports the key is not enrolled. R2 goes yellow, R3 goes red. The "Create & Enroll Certificate" button is available again. **No infinite loop — just a state machine that reflects reality and offers the same repair path until the user completes enrollment.**

### The Virtual Machine Exception

Virtual machines (VMware, KVM, VirtualBox) often expose incomplete or emulated Secure Boot behavior. Xbox controller drivers are meaningless in a VM. Testing kernel module signing in a virtualized UEFI is unreliable.

**System response:** `create_xbox_page` calls `is_virtual_machine()` which runs `systemd-detect-virt`. If detected as a VM, the Xbox page is never added to the carousel. The user never sees it. No time wasted on a meaningless diagnostic.

### The "No NVIDIA GPU" Optimization

Machines without NVIDIA hardware (Intel-only laptops, AMD desktops) should not see the NVIDIA driver page.

**System response:** `has_nvidia_gpu()` runs `lspci` and checks for "NVIDIA" in the output. If not found, the NVIDIA page is skipped. The OOBE wizard is shorter and more relevant.

### The "Secure Boot Disabled" Happy Path

If Secure Boot is off, the entire signing chain is irrelevant — the kernel loads unsigned modules without complaint.

**System response:** The Secure Boot page is never shown. The Xbox page skips R2 (signature check) entirely and only shows R1 (installed?) and R3 (loaded?). Green, green, done.

---

## What AnduinOS Adds on Top of Ubuntu

### The Gap: Ubuntu Does Everything Right — When Nothing Goes Wrong

Ubuntu's Secure Boot flow works perfectly in the ideal case: user checks "third-party software" during install, Ubiquity generates the MOK, user enrolls it on first reboot, DKMS builds modules, everything is signed and trusted.

But Ubuntu has no graceful recovery when the ideal case fails:

- User unchecks "third-party software" → no MOK generated → NVIDIA driver installs but modules won't load. Ubuntu's response: pink-screen debconf prompt demanding a password the user never set.
- User generates MOK but misses the MOKManager 10-second timeout → MOK exists but is not enrolled → modules signed with correct key but kernel doesn't trust it. Ubuntu's response: a Wiki page.
- DKMS builds a module before the signing config is in place → module signed with wrong key → kernel rejects it. Ubuntu's response: another Wiki page.

All three failures are invisible to Ubuntu's tooling. The user just sees "NVIDIA driver installed successfully" followed by "nouveau loaded on next boot" (because the proprietary module wouldn't load), and has no idea why.

### What AnduinOS OOBE Does Differently

AnduinOS OOBE is a **graphical state inspector and recovery panel.** It does not replace Ubuntu's signing infrastructure. It surfaces it.

**1. Five-layer health check**

Instead of assuming everything worked, OOBE verifies every link in the chain:

```
mokutil --sb-state          → Is Secure Boot on?
mokutil --test-key          → Is MOK enrolled in firmware?
openssl x509 -serial        → What is the MOK certificate serial?
modinfo | grep sig_key      → What key actually signed the module?
String comparison           → Do they match?
```

**2. Explicit DKMS signing configuration**

Ubuntu's DKMS (3.2.2+) has a fallback that auto-detects MOK keys at `/var/lib/shim-signed/mok/MOK.priv`. In production testing, this fallback **silently failed** on real hardware — modules were signed with a different, auto-generated key despite the MOK key existing. We write `/etc/dkms/framework.conf.d/anduinos-sb-sign.conf` to remove this ambiguity. This is the one piece of *configuration* that AnduinOS adds that Ubuntu does not create by default.

**3. One-click repair buttons**

"Create & Enroll Certificate" and "Fix & Reinstall Driver" are GUI wrappers around Ubuntu's existing CLI tools (`update-secureboot-policy`, `mokutil --import`, `dkms autoinstall`, `apt reinstall`). The user sees a button, not a terminal.

### The User Experience

**Ubuntu ideal path:** Install with checkbox → reboot → blue screen → type password → done. (If anything fails: Wiki page.)

**AnduinOS path:** OOBE shows green/yellow/red for each row → if yellow, click the button → reboot → type 123456 → OOBE shows all green.

The underlying machinery is the same. The difference is that AnduinOS **knows when something is wrong, tells the user in a language they understand, and offers a button to fix it.**

---

## Files Involved

| File | Role |
|------|------|
| `/etc/dkms/framework.conf.d/anduinos-sb-sign.conf` | Global DKMS signing config (created by OOBE) |
| `/var/lib/shim-signed/mok/MOK.priv` | MOK private key |
| `/var/lib/shim-signed/mok/MOK.der` | MOK certificate (enrolled in UEFI firmware) |
| `/usr/sbin/update-secureboot-policy` | Ubuntu shim-signed tool (generates MOK keys) |
| `/usr/bin/anduinos-oobe` | OOBE wizard (Secure Boot page + Xbox page state machine) |
| `/etc/dkms/framework.conf` | DKMS main config (we leave this alone; our config is in `conf.d/`) |
| `/lib/modules/$(uname -r)/updates/dkms/` | Where signed DKMS modules are installed |

## Why NVIDIA Also Benefits

The `anduinos-sb-sign.conf` is a **global DKMS configuration file**. It applies to every DKMS module on the system, not just xpadneo. When the user installs the NVIDIA proprietary driver (which ships as `nvidia-dkms-595-open`), DKMS:

1. Reads `anduinos-sb-sign.conf`
2. Finds the MOK key paths
3. Signs `nvidia.ko`, `nvidia-modeset.ko`, `nvidia-uvm.ko`, etc. with the same MOK key
4. The kernel trusts these modules because the MOK certificate is already enrolled

No additional configuration, no per-driver scripts, no pink screen. The signing infrastructure was already in place from the moment the user went through the Secure Boot page.

---

## Testing the Signing Chain

```bash
# Is Secure Boot on?
mokutil --sb-state

# Is the MOK certificate enrolled?
sudo mokutil --test-key /var/lib/shim-signed/mok/MOK.der

# Is DKMS configured to use the MOK key?
cat /etc/dkms/framework.conf.d/anduinos-sb-sign.conf

# What key signed the current module?
modinfo hid-xpadneo | grep -E "sig_key|signer"

# Compare with MOK certificate serial
openssl x509 -in /var/lib/shim-signed/mok/MOK.der -inform DER -noout -serial

# Does the module load?
sudo modprobe hid-xpadneo && lsmod | grep xpadneo
```

**All three rows green** in the OOBE Xbox page is the definitive confirmation that the entire trust chain — from UEFI firmware through Shim through the kernel through DKMS — is intact and functioning.
