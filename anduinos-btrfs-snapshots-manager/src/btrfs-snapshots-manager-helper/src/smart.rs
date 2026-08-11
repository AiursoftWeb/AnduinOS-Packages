//! Bounded, read-only S.M.A.R.T. device summaries for the settings UI.
//!
//! Device paths are derived from the mounted root Btrfs filesystem and the
//! kernel block-device topology. The D-Bus caller cannot provide a path.

use anyhow::{Context, Result, bail};
use serde_json::Value;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

const BTRFS: &str = "/usr/bin/btrfs";
const LSBLK: &str = "/usr/bin/lsblk";
const SMARTCTL: &str = "/usr/sbin/smartctl";
const MAX_SMARTCTL_OUTPUT_BYTES: usize = 2 * 1024 * 1024;
const MAX_DEVICES: usize = 64;
const STORAGE_TOPOLOGY_COMMAND_TIMEOUT: Duration = Duration::from_secs(5);
const SMARTCTL_COMMAND_TIMEOUT: Duration = Duration::from_secs(10);
const SMART_QUERY_TIMEOUT: Duration = Duration::from_secs(30);
const CHILD_POLL_INTERVAL: Duration = Duration::from_millis(20);
static TIMED_OUT_STORAGE_COMMAND_PENDING: AtomicBool = AtomicBool::new(false);

#[derive(Debug, serde::Serialize)]
pub struct SmartHealthStatus {
    pub schema_version: u32,
    pub available: bool,
    pub devices: Vec<SmartDiskHealth>,
    pub error: Option<String>,
}

#[derive(Debug, Default, serde::Serialize)]
pub struct SmartDiskHealth {
    pub device: String,
    pub model: String,
    pub protocol: String,
    pub capacity_bytes: Option<u64>,
    pub smart_available: bool,
    pub smart_enabled: bool,
    pub passed: Option<bool>,
    pub assessment: String,
    pub rotation_rate_rpm: Option<u64>,
    pub temperature_celsius: Option<i64>,
    pub power_on_hours: Option<u64>,
    pub power_cycles: Option<u64>,
    pub lifetime_used_percent: Option<u64>,
    pub critical_warning: Option<u64>,
    pub available_spare_percent: Option<u64>,
    pub available_spare_threshold_percent: Option<u64>,
    pub bytes_read: Option<u64>,
    pub bytes_written: Option<u64>,
    pub unsafe_shutdowns: Option<u64>,
    pub error_log_entries: Option<u64>,
    pub warning_temperature_minutes: Option<u64>,
    pub critical_temperature_minutes: Option<u64>,
    pub reallocated_sectors: Option<u64>,
    pub pending_sectors: Option<u64>,
    pub offline_uncorrectable: Option<u64>,
    pub reported_uncorrectable: Option<u64>,
    pub interface_crc_errors: Option<u64>,
    pub spin_retry_count: Option<u64>,
    pub media_errors: Option<u64>,
    pub threshold_exceeded_in_past: bool,
    pub threshold_failing_now: bool,
    pub error: Option<String>,
}

pub fn disk_health() -> Result<SmartHealthStatus> {
    let deadline = Instant::now() + SMART_QUERY_TIMEOUT;
    let members = root_btrfs_members(deadline)?;
    let topology = block_topology(deadline)?;
    let system_devices = physical_devices(&members, &topology);
    if system_devices.len() > MAX_DEVICES {
        bail!("The root Btrfs filesystem has too many storage devices");
    }

    let mut devices = Vec::with_capacity(system_devices.len());
    for name in system_devices {
        let result = remaining_timeout(deadline, SMARTCTL_COMMAND_TIMEOUT)
            .context("The S.M.A.R.T. query reached its overall time limit")
            .and_then(|timeout| {
                run_smartctl(
                    &[
                        "--info",
                        "--health",
                        "--attributes",
                        "--capabilities",
                        "--json",
                        &name,
                    ],
                    timeout,
                )
            });
        devices.push(match result {
            Ok(value) => parse_disk(&value, &name, "Unknown"),
            Err(error) => SmartDiskHealth {
                device: name,
                assessment: "unavailable".into(),
                error: Some(error.to_string()),
                ..SmartDiskHealth::default()
            },
        });
    }

    let error = if devices.is_empty() {
        Some("No physical storage device backs the root Btrfs filesystem".into())
    } else {
        None
    };
    Ok(SmartHealthStatus {
        schema_version: 2,
        available: !devices.is_empty(),
        devices,
        error,
    })
}

#[derive(Debug, Default)]
struct BlockTopology {
    kinds: HashMap<String, String>,
    parents: HashMap<String, Vec<String>>,
}

fn root_btrfs_members(deadline: Instant) -> Result<Vec<String>> {
    let output = run_text_command(
        BTRFS,
        &["filesystem", "show", "--raw", "/"],
        "Failed to inspect the root Btrfs filesystem",
        remaining_timeout(deadline, STORAGE_TOPOLOGY_COMMAND_TIMEOUT)
            .context("The storage topology query reached its overall time limit")?,
    )?;
    let members = parse_btrfs_members(&output);
    if members.is_empty() {
        bail!("The root Btrfs filesystem did not report any available member devices");
    }
    Ok(members)
}

fn block_topology(deadline: Instant) -> Result<BlockTopology> {
    let output = run_text_command(
        LSBLK,
        &[
            "--raw",
            "--paths",
            "--noheadings",
            "--output",
            "PATH,TYPE,PKNAME",
        ],
        "Failed to inspect the system storage topology",
        remaining_timeout(deadline, STORAGE_TOPOLOGY_COMMAND_TIMEOUT)
            .context("The storage topology query reached its overall time limit")?,
    )?;
    Ok(parse_block_topology(&output))
}

fn run_text_command(
    command: &str,
    arguments: &[&str],
    description: &str,
    timeout: Duration,
) -> Result<String> {
    let mut command = Command::new(command);
    command
        .args(arguments)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C");
    let output = command_output_with_timeout(&mut command, timeout, description)?;
    if output.stdout.len() > MAX_SMARTCTL_OUTPUT_BYTES
        || output.stderr.len() > MAX_SMARTCTL_OUTPUT_BYTES
    {
        bail!("A storage topology command returned excessive output");
    }
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        bail!("{description}: {}", stderr.trim());
    }
    String::from_utf8(output.stdout).context("A storage topology command returned non-UTF-8 output")
}

fn parse_btrfs_members(output: &str) -> Vec<String> {
    output
        .lines()
        .filter(|line| line.split_whitespace().next() == Some("devid"))
        .filter(|line| !line.split_whitespace().any(|field| field == "MISSING"))
        .filter_map(|line| {
            let fields = line.split_whitespace().collect::<Vec<_>>();
            let path = fields
                .iter()
                .position(|field| *field == "path")
                .and_then(|index| fields.get(index + 1))?;
            safe_device_path(path).then(|| (*path).to_string())
        })
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn parse_block_topology(output: &str) -> BlockTopology {
    let mut topology = BlockTopology::default();
    for line in output.lines() {
        let mut fields = line.split_whitespace();
        let (Some(path), Some(kind)) = (fields.next(), fields.next()) else {
            continue;
        };
        if !safe_device_path(path) {
            continue;
        }
        topology.kinds.insert(path.into(), kind.into());
        if let Some(parent) = fields.next()
            && safe_device_path(parent)
        {
            topology
                .parents
                .entry(path.into())
                .or_default()
                .push(parent.into());
        }
    }
    topology
}

fn physical_devices(members: &[String], topology: &BlockTopology) -> Vec<String> {
    let mut resolved = BTreeSet::new();
    for member in members {
        resolve_physical_devices(member, topology, &mut HashSet::new(), &mut resolved);
    }
    resolved.into_iter().collect()
}

fn resolve_physical_devices(
    device: &str,
    topology: &BlockTopology,
    visited: &mut HashSet<String>,
    resolved: &mut BTreeSet<String>,
) {
    if !visited.insert(device.to_string()) {
        return;
    }
    if topology
        .kinds
        .get(device)
        .is_some_and(|kind| kind == "disk")
    {
        resolved.insert(device.to_string());
        return;
    }
    let Some(parents) = topology.parents.get(device) else {
        // Query the member itself when the kernel topology lacks a mapping.
        // smartctl supports raw disks and NVMe namespaces directly.
        resolved.insert(device.to_string());
        return;
    };
    for parent in parents {
        resolve_physical_devices(parent, topology, visited, resolved);
    }
}

fn run_smartctl(arguments: &[&str], timeout: Duration) -> Result<Value> {
    let mut command = Command::new(SMARTCTL);
    command
        .args(arguments)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C");
    let output = command_output_with_timeout(&mut command, timeout, "smartctl")?;
    if output.stdout.len() > MAX_SMARTCTL_OUTPUT_BYTES
        || output.stderr.len() > MAX_SMARTCTL_OUTPUT_BYTES
    {
        bail!("smartctl returned excessive output");
    }
    let value: Value = serde_json::from_slice(&output.stdout).with_context(|| {
        let stderr = String::from_utf8_lossy(&output.stderr);
        format!("smartctl did not return valid JSON: {}", stderr.trim())
    })?;
    // smartctl uses a bitmask exit status for both command errors and health
    // findings. Valid JSON remains authoritative even when the status is not 0.
    Ok(value)
}

fn remaining_timeout(deadline: Instant, per_command_limit: Duration) -> Option<Duration> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|remaining| !remaining.is_zero())
        .map(|remaining| remaining.min(per_command_limit))
}

fn command_output_with_timeout(
    command: &mut Command,
    timeout: Duration,
    description: &str,
) -> Result<Output> {
    if TIMED_OUT_STORAGE_COMMAND_PENDING.load(Ordering::Acquire) {
        bail!("A previous timed-out storage command is still terminating");
    }
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .with_context(|| format!("Failed to execute {description}"))?;
    let deadline = Instant::now() + timeout;
    loop {
        if child
            .try_wait()
            .with_context(|| format!("Failed while waiting for {description}"))?
            .is_some()
        {
            return child
                .wait_with_output()
                .with_context(|| format!("Failed to collect output from {description}"));
        }
        let now = Instant::now();
        if now >= deadline {
            let _ = child.kill();
            TIMED_OUT_STORAGE_COMMAND_PENDING.store(true, Ordering::Release);
            // A drive command may be stuck in uninterruptible kernel I/O. Reap
            // it off-thread so the D-Bus request still returns at its deadline.
            // Until it is actually reaped, the global latch prevents any new
            // storage probe from accumulating behind the broken device.
            let _ = std::thread::Builder::new()
                .name("smart-command-reaper".into())
                .spawn(move || {
                    let _ = child.wait_with_output();
                    TIMED_OUT_STORAGE_COMMAND_PENDING.store(false, Ordering::Release);
                });
            bail!(
                "{description} timed out after {:.1} seconds",
                timeout.as_secs_f64()
            );
        }
        std::thread::sleep(CHILD_POLL_INTERVAL.min(deadline - now));
    }
}

fn safe_device_path(device: &str) -> bool {
    let Some(relative) = device.strip_prefix("/dev/") else {
        return false;
    };
    let components = relative.split('/').collect::<Vec<_>>();
    !components.is_empty()
        && components.len() <= 4
        && components.iter().all(|component| {
            !component.is_empty()
                && *component != "."
                && *component != ".."
                && !component.starts_with('-')
                && component
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
        })
}

fn parse_disk(value: &Value, fallback_device: &str, fallback_protocol: &str) -> SmartDiskHealth {
    let smart_available = value
        .pointer("/smart_support/available")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let smart_enabled = value
        .pointer("/smart_support/enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let passed = value
        .pointer("/smart_status/passed")
        .and_then(Value::as_bool);
    let reallocated_sectors = ata_attribute(value, 5);
    let pending_sectors = ata_attribute(value, 197);
    let offline_uncorrectable = ata_attribute(value, 198);
    let reported_uncorrectable = ata_attribute(value, 187);
    let interface_crc_errors = ata_attribute(value, 199);
    let spin_retry_count = ata_attribute(value, 10);
    let media_errors = u64_at(value, "/nvme_smart_health_information_log/media_errors");
    let critical_warning = u64_at(value, "/nvme_smart_health_information_log/critical_warning");
    let available_spare_percent =
        u64_at(value, "/nvme_smart_health_information_log/available_spare");
    let available_spare_threshold_percent = u64_at(
        value,
        "/nvme_smart_health_information_log/available_spare_threshold",
    );
    let error = smartctl_error(value);
    let current_attribute_failure = value
        .pointer("/ata_smart_attributes/table")
        .and_then(Value::as_array)
        .is_some_and(|attributes| {
            attributes.iter().any(|attribute| {
                attribute
                    .get("when_failed")
                    .and_then(Value::as_str)
                    .is_some_and(|state| state == "now")
            })
        });
    let past_attribute_failure = value
        .pointer("/ata_smart_attributes/table")
        .and_then(Value::as_array)
        .is_some_and(|attributes| {
            attributes.iter().any(|attribute| {
                attribute
                    .get("when_failed")
                    .and_then(Value::as_str)
                    .is_some_and(|state| !state.is_empty())
            })
        });
    let has_reliability_events = [
        reallocated_sectors,
        pending_sectors,
        offline_uncorrectable,
        reported_uncorrectable,
        interface_crc_errors,
        spin_retry_count,
        media_errors,
    ]
    .into_iter()
    .flatten()
    .any(|count| count > 0);
    let has_nvme_critical_warning = critical_warning.is_some_and(|warning| warning != 0);
    let spare_below_threshold = available_spare_percent
        .zip(available_spare_threshold_percent)
        .is_some_and(|(available, threshold)| available <= threshold);
    let assessment = match passed {
        Some(false) => "failing",
        Some(true)
            if current_attribute_failure || has_nvme_critical_warning || spare_below_threshold =>
        {
            "failing"
        }
        Some(true) if past_attribute_failure || has_reliability_events || error.is_some() => {
            "warning"
        }
        Some(true) => "healthy",
        None if has_nvme_critical_warning || spare_below_threshold => "failing",
        None if error.is_some() => "unavailable",
        None if smart_available && !smart_enabled => "disabled",
        None if smart_available => "unknown",
        None => "unavailable",
    };

    SmartDiskHealth {
        device: value
            .pointer("/device/name")
            .and_then(Value::as_str)
            .unwrap_or(fallback_device)
            .to_string(),
        model: value
            .get("model_name")
            .and_then(Value::as_str)
            .unwrap_or("Unknown storage device")
            .to_string(),
        protocol: value
            .pointer("/device/protocol")
            .and_then(Value::as_str)
            .unwrap_or(fallback_protocol)
            .to_string(),
        capacity_bytes: u64_at(value, "/user_capacity/bytes")
            .or_else(|| u64_at(value, "/nvme_total_capacity")),
        smart_available,
        smart_enabled,
        passed,
        assessment: assessment.into(),
        rotation_rate_rpm: u64_at(value, "/rotation_rate"),
        temperature_celsius: value
            .pointer("/temperature/current")
            .and_then(Value::as_i64)
            .or_else(|| {
                value
                    .pointer("/nvme_smart_health_information_log/temperature")
                    .and_then(Value::as_i64)
            }),
        power_on_hours: u64_at(value, "/power_on_time/hours")
            .or_else(|| u64_at(value, "/nvme_smart_health_information_log/power_on_hours")),
        power_cycles: u64_at(value, "/nvme_smart_health_information_log/power_cycles")
            .or_else(|| ata_attribute(value, 12)),
        lifetime_used_percent: u64_at(value, "/endurance_used/current_percent")
            .or_else(|| u64_at(value, "/nvme_smart_health_information_log/percentage_used")),
        critical_warning,
        available_spare_percent,
        available_spare_threshold_percent,
        bytes_read: nvme_data_bytes(value, "data_units_read"),
        bytes_written: nvme_data_bytes(value, "data_units_written"),
        unsafe_shutdowns: u64_at(value, "/nvme_smart_health_information_log/unsafe_shutdowns")
            .or_else(|| ata_named_attribute(value, &["unsafe_shutdown", "unexpected_power_loss"])),
        error_log_entries: u64_at(
            value,
            "/nvme_smart_health_information_log/num_err_log_entries",
        ),
        warning_temperature_minutes: u64_at(
            value,
            "/nvme_smart_health_information_log/warning_temp_time",
        ),
        critical_temperature_minutes: u64_at(
            value,
            "/nvme_smart_health_information_log/critical_comp_time",
        ),
        reallocated_sectors,
        pending_sectors,
        offline_uncorrectable,
        reported_uncorrectable,
        interface_crc_errors,
        spin_retry_count,
        media_errors,
        threshold_exceeded_in_past: past_attribute_failure,
        threshold_failing_now: current_attribute_failure,
        error,
    }
}

fn u64_at(value: &Value, pointer: &str) -> Option<u64> {
    value
        .pointer(pointer)
        .and_then(|value| {
            value
                .as_u64()
                .or_else(|| value.as_str().and_then(|value| value.parse().ok()))
        })
        .or_else(|| {
            value
                .pointer(&format!("{pointer}_s"))
                .and_then(Value::as_str)
                .and_then(|value| value.parse().ok())
        })
}

fn nvme_data_bytes(value: &Value, field: &str) -> Option<u64> {
    // NVMe defines one data unit as 1,000 logical blocks of 512 bytes.
    u64_at(
        value,
        &format!("/nvme_smart_health_information_log/{field}"),
    )?
    .checked_mul(512_000)
}

fn ata_attribute(value: &Value, id: u64) -> Option<u64> {
    value
        .pointer("/ata_smart_attributes/table")?
        .as_array()?
        .iter()
        .find(|attribute| attribute.get("id").and_then(Value::as_u64) == Some(id))?
        .pointer("/raw/value")?
        .as_u64()
}

fn ata_named_attribute(value: &Value, name_fragments: &[&str]) -> Option<u64> {
    value
        .pointer("/ata_smart_attributes/table")?
        .as_array()?
        .iter()
        .find(|attribute| {
            let name = attribute
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_ascii_lowercase();
            name_fragments
                .iter()
                .any(|fragment| name.contains(fragment))
        })?
        .pointer("/raw/value")?
        .as_u64()
}

fn smartctl_error(value: &Value) -> Option<String> {
    let messages = value.get("smartctl")?.get("messages")?.as_array()?;
    let joined = messages
        .iter()
        .filter(|message| message.get("severity").and_then(Value::as_str) == Some("error"))
        .filter_map(|message| message.get("string").and_then(Value::as_str))
        .collect::<Vec<_>>()
        .join("; ");
    (!joined.is_empty()).then_some(joined)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn external_storage_commands_are_hard_bounded() {
        let started = Instant::now();
        let mut command = Command::new("/bin/sleep");
        command.arg("5");
        let error = command_output_with_timeout(
            &mut command,
            Duration::from_millis(40),
            "test storage command",
        )
        .unwrap_err();
        assert!(error.to_string().contains("timed out"));
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn per_command_timeout_never_exceeds_the_overall_deadline() {
        let deadline = Instant::now() + Duration::from_millis(50);
        let timeout = remaining_timeout(deadline, Duration::from_secs(10)).unwrap();
        assert!(timeout <= Duration::from_millis(50));
        assert!(remaining_timeout(Instant::now(), Duration::from_secs(10)).is_none());
    }

    #[test]
    fn accepts_only_bounded_device_paths_below_dev() {
        assert!(safe_device_path("/dev/nvme0"));
        assert!(safe_device_path("/dev/disk-1_test.2"));
        assert!(safe_device_path("/dev/bus/0"));
        assert!(!safe_device_path("/dev/../etc/passwd"));
        assert!(!safe_device_path("../../etc/passwd"));
        assert!(!safe_device_path("/dev/--scan"));
    }

    #[test]
    fn selects_only_physical_disks_backing_root_btrfs_members() {
        let btrfs = concat!(
            "Label: 'AnduinOS' uuid: example\n",
            "  devid 1 size 100 used 50 path /dev/nvme0n1p4\n",
            "  devid 2 size 100 used 50 path /dev/mapper/system-data\n",
            "  devid 3 size 0 used 0 path /dev/sdz1 MISSING\n",
        );
        let members = parse_btrfs_members(btrfs);
        assert_eq!(
            members,
            vec![
                "/dev/mapper/system-data".to_string(),
                "/dev/nvme0n1p4".to_string()
            ]
        );

        let lsblk = concat!(
            "/dev/nvme0n1 disk\n",
            "/dev/nvme0n1p4 part /dev/nvme0n1\n",
            "/dev/sda disk\n",
            "/dev/sda3 part /dev/sda\n",
            "/dev/sdb disk\n",
            "/dev/sdb3 part /dev/sdb\n",
            "/dev/md1 raid1 /dev/sda3\n",
            "/dev/md1 raid1 /dev/sdb3\n",
            "/dev/mapper/system-data crypt /dev/md1\n",
            "/dev/nvme9n1 disk\n",
        );
        let topology = parse_block_topology(lsblk);
        assert_eq!(
            physical_devices(&members, &topology),
            vec![
                "/dev/nvme0n1".to_string(),
                "/dev/sda".to_string(),
                "/dev/sdb".to_string()
            ]
        );
    }

    #[test]
    fn parses_nvme_health_summary() {
        let value = serde_json::json!({
            "device": {"name": "/dev/nvme0", "protocol": "NVMe"},
            "model_name": "Example NVMe",
            "user_capacity": {"bytes": 1_000_000_000_000_u64},
            "smart_support": {"available": true, "enabled": true},
            "smart_status": {"passed": true},
            "temperature": {"current": 41},
            "power_on_time": {"hours": 12_345},
            "endurance_used": {"current_percent": 7},
            "nvme_smart_health_information_log": {
                "critical_warning": 0,
                "available_spare": 98,
                "available_spare_threshold": 5,
                "data_units_read": 2_000,
                "data_units_written": 3_000,
                "power_cycles": 42,
                "unsafe_shutdowns": 3,
                "media_errors": 0,
                "num_err_log_entries": 11,
                "warning_temp_time": 7,
                "critical_comp_time": 0
            }
        });
        let disk = parse_disk(&value, "/dev/fallback", "Unknown");
        assert_eq!(disk.device, "/dev/nvme0");
        assert_eq!(disk.model, "Example NVMe");
        assert_eq!(disk.assessment, "healthy");
        assert_eq!(disk.temperature_celsius, Some(41));
        assert_eq!(disk.lifetime_used_percent, Some(7));
        assert_eq!(disk.bytes_read, Some(1_024_000_000));
        assert_eq!(disk.bytes_written, Some(1_536_000_000));
        assert_eq!(disk.power_cycles, Some(42));
        assert_eq!(disk.unsafe_shutdowns, Some(3));
        assert_eq!(disk.available_spare_percent, Some(98));
        assert_eq!(disk.media_errors, Some(0));
        assert_eq!(disk.error_log_entries, Some(11));
    }

    #[test]
    fn ata_reliability_events_raise_attention_without_overriding_passed() {
        let value = serde_json::json!({
            "device": {"name": "/dev/sda", "protocol": "ATA"},
            "model_name": "Example HDD",
            "smart_support": {"available": true, "enabled": true},
            "smart_status": {"passed": true},
            "ata_smart_attributes": {"table": [
                {"id": 5, "when_failed": "", "raw": {"value": 2}},
                {"id": 197, "when_failed": "", "raw": {"value": 0}},
                {"id": 198, "when_failed": "", "raw": {"value": 0}}
            ]}
        });
        let disk = parse_disk(&value, "/dev/sda", "ATA");
        assert_eq!(disk.assessment, "warning");
        assert_eq!(disk.reallocated_sectors, Some(2));
        assert_eq!(disk.pending_sectors, Some(0));
    }

    #[test]
    fn current_threshold_failure_is_failing() {
        let value = serde_json::json!({
            "smart_support": {"available": true, "enabled": true},
            "smart_status": {"passed": true},
            "ata_smart_attributes": {"table": [
                {"id": 5, "when_failed": "now", "raw": {"value": 9}}
            ]}
        });
        assert_eq!(parse_disk(&value, "/dev/sda", "ATA").assessment, "failing");
    }

    #[test]
    fn nvme_critical_warning_is_failing_even_without_overall_status() {
        let value = serde_json::json!({
            "smart_support": {"available": true, "enabled": true},
            "nvme_smart_health_information_log": {
                "critical_warning": 4,
                "available_spare": 100,
                "available_spare_threshold": 1
            }
        });
        assert_eq!(
            parse_disk(&value, "/dev/nvme0", "NVMe").assessment,
            "failing"
        );
    }

    #[test]
    fn parses_mechanical_disk_reliability_counters() {
        let value = serde_json::json!({
            "device": {"name": "/dev/sda", "protocol": "ATA"},
            "model_name": "Example HDD",
            "rotation_rate": 7200,
            "smart_support": {"available": true, "enabled": true},
            "smart_status": {"passed": true},
            "ata_smart_attributes": {"table": [
                {"id": 5, "name": "Reallocated_Sector_Ct", "when_failed": "", "raw": {"value": 0}},
                {"id": 10, "name": "Spin_Retry_Count", "when_failed": "", "raw": {"value": 0}},
                {"id": 12, "name": "Power_Cycle_Count", "when_failed": "", "raw": {"value": 321}},
                {"id": 187, "name": "Reported_Uncorrect", "when_failed": "", "raw": {"value": 0}},
                {"id": 197, "name": "Current_Pending_Sector", "when_failed": "", "raw": {"value": 0}},
                {"id": 198, "name": "Offline_Uncorrectable", "when_failed": "", "raw": {"value": 0}},
                {"id": 199, "name": "UDMA_CRC_Error_Count", "when_failed": "", "raw": {"value": 2}}
            ]}
        });
        let disk = parse_disk(&value, "/dev/sda", "ATA");
        assert_eq!(disk.rotation_rate_rpm, Some(7200));
        assert_eq!(disk.power_cycles, Some(321));
        assert_eq!(disk.interface_crc_errors, Some(2));
        assert_eq!(disk.assessment, "warning");
    }

    #[test]
    fn distinguishes_disabled_and_unavailable_smart() {
        let disabled = serde_json::json!({
            "smart_support": {"available": true, "enabled": false}
        });
        assert_eq!(
            parse_disk(&disabled, "/dev/sda", "ATA").assessment,
            "disabled"
        );

        let unavailable = serde_json::json!({
            "smart_support": {"available": false, "enabled": false}
        });
        assert_eq!(
            parse_disk(&unavailable, "/dev/sda", "ATA").assessment,
            "unavailable"
        );
    }
}
