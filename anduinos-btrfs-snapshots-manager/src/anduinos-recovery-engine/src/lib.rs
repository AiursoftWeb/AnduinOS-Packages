//! Trusted recovery core for Disk Snapshots Manager.
//!
//! This crate deliberately has no GTK, D-Bus, Polkit, package-manager, or
//! scheduler dependency. It owns only the versioned deployment records,
//! durable rollback transaction, and early-boot Btrfs replacement state
//! machine. The privileged helper must validate platform policy before it
//! asks this crate to schedule or execute a recovery.

pub mod apt_policy;
pub mod boot;
pub mod browse_lock;
pub mod cleanup;
pub mod confirmation;
pub mod coordination;
pub mod layout;
pub mod lineage;
pub mod model;
pub mod operations;
pub mod package_hook;
pub mod package_transaction;
pub mod personal;
pub mod recovery;
pub mod rollback;
pub mod secure_boot;
pub mod space;
pub mod store;
pub mod system_browser;
pub mod transaction;

pub use apt_policy::AptSnapshotPolicy;

pub const DEPLOYMENT_SCHEMA_VERSION: u32 = 1;
pub const PERSONAL_SNAPSHOT_SCHEMA_VERSION: u32 = 1;
pub const RECOVERY_STORE_ROOT: &str = "/.snapshots/anduinos-btrfs-snapshots-manager";
