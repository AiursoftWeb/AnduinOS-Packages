use crate::i18n::{i18n, i18n_fmt};
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

const MANAGED_KEYS: [&str; 4] = [
    "gpg.format",
    "user.signingKey",
    "commit.gpgSign",
    "tag.gpgSign",
];

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct GitValues {
    pub format: Option<String>,
    pub signing_key: Option<String>,
    pub sign_commits: Option<String>,
    pub sign_tags: Option<String>,
}

impl GitValues {
    fn get(&self, key: &str) -> Option<&str> {
        match key {
            "gpg.format" => self.format.as_deref(),
            "user.signingKey" => self.signing_key.as_deref(),
            "commit.gpgSign" => self.sign_commits.as_deref(),
            "tag.gpgSign" => self.sign_tags.as_deref(),
            _ => None,
        }
    }
}

#[derive(Clone, Debug)]
pub struct GitStatus {
    pub available: bool,
    pub version: String,
    pub values: GitValues,
    pub managed: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct ManagedState {
    previous: GitValues,
    applied: GitValues,
}

pub fn status() -> GitStatus {
    let version = command_output("git", &["--version"])
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string());
    let available = version.is_some();
    GitStatus {
        available,
        version: version.unwrap_or_else(|| i18n("Git is not installed")),
        values: if available {
            read_values().unwrap_or_default()
        } else {
            GitValues::default()
        },
        managed: state_path().is_some_and(|path| path.is_file()),
    }
}

pub fn signing_selector(
    public_key: &str,
    local_handle_path: Option<&Path>,
    loaded_in_agent: bool,
) -> Result<String, String> {
    if let Some(path) = local_handle_path {
        if path.is_file() {
            return path
                .to_str()
                .map(ToString::to_string)
                .ok_or_else(|| i18n("The local SSH key-handle path is not valid UTF-8."));
        }
    }
    if loaded_in_agent {
        let mut fields = public_key.split_whitespace();
        let algorithm = fields.next().unwrap_or_default();
        let encoded = fields.next().unwrap_or_default();
        if algorithm.starts_with("sk-") && !encoded.is_empty() {
            return Ok(format!("key::{algorithm} {encoded}"));
        }
    }
    Err(i18n("This credential needs a local key-handle file or must be loaded into the SSH agent before Git can use it."))
}

pub fn apply(
    selector: &str,
    sign_commits: bool,
    sign_tags: bool,
) -> Result<(), String> {
    if selector.trim().is_empty() {
        return Err(i18n("Choose an SSH key for Git signing."));
    }
    let state_file = state_path().ok_or_else(|| i18n("The user configuration folder is unavailable."))?;
    let previous_state = read_managed_state(&state_file)?;
    let current = read_values()?;
    let previous = previous_state
        .as_ref()
        .map(|state| state.previous.clone())
        .unwrap_or_else(|| current.clone());
    let desired = GitValues {
        format: Some("ssh".into()),
        signing_key: Some(selector.into()),
        sign_commits: Some(if sign_commits { "true" } else { "false" }.into()),
        sign_tags: Some(if sign_tags { "true" } else { "false" }.into()),
    };

    if let Some(state) = previous_state {
        ensure_no_conflict(&current, &state.applied)?;
    }
    if let Err(error) = write_values(&desired) {
        let _ = write_values(&current);
        return Err(error);
    }
    let state = ManagedState {
        previous,
        applied: desired,
    };
    if let Err(error) = write_managed_state(&state_file, &state) {
        let _ = write_values(&current);
        return Err(error);
    }
    Ok(())
}

pub fn restore() -> Result<(), String> {
    let state_file = state_path().ok_or_else(|| i18n("The user configuration folder is unavailable."))?;
    let Some(state) = read_managed_state(&state_file)? else {
        return Err(i18n("No Git signing configuration managed by this application was found."));
    };
    let current = read_values()?;
    ensure_no_conflict(&current, &state.applied)?;
    if let Err(error) = write_values(&state.previous) {
        let _ = write_values(&current);
        return Err(error);
    }
    fs::remove_file(&state_file).map_err(|error| {
        i18n_fmt(
            &i18n("Git settings were restored, but the recovery record could not be removed: {0}"),
            &[&error.to_string()],
        )
    })
}

fn ensure_no_conflict(current: &GitValues, applied: &GitValues) -> Result<(), String> {
    if current == applied {
        Ok(())
    } else {
        Err(i18n("Git signing settings changed outside this application. Review the current Git configuration before replacing or restoring it."))
    }
}

fn read_values() -> Result<GitValues, String> {
    Ok(GitValues {
        format: read_value("gpg.format")?,
        signing_key: read_value("user.signingKey")?,
        sign_commits: read_value("commit.gpgSign")?,
        sign_tags: read_value("tag.gpgSign")?,
    })
}

fn read_value(key: &str) -> Result<Option<String>, String> {
    let output = command_output("git", &["config", "--global", "--get", key])?;
    if output.status.success() {
        return Ok(Some(
            String::from_utf8_lossy(&output.stdout).trim().to_string(),
        ));
    }
    if output.status.code() == Some(1) {
        return Ok(None);
    }
    Err(command_error("git config", &output))
}

fn write_values(values: &GitValues) -> Result<(), String> {
    for key in MANAGED_KEYS {
        match values.get(key) {
            Some(value) => {
                let output =
                    command_output("git", &["config", "--global", "--replace-all", key, value])?;
                if !output.status.success() {
                    return Err(command_error("git config", &output));
                }
            }
            None => {
                let output =
                    command_output("git", &["config", "--global", "--unset-all", key])?;
                if !output.status.success() && output.status.code() != Some(5) {
                    return Err(command_error("git config", &output));
                }
            }
        }
    }
    Ok(())
}

fn state_path() -> Option<PathBuf> {
    let config = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".config")))?;
    Some(config.join("anduinos-yubikey-manager/git-signing-backup.json"))
}

fn read_managed_state(path: &Path) -> Result<Option<ManagedState>, String> {
    if !path.exists() {
        return Ok(None);
    }
    let content = fs::read_to_string(path).map_err(|error| {
        i18n_fmt(
            &i18n("Could not read the Git signing recovery record: {0}"),
            &[&error.to_string()],
        )
    })?;
    serde_json::from_str(&content).map(Some).map_err(|error| {
        i18n_fmt(
            &i18n("The Git signing recovery record is invalid: {0}"),
            &[&error.to_string()],
        )
    })
}

fn write_managed_state(path: &Path, state: &ManagedState) -> Result<(), String> {
    let parent = path.parent().ok_or_else(|| i18n("The user configuration folder is unavailable."))?;
    fs::create_dir_all(parent).map_err(|error| {
        i18n_fmt(
            &i18n("Could not create the application configuration folder: {0}"),
            &[&error.to_string()],
        )
    })?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(parent, fs::Permissions::from_mode(0o700)).map_err(|error| {
            i18n_fmt(
                &i18n("Could not protect the application configuration folder: {0}"),
                &[&error.to_string()],
            )
        })?;
    }
    let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(|error| {
        i18n_fmt(
            &i18n("Could not create the Git signing recovery record: {0}"),
            &[&error.to_string()],
        )
    })?;
    serde_json::to_writer_pretty(&mut temporary, state).map_err(|error| {
        i18n_fmt(
            &i18n("Could not encode the Git signing recovery record: {0}"),
            &[&error.to_string()],
        )
    })?;
    temporary.write_all(b"\n").map_err(|error| error.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        temporary
            .as_file()
            .set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|error| error.to_string())?;
    }
    temporary.persist(path).map_err(|error| {
        i18n_fmt(
            &i18n("Could not save the Git signing recovery record: {0}"),
            &[&error.error.to_string()],
        )
    })?;
    Ok(())
}

fn command_output(program: &str, args: &[&str]) -> Result<Output, String> {
    Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|error| {
            i18n_fmt(
                &i18n("Could not run {0}: {1}"),
                &[program, &error.to_string()],
            )
        })
}

fn command_error(program: &str, output: &Output) -> String {
    let detail = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if detail.is_empty() {
        i18n_fmt(
            &i18n("{0} exited with {1}"),
            &[program, &output.status.to_string()],
        )
    } else {
        detail
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefers_a_local_security_key_handle() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("git-signing");
        fs::write(&path, "handle").unwrap();
        assert_eq!(
            signing_selector("sk-test AAAA", Some(&path), false).unwrap(),
            path.to_string_lossy()
        );
    }

    #[test]
    fn uses_an_agent_public_key_without_persisting_its_comment() {
        assert_eq!(
            signing_selector("sk-ecdsa AAAA private label", None, true).unwrap(),
            "key::sk-ecdsa AAAA"
        );
    }

    #[test]
    fn rejects_a_key_that_git_cannot_reach() {
        assert!(signing_selector("sk-ecdsa AAAA", None, false).is_err());
    }

    #[test]
    fn detects_external_changes_before_overwriting_managed_values() {
        let applied = GitValues {
            format: Some("ssh".into()),
            signing_key: Some("key::sk-test AAAA".into()),
            sign_commits: Some("true".into()),
            sign_tags: Some("false".into()),
        };
        assert!(ensure_no_conflict(&applied, &applied).is_ok());
        let mut changed = applied.clone();
        changed.signing_key = Some("another-key".into());
        assert!(ensure_no_conflict(&changed, &applied).is_err());
    }
}
