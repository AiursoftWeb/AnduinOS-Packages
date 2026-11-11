"""ATGZ v1's stable envelope; installation capabilities are checked separately."""
import json
import re
from datetime import datetime

from .native import BundleError

MAX_PAYLOAD = 64 * 1024 ** 3
METADATA = {"repository/Packages", "repository/Release", "repository/InRelease", "repository/Release.gpg"}
FEATURES = {"apt-repository-v1", "sequential-apt-plans-v1", "updater-bootstrap-v1"}


def require(condition, message):
    if not condition:
        raise BundleError(message)


def text(value, pattern, name):
    require(isinstance(value, str) and len(value) <= 512 and re.fullmatch(pattern, value), f"Invalid {name}")
    return value


def package_name(value):
    return text(value, r"[a-z0-9][a-z0-9+.-]*", "package name")


def version(value):
    return text(value, r"[0-9][A-Za-z0-9.+:~\-]*", "Debian version")


def path(value):
    require(isinstance(value, str) and 0 < len(value) <= 512 and
            not any(c in value for c in "\\:") and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value) and
            not any(p in ("", ".", "..") for p in value.split("/")), "Unsafe archive path")
    return value


def identity(item):
    require(isinstance(item, dict), "Invalid package identity")
    return package_name(item.get("name")), version(item.get("version")), text(item.get("architecture"), r"amd64|arm64|all", "architecture")


def digest(item):
    require(isinstance(item, dict) and type(item.get("size")) is int and 0 <= item["size"] <= MAX_PAYLOAD, "Invalid file size")
    text(item.get("sha256"), r"[0-9a-f]{64}", "SHA-256")


def read_json(data):
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, "Duplicate JSON property")
            result[k] = v
        return result

    def check(value, depth=0):
        require(depth <= 32 and value is not None, "JSON depth or null value is unsupported")
        if isinstance(value, dict):
            for child in value.values():
                check(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check(child, depth + 1)
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(BundleError("Non-finite JSON number")))
        check(result)
        return result
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise BundleError("Invalid manifest JSON") from error


def validate(m):
    require(isinstance(m, dict), "Expected manifest object")
    require(type(m.get("format_version")) is int and m["format_version"] == 1 and m.get("product") == "AnduinOS", "Unsupported bundle format/product")
    text(m.get("architecture"), r"amd64|arm64", "architecture")
    line = text(m.get("release_line"), r"[0-9]+\.[0-9]+", "release line")
    text(m.get("suite"), r"[A-Za-z0-9][A-Za-z0-9-]*", "suite")
    target = text(m.get("target_version"), r"[0-9]+(?:\.[0-9]+)+", "target OS version")
    require(target.startswith(line + "."), "Target is outside the release line")
    text(m.get("batch_id"), r"[0-9a-f]{64}", "batch ID")
    package_name(m.get("version_package")); path(m.get("version_file"))
    created = text(m.get("created_utc"), r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+(?:Z|[+-][0-9]{2}:[0-9]{2})", "creation time")
    try:
        datetime.fromisoformat(created)
    except ValueError as error:
        raise BundleError("Invalid bundle creation time") from error
    features = m.get("required_features")
    require(isinstance(features, list) and all(isinstance(f, str) for f in features), "Invalid required features")
    require(set(features) <= FEATURES and {"apt-repository-v1", "sequential-apt-plans-v1"} <= set(features), "Unsupported envelope capability")
    extra = m.get("required_installer_features", [])
    require(isinstance(extra, list) and len(extra) <= 32, "Invalid installer capabilities")
    for feature in extra:
        text(feature, r"[A-Za-z0-9-]{1,128}", "installer capability")
    require(len(set(extra)) == len(extra), "Duplicate installer capability")
    bootstrap = m.get("bootstrap")
    require(("updater-bootstrap-v1" in features) == (bootstrap is not None), "Bootstrap capability and declaration disagree")
    require(not extra or bootstrap is not None, "Future installer capabilities require a bootstrap declaration")
    if bootstrap is not None:
        require(isinstance(bootstrap, dict) and type(bootstrap.get("schema_version")) is int and bootstrap["schema_version"] == 1 and
                bootstrap.get("package") == "anduinos-offline-updater", "Unsupported updater bootstrap identity/schema")
        version(bootstrap.get("minimum_version"))
    stages, removals = m.get("stages"), m.get("allowed_removals")
    require(isinstance(stages, list) and 0 < len(stages) <= 16 and isinstance(removals, list), "Invalid bounded update plan")
    for name in removals:
        package_name(name)
    for stage in stages:
        require(isinstance(stage, dict) and isinstance(stage.get("operation"), str) and isinstance(stage.get("packages"), list), "Invalid stage")
        for name in stage["packages"]:
            package_name(name)
        if not extra:
            require(stage["operation"] in ("install", "dist-upgrade") and
                    bool(stage["packages"]) == (stage["operation"] == "install"), "Unsupported installer stage")
    if not extra:
        require(stages[-1]["operation"] == "dist-upgrade", "Plan must end with dist-upgrade")
    files, packages, targets = m.get("files"), m.get("packages"), m.get("required_installed_targets")
    require(isinstance(files, dict) and len(files) <= 100000 and isinstance(packages, list) and packages and
            isinstance(targets, list) and isinstance(m.get("baselines"), list) and m["baselines"], "Incomplete inventory")
    total = 0
    for name, value in files.items():
        path(name); digest(value)
        require(name.startswith("repository/"), "Payload outside repository")
        total += value["size"]
    require(total <= MAX_PAYLOAD, "Unpacked bundle exceeds limit")
    keys, identities, package_paths = set(), set(), set()
    for pkg in packages:
        require(isinstance(pkg, dict), "Invalid package inventory")
        name, ver, arch = identity(pkg.get("identity"))
        require(arch in (m["architecture"], "all") and (name, arch) not in keys, "Conflicting package inventory")
        keys.add((name, arch)); identities.add((name, ver, arch))
        entry = path(pkg.get("path")); digest(pkg.get("content"))
        require(entry.startswith("repository/pool/") and entry not in package_paths and files.get(entry) == pkg["content"], "Package digest disagrees with inventory")
        package_paths.add(entry)
    require(set(files) == package_paths | METADATA, "Unexpected or missing repository files")
    target_ids = [identity(t) for t in targets]
    require(len(target_ids) == len(set(target_ids)) and set(target_ids) == identities, "Required targets must equal package inventory")
    require(any(i[0] == m["version_package"] for i in identities), "Missing OS version package")
    if bootstrap:
        require(sum(i[0] == bootstrap["package"] for i in identities) == 1, "Missing or ambiguous bootstrap updater")
    for name in METADATA:
        require(files[name]["size"] <= (32 * 1024 ** 2 if name.endswith("/Packages") else 1024 ** 2), "Repository metadata exceeds limit")
    return m
