"""Stream and authenticate an ATGZ; never use extractall or execute payloads."""
import gzip
import hashlib
import shutil
import tarfile
import tempfile
from pathlib import Path

import apt_pkg

from .manifest import MAX_PAYLOAD, METADATA, identity, path, read_json, require, validate
from .native import BundleError, paragraphs, run


class BoundedTarInfo(tarfile.TarInfo):
    def _proc_pax(self, archive):
        require(self.type == tarfile.XHDTYPE and 0 < self.size <= 65536, "Unsupported or oversized PAX header")
        depth = getattr(archive, "_offline_pax_depth", 0) + 1
        require(depth <= 8, "Too many nested extended headers")
        archive._offline_pax_depth = depth
        try:
            return super()._proc_pax(archive)
        finally:
            archive._offline_pax_depth -= 1

    def _proc_gnusparse_00(self, *args):
        raise BundleError("Sparse archive entries are unsupported")

    _proc_gnusparse_01 = _proc_gnusparse_00
    _proc_gnusparse_10 = _proc_gnusparse_00

    def _proc_gnulong(self, archive):
        raise BundleError("GNU long-name headers are unsupported")

    def _proc_sparse(self, archive):
        raise BundleError("Sparse archive entries are unsupported")


def signature(data, signed, keyring, work, *, clear_output=None):
    require(Path(keyring).is_file(), "An external trusted keyring is required")
    args = ["gpgv", "--homedir", str(work), "--keyring", str(Path(keyring).resolve()), "--status-fd", "1"]
    if clear_output is not None:
        args += ["--output", str(clear_output), str(signed)]
    else:
        args += [str(signed), str(data)]
    status = run(args).decode("utf-8", errors="strict").splitlines()
    invalid = ("EXPKEYSIG", "EXPSIG", "REVKEYSIG", "KEYEXPIRED", "SIGEXPIRED")
    require(not any(line.startswith("[GNUPG:] " + code) for line in status for code in invalid), "Expired or revoked signing material")
    valid = [line.split()[2] for line in status if line.startswith("[GNUPG:] VALIDSIG ")]
    require(len(valid) == 1, "Expected one valid signature")
    return valid[0]


def regular(entry):
    require(entry is not None, "Truncated archive")
    path(entry.name)
    require(entry.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not entry.linkname and entry.sparse is None and
            0 <= entry.size <= MAX_PAYLOAD and not any(k.startswith("GNU.sparse") for k in entry.pax_headers), "Only bounded regular files are supported")


def header(archive, name, limit):
    entry = archive.next()
    regular(entry)
    require(entry.name == name and 0 < entry.size <= limit, f"Expected bounded {name}")
    return archive.extractfile(entry).read()


def check_repository(root, m, signer, keyring, work):
    repository = root / "repository"
    release = repository / "Release"
    clear = work / "verified-release"
    require(signature(release, repository / "Release.gpg", keyring, work) == signer and
            signature(None, repository / "InRelease", keyring, work, clear_output=clear) == signer and
            clear.read_bytes() == release.read_bytes(), "Repository signature or Release content disagrees with bundle")
    records = paragraphs(release.read_text())
    expected = m["files"]["repository/Packages"]
    require(len(records) == 1, "Expected one Release record")
    r = records[0]
    require(r.get("Suite") == m["suite"] and r.get("Codename") == m["suite"] and
            r.get("Architectures") == m["architecture"] + " all" and
            r.get("SHA256", "").strip() == f'{expected["sha256"]} {expected["size"]} Packages', "Release differs from manifest")
    inventory = {identity(p["identity"]): p for p in m["packages"]}
    for record in paragraphs((repository / "Packages").read_text()):
        key = (record.get("Package"), record.get("Version"), record.get("Architecture"))
        pkg = inventory.pop(key, None)
        require(pkg is not None and record.get("Filename") == pkg["path"][len("repository/"):] and
                record.get("Size") == str(pkg["content"]["size"]) and record.get("SHA256") == pkg["content"]["sha256"], "APT index differs from manifest")
    require(not inventory, "APT index is missing packages")
    if m.get("bootstrap"):
        boot = m["bootstrap"]
        pkg = next(p for p in m["packages"] if p["identity"]["name"] == boot["package"])
        require(apt_pkg.version_compare(pkg["identity"]["version"], boot["minimum_version"]) >= 0, "Bundled updater does not meet its minimum version")


def verify(file, keyring, destination):
    """Caller owns an empty temporary destination; all output is disposable."""
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    require(not any(root.iterdir()), "Verification directory must be empty")
    with tempfile.TemporaryDirectory(prefix="offline-signature-") as name:
        work = Path(name)
        try:
            with gzip.open(file, "rb") as stream, tarfile.open(fileobj=stream, mode="r:", tarinfo=BoundedTarInfo) as archive:
                raw = header(archive, "manifest.json", 4 * 1024 ** 2)
                signed = header(archive, "manifest.asc", 256 * 1024)
                (work / "manifest.json").write_bytes(raw)
                (work / "manifest.asc").write_bytes(signed)
                signer = signature(work / "manifest.json", work / "manifest.asc", keyring, work)
                m = validate(read_json(raw))
                total = sum(v["size"] for v in m["files"].values())
                require(shutil.disk_usage(root).free >= total, "Insufficient space for unpacking; no payload was extracted")
                seen = set()
                while (entry := archive.next()) is not None:
                    regular(entry)
                    expected = m["files"].get(entry.name)
                    require(entry.name not in seen and expected is not None and entry.size == expected["size"], "Unexpected, duplicate or wrong-size payload")
                    seen.add(entry.name)
                    target = root / entry.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    count = 0
                    with archive.extractfile(entry) as source, target.open("xb") as output:
                        while data := source.read(128 * 1024):
                            count += len(data)
                            require(count <= expected["size"], "Payload exceeds declared size")
                            digest.update(data); output.write(data)
                    require(count == expected["size"] and digest.hexdigest() == expected["sha256"], "Payload checksum or size mismatch")
                require(seen == set(m["files"]), "Missing payload")
                # Consume the gzip trailer/CRC, allowing only bounded tar zero padding.
                tail = stream.read(10241)
                require(len(tail) <= 10240 and not any(tail) and stream.read(1) == b"", "Unexpected content after tar archive")
            check_repository(root, m, signer, keyring, work)
            return {"manifest": m, "signer": signer, "unpacked_bytes": total}
        except (OSError, EOFError, tarfile.TarError, UnicodeError) as error:
            raise BundleError(f"Invalid or unreadable bundle: {error}") from error
