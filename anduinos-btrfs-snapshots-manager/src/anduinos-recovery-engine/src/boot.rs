use std::ffi::OsStr;
use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;

use sha2::{Digest, Sha256};

use crate::RECOVERY_STORE_ROOT;
use crate::model::DeploymentRecord;
#[cfg(test)]
use crate::model::DeploymentState;
use crate::store::DeploymentStore;
use crate::transaction::{RECOVERY_PROTOCOL_VERSION, RollbackPhase, TransactionStore};

const GRUB_PROBE: &str = "/usr/sbin/grub-probe";
const MACHINE_KEY: &str = "/etc/anduinos-luks/machine.key";
const GRUB_MKRELPATH: &str = "/usr/bin/grub-mkrelpath";
const GRUB_EDITENV: &str = "/usr/bin/grub-editenv";
const MOUNTPOINT: &str = "/usr/bin/mountpoint";
const LSINITRD: &str = "/usr/bin/lsinitrd";
const DRACUT: &str = "/usr/bin/dracut";
const KERNEL_RELEASE: &str = "/proc/sys/kernel/osrelease";
const SYSTEM_BOOT_ROOT: &str = "/boot";
const RECOVERY_BOOT_DIRECTORY: &str = "recovery-boot";
const RECOVERY_KERNEL: &str = "vmlinuz";
const RECOVERY_INITRAMFS: &str = "initrd.img";
pub const RECOVERY_CONFIRM: &str = "confirm";
const SYSTEM_CONFIRM_BINARY: &str = "/usr/libexec/anduinos-btrfs-snapshots-manager-confirm";
const INITRAMFS_SCRIPT: &str =
    "var/lib/dracut/hooks/pre-mount/50-anduinos-btrfs-snapshots-manager.sh";
const INITRAMFS_BINARY: &str = "usr/libexec/anduinos-btrfs-snapshots-manager-initramfs";
const INITRAMFS_CONFIRM_BINARY: &str = "usr/libexec/anduinos-btrfs-snapshots-manager-confirm";
const INITRAMFS_PROTOCOL: &str = "etc/anduinos-btrfs-snapshots-manager/recovery-protocol-version";
const INITRAMFS_REQUIRED_TOOLS: [&str; 7] = [
    "usr/bin/cat",
    "usr/bin/chmod",
    "usr/bin/cp",
    "usr/bin/ln",
    "usr/bin/mkdir",
    "usr/bin/mount",
    "usr/bin/btrfs",
];
pub const GRUB_EXTERNAL_ENVIRONMENT: &str =
    "/boot/efi/EFI/anduinos/btrfs-snapshots-manager-grubenv";
pub const GRUB_NEXT_ENTRY_VARIABLE: &str = "btrfs_snapshots_manager_next_entry";
const MAX_TOOL_OUTPUT: usize = 4096;
const MAX_INITRAMFS_LISTING: usize = 16 * 1024 * 1024;

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
pub struct RecoveryBootArtifacts {
    pub kernel_release: String,
    pub kernel_sha256: String,
    pub initramfs_sha256: String,
    pub confirm_sha256: String,
}

struct TargetBootSource {
    kernel_release: String,
    kernel: PathBuf,
    modules: PathBuf,
    firmware: PathBuf,
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
        let maximum_output = if program == Path::new(LSINITRD) {
            MAX_INITRAMFS_LISTING
        } else {
            MAX_TOOL_OUTPUT
        };
        if output.stdout.len() > maximum_output {
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

    pub fn provision_recovery_boot_artifacts(
        &self,
        target: Option<&DeploymentRecord>,
    ) -> Result<RecoveryBootArtifacts, BootError> {
        if let Some(target) = target {
            let source = self.target_boot_source(target)?;
            verify_fallback_modules(&source.kernel_release, Path::new("/lib/modules"))?;
            return self
                .provision_target_recovery_artifacts(target, Path::new(SYSTEM_CONFIRM_BINARY));
        }
        self.provision_recovery_boot_artifacts_from(
            Path::new(KERNEL_RELEASE),
            Path::new(SYSTEM_BOOT_ROOT),
            Path::new(SYSTEM_CONFIRM_BINARY),
        )
    }

    /// System restores need the target kernel and modules. Home-only restores
    /// keep the running root and can reuse its existing recovery initramfs.
    pub fn verify_recovery_boot_source(
        &self,
        target: Option<&DeploymentRecord>,
    ) -> Result<(), BootError> {
        if let Some(target) = target {
            let source = self.target_boot_source(target)?;
            verify_fallback_modules(&source.kernel_release, Path::new("/lib/modules"))?;
            self.verify_fallback_initramfs(&source)?;
            ensure_regular_file(Path::new(DRACUT))?;
            ensure_regular_file(Path::new(SYSTEM_CONFIRM_BINARY))?;
        } else {
            self.verify_recovery_boot_source_from(
                Path::new(KERNEL_RELEASE),
                Path::new(SYSTEM_BOOT_ROOT),
                Path::new(SYSTEM_CONFIRM_BINARY),
            )?;
        }
        ensure_real_directory(&self.snapshot_root, false)?;
        ensure_executable_filesystem(&self.snapshot_root)
    }
}

impl<R: BootToolRunner> BootIntegration<R> {
    fn target_boot_source(&self, target: &DeploymentRecord) -> Result<TargetBootSource, BootError> {
        let release = target.kernel_release.as_deref().ok_or_else(|| {
            BootError::new(
                BootErrorCode::InvalidDeployment,
                "Recovery metadata has no kernel release",
            )
        })?;
        validate_kernel_release(release)?;
        let root = self
            .snapshot_root
            .join("deployments")
            .join(target.id.to_string())
            .join("root");
        ensure_real_directory(&root, false)?;
        let kernel = root.join("boot").join(format!("vmlinuz-{release}"));
        ensure_regular_file(&kernel)?;
        // Resolve usr-merged /lib while keeping the boot inputs inside this
        // verified snapshot, rather than silently mixing its kernel with the
        // running system's copy of the modules or firmware.
        let directory = |relative: &Path| {
            let canonical = fs::canonicalize(root.join(relative))
                .and_then(|path| fs::canonicalize(&root).map(|root| (root, path)))
                .map_err(|error| {
                    BootError::new(
                        BootErrorCode::InvalidDeployment,
                        format!("Could not inspect the snapshot boot files: {error}"),
                    )
                })?;
            if !canonical.1.starts_with(&canonical.0) {
                return Err(BootError::new(
                    BootErrorCode::InvalidDeployment,
                    "The boot files are outside the selected system snapshot",
                ));
            }
            ensure_real_directory(&canonical.1, false)?;
            Ok(canonical.1)
        };
        let modules = directory(&Path::new("lib/modules").join(release))?;
        let firmware = directory(Path::new("lib/firmware"))?;
        ensure_regular_file(&modules.join("modules.dep"))?;
        Ok(TargetBootSource {
            kernel_release: release.to_string(),
            kernel,
            modules,
            firmware,
        })
    }

    fn verify_fallback_initramfs(&self, source: &TargetBootSource) -> Result<(), BootError> {
        // If the first boot fails, GRUB reads the restored root's normal
        // menu. Its original initrd must understand the pending transaction
        // well enough to revert; a newly built first-boot image is not enough.
        let original_initramfs = source
            .kernel
            .with_file_name(format!("initrd.img-{}", source.kernel_release));
        ensure_regular_file(&original_initramfs)?;
        self.verify_initramfs_compatibility(&original_initramfs)?;
        let protocol = self.runner.output(
            Path::new(LSINITRD),
            &[
                OsStr::new("--file"),
                OsStr::new(INITRAMFS_PROTOCOL),
                original_initramfs.as_os_str(),
            ],
        )?;
        if protocol.trim() != RECOVERY_PROTOCOL_VERSION.to_string() {
            return Err(BootError::new(
                BootErrorCode::UnsupportedEnvironment,
                "The snapshot's initramfs cannot safely revert an unconfirmed restore. Use the independent Recovery Environment instead.",
            ));
        }
        Ok(())
    }

    fn provision_target_recovery_artifacts(
        &self,
        target: &DeploymentRecord,
        confirm: &Path,
    ) -> Result<RecoveryBootArtifacts, BootError> {
        let source = self.target_boot_source(target)?;
        ensure_regular_file(confirm)?;
        ensure_real_directory(&self.snapshot_root, false)?;
        let recovery_boot = self.snapshot_root.join(RECOVERY_BOOT_DIRECTORY);
        ensure_real_directory(&recovery_boot, true)?;
        ensure_executable_filesystem(&recovery_boot)?;
        let temporary = recovery_boot.join(format!(".initrd.{}.tmp", uuid::Uuid::new_v4()));
        // A protocol-compatible old initrd may still contain older recovery tools.
        // Build with today's recovery tools and the target's modules instead
        // of booting a new kernel into an old root without matching drivers.
        // The ordinary one-shot transaction and confirmation protocol stay intact.
        let result = (|| {
            self.runner.output(
                Path::new(DRACUT),
                &[
                    OsStr::new("--force"),
                    OsStr::new("--no-hostonly"),
                    OsStr::new("--no-hostonly-cmdline"),
                    OsStr::new("--kmoddir"),
                    source.modules.as_os_str(),
                    OsStr::new("--fwdir"),
                    source.firmware.as_os_str(),
                    temporary.as_os_str(),
                    OsStr::new(&source.kernel_release),
                ],
            )?;
            self.verify_initramfs_compatibility(&temporary)?;
            copy_regular_file_atomic(&source.kernel, &recovery_boot.join(RECOVERY_KERNEL), 0o600)?;
            copy_regular_file_atomic(&temporary, &recovery_boot.join(RECOVERY_INITRAMFS), 0o600)?;
            copy_regular_file_atomic(confirm, &recovery_boot.join(RECOVERY_CONFIRM), 0o700)?;
            ensure_protected_executable(&recovery_boot.join(RECOVERY_CONFIRM))?;
            Ok(RecoveryBootArtifacts {
                kernel_release: source.kernel_release,
                kernel_sha256: hash_regular_file(&recovery_boot.join(RECOVERY_KERNEL))?,
                initramfs_sha256: hash_regular_file(&recovery_boot.join(RECOVERY_INITRAMFS))?,
                confirm_sha256: hash_regular_file(&recovery_boot.join(RECOVERY_CONFIRM))?,
            })
        })();
        let _ = fs::remove_file(temporary);
        result
    }

    fn verify_recovery_boot_source_from(
        &self,
        kernel_release_path: &Path,
        system_boot: &Path,
        confirm_binary: &Path,
    ) -> Result<(String, PathBuf, PathBuf), BootError> {
        let kernel_release = read_kernel_release(kernel_release_path)?;
        let kernel = system_boot.join(format!("vmlinuz-{kernel_release}"));
        let initramfs = system_boot.join(format!("initrd.img-{kernel_release}"));
        ensure_regular_file(&kernel)?;
        ensure_regular_file(&initramfs)?;
        ensure_regular_file(confirm_binary)?;
        self.verify_initramfs_compatibility(&initramfs)?;
        Ok((kernel_release, kernel, initramfs))
    }

    fn provision_recovery_boot_artifacts_from(
        &self,
        kernel_release_path: &Path,
        system_boot: &Path,
        confirm_binary: &Path,
    ) -> Result<RecoveryBootArtifacts, BootError> {
        let (kernel_release, kernel, initramfs) = self.verify_recovery_boot_source_from(
            kernel_release_path,
            system_boot,
            confirm_binary,
        )?;

        ensure_real_directory(&self.snapshot_root, false)?;
        let recovery_boot = self.snapshot_root.join(RECOVERY_BOOT_DIRECTORY);
        ensure_real_directory(&recovery_boot, true)?;
        ensure_executable_filesystem(&recovery_boot)?;
        copy_regular_file_atomic(&kernel, &recovery_boot.join(RECOVERY_KERNEL), 0o600)?;
        copy_regular_file_atomic(&initramfs, &recovery_boot.join(RECOVERY_INITRAMFS), 0o600)?;
        copy_regular_file_atomic(confirm_binary, &recovery_boot.join(RECOVERY_CONFIRM), 0o700)?;
        ensure_protected_executable(&recovery_boot.join(RECOVERY_CONFIRM))?;

        Ok(RecoveryBootArtifacts {
            kernel_release,
            kernel_sha256: hash_regular_file(&recovery_boot.join(RECOVERY_KERNEL))?,
            initramfs_sha256: hash_regular_file(&recovery_boot.join(RECOVERY_INITRAMFS))?,
            confirm_sha256: hash_regular_file(&recovery_boot.join(RECOVERY_CONFIRM))?,
        })
    }

    fn verify_initramfs_compatibility(&self, initramfs: &Path) -> Result<(), BootError> {
        let output = self
            .runner
            .output(Path::new(LSINITRD), &[initramfs.as_os_str()])?;
        for required in [
            INITRAMFS_SCRIPT,
            INITRAMFS_BINARY,
            INITRAMFS_CONFIRM_BINARY,
            INITRAMFS_PROTOCOL,
        ]
        .into_iter()
        .chain(INITRAMFS_REQUIRED_TOOLS)
        {
            if !initramfs_listing_contains(&output, required) {
                return Err(BootError::new(
                    BootErrorCode::UnsupportedEnvironment,
                    format!(
                        "The current initramfs does not contain recovery protocol {RECOVERY_PROTOCOL_VERSION}: missing {required}"
                    ),
                ));
            }
        }
        // A sandbox can let Dracut finish while rejecting preservation of a
        // setuid program's mode. Catch that concrete failure before arming it.
        // Symlink members are resolved by Dracut; the confirmation executable
        // intentionally remains non-executable until staged by our boot hook.
        for required in INITRAMFS_REQUIRED_TOOLS {
            if output.lines().any(|line| {
                let fields = line.split_whitespace().collect::<Vec<_>>();
                fields.last().copied() == Some(required)
                    && fields.first().is_some_and(|mode| {
                        mode.starts_with('-')
                            && !matches!(mode.as_bytes().get(3), Some(b'x' | b's'))
                    })
            }) {
                return Err(BootError::new(
                    BootErrorCode::UnsupportedEnvironment,
                    format!("The recovery initramfs contains a non-executable program: {required}"),
                ));
            }
        }
        Ok(())
    }

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
        self.recovery_menu_entry_with_key(None)
    }

    /// The rollback entry bypasses Ubuntu's 10_linux generator, so it must
    /// explicitly use the same encrypted-root RAM handoff as normal boots.
    /// Only the installer's exported opt-in selects this path. The credential
    /// stays inside the encrypted root and is read by GRUB into RAM at boot;
    /// neither the ESP nor the stored recovery initramfs receives its bytes.
    pub fn encrypted_recovery_menu_entry(&self) -> Result<Option<String>, BootError> {
        let abstraction = self.runner.output(
            Path::new(GRUB_PROBE),
            &[OsStr::new("--target=abstraction"), OsStr::new("/")],
        )?;
        if !abstraction
            .split_whitespace()
            .any(|value| value == "cryptodisk")
        {
            return Err(BootError::new(
                BootErrorCode::UnsupportedEnvironment,
                "Encrypted recovery handoff requires an encrypted root",
            ));
        }
        let key = Path::new(MACHINE_KEY);
        let metadata = fs::symlink_metadata(key).map_err(|error| {
            BootError::new(
                BootErrorCode::UnsupportedEnvironment,
                format!("Could not inspect the internal boot credential: {error}"),
            )
        })?;
        validate_machine_credential(
            metadata.is_file(),
            metadata.uid(),
            metadata.mode(),
            metadata.len(),
        )?;
        let key_path = self.grub_path(key)?;
        if key_path != "/@root/etc/anduinos-luks/machine.key" {
            return Err(BootError::new(
                BootErrorCode::UnsupportedEnvironment,
                "Encrypted recovery handoff requires the installed @root credential",
            ));
        }
        self.recovery_menu_entry_with_key(Some(&key_path))
    }

    fn recovery_menu_entry_with_key(
        &self,
        machine_key: Option<&str>,
    ) -> Result<Option<String>, BootError> {
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
        if transaction.recovery_protocol_version != RECOVERY_PROTOCOL_VERSION {
            return Err(BootError::new(
                BootErrorCode::InvalidTransaction,
                "A legacy recovery transaction cannot be armed with the current boot protocol",
            ));
        }
        let target = DeploymentStore::new(&self.snapshot_root)
            .load_record(transaction.target_deployment_id)
            .map_err(|error| BootError::new(BootErrorCode::InvalidDeployment, error.message))?;
        if !target.can_restore() {
            return Err(BootError::new(
                BootErrorCode::InvalidDeployment,
                "GRUB recovery target is not a complete, healthy snapshot",
            ));
        }
        let recovery_boot = self.snapshot_root.join(RECOVERY_BOOT_DIRECTORY);
        let kernel = recovery_boot.join(RECOVERY_KERNEL);
        let initramfs = recovery_boot.join(RECOVERY_INITRAMFS);
        let confirm = recovery_boot.join(RECOVERY_CONFIRM);
        ensure_regular_file(&kernel)?;
        ensure_regular_file(&initramfs)?;
        ensure_protected_executable(&confirm)?;
        ensure_executable_filesystem(&recovery_boot)?;
        verify_digest(
            &kernel,
            &transaction.recovery_kernel_sha256,
            "recovery kernel",
        )?;
        verify_digest(
            &initramfs,
            &transaction.recovery_initramfs_sha256,
            "recovery initramfs",
        )?;
        verify_digest(
            &confirm,
            &transaction.recovery_confirm_sha256,
            "recovery confirmation engine",
        )?;
        let kernel_path = self.grub_path(&kernel)?;
        let initramfs_path = self.grub_path(&initramfs)?;
        let id = transaction.id.to_string();
        let entry_id = &transaction.grub_entry_id;
        let fs_uuid = &transaction.root_filesystem_uuid;
        let handoff = machine_key.map_or_else(String::new, |path| {
            format!(" newc:/anduinos-unlock.key:{path}")
        });
        Ok(Some(format!(
            "menuentry 'Disk Snapshots Manager recovery' --class anduinos --class gnu-linux --id '{entry_id}' {{\n\
             \tinsmod btrfs\n\
             \tsearch --no-floppy --fs-uuid --set=root {fs_uuid}\n\
             \techo 'Starting AnduinOS system recovery…'\n\
             \tset btrfs_snapshots_manager_next_entry='{entry_id}'\n\
             \tsave_env -f \"($btrfs_snapshots_manager_esp)/EFI/anduinos/btrfs-snapshots-manager-grubenv\" btrfs_snapshots_manager_next_entry\n\
             \tlinux {kernel_path} root=UUID={fs_uuid} ro rootflags=subvol=@root anduinos.btrfs_snapshots_manager={id} anduinos.btrfs_snapshots_manager_protocol={protocol}\n\
             \tinitrd {initramfs_path}{handoff}\n\
             }}\n",
            protocol = transaction.recovery_protocol_version
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
        if transaction.recovery_protocol_version != RECOVERY_PROTOCOL_VERSION {
            return Err(BootError::new(
                BootErrorCode::InvalidTransaction,
                "A legacy recovery transaction cannot be selected for boot",
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

fn validate_machine_credential(
    regular: bool,
    owner: u32,
    mode: u32,
    size: u64,
) -> Result<(), BootError> {
    if !regular || owner != 0 || mode & 0o7777 != 0o600 || size != 64 {
        return Err(BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            "The internal boot credential must be a root-owned mode-0600 regular 64-byte file",
        ));
    }
    Ok(())
}

fn initramfs_listing_contains(output: &str, required: &str) -> bool {
    output.lines().any(|line| {
        let line = line.trim();
        if line == required {
            return true;
        }

        let fields = line.split_whitespace().collect::<Vec<_>>();
        let Some(mode) = fields.first() else {
            return false;
        };
        if !matches!(
            mode.as_bytes().first().copied(),
            Some(b'-' | b'b' | b'c' | b'd' | b'l' | b'p' | b's')
        ) {
            return false;
        }

        let member = if fields.len() >= 3 && fields[fields.len() - 2] == "->" {
            fields.get(fields.len() - 3).copied()
        } else {
            fields.last().copied()
        };
        member == Some(required)
    })
}

/// A failed target boots its original initramfs before reverting to the protected
/// current root. That root must also support the selected kernel. Do not arm a
/// restore which can succeed forward but strand its automatic fallback without
/// drivers; the independent recovery environment can restore such older roots.
fn verify_fallback_modules(release: &str, modules_root: &Path) -> Result<(), BootError> {
    let modules = modules_root.join(release);
    if ensure_real_directory(&modules, false).is_err()
        || ensure_regular_file(&modules.join("modules.dep")).is_err()
    {
        return Err(BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            "The current system no longer has the target kernel's modules. Restore this snapshot from the independent Recovery Environment instead.",
        ));
    }
    Ok(())
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

fn read_kernel_release(path: &Path) -> Result<String, BootError> {
    let value = fs::read_to_string(path).map_err(|error| {
        BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            format!("Could not read the running kernel release: {error}"),
        )
    })?;
    validate_kernel_release(value.trim()).map(str::to_string)
}

fn validate_kernel_release(value: &str) -> Result<&str, BootError> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._+-".contains(&byte))
    {
        return Err(BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            "The recovery kernel release is unsafe",
        ));
    }
    Ok(value)
}

pub(crate) fn copy_regular_file_atomic(
    source: &Path,
    target: &Path,
    mode: u32,
) -> Result<(), BootError> {
    ensure_regular_file(source)?;
    let parent = target.parent().ok_or_else(|| {
        BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            "Recovery artifact has no parent directory",
        )
    })?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        target.file_name().unwrap_or_default().to_string_lossy(),
        uuid::Uuid::new_v4().hyphenated()
    ));
    let result = (|| -> std::io::Result<()> {
        let mut input = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(source)?;
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(mode)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)?;
        std::io::copy(&mut input, &mut output)?;
        output.flush()?;
        output.sync_all()?;
        fs::rename(&temporary, target)?;
        OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_DIRECTORY | libc::O_NOFOLLOW)
            .open(parent)?
            .sync_all()
    })();
    if let Err(error) = result {
        let _ = fs::remove_file(&temporary);
        return Err(BootError::new(
            BootErrorCode::CommandFailed,
            format!("Could not provision {}: {error}", target.display()),
        ));
    }
    Ok(())
}

pub(crate) fn ensure_protected_executable(path: &Path) -> Result<(), BootError> {
    ensure_regular_file(path)?;
    let mode = fs::symlink_metadata(path)
        .map_err(|error| {
            BootError::new(
                BootErrorCode::InvalidDeployment,
                format!("Could not inspect {}: {error}", path.display()),
            )
        })?
        .permissions()
        .mode();
    if mode & 0o111 == 0 || mode & 0o022 != 0 {
        return Err(BootError::new(
            BootErrorCode::InvalidDeployment,
            format!(
                "Recovery confirmation artifact {} is not a protected executable",
                path.display()
            ),
        ));
    }
    Ok(())
}

fn ensure_executable_filesystem(path: &Path) -> Result<(), BootError> {
    let path = std::ffi::CString::new(path.as_os_str().as_bytes()).map_err(|_| {
        BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            "Recovery artifact path contains an embedded NUL byte",
        )
    })?;
    let mut values = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    if unsafe { libc::statvfs(path.as_ptr(), values.as_mut_ptr()) } != 0 {
        return Err(BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            format!(
                "Could not inspect recovery artifact mount flags: {}",
                std::io::Error::last_os_error()
            ),
        ));
    }
    let values = unsafe { values.assume_init() };
    validate_executable_mount_flags(values.f_flag)
}

fn validate_executable_mount_flags(flags: libc::c_ulong) -> Result<(), BootError> {
    if flags & libc::ST_NOEXEC != 0 {
        return Err(BootError::new(
            BootErrorCode::UnsupportedEnvironment,
            "The snapshot store is mounted noexec and cannot host the trusted recovery confirmation engine",
        ));
    }
    Ok(())
}

pub(crate) fn hash_regular_file(path: &Path) -> Result<String, BootError> {
    ensure_regular_file(path)?;
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)
        .map_err(|error| {
            BootError::new(
                BootErrorCode::InvalidDeployment,
                format!("Could not open {}: {error}", path.display()),
            )
        })?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|error| {
            BootError::new(
                BootErrorCode::InvalidDeployment,
                format!("Could not hash {}: {error}", path.display()),
            )
        })?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn verify_digest(path: &Path, expected: &str, name: &str) -> Result<(), BootError> {
    if hash_regular_file(path)? == expected {
        Ok(())
    } else {
        Err(BootError::new(
            BootErrorCode::InvalidDeployment,
            format!("The provisioned {name} no longer matches the rollback transaction"),
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
            if program == Path::new(LSINITRD) {
                return Ok(format!(
                    "{INITRAMFS_SCRIPT}\n{INITRAMFS_BINARY}\n{INITRAMFS_CONFIRM_BINARY}\n{INITRAMFS_PROTOCOL}\n{}\n",
                    INITRAMFS_REQUIRED_TOOLS.join("\n")
                ));
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
                state: DeploymentState::Ready,
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
            let recovery_boot = root.join(RECOVERY_BOOT_DIRECTORY);
            fs::create_dir_all(&recovery_boot).unwrap();
            let kernel = recovery_boot.join(RECOVERY_KERNEL);
            let initramfs = recovery_boot.join(RECOVERY_INITRAMFS);
            let confirm = recovery_boot.join(RECOVERY_CONFIRM);
            fs::write(&kernel, "kernel").unwrap();
            fs::write(&initramfs, "initramfs").unwrap();
            fs::write(&confirm, "confirm").unwrap();
            fs::set_permissions(&confirm, fs::Permissions::from_mode(0o700)).unwrap();
            let mut transaction = RollbackTransaction::new(
                target.id,
                DeploymentId::new(),
                "dddddddd-1111-4222-8333-eeeeeeeeeeee",
                "test-kernel",
                hash_regular_file(&kernel).unwrap(),
                hash_regular_file(&initramfs).unwrap(),
                hash_regular_file(&confirm).unwrap(),
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
    fn noexec_snapshot_store_is_rejected_before_a_restore_is_armed() {
        assert!(validate_executable_mount_flags(0).is_ok());
        assert_eq!(
            validate_executable_mount_flags(libc::ST_NOEXEC)
                .unwrap_err()
                .code,
            BootErrorCode::UnsupportedEnvironment
        );
    }

    #[test]
    fn lsinitrd_member_parser_preserves_symlink_names() {
        let output = concat!(
            "-rwxr-xr-x 1 root root 4854 Jan 1 00:00 ",
            "var/lib/dracut/hooks/pre-mount/50-anduinos-btrfs-snapshots-manager.sh\n",
            "lrwxrwxrwx 1 root root 30 Jan 1 00:00 ",
            "usr/bin/cat -> ../lib/cargo/bin/coreutils/cat\n",
        );
        assert!(initramfs_listing_contains(output, INITRAMFS_SCRIPT));
        assert!(initramfs_listing_contains(output, "usr/bin/cat"));
        assert!(!initramfs_listing_contains(
            output,
            "../lib/cargo/bin/coreutils/cat"
        ));
    }

    #[test]
    fn system_recovery_uses_target_modules_and_current_recovery_programs() {
        #[derive(Clone)]
        struct BuildingTools {
            fail: bool,
            nonexecutable_mount: bool,
            protocol: &'static str,
            calls: std::sync::Arc<std::sync::Mutex<Vec<Vec<std::ffi::OsString>>>>,
        }

        impl BootToolRunner for BuildingTools {
            fn output(&self, program: &Path, arguments: &[&OsStr]) -> Result<String, BootError> {
                if program == Path::new(LSINITRD)
                    && arguments.first().copied() == Some(OsStr::new("--file"))
                {
                    return Ok(self.protocol.into());
                }
                if program == Path::new(DRACUT) {
                    self.calls
                        .lock()
                        .unwrap()
                        .push(arguments.iter().map(|a| a.to_os_string()).collect());
                    fs::write(
                        Path::new(arguments[arguments.len() - 2]),
                        "new-recovery-image",
                    )
                    .unwrap();
                    if self.fail {
                        return Err(BootError::new(
                            BootErrorCode::CommandFailed,
                            "initrd build failed",
                        ));
                    }
                    return Ok(String::new());
                }
                let output = FakeTools {
                    env: String::new(),
                    unsafe_path: false,
                    calls: Default::default(),
                }
                .output(program, arguments)?;
                if program == Path::new(LSINITRD) && self.nonexecutable_mount {
                    return Ok(output.replace(
                        "usr/bin/mount\n",
                        "-rw------- 1 root root 67864 Jan 1 00:00 usr/bin/mount\n",
                    ));
                }
                Ok(output)
            }
        }

        for (fail, nonexecutable_mount) in [(false, false), (true, false), (false, true)] {
            let environment = Environment::new();
            let target = DeploymentStore::new(&environment.root)
                .load_record(environment.transaction.target_deployment_id)
                .unwrap();
            let store = environment.root.join("artifacts");
            let root = store
                .join("deployments")
                .join(target.id.to_string())
                .join("root");
            let release = target.kernel_release.as_deref().unwrap();
            fs::create_dir_all(root.join("boot")).unwrap();
            let modules = root.join("usr/lib/modules").join(release);
            fs::create_dir_all(&modules).unwrap();
            fs::create_dir_all(root.join("usr/lib/firmware")).unwrap();
            symlink("usr/lib", root.join("lib")).unwrap();
            fs::write(modules.join("modules.dep"), "").unwrap();
            fs::write(
                root.join("boot").join(format!("vmlinuz-{release}")),
                "target-kernel",
            )
            .unwrap();
            // The target's old initrd is deliberately absent. Building the
            // recovery image must use today's tools and the snapshot's modules.
            let current_modules = environment.root.join("current-modules");
            assert!(verify_fallback_modules(release, &current_modules).is_err());
            fs::create_dir_all(current_modules.join(release)).unwrap();
            fs::write(current_modules.join(release).join("modules.dep"), "").unwrap();
            verify_fallback_modules(release, &current_modules).unwrap();
            let confirm = environment.root.join("current-confirm");
            fs::write(&confirm, "current-confirm").unwrap();
            fs::set_permissions(&confirm, fs::Permissions::from_mode(0o755)).unwrap();
            let recovery = store.join(RECOVERY_BOOT_DIRECTORY);
            fs::create_dir(&recovery).unwrap();
            for name in [RECOVERY_KERNEL, RECOVERY_INITRAMFS, RECOVERY_CONFIRM] {
                fs::write(recovery.join(name), "previous-artifact").unwrap();
            }
            let tools = BuildingTools {
                fail,
                nonexecutable_mount,
                protocol: "2",
                calls: Default::default(),
            };
            let calls = tools.calls.clone();
            let integration = BootIntegration::new(&store, tools);
            let result = integration.provision_target_recovery_artifacts(&target, &confirm);
            if fail || nonexecutable_mount {
                assert!(result.is_err());
                for name in [RECOVERY_KERNEL, RECOVERY_INITRAMFS, RECOVERY_CONFIRM] {
                    assert_eq!(
                        fs::read_to_string(recovery.join(name)).unwrap(),
                        "previous-artifact"
                    );
                }
            } else {
                assert_eq!(result.unwrap().kernel_release, release);
                assert_eq!(
                    fs::read_to_string(recovery.join(RECOVERY_KERNEL)).unwrap(),
                    "target-kernel"
                );
                assert_eq!(
                    fs::read_to_string(recovery.join(RECOVERY_INITRAMFS)).unwrap(),
                    "new-recovery-image"
                );
                assert_eq!(
                    fs::read_to_string(recovery.join(RECOVERY_CONFIRM)).unwrap(),
                    "current-confirm"
                );
            }
            let calls = calls.lock().unwrap();
            assert_eq!(calls.len(), 1);
            assert_eq!(calls[0][4], modules.as_os_str());
            assert_eq!(calls[0][6], root.join("usr/lib/firmware").as_os_str());
            assert_eq!(calls[0].last().unwrap(), release);
            assert_eq!(fs::read_dir(&recovery).unwrap().count(), 3);
            let original_initramfs = root.join("boot").join(format!("initrd.img-{release}"));
            let source = integration.target_boot_source(&target).unwrap();
            assert!(integration.verify_fallback_initramfs(&source).is_err());
            fs::write(&original_initramfs, "original-image").unwrap();
            if !nonexecutable_mount {
                integration.verify_fallback_initramfs(&source).unwrap();
                let legacy = BootIntegration::new(
                    &store,
                    BuildingTools {
                        fail: false,
                        nonexecutable_mount: false,
                        protocol: "1",
                        calls: Default::default(),
                    },
                );
                assert!(legacy.verify_fallback_initramfs(&source).is_err());
            }
            fs::remove_file(modules.join("modules.dep")).unwrap();
            assert!(integration.target_boot_source(&target).is_err());
        }
    }

    #[test]
    fn provisions_snapshot_external_versioned_recovery_artifacts() {
        let root = std::env::temp_dir().join(format!(
            "snapshots-manager-recovery-boot-{}",
            uuid::Uuid::new_v4()
        ));
        let store = root.join("store");
        let boot = root.join("boot");
        fs::create_dir_all(&store).unwrap();
        fs::create_dir_all(&boot).unwrap();
        let release = root.join("osrelease");
        fs::write(&release, "7.0.0-test\n").unwrap();
        fs::write(boot.join("vmlinuz-7.0.0-test"), "trusted-kernel").unwrap();
        fs::write(boot.join("initrd.img-7.0.0-test"), "trusted-initramfs").unwrap();
        let confirm = root.join("confirm");
        fs::write(&confirm, "trusted-confirm").unwrap();
        fs::set_permissions(&confirm, fs::Permissions::from_mode(0o755)).unwrap();
        let integration = BootIntegration::new(
            &store,
            FakeTools {
                env: String::new(),
                unsafe_path: false,
                calls: Default::default(),
            },
        );
        let artifacts = integration
            .provision_recovery_boot_artifacts_from(&release, &boot, &confirm)
            .unwrap();
        assert_eq!(artifacts.kernel_release, "7.0.0-test");
        assert_eq!(
            fs::read_to_string(store.join("recovery-boot/vmlinuz")).unwrap(),
            "trusted-kernel"
        );
        assert_eq!(
            fs::read_to_string(store.join("recovery-boot/initrd.img")).unwrap(),
            "trusted-initramfs"
        );
        assert_eq!(
            fs::read_to_string(store.join("recovery-boot/confirm")).unwrap(),
            "trusted-confirm"
        );
        assert_eq!(artifacts.kernel_sha256.len(), 64);
        assert_eq!(artifacts.initramfs_sha256.len(), 64);
        assert_eq!(artifacts.confirm_sha256.len(), 64);
        fs::remove_dir_all(root).unwrap();
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
        assert!(entry.contains("/vmlinuz"));
        assert!(entry.contains("/initrd.img"));
        assert!(entry.contains("anduinos.btrfs_snapshots_manager_protocol=2"));
        assert!(entry.contains("set btrfs_snapshots_manager_next_entry="));
        assert!(entry.contains("save_env -f \"($btrfs_snapshots_manager_esp)"));
    }

    #[test]
    fn encrypted_rollback_appends_the_ram_credential_after_the_verified_initramfs() {
        let environment = Environment::new();
        let integration = BootIntegration::new(
            &environment.root,
            FakeTools {
                env: String::new(),
                unsafe_path: false,
                calls: Default::default(),
            },
        );
        let plain = integration.recovery_menu_entry().unwrap().unwrap();
        assert!(!plain.contains("newc:"));
        let encrypted = integration
            .recovery_menu_entry_with_key(Some("/@root/etc/anduinos-luks/machine.key"))
            .unwrap()
            .unwrap();
        assert!(encrypted.contains(
            "initrd /initrd.img newc:/anduinos-unlock.key:/@root/etc/anduinos-luks/machine.key\n"
        ));
        assert_eq!(encrypted.matches("newc:").count(), 1);
        assert!(encrypted.contains(&environment.transaction.grub_entry_id));
    }

    #[test]
    fn internal_boot_credential_requires_the_installer_file_shape() {
        assert!(validate_machine_credential(true, 0, 0o100600, 64).is_ok());
        for (regular, owner, mode, size) in [
            (false, 0, 0o120600, 64),
            (true, 1000, 0o100600, 64),
            (true, 0, 0o100644, 64),
            (true, 0, 0o100600, 63),
            (true, 0, 0o100600, 65),
        ] {
            assert!(validate_machine_credential(regular, owner, mode, size).is_err());
        }
    }

    #[test]
    fn refuses_recovery_artifacts_changed_after_the_transaction_was_armed() {
        let environment = Environment::new();
        fs::write(
            environment.root.join("recovery-boot/initrd.img"),
            "tampered-initramfs",
        )
        .unwrap();
        let integration = BootIntegration::new(
            &environment.root,
            FakeTools {
                env: String::new(),
                unsafe_path: false,
                calls: Default::default(),
            },
        );
        assert_eq!(
            integration.recovery_menu_entry().unwrap_err().code,
            BootErrorCode::InvalidDeployment
        );
    }

    #[test]
    fn refuses_confirmation_artifact_changed_after_the_transaction_was_armed() {
        let environment = Environment::new();
        fs::write(
            environment.root.join("recovery-boot/confirm"),
            "tampered-confirmation-engine",
        )
        .unwrap();
        fs::set_permissions(
            environment.root.join("recovery-boot/confirm"),
            fs::Permissions::from_mode(0o700),
        )
        .unwrap();
        let integration = BootIntegration::new(
            &environment.root,
            FakeTools {
                env: String::new(),
                unsafe_path: false,
                calls: Default::default(),
            },
        );
        assert_eq!(
            integration.recovery_menu_entry().unwrap_err().code,
            BootErrorCode::InvalidDeployment
        );
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
