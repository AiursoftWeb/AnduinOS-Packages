use gettextrs::{TextDomain, gettext};

pub const DOMAIN: &str = "anduinos-waypoint-gtk";

/// Initialize the system gettext catalog. Missing or incomplete catalogs
/// intentionally fall back to the English message identifier.
pub fn init() {
    if let Err(error) = TextDomain::new(DOMAIN).codeset("UTF-8").init() {
        log::warn!("Could not initialize the {DOMAIN} translation domain: {error}");
    }
}

pub fn tr(message: &str) -> String {
    gettext(message)
}

/// Translate a template before substituting numbered placeholders. This lets
/// translators reorder runtime values without translating those values.
pub fn trf(template: &str, values: &[&str]) -> String {
    let mut translated = tr(template);
    for (index, value) in values.iter().enumerate() {
        translated = translated.replace(&format!("{{{index}}}"), value);
    }
    translated
}
