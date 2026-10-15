# AnduinOS GRUB

Apkg selects the newest `grub2-common` in the configured target Ubuntu suite and
architecture. Its version and runtime dependencies are inherited, while its
binary payload is replaced by our build of the corresponding Ubuntu source.
AMD64 includes BIOS and EFI modules; ARM64 includes EFI modules. Both use the
same build and local signing/deployment logic.

Run `apkg build --all`. Docker with Buildx and support for the target architecture
is required. To add a suite, update `TargetSuites`, `UpstreamSuiteMapping` and
`SuiteShortNameMap`, as with the other Ubuntu-derived packages. Build scripts
use the target suite with its `-addon` repository suffix removed.

The container uses `ubuntu:<suite>` and obtains source through authenticated APT
indexes. The source version is read from the selected upstream package's Debian
changelog; no artifact URL, version or SHA256 is fixed in the build recipe.
Each build refreshes the upstream stage. Docker can reuse compilation when the
source and recipe are unchanged. Source hashes and build package versions are
recorded in the output for traceability, rather than maintained as input locks.

The EFI image embeds Btrfs, LUKS2 and Argon2id. SBAT component generations come
from the selected source; the distribution identity is `grub.anduinos`. Shim
verification stays enabled. The package ships no private key: the Secure Boot
toolkit signs its pristine EFI image using the machine's unified certificate.

Package relationships use unversioned compatibility names, like other renamed
packages in this repository. Ubuntu's boot-stack packages are replaced together;
`anduinos-shim-signed` depends on our GRUB package directly. External packages
requiring an exact Ubuntu boot-stack version are not interchangeable with this
stack and must be checked when qualifying an installation.

Disk encryption installation is a later phase; the embedded reader support
does not itself encrypt a disk. ARM64 installation qualification is in progress.
