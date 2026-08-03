# anduinos-bash-guess-command

Fast, offline ghost-text suggestions for interactive Bash without replacing
Bash's line editor.

The foreground consists of a roughly 10 KiB native loadable builtin and a small
Rust decision engine. The builtin wraps only Readline redisplay and Right Arrow:
paste, multiline input, Enter, history search, editing modes and every other key
remain native Bash/Readline behavior. There is no status bar, paste progress,
mode banner, syntax highlighting, or terminal cache generation.

The engine is local and typed. It understands apt workflows, Docker container
slots, process IDs, systemd services, Git refs and guarded `git clean` options.
Slow local discovery happens in the background between prompts; a keystroke
query only reads an immutable in-memory snapshot. Suggestions are append-only,
single-line, control-character-free and never execute automatically.

A compact offline index supplies safe defaults and shared-prefix completion for
more than 40 common CLIs. High-value verb grammars are generated from the pinned
Carapace specification instead of being copied by hand, then compiled into the
Rust engine; Carapace is never launched while typing. Existing Bash history
provides immediate personal ranking across equivalent `sudo` wrappers;
successful commands are then learned across sessions with frequency, recency
and current-directory weights. Credential-like commands are never
persisted, dangerous history is never replayed, and the private state file is
bounded. Current-directory filenames are also snapshotted between prompts, so
path suggestions perform no foreground disk access.

Examples:

```bash
sudo apt update                   # next: sudo apt up -> upgrade
apt auto                          # -> your most-used matching apt action
sudo docker ps | grep mysql       # next: docker logs -f  -> unique container
ps aux | grep mysqld              # next: sudo kill  -> matching PID
systemctl list-units | grep ssh   # next: systemctl status  -> ssh.service
git switch fea                    # -> a live matching branch
git clean . -                     # -> --dry-run
```

Right Arrow accepts the visible suggestion only when the cursor is at the end.
Enter always executes exactly the visible line. Tab remains normal programmable
completion, backed by the bundled Carapace command specifications.

Carapace and any helper it starts run inside a fresh unprivileged network
namespace. If the kernel denies that isolation, completion fails closed. No
command line, path, container name or other user data is sent over the network.

The feature is enabled by Bash's standard completion loader. Variables set
before the bash-completion block in `~/.bashrc`:

```bash
export ANDUINOS_GUESS_COMMAND=0       # disable the package entirely
export ANDUINOS_GUESS_ENGINE=0        # keep Tab completion, disable ghost text
export ANDUINOS_GUESS_STATIC=0        # disable Carapace Tab completion
export ANDUINOS_GUESS_HISTORY=0       # disable history import and learning
export ANDUINOS_GUESS_STATIC_TIMEOUT=100ms
```

Removing the package removes the loader, native frontend, engine and Carapace.
The next Bash session is stock Readline again; no user dotfile is modified.

Builds fetch only fixed, checksummed Carapace release archives. Installation and
normal shell startup never access the network. The native frontend uses a small
vendored declaration of Bash's stable loadable-builtin/Readline ABI, so cross
builds do not depend on host Bash development headers.
