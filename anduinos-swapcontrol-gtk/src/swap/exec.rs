use std::process::Command;

use crate::i18n::{i18n, i18n_fmt};

const HELPER: &str = "/usr/lib/anduinos-swapcontrol/helper";

/// Run a privileged command via the swapcontrol helper (single polkit entry point).
/// All operations share the same polkit action, so auth is cached after the first prompt.
pub fn run_helper(subcmd: &str, args: &[&str]) -> Result<String, String> {
    let mut cmd_args: Vec<&str> = vec![HELPER, subcmd];
    cmd_args.extend_from_slice(args);

    let output = Command::new("pkexec")
        .env("LC_ALL", "C")
        .env("LANGUAGE", "C")
        .args(&cmd_args)
        .output()
        .map_err(|e| i18n_fmt(&i18n("Failed to execute pkexec: {0}"), &[&e.to_string()]))?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        if output.status.code() == Some(126) {
            Err(i18n("Authentication cancelled"))
        } else {
            let msg = if stderr.is_empty() {
                stdout.to_string()
            } else {
                stderr.to_string()
            };
            Err(msg.trim().to_string())
        }
    }
}

/// Write a value to a file via the helper's `tee` subcommand.
pub fn write_sysfs(path: &str, value: &str) -> Result<String, String> {
    use std::io::Write;
    use std::process::Stdio;

    let mut child = Command::new("pkexec")
        .env("LC_ALL", "C")
        .arg(HELPER)
        .arg("tee")
        .arg(path)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| i18n_fmt(&i18n("Failed to execute pkexec: {0}"), &[&e.to_string()]))?;

    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(value.as_bytes());
        let _ = stdin.write_all(b"\n");
    }

    let output = child
        .wait_with_output()
        .map_err(|e| i18n_fmt(&i18n("Failed to wait on pkexec: {0}"), &[&e.to_string()]))?;

    if output.status.success() {
        Ok(String::new())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        if output.status.code() == Some(126) {
            Err(i18n("Authentication cancelled"))
        } else {
            Err(stderr.trim().to_string())
        }
    }
}
