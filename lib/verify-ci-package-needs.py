#!/usr/bin/env python3
"""Check CI package coverage and needs against internal package dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".gitlab-ci.yml"
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
REQUIRED_GATES = {"lint-all", "verify-ci-package-needs", "test-all"}

@dataclass
class Job:
    name: str
    package_dir: str | None = None
    needs: tuple[str, ...] = ()
    stage: str | None = None
    extends: str | None = None


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
        match = re.fullmatch(r"([.A-Za-z0-9][A-Za-z0-9_.-]*):\s*", line)
        if match:
            starts.append((index, match.group(1)))
    starts.append((len(lines), ""))
    for position in range(len(starts) - 1):
        start, name = starts[position]
        end = starts[position + 1][0]
        block = lines[start + 1 : end]
        package_dir = None
        stage = None
        extends = None
        needs: list[str] = []
        in_needs = False
        for line in block:
            stage_match = re.fullmatch(r"  stage:\s*([A-Za-z0-9_.-]+)\s*", line)
            if stage_match:
                stage = stage_match.group(1)
            extends_match = re.fullmatch(r"  extends:\s*([A-Za-z0-9_.-]+)\s*", line)
            if extends_match:
                extends = extends_match.group(1)
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
        if name in parsed or len(needs) != len(set(needs)):
            raise RuntimeError(f"Duplicate CI job or needs entry: {name}")
        parsed[name] = Job(name, package_dir, tuple(needs), stage, extends)
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
        result[package] = dependencies
    return result


def verify() -> tuple[int, int]:
    project_map = projects()
    job_map = jobs()
    for gate in REQUIRED_GATES:
        expected_stage = "test" if gate == "test-all" else "lint"
        if gate not in job_map or job_map[gate].stage != expected_stage:
            raise RuntimeError(f"{gate} must exist in stage {expected_stage}")
    if set(job_map["test-all"].needs) != REQUIRED_GATES - {"test-all"}:
        raise RuntimeError("test-all must wait for both lint gates")
    if ".publish" not in job_map or job_map[".publish"].stage != "packages":
        raise RuntimeError("The publish template must use the packages stage")
    job_by_directory: dict[str, str] = {}
    for job in job_map.values():
        if job.package_dir is None:
            continue
        if job.extends != ".publish" or job.stage not in (None, "packages"):
            raise RuntimeError(f"{job.name} must use the gated .publish template")
        if job.package_dir in job_by_directory:
            raise RuntimeError(
                f"Multiple jobs publish {job.package_dir}: "
                f"{job_by_directory[job.package_dir]}, {job.name}"
            )
        job_by_directory[job.package_dir] = job.name

    unknown_directories = set(job_by_directory) - {directory for directory, _ in project_map.values()}
    if unknown_directories:
        raise RuntimeError("CI jobs reference unknown package directories: " + ", ".join(sorted(unknown_directories)))
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
    errors: list[str] = []
    for package, expected_packages in sorted(relationships.items()):
        job_name = package_to_job[package]
        expected_jobs = {package_to_job[item] for item in expected_packages} | REQUIRED_GATES
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
            "Depends/Recommends/Suggests and mandatory lint/test gates:\n"
            + "\n".join(errors)
        )
    visited, active = set(), set()
    def visit(package):
        if package in active:
            raise RuntimeError(f"CI dependency cycle involving {package}")
        if package in visited:
            return
        active.add(package)
        for dependency in relationships[package]:
            visit(dependency)
        active.remove(package)
        visited.add(package)
    for package in relationships:
        visit(package)
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
