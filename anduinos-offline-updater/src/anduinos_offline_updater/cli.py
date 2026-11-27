"""Command-line entry point for verifying and installing offline updates."""
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


def call_helper(*args):
    from .jobs import HELPER

    result = subprocess.run([HELPER, *args], capture_output=True, text=True, check=False)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise BundleError("Could not read the installation response") from error
    if result.returncode:
        raise BundleError(report.get("error") or "; ".join(report.get("blockers", [])) or "Installation request failed")
    return report


def show_plan(plan):
    print(f'AnduinOS {plan["current_version"]} → {plan["target_version"]}')
    changes = [change for stage in plan["stages"] for change in stage["changes"]]
    print(f"Package changes: {len(changes)}")
    for change in changes:
        before = change["before"] or "not installed"
        after = change["after"] or "removed"
        print(f'  {change["name"]}:{change["architecture"]}  {before} → {after}')


def apply_update(file, assume_yes, *, allow_foreign_removals=False):
    if os.geteuid() != 0:
        print("Use sudo anduinos-offline-updater apply to authorize installation.", file=sys.stderr)
        return 2

    job_id = None
    try:
        print("Checking the update file…", flush=True)
        arguments = ["prepare", str(file.absolute())]
        if allow_foreign_removals:
            arguments.append("--allow-foreign-removals")
        job = call_helper(*arguments)
        job_id = job["job_id"]
        print(f"Update job: {job_id}", flush=True)
        for step in range(2):
            plan = job["preview"]
            if step:
                print("Updater upgraded. Remaining update plan")
            show_plan(plan)
            if plan["state"] == "already-installed" and not step:
                call_helper("discard", job_id)
                print("You already have this update.")
                if Path("/run/reboot-required").exists():
                    print("Restart your computer to finish applying the update.")
                return 0
            if plan["state"] != "already-installed" and not assume_yes:
                try:
                    question = "Remove the listed foreign packages and install this plan? [y/N] " if plan.get("foreign_removals") else "Install this plan? [y/N] "
                    confirmed = input(question).strip().lower() in {"y", "yes"}
                except EOFError:
                    confirmed = False
                if not confirmed:
                    if step:
                        print(f"Remaining update cancelled. Review it with sudo anduinos-offline-updater status {job_id}.")
                    else:
                        call_helper("discard", job_id)
                        print("Update cancelled.")
                    return 1

            print("Installing the update…", flush=True)
            job = call_helper("start", job_id, job["plan_sha256"])
            if job["state"] in {"complete", "already-installed"}:
                print("Update complete.")
                if job.get("reboot_required"):
                    print("Restart your computer to finish applying the update.")
                return 0
            if job["state"] != "prepared" or not job.get("confirmation_required"):
                detail = job.get("error") or job["state"]
                raise BundleError(detail)
        raise BundleError("The update requested another confirmation. Review the saved job.")
    except KeyboardInterrupt:
        print("\nUpdate interrupted. Check the system package state before retrying.", file=sys.stderr)
        exit_code = 130
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Update failed: {error}", file=sys.stderr)
        exit_code = 2
    if job_id:
        print(f"Check the update with sudo anduinos-offline-updater status {job_id}.", file=sys.stderr)
    return exit_code


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv == ["gui"]:
        from .gui import main as gui_main
        return gui_main()
    if argv == ["cleanup"]:
        from . import jobs
        try:
            cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "anduinos-offline-updater"
            jobs.clean_previous_boot(cache, jobs.PREVIEW_NAME, os.getuid())
            if jobs.needs_cleanup(jobs.ROOT):
                prefix = [] if os.geteuid() == 0 else ["/usr/bin/pkexec"]
                subprocess.run([*prefix, jobs.CLEANUP_HELPER], check=True)
            print("{}")
            return 0
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(json.dumps({"blockers": [str(error)]}))
            return 2
    client_version = running_version()
    parser = argparse.ArgumentParser(description="Inspect or install a signed AnduinOS offline update.")
    parser.add_argument("--version", action="version", version=client_version)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("apply", help="Review and install a signed update in the terminal")
    command.add_argument("file", type=Path)
    command.add_argument("-y", "--yes", action="store_true", help="Confirm installation plans without prompting")
    command.add_argument("--allow-foreign-removals", action="store_true",
                         help="Allow removal of listed non-native packages after plan review")
    command = sub.add_parser("prepare", help="Import and preview with administrator authorization; does not install")
    command.add_argument("file", type=Path)
    command.add_argument("--allow-foreign-removals", action="store_true",
                         help="Prepare a plan that may remove non-native packages")
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
            command.add_argument("--allow-foreign-removals", action="store_true")
            command.add_argument("--system-root", type=Path, default=Path("/"),
                                 help="Read captured system state from a different root; no files there are changed")
    args = parser.parse_args(argv)
    if args.command == "apply":
        return apply_update(args.file, args.yes, allow_foreign_removals=args.allow_foreign_removals)
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
            if action == "prepare" and args.allow_foreign_removals:
                values.append("--allow-foreign-removals")
            return subprocess.run(prefix + [jobs.HELPER, action, *values], check=False).returncode
        except (ValueError, OSError) as error:
            print(json.dumps({"state": "blocked", "blockers": [str(error)]}))
            return 2
    try:
        with tempfile.TemporaryDirectory(prefix="offline-updater-", dir=args.work_directory) as name:
            result = verify(args.file, args.keyring, name)
            if args.command == "inspect":
                report = preview(result["manifest"], Path(name) / "repository", args.keyring, args.system_root,
                                 args.work_directory, client_version=client_version,
                                 allow_foreign_removals=args.allow_foreign_removals)
                report.update(verification="verified", signer=result["signer"], archive_bytes=args.file.stat().st_size)
            else:
                m = result["manifest"]
                report = {"state": "verified", "target_version": m["target_version"], "batch_id": m["batch_id"],
                          "signer": result["signer"], "package_count": len(m["packages"]),
                          "unpacked_bytes": result["unpacked_bytes"], "required_installer_features": m.get("required_installer_features", []),
                          "read_only": True, "can_install": False}
    except (BundleError, OSError) as error:
        report = {"state": "blocked", "verification": "unverified", "blockers": [str(error)],
                  "read_only": True, "can_install": False}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f'Status: {report["state"]}')
        if "target_version" in report:
            print(f'Target: {report["target_version"]}')
        for reason in report.get("blockers", []):
            print(reason)
        print("Read-only preview. No system packages were changed.")
    return 2 if report["state"] in ("blocked", "foreign-removal-required") else 0


if __name__ == "__main__":
    raise SystemExit(main())
