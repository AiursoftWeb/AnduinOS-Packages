//! Bounded parsing of the dpkg state captured in a system recovery point.

use anyhow::{Context, Result};
use std::fs::OpenOptions;
use std::io::Read;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;
use waypoint_common::Package;

pub fn get_packages_from_status(path: &Path) -> Result<Vec<Package>> {
    const MAX_STATUS_BYTES: u64 = 64 * 1024 * 1024;
    let metadata = std::fs::symlink_metadata(path)
        .with_context(|| format!("Failed to inspect {}", path.display()))?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_STATUS_BYTES {
        anyhow::bail!("The captured dpkg status is not a bounded regular file");
    }
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)
        .with_context(|| format!("Failed to open {}", path.display()))?;
    let mut contents = String::new();
    file.take(MAX_STATUS_BYTES + 1)
        .read_to_string(&mut contents)
        .context("Failed to read the captured dpkg status")?;
    if contents.len() as u64 > MAX_STATUS_BYTES {
        anyhow::bail!("The captured dpkg status exceeds the safety limit");
    }
    parse_dpkg_status(&contents)
}

fn parse_dpkg_status(contents: &str) -> Result<Vec<Package>> {
    let mut packages = Vec::new();
    for paragraph in contents.split("\n\n") {
        let mut name = None;
        let mut version = None;
        let mut architecture = None;
        let mut multi_arch_same = false;
        let mut installed = false;
        for line in paragraph.lines() {
            if let Some(value) = line.strip_prefix("Package: ") {
                name = Some(value);
            } else if let Some(value) = line.strip_prefix("Version: ") {
                version = Some(value);
            } else if let Some(value) = line.strip_prefix("Architecture: ") {
                architecture = Some(value);
            } else if line == "Multi-Arch: same" {
                multi_arch_same = true;
            } else if line == "Status: install ok installed" {
                installed = true;
            }
        }
        if !installed {
            continue;
        }
        let (Some(name), Some(version)) = (name, version) else {
            anyhow::bail!("An installed dpkg status paragraph is incomplete");
        };
        let name = if multi_arch_same {
            format!(
                "{name}:{}",
                architecture.context("Multi-Arch package has no architecture")?
            )
        } else {
            name.to_string()
        };
        packages.push(Package {
            name,
            version: version.to_string(),
        });
    }
    packages.sort_by(|left, right| left.name.cmp(&right.name));
    Ok(packages)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_only_installed_packages_from_captured_status() {
        let packages = parse_dpkg_status(
            "Package: libc6\nStatus: install ok installed\nArchitecture: amd64\nMulti-Arch: same\nVersion: 2.41-12ubuntu1\n\nPackage: removed\nStatus: deinstall ok config-files\nArchitecture: all\nVersion: 1\n\nPackage: bash\nStatus: install ok installed\nArchitecture: amd64\nVersion: 5.2\n",
        )
        .unwrap();
        assert_eq!(packages[0].name, "bash");
        assert_eq!(packages[1].name, "libc6:amd64");
        assert_eq!(packages.len(), 2);
    }

    #[test]
    fn rejects_incomplete_installed_paragraphs() {
        assert!(parse_dpkg_status("Package: broken\nStatus: install ok installed\n").is_err());
    }
}
