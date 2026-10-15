"""Boot architecture parameters shared by package builds and installed tools."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BootArchitecture:
    deb_arch: str
    grub_platform: str
    efi_suffix: str
    pe_machine: int
    bios_platform: str = ""

    @property
    def grub_name(self):
        return f"grub{self.efi_suffix}.efi"

    @property
    def shim_name(self):
        return f"shim{self.efi_suffix}.efi"

    @property
    def mm_name(self):
        return f"mm{self.efi_suffix}.efi"

    @property
    def fallback_name(self):
        return f"BOOT{self.efi_suffix.upper()}.EFI"

    @property
    def source(self):
        return Path("/usr/lib/anduinos-grub") / self.grub_name

    @property
    def signed_grub(self):
        return Path(f"/usr/lib/grub/{self.grub_platform}-signed/{self.grub_name}.signed")

    @property
    def shim(self):
        return Path(f"/usr/lib/shim/{self.shim_name}.signed.latest")

    @property
    def mokmanager(self):
        return Path("/usr/lib/shim") / self.mm_name

    @property
    def old_packages(self):
        efi = f"grub-efi-{self.deb_arch}"
        packages = {"grub-common", "grub2-common", "shim", "shim-signed",
                    efi, f"{efi}-bin", f"{efi}-unsigned", f"{efi}-signed"}
        if self.bios_platform:
            packages.update(("grub-pc", "grub-pc-bin"))
        return packages


ARCHITECTURES = {
    "amd64": BootArchitecture("amd64", "x86_64-efi", "x64", 0x8664, "i386-pc"),
    "arm64": BootArchitecture("arm64", "arm64-efi", "aa64", 0xAA64),
}


def architecture(name):
    try:
        return ARCHITECTURES[name]
    except KeyError:
        raise ValueError(f"Unsupported boot architecture: {name!r}") from None


def native_architecture(runner):
    result = runner.run(["dpkg", "--print-architecture"])
    if result.returncode:
        raise ValueError("Cannot determine the installed boot architecture")
    return architecture(result.stdout.strip())


def machine_architecture(machine):
    for entry in ARCHITECTURES.values():
        if entry.pe_machine == machine:
            return entry
    raise ValueError(f"Unsupported EFI machine: {machine:#x}")


if __name__ == "__main__":
    import shlex
    import sys

    if len(sys.argv) != 3 or sys.argv[1] != "shell":
        sys.exit("Usage: architecture.py shell <Debian architecture>")
    selected = architecture(sys.argv[2])
    for key in ("deb_arch", "grub_platform", "efi_suffix", "bios_platform"):
        print(f"BOOT_{key.upper()}={shlex.quote(getattr(selected, key))}")
