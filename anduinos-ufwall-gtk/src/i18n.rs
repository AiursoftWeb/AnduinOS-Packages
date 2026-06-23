use gettextrs::{gettext, TextDomain};

use crate::config;

/// Initialize gettext for i18n support.
pub fn init() {
    TextDomain::new(config::GETTEXT_PACKAGE)
        .codeset("UTF-8")
        .init()
        .expect("Unable to initialize gettext locale");
}

/// Translate a string using gettext.
#[allow(dead_code)]
pub fn i18n(s: &str) -> String {
    gettext(s)
}
