"""Safely migrate the active Live-session Wi-Fi Netplan to the target."""

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
NETPLAN_DIRECTORY = Path("etc/netplan")
NETWORK_MANAGER_TYPES = frozenset({"802-11-wireless", "wifi"})
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
MAX_NETPLAN_BYTES = 1024 * 1024


@dataclass(frozen=True)
class WifiProfileSnapshot:
    """Identity-only snapshot; secret Netplan bytes are not retained."""

    uuid: str
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass
class MigrateWifiConnectionStep:
    runner: CommandRunner
    source_directory: Path = Path("/etc/netplan")
    source_uid: int = 0
    target_uid: int = 0
    target_gid: int = 0
    id: str = "migrate-wifi-connection"
    title: str = "Migrate active Wi-Fi connection"
    failure_policy: FailurePolicy = FailurePolicy.WARNING
    progress_weight: int = 1
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        """Freeze safe Netplan source identities before destructive work."""

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
                "No safe persistent Live-session Netplan directory was "
                f"found: {error}",
            )
            return
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or self.source_directory.is_symlink()
            or directory_stat.st_uid != self.source_uid
            or stat.S_IMODE(directory_stat.st_mode) & 0o022
        ):
            self._skip_with_warning(
                context,
                "Live-session Netplan directory is unsafe; Wi-Fi migration "
                "will be skipped",
            )
            return

        snapshots: list[WifiProfileSnapshot] = []
        for uuid in active_uuids:
            path = self.source_directory / _netplan_filename(uuid)
            snapshot = _safe_netplan_snapshot(path, uuid, self.source_uid)
            if snapshot is None:
                context.log(
                    f"Active Wi-Fi UUID {uuid} has no safe persistent "
                    "Netplan profile"
                )
                continue
            snapshots.append(snapshot)

        context.values["wifi_profile_snapshots"] = tuple(snapshots)
        if not snapshots:
            self._skip_with_warning(
                context,
                "No safe persistent Netplan profile matched the active "
                "Wi-Fi connection",
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
            context.values["wifi_profiles_to_verify"] = ()
            warning = context.values.get("wifi_migration_preflight_warning")
            if warning:
                raise StepWarning(str(warning))
            return

        target = _target(context)
        target_directory = target / NETPLAN_DIRECTORY
        _prepare_target_directory(
            target, target_directory, self.target_uid, self.target_gid
        )
        created: list[tuple[Path, str]] = []
        to_verify: list[tuple[Path, str]] = []
        context.values["migrated_wifi_profiles"] = created
        context.values["wifi_profiles_to_verify"] = to_verify

        for snapshot in snapshots:
            destination = target_directory / _netplan_filename(snapshot.uuid)
            if destination.exists() or destination.is_symlink():
                context.log(
                    f"Target Netplan profile {destination.name!r} already "
                    "exists; it was preserved"
                )
                to_verify.append((destination, snapshot.uuid))
                continue
            payload = _read_frozen_netplan(snapshot, self.source_uid)
            _atomic_create_netplan(
                destination,
                payload,
                snapshot.uuid,
                self.target_uid,
                self.target_gid,
            )
            created.append((destination, snapshot.uuid))
            to_verify.append((destination, snapshot.uuid))
            context.log(
                f"Migrated active Wi-Fi Netplan {destination.name!r}"
            )

        context.values["migrated_wifi_profiles"] = tuple(created)
        context.values["wifi_profiles_to_verify"] = tuple(to_verify)

    def verify(self, context: InstallContext) -> None:
        target = _target(context)
        for path, expected_uuid in context.values.get(
            "wifi_profiles_to_verify", ()
        ):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                raise RuntimeError(
                    f"Migrated Wi-Fi Netplan is not a regular file: {path}"
                )
            if info.st_uid != self.target_uid or info.st_gid != self.target_gid:
                raise RuntimeError(
                    f"Migrated Wi-Fi Netplan has an unsafe owner: {path}"
                )
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise RuntimeError(
                    f"Migrated Wi-Fi Netplan has unsafe permissions: {path}"
                )
            if path.name != _netplan_filename(expected_uuid):
                raise RuntimeError(
                    f"Migrated Wi-Fi Netplan identity changed: {path}"
                )
            result = self.runner.run(
                _netplan_mapping_command(target, expected_uuid),
                check=False,
                timeout=30,
                log_output=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "Netplan could not validate migrated Wi-Fi UUID "
                    f"{expected_uuid}"
                )

    def cleanup(self, context: InstallContext) -> None:
        for path, expected_uuid in context.values.get(
            "migrated_wifi_profiles", ()
        ):
            try:
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.name == _netplan_filename(expected_uuid)
                ):
                    path.unlink()
            except OSError as error:
                context.log(
                    f"Could not remove migrated Wi-Fi Netplan {path}: {error}"
                )
        context.values["migrated_wifi_profiles"] = ()
        context.values["wifi_profiles_to_verify"] = ()


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


def _netplan_filename(uuid: str) -> str:
    if not UUID_RE.fullmatch(uuid):
        raise ValueError("Invalid NetworkManager UUID")
    return f"90-NM-{uuid.lower()}.yaml"


def _netplan_mapping_command(target: Path, uuid: str) -> tuple[str, ...]:
    return (
        "netplan",
        "generate",
        "--root-dir",
        str(target),
        "--mapping",
        f"NM-{uuid}",
    )


def _safe_netplan_snapshot(
    path: Path, expected_uuid: str, required_uid: int
) -> WifiProfileSnapshot | None:
    try:
        if path.name != _netplan_filename(expected_uuid):
            return None
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or path.is_symlink()
            or info.st_uid != required_uid
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size <= 0
            or info.st_size > MAX_NETPLAN_BYTES
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
        payload.decode("utf-8")
        return WifiProfileSnapshot(
            expected_uuid,
            path,
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
        )
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return None


def _read_frozen_netplan(
    snapshot: WifiProfileSnapshot, required_uid: int
) -> bytes:
    payload, info = _read_no_follow(snapshot.path)
    if (
        snapshot.path.name != _netplan_filename(snapshot.uuid)
        or info.st_uid != required_uid
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_size <= 0
        or info.st_size > MAX_NETPLAN_BYTES
        or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        != (
            snapshot.device,
            snapshot.inode,
            snapshot.size,
            snapshot.mtime_ns,
        )
    ):
        raise RuntimeError(
            f"Live Wi-Fi Netplan changed after preflight: {snapshot.path}"
        )
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError(
            f"Live Wi-Fi Netplan changed after preflight: {snapshot.path}"
        ) from error
    return payload


def _read_no_follow(path: Path) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_NETPLAN_BYTES:
            raise RuntimeError(f"Unsafe Netplan profile: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(MAX_NETPLAN_BYTES + 1)
        if len(payload) > MAX_NETPLAN_BYTES:
            raise RuntimeError(f"Netplan profile is too large: {path}")
        return payload, info
    finally:
        os.close(descriptor)


def _prepare_target_directory(
    target: Path,
    directory: Path,
    owner_uid: int,
    owner_gid: int,
) -> None:
    target_root = target.resolve(strict=True)
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError("Target Netplan directory is unsafe")
    resolved = directory.resolve(strict=True)
    if target_root not in resolved.parents:
        raise RuntimeError("Target Netplan directory escapes the target")
    os.chown(directory, owner_uid, owner_gid)
    os.chmod(directory, 0o755)


def _atomic_create_netplan(
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
        # Hard-link publication is atomic and cannot overwrite a target file
        # created between the earlier existence check and publication.
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    return target
