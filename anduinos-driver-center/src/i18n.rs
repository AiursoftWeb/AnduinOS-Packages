use gettextrs::{gettext, ngettext, TextDomain};

use crate::config;

pub fn init() {
    let mut domain = TextDomain::new(config::GETTEXT_PACKAGE).codeset("UTF-8");
    if let Some(dir) = config::locale_dir() {
        domain = domain.prepend(dir);
    }
    domain.init().ok();
}

pub fn i18n(s: &str) -> String {
    gettext(s)
}

pub fn ni18n(singular: &str, plural: &str, n: u32) -> String {
    ngettext(singular, plural, n)
}
