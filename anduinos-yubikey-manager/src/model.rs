use serde::Deserialize;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct YubiKey {
    pub name: String,
    pub serial: String,
    pub firmware: String,
    pub interfaces: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
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

#[derive(Clone, Debug, Default, Deserialize)]
pub struct EnrollmentFile {
    #[serde(default)]
    pub enrollments: Vec<Enrollment>,
    #[serde(default)]
    pub passwordless_sudo_users: Vec<String>,
}

impl EnrollmentFile {
    pub fn passwordless_sudo_for(&self, username: &str) -> bool {
        self.passwordless_sudo_users
            .iter()
            .any(|item| item == username)
    }
}
