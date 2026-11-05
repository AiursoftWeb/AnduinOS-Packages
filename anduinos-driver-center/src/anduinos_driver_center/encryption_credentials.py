"""Replace managed LUKS credentials without rolling their state back with root.

The LUKS2 token stores roles and one interrupted transaction, never a secret.
A prepare record may be abandoned while the old credential is still intact.
A promoted record is durable evidence that the new slot was tested, so its old
slot can be retired. The machine slot and key never rotate here.
"""

from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from .encryption import encryption_state

TOKEN_TYPE = "anduinos-credentials"
ROLES = {"password", "recovery", "machine"}
HUMAN_PBKDF = (
    "--pbkdf", "argon2id", "--pbkdf-memory", "65536",
    "--pbkdf-parallel", "1", "--iter-time", "1000",
)


class CredentialError(RuntimeError):
    """Only fixed public codes cross the privileged boundary."""


@contextmanager
def key_file(value):
    descriptor = os.memfd_create("anduinos-credential", os.MFD_CLOEXEC)
    try:
        os.fchmod(descriptor, 0o600)
        data = value.encode("ascii") if isinstance(value, str) else value
        if os.write(descriptor, data) != len(data):
            raise CredentialError("operation-failed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        yield descriptor, f"/proc/self/fd/{descriptor}"
    finally:
        os.close(descriptor)


class Volume:
    def __init__(self, device, machine):
        self.device = device
        self.machine = machine

    def run(self, arguments, *, data=None, pass_fds=(), accepted=(0,)):
        result = subprocess.run(
            ["cryptsetup", *arguments], input=data, capture_output=True,
            timeout=180, check=False, pass_fds=pass_fds,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
        # Cryptsetup output may contain file paths or diagnostic context. It is
        # never forwarded to a desktop dialog or the generic helper log.
        if result.returncode not in accepted:
            raise CredentialError("operation-failed")
        return result

    def header(self):
        result = self.run(["luksDump", "--dump-json-metadata", self.device])
        return json.loads(result.stdout)

    def test(self, slot, value):
        with key_file(value) as (descriptor, name):
            result = self.run(
                ["open", "--type", "luks2", "--test-passphrase", "--key-slot", str(slot),
                 "--key-file", name, self.device],
                pass_fds=(descriptor,), accepted=(0, 2),
            )
        return result.returncode == 0

    def store(self, token_id, token, header):
        # Detect external edits between our commands. Cryptsetup still provides
        # each header operation's own locking; concurrent external editing is
        # not a supported management workflow.
        if self.header() != header:
            raise CredentialError("layout-changed")
        self.run(
            ["token", "import", "--token-id", str(token_id), "--token-replace",
             "--json-file", "-", self.device],
            data=json.dumps(token).encode(),
        )

    def add(self, slot, password_slot, current, replacement):
        with key_file(replacement) as (descriptor, name):
            self.run(
                ["luksAddKey", "--batch-mode", "--key-file", "-",
                 "--key-slot", str(password_slot), "--new-key-slot", str(slot),
                 *HUMAN_PBKDF, self.device, name],
                data=current.encode("ascii"), pass_fds=(descriptor,),
            )

    def remove(self, slot):
        self.run(["luksKillSlot", "--batch-mode", self.device, str(slot)], data=b"")


def current_volume():
    """Bind privileged actions to the actual root and its one LUKS2 parent."""
    root = encryption_state().root
    if root.encrypted is not True or root.filesystem not in {"btrfs", "ext4"}:
        raise CredentialError("unsupported-layout")
    info = os.stat(root.device)
    if not stat.S_ISBLK(info.st_mode):
        raise CredentialError("unsupported-layout")
    node = Path(f"/sys/dev/block/{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}")
    dm_uuid = (node / "dm/uuid").read_text().strip()
    parents = list((node / "slaves").iterdir())
    if not dm_uuid.startswith("CRYPT-LUKS2-") or len(parents) != 1:
        raise CredentialError("unsupported-layout")
    parent = parents[0]
    if not (parent / "partition").exists():
        raise CredentialError("unsupported-layout")
    try:
        descriptor = os.open("/etc/anduinos-luks/machine.key", os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise CredentialError("machine-unavailable") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise CredentialError("machine-unavailable")
        machine = os.read(descriptor, 65)
    finally:
        os.close(descriptor)
    if len(machine) != 64:
        raise CredentialError("machine-unavailable")
    volume = Volume("/dev/" + parent.name, machine)
    uuid = volume.run(["luksUUID", volume.device]).stdout.decode().strip()
    if not dm_uuid.startswith("CRYPT-LUKS2-" + uuid.replace("-", "") + "-"):
        raise CredentialError("layout-changed")
    return volume


def managed_token(header):
    tokens = header.get("tokens", {})
    found = [(number, token) for number, token in tokens.items() if token.get("type") == TOKEN_TYPE]
    if not found:
        if tokens:
            raise CredentialError("unsupported-layout")
        return None, None
    if len(found) != 1 or len(tokens) != 1:
        raise CredentialError("unsupported-layout")
    token_id, token = found[0]
    roles, pending = token.get("roles"), token.get("pending")
    if token.get("version") != 1 or not isinstance(roles, dict) or set(roles) != ROLES:
        raise CredentialError("unsupported-layout")
    slots = header["keyslots"]
    values = list(roles.values())
    if (any(type(value) is not str or not re.fullmatch(r"[0-9]|[12][0-9]|3[01]", value) for value in values)
            or len(set(values)) != 3 or not set(values) <= set(slots)):
        raise CredentialError("unsupported-layout")
    allowed = set(values)
    if pending is not None:
        if not isinstance(pending, dict) or set(pending) != {"role", "old", "new", "phase"}:
            raise CredentialError("unsupported-layout")
        role, phase = pending["role"], pending["phase"]
        if role not in {"password", "recovery"} or phase not in {"prepare", "promote"}:
            raise CredentialError("unsupported-layout")
        old, new = pending["old"], pending["new"]
        if any(type(value) is not str or not re.fullmatch(r"[0-9]|[12][0-9]|3[01]", value)
               for value in (old, new)):
            raise CredentialError("unsupported-layout")
        protected = set(values) - {roles[role]}
        if (old == new or roles[role] != (old if phase == "prepare" else new)
                or {old, new} & protected):
            raise CredentialError("unsupported-layout")
        allowed.update((pending["old"], pending["new"]))
    if not set(slots) <= allowed or set(token.get("keyslots", [])) != set(values):
        raise CredentialError("unsupported-layout")
    return token_id, copy.deepcopy(token)


def initialize(volume, current, recovery):
    """Adopt an earlier preview only after testing all three distinct roles."""
    header = volume.header()
    token_id, token = managed_token(header)
    if token is not None:
        return token_id, token
    if set(header["keyslots"]) != {"0", "1", "2"}:
        raise CredentialError("unsupported-layout")
    if not volume.test("0", current):
        raise CredentialError("wrong-password")
    if not recovery:
        raise CredentialError("setup-required")
    if not volume.test("1", recovery):
        raise CredentialError("wrong-recovery-key")
    if not volume.test("2", volume.machine):
        raise CredentialError("machine-unavailable")
    if (volume.test("1", current) or volume.test("0", recovery)
            or volume.test("2", current) or volume.test("2", recovery)):
        raise CredentialError("unsupported-layout")
    token = dict(type=TOKEN_TYPE, version=1, keyslots=["0", "1", "2"],
                 roles=dict(password="0", recovery="1", machine="2"), pending=None)
    volume.store("0", token, header)
    return "0", token


def save(volume, token_id, token, previous):
    token["keyslots"] = sorted(token["roles"].values(), key=int)
    header = volume.header()
    if managed_token(header) != (token_id, previous):
        raise CredentialError("layout-changed")
    volume.store(token_id, token, header)


def retire(volume, token_id, token, obsolete):
    header = volume.header()
    if managed_token(header) != (token_id, token) or obsolete in token["roles"].values():
        raise CredentialError("layout-changed")
    if obsolete in header["keyslots"]:
        volume.remove(obsolete)


def resume(volume, token_id, token):
    pending = token["pending"]
    if pending is None:
        return
    previous = copy.deepcopy(token)
    header = volume.header()
    if managed_token(header) != (token_id, token):
        raise CredentialError("layout-changed")
    # prepare: the original role is intact; discard only the unfinished new
    # slot. promote: the new role was tested before the durable promotion.
    obsolete = pending["new"] if pending["phase"] == "prepare" else pending["old"]
    retire(volume, token_id, token, obsolete)
    token["pending"] = None
    save(volume, token_id, token, previous)


def change(volume, request):
    if not isinstance(request, dict) or set(request) != {"role", "current", "replacement", "legacy_recovery"}:
        raise CredentialError("invalid-request")
    role = request["role"]
    if role not in {"password", "recovery"} or any(not isinstance(request[key], str) for key in ("current", "replacement", "legacy_recovery")):
        raise CredentialError("invalid-request")
    current, replacement = request["current"], request["replacement"]
    if (not re.fullmatch(r"[ -~]{8,128}", current)
            or not re.fullmatch(r"[ -~]{8,128}" if role == "password" else r"[0-9a-f]{48}", replacement)
            or len(request["legacy_recovery"]) > 128):
        raise CredentialError("invalid-request")
    token_id, token = initialize(volume, current, request["legacy_recovery"])
    if not volume.test(token["roles"]["password"], current):
        raise CredentialError("wrong-password")
    if not volume.test(token["roles"]["machine"], volume.machine):
        raise CredentialError("machine-unavailable")
    for slot in volume.header()["keyslots"]:
        if slot != token["roles"]["password"] and volume.test(slot, current):
            raise CredentialError("unsupported-layout")
    # Reject credential reuse before any recovery of an interrupted operation.
    for slot in volume.header()["keyslots"]:
        if token["pending"] and token["pending"]["phase"] == "prepare" and slot == token["pending"]["new"]:
            continue
        if volume.test(slot, replacement):
            raise CredentialError("credential-reused")
    resume(volume, token_id, token)
    header = volume.header()
    if managed_token(header)[1] != token:
        raise CredentialError("layout-changed")
    free = next((str(number) for number in range(32) if str(number) not in header["keyslots"]), None)
    if free is None:
        raise CredentialError("no-free-slot")
    old = token["roles"][role]
    previous = copy.deepcopy(token)
    token["pending"] = dict(role=role, old=old, new=free, phase="prepare")
    save(volume, token_id, token, previous)
    previous = copy.deepcopy(token)
    volume.add(free, token["roles"]["password"], current, replacement)
    if not volume.test(free, replacement):
        raise CredentialError("verification-failed")
    token["roles"][role] = free
    token["pending"]["phase"] = "promote"
    save(volume, token_id, token, previous)
    previous = copy.deepcopy(token)
    retire(volume, token_id, token, old)
    token["pending"] = None
    save(volume, token_id, token, previous)
    if managed_token(volume.header())[1] != token:
        raise CredentialError("verification-failed")
    return {"changed": role}


def main(operation, stream):
    """Reuse the existing polkit helper; secrets arrive only via bounded stdin."""
    try:
        if operation not in {"inspect", "change"}:
            raise CredentialError("invalid-request")
        descriptor = os.open("/run/lock/anduinos-encryption-credentials.lock",
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "r+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CredentialError("busy") from None
            volume = current_volume()
            if operation == "inspect":
                header = volume.header()
                _, token = managed_token(header)
                if token is None and set(header["keyslots"]) != {"0", "1", "2"}:
                    raise CredentialError("unsupported-layout")
                result = {"managed": token is not None, "pending": token is not None and token["pending"] is not None}
            else:
                raw = stream.readline(8193)
                if not raw or len(raw) > 8192:
                    raise CredentialError("invalid-request")
                result = change(volume, json.loads(raw))
        print(json.dumps(result), flush=True)
        return 0
    except CredentialError as error:
        code = str(error)
    except Exception:
        code = "operation-failed"
    print(json.dumps({"error": code}), flush=True)
    return 1
