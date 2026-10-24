#!/usr/bin/env python3
"""Assemble a target initramfs from verified package inputs, without a chroot."""
import gzip
import hashlib
import json
import os
import posixpath
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
LVGL_VERSION = "v9.6.0"
# Storage, input and firmware framebuffer support; no network or desktop session.
DRIVERS = "virtio_pci virtio_mmio virtio_blk virtio_scsi nvme ahci sd_mod usb_storage uas xhci_pci xhci_plat_hcd ehci_pci ohci_pci uhci_hcd usbhid hid_generic atkbd i8042 evdev psmouse virtio_input sdhci_pci sdhci_acpi mmc_block btrfs ext4 simpledrm virtio_gpu bochs".split()


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(*args):
    return run(*args, capture_output=True, text=True).stdout.strip()


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


class Runtime:
    """Copy selected files and ELF dependencies, resolving links inside inputs only."""
    def __init__(self, source, root, arch):
        self.source, self.root = source, root
        self.triplet = {"amd64": "x86_64-linux-gnu", "arm64": "aarch64-linux-gnu"}[arch]
        self.copied = set()

    def resolve(self, path):
        parts = path.lstrip("/").split("/")
        for _ in range(40):
            for index in range(len(parts)):
                candidate = self.source.joinpath(*parts[:index + 1])
                if candidate.is_symlink():
                    target = os.readlink(candidate)
                    absolute = posixpath.normpath(posixpath.join("/", *parts[:index], target, *parts[index + 1:]))
                    parts = absolute.lstrip("/").split("/")
                    break
            else:
                return self.source.joinpath(*parts)
        raise RuntimeError(f"Too many runtime symlinks: {path}")

    def dependencies(self, binary):
        if binary.read_bytes()[:4] != b"\x7fELF":
            return
        dynamic = output("readelf", "-d", str(binary))
        for name in re.findall(r"Shared library: \[(.*?)\]", dynamic):
            for directory in (f"usr/lib/{self.triplet}", "usr/lib", f"usr/lib/{self.triplet}/systemd", "usr/lib/systemd"):
                path = f"/{directory}/{name}"
                if self.resolve(path).is_file():
                    self.file(path)
                    break
            else:
                raise RuntimeError(f"Missing runtime library {name} required by {binary.name}")
        headers = output("readelf", "-l", str(binary))
        for interpreter in re.findall(r"Requesting program interpreter: (.*?)\]", headers):
            self.file(interpreter)

    def file(self, path):
        if path in self.copied:
            return
        self.copied.add(path)
        destination = self.root / path.lstrip("/")
        copy(self.resolve(path), destination)
        self.dependencies(destination)


def build_gui(runtime, directory, lvgl):
    """Compile the direct framebuffer UI and its original Fluent pointer."""
    directory.mkdir(parents=True, exist_ok=True)
    cursor = (HERE / "data/cursor.xcursor").read_bytes()
    magic, header, _, count = struct.unpack_from("<4I", cursor)
    if magic != 0x72756358:
        raise RuntimeError("Invalid Fluent Xcursor resource")
    entries = [struct.unpack_from("<3I", cursor, header + i * 12) for i in range(count)]
    position = next(pos for kind, size, pos in entries if kind == 0xfffd0002 and size == 24)
    header, _, _, _, width, height, xhot, yhot, _ = struct.unpack_from("<9I", cursor, position)
    pixels = cursor[position + header:position + header + width * height * 4]
    if len(pixels) != width * height * 4 or not (xhot < width and yhot < height):
        raise RuntimeError("Incomplete Fluent cursor image")
    (directory / "cursor.h").write_text(
        "/* Fluent-icon-theme, vinceliuice, GPL-3.0; see cursor.COPYING. */\n"
        f"#define FLUENT_CURSOR_HOT_X {xhot}\n#define FLUENT_CURSOR_HOT_Y {yhot}\n"
        "static const uint8_t fluent_cursor_pixels[] = {" + ",".join(map(str, pixels)) + "};\n"
        "static const lv_image_dsc_t fluent_cursor = { .header = {"
        ".magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_ARGB8888_PREMULTIPLIED, "
        f".flags = LV_IMAGE_FLAGS_PREMULTIPLIED, .w = {width}, .h = {height}, .stride = {width * 4}"
        "}, .data_size = sizeof(fluent_cursor_pixels), .data = fluent_cursor_pixels };\n")
    run("cmake", "-S", str(HERE), "-B", str(directory), "-DCMAKE_BUILD_TYPE=MinSizeRel",
        f"-DCMAKE_C_COMPILER={runtime.triplet}-gcc", f"-DCMAKE_CXX_COMPILER={runtime.triplet}-g++",
        f"-DLVGL_SOURCE={lvgl}", f"-DINPUT_ROOT={runtime.source}", f"-DTARGET_TRIPLET={runtime.triplet}")
    run("cmake", "--build", str(directory), "--parallel", str(min(os.cpu_count() or 1, 6)))
    run(f"{runtime.triplet}-strip", str(directory / "recovery-gui"))
    copy(directory / "recovery-gui", runtime.root / "usr/bin/recovery-gui")
    runtime.dependencies(runtime.root / "usr/bin/recovery-gui")
    for source, name in (("dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"), ("wqy/wqy-microhei.ttc", "wqy-microhei.ttc")):
        copy(runtime.resolve("/usr/share/fonts/truetype/" + source), runtime.root / "usr/share/fonts" / name)


def build():
    inputs = Path(os.environ["APKG_INPUTS_DIR"])
    stage = Path(os.environ["APKG_STAGE_DIR"])
    manifest = json.loads((inputs / "manifest.json").read_text())
    meta = next(i for i in manifest["Inputs"] if i["Id"] == "linux-image-generic")
    arch = meta["RequestedArchitecture"]
    triple = {"amd64": "x86_64-unknown-linux-gnu", "arm64": "aarch64-unknown-linux-gnu"}[arch]
    suite = meta["Suite"].removesuffix("-updates").removesuffix("-security")
    depends = output("dpkg-deb", "-f", str(inputs / meta["DebPath"]), "Depends")
    image = re.search(r"\b(linux-image-([0-9][\w.+-]*-generic))\b", depends)
    if image is None:
        raise RuntimeError("Ubuntu's generic kernel metapackage has no supported kernel")
    kernel = image[2]
    kernel_inputs = HERE / "obj" / f"kernel-{arch}"
    run("apkg", "fetch", "--package", image[1], "--package", f"linux-modules-{kernel}",
        "--repository", meta["Repository"], "--suite", suite, "--suite", suite + "-updates",
        "--suite", suite + "-security", "--component", "main", "--arch", arch,
        "--signed-by", "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
        "--extract", "--output", str(kernel_inputs))
    kernel_manifest = json.loads((kernel_inputs / "manifest.json").read_text())
    if len({i["Version"] for i in kernel_manifest["Inputs"]}) != 1:
        raise RuntimeError("Ubuntu kernel and modules versions differ; retry after publication")
    lvgl = HERE / "obj" / ("lvgl-" + LVGL_VERSION)
    if not lvgl.exists():
        run("git", "clone", "--depth=1", "--branch", LVGL_VERSION, "https://github.com/lvgl/lvgl.git", str(lvgl))
    if output("git", "-C", str(lvgl), "describe", "--tags", "--exact-match") != LVGL_VERSION:
        raise RuntimeError("Unexpected cached LVGL version")

    # Compile only the existing offline engine, never the desktop application.
    cargo_manifest = HERE.parent / "anduinos-btrfs-snapshots-manager/src/Cargo.toml"
    env = os.environ.copy()
    env["CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER"] = "x86_64-linux-gnu-gcc"
    env["CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER"] = "aarch64-linux-gnu-gcc"
    run("cargo", "build", "--manifest-path", str(cargo_manifest), "--locked", "--release",
        "--target", triple, "-p", "anduinos-recovery-engine",
        "--bin", "anduinos-btrfs-snapshots-manager-offline", env=env)
    engine = cargo_manifest.parent / "target" / triple / "release/anduinos-btrfs-snapshots-manager-offline"
    payload = stage / "usr/lib/anduinos-recovery-env"
    payload.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="recovery-build-") as temporary:
        work = Path(temporary)
        root, modules = work / "root", work / "modules"
        for name in ("bin", "dev", "proc", "sys", "run", "tmp", "root", "usr/bin", "usr/lib", "usr/lib64", "etc"):
            (root / name).mkdir(parents=True, exist_ok=True)
        (root / "lib").symlink_to("usr/lib")
        (root / "lib64").symlink_to("usr/lib64")
        runtime_source = work / "runtime"
        for item in manifest["Inputs"]:
            shutil.copytree(inputs / item["ExtractedPath"], runtime_source, symlinks=True, dirs_exist_ok=True)
        for name, target in (("lib", "usr/lib"), ("lib64", "usr/lib64")):
            if not (runtime_source / name).exists() and not (runtime_source / name).is_symlink():
                (runtime_source / name).symlink_to(target)
        runtime = Runtime(runtime_source, root, arch)
        extracted = inputs / "extracted"
        busybox = next((extracted / "busybox-static").glob("**/busybox"))
        copy(busybox, root / "bin/busybox")
        for name in "sh mount umount mkdir chmod cat sleep modprobe ls sync reboot poweroff clear awk grep tr readlink stat".split():
            (root / "bin" / name).symlink_to("busybox")
        for name in ("btrfs", "jq", "flock"):
            runtime.file("/usr/bin/" + name)
        copy(extracted / "util-linux/usr/sbin/blkid", root / "usr/bin/blkid")
        runtime.dependencies(root / "usr/bin/blkid")
        copy(engine, root / "usr/bin/recovery-offline")
        runtime.dependencies(root / "usr/bin/recovery-offline")
        build_gui(runtime, HERE / "obj" / ("gui-" + arch), lvgl)
        copy(HERE / "src/actions", root / "usr/bin/recovery-action")
        copy(HERE / "src/init", root / "init")
        (root / "init").chmod(0o755)

        for item in kernel_manifest["Inputs"]:
            source = kernel_inputs / item["ExtractedPath"]
            shutil.copytree(source, modules, symlinks=True, dirs_exist_ok=True)
        if not (modules / "lib").exists():
            (modules / "lib").symlink_to("usr/lib")
        run("depmod", "-b", str(modules), kernel)
        selected = set()
        available = []
        for driver in DRIVERS:
            result = subprocess.run(["modprobe", "--show-depends", "-d", str(modules),
                                     "-S", kernel, driver], capture_output=True, text=True)
            if result.returncode:
                if driver in ("btrfs", "ext4", "virtio_blk"):
                    raise RuntimeError(f"Required kernel driver is missing: {driver}")
                continue  # Some buses exist on only one architecture.
            available.append(driver)
            for line in result.stdout.splitlines():
                if line.startswith("insmod "):
                    selected.add(Path(line.split()[1]))
        for source in selected:
            relative = source.relative_to(modules).as_posix().removeprefix("usr/")
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == ".zst":
                with target.with_suffix("").open("wb") as stream:
                    run("zstd", "-q", "-d", "-c", str(source), stdout=stream)
            else:
                copy(source, target)
        for name in ("modules.builtin", "modules.builtin.modinfo", "modules.order"):
            copy(modules / "lib/modules" / kernel / name, root / "lib/modules" / kernel / name)
        run("depmod", "-b", str(root), kernel)
        (root / "etc/recovery-drivers").write_text("\n".join(available) + "\n")
        # Fail construction, rather than boot, if an extracted library is missing.
        libraries = {p.name for p in (root / "usr/lib").rglob("*") if p.is_file() or p.is_symlink()}
        for binary in [p for p in (root / "usr/bin").iterdir() if p.is_file()] + [p for p in (root / "usr/lib").rglob("*.so*") if p.is_file() and not p.is_symlink()]:
            result = subprocess.run(["readelf", "-d", str(binary)], capture_output=True, text=True)
            for needed in re.findall(r"Shared library: \[(.*?)\]", result.stdout):
                if needed not in libraries:
                    raise RuntimeError(f"Missing recovery runtime library: {needed}")
        names = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        with (work / "initrd.cpio").open("wb") as stream:
            run("cpio", "--create", "--null", "--format=newc", "--owner=0:0", "--reproducible",
                cwd=root, input=("\0".join(names) + "\0").encode(), stdout=stream)
        with (work / "initrd.cpio").open("rb") as source, (payload / "initrd.img").open("wb") as destination:
            with gzip.GzipFile(filename="", fileobj=destination, mode="wb", mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
        copy(modules / "boot" / f"vmlinuz-{kernel}", payload / "vmlinuz")
    hashes = {name: hashlib.sha256((payload / name).read_bytes()).hexdigest() for name in ("vmlinuz", "initrd.img")}
    if sum((payload / name).stat().st_size for name in hashes) > 100 * 1024 * 1024:
        raise RuntimeError("Recovery payload exceeds the 100 MiB budget for a 256 MiB volume")
    (payload / "manifest.json").write_text(json.dumps({"schema": 1, "architecture": arch,
        "kernel": kernel, "files": hashes}, indent=2) + "\n")
    doc = stage / "usr/share/doc/anduinos-recovery-env"
    doc.mkdir(parents=True, exist_ok=True)
    (doc / "inputs.json").write_text(json.dumps([manifest, kernel_manifest], indent=2) + "\n")
    (doc / "lvgl-source.txt").write_text("https://github.com/lvgl/lvgl.git\n" + LVGL_VERSION + "\n" +
        output("git", "-C", str(lvgl), "rev-parse", "HEAD") + "\n")
    copy(lvgl / "LICENCE.txt", doc / "licenses/LVGL.MIT")
    copy(HERE / "data/cursor.COPYING", doc / "licenses/Fluent.GPL-3.0")
    for directory in (inputs, kernel_inputs):
        for source in (directory / "extracted").glob("*/usr/share/doc/*/copyright"):
            copy(source, doc / "licenses" / (source.parent.name + ".copyright"))


if __name__ == "__main__":
    build()
