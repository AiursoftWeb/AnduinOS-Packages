use crate::world::history_safe;
use crate::HistoryEntry;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

const MAX_FILE_BYTES: u64 = 1_048_576;

pub(crate) fn state_path() -> Option<PathBuf> {
    if std::env::var("ANDUINOS_GUESS_HISTORY").as_deref() == Ok("0") {
        return None;
    }
    let root = std::env::var_os("XDG_STATE_HOME")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/state"))
        })?;
    Some(root.join("anduinos-bash-guess-command/history-v1"))
}

pub(crate) fn load(path: &Path) -> Vec<HistoryEntry> {
    let Ok(contents) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let mut entries: Vec<HistoryEntry> = Vec::new();
    for line in contents
        .lines()
        .rev()
        .take(8_000)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
    {
        let mut fields = line.splitn(4, '\t');
        let (Some(at), Some(count), Some(cwd), Some(command)) =
            (fields.next(), fields.next(), fields.next(), fields.next())
        else {
            continue;
        };
        let (Ok(last_used_ms), Ok(count), Some(cwd), Some(command)) = (
            at.parse::<u64>(),
            count.parse::<u32>(),
            decode_hex(cwd),
            decode_hex(command),
        ) else {
            continue;
        };
        if let Some(existing) = entries
            .iter_mut()
            .find(|entry| entry.command == command && entry.cwd == cwd)
        {
            existing.count = existing.count.saturating_add(count);
            existing.last_used_ms = existing.last_used_ms.max(last_used_ms);
        } else {
            entries.push(HistoryEntry {
                command,
                cwd,
                count,
                last_used_ms,
            });
        }
    }
    entries.sort_by_key(|entry| std::cmp::Reverse(entry.last_used_ms));
    entries.truncate(2_000);
    entries
}

pub(crate) fn load_bash_history() -> Vec<HistoryEntry> {
    let path = std::env::var_os("HISTFILE")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".bash_history")));
    let Some(path) = path else {
        return Vec::new();
    };
    let Ok(contents) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let lines: Vec<&str> = contents.lines().collect();
    let start = lines.len().saturating_sub(4_000);
    let mut entries: Vec<HistoryEntry> = Vec::new();
    let mut timestamp = 0;
    for line in &lines[start..] {
        if let Some(epoch) = line
            .strip_prefix('#')
            .and_then(|value| value.parse::<u64>().ok())
        {
            timestamp = epoch.saturating_mul(1_000);
            continue;
        }
        if !history_safe(line) {
            continue;
        }
        let command = line.trim().to_owned();
        if let Some(entry) = entries.iter_mut().find(|entry| entry.command == command) {
            entry.count = entry.count.saturating_add(1);
            entry.last_used_ms = entry.last_used_ms.max(timestamp);
        } else {
            entries.push(HistoryEntry {
                command,
                cwd: String::new(),
                count: 1,
                last_used_ms: timestamp,
            });
        }
    }
    entries.sort_by_key(|entry| {
        (
            std::cmp::Reverse(entry.count),
            std::cmp::Reverse(entry.last_used_ms),
        )
    });
    entries.truncate(1_000);
    entries
}

pub(crate) fn record(
    path: &Path,
    event: &HistoryEntry,
    snapshot: &[HistoryEntry],
) -> io::Result<()> {
    let Some(parent) = path.parent() else {
        return Ok(());
    };
    fs::create_dir_all(parent)?;
    fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(path)?;
    writeln!(
        file,
        "{}\t1\t{}\t{}",
        event.last_used_ms,
        encode_hex(&event.cwd),
        encode_hex(&event.command)
    )?;
    if file.metadata()?.len() <= MAX_FILE_BYTES {
        return Ok(());
    }
    drop(file);
    compact(path, snapshot)
}

fn compact(path: &Path, entries: &[HistoryEntry]) -> io::Result<()> {
    let temporary = path.with_extension(format!("tmp-{}", std::process::id()));
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .mode(0o600)
        .open(&temporary)?;
    for entry in entries.iter().take(2_000) {
        writeln!(
            file,
            "{}\t{}\t{}\t{}",
            entry.last_used_ms,
            entry.count,
            encode_hex(&entry.cwd),
            encode_hex(&entry.command)
        )?;
    }
    file.sync_all()?;
    fs::rename(temporary, path)
}

fn encode_hex(value: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value.bytes() {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 15) as usize] as char);
    }
    output
}

fn decode_hex(value: &str) -> Option<String> {
    if !value.len().is_multiple_of(2) {
        return None;
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        output.push((nibble(pair[0])? << 4) | nibble(pair[1])?);
    }
    String::from_utf8(output).ok()
}

fn nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_round_trip_is_protocol_safe() {
        let value = "git push '功能分支'\tmain";
        assert_eq!(decode_hex(&encode_hex(value)).as_deref(), Some(value));
    }

    #[test]
    fn credential_filter_applies_to_imported_history() {
        assert!(history_safe("git push origin main"));
        assert!(!history_safe(
            "curl --header 'Authorization: Bearer abc' host"
        ));
        assert!(!history_safe("API_TOKEN=abc deploy"));
    }
}
