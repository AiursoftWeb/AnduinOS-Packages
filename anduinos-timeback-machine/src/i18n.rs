use gettextrs::{gettext, TextDomain};

use crate::config;

pub fn init() {
    TextDomain::new(config::GETTEXT_PACKAGE)
        .codeset("UTF-8")
        .init()
        .ok();
}

pub fn i18n(message: &str) -> String {
    gettext(message)
}

pub fn i18n_fmt(template: &str, values: &[&str]) -> String {
    let mut translated = i18n(template);
    for (index, value) in values.iter().enumerate() {
        translated = translated.replace(&format!("{{{index}}}"), value);
    }
    translated
}
