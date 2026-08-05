//! User-friendly error messages with recovery suggestions
//!
//! This module transforms technical error messages into helpful, actionable
//! messages that guide users toward solutions.

use libadwaita as adw;

use crate::i18n::{tr, trf};

/// Error context for providing better user guidance
#[derive(Debug, Clone, Copy)]
pub enum ErrorContext {
    Create,
    Delete,
    Restore,
}

/// Show an improved error dialog with context and recovery suggestions
pub fn show_error_with_context(
    window: &adw::ApplicationWindow,
    context: ErrorContext,
    error: &str,
) {
    let (title, message, details) = format_error_message(context, error);

    // Build full message with details
    let full_message = if let Some(detail_text) = details {
        format!("{message}\n\n{detail_text}")
    } else {
        message
    };

    // Use common dialog helper
    super::dialogs::show_error(window, &title, &full_message);
}

/// Format error message with helpful context and recovery suggestions
fn format_error_message(context: ErrorContext, error: &str) -> (String, String, Option<String>) {
    match context {
        ErrorContext::Create => format_snapshot_create_error(error),
        ErrorContext::Delete => format_snapshot_delete_error(error),
        ErrorContext::Restore => format_snapshot_restore_error(error),
    }
}

fn format_snapshot_create_error(error: &str) -> (String, String, Option<String>) {
    let title = tr("Failed to Create Recovery Point");

    let (message, recovery) = if error.contains("not enough space")
        || error.contains("No space left")
        || error.contains("bytes available; at least")
    {
        (
            tr("There is not enough disk space to create a recovery point."),
            Some(tr(
                "Delete an unneeded recovery point or free some disk space, then try again.",
            )),
        )
    } else if error.contains("Authorization failed") || error.contains("not authorized") {
        (
            tr("Permission denied."),
            Some(tr(
                "Creating a recovery point requires administrator authorization. Enter the correct password when prompted.",
            )),
        )
    } else if error.contains("not a btrfs")
        || error.contains("wrong fs type")
        || error.contains("AnduinOS Btrfs layout")
    {
        (
            tr("The supported AnduinOS Btrfs layout is unavailable."),
            Some(tr(
                "AnduinOS Waypoint can create recovery points only on the fixed Btrfs layout produced by the AnduinOS installer.",
            )),
        )
    } else if error.contains("already exists") {
        (
            tr("A recovery point with this name already exists."),
            Some(tr("Choose a different recovery point name.")),
        )
    } else {
        (
            tr("An error occurred while creating the recovery point."),
            Some(trf("Technical details: {0}", &[error])),
        )
    };

    (title, message, recovery)
}

fn format_snapshot_delete_error(error: &str) -> (String, String, Option<String>) {
    let title = tr("Failed to Delete Recovery Point");

    let (message, recovery) = if error.contains("Authorization failed") {
        (
            tr("Permission denied."),
            Some(tr(
                "Deleting a recovery point requires administrator authorization.",
            )),
        )
    } else if error.contains("not found") || error.contains("does not exist") {
        (
            tr("Recovery point not found."),
            Some(tr(
                "It may already have been deleted. Refresh the recovery point list.",
            )),
        )
    } else if error.contains("busy") || error.contains("in use") {
        (
            tr("The recovery point is currently in use or protected."),
            Some(tr(
                "Cancel any pending restore that references it, or remove its protection before trying again.",
            )),
        )
    } else {
        (
            tr("An error occurred while deleting the recovery point."),
            Some(trf("Technical details: {0}", &[error])),
        )
    };

    (title, message, recovery)
}

fn format_snapshot_restore_error(error: &str) -> (String, String, Option<String>) {
    let title = tr("Failed to Prepare System Restore");

    let (message, recovery) = if error.contains("Authorization failed") {
        (
            tr("Permission denied."),
            Some(tr(
                "Preparing a system restore requires administrator authorization.",
            )),
        )
    } else if error.contains("not found") {
        (
            tr("Recovery point not found."),
            Some(tr(
                "It may have been deleted. Refresh the recovery point list and choose another one.",
            )),
        )
    } else if error.contains("Secure Boot") || error.contains("MOK") || error.contains("signature")
    {
        (
            tr("Secure Boot verification failed."),
            Some(tr(
                "The target deployment's kernel or required module-signing key could not be verified. No one-shot restore was scheduled.",
            )),
        )
    } else if error.contains("GRUB")
        || error.contains("grub")
        || error.contains("initramfs")
        || error.contains("boot")
    {
        (
            tr("Recovery boot preparation failed."),
            Some(tr(
                "Waypoint could not verify the one-shot GRUB and initramfs recovery path. The current deployment and known-good fallback remain unchanged.",
            )),
        )
    } else {
        (
            tr("An error occurred while preparing the system restore."),
            Some(trf(
                "No restore was scheduled. The current deployment remains selected.\n\nTechnical details: {0}",
                &[error],
            )),
        )
    };

    (title, message, recovery)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_disk_space_error_formatting() {
        let (title, message, details) =
            format_error_message(ErrorContext::Create, "not enough space on device");

        assert_eq!(title, "Failed to Create Recovery Point");
        assert!(message.contains("not enough disk space"));
        assert!(details.is_some());
        assert!(
            details
                .unwrap()
                .contains("Delete an unneeded recovery point")
        );
    }

    #[test]
    fn test_authorization_error_formatting() {
        let (title, message, details) =
            format_error_message(ErrorContext::Create, "Authorization failed: not authorized");

        assert_eq!(title, "Failed to Create Recovery Point");
        assert!(message.contains("Permission denied"));
        assert!(details.unwrap().contains("administrator authorization"));
    }

    #[test]
    fn test_btrfs_error_formatting() {
        let (title, message, details) =
            format_error_message(ErrorContext::Create, "not a btrfs filesystem");

        assert_eq!(title, "Failed to Create Recovery Point");
        assert!(message.contains("Btrfs layout is unavailable"));
        assert!(details.unwrap().contains("fixed Btrfs layout"));
    }

    #[test]
    fn test_snapshot_exists_error() {
        let (title, message, details) =
            format_error_message(ErrorContext::Create, "snapshot already exists");

        assert_eq!(title, "Failed to Create Recovery Point");
        assert!(message.contains("already exists"));
        assert!(details.unwrap().contains("different recovery point name"));
    }
}
