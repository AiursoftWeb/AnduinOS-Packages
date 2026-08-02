# Bundled upstream resources

| Component | Version | Source | SHA-256 / license |
|---|---:|---|---|
| ble.sh | 0.4.0-devel3-2 | `https://github.com/akinomyoga/ble.sh/releases/download/v0.4.0-devel3/ble-0.4.0-devel3-2.tar.xz` | `bdcdcfff216495403adf82a701fe41675f64b64644dc77caabd3e015871ebd61`, BSD-3-Clause |
| Carapace amd64 | 1.7.3 | `https://github.com/carapace-sh/carapace-bin/releases/download/v1.7.3/carapace-bin_1.7.3_linux_amd64.tar.gz` | `35ab52bfe7bdd8296d90c3687660bde80497599badde840ab615d2f421f5f053`, MIT |
| Carapace arm64 | 1.7.3 | `https://github.com/carapace-sh/carapace-bin/releases/download/v1.7.3/carapace-bin_1.7.3_linux_arm64.tar.gz` | `b2456cb09d77004db87de2567d6d7588a61ceb4724522c463e2b1c1f87b4d4b9`, MIT |

The release archives include their upstream license files. `download.sh` checks
the archive hashes and expected license/layout before staging package content.
No upstream files are downloaded during package installation or at runtime.

Carapace is consumed as its official statically linked per-architecture release
binary. This avoids adding a Go toolchain and a large module dependency graph to
this thin integration package's build while keeping the exact artifact pinned
and independently verifiable.
