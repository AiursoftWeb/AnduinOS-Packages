use std::io;
use std::process::{Child, Command};

pub const APP_ID: &str = "com.yubico.yubioath";
pub const FLATPAK_REF_URI: &str =
    "flatpak+https://dl.flathub.org/repo/appstream/com.yubico.yubioath.flatpakref";

pub fn is_installed() -> bool {
    is_installed_with(|program, arguments| {
        Command::new(program)
            .args(arguments)
            .status()
            .map(|status| status.success())
    })
}

fn is_installed_with<F>(run: F) -> bool
where
    F: FnOnce(&str, &[&str]) -> io::Result<bool>,
{
    run("flatpak", &["info", APP_ID]).unwrap_or(false)
}

pub fn launch() -> io::Result<Child> {
    Command::new("flatpak").args(["run", APP_ID]).spawn()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::ErrorKind;

    #[test]
    fn installed_when_flatpak_info_succeeds() {
        let installed = is_installed_with(|program, arguments| {
            assert_eq!(program, "flatpak");
            assert_eq!(arguments, ["info", "com.yubico.yubioath"]);
            Ok(true)
        });

        assert!(installed);
    }

    #[test]
    fn not_installed_when_flatpak_info_fails() {
        let installed = is_installed_with(|program, arguments| {
            assert_eq!(program, "flatpak");
            assert_eq!(arguments, ["info", "com.yubico.yubioath"]);
            Ok(false)
        });

        assert!(!installed);
    }

    #[test]
    fn not_installed_when_flatpak_is_unavailable() {
        let installed = is_installed_with(|program, arguments| {
            assert_eq!(program, "flatpak");
            assert_eq!(arguments, ["info", "com.yubico.yubioath"]);
            Err(io::Error::new(ErrorKind::NotFound, "flatpak not found"))
        });

        assert!(!installed);
    }
}
