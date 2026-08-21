# Pinned development resources

| Component | Version | Source | SHA-256 / license |
|---|---:|---|---|
| Carapace amd64 | 1.7.3 | `https://github.com/carapace-sh/carapace-bin/releases/download/v1.7.3/carapace-bin_1.7.3_linux_amd64.tar.gz` | `35ab52bfe7bdd8296d90c3687660bde80497599badde840ab615d2f421f5f053`, MIT |
| Carapace arm64 | 1.7.3 | `https://github.com/carapace-sh/carapace-bin/releases/download/v1.7.3/carapace-bin_1.7.3_linux_arm64.tar.gz` | `b2456cb09d77004db87de2567d6d7588a61ceb4724522c463e2b1c1f87b4d4b9`, MIT |

The release archives include their upstream license files. `download.sh` checks
the archive hashes and expected license/layout before staging the development
tool used by `update-command-specs.sh`. Carapace is not included in the package.

The grammar-update workflow consumes Carapace as its official statically linked
per-architecture release binary. It exports compiled JSON command trees, then
probes positional completion in a temporary directory containing only synthetic
filenames. Bridge completers are excluded so host programs and entities cannot
affect the checked-in corpus. This avoids adding a Go toolchain and a large
module dependency graph while keeping the exact development artifact pinned and
independently verifiable.

## Design references

No code or runtime component from the following projects is bundled. Their
documented ranking ideas informed the local implementation:

- `zsh-autosuggestions` (`match_prev_cmd` strategy):
  `https://github.com/zsh-users/zsh-autosuggestions`
- McFly (cwd, previous-command, frequency, recency and exit-status features):
  `https://github.com/cantino/mcfly`
- Atuin (directory/session filtering and frecency ranking):
  `https://github.com/atuinsh/atuin`
- Fig autocomplete (compact declarative command specifications):
  `https://github.com/withfig/autocomplete`
