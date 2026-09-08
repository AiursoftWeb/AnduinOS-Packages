# Package Development Guide

This guide covers the conventions for maintaining AnduinOS packages.
Package-specific implementation details belong beside the package source;
the [README](README.md) contains the repository overview.

## Package layout and ownership

Keep each package's source, resources, tests, and build helpers in its own
directory alongside its `.aosproj`.

- `src/`: application code and executable entry points.
- `data/`: desktop entries, icons, service units, and other integration files.
- `tests/`: tests owned by the package.
- `po/`: translation sources.
- `screenshots/`: images referenced by AppStream declarations.
- `bin/`, `obj/`, and `target/`: ignored build output.

Use `assets/` or `resources/` when they describe the package's structure
better. Do not move existing files solely to standardize directory names.
Do not put package-specific assertions in shared helpers or make one package's
tests scan unrelated packages.

## Choosing a packaging approach

Choose based on file ownership and upgrade behavior, not just whether a
change is branding.

- Use a standalone package for new applications, resources, or configuration
  that can coexist with upstream packages.
- Derive from an upstream package when its contents must be inherited and
  modified. Review inherited dependencies and maintainer scripts.
- When replacing an upstream package, explicitly review package identity,
  `Provides`, `Conflicts`, and `Replaces`, as applicable. Do not assume that
  `Replaces` alone makes two packages safe to co-install.
- Check exact-version dependencies between upstream packages before changing
  a replacement package's version or identity.

Use maintained projects as references rather than copying incomplete XML
templates: [base-files](base-files/base-files.aosproj) derives system identity
files, while [plymouth-anduinos](plymouth-anduinos/plymouth-anduinos.aosproj)
derives and relocates an upstream theme.

## Versions and target selection

Bump `PackageVersion` when changing installed code, resources, translations,
dependencies, or maintainer scripts. A successful pipeline does not imply
that changed source was published under an already existing version.

Keep `TargetSuites`, `TargetArchitectures`, conditional inputs, and upstream
suite mappings consistent. Declare only targets the package supports.

When output differs between suites, use distinct versions. In particular,
different `arch=all` payloads must not share the same package name and version
across suites. Use `$(SuiteShortName)` with `SuiteShortNameMap`, or `$(Suite)`,
to distinguish suite-specific output. A condition alone does not require a
version suffix if the resulting packages are identical.

For upstream-derived packages, `$(UpstreamVersion)` can be part of the version
expression; retain a revision component that can be bumped for local changes.
Do not assume every derived package uses the same version formula.

## Local validation and CI

Run the package's documented source tests and lint its project before a
build. To lint and build a selected package:

```bash
cd path/to/package
apkg lint
apkg test --profile anduinos-package-release-test
apkg build --all
```

From the repository root, validate CI package coverage and dependency ordering:

```bash
python3 lib/verify-ci-package-needs.py
apkg test --path . --recursive --profile anduinos-package-release-test --report test-results.xml
```

The current [.gitlab-ci.yml](.gitlab-ci.yml) defines:

- `lint-all`: runs `apkg lint` for every package.
- `verify-ci-package-needs`: checks package job coverage, internal
  `Dependency`/`Recommend`/`Suggest` edges, dependency cycles, and mandatory gates.
- `test-all`: waits for lint, then runs only `anduinos-package-release-test`
  across all packages and publishes a JUnit entry-level report.
- Package jobs: merge requests and non-release branches run
  `apkg build --all`; `master` deploys to the development repository and
  `prod` deploys to the production repository.

Every package job must explicitly need `lint-all`, `verify-ci-package-needs`,
and `test-all`, in addition to its internal dependencies. This applies to
merge requests, `master`, `prod`, and other branch builds. Publishing
uses `apkg deploy --all --skip-existing`: when preflight confirms that all
requested targets already exist, the build is skipped.
`--skip-duplicate` is not equivalent; it still builds before skipping
duplicate uploads.

Tests run before package construction even when publishing will skip an
existing version. Do not put tests in `PrebuildCommand` or call them from
`build.sh`. Build hooks remain for constructing production binaries and assets.

### Test entry points and profiles

Keep standalone test code, fixtures, and entry scripts in the package's
`tests/` directory. Rust unit tests may stay beside the code they exercise.
Use a direct command for a simple suite; add a package-owned entry script only
when setup or profile selection requires it. Do not add a wrapper per test.
Keep GUI and model-dependent Python suites in `tests/gui/` and `tests/native/`,
outside ordinary source discovery. Select those directories explicitly rather
than maintaining lists of individual test method names.

```xml
<TestCommand Name="source" Profile="anduinos-package-release-test"
             Run="bash tests/check-source.sh" TimeoutSeconds="600" />
```

Commands run in the package directory, with `APKG_TEST_PROFILE` set. Profile
selection is explicit and exact; it is not a build matrix or a request to
compile every package first. Tests may compile their own minimal artifacts,
but must not build, unpack, or install the package's deb. Entry names are
unique within their profile. Use `apkg test --profile PROFILE --list` to inspect
commands without running them.

The release profile contains deterministic source/native tests. `gui`,
`voice-native`, `voice-cpu`, `desktop-voice`, `performance`,
and `root-loopback` isolate prerequisites that
are not part of ordinary CI. Run only profiles suitable for the machine;
root/loopback and boot qualification require disposable environments.
Package-specific environment variables and manual qualification instructions
belong in that package's documentation.

Missing prerequisites must fail an entry, not silently skip its tests.
Separate timing benchmarks from shared-runner correctness gates. Synchronize
asynchronous tests on observable readiness with bounded deadlines instead of
assuming a fixed sleep is long enough. Tests of maintainer scripts must isolate
both filesystem paths and external commands; a temporary boot directory does
not make calls to the host's service manager safe.
Apkg evaluates command exit status, timeout, and cancellation; it does not
parse unittest, Cargo, or other frameworks' internal results. The JUnit
report records one case per entry. `NotConfigured` means no matching profile,
not a pass, and resource-only packages need not invent tests.

CI pins the new Apkg CLI version. During joint Apkg/package development, use
the locally built CLI; publish that version only after both repositories are
verified. Do not push a CI migration that requires an unavailable CLI version.

## Test quality: behavior, not implementation snapshots

A test must protect an observable outcome, a supported contract, or a safety
boundary. Before adding it, answer: **What realistic defect would make this
test fail, and why would that defect matter to a user?** If the only answer is
"the implementation changed," do not add the test.

| Do not test | Test instead, when useful |
|---|---|
| A directory currently contains exactly five files | Processing inputs produces the correct results and handles missing or invalid inputs |
| A version, default size, application ID, or command array equals a copied literal | Version comparison, size validation, routing, or command execution behaves correctly |
| Source text contains a function name, API call, UI label, or specific coding pattern | Calling the real code produces the expected effect, including failure paths |
| A `.deb` contains the files, permissions, dependencies, or metadata declared in `.aosproj` | Nothing in this repository: generic packaging behavior belongs to Apkg's own tests |
| A resource list or dependency list equals another copy of itself | A genuine consistency requirement or resource validity, without duplicating the inventory |
| A mock returns the value configured by the test | Real application code handles the dependency's success, failure, timeout, or cancellation correctly |

Use these rules when writing or reviewing tests:

- Exercise production code. Mock external boundaries such as hardware, network,
  privileged commands, or clocks; do not mock the behavior being verified or
  reimplement it inside the test.
- Derive expected results from requirements and controlled inputs, not from
  the same calculation or constant that the implementation uses. Exact values
  are appropriate for specified protocol responses and calculated outputs;
  they are not appropriate merely because they occur in today's source.
- Prefer useful failure cases: rejected hostile input causes no mutation;
  failed writes preserve the previous configuration; cancellation prevents
  late results; retries are idempotent; unrelated user data stays untouched.
- Checking that a file exists **after an operation creates it** can be a valid
  behavior assertion. Checking that a checked-in file still exists, or counting
  the current files, is not a substitute for testing behavior. Likewise, valid
  resource syntax and translation placeholders are contracts, not inventories.
- Keep tests independent of harmless refactoring, formatting, version bumps,
  and additions to resource lists. Do not freeze private helper names, source
  line order, UI dimensions, or implementation-specific data structures.
- Do not build, unpack, or install a `.deb` for package tests. Compilation of
  a test program or minimal native component is allowed. Do not recreate Apkg's
  file expansion, condition evaluation, or packaging logic in source tests.
- Isolate destructive and privileged operations from the host. Document any
  hardware, GUI, or VM prerequisites; an unavailable prerequisite or skipped
  test is not evidence that the behavior passed.

Delete tests whose only purpose is to repeat the implementation. A replacement
is not required, and fewer meaningful tests are preferable to more redundant
ones. Pure resource packages do not need invented tests to satisfy a count.
When removing a test, remove its unused helpers and execution references too.
Never update an expected value mechanically just to make a failure green:
first decide whether the requirement was broken or the assertion had no value.

## Desktop and Control Panel integration

Choose visibility according to the intended entry point:

- Control Panel-only tools may deliberately use `NoDisplay=true`.
- Standalone applications should provide a visible desktop entry.
- The Control Panel itself remains a user-facing launcher.

Do not apply a blanket rule that every settings tool must appear in the
applications menu. Check that Control Panel commands and desktop `Exec`
targets match the application's installed entry points. Being a page in the
AnduinOS Control Panel does not make an application a GNOME Control Center
plugin.

Validate desktop entry syntax when modifying a launcher:

```bash
desktop-file-validate path/to/application.desktop
```

Keep desktop IDs, icons, and AppStream declarations consistent. Menu
visibility and AppStream metadata serve different purposes; one does not
prove the other is correct.

## Boot stack policy

AnduinOS uses Dracut exclusively and no longer supports `initramfs-tools`.
Package dependencies, initramfs generation, and early-boot hooks must target
Dracut. Do not introduce dependencies on the legacy stack or add hooks for it.
Existing migration and compatibility code supports the transition to Dracut,
not ongoing support for two generators.

See the [Dracut migration design](anduinos-dracut-migration/DESIGN.md) for the
migration and recovery contracts.

## Settings databases and maintainer scripts

Do not duplicate global settings-cache updates in individual packages'
maintainer scripts.

- Install AnduinOS dconf defaults under `/etc/dconf/db/anduinos.d/`.
  [anduinos-dconf-runtime](anduinos-dconf-runtime/scripts/postinst.sh) owns
  the database update: it runs `dconf update` on live systems and
  `dconf compile` in chroots to avoid host D-Bus notifications.
- Leave compilation of system-wide schemas under
  `/usr/share/glib-2.0/schemas/` to the system's package triggers.
- Compile extension-private schemas during the build and ship
  `gschemas.compiled`; the global schema trigger does not cover those paths.

Keep maintainer scripts idempotent and safe in installation chroots. Do not
hide failures of operations required for a usable or bootable system.
