use crate::i18n::{i18n, i18n_fmt};
use std::fs::{self, File, Metadata};
use std::io::Write;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use tempfile::NamedTempFile;

const MANAGED_FILE_NAME: &str = "anduinos-yubikey-manager.inc";
const MANAGED_RELATIVE_PATH: &str = "config.d/anduinos-yubikey-manager.inc";
const BEGIN_MARKER: &str = "# BEGIN AnduinOS YubiKey Manager SSH persistence";
const END_MARKER: &str = "# END AnduinOS YubiKey Manager SSH persistence";
const MANAGED_BLOCK: &str = "# BEGIN AnduinOS YubiKey Manager SSH persistence\nHost *\n    Include config.d/anduinos-yubikey-manager.inc\n# END AnduinOS YubiKey Manager SSH persistence\n";
const MANAGED_FRAGMENT: &str =
    "    ControlMaster auto\n    ControlPath ~/.ssh/cm-%r@%h:%p\n    ControlPersist 10m\n";

pub const CONFIG_PREVIEW: &str =
    "Host *\n    ControlMaster auto\n    ControlPath ~/.ssh/cm-%r@%h:%p\n    ControlPersist 10m";

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PersistenceState {
    Enabled,
    Disabled,
    NeedsAttention(String),
}

#[derive(Clone, Debug)]
pub struct PersistenceSnapshot {
    pub state: PersistenceState,
    pub config_path: PathBuf,
    pub managed_path: PathBuf,
}

#[derive(Clone, Debug)]
struct ConfigPaths {
    ssh_dir: PathBuf,
    config_dir: PathBuf,
    config: PathBuf,
    managed: PathBuf,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct FileSnapshot {
    content: Vec<u8>,
    mode: u32,
    uid: u32,
    device: u64,
    inode: u64,
}

#[derive(Clone, Copy, Debug)]
struct ManagedRange {
    removal_start: usize,
    marker_start: usize,
    end: usize,
}

pub fn inspect() -> Result<PersistenceSnapshot, String> {
    inspect_at(&ConfigPaths::from_home()?, true)
}

pub fn set_enabled(enabled: bool) -> Result<PersistenceSnapshot, String> {
    let paths = ConfigPaths::from_home()?;
    let current = inspect_at(&paths, false)?;
    if let PersistenceState::NeedsAttention(reason) = &current.state {
        return Err(reason.clone());
    }

    match (&current.state, enabled) {
        (PersistenceState::Enabled, true) | (PersistenceState::Disabled, false) => {
            return inspect_at(&paths, enabled);
        }
        (PersistenceState::Disabled, true) => enable(&paths)?,
        (PersistenceState::Enabled, false) => disable(&paths)?,
        (PersistenceState::NeedsAttention(_), _) => unreachable!(),
    }

    let updated = inspect_at(&paths, enabled)?;
    let expected = if enabled {
        PersistenceState::Enabled
    } else {
        PersistenceState::Disabled
    };
    if updated.state != expected {
        return Err(i18n(
            "The SSH configuration changed while it was being updated. Refresh and try again.",
        ));
    }
    Ok(updated)
}

impl ConfigPaths {
    fn from_home() -> Result<Self, String> {
        let home = std::env::var_os("HOME")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .ok_or_else(|| i18n("HOME is not set. The SSH configuration cannot be located."))?;
        if !home.is_absolute() || !home.is_dir() {
            return Err(i18n_fmt(
                &i18n("HOME does not point to a valid folder: {0}"),
                &[&home.to_string_lossy()],
            ));
        }
        Ok(Self::for_home(&home))
    }

    fn for_home(home: &Path) -> Self {
        let ssh_dir = home.join(".ssh");
        let config_dir = ssh_dir.join("config.d");
        Self {
            config: ssh_dir.join("config"),
            managed: config_dir.join(MANAGED_FILE_NAME),
            ssh_dir,
            config_dir,
        }
    }
}

fn inspect_at(paths: &ConfigPaths, validate: bool) -> Result<PersistenceSnapshot, String> {
    let state = match inspect_state(paths) {
        Ok(PersistenceState::Enabled) if validate => match validate_ssh_config(&paths.config) {
            Ok(()) => PersistenceState::Enabled,
            Err(error) => PersistenceState::NeedsAttention(error),
        },
        Ok(state) => state,
        Err(error) => PersistenceState::NeedsAttention(error),
    };
    Ok(PersistenceSnapshot {
        state,
        config_path: paths.config.clone(),
        managed_path: paths.managed.clone(),
    })
}

fn inspect_state(paths: &ConfigPaths) -> Result<PersistenceState, String> {
    validate_existing_directory(&paths.ssh_dir)?;
    validate_existing_directory(&paths.config_dir)?;

    let config = read_optional_regular(&paths.config)?;
    let fragment = read_optional_regular(&paths.managed)?;
    let config_text = match &config {
        Some(snapshot) => std::str::from_utf8(&snapshot.content).map_err(|_| {
            path_error(
                &i18n("The SSH configuration is not valid UTF-8 and cannot be safely managed: {0}"),
                &paths.config,
            )
        })?,
        None => "",
    };
    let managed_range = locate_managed_block(config_text)?;

    match managed_range {
        Some(range) => match fragment {
            Some(snapshot) if snapshot.content == MANAGED_FRAGMENT.as_bytes() => {
                let mut unmanaged = config_text.as_bytes().to_vec();
                unmanaged.drain(range.removal_start..range.end);
                if fragment_may_be_referenced(&unmanaged) {
                    Ok(PersistenceState::NeedsAttention(i18n(
                        "The managed SSH file is also referenced outside the application-managed block. Remove the extra Include before using this setting.",
                    )))
                } else {
                    Ok(PersistenceState::Enabled)
                }
            }
            Some(_) => Ok(PersistenceState::NeedsAttention(path_error(
                &i18n("The managed SSH configuration was changed outside this application: {0}"),
                &paths.managed,
            ))),
            None => Ok(PersistenceState::NeedsAttention(path_error(
                &i18n("The managed SSH configuration is missing: {0}"),
                &paths.managed,
            ))),
        },
        None => match fragment {
            Some(snapshot) if snapshot.content == MANAGED_FRAGMENT.as_bytes() => {
                if fragment_may_be_referenced(config_text.as_bytes()) {
                    Ok(PersistenceState::NeedsAttention(i18n(
                        "The managed SSH file is referenced outside the application-managed block. Remove that Include before using this setting.",
                    )))
                } else {
                    Ok(PersistenceState::Disabled)
                }
            }
            Some(_) => Ok(PersistenceState::NeedsAttention(path_error(
                &i18n("A file already exists at the managed SSH configuration path: {0}"),
                &paths.managed,
            ))),
            None => Ok(PersistenceState::Disabled),
        },
    }
}

fn enable(paths: &ConfigPaths) -> Result<(), String> {
    let created_ssh_dir = ensure_secure_directory(&paths.ssh_dir)?;
    let created_config_dir = match ensure_secure_directory(&paths.config_dir) {
        Ok(created) => created,
        Err(error) => {
            remove_empty_created_directory(&paths.ssh_dir, created_ssh_dir);
            return Err(error);
        }
    };

    let result = enable_files(paths);
    if result.is_err() {
        remove_empty_created_directory(&paths.config_dir, created_config_dir);
        remove_empty_created_directory(&paths.ssh_dir, created_ssh_dir);
    }
    result
}

fn enable_files(paths: &ConfigPaths) -> Result<(), String> {
    let original_config = read_optional_regular(&paths.config)?;
    let config_text = original_config
        .as_ref()
        .map(|snapshot| std::str::from_utf8(&snapshot.content))
        .transpose()
        .map_err(|_| {
            path_error(
                &i18n("The SSH configuration is not valid UTF-8 and cannot be safely managed: {0}"),
                &paths.config,
            )
        })?
        .unwrap_or("");
    if locate_managed_block(config_text)?.is_some() {
        return Ok(());
    }

    let original_fragment = read_optional_regular(&paths.managed)?;
    let created_fragment = match &original_fragment {
        Some(snapshot) if snapshot.content == MANAGED_FRAGMENT.as_bytes() => false,
        Some(_) => {
            return Err(path_error(
                &i18n("A file already exists at the managed SSH configuration path: {0}"),
                &paths.managed,
            ));
        }
        None => {
            atomic_replace(
                &paths.managed,
                None,
                MANAGED_FRAGMENT.as_bytes(),
                0o600,
                |candidate| validate_fragment(candidate),
            )?;
            true
        }
    };

    let candidate = append_managed_block(
        original_config
            .as_ref()
            .map(|snapshot| snapshot.content.as_slice())
            .unwrap_or_default(),
    );
    let config_mode = original_config
        .as_ref()
        .map(|snapshot| snapshot.mode)
        .unwrap_or(0o600);
    let result = atomic_replace(
        &paths.config,
        original_config.as_ref(),
        &candidate,
        config_mode,
        validate_ssh_config,
    );
    if let Err(error) = result {
        if created_fragment {
            let _ = remove_if_unchanged(&paths.managed, MANAGED_FRAGMENT.as_bytes());
        }
        return Err(error);
    }
    Ok(())
}

fn disable(paths: &ConfigPaths) -> Result<(), String> {
    let original_config = read_optional_regular(&paths.config)?.ok_or_else(|| {
        path_error(
            &i18n("The managed SSH block exists but the main configuration is missing: {0}"),
            &paths.config,
        )
    })?;
    let config_text = std::str::from_utf8(&original_config.content).map_err(|_| {
        path_error(
            &i18n("The SSH configuration is not valid UTF-8 and cannot be safely managed: {0}"),
            &paths.config,
        )
    })?;
    let range = locate_managed_block(config_text)?.ok_or_else(|| {
        i18n("The managed SSH block is no longer present. Refresh and try again.")
    })?;
    let mut candidate = original_config.content.clone();
    candidate.drain(range.removal_start..range.end);

    atomic_replace(
        &paths.config,
        Some(&original_config),
        &candidate,
        original_config.mode,
        validate_ssh_config,
    )?;

    if !fragment_may_be_referenced(&candidate) {
        let fragment = match read_optional_regular(&paths.managed) {
            Ok(fragment) => fragment,
            Err(error) => {
                let rollback = rollback_config(paths, &candidate, &original_config);
                return Err(rollback.unwrap_or(error));
            }
        };
        if let Some(fragment) = fragment {
            if fragment.content != MANAGED_FRAGMENT.as_bytes() {
                let rollback = rollback_config(paths, &candidate, &original_config);
                return Err(rollback.unwrap_or_else(|| {
                    path_error(
                        &i18n(
                            "The managed SSH configuration changed while it was being removed: {0}",
                        ),
                        &paths.managed,
                    )
                }));
            }
            if let Err(error) = remove_if_unchanged(&paths.managed, MANAGED_FRAGMENT.as_bytes()) {
                let rollback = rollback_config(paths, &candidate, &original_config);
                return Err(rollback.unwrap_or(error));
            }
        }
    }
    Ok(())
}

fn rollback_config(paths: &ConfigPaths, current: &[u8], original: &FileSnapshot) -> Option<String> {
    let current_snapshot = read_optional_regular(&paths.config).ok().flatten()?;
    if current_snapshot.content != current {
        return Some(i18n(
            "The SSH configuration changed during rollback. No further changes were made.",
        ));
    }
    atomic_replace(
        &paths.config,
        Some(&current_snapshot),
        &original.content,
        original.mode,
        validate_ssh_config,
    )
    .err()
}

fn append_managed_block(original: &[u8]) -> Vec<u8> {
    let mut candidate = Vec::with_capacity(original.len() + MANAGED_BLOCK.len() + 1);
    candidate.extend_from_slice(original);
    if !original.is_empty() {
        candidate.push(b'\n');
    }
    candidate.extend_from_slice(MANAGED_BLOCK.as_bytes());
    candidate
}

fn locate_managed_block(content: &str) -> Result<Option<ManagedRange>, String> {
    let mut begins = Vec::new();
    let mut ends = Vec::new();
    let mut offset = 0;
    for line_with_newline in content.split_inclusive('\n') {
        let line = line_with_newline
            .strip_suffix('\n')
            .unwrap_or(line_with_newline)
            .strip_suffix('\r')
            .unwrap_or_else(|| {
                line_with_newline
                    .strip_suffix('\n')
                    .unwrap_or(line_with_newline)
            });
        if line == BEGIN_MARKER {
            begins.push(offset);
        }
        if line == END_MARKER {
            ends.push(offset + line_with_newline.len());
        }
        offset += line_with_newline.len();
    }

    if begins.is_empty() && ends.is_empty() {
        return Ok(None);
    }
    if begins.len() != 1 || ends.len() != 1 || begins[0] >= ends[0] {
        return Err(i18n(
            "The SSH configuration contains duplicate or incomplete application markers. Remove or repair the marked block manually.",
        ));
    }
    let marker_start = begins[0];
    let end = ends[0];
    if &content.as_bytes()[marker_start..end] != MANAGED_BLOCK.as_bytes() {
        return Err(i18n(
            "The application-managed block in the SSH configuration was changed. Restore or remove the marked block manually.",
        ));
    }
    let removal_start = if marker_start > 0 && content.as_bytes()[marker_start - 1] == b'\n' {
        marker_start - 1
    } else {
        marker_start
    };
    Ok(Some(ManagedRange {
        removal_start,
        marker_start,
        end,
    }))
}

fn validate_existing_directory(path: &Path) -> Result<(), String> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => {
            return Err(io_path_error(
                &i18n("Could not inspect {0}: {1}"),
                path,
                &error,
            ));
        }
    };
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(path_error(
            &i18n("The SSH path is not a safe, regular directory: {0}"),
            path,
        ));
    }
    validate_owner_and_mode(path, &metadata, true)
}

fn ensure_secure_directory(path: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() || !metadata.is_dir() {
                return Err(path_error(
                    &i18n("The SSH path is not a safe, regular directory: {0}"),
                    path,
                ));
            }
            validate_owner_and_mode(path, &metadata, true)?;
            Ok(false)
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(path)
                .map_err(|error| io_path_error(&i18n("Could not create {0}: {1}"), path, &error))?;
            fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|error| {
                io_path_error(&i18n("Could not protect {0}: {1}"), path, &error)
            })?;
            Ok(true)
        }
        Err(error) => Err(io_path_error(
            &i18n("Could not inspect {0}: {1}"),
            path,
            &error,
        )),
    }
}

fn read_optional_regular(path: &Path) -> Result<Option<FileSnapshot>, String> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => {
            return Err(io_path_error(
                &i18n("Could not inspect {0}: {1}"),
                path,
                &error,
            ));
        }
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(path_error(
            &i18n("The SSH configuration path is not a safe, regular file: {0}"),
            path,
        ));
    }
    validate_owner_and_mode(path, &metadata, false)?;
    let content = fs::read(path)
        .map_err(|error| io_path_error(&i18n("Could not read {0}: {1}"), path, &error))?;
    Ok(Some(snapshot_from(metadata, content)))
}

fn snapshot_from(metadata: Metadata, content: Vec<u8>) -> FileSnapshot {
    FileSnapshot {
        content,
        mode: metadata.mode() & 0o7777,
        uid: metadata.uid(),
        device: metadata.dev(),
        inode: metadata.ino(),
    }
}

fn validate_owner_and_mode(
    path: &Path,
    metadata: &Metadata,
    directory: bool,
) -> Result<(), String> {
    if metadata.uid() != unsafe { libc::geteuid() } {
        return Err(path_error(
            &i18n("The SSH path is not owned by the current user: {0}"),
            path,
        ));
    }
    if metadata.mode() & 0o022 != 0 {
        return Err(path_error(
            &if directory {
                i18n("The SSH directory is writable by another user: {0}")
            } else {
                i18n("The SSH configuration is writable by another user: {0}")
            },
            path,
        ));
    }
    Ok(())
}

fn atomic_replace<F>(
    path: &Path,
    expected: Option<&FileSnapshot>,
    content: &[u8],
    mode: u32,
    validate: F,
) -> Result<(), String>
where
    F: FnOnce(&Path) -> Result<(), String>,
{
    let parent = path
        .parent()
        .ok_or_else(|| i18n("The SSH configuration path is invalid."))?;
    let mut temporary = NamedTempFile::new_in(parent).map_err(|error| {
        io_path_error(
            &i18n("Could not create a temporary file in {0}: {1}"),
            parent,
            &error,
        )
    })?;
    temporary
        .as_file()
        .set_permissions(fs::Permissions::from_mode(mode))
        .map_err(|error| {
            io_path_error(
                &i18n("Could not protect the temporary SSH configuration in {0}: {1}"),
                parent,
                &error,
            )
        })?;
    temporary
        .write_all(content)
        .and_then(|_| temporary.flush())
        .and_then(|_| temporary.as_file().sync_all())
        .map_err(|error| {
            io_path_error(
                &i18n("Could not write the temporary SSH configuration in {0}: {1}"),
                parent,
                &error,
            )
        })?;
    validate(temporary.path())?;

    let current = read_optional_regular(path)?;
    if current.as_ref() != expected {
        return Err(i18n(
            "The SSH configuration changed while it was being updated. Refresh and try again.",
        ));
    }
    // Once rename commits, a later directory-fsync error must not be reported
    // as though the old file were still active. Check first, then make one
    // best-effort durability sync after the atomic replacement.
    sync_directory(parent)?;
    temporary
        .persist(path)
        .map_err(|error| io_path_error(&i18n("Could not replace {0}: {1}"), path, &error.error))?;
    let _ = sync_directory(parent);
    Ok(())
}

fn validate_fragment(path: &Path) -> Result<(), String> {
    let content = fs::read(path)
        .map_err(|error| io_path_error(&i18n("Could not read {0}: {1}"), path, &error))?;
    if content != MANAGED_FRAGMENT.as_bytes() {
        return Err(i18n(
            "The generated SSH persistence configuration did not match the expected preset.",
        ));
    }
    Ok(())
}

fn validate_ssh_config(path: &Path) -> Result<(), String> {
    let output = Command::new("ssh")
        .arg("-G")
        .arg("-F")
        .arg(path)
        .arg("--")
        .arg("anduinos-yubikey-manager.invalid")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|error| {
            i18n_fmt(
                &i18n("Could not validate the SSH configuration: {0}"),
                &[&error.to_string()],
            )
        })?;
    if output.status.success() {
        return Ok(());
    }
    let details = String::from_utf8_lossy(&output.stderr).trim().to_string();
    Err(if details.is_empty() {
        i18n("OpenSSH rejected the generated configuration.")
    } else {
        i18n_fmt(
            &i18n("OpenSSH rejected the generated configuration: {0}"),
            &[&details],
        )
    })
}

fn fragment_may_be_referenced(config: &[u8]) -> bool {
    let Ok(config) = std::str::from_utf8(config) else {
        return true;
    };
    config.lines().any(|line| {
        let line = line.trim_start();
        let Some(arguments) = line
            .strip_prefix("Include")
            .or_else(|| line.strip_prefix("include"))
        else {
            return false;
        };
        if !arguments.starts_with(char::is_whitespace) {
            return false;
        }
        arguments.split_whitespace().any(|pattern| {
            let relative_pattern = pattern
                .find("config.d/")
                .map(|index| &pattern[index..])
                .unwrap_or(pattern);
            pattern == MANAGED_RELATIVE_PATH
                || pattern.ends_with(MANAGED_FILE_NAME)
                || wildcard_matches(relative_pattern, MANAGED_RELATIVE_PATH)
        })
    })
}

fn wildcard_matches(pattern: &str, value: &str) -> bool {
    if pattern.contains('[') {
        return pattern.contains("config.d/");
    }
    let pattern = pattern.as_bytes();
    let value = value.as_bytes();
    let (mut pattern_index, mut value_index) = (0, 0);
    let (mut star, mut star_value) = (None, 0);
    while value_index < value.len() {
        if pattern_index < pattern.len()
            && (pattern[pattern_index] == b'?' || pattern[pattern_index] == value[value_index])
        {
            pattern_index += 1;
            value_index += 1;
        } else if pattern_index < pattern.len() && pattern[pattern_index] == b'*' {
            star = Some(pattern_index);
            pattern_index += 1;
            star_value = value_index;
        } else if let Some(star_index) = star {
            pattern_index = star_index + 1;
            star_value += 1;
            value_index = star_value;
        } else {
            return false;
        }
    }
    while pattern_index < pattern.len() && pattern[pattern_index] == b'*' {
        pattern_index += 1;
    }
    pattern_index == pattern.len()
}

fn remove_if_unchanged(path: &Path, expected: &[u8]) -> Result<(), String> {
    let snapshot = read_optional_regular(path)?;
    let Some(snapshot) = snapshot else {
        return Ok(());
    };
    if snapshot.content != expected {
        return Err(path_error(
            &i18n("The managed SSH configuration changed while it was being removed: {0}"),
            path,
        ));
    }
    let parent = path.parent();
    if let Some(parent) = parent {
        sync_directory(parent)?;
    }
    fs::remove_file(path)
        .map_err(|error| io_path_error(&i18n("Could not remove {0}: {1}"), path, &error))?;
    if let Some(parent) = parent {
        let _ = sync_directory(parent);
    }
    Ok(())
}

fn sync_directory(path: &Path) -> Result<(), String> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|error| io_path_error(&i18n("Could not synchronize {0}: {1}"), path, &error))
}

fn remove_empty_created_directory(path: &Path, created: bool) {
    if created {
        let _ = fs::remove_dir(path);
    }
}

fn path_error(template: &str, path: &Path) -> String {
    i18n_fmt(template, &[&path.to_string_lossy()])
}

fn io_path_error(template: &str, path: &Path, error: &std::io::Error) -> String {
    i18n_fmt(template, &[&path.to_string_lossy(), &error.to_string()])
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use tempfile::TempDir;

    fn setup() -> (TempDir, ConfigPaths) {
        let home = tempfile::tempdir().unwrap();
        let paths = ConfigPaths::for_home(home.path());
        (home, paths)
    }

    fn enable_without_ssh(paths: &ConfigPaths) -> Result<(), String> {
        ensure_secure_directory(&paths.ssh_dir)?;
        ensure_secure_directory(&paths.config_dir)?;
        let original = read_optional_regular(&paths.config)?;
        if let Some(original) = &original {
            if locate_managed_block(std::str::from_utf8(&original.content).unwrap())?.is_some() {
                return Ok(());
            }
        }
        let fragment = read_optional_regular(&paths.managed)?;
        if fragment.is_none() {
            atomic_replace(
                &paths.managed,
                None,
                MANAGED_FRAGMENT.as_bytes(),
                0o600,
                validate_fragment,
            )?;
        }
        let candidate = append_managed_block(
            original
                .as_ref()
                .map(|snapshot| snapshot.content.as_slice())
                .unwrap_or_default(),
        );
        atomic_replace(
            &paths.config,
            original.as_ref(),
            &candidate,
            original.as_ref().map(|item| item.mode).unwrap_or(0o600),
            |_| Ok(()),
        )
    }

    fn disable_without_ssh(paths: &ConfigPaths) -> Result<(), String> {
        let Some(original) = read_optional_regular(&paths.config)? else {
            return Ok(());
        };
        let text = std::str::from_utf8(&original.content).unwrap();
        let Some(range) = locate_managed_block(text)? else {
            return Ok(());
        };
        let mut candidate = original.content.clone();
        candidate.drain(range.removal_start..range.end);
        atomic_replace(
            &paths.config,
            Some(&original),
            &candidate,
            original.mode,
            |_| Ok(()),
        )?;
        if !fragment_may_be_referenced(&candidate) {
            remove_if_unchanged(&paths.managed, MANAGED_FRAGMENT.as_bytes())?;
        }
        Ok(())
    }

    #[test]
    fn missing_and_empty_configs_round_trip() {
        for original in [None, Some(Vec::new())] {
            let (_home, paths) = setup();
            fs::create_dir(&paths.ssh_dir).unwrap();
            fs::set_permissions(&paths.ssh_dir, fs::Permissions::from_mode(0o700)).unwrap();
            if let Some(content) = &original {
                fs::write(&paths.config, content).unwrap();
                fs::set_permissions(&paths.config, fs::Permissions::from_mode(0o600)).unwrap();
            }
            enable_without_ssh(&paths).unwrap();
            assert_eq!(inspect_state(&paths).unwrap(), PersistenceState::Enabled);
            disable_without_ssh(&paths).unwrap();
            assert_eq!(
                fs::read(&paths.config).unwrap(),
                original.unwrap_or_default()
            );
            assert!(!paths.managed.exists());
        }
    }

    #[test]
    fn no_trailing_newline_and_complex_config_round_trip_exactly() {
        let (_home, paths) = setup();
        fs::create_dir(&paths.ssh_dir).unwrap();
        fs::set_permissions(&paths.ssh_dir, fs::Permissions::from_mode(0o700)).unwrap();
        let original = b"# caf\xC3\xA9\r\nHost work\r\n    ControlMaster no\r\nMatch host *.example\r\n    User dev";
        fs::write(&paths.config, original).unwrap();
        fs::set_permissions(&paths.config, fs::Permissions::from_mode(0o600)).unwrap();
        enable_without_ssh(&paths).unwrap();
        let enabled = fs::read(&paths.config).unwrap();
        assert!(enabled.ends_with(MANAGED_BLOCK.as_bytes()));
        assert!(String::from_utf8_lossy(&enabled).contains("Host *\n    Include"));
        disable_without_ssh(&paths).unwrap();
        assert_eq!(fs::read(&paths.config).unwrap(), original);
    }

    #[test]
    fn enable_and_disable_are_idempotent_at_transform_level() {
        let (_home, paths) = setup();
        fs::create_dir(&paths.ssh_dir).unwrap();
        fs::set_permissions(&paths.ssh_dir, fs::Permissions::from_mode(0o700)).unwrap();
        let original = b"Host example\n    User alice\n";
        fs::write(&paths.config, original).unwrap();
        fs::set_permissions(&paths.config, fs::Permissions::from_mode(0o600)).unwrap();

        enable_without_ssh(&paths).unwrap();
        let once = fs::read(&paths.config).unwrap();
        enable_without_ssh(&paths).unwrap();
        assert_eq!(fs::read(&paths.config).unwrap(), once);
        let range = locate_managed_block(std::str::from_utf8(&once).unwrap())
            .unwrap()
            .unwrap();
        assert_eq!(
            &once[range.marker_start..range.end],
            MANAGED_BLOCK.as_bytes()
        );

        disable_without_ssh(&paths).unwrap();
        disable_without_ssh(&paths).unwrap();
        assert_eq!(fs::read(&paths.config).unwrap(), original);
    }

    #[test]
    fn preserves_handwritten_equivalent_settings_and_includes() {
        let (_home, paths) = setup();
        fs::create_dir(&paths.ssh_dir).unwrap();
        fs::set_permissions(&paths.ssh_dir, fs::Permissions::from_mode(0o700)).unwrap();
        let original = b"Include config.d/*.conf\nHost personal\n    ControlMaster auto\n    ControlPath ~/.ssh/own-%C\n    ControlPersist 30m\n";
        fs::write(&paths.config, original).unwrap();
        fs::set_permissions(&paths.config, fs::Permissions::from_mode(0o600)).unwrap();
        enable_without_ssh(&paths).unwrap();
        disable_without_ssh(&paths).unwrap();
        assert_eq!(fs::read(&paths.config).unwrap(), original);
    }

    #[test]
    fn duplicate_incomplete_and_modified_blocks_need_attention() {
        for content in [
            format!("{MANAGED_BLOCK}{MANAGED_BLOCK}"),
            format!("{BEGIN_MARKER}\nHost *\n"),
            MANAGED_BLOCK.replace("Host *", "Host example"),
        ] {
            let (_home, paths) = setup();
            fs::create_dir(&paths.ssh_dir).unwrap();
            fs::set_permissions(&paths.ssh_dir, fs::Permissions::from_mode(0o700)).unwrap();
            fs::write(&paths.config, content).unwrap();
            fs::set_permissions(&paths.config, fs::Permissions::from_mode(0o600)).unwrap();
            assert!(matches!(
                inspect_at(&paths, false).unwrap().state,
                PersistenceState::NeedsAttention(_)
            ));
        }
    }

    #[test]
    fn modified_fragment_and_symlinks_need_attention() {
        let (_home, paths) = setup();
        fs::create_dir(&paths.ssh_dir).unwrap();
        fs::set_permissions(&paths.ssh_dir, fs::Permissions::from_mode(0o700)).unwrap();
        fs::create_dir(&paths.config_dir).unwrap();
        fs::set_permissions(&paths.config_dir, fs::Permissions::from_mode(0o700)).unwrap();
        fs::write(&paths.managed, "ControlPersist 1h\n").unwrap();
        fs::set_permissions(&paths.managed, fs::Permissions::from_mode(0o600)).unwrap();
        assert!(matches!(
            inspect_at(&paths, false).unwrap().state,
            PersistenceState::NeedsAttention(_)
        ));
        fs::remove_file(&paths.managed).unwrap();
        let target = paths.ssh_dir.join("elsewhere");
        fs::write(&target, "").unwrap();
        symlink(&target, &paths.managed).unwrap();
        assert!(matches!(
            inspect_at(&paths, false).unwrap().state,
            PersistenceState::NeedsAttention(_)
        ));
    }

    #[test]
    fn creates_private_directories_and_files() {
        let (_home, paths) = setup();
        enable_without_ssh(&paths).unwrap();
        assert_eq!(fs::metadata(&paths.ssh_dir).unwrap().mode() & 0o777, 0o700);
        assert_eq!(
            fs::metadata(&paths.config_dir).unwrap().mode() & 0o777,
            0o700
        );
        assert_eq!(fs::metadata(&paths.config).unwrap().mode() & 0o777, 0o600);
        assert_eq!(fs::metadata(&paths.managed).unwrap().mode() & 0o777, 0o600);
    }

    #[test]
    fn failed_validation_leaves_original_unchanged() {
        let (_home, paths) = setup();
        fs::create_dir(&paths.ssh_dir).unwrap();
        fs::set_permissions(&paths.ssh_dir, fs::Permissions::from_mode(0o700)).unwrap();
        fs::write(&paths.config, "Host example\n").unwrap();
        fs::set_permissions(&paths.config, fs::Permissions::from_mode(0o600)).unwrap();
        let original = read_optional_regular(&paths.config).unwrap().unwrap();
        let result = atomic_replace(
            &paths.config,
            Some(&original),
            b"replacement",
            original.mode,
            |_| Err("validation failed".into()),
        );
        assert!(result.is_err());
        assert_eq!(fs::read(&paths.config).unwrap(), original.content);
    }

    #[test]
    fn failed_write_leaves_original_unchanged() {
        let (_home, paths) = setup();
        fs::create_dir(&paths.ssh_dir).unwrap();
        fs::set_permissions(&paths.ssh_dir, fs::Permissions::from_mode(0o700)).unwrap();
        fs::write(&paths.config, "original").unwrap();
        fs::set_permissions(&paths.config, fs::Permissions::from_mode(0o600)).unwrap();
        let original = read_optional_regular(&paths.config).unwrap().unwrap();
        fs::set_permissions(&paths.ssh_dir, fs::Permissions::from_mode(0o500)).unwrap();
        let result = atomic_replace(
            &paths.config,
            Some(&original),
            b"replacement",
            original.mode,
            |_| Ok(()),
        );
        fs::set_permissions(&paths.ssh_dir, fs::Permissions::from_mode(0o700)).unwrap();
        assert!(result.is_err());
        assert_eq!(fs::read(&paths.config).unwrap(), original.content);
    }

    #[test]
    fn concurrent_edit_is_detected_without_overwrite() {
        let (_home, paths) = setup();
        fs::create_dir(&paths.ssh_dir).unwrap();
        fs::set_permissions(&paths.ssh_dir, fs::Permissions::from_mode(0o700)).unwrap();
        fs::write(&paths.config, "before").unwrap();
        fs::set_permissions(&paths.config, fs::Permissions::from_mode(0o600)).unwrap();
        let original = read_optional_regular(&paths.config).unwrap().unwrap();
        let config = paths.config.clone();
        let result = atomic_replace(
            &paths.config,
            Some(&original),
            b"application",
            original.mode,
            move |_| {
                fs::write(&config, "user edit").unwrap();
                Ok(())
            },
        );
        assert!(result.is_err());
        assert_eq!(fs::read(&paths.config).unwrap(), b"user edit");
    }

    #[test]
    fn non_ascii_and_space_paths_are_supported() {
        let root = tempfile::tempdir().unwrap();
        let home_path = root.path().join("User Space \u{7528}\u{6237}");
        fs::create_dir(&home_path).unwrap();
        let paths = ConfigPaths::for_home(&home_path);
        enable_without_ssh(&paths).unwrap();
        assert_eq!(inspect_state(&paths).unwrap(), PersistenceState::Enabled);
    }

    #[test]
    fn direct_or_glob_include_keeps_managed_fragment() {
        assert!(fragment_may_be_referenced(
            b"Include config.d/anduinos-yubikey-manager.inc\n"
        ));
        assert!(fragment_may_be_referenced(b"Include config.d/*.inc\n"));
        assert!(!fragment_may_be_referenced(b"Include config.d/*.conf\n"));
    }
}
