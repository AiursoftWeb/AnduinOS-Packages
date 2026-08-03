# anduinos-bash-guess-command

Offline ghost-text suggestions for interactive Bash. The package combines a
small typed-context source, locally ranked Bash history and Carapace command
specifications. The sources form a staged fast path: unique local entities come
first, frequently reused recent history comes next, and broad static completion
is the fallback. Suggestions are never executed automatically and no command
line or path is sent over a network.
Command-line syntax, filenames, variables and completion menus retain Bash's
normal uncolored appearance; only the pending ghost-text suggestion is dimmed.
Failed commands also retain Bash's normal behavior without an additional
`[ble: exit N]` marker.

Carapace and any helper it starts run inside a fresh unprivileged network
namespace. If the kernel does not permit that isolation, static suggestions
fail closed while history and local typed-context suggestions continue.

Typed context is deliberately narrow and local. It completes Docker container
names and IDs, process IDs, systemd services and current-repository Git branches.
It also recognizes a few high-confidence command transitions. It can carry a
simple intent across one successful command, for example:

```bash
docker ps | grep mysql        # then: docker exec -it mysql-db
ps aux | grep mysqld          # then: sudo kill 4242
systemctl list-units | grep docker  # then: systemctl status docker.service
sudo apt update                # then: sudo apt upgrade
```

The package does not capture terminal output. It remembers only the successful
filter word in shell memory and re-queries the corresponding structured local
source with a hard timeout. Results are cached for a few seconds and are never
written to a user database. Unique matches receive a complete suggestion;
ambiguous matches receive only a shared prefix, and unusably weak matches stay
silent. Sensitive history values are suppressed, while destructive history is
ranked below a safe alternative with the same prefix.

Right Arrow accepts the whole visible suggestion when the cursor is at the end
of the line. Alt-Right accepts one word. Tab, Enter, Ctrl-C, Ctrl-D and Ctrl-R
retain their normal roles.

The feature is enabled by default through Bash's standard completion loader.
Set these variables earlier in `~/.bashrc`, before its bash-completion block:

```bash
export ANDUINOS_GUESS_COMMAND=0       # disable everything
export ANDUINOS_GUESS_HISTORY=0       # disable history suggestions
export ANDUINOS_GUESS_STATIC=0        # disable Carapace suggestions
export ANDUINOS_GUESS_CONTEXT=0       # disable live typed-context suggestions
export ANDUINOS_GUESS_DELAY=25        # idle delay in milliseconds
export ANDUINOS_GUESS_COLOR='fg=242'  # ble.sh face specification
export ANDUINOS_GUESS_STATIC_TIMEOUT=50ms
export ANDUINOS_GUESS_CONTEXT_TIMEOUT=120ms
export ANDUINOS_GUESS_HISTORY_SCAN=300 # recent entries used for local ranking
export ANDUINOS_GUESS_TRIM_PASTE_NEWLINE=0 # preserve a pasted final newline
```

Removing `anduinos-bash-guess-command` removes the system loader, ble.sh and
Carapace together. New Bash sessions then use the original line editor without
requiring edits to `~/.bashrc`.

Builds fetch fixed, checksummed upstream release archives through `download.sh`.
Installation and normal shell startup do not access the network.
