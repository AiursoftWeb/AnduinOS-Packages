//! UFW backend: reads firewall state from config files (no root needed),
//! executes modifications via pkexec (root needed).
//!
//! Architecture (Solution B):
//!   - /etc/ufw/ufw.conf      → ENABLED status (world-readable)
//!   - /etc/default/ufw        → DEFAULT policies (world-readable)
//!   - /etc/ufw/user.rules     → IPv4 rules (made readable via Apkg postinst)
//!   - /etc/ufw/user6.rules    → IPv6 rules (made readable via Apkg postinst)
//!   - /etc/ufw/applications.d → App profiles (world-readable)
//!   - pkexec ufw ...          → All write operations

use std::collections::HashMap;
use std::fs;
use std::net;
use std::path::Path;
use std::process::Command;

use super::types::*;

const UFW_CONF: &str = "/etc/ufw/ufw.conf";
const UFW_DEFAULTS: &str = "/etc/default/ufw";
const UFW_USER_RULES: &str = "/etc/ufw/user.rules";
const UFW_USER6_RULES: &str = "/etc/ufw/user6.rules";
const UFW_APPS_DIR: &str = "/etc/ufw/applications.d";

// ─── Reading state (no root needed) ──────────────────────────────────────────

/// Read the complete firewall status via `pkexec ufw status verbose`.
/// Authenticates once at startup; polkit caches the authorization for subsequent calls.
pub fn read_status() -> Result<UfwStatus, UfwError> {
    let output = Command::new("pkexec")
        .env("LC_ALL", "C")
        .env("LANGUAGE", "C")
        .args(["/usr/sbin/ufw", "status", "verbose"])
        .output()
        .map_err(|e| UfwError {
            message: format!("Failed to run pkexec ufw status: {e}"),
        })?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        if output.status.code() == Some(126) {
            return Err(UfwError {
                message: "Authentication cancelled".to_string(),
            });
        }
        return Err(UfwError {
            message: format!("pkexec failed: {}", stderr.trim()),
        });
    }

    let text = String::from_utf8_lossy(&output.stdout);
    let status = parse_ufw_status_verbose(&text)?;
    if status.rules.is_empty() && status.active {
        eprintln!(
            "Warning: firewall is active but parsed 0 rules. Raw output:\n{}",
            text
        );
    }
    Ok(status)
}

/// Parse the output of `ufw status verbose`.
fn parse_ufw_status_verbose(output: &str) -> Result<UfwStatus, UfwError> {
    let mut active = false;
    let mut default_incoming = Policy::Deny;
    let mut default_outgoing = Policy::Allow;
    let mut logging = String::from("off");
    let mut rules = Vec::new();
    let mut in_rules = false;

    for line in output.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        if line.starts_with("Status:") {
            let val = line.split_whitespace().nth(1).unwrap_or("").to_lowercase();
            active = val == "active";
        } else if line.starts_with("Logging:") {
            // "Logging: on (low)" → extract "low" from parens, or use "on"/"off"
            let rest = line.strip_prefix("Logging:").unwrap_or("off").trim();
            if let Some(paren) = rest.find('(') {
                let inner = &rest[paren + 1..];
                if let Some(end) = inner.find(')') {
                    logging = inner[..end].to_string();
                }
            } else {
                logging = rest.to_string();
            }
        } else if line.starts_with("Default:") {
            let policy_str = line.split_whitespace().nth(1).unwrap_or("deny").to_lowercase();
            let dir_str = line.split_whitespace().last().unwrap_or("incoming").to_lowercase();
            if let Some(p) = Policy::from_str(&policy_str) {
                if dir_str == "incoming" {
                    default_incoming = p;
                } else if dir_str == "outgoing" {
                    default_outgoing = p;
                }
            }
        } else if line.starts_with("--") || (line.contains("---") && line.contains("Action")) {
            // Separator line (e.g. "--  ------  ----") or old-style header
            in_rules = true;
            continue;
        } else if !in_rules {
            continue;
        } else {
            // Rule line format (action-anchored to handle ports with spaces):
            //   Port-based:    22                     ALLOW IN    Anywhere
            //   Address-based: 1.2.3.4                DENY OUT    Anywhere
            //   Inbound addr:  Anywhere               DENY IN     5.6.7.8
            //
            // Strip trailing comment (e.g. "# Audit Block: wechat") so it
            // doesn't leak into the from/to fields.
            let clean_line = match line.find(" # ") {
                Some(pos) => &line[..pos],
                None => line,
            };
            let (first_col, rest) = split_at_action(clean_line);
            if let Some((action_str, remainder)) = rest.split_once(' ') {
                let (direction_str, from_str) = remainder.split_once(' ').unwrap_or((remainder, "Anywhere"));
                let is_v6 = clean_line.contains("(v6)");

                let action = Action::from_str(action_str).unwrap_or(Action::Allow);
                let direction = Direction::from_str(direction_str).unwrap_or(Direction::In);

                // Strip "(v6)" suffix from first column and source for clean display
                let first_col_clean = first_col.trim_end_matches(" (v6)");
                let from_clean = from_str.trim().trim_end_matches(" (v6)");

                // Detect address-based rules (vs port-based):
                //   Outbound: first column is the destination IP
                //   Inbound:  first column is "Anywhere", source column is the IP
                let to_is_ip = is_ip_or_cidr(first_col_clean);
                let from_is_ip = is_ip_or_cidr(&from_clean);
                let is_address_rule = to_is_ip
                    || (from_is_ip && (first_col_clean == "Anywhere" || first_col_clean.is_empty()));

                let rule_num = (rules.len() + 1) as u32;
                rules.push(UfwRule {
                    number: rule_num,
                    port: if is_address_rule { String::new() } else {
                        first_col.to_string()
                    },
                    action,
                    direction,
                    from: from_clean.to_string(),
                    to: if to_is_ip { first_col_clean.to_string() } else {
                        "Anywhere".to_string()
                    },
                    v6: is_v6,
                });
            }
        }
    }

    Ok(UfwStatus {
        active,
        default_incoming,
        default_outgoing,
        rules,
        logging,
    })
}

/// Check if a string looks like an IP address or CIDR notation.
/// e.g. "1.2.3.4", "2001:db8::1", "192.168.1.0/24"
fn is_ip_or_cidr(s: &str) -> bool {
    // Try IPv4
    if let Some(addr_part) = s.split('/').next() {
        if addr_part.parse::<net::Ipv4Addr>().is_ok() {
            return true;
        }
    }
    // Try IPv6
    if s.parse::<net::Ipv6Addr>().is_ok() {
        return true;
    }
    // Also match IPv4-ish patterns that might fail parse (old format)
    s.contains('.') && !s.chars().any(|c| c.is_alphabetic())
}

/// Split a rule line at the action keyword to handle ports with spaces.
/// e.g. "Nginx Full                ALLOW IN    Anywhere"
///   -> ("Nginx Full", "ALLOW IN    Anywhere")
fn split_at_action(line: &str) -> (String, String) {
    for keyword in &[" ALLOW ", " DENY ", " REJECT ", " LIMIT "] {
        if let Some(idx) = line.find(keyword) {
            let port = line[..idx].trim().to_string();
            let rest = line[idx..].trim().to_string();
            return (port, rest);
        }
    }
    // Fallback: split by whitespace
    let (port, rest) = line.split_once(' ').unwrap_or((line, ""));
    (port.to_string(), rest.to_string())
}

/// Check if UFW is enabled by reading /etc/ufw/ufw.conf.
fn read_enabled() -> Result<bool, UfwError> {
    let content = fs::read_to_string(UFW_CONF).map_err(|e| UfwError {
        message: format!("Cannot read {UFW_CONF}: {e}"),
    })?;

    for line in content.lines() {
        let line = line.trim();
        if line.starts_with("ENABLED=") {
            let val = line.strip_prefix("ENABLED=").unwrap_or("no");
            return Ok(val.eq_ignore_ascii_case("yes"));
        }
    }

    Ok(false)
}

/// Read logging level from /etc/ufw/ufw.conf.
fn read_logging() -> Result<String, UfwError> {
    let content = fs::read_to_string(UFW_CONF).unwrap_or_default();

    for line in content.lines() {
        let line = line.trim();
        if line.starts_with("LOGLEVEL=") {
            let val = line.strip_prefix("LOGLEVEL=").unwrap_or("off");
            return Ok(val.to_string());
        }
    }
    Ok("off".to_string())
}

/// Read default policies from /etc/default/ufw.
fn read_defaults() -> Result<(Policy, Policy), UfwError> {
    let content = fs::read_to_string(UFW_DEFAULTS).map_err(|e| UfwError {
        message: format!("Cannot read {UFW_DEFAULTS}: {e}"),
    })?;

    let mut incoming = Policy::Deny;
    let mut outgoing = Policy::Allow;

    for line in content.lines() {
        let line = line.trim();
        if line.starts_with("DEFAULT_INPUT_POLICY=") {
            let val = line
                .strip_prefix("DEFAULT_INPUT_POLICY=")
                .unwrap_or("")
                .trim_matches('"');
            if let Some(p) = Policy::from_str(val) {
                incoming = p;
            }
        } else if line.starts_with("DEFAULT_OUTPUT_POLICY=") {
            let val = line
                .strip_prefix("DEFAULT_OUTPUT_POLICY=")
                .unwrap_or("")
                .trim_matches('"');
            if let Some(p) = Policy::from_str(val) {
                outgoing = p;
            }
        }
    }

    Ok((incoming, outgoing))
}

/// Parse rules from a user.rules file by reading `### tuple ###` comment lines.
///
/// Format: `### tuple ### <action> <protocol> <dport> <dst> <sport> <src> <direction>`
fn parse_rules_file(path: &str, v6: bool) -> Result<Vec<UfwRule>, UfwError> {
    let content = fs::read_to_string(path).map_err(|e| UfwError {
        message: format!("Cannot read {path}: {e}"),
    })?;

    let mut rules = Vec::new();
    let mut in_rules = false;

    for line in content.lines() {
        let line = line.trim();

        if line == "### RULES ###" {
            in_rules = true;
            continue;
        }
        if line == "### END RULES ###" {
            break;
        }
        if !in_rules {
            continue;
        }

        if let Some(tuple) = line.strip_prefix("### tuple ###") {
            if let Some(rule) = parse_tuple(tuple.trim(), v6) {
                rules.push(rule);
            }
        }
    }

    Ok(rules)
}

/// Parse a single `### tuple ###` comment line into a UfwRule.
///
/// Format: `<action> <protocol> <dport> <dst> <sport> <src> <direction> [comment=...]`
fn parse_tuple(tuple: &str, v6: bool) -> Option<UfwRule> {
    let parts: Vec<&str> = tuple.split_whitespace().collect();
    if parts.len() < 7 {
        return None;
    }

    let action = Action::from_str(parts[0])?;
    let protocol = Protocol::from_str(parts[1]).unwrap_or(Protocol::Both);
    let dport = parts[2]; // destination port
    let _dst = parts[3]; // destination address
    let _sport = parts[4]; // source port
    let src = parts[5]; // source address
    let direction = Direction::from_str(parts[6]).unwrap_or(Direction::In);

    // Build port display string
    let port = match protocol {
        Protocol::Both => dport.to_string(),
        Protocol::Tcp => {
            if dport == "any" {
                "any".to_string()
            } else {
                format!("{dport}/tcp")
            }
        }
        Protocol::Udp => {
            if dport == "any" {
                "any".to_string()
            } else {
                format!("{dport}/udp")
            }
        }
    };

    // Build source display string
    let from = if src == "0.0.0.0/0" || src == "::/0" {
        if v6 {
            "Anywhere (v6)".to_string()
        } else {
            "Anywhere".to_string()
        }
    } else {
        src.to_string()
    };

    // Build destination display string
    let to = if _dst == "0.0.0.0/0" || _dst == "::/0" {
        if v6 {
            "Anywhere (v6)".to_string()
        } else {
            "Anywhere".to_string()
        }
    } else {
        _dst.to_string()
    };

    Some(UfwRule {
        number: 0, // renumbered later
        port,
        action,
        direction,
        from,
        to,
        v6,
    })
}

// ─── Reading app profiles (no root needed) ───────────────────────────────────

/// Read all application profiles from system and bundled directories.
pub fn read_profiles() -> Result<Vec<AppProfile>, UfwError> {
    let mut profiles = Vec::new();

    // Read from system profiles (/etc/ufw/applications.d/)
    read_profiles_from_dir(UFW_APPS_DIR, &mut profiles);

    // Read from bundled profiles (/usr/share/ufwall-gtk/app_profiles/)
    read_profiles_from_dir(crate::config::APP_PROFILES_DIR, &mut profiles);

    // Deduplicate by name: system-installed profiles take priority (already added first)
    profiles.sort_by(|a, b| a.name.cmp(&b.name));
    profiles.dedup_by(|a, b| a.name.eq_ignore_ascii_case(&b.name));

    Ok(profiles)
}

/// Read profiles from a directory into the given vector.
fn read_profiles_from_dir(dir_path: &str, profiles: &mut Vec<AppProfile>) {
    let dir = Path::new(dir_path);
    if !dir.exists() {
        return;
    }
    if let Ok(entries) = fs::read_dir(dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() {
                if let Ok(content) = fs::read_to_string(&path) {
                    let mut file_profiles = parse_app_profiles(&content);
                    profiles.append(&mut file_profiles);
                }
            }
        }
    }
}

/// Parse an INI-style application profile file.
///
/// Format:
/// ```ini
/// [AppName]
/// title=Human Readable Title
/// description=What this app does
/// ports=80,443/tcp
/// ```
fn parse_app_profiles(content: &str) -> Vec<AppProfile> {
    let mut profiles = Vec::new();
    let mut current_section: Option<String> = None;
    let mut fields: HashMap<String, String> = HashMap::new();

    for line in content.lines() {
        let line = line.trim();

        // Skip comments and empty lines
        if line.is_empty() || line.starts_with('#') {
            continue;
        }

        // Section header: [AppName]
        if line.starts_with('[') && line.ends_with(']') {
            // Save previous section
            if let Some(name) = current_section.take() {
                profiles.push(AppProfile {
                    name,
                    title: fields.remove("title").unwrap_or_default(),
                    description: fields.remove("description").unwrap_or_default(),
                    ports: fields.remove("ports").unwrap_or_default(),
                });
                fields.clear();
            }
            current_section = Some(line[1..line.len() - 1].to_string());
            continue;
        }

        // Key=value
        if let Some((key, value)) = line.split_once('=') {
            fields.insert(key.trim().to_lowercase(), value.trim().to_string());
        }
    }

    // Save last section
    if let Some(name) = current_section.take() {
        profiles.push(AppProfile {
            name,
            title: fields.remove("title").unwrap_or_default(),
            description: fields.remove("description").unwrap_or_default(),
            ports: fields.remove("ports").unwrap_or_default(),
        });
    }

    profiles
}

// ─── Writing operations (require pkexec) ─────────────────────────────────────

/// Enable or disable the firewall via `pkexec ufw enable/disable`.
pub fn set_enabled(enabled: bool) -> Result<String, UfwError> {
    let arg = if enabled { "enable" } else { "disable" };
    run_pkexec_ufw(&["--force", arg])
}

/// Delete all custom rules without disabling the firewall.
pub fn delete_all_rules() -> Result<(), UfwError> {
    let status = read_status()?;
    let mut numbers: Vec<u32> = status.rules.iter().map(|r| r.number).collect();
    numbers.sort_by(|a, b| b.cmp(a)); // Sort descending

    for num in numbers {
        run_pkexec_ufw(&["--force", "delete", &num.to_string()])?;
    }
    Ok(())
}

/// Set the UFW logging level.
pub fn set_logging(level: &str) -> Result<String, UfwError> {
    run_pkexec_ufw(&["logging", level])
}

/// Set the default policy for a direction.
pub fn set_default_policy(direction: Direction, policy: Policy) -> Result<String, UfwError> {
    run_pkexec_ufw(&["default", policy.as_ufw_arg(), direction.as_ufw_arg()])
}

/// Add a new firewall rule.
pub fn add_rule(params: &RuleParams) -> Result<String, UfwError> {
    let mut args: Vec<String> = Vec::new();

    // Insert position: "insert N" must come first
    if let Some(pos) = params.insert_position {
        args.push("insert".to_string());
        args.push(pos.to_string());
    }

    // Action
    args.push(params.action.as_ufw_arg().to_string());

    // Direction (optional) — used for on-interface binding and rule direction
    let has_dir = params.direction.is_some();
    if let Some(dir) = &params.direction {
        args.push(dir.as_ufw_arg().to_string());
    }

    // From clause
    if let Some(from) = &params.from {
        if !from.is_empty() {
            args.push("from".to_string());
            args.push(from.clone());
        }
    }

    // To clause
    if let Some(to) = &params.to {
        if !to.is_empty() {
            args.push("to".to_string());
            args.push(to.clone());
        }
    }

    // Port with optional protocol
    if !params.port.is_empty() {
        if params.from.is_some() || params.to.is_some() {
            args.push("port".to_string());
            args.push(params.port.clone());
        } else {
            let port_str = match &params.protocol {
                Some(Protocol::Tcp) => format!("{}/tcp", params.port),
                Some(Protocol::Udp) => format!("{}/udp", params.port),
                _ => params.port.clone(),
            };
            args.push(port_str);
        }
    }

    // Protocol (when using from/to syntax)
    if (params.from.is_some() || params.to.is_some()) && params.protocol.is_some() {
        if let Some(proto) = params.protocol.as_ref().and_then(|p| p.as_ufw_arg()) {
            args.push("proto".to_string());
            args.push(proto.to_string());
        }
    }

    // Interface binding: direction on <iface>
    if let Some(iface) = &params.interface {
        if !iface.is_empty() {
            if has_dir {
                // Direction already specified; add "on <iface>" after it
                args.push("on".to_string());
                args.push(iface.clone());
            } else {
                // No explicit direction — UFW defaults apply
                args.push("on".to_string());
                args.push(iface.clone());
            }
        }
    }

    // Comment
    if let Some(comment) = &params.comment {
        if !comment.is_empty() {
            args.push("comment".to_string());
            args.push(comment.clone());
        }
    }

    let args_refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    run_pkexec_ufw(&args_refs)
}

/// Delete a rule by its number.
pub fn delete_rule(number: u32) -> Result<String, UfwError> {
    run_pkexec_ufw(&["--force", "delete", &number.to_string()])
}

/// Allow an application profile by its ports.
/// Calls `pkexec ufw allow` per port spec; polkit caches auth after first call.
pub fn allow_app(ports: &str) -> Result<String, UfwError> {
    let port_list: Vec<&str> = ports.split('|').map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
    if port_list.is_empty() {
        return Err(UfwError {
            message: "No ports defined for this profile".to_string(),
        });
    }
    let mut last_result = Ok(String::new());
    for port_spec in &port_list {
        last_result = run_pkexec_ufw(&["allow", port_spec]);
        if last_result.is_err() {
            return last_result;
        }
    }
    last_result
}

/// Delete an application profile allow rule by its ports.
pub fn delete_app(ports: &str) -> Result<String, UfwError> {
    let port_list: Vec<&str> = ports.split('|').map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
    if port_list.is_empty() {
        return Err(UfwError {
            message: "No ports defined for this profile".to_string(),
        });
    }
    let mut last_result = Ok(String::new());
    for port_spec in &port_list {
        last_result = run_pkexec_ufw(&["--force", "delete", "allow", port_spec]);
    }
    last_result
}

/// Check if an app profile is currently allowed by checking the rules.
/// Uses port-based matching: compares rule ports against profile port specs.
pub fn is_app_allowed(rules: &[UfwRule], profile: &AppProfile) -> bool {
    // Parse profile ports into individual specs
    let profile_ports = parse_profile_ports(&profile.ports);
    if profile_ports.is_empty() {
        // Fallback: no ports field — use name matching
        let name_lower = profile.name.to_lowercase();
        return rules.iter().any(|r| {
            r.port.to_lowercase() == name_lower && r.action == Action::Allow
        });
    }
    // Port-based matching
    rules.iter().any(|r| {
        r.action == Action::Allow
            && profile_ports.iter().any(|pp| ports_match(&r.port, pp))
    })
}

/// Split profile ports string by `|` or `,` into individual port specs.
fn parse_profile_ports(ports: &str) -> Vec<String> {
    ports
        .split(&['|', ','][..])
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

/// Compare a rule port (e.g. "22/tcp") with a profile port spec (e.g. "22/tcp" or "22").
fn ports_match(rule_port: &str, profile_port: &str) -> bool {
    let rp = rule_port.to_lowercase();
    let pp = profile_port.to_lowercase();
    if rp == pp {
        return true;
    }
    // If profile port has no protocol suffix, match on port number only
    if !pp.contains('/') {
        let rule_port_num = rp.split('/').next().unwrap_or(&rp);
        return rule_port_num == pp;
    }
    // If rule port has no protocol suffix but profile port does
    if !rp.contains('/') {
        let profile_port_num = pp.split('/').next().unwrap_or(&pp);
        return rp == profile_port_num;
    }
    false
}

// ─── Internal helpers ────────────────────────────────────────────────────────



/// Run `pkexec ufw <args>` and return stdout.
fn run_pkexec_ufw(args: &[&str]) -> Result<String, UfwError> {
    let mut cmd_args = vec!["/usr/sbin/ufw"];
    cmd_args.extend_from_slice(args);

    let output = Command::new("pkexec")
        .env("LC_ALL", "C")
        .env("LANGUAGE", "C")
        .args(&cmd_args)
        .output()
        .map_err(|e| UfwError {
            message: format!("Failed to execute pkexec: {e}"),
        })?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        // pkexec returns 126 when user dismisses the dialog
        if output.status.code() == Some(126) {
            Err(UfwError {
                message: "Authentication cancelled".to_string(),
            })
        } else {
            Err(UfwError {
                message: format!(
                    "UFW command failed: {}",
                    if stderr.is_empty() {
                        stdout.to_string()
                    } else {
                        stderr.to_string()
                    }
                ),
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_tuple_allow_tcp() {
        let rule = parse_tuple("allow tcp 22 0.0.0.0/0 any 0.0.0.0/0 in", false).unwrap();
        assert_eq!(rule.port, "22/tcp");
        assert_eq!(rule.action, Action::Allow);
        assert_eq!(rule.direction, Direction::In);
        assert_eq!(rule.from, "Anywhere");
        assert!(!rule.v6);
    }

    #[test]
    fn test_parse_tuple_deny_any() {
        let rule = parse_tuple("deny any 80 0.0.0.0/0 any 192.168.1.0/24 in", false).unwrap();
        assert_eq!(rule.port, "80");
        assert_eq!(rule.action, Action::Deny);
        assert_eq!(rule.from, "192.168.1.0/24");
    }

    #[test]
    fn test_parse_tuple_v6() {
        let rule = parse_tuple("allow tcp 443 ::/0 any ::/0 in", true).unwrap();
        assert_eq!(rule.port, "443/tcp");
        assert_eq!(rule.from, "Anywhere (v6)");
        assert!(rule.v6);
    }

    #[test]
    fn test_parse_tuple_short() {
        assert!(parse_tuple("allow tcp", false).is_none());
    }

    #[test]
    fn test_parse_app_profiles() {
        let content = r#"
[CUPS]
title=Common UNIX Printing System server
description=CUPS is a printing system with support for IPP
ports=631

[OpenSSH]
title=Secure Shell
description=OpenSSH server
ports=22/tcp
"#;
        let profiles = parse_app_profiles(content);
        assert_eq!(profiles.len(), 2);
        assert_eq!(profiles[0].name, "CUPS");
        assert_eq!(profiles[0].ports, "631");
        assert_eq!(profiles[1].name, "OpenSSH");
        assert_eq!(profiles[1].ports, "22/tcp");
    }

    #[test]
    fn test_policy_from_str() {
        assert_eq!(Policy::from_str("ALLOW"), Some(Policy::Allow));
        assert_eq!(Policy::from_str("DROP"), Some(Policy::Deny));
        assert_eq!(Policy::from_str("REJECT"), Some(Policy::Reject));
        assert_eq!(Policy::from_str("invalid"), None);
    }
    #[test]
    fn test_parse_ufw_status_verbose() {
        let output = r"Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), deny (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22                         ALLOW IN    Anywhere
80/tcp                     ALLOW IN    Anywhere
Nginx Full                 ALLOW IN    Anywhere
53/udp                     ALLOW IN    Anywhere
53/tcp                     ALLOW IN    Anywhere
22 (v6)                    ALLOW IN    Anywhere (v6)
53/udp (v6)                ALLOW IN    Anywhere (v6)
1.2.3.4                    DENY OUT    Anywhere                   # Audit Block: wechat
";
        let result = parse_ufw_status_verbose(output).unwrap();
        assert!(result.active);
        assert_eq!(result.logging, "low");
        assert_eq!(result.rules.len(), 8);
        // Port-based rules unchanged
        assert_eq!(result.rules[0].port, "22");
        assert_eq!(result.rules[1].port, "80/tcp");
        assert_eq!(result.rules[2].port, "Nginx Full");
        assert_eq!(result.rules[3].port, "53/udp");
        assert_eq!(result.rules[5].v6, true);
        // Address-based outbound rule: IP goes to `to`, port is empty, comment stripped
        let addr_rule = &result.rules[7];
        assert_eq!(addr_rule.port, "");
        assert_eq!(addr_rule.to, "1.2.3.4");
        assert_eq!(addr_rule.from, "Anywhere");
        assert_eq!(addr_rule.direction, Direction::Out);
        assert_eq!(addr_rule.action, Action::Deny);
    }

    #[test]
    fn test_parse_address_rule_inbound() {
        // ufw deny in from 5.6.7.8 → "Anywhere  DENY IN  5.6.7.8"
        let output = r"Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), deny (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
Anywhere                   DENY IN     5.6.7.8
";
        let result = parse_ufw_status_verbose(output).unwrap();
        assert_eq!(result.rules.len(), 1);
        let rule = &result.rules[0];
        assert_eq!(rule.port, ""); // address rule: no port
        assert_eq!(rule.to, "Anywhere");
        assert_eq!(rule.from, "5.6.7.8");
        assert_eq!(rule.direction, Direction::In);
        assert_eq!(rule.action, Action::Deny);
    }

    #[test]
    fn test_parse_address_rule_comment_stripped() {
        // Comment after " # " must not leak into from/to
        let output = r"Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), deny (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
1.2.3.4                    DENY OUT    Anywhere                   # Audit Block: wechat
";
        let result = parse_ufw_status_verbose(output).unwrap();
        assert_eq!(result.rules.len(), 1);
        let rule = &result.rules[0];
        assert_eq!(rule.port, "");
        assert_eq!(rule.to, "1.2.3.4");
        // Comment must not leak:
        assert_eq!(rule.from, "Anywhere");
        assert!(!rule.from.contains('#'));
        assert!(!rule.from.contains("wechat"));
    }

    #[test]
    fn test_is_ip_or_cidr() {
        // IPv4
        assert!(is_ip_or_cidr("1.2.3.4"));
        assert!(is_ip_or_cidr("192.168.1.1"));
        // CIDR
        assert!(is_ip_or_cidr("192.168.1.0/24"));
        // IPv6
        assert!(is_ip_or_cidr("2001:db8::1"));
        assert!(is_ip_or_cidr("::1"));
        // Not IPs
        assert!(!is_ip_or_cidr("22"));
        assert!(!is_ip_or_cidr("80/tcp"));
        assert!(!is_ip_or_cidr("Nginx Full"));
        assert!(!is_ip_or_cidr("Anywhere"));
        assert!(!is_ip_or_cidr(""));
    }

    #[test]
    fn test_ufw_rule_title_subtitle_address() {
        // Outbound deny to IP
        let rule = UfwRule {
            number: 1, port: "".into(), action: Action::Deny,
            direction: Direction::Out, from: "Anywhere".into(),
            to: "1.2.3.4".into(), v6: false,
        };
        assert_eq!(rule.title(), "1.2.3.4");
        assert_eq!(rule.subtitle(), "DENY OUT to 1.2.3.4");

        // Inbound deny from IP
        let rule = UfwRule {
            number: 2, port: "".into(), action: Action::Deny,
            direction: Direction::In, from: "5.6.7.8".into(),
            to: "Anywhere".into(), v6: false,
        };
        assert_eq!(rule.title(), "5.6.7.8");
        assert_eq!(rule.subtitle(), "DENY IN from 5.6.7.8");

        // Port-based rule (unchanged behavior)
        let rule = UfwRule {
            number: 3, port: "22".into(), action: Action::Allow,
            direction: Direction::In, from: "Anywhere".into(),
            to: "Anywhere".into(), v6: false,
        };
        assert_eq!(rule.title(), "22");
        assert_eq!(rule.subtitle(), "ALLOW IN");

        // Port-based with from restriction
        let rule = UfwRule {
            number: 4, port: "22".into(), action: Action::Allow,
            direction: Direction::In, from: "192.168.1.0/24".into(),
            to: "Anywhere".into(), v6: false,
        };
        assert_eq!(rule.title(), "22");
        assert_eq!(rule.subtitle(), "ALLOW IN from 192.168.1.0/24");

        // v6 rule
        let rule = UfwRule {
            number: 5, port: "".into(), action: Action::Deny,
            direction: Direction::Out, from: "Anywhere".into(),
            to: "::1".into(), v6: true,
        };
        assert_eq!(rule.title(), "::1 (v6)");
    }

    #[test]
    fn test_parse_ufw_status_mixed_rules() {
        // Verify existing port rules still work alongside address rules
        let output = r"Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), deny (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22                         ALLOW IN    Anywhere
1.2.3.4                    DENY OUT    Anywhere                   # Audit Block: app
Anywhere                   DENY IN     5.6.7.8
80/tcp                     ALLOW IN    192.168.1.0/24
";
        let result = parse_ufw_status_verbose(output).unwrap();
        assert_eq!(result.rules.len(), 4);

        // Rule 0: port-based allow SSH
        assert_eq!(result.rules[0].port, "22");
        assert_eq!(result.rules[0].to, "Anywhere");
        assert_eq!(result.rules[0].from, "Anywhere");

        // Rule 1: outbound deny to IP
        assert_eq!(result.rules[1].port, "");
        assert_eq!(result.rules[1].to, "1.2.3.4");
        assert_eq!(result.rules[1].from, "Anywhere");
        assert_eq!(result.rules[1].action, Action::Deny);
        assert_eq!(result.rules[1].direction, Direction::Out);

        // Rule 2: inbound deny from IP
        assert_eq!(result.rules[2].port, "");
        assert_eq!(result.rules[2].to, "Anywhere");
        assert_eq!(result.rules[2].from, "5.6.7.8");
        assert_eq!(result.rules[2].direction, Direction::In);

        // Rule 3: port-based with source restriction
        assert_eq!(result.rules[3].port, "80/tcp");
        assert_eq!(result.rules[3].from, "192.168.1.0/24");
    }
}
