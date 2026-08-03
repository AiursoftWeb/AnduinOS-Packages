"""Safely migrate the active Live-session Wi-Fi connection to the target."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .command import CommandRunner
from .steps import FailurePolicy, InstallContext, StepWarning
from .wifi import split_nmcli_terse


ACTIVE_WIFI_COMMAND = (
    "nmcli",
    "--terse",
    "--escape",
    "yes",
    "--fields",
    "UUID,TYPE",
    "connection",
    "show",
    "--active",
)
NETWORK_MANAGER_DIRECTORY = Path("etc/NetworkManager/system-connections")
NETWORK_MANAGER_TYPES = frozenset({"802-11-wireless", "wifi"})
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
MAX_PROFILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class WifiProfileSnapshot:
    """Identity-only snapshot; secret profile bytes are not retained in RAM."""

    uuid: str
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass
class MigrateWifiConnectionStep:
    runner: CommandRunner
    source_directory: Path = Path(
        "/etc/NetworkManager/system-connections"
    )
    source_uid: int = 0
    target_uid: int = 0
    target_gid: int = 0
    id: str = "migrate-wifi-connection"
    title: str = "Migrate active Wi-Fi connection"
    failure_policy: FailurePolicy = FailurePolicy.WARNING
    progress_weight: int = 1
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        """Freeze safe source identities before any destructive work starts."""

        context.values["wifi_profile_snapshots"] = ()
        context.values["wifi_migration_preflight_warning"] = ""
        try:
            result = self.runner.run(
                ACTIVE_WIFI_COMMAND,
                check=False,
                timeout=10,
                log_output=False,
            )
        except (OSError, RuntimeError) as error:
            self._skip_with_warning(
                context,
                "Could not inspect active NetworkManager connections: "
                f"{error}",
            )
            return
        if result.returncode != 0:
            self._skip_with_warning(
                context,
                "Could not inspect active NetworkManager connections; "
                "Wi-Fi migration will be skipped",
            )
            return

        active_uuids = _active_wifi_uuids(result.stdout)
        if not active_uuids:
            context.log("No active Wi-Fi connection requires migration")
            return

        try:
            directory_stat = self.source_directory.lstat()
        except OSError as error:
            self._skip_with_warning(
                context,
                "No safe persistent Live-session NetworkManager profile "
                f"directory was found: {error}",
            )
            return
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or self.source_directory.is_symlink()
        ):
            self._skip_with_warning(
                context,
                "Live-session NetworkManager profile directory is unsafe; "
                "Wi-Fi migration will be skipped",
            )
            return

        matches: dict[str, list[WifiProfileSnapshot]] = {
            uuid: [] for uuid in active_uuids
        }
        try:
            entries = tuple(os.scandir(self.source_directory))
        except OSError as error:
            self._skip_with_warning(
                context,
                f"Could not inspect Live-session Wi-Fi profiles: {error}",
            )
            return
        for entry in entries:
            snapshot = _safe_profile_snapshot(
                Path(entry.path), self.source_uid
            )
            if snapshot is not None and snapshot.uuid in matches:
                matches[snapshot.uuid].append(snapshot)

        snapshots: list[WifiProfileSnapshot] = []
        for uuid in active_uuids:
            candidates = matches[uuid]
            if len(candidates) == 1:
                snapshots.append(candidates[0])
            elif len(candidates) > 1:
                context.log(
                    f"Multiple NetworkManager profiles claim active UUID {uuid}; "
                    "that connection will not be migrated"
                )
            else:
                context.log(
                    f"Active Wi-Fi UUID {uuid} has no safe persistent profile"
                )
        context.values["wifi_profile_snapshots"] = tuple(snapshots)
        if not snapshots:
            self._skip_with_warning(
                context,
                "No safe persistent profile matched the active Wi-Fi "
                "connection",
            )

    @staticmethod
    def _skip_with_warning(
        context: InstallContext, message: str
    ) -> None:
        context.log(message)
        context.values["wifi_migration_preflight_warning"] = message

    def execute(self, context: InstallContext) -> None:
        snapshots = context.values.get("wifi_profile_snapshots", ())
        if not isinstance(snapshots, tuple) or not all(
            isinstance(item, WifiProfileSnapshot) for item in snapshots
        ):
            raise RuntimeError("Wi-Fi profile preflight state is invalid")
        if not snapshots:
            context.values["migrated_wifi_profiles"] = ()
            warning = context.values.get("wifi_migration_preflight_warning")
            if warning:
                raise StepWarning(str(warning))
            return

        target = _target(context)
        target_directory = target / NETWORK_MANAGER_DIRECTORY
        _prepare_target_directory(
            target, target_directory, self.target_uid, self.target_gid
        )
        existing_uuids = _existing_profile_uuids(target_directory)
        created: list[tuple[Path, str]] = []
        context.values["migrated_wifi_profiles"] = created

        for snapshot in snapshots:
            if snapshot.uuid in existing_uuids:
                context.log(
                    f"Target already contains Wi-Fi UUID {snapshot.uuid}; "
                    "the existing profile was preserved"
                )
                continue
            destination = target_directory / snapshot.path.name
            if destination.exists() or destination.is_symlink():
                context.log(
                    f"Target NetworkManager profile {destination.name!r} "
                    "already exists; it was preserved"
                )
                continue
            payload = _read_frozen_profile(snapshot, self.source_uid)
            _atomic_create_profile(
                destination,
                payload,
                snapshot.uuid,
                self.target_uid,
                self.target_gid,
            )
            created.append((destination, snapshot.uuid))
            existing_uuids.add(snapshot.uuid)
            context.log(
                f"Migrated active Wi-Fi profile {destination.name!r}"
            )

        context.values["migrated_wifi_profiles"] = tuple(created)

    def verify(self, context: InstallContext) -> None:
        for path, expected_uuid in context.values.get(
            "migrated_wifi_profiles", ()
        ):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                raise RuntimeError(
                    f"Migrated Wi-Fi profile is not a regular file: {path}"
                )
            if info.st_uid != self.target_uid or info.st_gid != self.target_gid:
                raise RuntimeError(
                    f"Migrated Wi-Fi profile has an unsafe owner: {path}"
                )
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise RuntimeError(
                    f"Migrated Wi-Fi profile has unsafe permissions: {path}"
                )
            if _profile_uuid(path.read_bytes()) != expected_uuid:
                raise RuntimeError(
                    f"Migrated Wi-Fi profile UUID changed: {path}"
                )

    def cleanup(self, context: InstallContext) -> None:
        for path, expected_uuid in context.values.get(
            "migrated_wifi_profiles", ()
        ):
            try:
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and _profile_uuid(path.read_bytes()) == expected_uuid
                ):
                    path.unlink()
            except OSError as error:
                context.log(
                    f"Could not remove migrated Wi-Fi profile {path}: {error}"
                )
        context.values["migrated_wifi_profiles"] = ()


def _active_wifi_uuids(output: str) -> tuple[str, ...]:
    uuids: list[str] = []
    for line in output.splitlines():
        fields = split_nmcli_terse(line)
        if len(fields) != 2:
            continue
        uuid, connection_type = (field.strip() for field in fields)
        if (
            UUID_RE.fullmatch(uuid)
            and connection_type in NETWORK_MANAGER_TYPES
            and uuid.lower() not in uuids
        ):
            uuids.append(uuid.lower())
    return tuple(uuids)


def _safe_profile_snapshot(
    path: Path, required_uid: int
) -> WifiProfileSnapshot | None:
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or path.is_symlink()
            or info.st_uid != required_uid
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > MAX_PROFILE_BYTES
        ):
            return None
        payload, opened = _read_no_follow(path)
        if (
            opened.st_dev != info.st_dev
            or opened.st_ino != info.st_ino
            or opened.st_size != info.st_size
            or opened.st_mtime_ns != info.st_mtime_ns
        ):
            return None
        identity = _profile_identity(payload)
        if identity is None or identity[1] not in NETWORK_MANAGER_TYPES:
            return None
        uuid = identity[0]
        return WifiProfileSnapshot(
            uuid, path, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
        )
    except (OSError, RuntimeError, UnicodeError):
        return None


def _read_frozen_profile(
    snapshot: WifiProfileSnapshot, required_uid: int
) -> bytes:
    payload, info = _read_no_follow(snapshot.path)
    if (
        info.st_uid != required_uid
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_size > MAX_PROFILE_BYTES
        or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        != (
            snapshot.device,
            snapshot.inode,
            snapshot.size,
            snapshot.mtime_ns,
        )
        or _profile_uuid(payload) != snapshot.uuid
    ):
        raise RuntimeError(
            f"Live Wi-Fi profile changed after preflight: {snapshot.path}"
        )
    return payload


def _read_no_follow(path: Path) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROFILE_BYTES:
            raise RuntimeError(f"Unsafe NetworkManager profile: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(MAX_PROFILE_BYTES + 1)
        if len(payload) > MAX_PROFILE_BYTES:
            raise RuntimeError(f"NetworkManager profile is too large: {path}")
        return payload, info
    finally:
        os.close(descriptor)


def _profile_identity(payload: bytes) -> tuple[str, str] | None:
    section = ""
    uuid = ""
    connection_type = ""
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section == "connection" and "=" in line:
            key, value = line.split("=", 1)
            key = key.strip().lower()
            if key == "uuid":
                uuid = value.strip().lower()
            elif key == "type":
                connection_type = value.strip()
    if UUID_RE.fullmatch(uuid) and connection_type:
        return uuid, connection_type
    return None


def _profile_uuid(payload: bytes) -> str | None:
    identity = _profile_identity(payload)
    return identity[0] if identity is not None else None


def _prepare_target_directory(
    target: Path,
    directory: Path,
    owner_uid: int,
    owner_gid: int,
) -> None:
    target_root = target.resolve(strict=True)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError(
            "Target NetworkManager profile directory is unsafe"
        )
    resolved = directory.resolve(strict=True)
    if target_root not in resolved.parents:
        raise RuntimeError(
            "Target NetworkManager profile directory escapes the target"
        )
    os.chown(directory, owner_uid, owner_gid)
    os.chmod(directory, 0o700)


def _existing_profile_uuids(directory: Path) -> set[str]:
    uuids: set[str] = set()
    for entry in os.scandir(directory):
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            payload, _info = _read_no_follow(Path(entry.path))
            uuid = _profile_uuid(payload)
            if uuid is not None:
                uuids.add(uuid)
        except (OSError, RuntimeError):
            continue
    return uuids


def _atomic_create_profile(
    destination: Path,
    payload: bytes,
    uuid: str,
    owner_uid: int,
    owner_gid: int,
) -> None:
    temporary = destination.parent / f".anduinos-installer-{uuid}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, owner_uid, owner_gid)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)

    try:
        # Hard-link publication is atomic and, unlike replace(), cannot
        # overwrite a target file created between the earlier check and now.
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    return target
