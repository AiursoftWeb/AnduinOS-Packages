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

SOURCE_DIRECTORY = Path("/usr/lib/anduinos-grub")
STORE = Path("/var/lib/anduinos-grub")
PROBE = "/usr/libexec/anduinos-grub-probe"
PACKAGES = ("anduinos-grub", "anduinos-shim-signed")


def source_image(runner):
    suffix, _, _ = boot_chain.efi_architecture(runner)
    return SOURCE_DIRECTORY / f"grub{suffix}.efi"


def shim_companions(runner):
    suffix, _, _ = boot_chain.efi_architecture(runner)
    return {f"shim{suffix}.efi": Path(f"/usr/lib/shim/shim{suffix}.efi.signed.latest"),
            f"mm{suffix}.efi": Path(f"/usr/lib/shim/mm{suffix}.efi")}


def old_packages(runner):
    suffix, platform, _ = boot_chain.efi_architecture(runner)
    architecture = "amd64" if suffix == "x64" else platform
    packages = {"grub-common", "grub2-common", "shim-signed", "shim"}
    packages.update(f"grub-efi-{architecture}{ending}" for ending in ("", "-bin", "-unsigned", "-signed"))
    if architecture == "amd64":
        packages.update(("grub-pc", "grub-pc-bin"))
    return packages


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
    _, _, machine = boot_chain.efi_architecture(runner)
    boot_chain.verify_image(image, machine, runner)


def prepare_image(runner, *, first_install=False, source=None, store=STORE):
    source = source if source is not None else source_image(runner)
    suffix, _, _ = boot_chain.efi_architecture(runner)
    key, certificate = signing_material(runner, first_install=first_install)
    package_payload(source, runner, {"anduinos-grub"})
    safe_path(store)
    store.mkdir(parents=True, exist_ok=True, mode=0o755)
    image, public = store / f"grub{suffix}.efi", store / "certificate.der"
    manifest = store / "image.json"
    expected = {"source_sha256": digest(source), "certificate_sha256": digest(certificate)}
    for path in (image, public, manifest):
        safe_path(path)
    cached = False
    if image.is_file() and public.is_file() and manifest.is_file():
        try:
            recorded = json.loads(manifest.read_text())
            cached = (isinstance(recorded, dict)
                      and all(recorded.get(k) == v for k, v in expected.items())
                      and recorded.get("image_sha256") == digest(image)
                      and digest(public) == expected["certificate_sha256"])
            if cached:
                verify_signature(image, certificate, runner)
        except (OSError, ValueError):
            # Only derived cache validation is recoverable. Authority and
            # pristine package checks above must still fail before signing.
            cached = False
    if cached:
        _compatibility_image(image, runner)
        return image
    # Sign only a package-owned pristine template. Never bless an ESP file.
    with tempfile.TemporaryDirectory(prefix=".candidate-", dir=store) as directory:
        candidate = Path(directory) / image.name
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
    _compatibility_image(image, runner)
    return image


def _compatibility_image(image, runner):
    suffix, platform, _ = boot_chain.efi_architecture(runner)
    compatibility = Path(f"/usr/lib/grub/{platform}-efi-signed")
    safe_path(compatibility)
    compatibility.mkdir(parents=True, exist_ok=True, mode=0o755)
    destination = compatibility / f"grub{suffix}.efi.signed"
    safe_path(destination)
    atomic_write(destination, image.read_bytes(), 0o644)


def payloads(runner):
    image = prepare_image(runner)
    _, _, machine = boot_chain.efi_architecture(runner)
    companions = shim_companions(runner)
    for path in companions.values():
        package_payload(path, runner, {"anduinos-shim-signed"})
        boot_chain.verify_image(path, machine, runner)
    return {image.name: image, **companions}, machine


def verify_first_install(runner, esp=boot_chain.ESP):
    """Read back the installed vendor chain before requesting MOK enrollment.

    Fresh targets use the installer's legacy identity, even before enrollment.
    Rescue of an existing managed target uses its selected signing identity.
    Neither path signs again or resolves the Live system's BootCurrent.
    """
    suffix, _, machine = boot_chain.efi_architecture(runner)
    vendor = esp / "EFI/AnduinOS"
    safe_path(vendor)
    source = source_image(runner)
    if source.parent.exists():
        package_payload(source, runner, {"anduinos-grub"})
        deployed = vendor / f"grub{suffix}.efi"
        safe_path(deployed)
        certificate = identity.LEGACY_CERTIFICATE
        if identity.current_identity(identity.ROOT):
            _, certificate = identity.selected_paths(identity.ROOT, runner=runner)
        verify_signature(deployed, certificate, runner)
        sources = shim_companions(runner)
        for path in sources.values():
            package_payload(path, runner, {"anduinos-shim-signed"})
    else:
        # Preserve installations using the official Ubuntu signed GRUB.
        sources, machine = boot_chain.payloads(runner)
    for name, source in sources.items():
        deployed = vendor / name
        safe_path(deployed)
        if deployed.read_bytes() != source.read_bytes():
            raise ValueError(f"Installed EFI image differs from its package payload: {deployed}")
        boot_chain.verify_image(deployed, machine, runner)


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
    suffix, _, _ = boot_chain.efi_architecture(runner)
    fallback = boot_chain.owned_fallback(runner, esp, target, shim_companions(runner)[f"shim{suffix}.efi"])
    if fallback is not None:
        (backup / "fallback").mkdir(mode=0o700)
        for name in (f"BOOT{suffix.upper()}.EFI", f"grub{suffix}.efi", f"mm{suffix}.efi", "grub.cfg"):
            path = fallback / name
            if path.is_file() and not path.is_symlink():
                atomic_write(backup / "fallback" / name, path.read_bytes(), 0o600)
    atomic_write(backup / "nvram.txt", target.nvram.encode(), 0o600)
    boot_chain.prepare_boot_chain(runner, esp, payload_loader=payloads,
                                  package_transaction=package_transaction)
    inspected = inspect_grub(runner, privileged=True, esp=esp)
    if inspected.deployment != "ready" or inspected.signature != "valid":
        raise ValueError("Bootloader deployment could not be verified: " + inspected.detail)


def inspect_active_chain(runner, esp):
    """Verify installed EFI files without depending on cache JSON or a new key.

    Only certificates confirmed enrolled in MOK can authenticate GRUB. The
    compatibility certificate preserves old trust during enrollment; the cache
    certificate can preserve it after signing defaults have moved to a new key.
    This does not certify the next kernel or firmware CA configuration.
    """
    try:
        suffix, _, machine = boot_chain.efi_architecture(runner)
        target = boot_chain.resolve_target(runner, esp)
        vendor = boot_chain.vendor_path(esp, target.entry)
        if target.entry.loader.lower() != rf"\EFI\{vendor.name}\shim{suffix}.efi".lower():
            return "untrusted", "The boot entry does not use the native AnduinOS shim"
        companions = shim_companions(runner)
        for source in companions.values():
            package_payload(source, runner, {"anduinos-shim-signed"})
            boot_chain.verify_image(source, machine, runner)
        certificates = []
        for certificate in (identity.LEGACY_CERTIFICATE, STORE / "certificate.der"):
            if certificate.is_file() and not certificate.is_symlink():
                safe_path(certificate)
                if identity.enrollment_state(certificate, runner) == "enrolled":
                    text = boot_chain.command(runner, ["openssl", "x509", "-inform", "DER",
                                                       "-in", str(certificate), "-noout", "-text"])
                    if identity.certificate_purpose(text) == "unified":
                        certificates.append(certificate)
        if not certificates:
            return "unknown", "No enrolled certificate is available to verify the installed GRUB"
        directories = [vendor]
        fallback = boot_chain.owned_fallback(runner, esp, target, companions[f"shim{suffix}.efi"])
        if fallback is not None:
            directories.append(fallback)
        for directory in directories:
            for name, source in companions.items():
                path = directory / (f"BOOT{suffix.upper()}.EFI" if directory == fallback and name.startswith("shim") else name)
                if path.is_symlink() or not path.is_file() or path.read_bytes() != source.read_bytes():
                    return "untrusted", f"Installed shim companion differs from its trusted package: {path}"
            image = directory / f"grub{suffix}.efi"
            if image.is_symlink() or not image.is_file():
                return "untrusted", f"Installed GRUB is missing or redirected: {image}"
            for certificate in certificates:
                try:
                    verify_signature(image, certificate, runner)
                    break
                except ValueError:
                    continue
            else:
                return "untrusted", f"Installed GRUB cannot be verified with an enrolled certificate: {image}"
        return "trusted", ""
    except (OSError, ValueError, KeyError, TypeError) as error:
        return "unknown", str(error)


def inspect_grub(runner=None, *, privileged=False, esp=boot_chain.ESP):
    """Prepared-file verification and actual ESP verification stay distinct."""
    runner = runner or SubprocessRunner()
    try:
        suffix, _, machine = boot_chain.efi_architecture(runner)
    except ValueError as error:
        return GrubState(supported=False, detail=str(error))
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
    active_chain, active_detail = inspect_active_chain(runner, esp)
    state = GrubState(installed=True, active_chain=active_chain, active_detail=active_detail)
    try:
        source = source_image(runner)
        signed, certificate = STORE / f"grub{suffix}.efi", STORE / "certificate.der"
        package_payload(source, runner, {"anduinos-grub"})
        if not signed.is_file() or not certificate.is_file():
            return replace(state, detail="The package GRUB has not been signed")
        verify_signature(signed, certificate, runner)
        state = replace(state, signature="valid", enrollment=identity.enrollment_state(certificate, runner))
        _, selected_certificate = identity.selected_paths(runner=runner)
        if not selected_certificate.is_file() or digest(selected_certificate) != digest(certificate):
            return replace(state, deployment="pending", detail="GRUB needs signing with the current unified certificate")
        recorded = json.loads((STORE / "image.json").read_text())
        if (not isinstance(recorded, dict)
                or recorded.get("source_sha256") != digest(source)
                or recorded.get("image_sha256") != digest(signed)
                or recorded.get("certificate_sha256") != digest(certificate)):
            return replace(state, deployment="pending", detail="A bootloader update needs signing and deployment")
        target = boot_chain.resolve_target(runner, esp)
        vendor = boot_chain.vendor_path(esp, target.entry)
        if "\\shim" not in target.entry.loader.lower():
            return replace(state, deployment="pending", detail="The boot entry does not use shim")
        if target.entry.loader.lower() != rf"\EFI\{vendor.name}\shim{suffix}.efi".lower():
            return replace(state, deployment="pending", detail="Boot entry architecture does not match the installed system")
        deployed = vendor / f"grub{suffix}.efi"
        verify_signature(deployed, certificate, runner)
        if digest(deployed) != digest(signed):
            return replace(state, deployment="pending", detail="The signed GRUB has not been deployed")
        for name, source in shim_companions(runner).items():
            package_payload(source, runner, {"anduinos-shim-signed"})
            boot_chain.verify_image(source, machine, runner)
            if (vendor / name).read_bytes() != source.read_bytes():
                return replace(state, deployment="pending", detail="The official shim companions need deployment")
        sources = {signed.name: signed, **shim_companions(runner)}
        fallback = boot_chain.owned_fallback(runner, esp, target, sources[f"shim{suffix}.efi"])
        if fallback is not None:
            for name, source in sources.items():
                path = fallback / (f"BOOT{suffix.upper()}.EFI" if name.startswith("shim") else name)
                if path.is_symlink() or path.read_bytes() != source.read_bytes():
                    return replace(state, deployment="pending", detail="The portable bootloader needs deployment")
        return replace(state, deployment="ready")
    except (OSError, ValueError, KeyError, TypeError) as error:
        return replace(state, detail=str(error))


def install_packages(run):
    from .operations import CallableRunner, OperationResult, StepResult
    runner = CallableRunner(run)
    signing_material(runner)
    allowed_removals = old_packages(runner)
    boot_chain.resolve_target(runner)
    # Protected Ubuntu boot packages need an explicit removal request, even
    # when a compatible provider conflicts with them. Request only real,
    # currently installed packages, not virtual names provided by our new one.
    old = [package + "-" for package in sorted(allowed_removals)
           if runner.run(["dpkg-query", "--show", "--showformat=${db:Status-Status}", package]).stdout.strip() == "installed"]
    selection = [*PACKAGES, *old]
    simulation = boot_chain.command(runner, ["apt-get", "--simulate", "--allow-remove-essential", "--no-install-recommends", "install", *selection])
    removals = set(re.findall(r"^Remv\s+(\S+)", simulation, re.MULTILINE))
    if removals - allowed_removals:
        raise ValueError("Bootloader replacement would remove unrelated packages: " + ", ".join(sorted(removals - allowed_removals)))
    # Ubuntu marks shim Protected. Permit that exact, prechecked replacement;
    # unrelated essential/system removals are rejected by the simulation above.
    completed = run(["apt-get", "-y", "--allow-remove-essential", "--no-install-recommends", "install", *selection], timeout=1800)
    result = OperationResult("install-bootloader")
    result.steps["packages"] = StepResult("success" if completed.returncode == 0 else "failed",
                                        "\n".join(output.strip() for output in
                                                  (completed.stdout, completed.stderr) if output.strip()))
    return result


def update_main(arguments):
    from .operations import LOCK_FILE
    if os.geteuid() != 0 or arguments not in (["check"], ["prepare"], ["deploy"], ["package-deploy"], ["first-install"], ["first-install-check"]):
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
        elif arguments == ["first-install-check"]:
            verify_first_install(runner)
        else:
            # Rootfs assembly installs payloads only; the installer signs after
            # generating its per-target certificate, before grub-install.
            if arguments == ["package-deploy"] and not (Path("/boot/grub/grub.cfg").is_file() and os.path.ismount("/boot/efi")):
                return 0
            if arguments == ["package-deploy"] and not packages_installed(runner):
                return 0
            deploy(runner, package_transaction=arguments == ["package-deploy"])
    return 0
