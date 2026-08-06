//! Parser for APT's structured history log.
//!
//! This intentionally does not parse `term.log` or command output: those are
//! human-facing and may be localized. Package state remains authoritative in
//! the `dpkg-query` capture stored with each system snapshot; APT history only
//! explains the transactions between two captures.

use chrono::NaiveDateTime;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum AptOperation {
    Install,
    Upgrade,
    Downgrade,
    Remove,
    Purge,
    Reinstall,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AptPackageChange {
    pub operation: AptOperation,
    pub package: String,
    pub old_version: Option<String>,
    pub new_version: Option<String>,
    pub automatic: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AptTransaction {
    pub start: NaiveDateTime,
    pub end: Option<NaiveDateTime>,
    pub command_line: Option<String>,
    pub requested_by: Option<String>,
    pub changes: Vec<AptPackageChange>,
}

/// Parse complete and interrupted transactions from an APT history file.
///
/// Unknown fields are ignored for forward compatibility. A malformed known
/// field rejects only its transaction block, so one damaged record does not
/// hide the remaining history.
pub fn parse_apt_history(contents: &str) -> AptHistoryReport {
    let mut report = AptHistoryReport::default();
    for (index, block) in contents.split("\n\n").enumerate() {
        if block.trim().is_empty() {
            continue;
        }
        match parse_transaction(block) {
            Ok(transaction) => report.transactions.push(transaction),
            Err(message) => report.issues.push(AptHistoryIssue {
                block: index + 1,
                message,
            }),
        }
    }
    report
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct AptHistoryReport {
    pub transactions: Vec<AptTransaction>,
    pub issues: Vec<AptHistoryIssue>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AptHistoryIssue {
    pub block: usize,
    pub message: String,
}

fn parse_transaction(block: &str) -> Result<AptTransaction, String> {
    let mut start = None;
    let mut end = None;
    let mut command_line = None;
    let mut requested_by = None;
    let mut changes = Vec::new();

    for line in block.lines() {
        let Some((field, value)) = line.split_once(':') else {
            continue;
        };
        let value = value.trim();
        match field {
            "Start-Date" => start = Some(parse_timestamp(value)?),
            "End-Date" => end = Some(parse_timestamp(value)?),
            "Commandline" => command_line = bounded_text(value, 4096),
            "Requested-By" => requested_by = bounded_text(value, 256),
            "Install" => parse_changes(value, AptOperation::Install, &mut changes)?,
            "Upgrade" => parse_changes(value, AptOperation::Upgrade, &mut changes)?,
            "Downgrade" => parse_changes(value, AptOperation::Downgrade, &mut changes)?,
            "Remove" => parse_changes(value, AptOperation::Remove, &mut changes)?,
            "Purge" => parse_changes(value, AptOperation::Purge, &mut changes)?,
            "Reinstall" => parse_changes(value, AptOperation::Reinstall, &mut changes)?,
            _ => {}
        }
    }

    let start = start.ok_or_else(|| "APT transaction has no valid Start-Date".to_string())?;
    if end.is_some_and(|end| end < start) {
        return Err("APT transaction ends before it starts".into());
    }
    Ok(AptTransaction {
        start,
        end,
        command_line,
        requested_by,
        changes,
    })
}

fn parse_timestamp(value: &str) -> Result<NaiveDateTime, String> {
    NaiveDateTime::parse_from_str(value, "%Y-%m-%d  %H:%M:%S")
        .map_err(|_| "APT transaction contains an invalid timestamp".into())
}

fn parse_changes(
    value: &str,
    operation: AptOperation,
    destination: &mut Vec<AptPackageChange>,
) -> Result<(), String> {
    for item in split_package_entries(value)? {
        let (package, details) = item
            .split_once(" (")
            .ok_or_else(|| "APT package change has no version details".to_string())?;
        if !valid_package_name(package) || !details.ends_with(')') {
            return Err("APT package change contains an invalid package name or tuple".into());
        }
        let values: Vec<&str> = details[..details.len() - 1]
            .split(',')
            .map(str::trim)
            .collect();
        let automatic = values.contains(&"automatic");
        let versions: Vec<&str> = values
            .into_iter()
            .filter(|item| *item != "automatic")
            .collect();
        if versions.is_empty() || versions.len() > 2 || versions.iter().any(|item| item.is_empty())
        {
            return Err("APT package change contains invalid version details".into());
        }
        let (old_version, new_version) = match operation {
            AptOperation::Upgrade | AptOperation::Downgrade if versions.len() == 2 => {
                (Some(versions[0].into()), Some(versions[1].into()))
            }
            AptOperation::Remove | AptOperation::Purge => (Some(versions[0].into()), None),
            _ => (None, Some(versions[versions.len() - 1].into())),
        };
        destination.push(AptPackageChange {
            operation,
            package: package.into(),
            old_version,
            new_version,
            automatic,
        });
    }
    Ok(())
}

fn split_package_entries(value: &str) -> Result<Vec<&str>, String> {
    let mut entries = Vec::new();
    let mut depth = 0_u8;
    let mut start = 0;
    for (index, character) in value.char_indices() {
        match character {
            '(' => depth = depth.saturating_add(1),
            ')' => {
                depth = depth
                    .checked_sub(1)
                    .ok_or_else(|| "APT package list has unbalanced parentheses".to_string())?;
            }
            ',' if depth == 0 => {
                entries.push(value[start..index].trim());
                start = index + 1;
            }
            _ => {}
        }
    }
    if depth != 0 {
        return Err("APT package list has unbalanced parentheses".into());
    }
    let final_entry = value[start..].trim();
    if !final_entry.is_empty() {
        entries.push(final_entry);
    }
    Ok(entries)
}

fn valid_package_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 255
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'-' | b'.' | b':' | b'_')
        })
}

fn bounded_text(value: &str, maximum: usize) -> Option<String> {
    (!value.is_empty()
        && value.len() <= maximum
        && !value.chars().any(|character| character.is_control()))
    .then(|| value.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_debian_versions_automatic_packages_and_requester() {
        let report = parse_apt_history(
            "Start-Date: 2026-08-02  12:34:43\n\
             Commandline: apt upgrade\n\
             Requested-By: anduin (1000)\n\
             Install: python3-fido2:amd64 (2.0.0-1, automatic)\n\
             Upgrade: libc6:amd64 (2.43-2ubuntu2, 2.43-2ubuntu2.3), firefox-anduinos:amd64 (1:2.0+153.0~build1-1, 1:2.0+153.0.1~build2-1)\n\
             End-Date: 2026-08-02  12:36:08\n",
        );
        assert!(report.issues.is_empty());
        let transaction = &report.transactions[0];
        assert_eq!(transaction.command_line.as_deref(), Some("apt upgrade"));
        assert_eq!(transaction.requested_by.as_deref(), Some("anduin (1000)"));
        assert_eq!(transaction.changes.len(), 3);
        assert!(transaction.changes[0].automatic);
        assert_eq!(
            transaction.changes[1].old_version.as_deref(),
            Some("2.43-2ubuntu2")
        );
        assert_eq!(
            transaction.changes[2].new_version.as_deref(),
            Some("1:2.0+153.0.1~build2-1")
        );
    }

    #[test]
    fn retains_interrupted_transactions_without_an_end_date() {
        let report = parse_apt_history(
            "Start-Date: 2026-08-04  07:00:00\n\
             Commandline: apt install example\n\
             Install: example:amd64 (1.0-1)\n",
        );
        assert!(report.issues.is_empty());
        assert!(report.transactions[0].end.is_none());
    }

    #[test]
    fn one_malformed_block_does_not_hide_valid_history() {
        let report = parse_apt_history(
            "Start-Date: not-a-date\nInstall: bad (1.0)\n\n\
             Start-Date: 2026-08-04  08:00:00\nRemove: good:amd64 (1.0-1)\nEnd-Date: 2026-08-04  08:00:01\n",
        );
        assert_eq!(report.issues.len(), 1);
        assert_eq!(report.transactions.len(), 1);
        assert_eq!(report.transactions[0].changes[0].package, "good:amd64");
    }
}
