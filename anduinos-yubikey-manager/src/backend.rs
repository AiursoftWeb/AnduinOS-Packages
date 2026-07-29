use crate::config;
use crate::i18n::{i18n, i18n_fmt};
use crate::model::{Enrollment, EnrollmentFile, YubiKey};
use std::fs;
use std::path::Path;
use std::process::{Command, Stdio};

fn command_output(program: &str, args: &[&str]) -> Result<String, String> {
    let output = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| i18n_fmt(&i18n("Could not run {0}: {1}"), &[program, &e.to_string()]))?;
    if !output.status.success() {
        let message = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if message.is_empty() {
            i18n_fmt(
                &i18n("{0} exited with {1}"),
                &[program, &output.status.to_string()],
            )
        } else {
            message
        });
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

pub fn current_user() -> Result<String, String> {
    command_output("id", &["-un"])
}

pub fn list_yubikeys() -> Result<Vec<YubiKey>, String> {
    if command_exists("ykman") {
        if let Ok(devices) = list_with_ykman() {
            if !devices.is_empty() {
                return Ok(devices);
            }
        }
    }
    list_from_sysfs()
}

fn command_exists(program: &str) -> bool {
    Command::new(program)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok()
}

fn list_with_ykman() -> Result<Vec<YubiKey>, String> {
    let serials = command_output("ykman", &["list", "--serials"])?;
    let mut devices = Vec::new();
    for serial in serials.lines().map(str::trim).filter(|s| !s.is_empty()) {
        let info = command_output("ykman", &["--device", serial, "info"])
            .unwrap_or_else(|_| String::new());
        devices.push(parse_info(serial, &info));
    }
    Ok(devices)
}

fn list_from_sysfs() -> Result<Vec<YubiKey>, String> {
    let usb_devices = Path::new("/sys/bus/usb/devices");
    let entries = fs::read_dir(usb_devices)
        .map_err(|error| {
            i18n_fmt(
                &i18n("Could not inspect USB devices: {0}"),
                &[&error.to_string()],
            )
        })?;
    let mut devices = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if read_trimmed(path.join("idVendor")).as_deref() != Some("1050") {
            continue;
        }
        let hardware_serial = read_trimmed(path.join("serial")).unwrap_or_default();
        let usb_path = entry.file_name().to_string_lossy().to_string();
        let serial = if hardware_serial.is_empty() {
            format!("usb-{usb_path}")
        } else {
            hardware_serial
        };
        let product = read_trimmed(path.join("product")).unwrap_or_else(|| i18n("YubiKey"));
        let firmware = read_trimmed(path.join("bcdDevice")).unwrap_or_default();
        devices.push(YubiKey {
            name: product,
            serial,
            firmware,
            interfaces: i18n("FIDO security key"),
        });
    }
    devices.sort_by(|left, right| left.serial.cmp(&right.serial));
    devices.dedup_by(|left, right| left.serial == right.serial);
    Ok(devices)
}

fn read_trimmed(path: impl AsRef<Path>) -> Option<String> {
    fs::read_to_string(path)
        .ok()
        .map(|value| value.trim().to_string())
}

fn parse_info(serial: &str, info: &str) -> YubiKey {
    let value = |prefix: &str| {
        info.lines()
            .find_map(|line| line.trim().strip_prefix(prefix))
            .map(str::trim)
            .unwrap_or("")
            .to_string()
    };
    let name = value("Device type:");
    let firmware = value("Firmware version:");
    let interfaces = info
        .lines()
        .filter(|line| line.contains("Enabled") || line.contains("USB"))
        .map(str::trim)
        .collect::<Vec<_>>()
        .join(" · ");
    YubiKey {
        name: if name.is_empty() { i18n("YubiKey") } else { name },
        serial: serial.into(),
        firmware,
        interfaces,
    }
}

pub fn enrollments() -> Vec<Enrollment> {
    fs::read_to_string(config::METADATA)
        .ok()
        .and_then(|data| serde_json::from_str::<EnrollmentFile>(&data).ok())
        .unwrap_or_default()
        .enrollments
}

pub fn is_enrolled(username: &str, serial: &str) -> bool {
    is_enrolled_for("gdm", username, serial)
}

pub fn is_enrolled_for(purpose: &str, username: &str, serial: &str) -> bool {
    enrollments()
        .iter()
        .any(|item| {
            item.purpose == purpose && item.username == username && item.serial == serial
        })
}

pub fn register_credential(purpose: &str, username: &str, serial: &str) -> Result<(), String> {
    if !matches!(purpose, "gdm" | "sudo") {
        return Err(i18n("Unknown authentication purpose."));
    }
    if !command_exists("pamu2fcfg") {
        return Err(
            i18n("The FIDO enrollment tool is not installed. Install the libpam-u2f package, then try again."),
        );
    }
    let connected = list_yubikeys()?;
    if connected.len() != 1 || connected[0].serial != serial {
        return Err(
            i18n("For safe enrollment, disconnect every other security key and leave only the selected YubiKey connected."),
        );
    }

    let output = command_output(
        "pamu2fcfg",
        &[
            "--nouser",
            "--origin=pam://anduinos",
            "--appid=pam://anduinos",
        ],
    )?;
    let credential = normalize_credential(&output)?;
    validate_credential(&credential)?;
    run_helper(&["enroll", purpose, username, serial, &credential])
}

pub fn remove_credential(purpose: &str, username: &str, serial: &str) -> Result<(), String> {
    run_helper(&["remove", purpose, username, serial])
}

pub fn passwordless_sudo() -> bool {
    let output = Command::new("sudo")
        .args(["-n", "-l"])
        .stdin(Stdio::null())
        .output();
    output
        .ok()
        .filter(|result| result.status.success())
        .map(|result| String::from_utf8_lossy(&result.stdout).contains("NOPASSWD: ALL"))
        .unwrap_or(false)
}

pub fn set_passwordless_sudo(username: &str, enabled: bool) -> Result<(), String> {
    run_helper(&[
        "passwordless-sudo",
        username,
        if enabled { "enable" } else { "disable" },
    ])
}

fn validate_credential(value: &str) -> Result<(), String> {
    if value.contains('\n')
        || value.contains(':')
        || value.len() < 40
        || !value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, ',' | '_' | '-' | '=' | '+' | '/' | '.'))
        || !(2..=4).contains(&value.split(',').count())
    {
        return Err(i18n("The security key returned an invalid PAM credential."));
    }
    Ok(())
}

fn normalize_credential(output: &str) -> Result<String, String> {
    let value = output.trim();
    let credential = value.strip_prefix(':').unwrap_or(value);
    if credential.starts_with(':') {
        return Err(i18n("The security key returned an invalid PAM credential."));
    }
    Ok(credential.to_string())
}

fn run_helper(args: &[&str]) -> Result<(), String> {
    let output = Command::new("pkexec")
        .arg(config::HELPER)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| {
            i18n_fmt(
                &i18n("Could not request administrator access: {0}"),
                &[&e.to_string()],
            )
        })?;
    if output.status.success() {
        return Ok(());
    }
    let message = String::from_utf8_lossy(&output.stderr).trim().to_string();
    Err(if message.is_empty() {
        i18n("The operation was cancelled or denied.")
    } else {
        message
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ykman_info() {
        let key = parse_info(
            "1234567",
            "Device type: YubiKey 5 NFC\nFirmware version: 5.7.1\nEnabled USB interfaces: OTP, FIDO, CCID",
        );
        assert_eq!(key.name, "YubiKey 5 NFC");
        assert_eq!(key.firmware, "5.7.1");
        assert!(key.interfaces.contains("FIDO"));
    }

    #[test]
    fn rejects_mapping_injection() {
        assert!(validate_credential("abc:def").is_err());
        assert!(validate_credential("abc\nother").is_err());
        assert!(validate_credential("abc,def,es256,+presence").is_err()); // deliberately too short
    }

    #[test]
    fn accepts_pamu2fcfg_append_format() {
        let key_handle = "A".repeat(64);
        let public_key = "B".repeat(64);
        let output = format!(":{key_handle},{public_key},es256,+presence");
        let credential = normalize_credential(&output).unwrap();
        assert!(!credential.starts_with(':'));
        assert!(validate_credential(&credential).is_ok());
    }
}
