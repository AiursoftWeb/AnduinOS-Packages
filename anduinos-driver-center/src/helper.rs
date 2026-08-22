use std::process::Command;

use serde_json::Value;

use crate::config;

pub struct HelperResult {
    pub ok: bool,
    pub message: String,
    pub payload: Value,
}

impl HelperResult {
    fn from_output(output: std::process::Output) -> Self {
        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        let payload = stdout
            .lines()
            .rev()
            .find_map(|line| serde_json::from_str::<Value>(line.trim()).ok())
            .unwrap_or(Value::Null);
        let message = stdout
            .lines()
            .last()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .unwrap_or_else(|| stderr.trim())
            .to_string();
        Self {
            ok: output.status.success(),
            message,
            payload,
        }
    }

    fn from_error(error: impl ToString) -> Self {
        Self {
            ok: false,
            message: error.to_string(),
            payload: Value::Null,
        }
    }
}

pub fn run(arguments: &[&str]) -> HelperResult {
    match Command::new("pkexec")
        .arg(config::HELPER)
        .args(arguments)
        .output()
    {
        Ok(output) => HelperResult::from_output(output),
        Err(error) => HelperResult::from_error(error),
    }
}

pub fn run_secureboot(action: &str) -> HelperResult {
    match Command::new("pkexec")
        .arg(config::SECUREBOOT_HELPER)
        .arg(action)
        .output()
    {
        Ok(output) => HelperResult::from_output(output),
        Err(error) => HelperResult::from_error(error),
    }
}

pub fn refresh_firmware() -> HelperResult {
    fwupd(&["refresh", "--force"], "Firmware metadata refreshed.")
}

pub fn update_firmware(device_id: &str) -> HelperResult {
    fwupd(&["update", device_id, "-y"], "Firmware update completed.")
}

pub fn update_all_firmware() -> HelperResult {
    fwupd(&["update", "-y"], "Firmware update completed.")
}

fn fwupd(args: &[&str], fallback: &str) -> HelperResult {
    match Command::new("fwupdmgr").args(args).output() {
        Ok(output) => {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let stderr = String::from_utf8_lossy(&output.stderr);
            let message = stdout
                .lines()
                .last()
                .map(str::trim)
                .filter(|line| !line.is_empty())
                .unwrap_or(fallback)
                .to_string();
            HelperResult {
                ok: output.status.success(),
                message: if output.status.success() {
                    message
                } else {
                    let detail = stderr.trim();
                    if detail.is_empty() {
                        message
                    } else {
                        detail.to_string()
                    }
                },
                payload: Value::Null,
            }
        }
        Err(error) => HelperResult::from_error(error),
    }
}

pub fn needs_reboot(message: &str) -> bool {
    let lowered = message.to_lowercase();
    lowered.contains("reboot") || lowered.contains("restart")
}

pub fn needs_shutdown(message: &str) -> bool {
    message.to_lowercase().contains("shut down") || message.to_lowercase().contains("shutdown")
}
