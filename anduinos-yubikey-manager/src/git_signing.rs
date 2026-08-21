use crate::i18n::{i18n, i18n_fmt};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

const MANAGED_KEYS: [&str; 4] = [
    "gpg.format",
    "user.signingKey",
    "commit.gpgSign",
    "tag.gpgSign",
];

#[derive(Clone, Debug, Default, PartialEq, Eq)]
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
    pub values: GitValues,
}

impl GitStatus {
    pub fn enabled(&self) -> bool {
        self.values.format.as_deref() == Some("ssh")
            && self.values.signing_key.is_some()
            && self
                .values
                .sign_commits
                .as_deref()
                .is_some_and(|value| value.eq_ignore_ascii_case("true"))
    }
}

pub fn status() -> GitStatus {
    let available = command_output("git", &["--version"])
        .ok()
        .filter(|output| output.status.success())
        .is_some();
    GitStatus {
        available,
        values: if available {
            read_values().unwrap_or_default()
        } else {
            GitValues::default()
        },
    }
}

pub fn signing_selector(
    public_key: &str,
    local_handle_path: Option<&Path>,
    _loaded_in_agent: bool,
) -> Result<String, String> {
    if let Some(path) = local_handle_path {
        if path.is_file() {
            return path
                .to_str()
                .map(ToString::to_string)
                .ok_or_else(|| i18n("The local SSH key-handle path is not valid UTF-8."));
        }
    }
    let mut fields = public_key.split_whitespace();
    let algorithm = fields.next().unwrap_or_default();
    let encoded = fields.next().unwrap_or_default();
    if algorithm.starts_with("sk-") && !encoded.is_empty() {
        return Ok(format!("key::{algorithm} {encoded}"));
    }
    Err(i18n(
        "This is not a supported OpenSSH security-key public key.",
    ))
}

pub fn configured_public_key(values: &GitValues) -> Option<String> {
    let selector = values.signing_key.as_deref()?;
    if let Some(public_key) = selector.strip_prefix("key::") {
        return Some(public_key.to_string());
    }
    let public_path = PathBuf::from(format!("{selector}.pub"));
    fs::read_to_string(public_path).ok().and_then(|content| {
        content
            .lines()
            .find(|line| !line.trim().is_empty())
            .map(str::trim)
            .map(ToString::to_string)
    })
}

pub fn select_key(selector: &str) -> Result<(), String> {
    if selector.trim().is_empty() {
        return Err(i18n("Choose an SSH key for Git signing."));
    }
    let current = read_values()?;
    let desired = GitValues {
        format: Some("ssh".into()),
        signing_key: Some(selector.into()),
        sign_commits: Some("true".into()),
        sign_tags: Some("false".into()),
    };
    write_with_rollback(&current, &desired)
}

pub fn disable() -> Result<(), String> {
    let current = read_values()?;
    let desired = GitValues {
        format: current.format.clone(),
        signing_key: current.signing_key.clone(),
        sign_commits: Some("false".into()),
        sign_tags: Some("false".into()),
    };
    write_with_rollback(&current, &desired)
}

fn write_with_rollback(current: &GitValues, desired: &GitValues) -> Result<(), String> {
    if let Err(error) = write_values(desired) {
        let _ = write_values(current);
        return Err(error);
    }
    Ok(())
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
                let output = command_output("git", &["config", "--global", "--unset-all", key])?;
                if !output.status.success() && output.status.code() != Some(5) {
                    return Err(command_error("git config", &output));
                }
            }
        }
    }
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
    fn uses_an_inline_public_key_when_no_handle_exists() {
        assert_eq!(
            signing_selector("sk-ecdsa AAAA private label", None, false).unwrap(),
            "key::sk-ecdsa AAAA"
        );
    }

    #[test]
    fn rejects_a_non_security_key() {
        assert!(signing_selector("ssh-ed25519 AAAA", None, true).is_err());
    }

    #[test]
    fn reads_public_key_from_an_inline_selector() {
        let values = GitValues {
            signing_key: Some("key::sk-ecdsa AAAA".into()),
            ..GitValues::default()
        };
        assert_eq!(
            configured_public_key(&values).as_deref(),
            Some("sk-ecdsa AAAA")
        );
    }
}
