# AnduinOS YubiKey Security Center Roadmap

## Current status

The first three SSH milestones are implemented:

- Home reports GDM and sudo status through a shared capability-badge model,
  with reserved SSH, LUKS, and Git capabilities.
- The SSH page detects the current agent, enumerates connected YubiKeys and
  resident SSH credentials, and shows algorithm, application, resident
  username, fingerprint, and agent state.
- Safe SSH operations are available: load resident keys with `ssh-add -K`,
  remove a public key from the current agent, copy or export its public key,
  and test signing.
- New resident keys can be created on an explicitly selected
  `/dev/hidrawN`. The normal preset is `ecdsa-sk`, resident, and
  touch-required; advanced users may select supported options such as
  verification-required. PIN entry uses the private askpass pipe and is never
  placed in argv, the environment, logs, or a temporary file.

Phase 4 is partially implemented. Permanent resident-credential deletion now
uses an explicitly selected device and credential ID, destructive
confirmation, protected PIN entry, and post-delete re-enumeration. **Remove
from agent remains a separate, non-destructive action.** Labels, agent
lifecycle, destination constraints, arbitrary SSH host editing, rollback
assistance, and automatic loading remain future work. A fixed, user-controlled
10-minute SSH connection-persistence preset is implemented independently.

Git SSH signing now has a self-contained implementation:

- Connected YubiKeys and their SSH credentials can be loaded directly on the
  Git page.
- One radio group contains `No signing` and every SSH credential. Selection is
  applied immediately, so shared and dedicated-key setups need no separate mode.
- Local FIDO key handles are preferred, with an inline public-key/agent fallback.
- Enabling a key displays GitHub Signing Key guidance and a copy-public-key action.
- Failed configuration writes roll back to the immediately preceding values, and
  the signing test creates no repository or commit.
- Home and SSH credential rows expose Git signing status.

Future Git work includes repository-local scope, an allowed-signers trust-file
assistant, and guidance for additional hosted providers.

## Phase 4: permanent deletion and advanced SSH management

### 1. Permanent resident-credential deletion — implemented

- Add a backend capability probe for CTAP2 credential management before
  showing destructive controls. Unsupported devices remain read-only.
- Bind every operation to both an explicitly selected `/dev/hidrawN` and the
  exact resident credential ID. Never infer a target from list order, serial
  alone, or whichever key answers first.
- Re-enumerate the selected device immediately before deletion and reject the
  operation if the credential or device identity changed.
- Require a dedicated confirmation dialog showing device model, serial,
  application, resident username, algorithm, and fingerprint. The user must
  type the fingerprint suffix to enable the destructive action.
- Ask for the FIDO PIN in the existing protected PIN dialog. Do not cache,
  log, persist, or pass it through argv.
- Keep the progress dialog open during PIN verification and credential
  deletion. Warn the user not to unplug the key.
- Re-enumerate after the command and report success only when the exact
  credential disappeared. A transport error after deletion must be reported
  as an unknown outcome, with instructions to inspect before retrying.
- Do not automatically delete local `.pub` or key-handle files. Offer their
  removal as a separate, clearly scoped cleanup after hardware deletion has
  been verified.
- Add backend tests for device swapping, duplicate labels, malformed
  credential IDs, partial failures, and the “deleted but response lost” case.

### 2. Labels and metadata

Treat these as two different features:

- A local alias is non-destructive metadata owned by this application. Store
  it by stable credential identity, never by list position, and show the real
  resident username and fingerprint alongside it.
- Updating the authenticator's resident user/display name is offered only
  when the selected authenticator advertises the required CTAP capability.
  It requires an explicit device, credential ID, and PIN, followed by
  re-enumeration. Never emulate rename by deleting and recreating a key.

Changing a local OpenSSH public-key comment must not change the fingerprint or
be presented as changing the YubiKey credential.

### 3. SSH agent lifecycle

- Show which agent socket is active without exposing unrelated environment
  values.
- Add per-key and “all keys from this YubiKey” load/unload actions.
- Allow an optional agent lifetime and confirmation policy where supported.
  Safe defaults remain unlimited agent lifetime, touch-required by the
  authenticator, and no silent policy weakening.
- Detect agent replacement or restart and refresh state instead of assuming a
  previously loaded key is still present.
- Treat agent lock/unlock as an advanced, session-scoped action. Never reuse a
  FIDO PIN as an agent password and never store either secret.

### 4. Destination constraints

- Detect whether the current OpenSSH client and agent support destination
  constraints before enabling the editor.
- Provide a structured host/destination editor with an advanced raw preview.
  Validate every constraint before invoking `ssh-add`.
- Resolve destination aliases using the user's effective SSH configuration
  and clearly explain that constraints are enforced by the agent, not stored
  inside the YubiKey.
- Refuse ambiguous or unsupported constraint syntax. After loading, display
  the policy that was actually requested and make constrained identities easy
  to remove.
- Test forwarded-agent scenarios, host-key changes, unsupported agents, and
  configurations containing wildcards or jump hosts.

### 5. `~/.ssh/config` integration — fixed persistence preset implemented

The first deliberately narrow integration is complete:

- The SSH page offers a default-off switch for a fixed global
  `ControlMaster auto`, `ControlPath ~/.ssh/cm-%r@%h:%p`, and
  `ControlPersist 10m` preset.
- The app owns one exact `.inc` fragment and one marked `Host *`/`Include`
  block. It preserves earlier host-specific OpenSSH values and never claims or
  removes handwritten equivalent settings.
- Writes use restrictive permissions, same-directory temporary files,
  `ssh -G` validation, concurrent-edit detection, atomic replacement, and
  rollback when a multi-file enable/disable operation cannot finish safely.
- Modified, duplicated, incomplete, symlinked, or additionally included
  managed content is reported as needing manual attention.

General SSH configuration work remains future scope:

- Default to a preview-only assistant; do not rewrite a user's full SSH
  configuration.
- Put application-managed entries in a dedicated file under
  `~/.ssh/config.d/` and add at most one idempotent `Include` line when the
  user approves it.
- Preserve ownership and restrictive permissions. Write through a same-filesystem
  temporary file, validate the result, and atomically rename it into place.
- Back up only files the application modifies, show a diff before applying,
  and provide a precise rollback action.
- Never overwrite user-managed host blocks or assume that one resident key
  belongs to every host.
- Validate the effective configuration with OpenSSH before committing a
  change, including existing `Include`, `Match`, wildcard, and permission
  edge cases.

### 6. Automatic-load policy

- Keep automatic loading disabled by default.
- Prefer a user-session notification with a deliberate “Load keys” action
  when a configured YubiKey appears. This preserves secure PIN and touch
  interaction.
- If session-start loading is enabled, bind the policy to stable device and
  credential identities and to the current user. Never run a broad
  “load resident keys from any connected authenticator” policy.
- Do not store the FIDO PIN or bypass touch/verification requirements.
- Account for changing `SSH_AUTH_SOCK`, locked sessions, absent agents,
  multiple connected YubiKeys, repeated device events, and agent restarts.
- Provide a visible kill switch and a complete removal path for any autostart
  file or user service created by the application.

## Delivery order

1. Introduce typed credential/device identities and read-only capability
   probes.
2. Implement local aliases and agent lifecycle controls.
3. Implement permanent deletion behind the confirmation and verification
   flow, with backend tests before UI exposure.
4. Add destination constraints.
5. Extend the fixed persistence preview into general preview-first SSH config
   integration and a user-facing rollback assistant.
6. Add opt-in automatic loading last, after multi-device and agent-restart
   behavior is covered by tests.

Each step must preserve the current ordinary-user defaults. Advanced controls
stay collapsed until requested, destructive actions are never combined with
non-destructive cleanup, and every hardware mutation is verified by
re-enumerating the explicitly selected YubiKey.
