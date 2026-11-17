//! Read-only detection of other firewall managers. A clean result is scoped to
//! supported managers, never a claim that all kernel traffic is permitted.
use crate::i18n::i18n;
use serde::Deserialize;
use std::process::Command;

#[derive(Debug, Deserialize)]
pub struct Manager {
    pub name: String,
    pub installed: bool,
    pub active: bool,
    pub enabled: bool,
    pub rules: bool,
}

#[derive(Debug, Deserialize)]
pub struct Health {
    pub ufw_available: bool,
    pub managers: Vec<Manager>,
    pub errors: Vec<String>,
    #[serde(default)]
    pub ufw_status: Option<String>,
    #[serde(default)]
    pub ufw_status_error: Option<String>,
}

impl Health {
    pub fn needs_attention(&self) -> bool {
        !self.ufw_available || self.managers.iter().any(|m| m.active || m.enabled || m.rules)
    }

    pub fn has_other_manager_installed(&self) -> bool {
        // nft is also the inspection tool and UFW's underlying rules interface.
        // Its package alone is not evidence of a second firewall manager.
        self.managers.iter().any(|m| m.installed && m.name != "nftables")
    }

    pub fn title(&self) -> String {
        if !self.ufw_available {
            i18n("Cannot manage the firewall: UFW is missing")
        } else if self.needs_attention() {
            i18n("Firewall configuration may conflict")
        } else if !self.errors.is_empty() {
            i18n("Cannot confirm the complete firewall state")
        } else if self.has_other_manager_installed() {
            i18n("Other firewall management software is installed")
        } else {
            i18n("UFW management")
        }
    }

    pub fn summary(&self) -> String {
        if !self.ufw_available {
            i18n("The UFW executable is unavailable. UFW controls cannot be used until it is restored.")
        } else if self.needs_attention() {
            i18n("Review the other firewall manager before changing UFW. Disabling UFW does not disable these other managers.")
        } else if !self.errors.is_empty() {
            i18n("Some checks could not be completed. Use Refresh to retry and authorize the inspection if prompted.")
        } else if self.has_other_manager_installed() {
            i18n("Installation alone does not mean another firewall is active.")
        } else {
            i18n("This application manages UFW only.")
        }
    }

    pub fn explanation(&self) -> String {
        let mut lines = vec![i18n("This application manages UFW only. Other firewall rules may still block connections when UFW is off.")];
        if !self.ufw_available {
            lines.push(i18n("The UFW executable is unavailable. UFW controls cannot be used until it is restored."));
        }
        for manager in &self.managers {
            let mut evidence = Vec::new();
            if manager.installed { evidence.push(i18n("installed")); }
            if manager.active { evidence.push(i18n("service running")); }
            if manager.enabled { evidence.push(i18n("service enabled")); }
            if manager.rules { evidence.push(i18n("filtering rules detected")); }
            if !evidence.is_empty() {
                lines.push(format!("{}: {}", manager.name, evidence.join(", ")));
            }
        }
        if self.needs_attention() && self.ufw_available {
            lines.push(i18n("Review the other firewall manager before changing UFW. Disabling UFW does not disable these other managers."));
        } else if self.has_other_manager_installed() {
            lines.push(i18n("Installation alone does not mean another firewall is active."));
        }
        if !self.errors.is_empty() {
            lines.push(i18n("Some checks could not be completed. Use Refresh to retry and authorize the inspection if prompted."));
            lines.push(format!("{}: {}", i18n("Incomplete checks"), self.errors.join(", ")));
        }
        lines.join("\n\n")
    }
}

pub fn ufw_available() -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata("/usr/sbin/ufw")
        .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

fn read_report(elevated: bool) -> Option<Health> {
    let helper = "/usr/libexec/ufwall-gtk/network-service-helper";
    let mut command = if elevated {
        let mut command = Command::new("pkexec");
        command.arg(helper);
        command
    } else {
        Command::new(helper)
    };
    let output = command.arg("firewall-health").output().ok()?;
    if !output.status.success() { return None; }
    serde_json::from_slice(&output.stdout).ok()
}

pub fn inspect() -> Health {
    // Package and service checks need no elevation. Retain that evidence if
    // the user declines permission to read kernel rules.
    let baseline = read_report(false).unwrap_or_else(|| Health {
        ufw_available: ufw_available(),
        managers: Vec::new(),
        errors: vec![i18n("Firewall inspection")],
        ufw_status: None,
        ufw_status_error: None,
    });
    if baseline.errors.is_empty() { return baseline; }
    read_report(true).unwrap_or(baseline)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn installed_is_not_active_but_enabled_and_rules_need_attention() {
        let mut health: Health = serde_json::from_str(r#"{"ufw_available":true,"managers":[{"name":"firewalld","installed":true,"active":false,"enabled":false,"rules":false}],"errors":[]}"#).unwrap();
        assert!(!health.needs_attention());
        health.managers[0].enabled = true;
        assert!(health.needs_attention());
        health.managers[0].enabled = false;
        health.managers[0].rules = true;
        assert!(health.needs_attention());
    }

    #[test]
    fn nftables_tool_alone_is_not_an_installed_manager_warning() {
        let mut health: Health = serde_json::from_str(r#"{"ufw_available":true,"managers":[{"name":"nftables","installed":true,"active":false,"enabled":false,"rules":false}],"errors":[]}"#).unwrap();
        assert!(!health.needs_attention());
        assert!(!health.has_other_manager_installed());
        health.managers[0].active = true;
        assert!(health.needs_attention());
        health.managers[0].active = false;
        health.managers[0].enabled = true;
        assert!(health.needs_attention());
        health.managers[0].enabled = false;
        health.managers[0].rules = true;
        assert!(health.needs_attention());
        health.managers[0].rules = false;
        health.managers[0].name = "firewalld".into();
        assert!(health.has_other_manager_installed());
    }

    #[test]
    fn missing_ufw_needs_attention_even_when_checks_fail() {
        let health = Health { ufw_available: false, managers: vec![], errors: vec!["rules".into()], ufw_status: None, ufw_status_error: None };
        assert!(health.needs_attention());
        assert!(!health.errors.is_empty());
    }
}
