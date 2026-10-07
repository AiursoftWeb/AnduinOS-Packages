use gettextrs::{gettext, TextDomain};

use crate::config;

/// Initialize gettext. Missing catalogs fall back to the English message IDs.
pub fn init() {
    if let Err(error) = TextDomain::new(config::GETTEXT_PACKAGE)
        .codeset("UTF-8")
        .init()
    {
        eprintln!("Could not initialize firewall translations: {error}; using English.");
    }
}

/// Translate a string using gettext.
#[allow(dead_code)]
pub fn i18n(s: &str) -> String {
    gettext(s)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    #[test]
    fn missing_catalogs_fall_back_to_english() {
        const CHILD: &str = "UFWALL_I18N_TEST_CHILD";
        if std::env::var_os(CHILD).is_some() {
            init();
            assert_eq!(i18n("Firewall"), "Firewall");
            return;
        }

        // Locale and gettext state are process-wide. Use separate processes so
        // these cases cannot change the locale of concurrently running tests.
        for (locale, language) in [
            ("hr_HR.UTF-8", "hr"),
            ("xx_XX.UTF-8", "xx"),
            ("en_US.UTF-8", "en"),
            ("C", "C"),
            ("C.UTF-8", "C"),
        ] {
            let result = Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "i18n::tests::missing_catalogs_fall_back_to_english",
                    "--nocapture",
                ])
                .env(CHILD, "1")
                .env("LANG", locale)
                .env("LANGUAGE", language)
                .env_remove("LC_ALL")
                .env_remove("LC_MESSAGES")
                // An empty search directory also covers damaged installations
                // where even a normally supported language has no catalog.
                .env(
                    "XDG_DATA_DIRS",
                    std::env::temp_dir()
                        .join(format!("ufwall-missing-catalogs-{}", std::process::id())),
                )
                .output()
                .unwrap();
            assert!(
                result.status.success(),
                "Locale {locale} failed:\n{}\n{}",
                String::from_utf8_lossy(&result.stdout),
                String::from_utf8_lossy(&result.stderr),
            );
        }
    }
}
