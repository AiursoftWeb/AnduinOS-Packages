#!/usr/bin/env python3
"""
POC: given an extension UUID and a target GNOME Shell version, find the "closest"
extension version — the one whose supported GNOME range is nearest to the target,
breaking ties by picking the newest (highest) extension version number.

Algorithm (example):
  Target GNOME 47:
    v1.1 supports [46, 47]  → distance=0 (47 is in range)
    v1.2 supports [47, 48]  → distance=0 (47 is in range)
    v1.3 supports [48, 49]  → distance=1 (closest is 48)
    → v1.2 wins: distance=0 AND it's the newest among 0-distance versions.

  Target GNOME 50 (none support it directly):
    v1.1 supports [46, 47]  → closest=47, distance=3
    v1.2 supports [47, 48]  → closest=48, distance=2
    v1.3 supports [48, 49]  → closest=49, distance=1
    → v1.3 wins: distance=1 AND it's the newest.

After download, we STILL force-patch metadata.json to add the target GNOME
version — because extensions that claim "48,49" often work fine on 50.

Usage:
    python3 gnome-version-resolver.py <uuid> [--target 50] [--download] [--patch-target 50]
"""

import argparse
import json
import os
import sys
import tempfile
import urllib.request
import urllib.error
import zipfile
from typing import Optional

BASE_URL = "https://extensions.gnome.org"


# ---------------------------------------------------------------------------
# GNOME Extensions API
# ---------------------------------------------------------------------------

def fetch_extension_info(uuid: str, shell_version: Optional[str] = None) -> dict:
    """Call /extension-info/ and return the parsed JSON."""
    params = {"uuid": uuid}
    if shell_version is not None:
        params["shell_version"] = shell_version
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE_URL}/extension-info/?{qs}"

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "AnduinOS-GnomeResolver/1.0")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.exit(f"Extension '{uuid}' not found on extensions.gnome.org")
        raise


# ---------------------------------------------------------------------------
# Version math
# ---------------------------------------------------------------------------

def parse_shell_version(v: str) -> int:
    """'3.36' → 3, '40' → 40, '45.0' → 45, '3.21.91' → 3"""
    return int(v.split(".")[0])


def gnome_distance(supported: str, target: int) -> int:
    """Absolute major-version distance."""
    return abs(parse_shell_version(supported) - target)


# ---------------------------------------------------------------------------
# Core: find the best extension version
# ---------------------------------------------------------------------------

def find_best_version(info: dict, target_gnome: int) -> dict:
    """
    Parse shell_version_map, group by extension version, score each group
    by the minimum GNOME-version distance to the target.

    Returns a dict with keys:
      extension_version, pk, download_shell_version, distance,
      supported_shells, all_versions, needs_patch
    """
    svmap: dict = info.get("shell_version_map", {})
    if not svmap:
        sys.exit("No shell_version_map in API response — cannot resolve.")

    # Group: extension_version → list of GNOME shell version strings
    by_ext_version: dict[int, list[str]] = {}
    pk_of: dict[int, int] = {}
    for sv, entry in svmap.items():
        ev = entry["version"]
        pk = entry["pk"]
        by_ext_version.setdefault(ev, []).append(sv)
        pk_of[ev] = max(pk_of.get(ev, 0), pk)

    if not by_ext_version:
        sys.exit("shell_version_map is empty.")

    # Score each extension version
    best_ev = None
    best_distance = float("inf")
    best_shells: list[str] = []

    for ev, shells in by_ext_version.items():
        min_dist = min(gnome_distance(s, target_gnome) for s in shells)
        # (distance ASC, extension_version DESC)
        if min_dist < best_distance or (min_dist == best_distance and (best_ev is None or ev > best_ev)):
            best_distance = min_dist
            best_ev = ev
            best_shells = sorted(shells, key=parse_shell_version)

    assert best_ev is not None

    # Download key: the shell_version closest to target WITHIN the chosen version's range
    best_shell = min(best_shells, key=lambda s: gnome_distance(s, target_gnome))

    # Does the chosen version directly claim support for the target?
    needs_patch = str(target_gnome) not in best_shells

    return {
        "extension_version": best_ev,
        "pk": pk_of[best_ev],
        "download_shell_version": best_shell,
        "distance": best_distance,
        "supported_shells": best_shells,
        "needs_patch": needs_patch,
        "all_versions": {
            ev: sorted(shells, key=parse_shell_version)
            for ev, shells in by_ext_version.items()
        },
    }


# ---------------------------------------------------------------------------
# Download & patch
# ---------------------------------------------------------------------------

def download_extension(uuid: str, shell_version: str, out_dir: str) -> str:
    """Download extension zip and extract to out_dir. Returns the output path."""
    url = f"{BASE_URL}/download-extension/{uuid}.shell-extension.zip?shell_version={shell_version}"

    print(f"  GET {url}")
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "AnduinOS-GnomeResolver/1.0")
    with urllib.request.urlopen(req, timeout=30) as resp:
        with tempfile.NamedTemporaryFile(prefix="gnome-ext-", suffix=".zip") as archive:
            archive.write(resp.read())
            archive.flush()
            os.makedirs(out_dir, exist_ok=True)
            with zipfile.ZipFile(archive.name, "r") as zf:
                zf.extractall(out_dir)
    return out_dir


def patch_metadata(out_dir: str, target_gnome: int):
    """Force-add target GNOME version to metadata.json shell-version array."""
    meta_path = os.path.join(out_dir, "metadata.json")
    if not os.path.exists(meta_path):
        print(f"  WARNING: no metadata.json found in {out_dir}")
        return

    with open(meta_path) as f:
        meta = json.load(f)

    sv = meta.get("shell-version", [])
    target_str = str(target_gnome)
    if target_str not in sv:
        sv.append(target_str)
        meta["shell-version"] = sorted(sv, key=parse_shell_version)
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        print(f"  Patched metadata.json: added GNOME {target_str} → shell-version = {meta['shell-version']}")
    else:
        print(f"  metadata.json already includes GNOME {target_str}, no patch needed.")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_summary(info: dict, result: dict, target: int):
    """Pretty-print the resolution result."""
    print(f"Extension : {info.get('name', '?')}")
    print(f"UUID      : {info.get('uuid', '?')}")
    print(f"Target GNOME Shell : {target}")
    print(f"Store URL : {info.get('link', '?')}")
    print()
    print("All available extension versions (vNN → GNOME [...supported...]):")
    for ev, shells in result["all_versions"].items():
        marker = "  ← BEST MATCH" if ev == result["extension_version"] else ""
        print(f"  v{ev:>4} → {shells}{marker}")
    print()
    print("Best match details:")
    print(f"  Extension version    : {result['extension_version']}")
    print(f"  PK (download ID)     : {result['pk']}")
    print(f"  Download with        : shell_version={result['download_shell_version']}")
    print(f"  Min distance to {target} : {result['distance']} major version(s)")
    print(f"  This version supports: GNOME {result['supported_shells']}")
    if result["needs_patch"]:
        print(f"  ⚠  Does NOT claim {target} support → metadata will be patched after download")
    else:
        print(f"  ✓  Already claims {target} support")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Resolve best GNOME extension version for a target Shell version"
    )
    parser.add_argument("uuid", help="Extension UUID, e.g. blur-my-shell@aunetx")
    parser.add_argument("--target", type=int, default=50,
                        help="Target GNOME Shell major version (default: 50)")
    parser.add_argument("--download", action="store_true",
                        help="Download and extract the resolved version")
    parser.add_argument("--patch-target", type=int, default=None,
                        help="Force-add this GNOME version to metadata after download "
                             "(default: same as --target)")
    parser.add_argument("--out", default=None,
                        help="Output directory for download (default: ./deploy/<uuid>)")
    args = parser.parse_args()

    patch_ver = args.patch_target if args.patch_target is not None else args.target

    # 1. Query the API (no shell_version filter → get the full shell_version_map)
    print(f"Fetching extension info for '{args.uuid}'...")
    info = fetch_extension_info(args.uuid)

    # 2. Find best version
    result = find_best_version(info, args.target)

    # 3. Re-query with the best shell_version to get the real download_url
    info_best = fetch_extension_info(args.uuid, shell_version=result["download_shell_version"])
    result["download_url"] = info_best.get("download_url")

    print_summary(info, result, args.target)

    # 4. Download + patch
    if args.download:
        out_dir = args.out or f"deploy/{args.uuid}"
        print(f"\nDownloading & extracting to {out_dir}/ ...")
        download_extension(args.uuid, result["download_shell_version"], out_dir)
        print(f"Done — {out_dir}/")

        if result["needs_patch"] or patch_ver != args.target:
            patch_metadata(out_dir, patch_ver)
        else:
            meta_path = os.path.join(out_dir, "metadata.json")
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
                print(f"  metadata.json shell-version: {meta.get('shell-version', 'N/A')}")


if __name__ == "__main__":
    main()
