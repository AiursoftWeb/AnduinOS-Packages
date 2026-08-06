use std::ffi::OsStr;
use std::fmt;
use std::fs;
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::RECOVERY_STORE_ROOT;
use crate::model::DeploymentState;
use crate::store::DeploymentStore;
use crate::transaction::{RollbackPhase, TransactionStore};

const GRUB_MKRELPATH: &str = "/usr/bin/grub-mkrelpath";
const GRUB_EDITENV: &str = "/usr/bin/grub-editenv";
const MOUNTPOINT: &str = "/usr/bin/mountpoint";
pub const GRUB_EXTERNAL_ENVIRONMENT: &str =
    "/boot/efi/EFI/anduinos/btrfs-snapshots-manager-grubenv";
pub const GRUB_NEXT_ENTRY_VARIABLE: &str = "btrfs_snapshots_manager_next_entry";
const MAX_TOOL_OUTPUT: usize = 4096;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BootErrorCode {
    NoTransaction,
    InvalidTransaction,
    InvalidDeployment,
    UnsupportedEnvironment,
    UnsafeOutput,
    CommandFailed,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BootError {
    pub code: BootErrorCode,
    pub message: String,
}

impl BootError {
    fn new(code: BootErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for BootError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl std::error::Error for BootError {}

pub trait BootToolRunner: Clone + Send + Sync + 'static {
    fn output(&self, program: &Path, arguments: &[&OsStr]) -> Result<String, BootError>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SystemBootToolRunner;

impl BootToolRunner for SystemBootToolRunner {
    fn output(&self, program: &Path, arguments: &[&OsStr]) -> Result<String, BootError> {
        let output = Command::new(program)
            .args(arguments)
            .env_clear()
            .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
            .env("LC_ALL", "C")
            .output()
            .map_err(|error| {
                BootError::new(
                    BootErrorCode::CommandFailed,
                    format!("Could not execute {}: {error}", program.display()),
                )
            })?;
        if !output.status.success() {
            return Err(BootError::new(
                BootErrorCode::CommandFailed,
                format!("{} exited with {}", program.display(), output.status),
            ));
        }
        if output.stdout.len() > MAX_TOOL_OUTPUT {
            return Err(BootError::new(
                BootErrorCode::UnsafeOutput,
                format!("{} returned excessive output", program.display()),
            ));
        }
        String::from_utf8(output.stdout).map_err(|_| {
            BootError::new(
                BootErrorCode::UnsafeOutput,
                format!("{} returned non-UTF-8 output", program.display()),
            )
        })
    }
}

#[derive(Clone, Debug)]
pub struct BootIntegration<R = SystemBootToolRunner> {
    snapshot_root: PathBuf,
    runner: R,
}

impl Default for BootIntegration<SystemBootToolRunner> {
    fn default() -> Self {
        Self::new(RECOVERY_STORE_ROOT, SystemBootToolRunner)
    }
}

impl BootIntegration<SystemBootToolRunner> {
    /// Provision the external GRUB environment at runtime as well as from the
    /// package maintainer script. ISO installations copy an already configured
    /// root filesystem, so their target ESP was not mounted when postinst ran.
    pub fn ensure_external_environment_block(&self) -> Result<String, BootError> {
        let efi_root = Path::new("/boot/efi");
        self.runner.output(
            Path::new(MOUNTPOINT),
            &[OsStr::new("-q"), efi_root.as_os_str()],
        )?;
        ensure_real_directory(efi_root, false)?;

        let efi_directory = efi_root.join("EFI");
        ensure_real_directory(&efi_directory, true)?;
        let anduinos_directory = efi_directory.join("anduinos");
        ensure_real_directory(&anduinos_directory, true)?;

        let environment = Path::new(GRUB_EXTERNAL_ENVIRONMENT);
        match fs::symlink_metadata(environment) {
            Ok(_) => validate_environment_file(environment)?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                self.runner.output(
                    Path::new(GRUB_EDITENV),
                    &[environment.as_os_str(), OsStr::new("create")],
                )?;
                validate_environment_file(environment)?;
            }
            Err(error) => {
                return Err(BootError::new(
                    BootErrorCode::UnsupportedEnvironment,
                    format!("Could not inspect {}: {error}", environment.display()),
                ));
            }
        }
        protect_environment_file(environment)?;
        self.verify_external_environment_block()
    }
}

impl<R: BootToolRunner> BootIntegration<R> {
    pub fn new(snapshot_root: impl Into<PathBuf>, runner: R) -> Self {
        Self {
            snapshot_root: snapshot_root.into(),
            runner,
        }
    }

    pub fn verify_external_environment_block(&self) -> Result<String, BootError> {
        let output = self.runner.output(
            Path::new(GRUB_EDITENV),
            &[OsStr::new(GRUB_EXTERNAL_ENVIRONMENT), OsStr::new("list")],
        )?;
        if output.lines().any(|line| {
            line.len() > 1024
                || line.chars().any(|character| character.is_control())
                || !line.contains('=')
        }) {
            return Err(BootError::new(
                BootErrorCode::UnsafeOutput,
                "GRUB returned unsafe external environment data",
            ));
        }
        Ok(GRUB_EXTERNAL_ENVIRONMENT.to_string())
    }

    pub fn recovery_menu_entry(&self) -> Result<Option<String>, BootError> {
        let Some(transaction) = TransactionStore::new(&self.snapshot_root)
            .load_pending()
            .map_err(|error| BootError::new(BootErrorCode::InvalidTransaction, error.message))?
        else {
            return Ok(None);
        };
        if !matches!(
            transaction.phase,
            RollbackPhase::Preparing | RollbackPhase::Armed
        ) {
            return Ok(None);
        }
        let target = DeploymentStore::new(&self.snapshot_root)
            .load_record(transaction.target_deployment_id)
            .map_err(|error| BootError::new(BootErrorCode::InvalidDeployment, error.message))?;
        if target.state != DeploymentState::PendingRollback || target.failure.is_some() {
            return Err(BootError::new(
                BootErrorCode::InvalidDeployment,
                "GRUB recovery target is not pending rollback",
            ));
        }
        if target.kernel_release.as_deref() != Some(&transaction.kernel_release) {
            return Err(BootError::new(
                BootErrorCode::InvalidDeployment,
                "GRUB recovery kernel does not match the transaction",
            ));
        }
        let root = self
            .snapshot_root
            .join("deployments")
            .join(transaction.target_deployment_id.to_string())
            .join("root");
        let kernel = root
            .join("boot")
            .join(format!("vmlinuz-{}", transaction.kernel_release));
        let initramfs = root
            .join("boot")
            .join(format!("initrd.img-{}", transaction.kernel_release));
        ensure_regular_file(&kernel)?;
        ensure_regular_file(&initramfs)?;
        let kernel_path = self.grub_path(&kernel)?;
        let initramfs_path = self.grub_path(&initramfs)?;
        let id = transaction.id.to_string();
        let entry_id = &transaction.grub_entry_id;
        let fs_uuid = &transaction.root_filesystem_uuid;
        Ok(Some(format!(
            "menuentry 'Disk Snapshots Manager recovery' --class anduinos --class gnu-linux --id '{entry_id}' {{\n\
             \tinsmod btrfs\n\
             \tsearch --no-floppy --fs-uuid --set=root {fs_uuid}\n\
             \techo 'Starting AnduinOS system recovery…'\n\
             \tlinux {kernel_path} root=UUID={fs_uuid} ro rootflags=subvol=@root anduinos.btrfs_snapshots_manager={id}\n\
             \tinitrd {initramfs_path}\n\
             }}\n"
        )))
    }

    pub fn arm_pending_once(&self) -> Result<String, BootError> {
        self.verify_external_environment_block()?;
        let transaction = TransactionStore::new(&self.snapshot_root)
            .load_pending()
            .map_err(|error| BootError::new(BootErrorCode::InvalidTransaction, error.message))?
            .ok_or_else(|| {
                BootError::new(
                    BootErrorCode::NoTransaction,
                    "No rollback transaction is pending",
                )
            })?;
        if transaction.phase != RollbackPhase::Armed {
            return Err(BootError::new(
                BootErrorCode::InvalidTransaction,
                "Only an armed rollback transaction can select the one-shot GRUB entry",
            ));
        }
        let selection = format!("{GRUB_NEXT_ENTRY_VARIABLE}={}", transaction.grub_entry_id);
        self.runner.output(
            Path::new(GRUB_EDITENV),
            &[
                OsStr::new(GRUB_EXTERNAL_ENVIRONMENT),
                OsStr::new("set"),
                OsStr::new(&selection),
            ],
        )?;
        Ok(transaction.grub_entry_id)
    }

    /// Clear Disk Snapshots Manager's selector without treating an already-clear GRUB
    /// environment as an error. Cleanup runs before and after the selector is
    /// armed, and grub-editenv returns a failure for an absent variable.
    pub fn clear_pending_once(&self) -> Result<(), BootError> {
        let output = self.runner.output(
            Path::new(GRUB_EDITENV),
            &[OsStr::new(GRUB_EXTERNAL_ENVIRONMENT), OsStr::new("list")],
        )?;
        let selection = format!("{GRUB_NEXT_ENTRY_VARIABLE}=");
        if !output.lines().any(|line| line.starts_with(&selection)) {
            return Ok(());
        }
        self.runner.output(
            Path::new(GRUB_EDITENV),
            &[
                OsStr::new(GRUB_EXTERNAL_ENVIRONMENT),
                OsStr::new("unset"),
                OsStr::new(GRUB_NEXT_ENTRY_VARIABLE),
            ],
        )?;
        Ok(())
    }

    fn grub_path(&self, path: &Path) -> Result<String, BootError> {
        let output = self
            .runner
            .output(Path::new(GRUB_MKRELPATH), &[path.as_os_str()])?;
        let value = output.trim();
        if !value.starts_with('/')
            || value.len() > 1024
            || !value
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"/@._+-=".contains(&byte))
        {
            return Err(BootError::new(
                BootErrorCode::UnsafeOutput,
                "grub-mkrelpath returned an unsafe recovery path",
            ));
        }
        Ok(value.to_string())
    }
}

fn ensure_regular_file(path: &Path) -> Result<(), BootError> {
    let metadata = std::fs::symlink_metadata(path).map_err(|error| {
        BootError::new(
            BootErrorCode::InvalidDeployment,
            format!(
                "Recovery boot artifact {} is unavailable: {error}",
                path.display()
            ),
        )
    })?;
    if metadata.file_type().is_file() {
        Ok(())
    } else {
        Err(BootError::new(
            BootErrorCode::InvalidDeployment,
            format!(
                "Recovery boot artifact {} is not a regular file",
                path.display()
            ),
        ))
    }
}

fn ensure_real_directory(path: &Path, create: bool) -> Result<(), BootError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => Ok(()),
        Ok(_) => Err(BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            format!("{} is not a real directory", path.display()),
        )),
        Err(error) if create && error.kind() == std::io::ErrorKind::NotFound => {
            fs::DirBuilder::new()
                .mode(0o700)
                .create(path)
                .map_err(|error| {
                    BootError::new(
                        BootErrorCode::UnsupportedEnvironment,
                        format!("Could not create {}: {error}", path.display()),
                    )
                })
        }
        Err(error) => Err(BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            format!("Could not inspect {}: {error}", path.display()),
        )),
    }
}

fn validate_environment_file(path: &Path) -> Result<(), BootError> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            format!("Could not inspect {}: {error}", path.display()),
        )
    })?;
    if !metadata.file_type().is_file() || metadata.len() != 1024 {
        return Err(BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            "The external GRUB environment is not a regular 1024-byte environment block",
        ));
    }
    Ok(())
}

fn protect_environment_file(path: &Path) -> Result<(), BootError> {
    validate_environment_file(path)?;
    let permissions = fs::symlink_metadata(path)
        .map_err(|error| {
            BootError::new(
                BootErrorCode::UnsupportedEnvironment,
                format!("Could not inspect {}: {error}", path.display()),
            )
        })?
        .permissions();

    // The ESP is VFAT. Its apparent Unix mode is synthesized from fmask and
    // chmod(2) can return EROFS even while the filesystem is mounted rw. Do
    // not require an operation the filesystem cannot represent: the security
    // property we need is that only root can change the environment block.
    if permissions.mode() & 0o022 == 0 {
        return Ok(());
    }

    fs::set_permissions(path, fs::Permissions::from_mode(0o600)).map_err(|error| {
        BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            format!("Could not protect {}: {error}", path.display()),
        )
    })?;
    let hardened = fs::symlink_metadata(path)
        .map_err(|error| {
            BootError::new(
                BootErrorCode::UnsupportedEnvironment,
                format!("Could not re-check {}: {error}", path.display()),
            )
        })?
        .permissions();
    if hardened.mode() & 0o022 != 0 {
        return Err(BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            format!("{} is writable by non-root users", path.display()),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::os::unix::fs::symlink;

    use chrono::Utc;

    use crate::DEPLOYMENT_SCHEMA_VERSION;
    use crate::model::{DeploymentId, DeploymentKind, DeploymentRecord};
    use crate::transaction::{RollbackTransaction, TransactionStore};

    use super::*;

    #[derive(Clone)]
    struct FakeTools {
        env: String,
        unsafe_path: bool,
        calls: std::sync::Arc<std::sync::Mutex<Vec<String>>>,
    }

    impl BootToolRunner for FakeTools {
        fn output(&self, program: &Path, arguments: &[&OsStr]) -> Result<String, BootError> {
            self.calls.lock().unwrap().push(format!(
                "{} {}",
                program.display(),
                arguments
                    .iter()
                    .map(|argument| argument.to_string_lossy())
                    .collect::<Vec<_>>()
                    .join(" ")
            ));
            if program == Path::new(GRUB_EDITENV) {
                return Ok(self.env.clone());
            }
            let path = Path::new(arguments[0]);
            if self.unsafe_path {
                Ok("/safe\nlinux /injected".into())
            } else {
                Ok(format!(
                    "/{}\n",
                    path.file_name().unwrap().to_string_lossy()
                ))
            }
        }
    }

    struct Environment {
        root: PathBuf,
        transaction: RollbackTransaction,
    }

    impl Environment {
        fn new() -> Self {
            let root = std::env::temp_dir()
                .join(format!("snapshots-manager-boot-{}", uuid::Uuid::new_v4()));
            fs::create_dir_all(root.join("metadata")).unwrap();
            fs::create_dir_all(root.join("transactions")).unwrap();
            let target = DeploymentRecord {
                schema_version: DEPLOYMENT_SCHEMA_VERSION,
                id: DeploymentId::new(),
                parent_id: None,
                kind: DeploymentKind::Manual,
                state: DeploymentState::PendingRollback,
                created_at: Utc::now(),
                title: "Target".into(),
                reason: "Boot integration test".into(),
                schedule_id: None,
                snapshot_uuid: Some("aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb".into()),
                snapshot_parent_uuid: None,
                kernel_release: Some("test-kernel".into()),
                initramfs_sha256: Some("a".repeat(64)),
                boot_artifact_sha256: Some("b".repeat(64)),
                dpkg_status_sha256: Some("c".repeat(64)),
                mok_certificate_sha256: None,
                pinned: false,
                failure: None,
            };
            fs::write(
                root.join("metadata").join(format!("{}.json", target.id)),
                serde_json::to_vec(&target).unwrap(),
            )
            .unwrap();
            let boot = root
                .join("deployments")
                .join(target.id.to_string())
                .join("root/boot");
            fs::create_dir_all(&boot).unwrap();
            fs::write(boot.join("vmlinuz-test-kernel"), "kernel").unwrap();
            fs::write(boot.join("initrd.img-test-kernel"), "initramfs").unwrap();
            let mut transaction = RollbackTransaction::new(
                target.id,
                DeploymentId::new(),
                "dddddddd-1111-4222-8333-eeeeeeeeeeee",
                "test-kernel",
            );
            transaction
                .transition(RollbackPhase::Armed, Utc::now())
                .unwrap();
            TransactionStore::new(&root).create(&transaction).unwrap();
            Self { root, transaction }
        }
    }

    impl Drop for Environment {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.root).unwrap();
        }
    }

    #[test]
    fn external_environment_block_must_be_readable_and_safe() {
        let root = std::env::temp_dir();
        let supported = BootIntegration::new(
            &root,
            FakeTools {
                env: "btrfs_snapshots_manager_next_entry=safe\n".into(),
                unsafe_path: false,
                calls: Default::default(),
            },
        );
        assert_eq!(
            supported.verify_external_environment_block().unwrap(),
            GRUB_EXTERNAL_ENVIRONMENT
        );
        let unsupported = BootIntegration::new(
            &root,
            FakeTools {
                env: "invalid-line".into(),
                unsafe_path: false,
                calls: Default::default(),
            },
        );
        assert_eq!(
            unsupported
                .verify_external_environment_block()
                .unwrap_err()
                .code,
            BootErrorCode::UnsafeOutput
        );
    }

    #[test]
    fn external_environment_file_has_exact_shape() {
        let root = std::env::temp_dir().join(format!(
            "snapshots-manager-grubenv-shape-{}",
            uuid::Uuid::new_v4()
        ));
        fs::create_dir(&root).unwrap();

        let valid = root.join("valid");
        fs::write(&valid, vec![b'#'; 1024]).unwrap();
        validate_environment_file(&valid).unwrap();

        let short = root.join("short");
        fs::write(&short, vec![b'#'; 1023]).unwrap();
        assert_eq!(
            validate_environment_file(&short).unwrap_err().code,
            BootErrorCode::UnsupportedEnvironment
        );

        let linked = root.join("linked");
        symlink(&valid, &linked).unwrap();
        assert_eq!(
            validate_environment_file(&linked).unwrap_err().code,
            BootErrorCode::UnsupportedEnvironment
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn external_environment_file_must_not_be_writable_by_non_root_users() {
        let root = std::env::temp_dir().join(format!(
            "snapshots-manager-grubenv-permissions-{}",
            uuid::Uuid::new_v4()
        ));
        fs::create_dir(&root).unwrap();
        let environment = root.join("environment");
        fs::write(&environment, vec![b'#'; 1024]).unwrap();
        fs::set_permissions(&environment, fs::Permissions::from_mode(0o666)).unwrap();

        protect_environment_file(&environment).unwrap();
        assert_eq!(
            fs::symlink_metadata(&environment)
                .unwrap()
                .permissions()
                .mode()
                & 0o022,
            0
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn emits_transaction_bound_grub_entry() {
        let environment = Environment::new();
        let integration = BootIntegration::new(
            &environment.root,
            FakeTools {
                env: String::new(),
                unsafe_path: false,
                calls: Default::default(),
            },
        );
        let entry = integration.recovery_menu_entry().unwrap().unwrap();
        assert!(entry.contains(&environment.transaction.grub_entry_id));
        assert!(entry.contains(&format!(
            "anduinos.btrfs_snapshots_manager={}",
            environment.transaction.id
        )));
        assert!(entry.contains("rootflags=subvol=@root"));
        assert!(entry.contains("/vmlinuz-test-kernel"));
        assert!(entry.contains("/initrd.img-test-kernel"));
    }

    #[test]
    fn rejects_multiline_grub_paths() {
        let environment = Environment::new();
        let integration = BootIntegration::new(
            &environment.root,
            FakeTools {
                env: String::new(),
                unsafe_path: true,
                calls: Default::default(),
            },
        );
        assert_eq!(
            integration.recovery_menu_entry().unwrap_err().code,
            BootErrorCode::UnsafeOutput
        );
    }

    #[test]
    fn arms_only_the_transaction_bound_grub_entry() {
        let environment = Environment::new();
        let tools = FakeTools {
            env: String::new(),
            unsafe_path: false,
            calls: Default::default(),
        };
        let integration = BootIntegration::new(&environment.root, tools.clone());
        assert_eq!(
            integration.arm_pending_once().unwrap(),
            environment.transaction.grub_entry_id
        );
        assert!(tools.calls.lock().unwrap().iter().any(|call| {
            call == &format!(
                "{GRUB_EDITENV} {GRUB_EXTERNAL_ENVIRONMENT} set {GRUB_NEXT_ENTRY_VARIABLE}={}",
                environment.transaction.grub_entry_id
            )
        }));
    }

    #[test]
    fn clearing_an_absent_one_shot_selector_is_idempotent() {
        let tools = FakeTools {
            env: String::new(),
            unsafe_path: false,
            calls: Default::default(),
        };
        BootIntegration::new(std::env::temp_dir(), tools.clone())
            .clear_pending_once()
            .unwrap();
        assert_eq!(tools.calls.lock().unwrap().len(), 1);
    }

    #[test]
    fn clearing_a_present_one_shot_selector_calls_grub_editenv() {
        let tools = FakeTools {
            env: format!("{GRUB_NEXT_ENTRY_VARIABLE}=recovery-entry\n"),
            unsafe_path: false,
            calls: Default::default(),
        };
        BootIntegration::new(std::env::temp_dir(), tools.clone())
            .clear_pending_once()
            .unwrap();
        assert!(tools.calls.lock().unwrap().iter().any(|call| {
            call == &format!(
                "{GRUB_EDITENV} {GRUB_EXTERNAL_ENVIRONMENT} unset {GRUB_NEXT_ENTRY_VARIABLE}"
            )
        }));
    }
}
