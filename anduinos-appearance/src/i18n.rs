use gettextrs::{gettext, TextDomain};

use crate::config;

pub fn init() {
    TextDomain::new(config::GETTEXT_PACKAGE)
        .codeset("UTF-8")
        .init()
        .ok();
}

pub fn i18n(s: &str) -> String {
    gettext(s)
}

pub fn i18n_replace(msgid: &str, pairs: &[(&str, &str)]) -> String {
    let mut translated = i18n(msgid);
    for (key, value) in pairs {
        translated = translated.replace(&format!("{{{key}}}"), value);
    }
    translated
}
