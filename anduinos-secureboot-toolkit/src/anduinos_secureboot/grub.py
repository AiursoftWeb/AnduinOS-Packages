"""Sign pristine package GRUB and deploy through the existing ESP boundary.

No generic signing arguments, certificate generation, fallback-loader writes,
or deletion of firmware trust. Package hooks and interactive repairs share this
implementation. Generated EFI and public certificates are not signing keys.
"""
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from . import boot_chain, identity
from .defaults import atomic_write, safe_path
from .firmware import probe_firmware
from .inspect import SubprocessRunner
from .model import GrubState, SecureBootStatus

SOURCE = Path("/usr/lib/anduinos-grub/grubx64.efi")
STORE = Path("/var/lib/anduinos-grub")
SIGNED = STORE / "grubx64.efi"
CERTIFICATE = STORE / "certificate.der"
PROBE = "/usr/libexec/anduinos-grub-probe"
PACKAGES = ("anduinos-grub", "anduinos-shim-signed")
OLD_PACKAGES = {"grub-common", "grub2-common", "grub-pc", "grub-pc-bin", "grub-efi-amd64",
                "grub-efi-amd64-bin", "grub-efi-amd64-unsigned", "grub-efi-amd64-signed", "shim-signed", "shim"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def packages_installed(runner):
    return all(runner.run(["dpkg-query", "--show", "--showformat=${db:Status-Status}", package]).stdout.strip()
               == "installed" for package in PACKAGES)


def signing_material(runner, *, first_install=False):
    evidence = probe_firmware(run=lambda command, **kwargs: runner.run(command))
    if evidence.status is SecureBootStatus.UNKNOWN:
        raise ValueError("Secure Boot state could not be determined")
    key, cert = identity.selected_paths(runner=runner)
    safe_path(key)
    safe_path(cert)
    identity.check_unified_pair(key, cert, runner.run)
    if (evidence.status is SecureBootStatus.ENABLED and not first_install
            and identity.enrollment_state(cert, runner) != "enrolled"):
        raise ValueError("Enroll the unified certificate before replacing the bootloader")
    return key, cert


def package_payload(path, runner, owners):
    """Refuse locally modified inputs; verify only the immutable EFI payload."""
    safe_path(path)
    owner = boot_chain.command(runner, ["dpkg-query", "--search", str(path)]).strip()
    if "\n" in owner or ": /" not in owner:
        raise ValueError(f"Ambiguous package ownership: {path}")
    package = owner.split(": /", 1)[0]
    if package.split(":")[0] not in owners:
        raise ValueError(f"Unexpected bootloader payload owner: {path}")
    checked = runner.run(["dpkg", "--verify", package], timeout=60)
    if checked.returncode or any(str(path) in line for line in checked.stdout.splitlines()):
        raise ValueError(f"Package bootloader payload was modified: {path}")
    if not path.is_file():
        raise ValueError(f"Bootloader payload is missing: {path}")


def verify_signature(image, certificate, runner):
    with tempfile.TemporaryDirectory(prefix="anduinos-grub-verify-") as directory:
        pem = Path(directory) / "certificate.pem"
        boot_chain.command(runner, ["openssl", "x509", "-inform", "DER", "-in",
                                   str(certificate), "-out", str(pem)])
        boot_chain.command(runner, ["sbverify", "--cert", str(pem), str(image)])
    boot_chain.verify_image(image, 0x8664, runner)


def prepare_image(runner, *, first_install=False, source=SOURCE, store=STORE):
    key, certificate = signing_material(runner, first_install=first_install)
    package_payload(source, runner, {"anduinos-grub"})
    safe_path(store)
    store.mkdir(parents=True, exist_ok=True, mode=0o755)
    image, public = store / "grubx64.efi", store / "certificate.der"
    manifest = store / "image.json"
    expected = {"source_sha256": digest(source), "certificate_sha256": digest(certificate)}
    for path in (image, public, manifest):
        safe_path(path)
    if image.is_file() and public.is_file() and manifest.is_file():
        recorded = json.loads(manifest.read_text())
        if all(recorded.get(k) == v for k, v in expected.items()) and recorded.get("image_sha256") == digest(image):
            verify_signature(image, certificate, runner)
            _compatibility_image(image)
            return image
    # Sign only a package-owned pristine template. Never bless an ESP file.
    with tempfile.TemporaryDirectory(prefix=".candidate-", dir=store) as directory:
        candidate = Path(directory) / "grubx64.efi"
        pem = Path(directory) / "certificate.pem"
        boot_chain.command(runner, ["openssl", "x509", "-inform", "DER", "-in",
                                   str(certificate), "-out", str(pem)])
        boot_chain.command(runner, ["sbsign", "--key", str(key), "--cert", str(pem),
                                   "--output", str(candidate), str(source)])
        verify_signature(candidate, certificate, runner)
        image_bytes = candidate.read_bytes()
        expected["image_sha256"] = hashlib.sha256(image_bytes).hexdigest()
        atomic_write(image, image_bytes, 0o644)
        atomic_write(public, certificate.read_bytes(), 0o644)
        atomic_write(manifest, json.dumps(expected).encode(), 0o644)
    # Compatibility with grub-install's --uefi-secure-boot and the installer.
    _compatibility_image(image)
    return image


def _compatibility_image(image):
    compatibility = Path("/usr/lib/grub/x86_64-efi-signed")
    safe_path(compatibility)
    compatibility.mkdir(parents=True, exist_ok=True, mode=0o755)
    safe_path(compatibility / "grubx64.efi.signed")
    atomic_write(compatibility / "grubx64.efi.signed", image.read_bytes(), 0o644)


def payloads(runner):
    image = prepare_image(runner)
    sources = {
        "grubx64.efi": image,
        "shimx64.efi": Path("/usr/lib/shim/shimx64.efi.signed.latest"),
        "mmx64.efi": Path("/usr/lib/shim/mmx64.efi"),
    }
    for name, path in sources.items():
        if name != "grubx64.efi":
            package_payload(path, runner, {"anduinos-shim-signed"})
            boot_chain.verify_image(path, 0x8664, runner)
    return sources, 0x8664


def deploy(runner, *, package_transaction=False, esp=boot_chain.ESP):
    signing_material(runner)
    if not packages_installed(runner):
        raise ValueError("Install both AnduinOS bootloader packages first")
    target = boot_chain.resolve_target(runner, esp)
    vendor = boot_chain.vendor_path(esp, target.entry)
    safe_path(STORE)
    STORE.mkdir(parents=True, exist_ok=True, mode=0o755)
    backup = Path(tempfile.mkdtemp(prefix="boot-", dir=STORE))
    # Durable recovery artifacts precede every ESP mutation; no private key.
    for path in vendor.iterdir():
        if path.is_file() and not path.is_symlink():
            atomic_write(backup / path.name, path.read_bytes(), 0o600)
    atomic_write(backup / "nvram.txt", target.nvram.encode(), 0o600)
    boot_chain.prepare_boot_chain(runner, esp, payload_loader=payloads,
                                  package_transaction=package_transaction)
    inspected = inspect_grub(runner, privileged=True, esp=esp)
    if inspected.deployment != "ready" or inspected.signature != "valid":
        raise ValueError("Bootloader deployment could not be verified: " + inspected.detail)


def inspect_grub(runner=None, *, privileged=False, esp=boot_chain.ESP):
    """Prepared-file verification and actual ESP verification stay distinct."""
    runner = runner or SubprocessRunner()
    architecture = runner.run(["dpkg", "--print-architecture"]).stdout.strip()
    if architecture != "amd64":
        return GrubState(supported=False, detail="AnduinOS GRUB currently supports amd64 only")
    if not packages_installed(runner):
        return GrubState(detail="AnduinOS GRUB and shim are not installed")
    if os.geteuid() != 0 and not privileged:
        result = runner.run(["pkexec", PROBE], timeout=60)
        try:
            if result.returncode:
                raise ValueError(result.stderr.strip() or "Bootloader inspection was not authorized")
            data = json.loads(result.stdout)
            return GrubState(**data)
        except (ValueError, TypeError):
            return GrubState(installed=True, detail="Bootloader inspection could not be completed")
    state = GrubState(installed=True)
    try:
        package_payload(SOURCE, runner, {"anduinos-grub"})
        if not SIGNED.is_file() or not CERTIFICATE.is_file():
            return replace(state, detail="The package GRUB has not been signed")
        verify_signature(SIGNED, CERTIFICATE, runner)
        state = replace(state, signature="valid", enrollment=identity.enrollment_state(CERTIFICATE, runner))
        _, selected_certificate = identity.selected_paths(runner=runner)
        if not selected_certificate.is_file() or digest(selected_certificate) != digest(CERTIFICATE):
            return replace(state, deployment="pending", detail="GRUB needs signing with the current unified certificate")
        recorded = json.loads((STORE / "image.json").read_text())
        if (recorded.get("source_sha256") != digest(SOURCE)
                or recorded.get("image_sha256") != digest(SIGNED)
                or recorded.get("certificate_sha256") != digest(CERTIFICATE)):
            return replace(state, deployment="pending", detail="A bootloader update needs signing and deployment")
        target = boot_chain.resolve_target(runner, esp)
        vendor = boot_chain.vendor_path(esp, target.entry)
        if "\\shim" not in target.entry.loader.lower():
            return replace(state, deployment="pending", detail="The boot entry does not use shim")
        deployed = vendor / "grubx64.efi"
        verify_signature(deployed, CERTIFICATE, runner)
        if digest(deployed) != digest(SIGNED):
            return replace(state, deployment="pending", detail="The signed GRUB has not been deployed")
        for name, source in (("shimx64.efi", Path("/usr/lib/shim/shimx64.efi.signed.latest")),
                             ("mmx64.efi", Path("/usr/lib/shim/mmx64.efi"))):
            package_payload(source, runner, {"anduinos-shim-signed"})
            boot_chain.verify_image(source, 0x8664, runner)
            if (vendor / name).read_bytes() != source.read_bytes():
                return replace(state, deployment="pending", detail="The official shim companions need deployment")
        return replace(state, deployment="ready")
    except (OSError, ValueError, KeyError, TypeError) as error:
        return replace(state, detail=str(error))


def install_packages(run):
    from .operations import CallableRunner, OperationResult, StepResult
    runner = CallableRunner(run)
    signing_material(runner)
    boot_chain.resolve_target(runner)
    # Protected Ubuntu boot packages need an explicit removal request, even
    # when a compatible provider conflicts with them. Request only real,
    # currently installed packages, not virtual names provided by our new one.
    old = [package + "-" for package in sorted(OLD_PACKAGES)
           if runner.run(["dpkg-query", "--show", "--showformat=${db:Status-Status}", package]).stdout.strip() == "installed"]
    selection = [*PACKAGES, *old]
    simulation = boot_chain.command(runner, ["apt-get", "--simulate", "--allow-remove-essential", "--no-install-recommends", "install", *selection])
    removals = set(re.findall(r"^Remv\s+(\S+)", simulation, re.MULTILINE))
    if removals - OLD_PACKAGES:
        raise ValueError("Bootloader replacement would remove unrelated packages: " + ", ".join(sorted(removals - OLD_PACKAGES)))
    # Ubuntu marks shim Protected. Permit that exact, prechecked replacement;
    # unrelated essential/system removals are rejected by the simulation above.
    completed = run(["apt-get", "-y", "--allow-remove-essential", "--no-install-recommends", "install", *selection], timeout=1800)
    result = OperationResult("install-bootloader")
    result.steps["packages"] = StepResult("success" if completed.returncode == 0 else "failed",
                                        (completed.stderr or completed.stdout)[-4000:])
    return result


def update_main(arguments):
    from .operations import LOCK_FILE
    if os.geteuid() != 0 or arguments not in (["check"], ["prepare"], ["deploy"], ["package-deploy"], ["first-install"]):
        return 64
    runner = SubprocessRunner()
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if arguments == ["check"]:
            signing_material(runner)
            boot_chain.resolve_target(runner)
        elif arguments in (["prepare"], ["first-install"]):
            prepare_image(runner, first_install=arguments == ["first-install"])
        else:
            # Rootfs assembly installs payloads only; the installer signs after
            # generating its per-target certificate, before grub-install.
            if arguments == ["package-deploy"] and not (Path("/boot/grub/grub.cfg").is_file() and os.path.ismount("/boot/efi")):
                return 0
            if arguments == ["package-deploy"] and not packages_installed(runner):
                return 0
            deploy(runner, package_transaction=arguments == ["package-deploy"])
    return 0
