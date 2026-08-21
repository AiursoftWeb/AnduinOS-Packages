//! Short-lived privileged workers for Btrfs commands that need a writable root.
//!
//! The long-running D-Bus helper keeps `ProtectSystem=strict`. Balance cannot
//! operate in that namespace, so it runs as a transient unit with the smallest
//! capability set and no network. Keeping the complete policy here makes the
//! security boundary reviewable without mixing systemd details into Btrfs task
//! state management.

use std::process::Command;

const SYSTEMD_RUN: &str = "/usr/bin/systemd-run";
const BTRFS: &str = "/usr/bin/btrfs";

// These are fixed by the application, not supplied by D-Bus callers.
const WORKER_OPTIONS: &[&str] = &[
    "--quiet",
    "--wait",
    "--pipe",
    "--collect",
    "--property=Type=exec",
    "--property=NoNewPrivileges=yes",
    "--property=PrivateNetwork=yes",
    "--property=ProtectSystem=full",
    "--property=ProtectHome=read-only",
    "--property=ProtectKernelTunables=yes",
    "--property=ProtectKernelModules=yes",
    "--property=ProtectControlGroups=yes",
    "--property=RestrictAddressFamilies=AF_UNIX AF_NETLINK",
    "--property=CapabilityBoundingSet=CAP_SYS_ADMIN",
    "--property=LockPersonality=yes",
    "--property=MemoryDenyWriteExecute=yes",
    "--property=RestrictSUIDSGID=yes",
    "--property=UMask=0077",
];

pub(crate) struct WorkerOutput {
    pub(crate) success: bool,
    pub(crate) stdout: String,
    pub(crate) stderr: String,
}

pub(crate) fn run_btrfs(unit_name: &str, arguments: &[&str]) -> WorkerOutput {
    match Command::new(SYSTEMD_RUN)
        .args(WORKER_OPTIONS)
        .arg(format!("--unit={unit_name}"))
        .arg(BTRFS)
        .args(arguments)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .output()
    {
        Ok(output) => WorkerOutput {
            success: output.status.success(),
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        },
        Err(error) => WorkerOutput {
            success: false,
            stdout: String::new(),
            stderr: format!("Could not start the isolated Btrfs worker: {error}"),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worker_policy_keeps_the_expected_security_boundary() {
        assert!(WORKER_OPTIONS.contains(&"--property=PrivateNetwork=yes"));
        assert!(WORKER_OPTIONS.contains(&"--property=NoNewPrivileges=yes"));
        assert!(WORKER_OPTIONS.contains(&"--property=CapabilityBoundingSet=CAP_SYS_ADMIN"));
        assert!(WORKER_OPTIONS.contains(&"--property=ProtectHome=read-only"));
    }
}
