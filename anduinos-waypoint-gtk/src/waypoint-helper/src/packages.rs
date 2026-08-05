// Package management for waypoint-helper

use anyhow::{Context, Result};
use std::fs::OpenOptions;
use std::io::Read;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;
use std::process::Command;
use waypoint_common::Package;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, Eq, PartialEq)]
pub struct PackageUpdate {
    pub name: String,
    pub old_version: String,
    pub new_version: String,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, Eq, PartialEq)]
pub struct PackageDiff {
    pub added: Vec<Package>,
    pub removed: Vec<Package>,
    pub updated: Vec<PackageUpdate>,
}

/// Get the installed dpkg package state in a locale-independent format.
pub fn get_installed_packages() -> Result<Vec<Package>> {
    let output = Command::new("dpkg-query")
        .args(["-W", "-f=${binary:Package}\\t${Version}\\n"])
        .output()
        .context("Failed to execute dpkg-query")?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        anyhow::bail!("dpkg-query failed: {stderr}");
    }

    parse_dpkg_query(&String::from_utf8_lossy(&output.stdout))
}

fn parse_dpkg_query(output: &str) -> Result<Vec<Package>> {
    let mut packages = Vec::new();
    for (line_number, line) in output.lines().enumerate() {
        if line.is_empty() {
            continue;
        }
        let (name, version) = line.split_once('\t').ok_or_else(|| {
            anyhow::anyhow!("Malformed dpkg-query record on line {}", line_number + 1)
        })?;
        if name.is_empty() || version.is_empty() {
            anyhow::bail!("Incomplete dpkg-query record on line {}", line_number + 1);
        }
        packages.push(Package {
            name: name.to_string(),
            version: version.to_string(),
        });
    }
    packages.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(packages)
}

/// Read the installed-package view captured inside an immutable deployment.
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

pub fn compare_status_files(old_path: &Path, new_path: &Path) -> Result<PackageDiff> {
    let old_packages = get_packages_from_status(old_path)?;
    let new_packages = get_packages_from_status(new_path)?;
    Ok(diff_packages(&old_packages, &new_packages))
}

fn diff_packages(old_packages: &[Package], new_packages: &[Package]) -> PackageDiff {
    let old = old_packages
        .iter()
        .map(|package| (package.name.as_str(), package.version.as_str()))
        .collect::<std::collections::BTreeMap<_, _>>();
    let new = new_packages
        .iter()
        .map(|package| (package.name.as_str(), package.version.as_str()))
        .collect::<std::collections::BTreeMap<_, _>>();

    let added = new_packages
        .iter()
        .filter(|package| !old.contains_key(package.name.as_str()))
        .cloned()
        .collect();
    let removed = old_packages
        .iter()
        .filter(|package| !new.contains_key(package.name.as_str()))
        .cloned()
        .collect();
    let updated = new_packages
        .iter()
        .filter_map(|package| {
            let old_version = old.get(package.name.as_str())?;
            (*old_version != package.version).then(|| PackageUpdate {
                name: package.name.clone(),
                old_version: (*old_version).to_string(),
                new_version: package.version.clone(),
            })
        })
        .collect();

    PackageDiff {
        added,
        removed,
        updated,
    }
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
    fn parses_binary_package_names_and_debian_versions() {
        let packages = parse_dpkg_query(
            "libc6:amd64\t2.41-12ubuntu1\nlinux-image-generic\t7.0.0.28.28~26.04.1\n",
        )
        .expect("valid dpkg-query output");

        assert_eq!(packages.len(), 2);
        assert_eq!(packages[0].name, "libc6:amd64");
        assert_eq!(packages[0].version, "2.41-12ubuntu1");
        assert_eq!(packages[1].name, "linux-image-generic");
    }

    #[test]
    fn compares_captured_package_states_without_localized_output() {
        let old = vec![
            Package {
                name: "linux-image-generic".into(),
                version: "7.0.0.27".into(),
            },
            Package {
                name: "removed".into(),
                version: "1".into(),
            },
        ];
        let new = vec![
            Package {
                name: "added".into(),
                version: "1".into(),
            },
            Package {
                name: "linux-image-generic".into(),
                version: "7.0.0.28".into(),
            },
        ];
        let diff = diff_packages(&old, &new);
        assert_eq!(diff.added[0].name, "added");
        assert_eq!(diff.removed[0].name, "removed");
        assert_eq!(
            diff.updated,
            vec![PackageUpdate {
                name: "linux-image-generic".into(),
                old_version: "7.0.0.27".into(),
                new_version: "7.0.0.28".into(),
            }]
        );
    }

    #[test]
    fn compares_two_bounded_dpkg_status_files() {
        struct Cleanup(std::path::PathBuf);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }

        let root = std::env::temp_dir().join(format!(
            "waypoint-package-comparison-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&root).unwrap();
        let _cleanup = Cleanup(root.clone());
        let old = root.join("old.status");
        let new = root.join("new.status");
        std::fs::write(
            &old,
            "Package: kernel\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1\n\n",
        )
        .unwrap();
        std::fs::write(
            &new,
            "Package: kernel\nStatus: install ok installed\nArchitecture: amd64\nVersion: 2\n\nPackage: editor\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1\n\n",
        )
        .unwrap();

        let diff = compare_status_files(&old, &new).unwrap();
        assert_eq!(diff.added[0].name, "editor");
        assert!(diff.removed.is_empty());
        assert_eq!(diff.updated[0].old_version, "1");
        assert_eq!(diff.updated[0].new_version, "2");
    }

    #[test]
    fn rejects_malformed_dpkg_records() {
        assert!(parse_dpkg_query("missing-tab\n").is_err());
        assert!(parse_dpkg_query("package\t\n").is_err());
    }

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
}
