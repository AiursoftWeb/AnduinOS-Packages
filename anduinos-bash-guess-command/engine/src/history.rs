use crate::world::history_safe;
use crate::{HistoryEntry, TransitionEntry};
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

const MAX_FILE_BYTES: u64 = 1_048_576;
const MAX_HISTORY_IMPORT_BYTES: u64 = 1_048_576;
const O_CLOEXEC: i32 = 0o2_000_000;
const O_NOFOLLOW: i32 = 0o400_000;
const O_NONBLOCK: i32 = 0o4_000;
const LOCK_EX: i32 = 2;

pub(crate) fn enabled() -> bool {
    std::env::var("ANDUINOS_GUESS_HISTORY").as_deref() != Ok("0")
}

fn persistence_enabled() -> bool {
    enabled() && std::env::var("ANDUINOS_GUESS_PERSIST").as_deref() == Ok("1")
}

pub(crate) fn state_path() -> Option<PathBuf> {
    if !persistence_enabled() {
        return None;
    }
    let root = std::env::var_os("XDG_STATE_HOME")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/state"))
        })?;
    Some(root.join("anduinos-bash-guess-command/history-v1"))
}

pub(crate) fn transition_state_path() -> Option<PathBuf> {
    state_path().map(|path| path.with_file_name("transitions-v1"))
}

pub(crate) fn load(path: &Path) -> Vec<HistoryEntry> {
    let Some(contents) = read_tail_text(path, MAX_FILE_BYTES) else {
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
    if !enabled() {
        return Vec::new();
    }
    let Some(path) = bash_history_path() else {
        return Vec::new();
    };
    let Some(contents) = read_tail_text(&path, MAX_HISTORY_IMPORT_BYTES) else {
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

pub(crate) fn load_transitions(path: &Path) -> Vec<TransitionEntry> {
    let Some(contents) = read_tail_text(path, MAX_FILE_BYTES) else {
        return Vec::new();
    };
    let mut entries: Vec<TransitionEntry> = Vec::new();
    for line in contents
        .lines()
        .rev()
        .take(8_000)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
    {
        let mut fields = line.splitn(5, '\t');
        let (Some(at), Some(count), Some(cwd), Some(previous), Some(next)) = (
            fields.next(),
            fields.next(),
            fields.next(),
            fields.next(),
            fields.next(),
        ) else {
            continue;
        };
        let (Ok(last_used_ms), Ok(count), Some(cwd), Some(previous), Some(next)) = (
            at.parse::<u64>(),
            count.parse::<u32>(),
            decode_hex(cwd),
            decode_hex(previous),
            decode_hex(next),
        ) else {
            continue;
        };
        merge_transition(
            &mut entries,
            TransitionEntry {
                previous,
                next,
                cwd,
                count,
                last_used_ms,
            },
        );
    }
    entries.sort_by_key(|entry| std::cmp::Reverse(entry.last_used_ms));
    entries.truncate(2_000);
    entries
}

/// Imports zsh-autosuggestions' useful `match_prev_cmd` signal from Bash's
/// chronological history. Exit status and cwd are unavailable here, so these
/// entries deliberately receive weaker ranking than observed session facts.
pub(crate) fn load_bash_transitions() -> Vec<TransitionEntry> {
    if !enabled() {
        return Vec::new();
    }
    let Some(path) = bash_history_path() else {
        return Vec::new();
    };
    let Some(contents) = read_tail_text(&path, MAX_HISTORY_IMPORT_BYTES) else {
        return Vec::new();
    };
    let lines: Vec<&str> = contents.lines().collect();
    let start = lines.len().saturating_sub(4_000);
    let mut entries = Vec::new();
    let mut previous: Option<String> = None;
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
            previous = None;
            continue;
        }
        let current = normalized(line);
        if let Some(previous) = previous.take() {
            if previous != current {
                merge_transition(
                    &mut entries,
                    TransitionEntry {
                        previous,
                        next: current.clone(),
                        cwd: String::new(),
                        count: 1,
                        last_used_ms: timestamp,
                    },
                );
            }
        }
        previous = Some(current);
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

fn bash_history_path() -> Option<PathBuf> {
    std::env::var_os("ANDUINOS_BASH_HISTFILE")
        .or_else(|| std::env::var_os("HISTFILE"))
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
}

fn read_tail_text(path: &Path, limit: u64) -> Option<String> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK)
        .open(path)
        .ok()?;
    let metadata = file.metadata().ok()?;
    if !metadata.is_file() {
        return None;
    }
    let length = metadata.len();
    let offset = length.saturating_sub(limit);
    file.seek(SeekFrom::Start(offset)).ok()?;
    let mut bytes = Vec::with_capacity(length.saturating_sub(offset) as usize);
    file.take(limit).read_to_end(&mut bytes).ok()?;
    if offset > 0 {
        let first_line_end = bytes.iter().position(|byte| *byte == b'\n')?;
        bytes.drain(..=first_line_end);
    }
    String::from_utf8(bytes).ok()
}

pub(crate) fn record(path: &Path, event: &HistoryEntry) -> io::Result<()> {
    let Some(parent) = path.parent() else {
        return Ok(());
    };
    fs::create_dir_all(parent)?;
    fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
    let _lock = acquire_lock(path)?;
    let mut file = open_private(path, true, false)?;
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
    let snapshot = load(path);
    compact(path, &snapshot)
}

pub(crate) fn record_transition(path: &Path, event: &TransitionEntry) -> io::Result<()> {
    let Some(parent) = path.parent() else {
        return Ok(());
    };
    fs::create_dir_all(parent)?;
    fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
    let _lock = acquire_lock(path)?;
    let mut file = open_private(path, true, false)?;
    writeln!(
        file,
        "{}\t1\t{}\t{}\t{}",
        event.last_used_ms,
        encode_hex(&event.cwd),
        encode_hex(&event.previous),
        encode_hex(&event.next)
    )?;
    if file.metadata()?.len() <= MAX_FILE_BYTES {
        return Ok(());
    }
    let snapshot = load_transitions(path);
    compact_transitions(path, &snapshot)
}

fn compact(path: &Path, entries: &[HistoryEntry]) -> io::Result<()> {
    let temporary = path.with_extension(format!("tmp-{}", std::process::id()));
    let mut file = open_private(&temporary, false, true)?;
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
    fs::rename(temporary, path)?;
    sync_parent(path)
}

fn compact_transitions(path: &Path, entries: &[TransitionEntry]) -> io::Result<()> {
    let temporary = path.with_extension(format!("tmp-{}", std::process::id()));
    let mut file = open_private(&temporary, false, true)?;
    for entry in entries.iter().take(2_000) {
        writeln!(
            file,
            "{}\t{}\t{}\t{}\t{}",
            entry.last_used_ms,
            entry.count,
            encode_hex(&entry.cwd),
            encode_hex(&entry.previous),
            encode_hex(&entry.next)
        )?;
    }
    file.sync_all()?;
    fs::rename(temporary, path)?;
    sync_parent(path)
}

fn open_private(path: &Path, append: bool, truncate: bool) -> io::Result<fs::File> {
    let mut options = OpenOptions::new();
    options
        .create(true)
        .write(true)
        .append(append)
        .truncate(truncate)
        .mode(0o600)
        .custom_flags(O_CLOEXEC | O_NOFOLLOW);
    let file = options.open(path)?;
    file.set_permissions(fs::Permissions::from_mode(0o600))?;
    Ok(file)
}

fn acquire_lock(path: &Path) -> io::Result<fs::File> {
    let lock_path = path.with_extension("lock");
    let file = open_private(&lock_path, true, false)?;
    loop {
        // SAFETY: the file descriptor remains owned by `file` for the full
        // lock lifetime and flock does not retain any Rust pointer.
        let result = unsafe { flock(file.as_raw_fd(), LOCK_EX) };
        if result == 0 {
            return Ok(file);
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(error);
        }
    }
}

fn sync_parent(path: &Path) -> io::Result<()> {
    match path.parent() {
        Some(parent) => fs::File::open(parent)?.sync_all(),
        None => Ok(()),
    }
}

unsafe extern "C" {
    fn flock(fd: std::ffi::c_int, operation: std::ffi::c_int) -> std::ffi::c_int;
}

fn merge_transition(entries: &mut Vec<TransitionEntry>, incoming: TransitionEntry) {
    if let Some(existing) = entries.iter_mut().find(|entry| {
        entry.previous == incoming.previous
            && entry.next == incoming.next
            && entry.cwd == incoming.cwd
    }) {
        existing.count = existing.count.saturating_add(incoming.count);
        existing.last_used_ms = existing.last_used_ms.max(incoming.last_used_ms);
    } else {
        entries.push(incoming);
    }
}

fn normalized(command: &str) -> String {
    let trimmed = command.trim_start();
    trimmed
        .strip_prefix("sudo ")
        .map(str::trim_start)
        .unwrap_or(trimmed)
        .to_owned()
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
    use std::os::unix::fs::symlink;
    use std::thread;

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
        assert!(!history_safe("docker login -u alice -p hunter2"));
        assert!(!history_safe("curl -u alice:hunter2 https://example.com"));
        assert!(!history_safe("mysql -uroot -phunter2"));
        assert!(!history_safe("sshpass -p hunter2 ssh server"));
    }

    #[test]
    fn transition_log_round_trips_without_a_database() {
        let root = std::env::temp_dir().join(format!(
            "anduinos-transition-log-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        let path = root.join("transitions-v1");
        let entry = TransitionEntry {
            previous: "docker ps".into(),
            next: "docker exec -it api".into(),
            cwd: "/repo".into(),
            count: 1,
            last_used_ms: 42,
        };
        record_transition(&path, &entry).unwrap();
        assert_eq!(load_transitions(&path), vec![entry]);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn private_log_rejects_symlinks_and_repairs_existing_permissions() {
        let root = std::env::temp_dir().join(format!(
            "anduinos-private-log-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        let event = HistoryEntry {
            command: "git status".into(),
            cwd: "/repo".into(),
            count: 1,
            last_used_ms: 42,
        };

        let private = root.join("private");
        fs::write(&private, "").unwrap();
        fs::set_permissions(&private, fs::Permissions::from_mode(0o644)).unwrap();
        record(&private, &event).unwrap();
        assert_eq!(
            fs::metadata(&private).unwrap().permissions().mode() & 0o777,
            0o600
        );

        let target = root.join("target");
        fs::write(&target, "sentinel").unwrap();
        let linked = root.join("linked");
        symlink(&target, &linked).unwrap();
        assert!(record(&linked, &event).is_err());
        assert_eq!(fs::read_to_string(target).unwrap(), "sentinel");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn concurrent_shell_writers_do_not_interleave_history_records() {
        let root = std::env::temp_dir().join(format!(
            "anduinos-concurrent-log-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        let path = root.join("history-v1");
        let event = HistoryEntry {
            command: "git status".into(),
            cwd: "/repo".into(),
            count: 1,
            last_used_ms: 42,
        };
        let mut writers = Vec::new();
        for _ in 0..8 {
            let path = path.clone();
            let event = event.clone();
            writers.push(thread::spawn(move || {
                for _ in 0..50 {
                    record(&path, &event).unwrap();
                }
            }));
        }
        for writer in writers {
            writer.join().unwrap();
        }
        let loaded = load(&path);
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].count, 400);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn bounded_tail_reader_discards_a_partial_first_record() {
        let root = std::env::temp_dir().join(format!(
            "anduinos-tail-read-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("history");
        fs::write(&path, "discard-this-line\nkeep-one\nkeep-two\n").unwrap();
        assert_eq!(
            read_tail_text(&path, 20).as_deref(),
            Some("keep-one\nkeep-two\n")
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn history_reader_rejects_special_files() {
        assert!(read_tail_text(Path::new("/dev/zero"), 1_024).is_none());
    }
}
