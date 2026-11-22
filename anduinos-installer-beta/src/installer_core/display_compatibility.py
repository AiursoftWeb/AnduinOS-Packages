#!/usr/bin/python3
"""Generate the installed system's optional Intel display boot entry."""

from pathlib import Path
import re
import subprocess


GRUB_SCRIPT = Path("etc/grub.d/11_anduinos_display")
ENTRY_ID = "anduinos-display-compatibility"
PSR_ARGUMENTS = "i915.enable_psr=0 xe.enable_psr=0"


def intel_integrated_graphics_visible(
    pci_devices: Path = Path("/sys/bus/pci/devices"),
) -> bool:
    # CPU-integrated Intel graphics use device 2, function 0 on the root bus.
    # Query PCI identity directly so this also works in Live Safe Graphics.
    for device in pci_devices.glob("????:00:02.0"):
        try:
            vendor = (device / "vendor").read_text().strip()
            device_class = (device / "class").read_text().strip()
        except FileNotFoundError:
            continue
        if vendor == "0x8086" and device_class.startswith("0x03"):
            return True
    return False


def display_compatibility_entry(linux_entries: str) -> str:
    """Copy the first top-level Linux entry produced by the standard generator."""
    lines = iter(linux_entries.splitlines())
    for line in lines:
        if line.startswith("menuentry ") and line.endswith(" {"):
            break
    else:
        return ""

    body = []
    has_kernel = False
    for line in lines:
        if line == "}":
            break
        command = line.lstrip()
        if command == "savedefault":
            continue
        if re.match(r"linux(?:efi|16)?[ \t]+", command):
            has_kernel = True
            line = re.sub(r"[ \t]+(?:i915|xe)\.enable_psr=[^ \t]+", "", line)
            line = f"{line.rstrip()} {PSR_ARGUMENTS}"
        body.append(line)
    else:
        return ""
    if not has_kernel:
        return ""

    return "\n".join((
        "set timeout_style=menu",
        'if [ "${timeout}" -ge 0 ] && [ "${timeout}" -lt 5 ]; then',
        "  set timeout=5",
        "fi",
        "menuentry 'AnduinOS (Display Compatibility)' --class anduinos "
        f"--class gnu-linux --class gnu --class os $menuentry_id_option '{ENTRY_ID}' {{",
        *body,
        "}",
        "",
    ))


if __name__ == "__main__":
    generated = subprocess.run(
        ["/etc/grub.d/10_linux"], check=True, text=True, capture_output=True
    )
    print(display_compatibility_entry(generated.stdout), end="")
