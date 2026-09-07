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
apkg build --all
```

From the repository root, validate CI package coverage and dependency ordering:

```bash
python3 lib/verify-ci-package-needs.py
```

The current [.gitlab-ci.yml](.gitlab-ci.yml) defines:

- `lint-all`: runs `apkg lint` for every package.
- `verify-ci-package-needs`: checks package job coverage, internal
  `Dependency`/`Recommend`/`Suggest` edges, and dependency cycles.
- Package jobs: merge requests and non-release branches run
  `apkg build --all`; `master` deploys to the development repository and
  `prod` deploys to the production repository.

Keep package `needs` aligned with internal dependencies. Publishing currently
uses `apkg deploy --all --skip-existing`: when preflight confirms that all
requested targets already exist, the build is skipped.
`--skip-duplicate` is not equivalent; it still builds before skipping
duplicate uploads.

Some existing packages run tests in `PrebuildCommand`. Those tests also get
skipped when the build is skipped. This is the current implementation, not an
independent test gate; do not treat a skipped package job as a fresh test pass.
The planned `apkg test` workflow is not yet available in this repository.

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
