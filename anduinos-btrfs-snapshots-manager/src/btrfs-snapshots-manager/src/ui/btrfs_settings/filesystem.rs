use std::cell::RefCell;
use std::rc::Rc;

use adw::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use super::shared::{confirmation, show_result};
use crate::dbus_client::{BtrfsFilesystemStatus, SnapshotsManagerHelperClient};
use crate::i18n::{tr, trf};

#[derive(Clone)]
struct FilesystemRows {
    source: adw::ActionRow,
    capacity: adw::ActionRow,
    data: adw::ActionRow,
    metadata: adw::ActionRow,
    compression: adw::ActionRow,
    discard: adw::ActionRow,
    quota: adw::ActionRow,
    quota_button: gtk::Button,
}

pub fn filesystem_page(parent: &adw::PreferencesWindow) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::new();
    page.set_title(&tr("File System"));
    page.set_icon_name(Some("drive-harddisk-symbolic"));

    let (overview, source, capacity, data, metadata) = overview_group();
    page.add(&overview);
    let (behavior, compression, discard) = behavior_group();
    page.add(&behavior);
    let (accounting, quota, quota_button, learn) = accounting_group();
    page.add(&accounting);

    let rows = FilesystemRows {
        source,
        capacity,
        data,
        metadata,
        compression,
        discard,
        quota,
        quota_button,
    };
    let quota_state = Rc::new(RefCell::new(String::new()));
    refresh_filesystem(parent, &rows, &quota_state);
    connect_quota_control(parent, &rows, &quota_state);
    connect_dedup_explanation(parent, &learn);
    page
}

fn overview_group() -> (
    adw::PreferencesGroup,
    adw::ActionRow,
    adw::ActionRow,
    adw::ActionRow,
    adw::ActionRow,
) {
    let group = adw::PreferencesGroup::new();
    group.set_title(&tr("At a Glance"));
    group.set_description(Some(&tr(
        "Live information reported by the mounted Btrfs file system.",
    )));
    let source = value_row(&tr("System storage"), "content-loading-symbolic");
    let capacity = value_row(&tr("Space usage"), "drive-harddisk-symbolic");
    let data = value_row(&tr("Actual file contents"), "text-x-generic-symbolic");
    let metadata = value_row(
        &tr("File system structure (directories, file names, and more)"),
        "view-list-symbolic",
    );
    for row in [&source, &capacity, &data, &metadata] {
        group.add(row);
    }
    (group, source, capacity, data, metadata)
}

fn behavior_group() -> (adw::PreferencesGroup, adw::ActionRow, adw::ActionRow) {
    let group = adw::PreferencesGroup::new();
    group.set_title(&tr("Storage Behavior"));
    let compression = value_row(&tr("Transparent compression"), "package-x-generic-symbolic");
    compression.set_subtitle(&tr("Applied automatically to newly written data"));
    let discard = value_row(&tr("SSD space reclamation"), "edit-clear-all-symbolic");
    group.add(&compression);
    group.add(&discard);
    (group, compression, discard)
}

fn accounting_group() -> (
    adw::PreferencesGroup,
    adw::ActionRow,
    gtk::Button,
    gtk::Button,
) {
    let group = adw::PreferencesGroup::new();
    group.set_title(&tr("Space Accounting"));
    group.set_description(Some(&tr(
        "Quota accounting provides shared and exclusive sizes for subvolumes, but its initial scan can take time.",
    )));

    let quota = adw::ActionRow::new();
    quota.set_title(&tr("Subvolume quota accounting"));
    quota.set_subtitle(&tr("Checking…"));
    quota.add_prefix(&gtk::Image::from_icon_name("folder-symbolic"));
    let quota_button = gtk::Button::with_label(&tr("Change…"));
    quota_button.set_valign(gtk::Align::Center);
    quota_button.set_sensitive(false);
    quota.add_suffix(&quota_button);
    group.add(&quota);

    let dedup = adw::ActionRow::new();
    dedup.set_title(&tr("Content-based deduplication"));
    dedup.set_subtitle(&tr(
        "Not managed here · Btrfs requires a separate deduplication engine",
    ));
    dedup.add_prefix(&gtk::Image::from_icon_name("edit-copy-symbolic"));
    let learn = gtk::Button::with_label(&tr("Why?"));
    learn.set_valign(gtk::Align::Center);
    dedup.add_suffix(&learn);
    group.add(&dedup);
    (group, quota, quota_button, learn)
}

fn value_row(title: &str, icon: &str) -> adw::ActionRow {
    let row = adw::ActionRow::new();
    row.set_title(title);
    row.set_subtitle(&tr("Checking…"));
    row.add_prefix(&gtk::Image::from_icon_name(icon));
    row
}

fn connect_quota_control(
    parent: &adw::PreferencesWindow,
    rows: &FilesystemRows,
    quota_state: &Rc<RefCell<String>>,
) {
    let parent = parent.clone();
    let rows = rows.clone();
    let quota_state = quota_state.clone();
    rows.quota_button.clone().connect_clicked(move |_| {
        let enabled = matches!(quota_state.borrow().as_str(), "enabled" | "scanning");
        let (heading, body, action, label) = if enabled {
            (
                tr("Disable quota accounting?"),
                tr("Shared and exclusive size statistics and any subvolume limits will be removed. Snapshots themselves are not deleted."),
                "quota-disable",
                tr("Disable"),
            )
        } else {
            (
                tr("Enable quota accounting?"),
                tr("Btrfs will scan existing subvolumes in the background. Size statistics may remain incomplete until the scan finishes."),
                "quota-enable",
                tr("Enable"),
            )
        };
        let dialog = confirmation(&parent, &heading, &body, &label, false);
        let parent = parent.clone();
        let rows = rows.clone();
        let quota_state = quota_state.clone();
        dialog.connect_response(None, move |dialog, response| {
            dialog.close();
            if response != "run" {
                return;
            }
            let parent = parent.clone();
            let rows = rows.clone();
            let quota_state = quota_state.clone();
            glib::spawn_future_local(async move {
                let result = gio::spawn_blocking(move || {
                    SnapshotsManagerHelperClient::new()?
                        .run_btrfs_maintenance_action(action)
                })
                .await
                .map_err(|_| anyhow::anyhow!("The quota operation stopped unexpectedly"))
                .and_then(|result| result);
                show_result(&parent, result);
                refresh_filesystem(&parent, &rows, &quota_state);
            });
        });
        dialog.present();
    });
}

fn connect_dedup_explanation(parent: &adw::PreferencesWindow, learn: &gtk::Button) {
    let parent = parent.clone();
    learn.connect_clicked(move |_| {
        let dialog = adw::MessageDialog::new(
            Some(&parent),
            Some(&tr("Deduplication needs an engine")),
            Some(&tr(
                "Btrfs does not provide an on/off real-time deduplication switch. Tools such as duperemove and BEES use different strategies, resource limits, and scan scopes. Disk Snapshots Manager will not silently install or run one without a complete policy.",
            )),
        );
        dialog.add_response("close", &tr("Close"));
        dialog.present();
    });
}

fn refresh_filesystem(
    parent: &adw::PreferencesWindow,
    rows: &FilesystemRows,
    quota_state: &Rc<RefCell<String>>,
) {
    let weak_parent = parent.downgrade();
    let rows = rows.clone();
    let quota_state = quota_state.clone();
    glib::spawn_future_local(async move {
        let result = gio::spawn_blocking(|| {
            SnapshotsManagerHelperClient::new()?.get_btrfs_filesystem_status()
        })
        .await
        .map_err(|_| anyhow::anyhow!("The Btrfs status query stopped unexpectedly"))
        .and_then(|result| result);
        if weak_parent.upgrade().is_none() {
            return;
        }
        match result {
            Ok(status) if status.available => apply_filesystem_status(&rows, &quota_state, &status),
            Ok(status) => rows
                .source
                .set_subtitle(status.error.as_deref().unwrap_or(&tr("Unavailable"))),
            Err(error) => rows.source.set_subtitle(&error.to_string()),
        }
    });
}

fn apply_filesystem_status(
    rows: &FilesystemRows,
    quota_state: &Rc<RefCell<String>>,
    status: &BtrfsFilesystemStatus,
) {
    rows.source.set_subtitle(&status.source);
    rows.capacity
        .set_subtitle(&match (status.used_bytes, status.total_bytes) {
            (Some(used), Some(total)) => trf(
                "{0} of {1} used",
                &[&format_bytes(used), &format_bytes(total)],
            ),
            _ => tr("Unavailable"),
        });
    rows.data
        .set_subtitle(&storage_profile_description(&status.data_profile));
    rows.metadata
        .set_subtitle(&storage_profile_description(&status.metadata_profile));
    rows.compression
        .set_subtitle(&trf("{0} · new writes", &[&status.compression]));
    rows.discard.set_subtitle(&status.discard);
    *quota_state.borrow_mut() = status.quota.clone();
    let (subtitle, label) = match status.quota.as_str() {
        "enabled" => (tr("Enabled"), tr("Disable…")),
        "scanning" => (tr("Enabled · initial scan in progress"), tr("Disable…")),
        "disabled" => (tr("Disabled"), tr("Enable…")),
        _ => (tr("Unavailable"), tr("Change…")),
    };
    rows.quota.set_subtitle(&subtitle);
    rows.quota_button.set_label(&label);
    rows.quota_button
        .set_sensitive(status.quota != "unavailable");
}

fn storage_profile_description(profile: &str) -> String {
    match profile.to_ascii_uppercase().as_str() {
        "SINGLE" => tr("One copy · damage can be detected, but there is no spare copy for repair"),
        "DUP" => tr("Two copies on this device · a damaged copy can be repaired automatically"),
        "RAID0" => tr("Striped across devices · no redundant copy is available for repair"),
        "RAID1" => {
            tr("Two copies on separate devices · a damaged copy can be repaired automatically")
        }
        "RAID1C3" => {
            tr("Three copies on separate devices · damaged copies can be repaired automatically")
        }
        "RAID1C4" => {
            tr("Four copies on separate devices · damaged copies can be repaired automatically")
        }
        "RAID10" => {
            tr("Mirrored and striped across devices · redundant copies are available for repair")
        }
        "RAID5" => tr("Striped across devices with one parity block for recovery"),
        "RAID6" => tr("Striped across devices with two parity blocks for recovery"),
        _ => trf("Storage layout: {0}", &[profile]),
    }
}

pub(super) fn format_bytes(bytes: u64) -> String {
    const UNITS: [&str; 5] = ["B", "KiB", "MiB", "GiB", "TiB"];
    let mut value = bytes as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit < UNITS.len() - 1 {
        value /= 1024.0;
        unit += 1;
    }
    if unit == 0 {
        format!("{bytes} {}", UNITS[unit])
    } else {
        format!("{value:.1} {}", UNITS[unit])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_storage_for_people() {
        assert_eq!(format_bytes(1024), "1.0 KiB");
        assert_eq!(format_bytes(5 * 1024 * 1024 * 1024), "5.0 GiB");
    }

    #[test]
    fn explains_storage_profiles_without_btrfs_jargon() {
        assert_eq!(
            storage_profile_description("single"),
            tr("One copy · damage can be detected, but there is no spare copy for repair")
        );
        assert_eq!(
            storage_profile_description("DUP"),
            tr("Two copies on this device · a damaged copy can be repaired automatically")
        );
        assert_eq!(
            storage_profile_description("raid1"),
            tr("Two copies on separate devices · a damaged copy can be repaired automatically")
        );
    }
}
