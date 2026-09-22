#!/usr/bin/env python3
"""Build a disposable initrd from a real ISO for graphical media-check tests.

This never edits the source ISO or the host's boot configuration. QMP sockets,
screenshots and serial logs are kept in the supplied output directory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT.parent


def run(*args, **kwargs):
    return subprocess.run(tuple(map(str, args)), check=True, **kwargs)


def prepare(iso: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    stage = output / "initrd-tree"
    if not stage.exists():
        stage.mkdir()
        run("xorriso", "-osirrox", "on", "-indev", iso,
            "-extract", "/LiveOS/initrd", output / "original-initrd",
            "-extract", "/LiveOS/vmlinuz", output / "vmlinuz",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run("lsinitrd", "--unpack", output / "original-initrd", cwd=stage)

    def copy(source, destination):
        dest = stage / destination.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        dest.chmod(0o755 if str(source).endswith(".sh") or source == ROOT / "assets/anduinos-media-check" else 0o644)

    if not (stage / "usr/sbin/dmsquash-live-root.upstream").exists():
        shutil.copy2(stage / "usr/sbin/dmsquash-live-root", stage / "usr/sbin/dmsquash-live-root.upstream")
    copy(ROOT / "dracut/95anduinos-live-layers/anduinos-live-root.sh", "/usr/sbin/dmsquash-live-root")
    copy(ROOT / "assets/anduinos-media-check", "/usr/libexec/anduinos-media-check")
    shutil.copytree(ROOT / "data/media-check", stage / "usr/share/anduinos-live/media-check", dirs_exist_ok=True)
    run("/usr/lib/dracut/dracut-install", "-D", stage, "-a", "-l",
        "bash", "checkisomd5", "md5sum", "realpath", "flock", "tail", "sleep",
        "sed", "readlink", "cut", "grep", "stat", "mktemp", "mv", "chmod",
        "mkdir", "rm", "id", "findmnt", "mount", "umount", "poweroff", "awk")
    for name in (
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
    ):
        copy(Path(name), name)
    # Use the same theme/plugin/font population as Dracut's plymouth module.
    # Clear only the old fixture's theme selection before upstream rebuilds it.
    for name in ("usr/share/plymouth/themes/default.plymouth", "etc/alternatives/default.plymouth"):
        (stage / name).unlink(missing_ok=True)
    run("/usr/libexec/plymouth/plymouth-populate-initrd", "-t", stage)
    # Capture the real product report through the serial port at the pivot
    # boundary. This instrumentation neither changes nor manufactures results.
    hook = stage / "var/lib/dracut/hooks/pre-pivot/99-media-test-report.sh"
    hook.write_text("#!/bin/sh\ncat /run/anduinos-live/media-check.result > /dev/ttyS0\n"
                    "cat /run/media-plymouth-debug.log > /dev/ttyS0\n")
    hook.chmod(0o755)
    unit = stage / "usr/lib/systemd/system/plymouth-start.service"
    content = unit.read_text()
    if "--debug-file" not in content:
        unit.write_text(content.replace("--mode=boot", "--mode=boot --debug --debug-file=/run/media-plymouth-debug.log"))
    with (output / "initrd").open("wb") as target:
        files = subprocess.Popen(["find", ".", "-print0"], cwd=stage, stdout=subprocess.PIPE)
        archive = subprocess.Popen(["cpio", "--null", "-o", "-H", "newc", "--owner=0:0"],
                                   cwd=stage, stdin=files.stdout, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
        files.stdout.close()
        compressor = subprocess.Popen(["zstd", "-q", "-T2", "-3"], stdin=archive.stdout, stdout=target)
        archive.stdout.close()
        if compressor.wait() or archive.wait() or files.wait():
            raise RuntimeError("Could not pack test initrd")


def command(output: Path, execute: str, arguments=None):
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(10)
    sock.connect(str(output / "qmp.sock"))
    stream = sock.makefile("rwb", buffering=0)
    stream.readline()
    for operation in ({"execute": "qmp_capabilities"},
                      {"execute": execute, "arguments": arguments or {}}):
        stream.write(json.dumps(operation).encode() + b"\n")
        while True:
            response = json.loads(stream.readline())
            if "error" in response:
                raise RuntimeError(response)
            if "return" in response:
                break
    sock.close()
    return response


def start(iso: Path, output: Path, locale: str, mode: str, rate: int, from_iso: bool):
    pid_file = output / "pid"
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text())
            os.kill(old_pid, 0)
        except (OSError, ValueError):
            pass
        else:
            raise RuntimeError(f"QEMU pid {old_pid} is still using {output}; stop it first")
    for name in ("qmp.sock", "serial.sock"):
        (output / name).unlink(missing_ok=True)
    qemu_log = (output / "qemu.log").open("ab")
    machine = [
        "qemu-system-x86_64", "-enable-kvm", "-m", "4096", "-smp", "2",
        "-display", "none", "-device", "virtio-vga", "-no-reboot",
        "-qmp", f"unix:{output / 'qmp.sock'},server=on,wait=off",
        "-chardev", f"socket,id=serial,path={output / 'serial.sock'},server=on,wait=off,"
                    f"logfile={output / 'serial.log'},logappend=on",
        "-serial", "chardev:serial",
        "-nic", "none",
    ]
    if from_iso:
        variables = output / "OVMF_VARS.fd"
        shutil.copyfile("/usr/share/OVMF/OVMF_VARS_4M.fd", variables)
        machine += [
            "-machine", "q35",
            "-drive", "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd",
            "-drive", f"if=pflash,format=raw,file={variables}",
            "-drive", f"file={iso},media=cdrom,readonly=on,if=ide,throttling.bps-read={rate}",
            "-boot", "d",
        ]
    else:
        machine += [
            "-kernel", str(output / "vmlinuz"), "-initrd", str(output / "initrd"),
            "-append", "root=live:CDLABEL=anduinos rd.live.dir=LiveOS "
            "rd.live.squashimg=rootfs.squashfs rd.overlay rd.anduinos.live=1 "
            f"rd.anduinos.media-check={mode} locale={locale}.UTF-8 "
            "console=ttyS0 console=tty0 quiet splash plymouth.ignore-serial-consoles",
            "-drive", f"file={iso},media=cdrom,readonly=on,if=ide,throttling.bps-read={rate}",
        ]
    process = subprocess.Popen(machine, stdout=qemu_log, stderr=qemu_log,
                               start_new_session=True)
    pid_file.write_text(str(process.pid))
    print(f"QEMU pid={process.pid} output={output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "start", "shot", "key", "stop"))
    parser.add_argument("--iso", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--locale", default="en_US")
    parser.add_argument("--mode", default="auto")
    parser.add_argument("--rate", type=int, default=40 * 1024 * 1024)
    parser.add_argument("--key", default="s")
    parser.add_argument("--name", default="screen.png")
    parser.add_argument("--from-iso", action="store_true")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.iso.resolve(), args.output.resolve())
    elif args.action == "start":
        start(args.iso.resolve(), args.output.resolve(), args.locale, args.mode,
              args.rate, args.from_iso)
    elif args.action == "shot":
        command(args.output, "screendump", {"filename": str(args.output / args.name), "format": "png"})
    elif args.action == "key":
        command(args.output, "send-key", {"keys": [{"type": "qcode", "data": args.key}]})
    else:
        command(args.output, "quit")
