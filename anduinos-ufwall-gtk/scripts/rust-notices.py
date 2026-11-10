#!/usr/bin/python3
"""Collect locked Cargo dependency attribution for a target package build.

Includes build-time dependencies as well as linked crates. Read the actual
downloaded crate notices, never manufacture a license from its SPDX identifier.
Missing upstream license text fails the build and requires a maintainer review.
For crates omitting workspace licences, use their recorded GitHub source commit
and cache the source archive under obj; never substitute a moving branch.
"""
import argparse
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request


def upstream_licenses(package, destination):
    """Read omitted workspace licences at the revision recorded in the crate."""
    source = Path(package["manifest_path"]).parent / ".cargo_vcs_info.json"
    repository = urllib.parse.urlsplit(package.get("repository") or "")
    if not source.is_file() or repository.scheme != "https" or repository.netloc != "github.com":
        return []
    path = repository.path.removesuffix(".git").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
        return []
    vcs = json.loads(source.read_text())
    revision = vcs.get("git", {}).get("sha1", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return []
    workspace = PurePosixPath(vcs.get("path_in_vcs") or ".")
    if workspace.is_absolute() or ".." in workspace.parts:
        return []
    parents = {workspace, *workspace.parents}
    archive = Path(destination).parent / "upstream-license-sources" / f"{revision}.tar.gz"
    if not archive.is_file():
        url = f"https://codeload.github.com/{path}/tar.gz/{revision}"
        print(f"Reading omitted workspace licences: {package['name']} from {url}", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            raise SystemExit("Upstream licence archive exceeds 64 MiB")
        archive.parent.mkdir(parents=True, exist_ok=True)
        temporary = archive.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(archive)
    texts = []
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            relative = PurePosixPath(*PurePosixPath(member.name).parts[1:])
            if (not member.isfile() or relative.parent not in parents
                    or not relative.name.lower().startswith(("license", "licence", "copying", "copyright", "notice"))):
                continue
            texts.append((str(relative), tar.extractfile(member).read().decode("utf-8")))
    if not any(PurePosixPath(name).name.lower().startswith(("license", "licence", "copying"))
               and text.strip() for name, text in texts):
        return []
    return [(f"upstream {name} at {revision}", text) for name, text in texts]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target")
    parser.add_argument("destination")
    args = parser.parse_args(sys.argv[1:])
    target, destination = args.target, args.destination
    metadata = json.loads(subprocess.check_output([
        "cargo", "metadata", "--locked", "--offline", "--format-version=1",
        "--filter-platform", target,
    ], text=True))
    resolved = {node["id"] for node in metadata["resolve"]["nodes"]}
    sections = ["Rust dependency notices\n"
                "Generated from Cargo.lock and upstream crate source archives.\n"
                "Includes resolved build dependencies; not all listed code is linked.\n"]
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
    for package in packages:
        if package["id"] not in resolved or not package["source"]:
            continue
        directory = Path(package["manifest_path"]).parent
        files = {p for p in directory.iterdir() if p.is_file() and
                 p.name.lower().startswith(("license", "licence", "copying", "copyright", "notice"))}
        if package.get("license_file"):
            files.add(directory / package["license_file"])
        texts = [(file.name, file.read_text()) for file in sorted(files)]
        if not files or not any(p.name.lower().startswith(("license", "licence", "copying"))
                                or p == directory / (package.get("license_file") or "")
                                for p in files):
            recovered = upstream_licenses(package, destination)
            if not recovered:
                raise SystemExit(f"Missing license text: {package['name']} {package['version']}")
            texts.extend(recovered)
        sections.append(f"\n{'=' * 72}\n{package['name']} {package['version']}\n"
                        f"License: {package.get('license') or 'see supplied license'}\n"
                        f"Source: {package.get('repository') or package['source']}\n")
        for name, text in texts:
            sections.append(f"\n--- {name} ---\n{text}\n")
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress("".join(sections).encode("utf-8"), compresslevel=9, mtime=0))


if __name__ == "__main__":
    main()
