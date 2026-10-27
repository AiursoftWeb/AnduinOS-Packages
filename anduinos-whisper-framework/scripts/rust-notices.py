#!/usr/bin/python3
"""Collect locked Cargo dependency attribution for a target package build.

Includes build-time dependencies as well as linked crates. Read the actual
downloaded crate notices, never manufacture a license from its SPDX identifier.
Missing upstream license text fails the build and requires a maintainer review.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target")
    parser.add_argument("destination")
    parser.add_argument("--package", help="Collect one named dependency and the standard-library notices")
    args = parser.parse_args()
    target, destination = args.target, args.destination
    metadata = json.loads(subprocess.check_output([
        "cargo", "metadata", "--locked", "--offline", "--format-version=1",
        "--filter-platform", target,
    ], text=True))
    resolved = {node["id"] for node in metadata["resolve"]["nodes"]}
    sections = ["Rust dependency notices\n"
                "Generated from Cargo.lock and upstream crate source archives.\n"
                + (f"Selected dependency: {args.package}; standard-library notices also included.\n"
                   if args.package else
                   "Includes resolved build dependencies; not all listed code is linked.\n")]
    # Cargo metadata does not list the statically linked Rust standard library.
    version = subprocess.check_output(["rustc", "--version"], text=True).split()[1]
    sysroot = Path(subprocess.check_output(["rustc", "--print", "sysroot"], text=True).strip())
    standard_notices = ([Path(os.environ["RUST_STDLIB_NOTICES"])]
                        if os.environ.get("RUST_STDLIB_NOTICES") else [
                            # rustup ships HTML notices, including a smaller
                            # standard-library-only document on newer releases.
                            sysroot / "share/doc/rust/COPYRIGHT-library.html",
                            sysroot / "share/doc/rust/COPYRIGHT",
                            sysroot / "share/doc/rust/COPYRIGHT.html",
                            Path("/usr/share/doc") / f"libstd-rust-{'.'.join(version.split('.')[:2])}" / "copyright",
                        ])
    standard_notice = next((p for p in standard_notices if p.is_file()), None)
    if standard_notice is None:
        raise SystemExit("Missing Rust standard-library notices; set RUST_STDLIB_NOTICES for this toolchain")
    sections.append(f"\nRust standard library {version}\n{standard_notice.read_text()}\n")
    packages = sorted(metadata["packages"], key=lambda p: (p["name"], p["version"]))
    if args.package and not any(p["name"] == args.package and p["id"] in resolved for p in packages):
        raise SystemExit(f"Dependency not resolved: {args.package}")
    for package in packages:
        if args.package and package["name"] != args.package:
            continue
        if package["id"] not in resolved or not package["source"]:
            continue
        directory = Path(package["manifest_path"]).parent
        files = {p for p in directory.iterdir() if p.is_file() and
                 p.name.lower().startswith(("license", "copying", "copyright", "notice"))}
        if package.get("license_file"):
            files.add(directory / package["license_file"])
        if not files or not any(p.name.lower().startswith(("license", "copying"))
                                or p == directory / (package.get("license_file") or "")
                                for p in files):
            raise SystemExit(f"Missing license text: {package['name']} {package['version']}")
        sections.append(f"\n{'=' * 72}\n{package['name']} {package['version']}\n"
                        f"License: {package.get('license') or 'see supplied license'}\n"
                        f"Source: {package.get('repository') or package['source']}\n")
        for file in sorted(files):
            sections.append(f"\n--- {file.name} ---\n{file.read_text()}\n")
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(sections))


if __name__ == "__main__":
    main()
