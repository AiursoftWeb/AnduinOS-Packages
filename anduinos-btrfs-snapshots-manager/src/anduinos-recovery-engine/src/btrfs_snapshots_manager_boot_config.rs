use std::process::ExitCode;

use anduinos_recovery_engine::boot::BootIntegration;

fn main() -> ExitCode {
    let integration = BootIntegration::default();
    // grub-mkconfig inherits this explicit opt-in from the installer's defaults.
    // Existing unencrypted systems keep their original entry byte for byte.
    let entry = if std::env::var_os("GRUB_ANDUINOS_CRYPT_HANDOFF").as_deref()
        == Some(std::ffi::OsStr::new("y"))
    {
        integration.encrypted_recovery_menu_entry()
    } else {
        integration.recovery_menu_entry()
    };
    match entry {
        Ok(Some(entry)) => {
            print!("{entry}");
            ExitCode::SUCCESS
        }
        Ok(None) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!(
                "Disk Snapshots Manager could not generate its recovery boot entry: {}",
                error.message
            );
            ExitCode::FAILURE
        }
    }
}
