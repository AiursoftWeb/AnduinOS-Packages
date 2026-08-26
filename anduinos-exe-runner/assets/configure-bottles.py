#!/usr/bin/env python3
"""Configure an AnduinOS bottle using the API sequence from Bottles 66.9 CLI."""

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio


PKG_DATA_DIR = "/app/share/bottles"
DEPENDENCIES = ("cjkfonts", "allfonts")


def fail(message):
    print(f"Error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def require_success(result, operation):
    if result is None or not result.ok:
        detail = getattr(result, "message", "") if result is not None else ""
        fail(f"{operation} failed{f': {detail}' if detail else ''}")


def main():
    if len(sys.argv) != 2:
        fail("expected exactly one bottle name")

    bottle_name = sys.argv[1]
    sys.path.insert(1, PKG_DATA_DIR)

    data_resource = Gio.Resource.load(f"{PKG_DATA_DIR}/data.gresource")
    bottles_resource = Gio.Resource.load(f"{PKG_DATA_DIR}/bottles.gresource")
    data_resource._register()
    bottles_resource._register()

    # Imports intentionally follow resource registration, as Bottles does at startup.
    from bottles.backend.managers.manager import Manager
    from bottles.backend.state import EventManager, Events
    from bottles.frontend.params import APP_ID

    manager = Manager(g_settings=Gio.Settings.new(APP_ID), is_cli=True)

    # Keep the same synchronous initialization order as bottles-cli new.
    require_success(
        manager.checks(install_latest=False, first_run=True),
        "Initial Bottles checks",
    )
    for event in (
        Events.ComponentsOrganizing,
        Events.DependenciesOrganizing,
        Events.InstallersOrganizing,
    ):
        EventManager.wait(event)
    require_success(
        manager.checks(install_latest=True, first_run=False),
        "Bottles catalog update",
    )

    manager.check_bottles()
    bottle = manager.local_bottles.get(bottle_name)
    if bottle is None:
        fail(f"bottle '{bottle_name}' was not found")

    for dependency_name in DEPENDENCIES:
        manifest = manager.dependency_manager.get_dependency(dependency_name)
        if not manifest:
            fail(f"dependency '{dependency_name}' is unavailable")

        print(f"Installing {dependency_name}...", flush=True)
        require_success(
            manager.dependency_manager.install(
                bottle,
                [dependency_name, manifest],
            ),
            f"Installing {dependency_name}",
        )

    print("Bottles configuration completed.", flush=True)


if __name__ == "__main__":
    main()
