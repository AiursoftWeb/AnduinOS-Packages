use base64::engine::general_purpose::{STANDARD, STANDARD_NO_PAD};
use base64::Engine;
use crate::i18n::{i18n, i18n_fmt};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};

#[derive(Clone, Debug)]
pub struct FidoDevice {
    pub path: String,
    pub description: String,
}

#[derive(Clone, Debug)]
pub struct ResidentSshCredential {
    pub application: String,
    pub username: String,
    pub algorithm: String,
    pub fingerprint: String,
    pub public_key: String,
    pub loaded_in_agent: bool,
}

#[derive(Clone, Debug)]
pub struct AgentStatus {
    pub available: bool,
    pub socket: String,
    pub identity_count: usize,
    pub error: Option<String>,
}

#[derive(Clone, Debug)]
pub struct CreateOptions {
    pub device: String,
    pub algorithm: String,
    pub application: String,
    pub username: String,
    pub comment: String,
    pub output_path: PathBuf,
    pub verify_required: bool,
}

#[derive(Clone, Debug)]
pub struct CreateOutcome {
    pub credential: ResidentSshCredential,
    pub credentials: Vec<ResidentSshCredential>,
    pub private_path: PathBuf,
    pub public_path: PathBuf,
}

pub fn list_fido_devices() -> Result<Vec<FidoDevice>, String> {
    let output = run("fido2-token", &["-L"], None)?;
    Ok(output
        .lines()
        .filter_map(|line| {
            let (path, description) = line.split_once(':')?;
            let path = path.trim();
            if !path.starts_with("/dev/hidraw") {
                return None;
            }
            Some(FidoDevice {
                path: path.to_string(),
                description: description.trim().to_string(),
            })
        })
        .collect())
}

pub fn agent_status() -> AgentStatus {
    let socket = std::env::var("SSH_AUTH_SOCK").unwrap_or_default();
    if socket.is_empty() {
        return AgentStatus {
            available: false,
            socket,
            identity_count: 0,
            error: Some(i18n("SSH_AUTH_SOCK is not set")),
        };
    }
    match run("ssh-add", &["-L"], None) {
        Ok(output) => AgentStatus {
            available: true,
            socket,
            identity_count: output.lines().filter(|line| !line.trim().is_empty()).count(),
            error: None,
        },
        Err(error)
            if error.to_ascii_lowercase().contains("no identities")
                || error.contains("The agent has no identities") =>
        {
            AgentStatus {
                available: true,
                socket,
                identity_count: 0,
                error: None,
            }
        }
        Err(error) => AgentStatus {
            available: false,
            socket,
            identity_count: 0,
            error: Some(error),
        },
    }
}

pub fn inspect_resident_ssh(
    device: &str,
    pin: &str,
) -> Result<Vec<ResidentSshCredential>, String> {
    if !device.starts_with("/dev/hidraw")
        || !device["/dev/hidraw".len()..]
            .chars()
            .all(|character| character.is_ascii_digit())
    {
        return Err(i18n("Invalid FIDO device path."));
    }
    let agent_fingerprints = agent_fingerprints();
    let relying_parties = run("fido2-token", &["-L", "-r", device], Some(pin))?;
    let mut credentials = Vec::new();
    for application in parse_relying_parties(&relying_parties)
        .into_iter()
        .filter(|application| application.starts_with("ssh:"))
    {
        let listing = run(
            "fido2-token",
            &["-L", "-k", &application, device],
            Some(pin),
        )?;
        for (credential_id, username) in parse_credentials(&listing) {
            let details = run(
                "fido2-token",
                &["-I", "-k", &application, "-i", &credential_id, device],
                Some(pin),
            )?;
            let pem = extract_pem(&details)?;
            let (algorithm, blob) = ssh_public_blob(&application, &pem)?;
            let fingerprint = fingerprint(&blob);
            let comment = if username.is_empty() {
                application.clone()
            } else {
                username.clone()
            };
            let public_key = format!("{algorithm} {} {comment}", STANDARD.encode(&blob));
            credentials.push(ResidentSshCredential {
                application: application.clone(),
                username,
                algorithm,
                loaded_in_agent: agent_fingerprints.contains(&fingerprint),
                fingerprint,
                public_key,
            });
        }
    }
    Ok(credentials)
}

/// OpenSSH does not offer a device selector for `ssh-add -K`. The caller must
/// enforce that exactly one FIDO authenticator is connected.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LoadResult {
    AlreadyLoaded,
    Loaded { added: usize },
}

pub fn load_resident_keys(pin: &str, expected: &[String]) -> Result<LoadResult, String> {
    let before = agent_fingerprints();
    if !expected.is_empty()
        && expected
            .iter()
            .all(|fingerprint| before.contains(fingerprint))
    {
        return Ok(LoadResult::AlreadyLoaded);
    }
    let output = run_with_askpass("/usr/bin/ssh-add", &["-K".into()], pin)?;
    let after = agent_fingerprints();
    let added = after.difference(&before).count();
    if !expected.is_empty()
        && expected
            .iter()
            .all(|fingerprint| after.contains(fingerprint))
    {
        return Ok(if added == 0 {
            LoadResult::AlreadyLoaded
        } else {
            LoadResult::Loaded { added }
        });
    }
    if !output.status.success() {
        return Err(load_error(&output));
    }
    Ok(LoadResult::Loaded { added })
}

pub fn create_resident_key(options: &CreateOptions, pin: &str) -> Result<CreateOutcome, String> {
    validate_create_options(options)?;
    let devices = list_fido_devices()?;
    if !devices.iter().any(|device| device.path == options.device) {
        return Err(i18n("The selected FIDO device is no longer connected. Refresh and try again."));
    }
    let parent = options
        .output_path
        .parent()
        .ok_or_else(|| i18n("Choose a valid output folder."))?;
    let default_ssh_dir = std::env::var_os("HOME")
        .map(PathBuf::from)
        .map(|home| home.join(".ssh"));
    if !parent.exists() && default_ssh_dir.as_deref() == Some(parent) {
        fs::create_dir(parent)
            .map_err(|error| {
                i18n_fmt(
                    &i18n("Could not create {0}: {1}"),
                    &[&parent.to_string_lossy(), &error.to_string()],
                )
            })?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(parent, fs::Permissions::from_mode(0o700))
                .map_err(|error| {
                    i18n_fmt(
                        &i18n("Could not protect {0}: {1}"),
                        &[&parent.to_string_lossy(), &error.to_string()],
                    )
                })?;
        }
    }
    if !parent.is_dir() {
        return Err(i18n_fmt(
            &i18n("The output folder does not exist: {0}"),
            &[&parent.to_string_lossy()],
        ));
    }
    let public_path = PathBuf::from(format!("{}.pub", options.output_path.display()));
    if options.output_path.exists() || public_path.exists() {
        return Err(i18n("The selected private-key or .pub file already exists. Choose another name."));
    }

    // A successful preflight proves the PIN and exact device before a
    // persistent credential is created.
    let before = inspect_resident_ssh(&options.device, pin)?;
    let before_fingerprints: HashSet<_> = before
        .iter()
        .map(|credential| credential.fingerprint.clone())
        .collect();

    let args = create_keygen_args(options);
    let output = run_with_askpass("/usr/bin/ssh-keygen", &args, pin)?;
    let after = inspect_resident_ssh(&options.device, pin).map_err(|error| {
        i18n_fmt(
            &i18n("OpenSSH finished creating the key, but the YubiKey could not be verified afterward: {0}\nDo not immediately retry; inspect the key first to avoid creating a duplicate."),
            &[&error],
        )
    })?;
    let created = after
        .iter()
        .find(|credential| !before_fingerprints.contains(&credential.fingerprint))
        .cloned();

    if !output.status.success() {
        if let Some(credential) = created {
            return Err(i18n_fmt(
                &i18n("The resident credential {0} was created on the YubiKey, but OpenSSH did not complete successfully:\n{1}\nDo not retry creation. Inspect the key and preserve any local files that were written."),
                &[&credential.fingerprint, &command_error_detail(&output)],
            ));
        }
        return Err(i18n_fmt(
            &i18n("OpenSSH did not create a resident key:\n{0}"),
            &[&command_error_detail(&output)],
        ));
    }
    let credential = created.ok_or_else(|| {
        i18n("OpenSSH reported success, but no new resident credential was detected. Do not retry until you inspect the YubiKey.")
    })?;
    if !options.output_path.is_file() || !public_path.is_file() {
        return Err(i18n_fmt(
            &i18n("The resident credential {0} exists on the YubiKey, but its local handle files are incomplete. Do not retry creation."),
            &[&credential.fingerprint],
        ));
    }
    Ok(CreateOutcome {
        credential,
        credentials: after,
        private_path: options.output_path.clone(),
        public_path,
    })
}

fn create_keygen_args(options: &CreateOptions) -> Vec<String> {
    let mut args = vec![
        "-q".into(),
        "-t".into(),
        options.algorithm.clone(),
        "-O".into(),
        "resident".into(),
        "-O".into(),
        format!("device={}", options.device),
        "-O".into(),
        format!("application={}", options.application),
        "-O".into(),
        format!("user={}", options.username),
        "-C".into(),
        options.comment.clone(),
        "-N".into(),
        String::new(),
        "-f".into(),
        options.output_path.to_string_lossy().into_owned(),
    ];
    if options.verify_required {
        args.push("-O".into());
        args.push("verify-required".into());
    }
    args
}

pub fn default_key_path() -> PathBuf {
    let ssh_dir = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".ssh");
    for suffix in 0..1000 {
        let name = if suffix == 0 {
            "id_ecdsa_sk_yubikey".to_string()
        } else {
            format!("id_ecdsa_sk_yubikey_{suffix}")
        };
        let candidate = ssh_dir.join(name);
        let public = PathBuf::from(format!("{}.pub", candidate.display()));
        if !candidate.exists() && !public.exists() {
            return candidate;
        }
    }
    ssh_dir.join("id_ecdsa_sk_yubikey_new")
}

pub fn validate_create_options(options: &CreateOptions) -> Result<(), String> {
    if !valid_device_path(&options.device) {
        return Err(i18n("Choose a valid /dev/hidrawN FIDO device."));
    }
    if !matches!(options.algorithm.as_str(), "ecdsa-sk" | "ed25519-sk") {
        return Err(i18n("Choose a supported OpenSSH security-key algorithm."));
    }
    if !options.application.starts_with("ssh:")
        || options.application.len() > 253
        || options
            .application
            .chars()
            .any(|character| character.is_control() || character.is_whitespace())
    {
        return Err(i18n("Application must begin with ssh:, contain no whitespace, and be at most 253 bytes."));
    }
    if options.username.is_empty()
        || options.username.as_bytes().len() > 64
        || options.username.chars().any(char::is_control)
    {
        return Err(i18n("Resident username must contain 1–64 bytes and no control characters."));
    }
    if options.comment.as_bytes().len() > 200 || options.comment.chars().any(char::is_control) {
        return Err(i18n("Display label must be at most 200 bytes and contain no control characters."));
    }
    if !options.output_path.is_absolute() {
        return Err(i18n("The local key path must be absolute."));
    }
    Ok(())
}

fn valid_device_path(device: &str) -> bool {
    device.starts_with("/dev/hidraw")
        && !device["/dev/hidraw".len()..].is_empty()
        && device["/dev/hidraw".len()..]
            .chars()
            .all(|character| character.is_ascii_digit())
}

fn run_with_askpass(
    program: &str,
    args: &[String],
    pin: &str,
) -> Result<std::process::Output, String> {
    let askpass = std::env::current_exe()
        .map_err(|error| {
            i18n_fmt(
                &i18n("Could not locate the secure PIN helper: {0}"),
                &[&error.to_string()],
            )
        })?;
    let mut child = Command::new(program)
        .args(args)
        .env("SSH_ASKPASS", askpass)
        .env("SSH_ASKPASS_REQUIRE", "force")
        .env("ANDUINOS_YUBIKEY_ASKPASS", "1")
        .env("DISPLAY", std::env::var("DISPLAY").unwrap_or_else(|_| ":0".into()))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| {
            i18n_fmt(
                &i18n("Could not start {0}: {1}"),
                &[program, &error.to_string()],
            )
        })?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(format!("{pin}\n").as_bytes())
            .map_err(|error| {
                i18n_fmt(
                    &i18n("Could not provide the FIDO PIN: {0}"),
                    &[&error.to_string()],
                )
            })?;
    }
    child
        .wait_with_output()
        .map_err(|error| {
            i18n_fmt(
                &i18n("{0} failed: {1}"),
                &[program, &error.to_string()],
            )
        })
}

fn command_error_detail(output: &std::process::Output) -> String {
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let detail = format!("{stdout}\n{stderr}");
    let meaningful = detail
        .lines()
        .map(str::trim)
        .filter(|line| {
            !line.is_empty()
                && !line.starts_with("Enter PIN")
                && !line.starts_with("read_passphrase:")
        })
        .take(6)
        .collect::<Vec<_>>()
        .join("\n");
    if meaningful.is_empty() {
        i18n_fmt(
            &i18n("Command exited with {0}"),
            &[&output.status.to_string()],
        )
    } else {
        meaningful
    }
}

fn load_error(output: &std::process::Output) -> String {
    let meaningful = command_error_detail(output);
    if meaningful.starts_with(&i18n("Command exited with")) {
        i18n("OpenSSH could not load resident keys. The SSH agent contains no newly loaded identity.")
    } else {
        i18n_fmt(
            &i18n("OpenSSH could not load resident keys:\n{0}"),
            &[&meaningful],
        )
    }
}

pub fn remove_from_agent(public_key: &str) -> Result<(), String> {
    with_public_key_file(public_key, |path| {
        let path = path
            .to_str()
            .ok_or_else(|| i18n("Temporary public-key path is invalid."))?;
        run("ssh-add", &["-d", path], None).map(|_| ())
    })
}

pub fn test_signing(public_key: &str) -> Result<(), String> {
    with_public_key_file(public_key, |path| {
        let path = path
            .to_str()
            .ok_or_else(|| i18n("Temporary public-key path is invalid."))?;
        run("ssh-add", &["-T", path], None).map(|_| ())
    })
}

pub fn refresh_agent_matches(credentials: &mut [ResidentSshCredential]) {
    let fingerprints = agent_fingerprints();
    for credential in credentials {
        credential.loaded_in_agent = fingerprints.contains(&credential.fingerprint);
    }
}

fn with_public_key_file<T>(
    public_key: &str,
    operation: impl FnOnce(&std::path::Path) -> Result<T, String>,
) -> Result<T, String> {
    let mut file = tempfile::NamedTempFile::new()
        .map_err(|error| {
            i18n_fmt(
                &i18n("Could not create a temporary public-key file: {0}"),
                &[&error.to_string()],
            )
        })?;
    writeln!(file, "{public_key}")
        .map_err(|error| {
            i18n_fmt(
                &i18n("Could not write the temporary public key: {0}"),
                &[&error.to_string()],
            )
        })?;
    file.flush()
        .map_err(|error| {
            i18n_fmt(
                &i18n("Could not flush the temporary public key: {0}"),
                &[&error.to_string()],
            )
        })?;
    operation(file.path())
}

fn parse_relying_parties(output: &str) -> Vec<String> {
    output
        .lines()
        .filter_map(|line| {
            let (_, fields) = line.split_once(':')?;
            fields
                .split_whitespace()
                .find(|field| field.starts_with("ssh:"))
                .map(ToString::to_string)
        })
        .collect()
}

fn parse_credentials(output: &str) -> Vec<(String, String)> {
    output
        .lines()
        .filter_map(|line| {
            let (_, fields) = line.split_once(':')?;
            let fields: Vec<_> = fields.split_whitespace().collect();
            let credential_id = fields.first()?.to_string();
            let username = fields
                .get(2)
                .or_else(|| fields.get(1))
                .copied()
                .map(ToString::to_string)
                .unwrap_or_else(|| i18n("Unnamed SSH credential"));
            Some((credential_id, username))
        })
        .collect()
}

fn extract_pem(output: &str) -> Result<Vec<u8>, String> {
    let begin = output
        .find("-----BEGIN PUBLIC KEY-----")
        .ok_or_else(|| i18n("The resident credential did not return a public key."))?;
    let end_marker = "-----END PUBLIC KEY-----";
    let end = output[begin..]
        .find(end_marker)
        .map(|offset| begin + offset + end_marker.len())
        .ok_or_else(|| i18n("The resident credential returned an incomplete public key."))?;
    let body = output[begin..end]
        .lines()
        .filter(|line| !line.starts_with("-----"))
        .collect::<String>();
    STANDARD
        .decode(body)
        .map_err(|_| i18n("The resident credential returned malformed PEM data."))
}

fn ssh_public_blob(application: &str, der: &[u8]) -> Result<(String, Vec<u8>), String> {
    const ED25519_OID: &[u8] = &[0x2b, 0x65, 0x70];
    if der.windows(ED25519_OID.len()).any(|window| window == ED25519_OID)
        && der.len() >= 32
    {
        let algorithm = "sk-ssh-ed25519@openssh.com";
        let mut blob = Vec::new();
        push_ssh_string(&mut blob, algorithm.as_bytes());
        push_ssh_string(&mut blob, &der[der.len() - 32..]);
        push_ssh_string(&mut blob, application.as_bytes());
        return Ok((algorithm.into(), blob));
    }
    if der.len() >= 65 && der[der.len() - 65] == 0x04 {
        let algorithm = "sk-ecdsa-sha2-nistp256@openssh.com";
        let mut blob = Vec::new();
        push_ssh_string(&mut blob, algorithm.as_bytes());
        push_ssh_string(&mut blob, b"nistp256");
        push_ssh_string(&mut blob, &der[der.len() - 65..]);
        push_ssh_string(&mut blob, application.as_bytes());
        return Ok((algorithm.into(), blob));
    }
    Err(i18n("Unsupported resident SSH public-key algorithm."))
}

fn push_ssh_string(output: &mut Vec<u8>, value: &[u8]) {
    output.extend_from_slice(&(value.len() as u32).to_be_bytes());
    output.extend_from_slice(value);
}

fn fingerprint(blob: &[u8]) -> String {
    format!("SHA256:{}", STANDARD_NO_PAD.encode(Sha256::digest(blob)))
}

fn agent_fingerprints() -> HashSet<String> {
    run("ssh-add", &["-L"], None)
        .unwrap_or_default()
        .lines()
        .filter_map(|line| line.split_whitespace().nth(1))
        .filter_map(|encoded| STANDARD.decode(encoded).ok())
        .map(|blob| fingerprint(&blob))
        .collect()
}

fn run(program: &str, args: &[&str], input: Option<&str>) -> Result<String, String> {
    let mut child = Command::new(program)
        .args(args)
        .stdin(if input.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| {
            i18n_fmt(
                &i18n("Could not run {0}: {1}"),
                &[program, &error.to_string()],
            )
        })?;
    if let Some(input) = input {
        if let Some(mut stdin) = child.stdin.take() {
            stdin
                .write_all(format!("{input}\n").as_bytes())
                .map_err(|error| {
                    i18n_fmt(
                        &i18n("Could not provide the FIDO PIN: {0}"),
                        &[&error.to_string()],
                    )
                })?;
        }
    }
    let output = child
        .wait_with_output()
        .map_err(|error| {
            i18n_fmt(
                &i18n("{0} failed: {1}"),
                &[program, &error.to_string()],
            )
        })?;
    if output.status.success() {
        return Ok(String::from_utf8_lossy(&output.stdout).trim().to_string());
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    Err(if stderr.is_empty() {
        i18n_fmt(
            &i18n("{0} exited with {1}"),
            &[program, &output.status.to_string()],
        )
    } else {
        stderr
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ssh_relying_parties_only() {
        let output = "00: hash ssh:\n01: hash example.com\n02: hash ssh:ultra\n";
        assert_eq!(
            parse_relying_parties(output),
            vec!["ssh:".to_string(), "ssh:ultra".to_string()]
        );
    }

    #[test]
    fn constructs_ecdsa_sk_fingerprint() {
        let mut der = vec![0x30, 0x01, 0x00];
        der.push(0x04);
        der.extend(1_u8..=64);
        let (algorithm, blob) = ssh_public_blob("ssh:ultra", &der).unwrap();
        assert_eq!(algorithm, "sk-ecdsa-sha2-nistp256@openssh.com");
        assert!(fingerprint(&blob).starts_with("SHA256:"));
    }

    #[test]
    fn load_error_omits_pin_prompt() {
        #[cfg(unix)]
        use std::os::unix::process::ExitStatusExt;
        let output = std::process::Output {
            status: std::process::ExitStatus::from_raw(256),
            stdout: b"Enter PIN for authenticator: \n".to_vec(),
            stderr: b"Unable to load resident keys: device not found\n".to_vec(),
        };
        let error = load_error(&output);
        assert!(!error.contains("Enter PIN"));
        assert!(error.contains("device not found"));
    }

    #[test]
    fn validates_safe_create_defaults() {
        let options = CreateOptions {
            device: "/dev/hidraw7".into(),
            algorithm: "ecdsa-sk".into(),
            application: "ssh:anduinos".into(),
            username: "anduin".into(),
            comment: "anduin@lunar".into(),
            output_path: "/tmp/id_ecdsa_sk_yubikey".into(),
            verify_required: false,
        };
        assert!(validate_create_options(&options).is_ok());
    }

    #[test]
    fn rejects_unsafe_create_metadata_and_relative_paths() {
        let options = CreateOptions {
            device: "/dev/hidraw1;touch".into(),
            algorithm: "ecdsa-sk".into(),
            application: "https://example.com".into(),
            username: "anduin\nroot".into(),
            comment: "label".into(),
            output_path: "id_ecdsa_sk".into(),
            verify_required: false,
        };
        assert!(validate_create_options(&options).is_err());
    }

    #[test]
    fn keygen_args_keep_safe_defaults_and_exact_device() {
        let options = CreateOptions {
            device: "/dev/hidraw9".into(),
            algorithm: "ecdsa-sk".into(),
            application: "ssh:anduinos".into(),
            username: "anduin".into(),
            comment: "anduin@lunar".into(),
            output_path: "/tmp/id_ecdsa_sk_yubikey".into(),
            verify_required: true,
        };
        let args = create_keygen_args(&options);
        assert!(args.windows(2).any(|pair| pair == ["-t", "ecdsa-sk"]));
        assert!(args.windows(2).any(|pair| pair == ["-O", "resident"]));
        assert!(args
            .windows(2)
            .any(|pair| pair == ["-O", "device=/dev/hidraw9"]));
        assert!(args
            .windows(2)
            .any(|pair| pair == ["-O", "verify-required"]));
        assert!(!args.iter().any(|argument| argument == "no-touch-required"));
    }
}
