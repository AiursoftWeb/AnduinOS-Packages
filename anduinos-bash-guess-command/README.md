# anduinos-bash-guess-command

Offline ghost-text suggestions for interactive Bash. The package combines
ble.sh's history prefix search with Carapace's static command specifications.
History is preferred; static completion is used as a fallback. Suggestions are
never executed automatically and no command line or path is sent over a network.
Command-line syntax, filenames, variables and completion menus retain Bash's
normal uncolored appearance; only the pending ghost-text suggestion is dimmed.

Right Arrow accepts the whole visible suggestion when the cursor is at the end
of the line. Alt-Right accepts one word. Tab, Enter, Ctrl-C, Ctrl-D and Ctrl-R
retain their normal roles.

The feature is enabled by default through Bash's standard completion loader.
Set these variables earlier in `~/.bashrc`, before its bash-completion block:

```bash
export ANDUINOS_GUESS_COMMAND=0       # disable everything
export ANDUINOS_GUESS_HISTORY=0       # disable history suggestions
export ANDUINOS_GUESS_STATIC=0        # disable Carapace suggestions
export ANDUINOS_GUESS_DELAY=25        # idle delay in milliseconds
export ANDUINOS_GUESS_COLOR='fg=242'  # ble.sh face specification
export ANDUINOS_GUESS_STATIC_TIMEOUT=50ms
```

Removing `anduinos-bash-guess-command` removes the system loader, ble.sh and
Carapace together. New Bash sessions then use the original line editor without
requiring edits to `~/.bashrc`.

Builds fetch fixed, checksummed upstream release archives through `download.sh`.
Installation and normal shell startup do not access the network.
