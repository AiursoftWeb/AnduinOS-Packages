//! UFW statistics: kernel log parsing, listening ports, rule counters.

use std::process::Command;

use crate::i18n::i18n;

use super::types::UfwError;

/// A single blocked/allowed event from the kernel log.
#[derive(Debug, Clone)]
pub struct BlockedEvent {
    pub timestamp: String,
    pub src_ip: String,
    pub dst_ip: String,
    pub dst_port: String,
    pub protocol: String,
    pub action: String, // BLOCK, ALLOW
}

/// A listening port with associated process.
#[derive(Debug, Clone)]
pub struct ListeningPort {
    pub protocol: String,
    pub local_address: String,
    pub port: u16,
    pub process: String,
}


/// Read recent blocked/allowed events from the kernel log.
pub fn read_blocked_events(limit: usize) -> Result<Vec<BlockedEvent>, UfwError> {
    // Try journalctl first for kernel UFW messages
    let output = Command::new("journalctl")
        .env("LC_ALL", "C")
        .env("LANGUAGE", "C")
        .args(["-q", "-k", "-n", &limit.to_string(), "-o", "short-iso"])
        .output()
        .map_err(|e| UfwError {
            message: format!("{}: {e}", i18n("Failed to run journalctl")),
        })?;

    let text = String::from_utf8_lossy(&output.stdout);
    let mut events = Vec::new();

    for line in text.lines() {
        if let Some(event) = parse_kernel_log_line(line) {
            events.push(event);
        }
    }

    // If journalctl returned nothing, try /var/log/ufw.log
    if events.is_empty() {
        if let Ok(content) = std::fs::read_to_string("/var/log/ufw.log") {
            for line in content.lines().rev().take(limit) {
                if let Some(event) = parse_kernel_log_line(line) {
                    events.push(event);
                }
            }
            events.reverse();
        }
    }

    Ok(events)
}

/// Parse a kernel log line containing [UFW BLOCK] or [UFW ALLOW] or [UFW AUDIT].
fn parse_kernel_log_line(line: &str) -> Option<BlockedEvent> {
    if !line.contains("[UFW ") {
        return None;
    }

    // Extract timestamp (first field in short-iso or syslog format)
    let timestamp = line.split_whitespace().next().unwrap_or("").to_string();

    // Extract action: [UFW BLOCK], [UFW ALLOW], etc.
    let action = if line.contains("[UFW BLOCK]") {
        "BLOCK".to_string()
    } else if line.contains("[UFW ALLOW]") {
        "ALLOW".to_string()
    } else {
        return None;
    };

    let src_ip = extract_field(line, "SRC=");
    let dst_ip = extract_field(line, "DST=");
    let dst_port = extract_field(line, "DPT=");
    let protocol = extract_field(line, "PROTO=");

    Some(BlockedEvent {
        timestamp,
        src_ip,
        dst_ip,
        dst_port,
        protocol,
        action,
    })
}

/// Extract a field value from a UFW log line (e.g. "SRC=192.168.1.1" → "192.168.1.1").
fn extract_field(line: &str, key: &str) -> String {
    let start = match line.find(key) {
        Some(pos) => pos + key.len(),
        None => return String::new(),
    };
    let rest = &line[start..];
    rest.split_whitespace()
        .next()
        .unwrap_or("")
        .to_string()
}

/// Get the top N blocked connections from a list of events.
pub fn top_blocked_connections(events: &[BlockedEvent], limit: usize) -> Vec<(String, u64)> {
    let mut counts: std::collections::HashMap<String, u64> = std::collections::HashMap::new();
    for event in events {
        if event.action == "BLOCK" && !event.src_ip.is_empty() {
            let conn = format!("{} → {}:{} ({})", event.src_ip, event.dst_ip, event.dst_port, event.protocol);
            *counts.entry(conn).or_default() += 1;
        }
    }
    let mut sorted: Vec<_> = counts.into_iter().collect();
    sorted.sort_by(|a, b| b.1.cmp(&a.1));
    sorted.truncate(limit);
    sorted
}

/// Get the top N targeted destination ports from a list of events.
pub fn top_blocked_ports(events: &[BlockedEvent], limit: usize) -> Vec<(String, u64)> {
    let mut counts: std::collections::HashMap<String, u64> = std::collections::HashMap::new();
    for event in events {
        if event.action == "BLOCK" && !event.dst_port.is_empty() {
            *counts
                .entry(format!("{}/{}", event.dst_port, event.protocol))
                .or_default() += 1;
        }
    }
    let mut sorted: Vec<_> = counts.into_iter().collect();
    sorted.sort_by(|a, b| b.1.cmp(&a.1));
    sorted.truncate(limit);
    sorted
}

/// Read live listening ports via `ss -tulnp`.
pub fn read_listening_ports() -> Result<Vec<ListeningPort>, UfwError> {
    let output = Command::new("ss")
        .env("LC_ALL", "C")
        .env("LANGUAGE", "C")
        .args(["-tulnp"])
        .output()
        .map_err(|e| UfwError {
            message: format!("{}: {e}", i18n("Failed to run ss")),
        })?;

    let text = String::from_utf8_lossy(&output.stdout);
    let mut ports = Vec::new();

    for line in text.lines().skip(1) {
        // ss -tulnp output columns: Netid State Recv-Q Send-Q LocalAddress:Port PeerAddress:Port Process
        let fields: Vec<&str> = line.split_whitespace().collect();
        if fields.len() < 6 {
            continue;
        }

        let protocol = fields[0].to_string();
        let local = fields[4];

        // Parse LocalAddress:Port (IPv6 uses [addr]:port format)
        let (addr, port_str) = if local.starts_with('[') {
            // IPv6
            if let Some(pos) = local.rfind("]:") {
                (&local[1..pos], &local[pos + 2..])
            } else {
                continue;
            }
        } else {
            if let Some(pos) = local.rfind(':') {
                (&local[..pos], &local[pos + 1..])
            } else {
                continue;
            }
        };

        let port: u16 = port_str.parse().unwrap_or(0);
        if port == 0 {
            continue;
        }

        let process = if fields.len() >= 7 {
            // Last field is process info like users:(("sshd",pid=1234,fd=3))
            let proc_str = fields[6..].join(" ");
            extract_process_name(&proc_str)
        } else {
            String::new()
        };

        ports.push(ListeningPort {
            protocol,
            local_address: addr.to_string(),
            port,
            process,
        });
    }

    Ok(ports)
}

/// Extract process name from ss -tulnp "Process" column output.
fn extract_process_name(proc_str: &str) -> String {
    // Format: users:(("sshd",pid=1234,fd=3))
    if let Some(start) = proc_str.find("((\"") {
        let rest = &proc_str[start + 3..];
        if let Some(end) = rest.find('\"') {
            return rest[..end].to_string();
        }
    }
    // Simpler format: "sshd"
    proc_str.trim_matches(&['(', ')', '"'][..]).to_string()
}
