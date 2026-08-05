use std::ffi::OsStr;
use std::fmt;
use std::fs::{self, File};
use std::io::Read;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde::Deserialize;
use sha2::{Digest, Sha256};

use crate::model::DeploymentRecord;

const SECURE_BOOT_CTL: &str = "/usr/bin/anduinos-securebootctl";
const SBVERIFY: &str = "/usr/bin/sbverify";
const MODINFO: &str = "/usr/sbin/modinfo";
const CURRENT_MOK_CERTIFICATE: &str = "/var/lib/shim-signed/mok/MOK.der";
const MAX_TOOL_OUTPUT: usize = 4 * 1024 * 1024;
const MAX_DKMS_MODULES: usize = 4096;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SecureBootErrorCode {
    InspectionFailed,
    InvalidKernel,
    MissingTrust,
    CertificateChanged,
    UntrustedModule,
    UnsafeFilesystem,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SecureBootError {
    pub code: SecureBootErrorCode,
    pub message: String,
}

impl SecureBootError {
    fn new(code: SecureBootErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for SecureBootError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for SecureBootError {}

pub trait SecureBootToolRunner: Clone {
    fn output(&self, program: &Path, arguments: &[&OsStr]) -> Result<String, SecureBootError>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemSecureBootToolRunner;

impl SecureBootToolRunner for SystemSecureBootToolRunner {
    fn output(&self, program: &Path, arguments: &[&OsStr]) -> Result<String, SecureBootError> {
        let output = Command::new(program)
            .args(arguments)
            .env_clear()
            .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
            .env("LC_ALL", "C")
            .output()
            .map_err(|error| {
                SecureBootError::new(
                    SecureBootErrorCode::InspectionFailed,
                    format!("Could not execute {}: {error}", program.display()),
                )
            })?;
        if !output.status.success() {
            return Err(SecureBootError::new(
                SecureBootErrorCode::InspectionFailed,
                format!("{} exited with {}", program.display(), output.status),
            ));
        }
        if output.stdout.len() > MAX_TOOL_OUTPUT {
            return Err(SecureBootError::new(
                SecureBootErrorCode::InspectionFailed,
                format!("{} returned excessive output", program.display()),
            ));
        }
        String::from_utf8(output.stdout).map_err(|_| {
            SecureBootError::new(
                SecureBootErrorCode::InspectionFailed,
                format!("{} returned non-UTF-8 output", program.display()),
            )
        })
    }
}

#[derive(Clone, Debug)]
pub struct SecureBootValidator<R = SystemSecureBootToolRunner> {
    runner: R,
    secure_boot_ctl: PathBuf,
    sbverify: PathBuf,
    modinfo: PathBuf,
    current_mok_certificate: PathBuf,
}

impl Default for SecureBootValidator<SystemSecureBootToolRunner> {
    fn default() -> Self {
        Self::new(SystemSecureBootToolRunner)
    }
}

impl<R: SecureBootToolRunner> SecureBootValidator<R> {
    pub fn new(runner: R) -> Self {
        Self {
            runner,
            secure_boot_ctl: PathBuf::from(SECURE_BOOT_CTL),
            sbverify: PathBuf::from(SBVERIFY),
            modinfo: PathBuf::from(MODINFO),
            current_mok_certificate: PathBuf::from(CURRENT_MOK_CERTIFICATE),
        }
    }

    #[cfg(test)]
    fn with_paths(
        runner: R,
        secure_boot_ctl: PathBuf,
        sbverify: PathBuf,
        modinfo: PathBuf,
        current_mok_certificate: PathBuf,
    ) -> Self {
        Self {
            runner,
            secure_boot_ctl,
            sbverify,
            modinfo,
            current_mok_certificate,
        }
    }

    /// Validate only the trust requirements that firmware enforces at boot.
    ///
    /// Secure Boot disabled or unsupported is explicit and skips signature checks.
    /// An indeterminate state fails closed rather than masquerading as disabled.
    /// When it is enabled, the recovery kernel must be signed. If the target contains DKMS
    /// modules, its recorded MOK must still be the currently enrolled MOK and every module must
    /// carry that key identifier.
    pub fn verify_target(
        &self,
        snapshot_root: &Path,
        record: &DeploymentRecord,
    ) -> Result<(), SecureBootError> {
        let status_json = self.runner.output(
            &self.secure_boot_ctl,
            &[OsStr::new("status"), OsStr::new("--json")],
        )?;
        let status: ToolkitStatus = serde_json::from_str(&status_json).map_err(|error| {
            SecureBootError::new(
                SecureBootErrorCode::InspectionFailed,
                format!("Secure Boot toolkit returned invalid state: {error}"),
            )
        })?;
        if status.schema != 2 {
            return Err(SecureBootError::new(
                SecureBootErrorCode::InspectionFailed,
                format!("Unsupported Secure Boot toolkit schema {}", status.schema),
            ));
        }
        let status_enabled = matches!(status.secure_boot.status, ToolkitSecureBootStatus::Enabled);
        if status.secure_boot.enabled != status_enabled {
            return Err(SecureBootError::new(
                SecureBootErrorCode::InspectionFailed,
                "Secure Boot toolkit returned contradictory state",
            ));
        }
        match status.secure_boot.status {
            ToolkitSecureBootStatus::Disabled | ToolkitSecureBootStatus::Unsupported => {
                return Ok(());
            }
            ToolkitSecureBootStatus::Unknown => {
                return Err(SecureBootError::new(
                    SecureBootErrorCode::InspectionFailed,
                    "Secure Boot state could not be determined",
                ));
            }
            ToolkitSecureBootStatus::Enabled => {}
        }

        let kernel_release = record.kernel_release.as_deref().ok_or_else(|| {
            SecureBootError::new(
                SecureBootErrorCode::InvalidKernel,
                "Recovery point has no kernel release",
            )
        })?;
        let kernel = snapshot_root
            .join("boot")
            .join(format!("vmlinuz-{kernel_release}"));
        ensure_regular_file(&kernel, "recovery kernel")?;
        self.runner
            .output(&self.sbverify, &[OsStr::new("--list"), kernel.as_os_str()])
            .map_err(|error| {
                SecureBootError::new(
                    SecureBootErrorCode::InvalidKernel,
                    format!("Recovery kernel is not Secure Boot signed: {error}"),
                )
            })?;

        let modules_root = snapshot_root
            .join("lib/modules")
            .join(kernel_release)
            .join("updates/dkms");
        let modules = collect_dkms_modules(&modules_root)?;
        if modules.is_empty() {
            return Ok(());
        }

        if !status.secure_boot.key_present
            || !status.secure_boot.certificate_present
            || !status.secure_boot.enrolled
        {
            return Err(SecureBootError::new(
                SecureBootErrorCode::MissingTrust,
                "Secure Boot is enabled, but the MOK required by this recovery point is not enrolled",
            ));
        }
        let expected_mok = record.mok_certificate_sha256.as_deref().ok_or_else(|| {
            SecureBootError::new(
                SecureBootErrorCode::MissingTrust,
                "Recovery point contains DKMS modules but records no MOK certificate",
            )
        })?;
        let current_mok = hash_regular_file(&self.current_mok_certificate)?;
        if current_mok != expected_mok {
            return Err(SecureBootError::new(
                SecureBootErrorCode::CertificateChanged,
                "The currently enrolled MOK differs from the certificate recorded by this recovery point",
            ));
        }
        let expected_key = normalize_key(
            status
                .secure_boot
                .certificate_serial
                .as_deref()
                .unwrap_or_default(),
        );
        if expected_key.is_empty() {
            return Err(SecureBootError::new(
                SecureBootErrorCode::MissingTrust,
                "The enrolled MOK has no usable key identifier",
            ));
        }
        for module in modules {
            let signature = self.runner.output(
                &self.modinfo,
                &[OsStr::new("-F"), OsStr::new("sig_key"), module.as_os_str()],
            )?;
            if normalize_key(&signature) != expected_key {
                return Err(SecureBootError::new(
                    SecureBootErrorCode::UntrustedModule,
                    format!(
                        "DKMS module {} is not signed by the enrolled MOK",
                        module.display()
                    ),
                ));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Deserialize)]
struct ToolkitStatus {
    schema: u32,
    secure_boot: ToolkitSecureBootState,
}

#[derive(Debug, Deserialize)]
struct ToolkitSecureBootState {
    enabled: bool,
    status: ToolkitSecureBootStatus,
    key_present: bool,
    certificate_present: bool,
    enrolled: bool,
    certificate_serial: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
enum ToolkitSecureBootStatus {
    Enabled,
    Disabled,
    Unsupported,
    Unknown,
}

fn collect_dkms_modules(root: &Path) -> Result<Vec<PathBuf>, SecureBootError> {
    match fs::symlink_metadata(root) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => {
            return Err(SecureBootError::new(
                SecureBootErrorCode::UnsafeFilesystem,
                format!("Could not inspect {}: {error}", root.display()),
            ));
        }
        Ok(metadata) if !metadata.file_type().is_dir() => {
            return Err(SecureBootError::new(
                SecureBootErrorCode::UnsafeFilesystem,
                format!("{} is not a real directory", root.display()),
            ));
        }
        Ok(_) => {}
    }

    let mut pending = vec![root.to_path_buf()];
    let mut modules = Vec::new();
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).map_err(|error| {
            SecureBootError::new(
                SecureBootErrorCode::UnsafeFilesystem,
                format!("Could not read {}: {error}", directory.display()),
            )
        })? {
            let entry = entry.map_err(|error| {
                SecureBootError::new(
                    SecureBootErrorCode::UnsafeFilesystem,
                    format!("Could not inspect DKMS directory entry: {error}"),
                )
            })?;
            let metadata = fs::symlink_metadata(entry.path()).map_err(|error| {
                SecureBootError::new(
                    SecureBootErrorCode::UnsafeFilesystem,
                    format!("Could not inspect {}: {error}", entry.path().display()),
                )
            })?;
            if metadata.file_type().is_symlink() {
                return Err(SecureBootError::new(
                    SecureBootErrorCode::UnsafeFilesystem,
                    format!("DKMS path {} is a symbolic link", entry.path().display()),
                ));
            }
            if metadata.file_type().is_dir() {
                pending.push(entry.path());
                continue;
            }
            if !metadata.file_type().is_file() {
                return Err(SecureBootError::new(
                    SecureBootErrorCode::UnsafeFilesystem,
                    format!("DKMS path {} is not a regular file", entry.path().display()),
                ));
            }
            if is_kernel_module(&entry.path()) {
                modules.push(entry.path());
                if modules.len() > MAX_DKMS_MODULES {
                    return Err(SecureBootError::new(
                        SecureBootErrorCode::UnsafeFilesystem,
                        "Recovery point contains an excessive number of DKMS modules",
                    ));
                }
            }
        }
    }
    modules.sort();
    Ok(modules)
}

fn is_kernel_module(path: &Path) -> bool {
    let name = path.file_name().and_then(OsStr::to_str).unwrap_or_default();
    name.ends_with(".ko") || name.ends_with(".ko.xz") || name.ends_with(".ko.zst")
}

fn ensure_regular_file(path: &Path, description: &str) -> Result<(), SecureBootError> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        SecureBootError::new(
            SecureBootErrorCode::UnsafeFilesystem,
            format!(
                "Could not inspect {description} {}: {error}",
                path.display()
            ),
        )
    })?;
    if metadata.file_type().is_file() && metadata.nlink() == 1 {
        Ok(())
    } else {
        Err(SecureBootError::new(
            SecureBootErrorCode::UnsafeFilesystem,
            format!(
                "{description} {} is not a private regular file",
                path.display()
            ),
        ))
    }
}

fn hash_regular_file(path: &Path) -> Result<String, SecureBootError> {
    ensure_regular_file(path, "MOK certificate")?;
    let mut file = File::open(path).map_err(|error| {
        SecureBootError::new(
            SecureBootErrorCode::UnsafeFilesystem,
            format!("Could not open MOK certificate {}: {error}", path.display()),
        )
    })?;
    let mut hash = Sha256::new();
    let mut buffer = [0_u8; 16 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|error| {
            SecureBootError::new(
                SecureBootErrorCode::UnsafeFilesystem,
                format!("Could not read MOK certificate {}: {error}", path.display()),
            )
        })?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

fn normalize_key(value: &str) -> String {
    value
        .chars()
        .filter(|character| character.is_ascii_hexdigit())
        .flat_map(char::to_lowercase)
        .collect()
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;
    use std::fs;
    use std::sync::{Arc, Mutex};

    use chrono::Utc;

    use crate::DEPLOYMENT_SCHEMA_VERSION;
    use crate::model::{DeploymentId, DeploymentKind, DeploymentRecord};

    use super::*;

    #[derive(Clone)]
    struct FakeRunner {
        responses: Arc<Mutex<VecDeque<Result<String, SecureBootError>>>>,
    }

    impl FakeRunner {
        fn new(responses: Vec<Result<String, SecureBootError>>) -> Self {
            Self {
                responses: Arc::new(Mutex::new(responses.into())),
            }
        }
    }

    impl SecureBootToolRunner for FakeRunner {
        fn output(
            &self,
            _program: &Path,
            _arguments: &[&OsStr],
        ) -> Result<String, SecureBootError> {
            self.responses.lock().unwrap().pop_front().unwrap()
        }
    }

    fn record(kernel: &str, mok: Option<String>) -> DeploymentRecord {
        DeploymentRecord {
            schema_version: DEPLOYMENT_SCHEMA_VERSION,
            id: DeploymentId::new(),
            parent_id: None,
            kind: DeploymentKind::Manual,
            state: crate::model::DeploymentState::Ready,
            created_at: Utc::now(),
            title: "test".into(),
            reason: "test".into(),
            schedule_id: None,
            snapshot_uuid: Some("22222222-2222-2222-2222-222222222222".into()),
            snapshot_parent_uuid: None,
            kernel_release: Some(kernel.into()),
            initramfs_sha256: Some("a".repeat(64)),
            boot_artifact_sha256: Some("b".repeat(64)),
            dpkg_status_sha256: Some("c".repeat(64)),
            mok_certificate_sha256: mok,
            pinned: false,
            failure: None,
        }
    }

    fn status(enabled: bool, enrolled: bool, serial: Option<&str>) -> String {
        serde_json::json!({
            "schema": 2,
            "secure_boot": {
                "enabled": enabled,
                "status": if enabled { "enabled" } else { "disabled" },
                "key_present": enrolled,
                "certificate_present": enrolled,
                "enrolled": enrolled,
                "certificate_serial": serial,
                "enrollment_pending": false,
                "dkms_available": true,
                "headers_available": true,
                "configuration_present": true
            },
            "dkms": {}
        })
        .to_string()
    }

    fn validator(
        root: &Path,
        runner: FakeRunner,
        certificate: PathBuf,
    ) -> SecureBootValidator<FakeRunner> {
        SecureBootValidator::with_paths(
            runner,
            root.join("securebootctl"),
            root.join("sbverify"),
            root.join("modinfo"),
            certificate,
        )
    }

    #[test]
    fn disabled_secure_boot_skips_target_signature_requirements() {
        let temp = tempfile_dir();
        validator(
            &temp,
            FakeRunner::new(vec![Ok(status(false, false, None))]),
            temp.join("missing.der"),
        )
        .verify_target(&temp.join("missing-snapshot"), &record("test", None))
        .unwrap();
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn unsupported_secure_boot_skips_target_signature_requirements() {
        let temp = tempfile_dir();
        let payload = status(false, false, None).replace("\"disabled\"", "\"unsupported\"");
        validator(
            &temp,
            FakeRunner::new(vec![Ok(payload)]),
            temp.join("missing.der"),
        )
        .verify_target(&temp.join("missing-snapshot"), &record("test", None))
        .unwrap();
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn unknown_secure_boot_state_fails_closed() {
        let temp = tempfile_dir();
        let payload = status(false, false, None).replace("\"disabled\"", "\"unknown\"");
        let error = validator(
            &temp,
            FakeRunner::new(vec![Ok(payload)]),
            temp.join("missing.der"),
        )
        .verify_target(&temp.join("missing-snapshot"), &record("test", None))
        .unwrap_err();
        assert_eq!(error.code, SecureBootErrorCode::InspectionFailed);
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn boolean_only_toolkit_schema_fails_closed() {
        let temp = tempfile_dir();
        let payload = status(false, false, None).replace("\"schema\":2", "\"schema\":1");
        let error = validator(
            &temp,
            FakeRunner::new(vec![Ok(payload)]),
            temp.join("missing.der"),
        )
        .verify_target(&temp.join("missing-snapshot"), &record("test", None))
        .unwrap_err();
        assert_eq!(error.code, SecureBootErrorCode::InspectionFailed);
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn enabled_secure_boot_accepts_signed_kernel_without_dkms() {
        let temp = tempfile_dir();
        let snapshot = temp.join("snapshot");
        let kernel = snapshot.join("boot/vmlinuz-test");
        fs::create_dir_all(kernel.parent().unwrap()).unwrap();
        fs::write(&kernel, b"kernel").unwrap();
        validator(
            &temp,
            FakeRunner::new(vec![Ok(status(true, false, None)), Ok("signature".into())]),
            temp.join("missing.der"),
        )
        .verify_target(&snapshot, &record("test", None))
        .unwrap();
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn enabled_secure_boot_rejects_unenrolled_dkms_key() {
        let temp = tempfile_dir();
        let snapshot = temp.join("snapshot");
        let kernel = snapshot.join("boot/vmlinuz-test");
        let module = snapshot.join("lib/modules/test/updates/dkms/example.ko");
        fs::create_dir_all(kernel.parent().unwrap()).unwrap();
        fs::create_dir_all(module.parent().unwrap()).unwrap();
        fs::write(&kernel, b"kernel").unwrap();
        fs::write(&module, b"module").unwrap();
        let error = validator(
            &temp,
            FakeRunner::new(vec![Ok(status(true, false, None)), Ok("signature".into())]),
            temp.join("missing.der"),
        )
        .verify_target(&snapshot, &record("test", None))
        .unwrap_err();
        assert_eq!(error.code, SecureBootErrorCode::MissingTrust);
        fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn enabled_secure_boot_accepts_matching_enrolled_dkms_key() {
        let temp = tempfile_dir();
        let snapshot = temp.join("snapshot");
        let kernel = snapshot.join("boot/vmlinuz-test");
        let module = snapshot.join("lib/modules/test/updates/dkms/example.ko.zst");
        let certificate = temp.join("MOK.der");
        fs::create_dir_all(kernel.parent().unwrap()).unwrap();
        fs::create_dir_all(module.parent().unwrap()).unwrap();
        fs::write(&kernel, b"kernel").unwrap();
        fs::write(&module, b"module").unwrap();
        fs::write(&certificate, b"certificate").unwrap();
        let digest = hash_regular_file(&certificate).unwrap();
        validator(
            &temp,
            FakeRunner::new(vec![
                Ok(status(true, true, Some("AA:12"))),
                Ok("signature".into()),
                Ok("aa12\n".into()),
            ]),
            certificate,
        )
        .verify_target(&snapshot, &record("test", Some(digest)))
        .unwrap();
        fs::remove_dir_all(temp).unwrap();
    }

    fn tempfile_dir() -> PathBuf {
        let path =
            std::env::temp_dir().join(format!("waypoint-secure-boot-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&path).unwrap();
        path
    }
}
