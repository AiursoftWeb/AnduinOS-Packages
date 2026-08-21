use gettextrs::{gettext, TextDomain};

use crate::config;

/// Initialize gettext. Missing catalogs safely fall back to the English msgid.
pub fn init() {
    TextDomain::new(config::GETTEXT_PACKAGE)
        .codeset("UTF-8")
        .init()
        .ok();
}

/// Translate one static UI string.
pub fn i18n(message: &str) -> String {
    gettext(message)
}

/// Translate a template first, then substitute numbered placeholders.
///
/// Translators may reorder `{0}`, `{1}`, and later placeholders to match the
/// grammar of their language. Runtime values are never sent for translation.
pub fn i18n_fmt(template: &str, values: &[&str]) -> String {
    let mut translated = i18n(template);
    for (index, value) in values.iter().enumerate() {
        translated = translated.replace(&format!("{{{index}}}"), value);
    }
    translated
}
