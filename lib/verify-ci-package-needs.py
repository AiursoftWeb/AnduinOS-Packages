#!/usr/bin/env python3
"""Check that GitLab package needs match internal package relationships.

Dependencies, recommendations, and suggestions on packages built in this
repository are all build-order relationships. This is independent of their
different runtime installation semantics in APT. A small, explicit set of
release-only relationships additionally protects atomic repository switches
without turning those relationships into runtime package dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".gitlab-ci.yml"
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")

# These versioned runtime dependencies deliberately point opposite the publish
# order. APT must see the new consumers as uninstallable until the guarded core
# becomes visible; CI must therefore publish the consumers first and the core
# last. They are removed from ordinary build-order edges below and represented
# by the reverse release-only edges.
PUBLICATION_GATE_DEPENDENCIES = {
    "anduinos-btrfs-snapshots-manager": {"anduinos-core-system"},
    "plymouth-anduinos": {"anduinos-core-system"},
}
RELEASE_ONLY_RELATIONSHIPS = {
    "anduinos-core-system": {
        "anduinos-btrfs-snapshots-manager",
        "plymouth-anduinos",
    },
}


@dataclass
class Job:
    name: str
    package_dir: str | None = None
    needs: tuple[str, ...] = ()


def projects() -> dict[str, tuple[str, ET.Element]]:
    result: dict[str, tuple[str, ET.Element]] = {}
    for project_path in sorted(ROOT.glob("*/*.aosproj")):
        package_dir = project_path.parent.name
        root = ET.parse(project_path).getroot()
        package_name = root.findtext(".//PackageName")
        if not package_name or not PACKAGE_RE.fullmatch(package_name):
            raise RuntimeError(f"Invalid PackageName in {project_path}")
        if package_name in result:
            raise RuntimeError(f"Duplicate PackageName: {package_name}")
        result[package_name] = (package_dir, root)
    return result


def jobs() -> dict[str, Job]:
    parsed: dict[str, Job] = {}
    lines = CI_PATH.read_text(encoding="utf-8").splitlines()
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*):\s*", line)
        if match:
            starts.append((index, match.group(1)))
    starts.append((len(lines), ""))
    for position in range(len(starts) - 1):
        start, name = starts[position]
        end = starts[position + 1][0]
        block = lines[start + 1 : end]
        package_dir = None
        needs: list[str] = []
        in_needs = False
        for line in block:
            directory = re.fullmatch(r"    PACKAGE_DIR:\s*([^\s#]+)\s*", line)
            if directory:
                package_dir = directory.group(1)
            if re.fullmatch(r"  needs:\s*", line):
                in_needs = True
                continue
            if in_needs:
                item = re.fullmatch(
                    r"    -\s+([A-Za-z0-9][A-Za-z0-9_.-]*)\s*", line
                )
                if item:
                    needs.append(item.group(1))
                    continue
                if line.strip() and not line.startswith("    "):
                    in_needs = False
        parsed[name] = Job(name, package_dir, tuple(needs))
    return parsed


def internal_relationships(
    project_map: dict[str, tuple[str, ET.Element]],
) -> dict[str, set[str]]:
    known = set(project_map)
    result: dict[str, set[str]] = {}
    for package, (_directory, root) in project_map.items():
        dependencies: set[str] = set()
        for tag in ("Dependency", "Recommend", "Suggest"):
            for item in root.findall(f".//{tag}"):
                value = item.get("Include", "")
                for alternative in value.split("|"):
                    candidate = re.split(
                        r"\s|\(", alternative.strip(), maxsplit=1
                    )[0]
                    if candidate in known and candidate != package:
                        dependencies.add(candidate)
        dependencies -= PUBLICATION_GATE_DEPENDENCIES.get(package, set())
        result[package] = dependencies
    return result


def verify() -> tuple[int, int]:
    project_map = projects()
    job_map = jobs()
    job_by_directory: dict[str, str] = {}
    for job in job_map.values():
        if job.package_dir is None:
            continue
        if job.package_dir in job_by_directory:
            raise RuntimeError(
                f"Multiple jobs publish {job.package_dir}: "
                f"{job_by_directory[job.package_dir]}, {job.name}"
            )
        job_by_directory[job.package_dir] = job.name

    missing_jobs = sorted(
        directory
        for directory, _root in project_map.values()
        if directory not in job_by_directory
    )
    if missing_jobs:
        raise RuntimeError(
            "Projects without GitLab publish jobs: " + ", ".join(missing_jobs)
        )

    package_to_job = {
        package: job_by_directory[directory]
        for package, (directory, _root) in project_map.items()
    }
    relationships = internal_relationships(project_map)
    for package, gated_dependencies in PUBLICATION_GATE_DEPENDENCIES.items():
        _directory, root = project_map[package]
        declared = {
            item.get("Include", "") for item in root.findall(".//Dependency")
        }
        for dependency in gated_dependencies:
            expected = f"{dependency} (>= 2.0.2-3)"
            if expected not in declared:
                raise RuntimeError(
                    f"{package} must retain publication gate: {expected}"
                )
    unknown_release_packages = sorted(
        {
            package
            for package, dependencies in (
                RELEASE_ONLY_RELATIONSHIPS | PUBLICATION_GATE_DEPENDENCIES
            ).items()
            for package in (package, *dependencies)
            if package not in project_map
        }
    )
    if unknown_release_packages:
        raise RuntimeError(
            "Unknown release-only package relationships: "
            + ", ".join(unknown_release_packages)
        )
    errors: list[str] = []
    for package, expected_packages in sorted(relationships.items()):
        job_name = package_to_job[package]
        ordered_packages = expected_packages | RELEASE_ONLY_RELATIONSHIPS.get(
            package, set()
        )
        expected_jobs = {package_to_job[item] for item in ordered_packages}
        actual_jobs = set(job_map[job_name].needs)
        missing = sorted(expected_jobs - actual_jobs)
        extra = sorted(actual_jobs - expected_jobs)
        if missing or extra:
            parts = [f"{job_name}:"]
            if missing:
                parts.append("missing=" + ",".join(missing))
            if extra:
                parts.append("extra=" + ",".join(extra))
            errors.append(" ".join(parts))
    if errors:
        raise RuntimeError(
            "GitLab needs differ from internal aosproj "
            "Depends/Recommends/Suggests:\n"
            + "\n".join(errors)
        )
    return len(project_map), sum(len(items) for items in relationships.values())


def main() -> int:
    try:
        package_count, relationship_count = verify()
    except Exception as error:
        print(f"CI package-needs policy failed: {error}", file=sys.stderr)
        return 1
    print(
        f"CI package-needs policy passed: {package_count} projects, "
        f"{relationship_count} internal relationships"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
