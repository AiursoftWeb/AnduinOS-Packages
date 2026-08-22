use std::path::{Path, PathBuf};

pub const APP_ID: &str = "com.anduinos.DriverCenter";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const GETTEXT_PACKAGE: &str = "anduinos-driver-center";
pub const HELPER: &str = "/usr/libexec/anduinos-driver-center/driver-helper";
pub const SECUREBOOT_HELPER: &str = "/usr/libexec/anduinos-secureboot-helper";
pub const ILLUSTRATIONS: &str = "/usr/share/anduinos-driver-center/illustrations";
pub const SECUREBOOTCTL: &str = "anduinos-securebootctl";

pub fn illustration(name: &str) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    candidates.push(Path::new(ILLUSTRATIONS).join(name));
    if let Some(home) = std::env::var_os("HOME") {
        candidates.push(
            PathBuf::from(home)
                .join(".local/share/anduinos-driver-center/illustrations")
                .join(name),
        );
    }
    candidates.push(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("resources")
            .join(name),
    );
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(
                dir.join("../share/anduinos-driver-center/illustrations")
                    .join(name),
            );
        }
    }
    candidates.into_iter().find(|path| path.is_file())
}

pub fn locale_dir() -> Option<PathBuf> {
    if let Some(home) = std::env::var_os("HOME") {
        let local = PathBuf::from(home).join(".local/share/locale");
        if local.is_dir() {
            return Some(local);
        }
    }
    None
}
