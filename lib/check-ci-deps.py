#!/usr/bin/env python3
"""
Cross-check GitLab CI 'needs' against aosproj Dependency/Recommend/Suggest.

Finds:
  - CI_MISSING:  aosproj declares a dependency, but CI has no 'needs' for it
  - CI_STALE:    CI has a 'needs' that aosproj does NOT declare

Only reports internal (AnduinOS) package dependencies.
"""

import re
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent

LEVEL_MAP = {"Suggest": 1, "Recommend": 2, "Dependency": 3}


def parse_aosproj(fp: Path):
    text = fp.read_text()
    name = re.search(r"<PackageName>(.*?)</PackageName>", text)
    if not name:
        return None, set()
    deps = set()
    for m in re.finditer(
        r'<(Dependency|Recommend|Suggest)\s+Include="([^"]+)"', text
    ):
        for part in m.group(2).split("|"):
            deps.add(part.strip())
    return name.group(1), deps


def parse_ci(ci_path: Path):
    """Return {job_name: set of needed job names}."""
    needs = defaultdict(set)
    current = None
    in_needs = False
    for line in ci_path.read_text().splitlines():
        # Detect job header: "package-name:"
        m_job = re.match(r"^(\S[^:]*):$", line)
        if m_job and not line.startswith(".") and not line.startswith("#"):
            current = m_job.group(1)
            needs[current] = set()
            in_needs = False
            continue
        if current and re.match(r"\s+needs\s*:", line):
            in_needs = True
            # single-line needs list?
            m = re.search(r"needs\s*:\s*\[(.*)\]", line)
            if m:
                for item in re.findall(r"(\S+)", m.group(1)):
                    if item != "-":
                        needs[current].add(item)
                in_needs = False
            continue
        if in_needs and re.match(r"\s+- (\S+)", line):
            needs[current].add(re.match(r"\s+- (\S+)", line).group(1))
            continue
        if in_needs and not re.match(r"\s+-", line) and not re.match(r"^\s*$", line):
            in_needs = False
    return needs


def main():
    # Parse all packages
    all_pkgs = set()
    aosproj_deps = {}
    for f in sorted(ROOT.glob("*/*.aosproj")):
        name, deps = parse_aosproj(f)
        if name:
            all_pkgs.add(name)
            aosproj_deps[name] = deps

    # Parse CI
    ci = parse_ci(ROOT / ".gitlab-ci.yml")

    missing = []  # in aosproj but not in CI
    stale = []    # in CI but not in aosproj

    for pkg in sorted(aosproj_deps):
        internal_deps = aosproj_deps[pkg] & all_pkgs
        ci_needs = ci.get(pkg, set()) & all_pkgs

        for d in sorted(internal_deps - ci_needs):
            missing.append((pkg, d))
        for d in sorted(ci_needs - internal_deps):
            stale.append((pkg, d))

    if not missing and not stale:
        print("✅ CI ↔ aosproj perfectly consistent!")
        return 0

    if stale:
        print(f"🔴 STALE ({len(stale)}): CI needs this but aosproj does NOT depend on it")
        for pkg, dep in stale:
            print(f"    {pkg}  →  {dep}")

    if missing:
        print(f"\n🟡 MISSING ({len(missing)}): aosproj depends but CI does NOT need it")
        for pkg, dep in missing:
            print(f"    {pkg}  →  {dep}")

    return 1


if __name__ == "__main__":
    sys.exit(min(main(), 1))
