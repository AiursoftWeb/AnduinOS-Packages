# anduinos-bash-guess-command

Fast, offline ghost-text suggestions for interactive Bash without replacing
Bash's line editor.

The foreground consists of a roughly 22 KiB native loadable builtin and a small
third-party-crate-free Rust decision engine. The builtin wraps only Readline
redisplay, Right Arrow and End: paste, multiline input, Enter, history search,
editing modes and every other key remain native Bash/Readline behavior. There
is no status bar, paste progress, mode banner, syntax highlighting, or terminal
cache generation.

The engine is local and typed. It understands apt workflows and package names,
Docker container slots, process IDs, systemd services, Git refs, SSH aliases,
verified command artifacts and guarded `git clean` options.
Slow local discovery happens in the background between prompts; a keystroke
query only reads an immutable in-memory snapshot. Suggestions are append-only,
single-line, control-character-free and never execute automatically.

Root-command completion is not limited to the compiled grammar. The background
observer also snapshots executable files from the current shell's startup
`PATH`, including relative entries resolved from the startup directory, within
fixed directory, entry, command and time budgets. This scan runs exactly once
when the per-shell engine starts; it adds no prompt hook, watcher or periodic
refresh, and a keystroke never performs filesystem I/O. Unsafe command names
that cannot be appended as an unquoted shell word are ignored. Programs
installed later become visible in a new Bash session.

APT package names and installed state are also snapshotted once in the
background with the distribution's own `apt-cache` and `dpkg-query` tools, then
refreshed after a successful apt update, installation or removal. Package
prediction uses a checked-in prior of more than 3,500 popular packages ranked
from Debian installation statistics, with deliberately installed tools given
first priority, plus personal history and unique-prefix fallback. A just-failed
command (exit 127) is direct installation intent when an identically named
package exists. Every keystroke still performs only immutable in-memory lookup;
normal startup and prediction never contact the network.

A compact offline index supplies safe defaults for high-value workflows plus a
generated corpus of more than 700 CLIs and 7,500 multi-level command nodes. It
contains over 20,000 uniquely testable option prefixes and more than 2,000
positional path slots, including deep forms such as `docker builder prune`,
`kubectl create clusterrolebinding`, `git commit --amend` and
`docker compose build`. The corpus is exported at development time from a
pinned Carapace release and compiled into the Rust engine; Carapace is not
installed or launched at runtime.

Existing Bash history provides immediate personal ranking across equivalent
`sudo` wrappers. Successful commands are then learned in the current shell with
frequency, recency and current-directory weights; users may explicitly opt in
to carrying that learning across sessions. A bounded adjacent-command graph
additionally learns that, in a given directory, one successful command usually
follows another. Obvious credential-bearing forms are excluded from learning,
failed commands do not train transitions, and completion never executes text by
itself. This filter is defense in depth rather than a secret detector; users who
do not want any command import or learning can set `ANDUINOS_GUESS_HISTORY=0`.
Ordinary destructive commands are eligible because Right Arrow and End only
accept text and Enter remains the execution boundary. The sole narrow replay
guard is a complete historical `dd` command writing directly to a `/dev/` device;
manually typed `dd` paths still complete normally.

Bounded directory snapshots cover the current tree plus useful home locations
such as `~/.ssh`, `~/.config` and `~/.local/bin`. Successful `ssh-keygen`,
`mkdir`, `git clone` and Python venv commands publish only artifacts that a
background observer verifies actually exist. SSH config aliases and unhashed
known hosts are snapshotted the same way. None of these providers performs
filesystem or process work on a keystroke query.

Examples:

```bash
sudo apt update                   # next: sudo apt up -> upgrade
sudo apt install b                # -> btop when available and not installed
apt auto                          # -> your most-used matching apt action
sudo docker ps | grep mysql       # next: docker logs -f  -> unique container
sudo docker ps                    # next: docker e -> docker exec -it <container>
ps aux | grep mysqld              # next: sudo kill  -> matching PID
systemctl list-units | grep ssh   # next: systemctl status  -> ssh.service
ssh-keygen -f ~/.ssh/id_work      # next: cat ~/.ssh/i -> its verified .pub file
python3 -m venv .venv             # next: source .v -> .venv/bin/activate
ssh prod                          # -> an alias from ~/.ssh/config
docker compose lo                 # -> logs
docker run --publ                 # -> --publish
kubectl apply -f manifests/de     # -> a real matching local path
git add src/ma                    # -> a real matching local path
git switch fea                    # -> a live matching branch
git clean . -                     # -> --dry-run
```

Right Arrow or End accepts the visible suggestion only when the cursor is
already at the end. Elsewhere, End retains its native move-to-end behavior.
Enter always executes exactly the visible line. Tab completion is never
registered or modified by this package and remains owned by Bash and the
system's existing completion scripts.

The feature is enabled by Bash's standard completion loader. These variables
may be set anywhere in `~/.bashrc`; setting the master switch to `0` in an
active shell takes effect at the next prompt or redisplay:

```bash
export ANDUINOS_GUESS_COMMAND=0       # disable predictions and their helper
export ANDUINOS_GUESS_ENGINE=0        # disable ghost text
export ANDUINOS_GUESS_HISTORY=0       # disable history import and learning
export ANDUINOS_GUESS_PERSIST=1       # opt in to extra cross-session state
```

Removing the package removes the loader, native frontend and engine. The next
Bash session is stock Readline again; no user dotfile is modified.

Learning uses no database and no daemon-wide service. Each interactive Bash owns
its small engine process. By default the engine imports only the history file
selected by the current Bash and keeps new learning in memory for that shell;
it does not create another command log. Setting `ANDUINOS_GUESS_PERSIST=1`
explicitly enables private mode-0600 `history-v1` and `transitions-v1` logs under
`${XDG_STATE_HOME:-~/.local/state}/anduinos-bash-guess-command/`. Each log
compacts at 1 MiB and each in-memory index is capped at 2,000 entries. Setting
`ANDUINOS_GUESS_HISTORY=0` disables import, session learning and persistence.

The optional grammar-update workflow fetches only fixed, checksummed Carapace
release archives. Package builds, installation and normal shell startup do not
access the network. The native frontend uses a small vendored declaration of
Bash's stable loadable-builtin/Readline ABI, so cross builds do not depend on
host Bash development headers.
