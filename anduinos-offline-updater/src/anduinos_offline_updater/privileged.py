"""Fixed Polkit entry point. No caller-supplied commands, keys or APT options."""
import argparse
import json
import os
from pathlib import Path

from . import jobs
from .manifest import require


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("prepare").add_argument("file", type=Path)
    command = sub.add_parser("start"); command.add_argument("job"); command.add_argument("plan")
    for name in ("run", "discard"):
        sub.add_parser(name).add_argument("job")
    args = parser.parse_args(argv)
    try:
        require(os.geteuid() == 0, "Administrator authorization is required")
        uid = int(os.environ.get("PKEXEC_UID", "0"))
        # Do not inherit custom Python/APT/loader/configuration settings from callers.
        os.environ.clear()
        os.environ.update(PATH="/usr/sbin:/usr/bin:/sbin:/bin", HOME="/root", LANG="C.UTF-8",
                          LC_ALL="C.UTF-8", DEBIAN_FRONTEND="noninteractive")
        os.umask(0o022)
        if args.action == "run":
            require(uid == 0, "Installation workers are started by the system service")
            from .install import execute
            return execute(args.job)
        if args.action == "prepare": result = jobs.prepare(args.file, uid)
        elif args.action == "start": result = jobs.start(args.job, args.plan, uid)
        else: result = jobs.discard(args.job, uid)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"state": "blocked", "blockers": [str(error)]}))
        return 2
