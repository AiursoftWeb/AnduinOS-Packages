use serde::Deserialize;

#[derive(Clone, Debug)]
pub struct YubiKey {
    pub name: String,
    pub serial: String,
    pub firmware: String,
    pub interfaces: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Enrollment {
    pub username: String,
    pub serial: String,
    #[serde(default = "default_purpose")]
    pub purpose: String,
    #[allow(dead_code)]
    pub credential: String,
}

fn default_purpose() -> String {
    "gdm".into()
}

#[derive(Default, Deserialize)]
pub struct EnrollmentFile {
    #[serde(default)]
    pub enrollments: Vec<Enrollment>,
}
