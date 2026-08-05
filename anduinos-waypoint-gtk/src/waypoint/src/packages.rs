use serde::Deserialize;

pub use waypoint_common::Package;

/// Trusted package-state difference returned by the privileged helper after it
/// verifies both immutable deployment identities and reads their bounded dpkg
/// status databases.
#[derive(Debug, Clone, Deserialize)]
pub struct PackageDiff {
    pub added: Vec<Package>,
    pub removed: Vec<Package>,
    pub updated: Vec<PackageUpdate>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PackageUpdate {
    pub name: String,
    pub old_version: String,
    pub new_version: String,
}
