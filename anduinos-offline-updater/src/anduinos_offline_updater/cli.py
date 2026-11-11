"""Command-line entry point for read-only bundle verification and preview."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import VERSION
from .bundle import verify
from .native import BundleError, run
from .planning import preview


def running_version():
    """Keep the Debian suffix, but never claim newly installed code is loaded."""
    try:
        value = run(["dpkg-query", "--admindir=/var/lib/dpkg", "--show",
                     "--showformat=${Version}", "anduinos-offline-updater"]).decode().strip()
        if value == VERSION or value.startswith(VERSION + "+"):
            return value
    except BundleError:
        pass  # Source-tree validation has no installed package.
    return VERSION


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv == ["gui"]:
        from .gui import main as gui_main
        return gui_main()
    client_version = running_version()
    parser = argparse.ArgumentParser(description="Inspect or install a signed AnduinOS offline update.")
    parser.add_argument("--version", action="version", version=client_version)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("prepare", help="Import and preview with administrator authorization; does not install")
    command.add_argument("file", type=Path)
    command = sub.add_parser("install", help="Start exactly the previously prepared and confirmed plan")
    command.add_argument("job")
    command.add_argument("--confirm-plan", required=True, help="SHA256 returned by prepare")
    for name in ("status", "discard"):
        sub.add_parser(name).add_argument("job")
    for name in ("verify", "inspect"):
        command = sub.add_parser(name)
        command.add_argument("file", type=Path)
        command.add_argument("--keyring", type=Path, default=Path("/usr/share/keyrings/anduinos-offline-updates.gpg"),
                             help="External trusted keyring. Test signing keys are not shipped.")
        command.add_argument("--work-directory", type=Path, help="Existing temporary-storage parent directory")
        command.add_argument("--json", action="store_true")
        if name == "inspect":
            command.add_argument("--system-root", type=Path, default=Path("/"),
                                 help="Read captured system state from a different root; no files there are changed")
    args = parser.parse_args(argv)
    if args.command in ("prepare", "install", "status", "discard"):
        from . import jobs
        try:
            if args.command == "status":
                print(json.dumps(jobs.status(args.job, os.getuid()), indent=2))
                return 0
            prefix = [] if os.geteuid() == 0 else ["/usr/bin/pkexec"]
            action = "start" if args.command == "install" else args.command
            values = [str(args.file.absolute())] if action == "prepare" else [args.job]
            if action == "start": values.append(args.confirm_plan)
            return subprocess.run(prefix + [jobs.HELPER, action, *values], check=False).returncode
        except (ValueError, OSError) as error:
            print(json.dumps({"state": "blocked", "blockers": [str(error)]}))
            return 2
    try:
        with tempfile.TemporaryDirectory(prefix="offline-updater-", dir=args.work_directory) as name:
            result = verify(args.file, args.keyring, name)
            if args.command == "inspect":
                report = preview(result["manifest"], Path(name) / "repository", args.keyring, args.system_root,
                                 args.work_directory, client_version=client_version)
                report.update(signer=result["signer"], archive_bytes=args.file.stat().st_size)
            else:
                m = result["manifest"]
                report = {"state": "verified", "target_version": m["target_version"], "batch_id": m["batch_id"],
                          "signer": result["signer"], "package_count": len(m["packages"]),
                          "unpacked_bytes": result["unpacked_bytes"], "required_installer_features": m.get("required_installer_features", []),
                          "read_only": True, "can_install": False}
    except (BundleError, OSError) as error:
        report = {"state": "blocked", "blockers": [str(error)], "read_only": True, "can_install": False}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f'Status: {report["state"]}')
        if "target_version" in report:
            print(f'Target: {report["target_version"]}')
        for reason in report.get("blockers", []):
            print(reason)
        print("Read-only preview. No system packages were changed.")
    return 2 if report["state"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
