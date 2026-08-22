use serde::Deserialize;

use crate::config;

#[derive(Clone, Debug, Default)]
pub struct SecureBootState {
    pub enabled: bool,
    pub key_present: bool,
    pub certificate_present: bool,
    pub enrolled: bool,
    pub enrollment_pending: bool,
    pub configuration_present: bool,
    pub dkms_available: bool,
    pub state_known: bool,
    pub enforcement_inactive: bool,
    pub ready: bool,
    pub trust_ready: bool,
    pub enrollment_required: bool,
    pub status: String,
}

#[derive(Clone, Debug, Default)]
pub struct DkmsState {
    pub modules: Vec<String>,
    pub untrusted_modules: Vec<String>,
}

impl DkmsState {
    pub fn ready(&self) -> bool {
        self.untrusted_modules.is_empty()
    }
}

#[derive(Clone, Debug, Default)]
pub struct TrustSnapshot {
    pub state: SecureBootState,
    pub dkms: DkmsState,
}

#[derive(Deserialize)]
struct Wire {
    secure_boot: WireSecureBoot,
    #[serde(default)]
    dkms: WireDkms,
}

#[derive(Deserialize)]
struct WireSecureBoot {
    #[serde(default)]
    enabled: bool,
    #[serde(default)]
    key_present: bool,
    #[serde(default)]
    certificate_present: bool,
    #[serde(default)]
    enrolled: bool,
    #[serde(default)]
    enrollment_pending: bool,
    #[serde(default)]
    dkms_available: bool,
    #[serde(default)]
    configuration_present: bool,
    #[serde(default)]
    status: Option<String>,
}

#[derive(Deserialize, Default)]
struct WireDkms {
    #[serde(default)]
    modules: Vec<String>,
    #[serde(default)]
    untrusted_modules: Vec<String>,
}

pub fn inspect() -> TrustSnapshot {
    let output = std::process::Command::new(config::SECUREBOOTCTL)
        .args(["status", "--json"])
        .output();
    let Ok(output) = output else {
        return unknown();
    };
    if !output.status.success() {
        return unknown();
    }
    parse_status(&output.stdout).unwrap_or_else(unknown)
}

pub fn parse_status(bytes: &[u8]) -> Option<TrustSnapshot> {
    let wire = serde_json::from_slice::<Wire>(bytes).ok()?;
    let status = wire.secure_boot.status.unwrap_or_else(|| {
        if wire.secure_boot.enabled {
            "enabled".into()
        } else {
            "disabled".into()
        }
    });
    let enforcement_inactive = matches!(status.as_str(), "disabled" | "unsupported");
    let state_known = status != "unknown";
    let trust_ready = enforcement_inactive
        || (status == "enabled"
            && wire.secure_boot.key_present
            && wire.secure_boot.certificate_present
            && wire.secure_boot.enrolled);
    let ready = trust_ready && (enforcement_inactive || wire.secure_boot.configuration_present);
    let enrollment_required =
        wire.secure_boot.enabled && !trust_ready && !wire.secure_boot.enrollment_pending;
    Some(TrustSnapshot {
        state: SecureBootState {
            enabled: wire.secure_boot.enabled,
            key_present: wire.secure_boot.key_present,
            certificate_present: wire.secure_boot.certificate_present,
            enrolled: wire.secure_boot.enrolled,
            enrollment_pending: wire.secure_boot.enrollment_pending,
            configuration_present: wire.secure_boot.configuration_present,
            dkms_available: wire.secure_boot.dkms_available,
            state_known,
            enforcement_inactive,
            ready,
            trust_ready,
            enrollment_required,
            status,
        },
        dkms: DkmsState {
            modules: wire.dkms.modules,
            untrusted_modules: wire.dkms.untrusted_modules,
        },
    })
}

fn unknown() -> TrustSnapshot {
    TrustSnapshot {
        state: SecureBootState {
            state_known: false,
            status: "unknown".into(),
            ..SecureBootState::default()
        },
        dkms: DkmsState::default(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_toolkit_status_json() {
        let json = r#"{
            "schema": 2,
            "secure_boot": {
                "enabled": true,
                "key_present": true,
                "certificate_present": true,
                "enrolled": false,
                "enrollment_pending": false,
                "dkms_available": true,
                "configuration_present": true,
                "status": "enabled"
            },
            "dkms": {"modules": [], "trusted_modules": [], "untrusted_modules": []}
        }"#;
        let snapshot = parse_status(json.as_bytes()).unwrap();
        assert!(snapshot.state.enabled);
        assert!(snapshot.state.key_present);
        assert!(!snapshot.state.enrolled);
        assert!(snapshot.state.enrollment_required);
        assert!(!snapshot.state.ready);
        assert!(snapshot.dkms.ready());
    }
}
