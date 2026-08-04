# Quiet Engine architecture

This directory describes the production suggestion pipeline. The semantic
engine and native Readline frontend replaced the previous line-editor layer in
version `1.0.0-16`.

## Boundary

The foreground engine is a deterministic function:

```text
(line, cursor, monotonic time, immutable world snapshot) -> zero or one suggestion
```

It cannot execute commands, read directories, access the network, or mutate the
world. A background observer owns all slow work and atomically publishes
versioned snapshots between prompts.

## Query stages

1. Parse incomplete shell syntax into tokens and an active simple command.
2. Classify the cursor into a semantic slot with an explicit type allow-list.
3. Ask domain generators for structured, evidenced candidates.
4. Reject candidates that violate type, freshness, risk, or append-only rules.
5. Rank eligible candidates and return one quiet ghost-text insertion.

`None` is a first-class correct result. A source cannot bypass the arbiter and a
filesystem candidate can never enter a container, process, service, or Git-ref
slot.

## Non-negotiable foreground invariants

- No process creation, filesystem queries, or network access.
- Suggestions strictly extend the visible input at the cursor.
- Suggestions contain no newline, carriage return, NUL, or control byte.
- Enter never accepts invisible text; acceptance belongs to the frontend.
- Entity candidates depend on a matching snapshot generation and expiry.
- A crash or unavailable snapshot produces silence, never impaired Bash input.

## Implemented runtime slice

Version `1.0.0-13` ships the first production slice:

- a 385 KiB stripped `anduinos-quietd` process per interactive Bash session;
- a hex-framed protocol that cannot be injected by command text;
- persistent coprocess pipes, with a 10 ms fail-closed frontend deadline;
- typed apt-update and Docker-list observations;
- background Docker snapshot prewarming and post-list refresh;
- generation tickets that prevent an older refresh racing over newer state;
- authoritative-empty responses that block unrelated history/file fallbacks;
- apt, Docker, and guarded `git clean` decisions through the common arbiter.

The foreground query path remains process-free. Runtime tests issue 100 queries
through the real pipes and assert both low latency and an unchanged Docker
invocation count. The legacy context providers remain temporarily available for
semantic domains not yet migrated.

Version `1.0.0-14` moves process IDs, systemd services, and Git refs into the
same model. The observation protocol carries the post-command working directory
so Git snapshots cannot cross repository boundaries. `ps`, `systemctl`, and
`git for-each-ref` run only during background prewarming or prompt observation;
runtime tests record their invocation counts and require them to remain
unchanged across foreground queries. The legacy live-context source and its
observer hook are no longer installed in the completion chain.

Version `1.0.0-16` removes the alternate line editor entirely. A roughly 10 KiB
Bash loadable builtin wraps Readline redisplay, draws a one-row dim suffix and
binds only Right Arrow for acceptance. It forks the existing engine between
prompts and talks over a private Unix socket. Enter, bracketed paste, multiline
parsing, history, modes and all other editing remain owned by native Readline.
The renderer suppresses suggestions that would wrap, and every frontend error
fails to a stock Bash session. PTY contracts require normal multi-command paste,
visible ghost text, exact Enter behavior and the absence of editor UI banners.

Version `1.0.0-17` adds an in-process command skeleton layer for common Docker,
Git, systemctl, kubectl, Cargo, .NET and Node workflows. Empty subcommand slots
receive one conservative safe default; typed ambiguous prefixes extend only a
shared prefix and never authorize an arbitrary subcommand. The table is static,
auditable and adds no process or filesystem work to keystroke queries.

Version `1.0.0-18` applies the same conservative-default rule to an empty apt
action slot: `apt ` proposes `update`, while typed ambiguous prefixes still use
only a shared extension and a successful update promotes the upgrade workflow.

Version `1.0.0-19` introduces the general coverage layers. A compact compiled
registry supplies safe defaults and shared-prefix actions for more than 40 CLIs.
The personal layer imports filtered Bash history, learns successful commands in
a bounded mode-0600 state file, and ranks by cwd, frequency and recency; secrets
are rejected before persistence and learned commands pass through the common
arbiter. A versioned current-directory snapshot adds `cd` and read-only path
completion without foreground filesystem access. The real daemon remains near
400 KiB and measured foreground pipe P95 remains under 2 ms.

Version `1.0.0-20` gives a small, general ranking advantage to a safe default
when a typed prefix is still ambiguous (`docker p` therefore extends to `ps`),
without inventing a choice where no default matches (`git c` stays quiet).
Leading-whitespace commands are excluded from learning, matching Bash users'
common `HISTCONTROL=ignorespace` privacy expectation.

Version `1.0.0-21` removes hand-maintained verb lists as the primary grammar
source. A build-time extractor runs the pinned Carapace binary with an empty
home, empty executable path and empty working directory, accepts only validated
root-subcommand grammars, and compiles the result into the engine. Dynamic root
completions are rejected so build-host files, accounts and hostnames cannot
enter the package. More than 500 generated unique-prefix contracts exercise the
whole index. Personal history matching now normalizes equivalent `sudo`
wrappers, and grammar ambiguities of at most three safe candidates emit quiet,
low-confidence ghost text instead of disappearing.

Version `1.0.0-22` makes argument token boundaries an explicit apt-domain
contract: a suggestion after a command token with no trailing whitespace must
insert the separator itself. Both wrapped and unwrapped forms are tested, so
`sudo apt` can only extend to `sudo apt update`, never `sudo aptupdate`.

Version `1.0.0-23` separates grammatical validity from semantic prominence.
Generated Carapace actions remain the legality boundary, while a compact policy
tier promotes ordinary human-facing actions over plumbing commands after a
meaningful prefix (`git che` therefore selects `checkout`). Entity listing
order is no longer treated as intent: multiple Docker containers produce only
a shared typed-prefix extension, or silence when no container is distinguished.

Version `1.0.0-24` removes runtime Carapace and leaves programmable Tab
completion entirely untouched. Carapace remains only an optional, pinned
development tool for regenerating the compiled root-subcommand grammar. The
installed package now owns only ghost-text redisplay, Right Arrow acceptance and
prompt-time observation; a regression contract verifies that loading it
preserves an existing Bash completion registration byte-for-byte.

Version `1.0.0-25` adds a compact contextual ranking layer inspired by mature
history search and autosuggestion systems. A bounded adjacent-command graph
ranks likely successors by previous command, cwd, frequency and recency. A
background verifier turns successful `ssh-keygen`, `mkdir`, `git clone` and
Python venv actions into expiring artifact facts; Docker, process, service and
Git workflows use typed entity snapshots rather than terminal output. Bounded
three-level path snapshots, SSH aliases, more than 90 root CLI policies and over
370 nested actions improve cold-start coverage. The installed amd64 frontend and
engine remain below 600 KiB combined, with a measured real-pipe P95 of 1 ms.
PTY contracts now exercise full-command prediction, transition ranking,
artifact handoff, SSH aliases, multiline paste, exact Enter behavior, Right
Arrow acceptance and unchanged programmable Tab completion.

Version `1.0.0-26` normalizes an explicit `./` for current-directory snapshot
matching while preserving it in the rendered suggestion. Hidden-path common
prefixes such as `cat ./.ba` now extend safely to `cat ./.bash` instead of being
silent.

Version `1.0.0-27` removes the blanket dangerous-command completion penalty:
ghost text is not execution, and Enter remains the user's confirmation boundary.
Failed and credential-bearing commands are still excluded from learning. A
narrow guard prevents one-shot history replay of a complete `dd` write to a
`/dev/` device. Independently, `dd if=` and `dd of=` now have structured path
slots; `if=/` may suggest `/dev/`, absolute device paths and relative image files
use verified snapshots, and an empty `of=` never invents a destination.

Version `1.0.0-28` routes `ls` and other unambiguous filesystem consumers
through the path slot even after command options. The exact root-directory case
`ls -ashl ./de` now preserves `./` and extends to `ls -ashl ./dev/` from the
prompt-time snapshot.

Version `1.0.0-29` replaces the small root-only extraction with Carapace's
explicit JSON export trees. A hermetic development-time generator compiles more
than 700 commands and 7,500 multi-level nodes into an approximately 900 KiB TSV
corpus; the runtime uses sorted binary lookup and contains no Carapace or Go
code. Local and persistent flags become typed option candidates, Docker's
grouped/legacy aliases and Compose v2 paths share grammar, and ambiguous command
names extend only to their common prefix rather than selecting an arbitrary
program. More than 6,500 subcommand and 20,000 option unique-prefix contracts
exercise the public prediction entry point. A second hermetic probe containing
only synthetic filenames identifies over 2,000 positional path slots, while a
small audited overlay handles common path-valued flags such as `kubectl -f`,
`curl -o`, `git -F` and `docker build -f`.
