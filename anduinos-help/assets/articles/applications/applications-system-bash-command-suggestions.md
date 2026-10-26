# Bash Command Suggestions

AnduinOS can show a quiet, grey completion after the text you type in an interactive Bash terminal. The suggestion is generated locally and is never executed automatically.

For example, after a successful `sudo apt update`, typing `sudo apt up` may suggest `grade`. Suggestions can also use installed APT packages, Docker containers, systemd services, Git branches, SSH aliases, command history, and files in the current project.

## Accept or ignore a suggestion

- Press <kbd>Right Arrow</kbd> or <kbd>End</kbd> while the cursor is already at the end of the line to accept the visible suggestion.
- Continue typing to refine or replace it.
- Press <kbd>Enter</kbd> to execute only the command that is visibly present on the command line.
- Press <kbd>Tab</kbd> for Bash's normal completion. AnduinOS does not replace or redefine Tab completion.

When the cursor is in the middle of a command, <kbd>End</kbd> keeps its normal behavior and moves the cursor to the end instead of accepting hidden text.

!!! warning "Read every command before pressing Enter"

    Suggestions may include administrative or destructive commands from your own history. Accepting a suggestion only inserts text; pressing Enter remains the separate confirmation that executes it.

## What the feature learns

The engine starts with an offline command index and reads local system information. It does not contact an online suggestion service.

It can rank suggestions using:

- the current Bash history;
- commands that succeeded earlier in the current shell;
- the current directory and nearby files;
- installed commands and APT package metadata;
- Docker containers, running processes, systemd services, Git references, and SSH aliases when applicable.

Obvious credential-bearing command forms are excluded from learning. This is a precaution, not a general secret detector; avoid putting passwords and tokens directly in shell commands.

By default, new learning remains in the current shell process. AnduinOS does not create a second persistent command log unless you explicitly enable it.

## Turn suggestions on or off

The **Bash Command Predictions** switch in [Welcome Center](../Welcome-Center/Welcome-Center.md) controls the normal user-facing setting. Open a new terminal after changing it.

For a temporary test in one terminal, run:

```bash title="Disable suggestions in the current shell"
export ANDUINOS_GUESS_COMMAND=0
```

The change takes effect at the next prompt or redisplay. Start a new terminal to return to the normal configured behavior.

Advanced users can put one or more of these settings in `~/.bashrc`:

```bash title="Advanced Bash suggestion settings"
export ANDUINOS_GUESS_COMMAND=0  # Disable the complete feature and helper
export ANDUINOS_GUESS_ENGINE=0   # Disable visible ghost text
export ANDUINOS_GUESS_HISTORY=0  # Disable history import and learning
export ANDUINOS_GUESS_PERSIST=1  # Keep additional learning across sessions
```

Persistent learning is stored with private permissions under:

```text
~/.local/state/anduinos-bash-guess-command/
```

Setting `ANDUINOS_GUESS_HISTORY=0` disables history import, session learning, and persistent learning even when persistence was enabled separately.

## Troubleshooting

Suggestions appear only in an interactive Bash session. They do not appear in another shell such as Zsh, in a non-interactive script, or when the feature is disabled in the environment.

If an application installed during the current terminal session is not suggested, open a new terminal. The command search path is scanned once when the Bash helper starts.

To return completely to standard Bash behavior, remove the package:

```bash title="Remove Bash command suggestions"
sudo apt remove anduinos-bash-guess-command
```

The package does not modify `~/.bashrc` during installation or removal.
