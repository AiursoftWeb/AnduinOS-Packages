use crate::config;
use crate::i18n::{i18n, i18n_fmt};
use crate::model::{EnrollmentFile, YubiKey};
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

/// Fast, non-interactive device inventory for the home page and hotplug path.
/// This reads sysfs only and must never trigger a PIN, PAM, or touch request.
pub fn list_yubikeys_fast() -> Result<Vec<YubiKey>, String> {
    list_from_sysfs()
}

/// Device inventory for explicit GDM and sudo pages.
///
/// USB sysfs intentionally remains the hotplug source, but FIDO-only YubiKeys
/// may omit their serial from USB descriptors. In that case a short,
/// non-interactive `ykman list` probe supplies the stable serial used by
/// enrollment metadata. The summary probe is bounded and accepted only when
/// it accounts for every sysfs YubiKey, so multi-key identities are never
/// guessed by position.
pub fn list_yubikeys_for_security() -> Result<Vec<YubiKey>, String> {
    let sysfs = list_from_sysfs()?;
    if sysfs.iter().all(|key| !key.serial.starts_with("usb-")) {
        return Ok(sysfs);
    }
    match list_with_ykman_summary() {
        Ok(identified) if !identified.is_empty() && identified.len() == sysfs.len() => {
            Ok(identified)
        }
        _ => Ok(sysfs),
    }
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

fn list_with_ykman_summary() -> Result<Vec<YubiKey>, String> {
    let output = Command::new("timeout")
        .args(["--signal=TERM", "--kill-after=1s", "2s", "ykman", "list"])
        .stdin(Stdio::null())
        .output()
        .map_err(|error| error.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    let mut devices = String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(parse_ykman_list_summary)
        .collect::<Vec<_>>();
    devices.sort_by(|left, right| left.serial.cmp(&right.serial));
    devices.dedup_by(|left, right| left.serial == right.serial);
    Ok(devices)
}

fn parse_ykman_list_summary(line: &str) -> Option<YubiKey> {
    let (identity, serial) = line.rsplit_once(" Serial: ")?;
    let serial = serial.trim();
    if serial.is_empty() || !serial.chars().all(|character| character.is_ascii_digit()) {
        return None;
    }
    let (model_and_version, interfaces) = identity.rsplit_once(" [")?;
    let interfaces = interfaces.strip_suffix(']')?.trim();
    let version_start = model_and_version.rfind(" (")?;
    let name = model_and_version[..version_start].trim();
    let firmware = model_and_version[version_start + 2..]
        .strip_suffix(')')?
        .trim();
    if name.is_empty() || firmware.is_empty() {
        return None;
    }
    Some(YubiKey {
        name: name.to_string(),
        serial: serial.to_string(),
        firmware: firmware.to_string(),
        interfaces: interfaces.to_string(),
    })
}

fn list_from_sysfs() -> Result<Vec<YubiKey>, String> {
    let usb_devices = Path::new("/sys/bus/usb/devices");
    let entries = fs::read_dir(usb_devices).map_err(|error| {
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
        let firmware = read_trimmed(path.join("bcdDevice"))
            .map(|value| format_bcd_firmware(&value))
            .unwrap_or_default();
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

fn format_bcd_firmware(value: &str) -> String {
    let digits = value.trim_start_matches('0');
    if digits.len() >= 3 && digits.chars().all(|character| character.is_ascii_digit()) {
        let (major, remainder) = digits.split_at(digits.len() - 2);
        let (minor, patch) = remainder.split_at(1);
        format!("{major}.{minor}.{patch}")
    } else {
        value.to_string()
    }
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
        name: if name.is_empty() {
            i18n("YubiKey")
        } else {
            name
        },
        serial: serial.into(),
        firmware,
        interfaces,
    }
}

pub fn security_state() -> EnrollmentFile {
    let mut state = fs::read_to_string(config::METADATA)
        .ok()
        .and_then(|data| serde_json::from_str::<EnrollmentFile>(&data).ok())
        .unwrap_or_default();
    if let Ok(snapshot) = fs::read_to_string(config::PASSWORDLESS_SUDO_STATE) {
        if let Some(users) = parse_passwordless_sudo_state(&snapshot) {
            state.passwordless_sudo_users = users;
        }
    }
    state
}

fn parse_passwordless_sudo_state(snapshot: &str) -> Option<Vec<String>> {
    let mut users = Vec::new();
    for username in snapshot.lines() {
        let mut characters = username.chars();
        let first = characters.next()?;
        if username.len() > 32
            || !(first.is_ascii_lowercase() || first == '_')
            || !characters.all(|character| {
                character.is_ascii_lowercase()
                    || character.is_ascii_digit()
                    || matches!(character, '_' | '-')
            })
            || users.iter().any(|item| item == username)
        {
            return None;
        }
        users.push(username.to_string());
    }
    Some(users)
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

pub fn set_passwordless_sudo(username: &str, enabled: bool) -> Result<(), String> {
    run_helper(&[
        "passwordless-sudo",
        username,
        if enabled { "enable" } else { "disable" },
    ])
}

pub fn install_git() -> Result<(), String> {
    run_helper(&["install-git"])
}

fn validate_credential(value: &str) -> Result<(), String> {
    if value.contains('\n')
        || value.contains(':')
        || value.len() < 40
        || !value.chars().all(|c| {
            c.is_ascii_alphanumeric() || matches!(c, ',' | '_' | '-' | '=' | '+' | '/' | '.')
        })
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

    #[test]
    fn formats_usb_bcd_as_a_firmware_version() {
        assert_eq!(format_bcd_firmware("0574"), "5.7.4");
        assert_eq!(format_bcd_firmware("1234"), "12.3.4");
        assert_eq!(format_bcd_firmware(""), "");
    }

    #[test]
    fn parses_noninteractive_ykman_list_summary() {
        let key = parse_ykman_list_summary(
            "YubiKey C Bio - FIDO Edition (5.7.4) [FIDO] Serial: 35411498",
        )
        .unwrap();
        assert_eq!(key.name, "YubiKey C Bio - FIDO Edition");
        assert_eq!(key.serial, "35411498");
        assert_eq!(key.firmware, "5.7.4");
        assert_eq!(key.interfaces, "FIDO");
        assert!(parse_ykman_list_summary("YubiKey C Bio - FIDO Edition (5.7.4) [FIDO]").is_none());
    }

    #[test]
    fn parses_root_owned_passwordless_sudo_snapshot() {
        assert_eq!(
            parse_passwordless_sudo_state("alice\nbob-admin\n"),
            Some(vec!["alice".into(), "bob-admin".into()])
        );
        assert_eq!(parse_passwordless_sudo_state(""), Some(Vec::new()));
        assert_eq!(parse_passwordless_sudo_state("Alice\n"), None);
        assert_eq!(parse_passwordless_sudo_state("alice\nalice\n"), None);
    }
}
