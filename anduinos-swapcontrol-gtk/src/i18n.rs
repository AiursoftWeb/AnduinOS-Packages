use gettextrs::{gettext, TextDomain};

use crate::config;

/// Initialize gettext for i18n support.
/// Does not panic if translations are missing — falls back to English.
pub fn init() {
    TextDomain::new(config::GETTEXT_PACKAGE)
        .codeset("UTF-8")
        .init()
        .ok(); // Don't panic if locale files are not installed yet
}

/// Translate a string using gettext.
#[allow(dead_code)]
pub fn i18n(s: &str) -> String {
    gettext(s)
}

/// Translate a format template, then replace numbered placeholders
/// `{0}`, `{1}`, … with the corresponding values.
/// Values are applied left-to-right: `{0}` first, then `{1}`, etc.
///
/// Usage: `i18n_fmt("Hello {0}, you have {1} messages", &[name, &n.to_string()])`
pub fn i18n_fmt(template: &str, values: &[&dyn AsRef<str>]) -> String {
    let mut s = i18n(template);
    for (i, v) in values.iter().enumerate() {
        s = s.replace(&format!("{{{}}}", i), v.as_ref());
    }
    s
}
