use std::cell::RefCell;
use std::rc::Rc;

use adw::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use crate::dbus_client::{BtrfsFilesystemStatus, SnapshotsManagerHelperClient};
use crate::i18n::{tr, trf};

pub fn filesystem_page(parent: &adw::PreferencesWindow) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::new();
    page.set_title(&tr("File System"));
    page.set_icon_name(Some("drive-harddisk-symbolic"));

    let overview = adw::PreferencesGroup::new();
    overview.set_title(&tr("At a Glance"));
    overview.set_description(Some(&tr(
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
        overview.add(row);
    }
    page.add(&overview);

    let behavior = adw::PreferencesGroup::new();
    behavior.set_title(&tr("Storage Behavior"));
    let compression = value_row(&tr("Transparent compression"), "package-x-generic-symbolic");
    compression.set_subtitle(&tr("Applied automatically to newly written data"));
    let discard = value_row(&tr("SSD space reclamation"), "edit-clear-all-symbolic");
    behavior.add(&compression);
    behavior.add(&discard);
    page.add(&behavior);

    let accounting = adw::PreferencesGroup::new();
    accounting.set_title(&tr("Space Accounting"));
    accounting.set_description(Some(&tr(
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
    accounting.add(&quota);

    let dedup = adw::ActionRow::new();
    dedup.set_title(&tr("Content-based deduplication"));
    dedup.set_subtitle(&tr(
        "Not managed here · Btrfs requires a separate deduplication engine",
    ));
    dedup.add_prefix(&gtk::Image::from_icon_name("edit-copy-symbolic"));
    let learn = gtk::Button::with_label(&tr("Why?"));
    learn.set_valign(gtk::Align::Center);
    dedup.add_suffix(&learn);
    accounting.add(&dedup);
    page.add(&accounting);

    let quota_state = Rc::new(RefCell::new(String::new()));
    refresh_filesystem(
        parent,
        &source,
        &capacity,
        &data,
        &metadata,
        &compression,
        &discard,
        &quota,
        &quota_button,
        &quota_state,
    );

    let parent_quota = parent.clone();
    let quota_state_click = quota_state.clone();
    let source_refresh = source.clone();
    let capacity_refresh = capacity.clone();
    let data_refresh = data.clone();
    let metadata_refresh = metadata.clone();
    let compression_refresh = compression.clone();
    let discard_refresh = discard.clone();
    let quota_refresh = quota.clone();
    let quota_button_refresh = quota_button.clone();
    quota_button.connect_clicked(move |_| {
        let enabled = matches!(quota_state_click.borrow().as_str(), "enabled" | "scanning");
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
        let dialog = confirmation(&parent_quota, &heading, &body, &label, false);
        let parent_run = parent_quota.clone();
        let state_run = quota_state_click.clone();
        let action = action.to_string();
        let rows = (
            source_refresh.clone(),
            capacity_refresh.clone(),
            data_refresh.clone(),
            metadata_refresh.clone(),
            compression_refresh.clone(),
            discard_refresh.clone(),
            quota_refresh.clone(),
            quota_button_refresh.clone(),
        );
        dialog.connect_response(None, move |dialog, response| {
            dialog.close();
            if response != "run" {
                return;
            }
            let parent_done = parent_run.clone();
            let state_done = state_run.clone();
            let action = action.clone();
            let rows = rows.clone();
            glib::spawn_future_local(async move {
                let result = gio::spawn_blocking(move || {
                    SnapshotsManagerHelperClient::new()?
                        .run_btrfs_maintenance_action(&action)
                })
                .await
                .map_err(|_| anyhow::anyhow!("The quota operation stopped unexpectedly"))
                .and_then(|result| result);
                show_result(&parent_done, result);
                refresh_filesystem(
                    &parent_done,
                    &rows.0,
                    &rows.1,
                    &rows.2,
                    &rows.3,
                    &rows.4,
                    &rows.5,
                    &rows.6,
                    &rows.7,
                    &state_done,
                );
            });
        });
        dialog.present();
    });

    let parent_learn = parent.clone();
    learn.connect_clicked(move |_| {
        let dialog = adw::MessageDialog::new(
            Some(&parent_learn),
            Some(&tr("Deduplication needs an engine")),
            Some(&tr(
                "Btrfs does not provide an on/off real-time deduplication switch. Tools such as duperemove and BEES use different strategies, resource limits, and scan scopes. Disk Snapshots Manager will not silently install or run one without a complete policy.",
            )),
        );
        dialog.add_response("close", &tr("Close"));
        dialog.present();
    });

    page
}

pub fn maintenance_page(parent: &adw::PreferencesWindow) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::new();
    page.set_title(&tr("Maintenance"));
    page.set_icon_name(Some("emblem-system-symbolic"));

    let health = adw::PreferencesGroup::new();
    health.set_title(&tr("Integrity"));
    health.set_description(Some(&tr(
        "Scrub reads allocated data and metadata, verifies checksums, and repairs damage when another valid copy exists.",
    )));
    let scrub = maintenance_row(
        &tr("Check file system integrity"),
        &tr("Recommended about once a month"),
        "security-high-symbolic",
        &tr("Start Scrub"),
    );
    scrub.1.set_widget_name("scrub-start");
    scrub.1.add_css_class("suggested-action");
    health.add(&scrub.0);
    page.add(&health);

    let allocation = adw::PreferencesGroup::new();
    allocation.set_title(&tr("Space Allocation"));
    allocation.set_description(Some(&tr(
        "A limited balance only relocates data and metadata block groups that are at most 50% full.",
    )));
    let balance = maintenance_row(
        &tr("Reclaim underused block groups"),
        &tr("Useful after deleting large amounts of data"),
        "drive-harddisk-symbolic",
        &tr("Start Balance"),
    );
    balance.1.set_widget_name("balance-start");
    allocation.add(&balance.0);
    page.add(&allocation);

    let files = adw::PreferencesGroup::new();
    files.set_title(&tr("File Layout"));
    files.set_description(Some(&tr(
        "Defragmentation rewrites file extents and can increase disk usage by breaking shared snapshot or reflink data.",
    )));
    let defrag = maintenance_row(
        &tr("Defragment Home files"),
        &tr("Only /home · snapshot storage is excluded"),
        "dialog-warning-symbolic",
        &tr("Defragment…"),
    );
    defrag.1.add_css_class("destructive-action");
    files.add(&defrag.0);
    page.add(&files);

    let parent_scrub = parent.clone();
    let scrub_row = scrub.0.clone();
    let scrub_button = scrub.1.clone();
    scrub_button.connect_clicked(move |button| {
        let action = if button.widget_name() == "scrub-cancel" {
            "scrub-cancel"
        } else {
            "scrub-start"
        };
        run_maintenance(&parent_scrub, button, &scrub_row, action);
    });

    let parent_balance = parent.clone();
    let balance_row = balance.0.clone();
    let balance_button = balance.1.clone();
    balance_button.clone().connect_clicked(move |button| {
        if button.widget_name() == "balance-cancel" {
            run_maintenance(&parent_balance, button, &balance_row, "balance-cancel");
            return;
        }
        let dialog = confirmation(
            &parent_balance,
            &tr("Start a limited balance?"),
            &tr("Only block groups at most 50% full will be relocated. The operation can use significant disk bandwidth but can be cancelled safely."),
            &tr("Start Balance"),
            false,
        );
        let parent_run = parent_balance.clone();
        let button_run = balance_button.clone();
        let row_run = balance_row.clone();
        dialog.connect_response(None, move |dialog, response| {
            dialog.close();
            if response == "run" {
                run_maintenance(&parent_run, &button_run, &row_run, "balance-start");
            }
        });
        dialog.present();
    });

    let parent_defrag = parent.clone();
    let defrag_row = defrag.0.clone();
    let defrag_button = defrag.1.clone();
    defrag_button.clone().connect_clicked(move |_| {
        let dialog = confirmation(
            &parent_defrag,
            &tr("Defragment Home files?"),
            &tr("This rewrites files below /home using ZSTD compression. It does not enter /.snapshots, but shared extents with existing snapshots may become private and consume more space."),
            &tr("Defragment"),
            true,
        );
        let parent_run = parent_defrag.clone();
        let button_run = defrag_button.clone();
        let row_run = defrag_row.clone();
        dialog.connect_response(None, move |dialog, response| {
            dialog.close();
            if response == "run" {
                run_maintenance(&parent_run, &button_run, &row_run, "defrag-home");
            }
        });
        dialog.present();
    });

    for button in [&scrub.1, &balance.1, &defrag.1] {
        button.set_sensitive(false);
    }
    refresh_maintenance(
        parent, &scrub.0, &scrub.1, &balance.0, &balance.1, &defrag.1,
    );
    page
}

fn value_row(title: &str, icon: &str) -> adw::ActionRow {
    let row = adw::ActionRow::new();
    row.set_title(title);
    row.set_subtitle(&tr("Checking…"));
    row.add_prefix(&gtk::Image::from_icon_name(icon));
    row
}

fn maintenance_row(
    title: &str,
    subtitle: &str,
    icon: &str,
    label: &str,
) -> (adw::ActionRow, gtk::Button) {
    let row = adw::ActionRow::new();
    row.set_title(title);
    row.set_subtitle(subtitle);
    row.add_prefix(&gtk::Image::from_icon_name(icon));
    let button = gtk::Button::with_label(label);
    button.set_valign(gtk::Align::Center);
    row.add_suffix(&button);
    (row, button)
}

#[allow(clippy::too_many_arguments)]
fn refresh_filesystem(
    parent: &adw::PreferencesWindow,
    source: &adw::ActionRow,
    capacity: &adw::ActionRow,
    data: &adw::ActionRow,
    metadata: &adw::ActionRow,
    compression: &adw::ActionRow,
    discard: &adw::ActionRow,
    quota: &adw::ActionRow,
    quota_button: &gtk::Button,
    quota_state: &Rc<RefCell<String>>,
) {
    let weak_parent = parent.downgrade();
    let rows = (
        source.clone(),
        capacity.clone(),
        data.clone(),
        metadata.clone(),
        compression.clone(),
        discard.clone(),
        quota.clone(),
        quota_button.clone(),
    );
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
            Ok(status) => {
                rows.0
                    .set_subtitle(status.error.as_deref().unwrap_or(&tr("Unavailable")));
            }
            Err(error) => rows.0.set_subtitle(&error.to_string()),
        }
    });
}

fn apply_filesystem_status(
    rows: &(
        adw::ActionRow,
        adw::ActionRow,
        adw::ActionRow,
        adw::ActionRow,
        adw::ActionRow,
        adw::ActionRow,
        adw::ActionRow,
        gtk::Button,
    ),
    quota_state: &Rc<RefCell<String>>,
    status: &BtrfsFilesystemStatus,
) {
    rows.0.set_subtitle(&status.source);
    rows.1
        .set_subtitle(&match (status.used_bytes, status.total_bytes) {
            (Some(used), Some(total)) => trf(
                "{0} of {1} used",
                &[&format_bytes(used), &format_bytes(total)],
            ),
            _ => tr("Unavailable"),
        });
    rows.2
        .set_subtitle(&storage_profile_description(&status.data_profile));
    rows.3
        .set_subtitle(&storage_profile_description(&status.metadata_profile));
    rows.4
        .set_subtitle(&trf("{0} · new writes", &[&status.compression]));
    rows.5.set_subtitle(&status.discard);
    *quota_state.borrow_mut() = status.quota.clone();
    let (subtitle, label) = match status.quota.as_str() {
        "enabled" => (tr("Enabled"), tr("Disable…")),
        "scanning" => (tr("Enabled · initial scan in progress"), tr("Disable…")),
        "disabled" => (tr("Disabled"), tr("Enable…")),
        _ => (tr("Unavailable"), tr("Change…")),
    };
    rows.6.set_subtitle(&subtitle);
    rows.7.set_label(&label);
    rows.7.set_sensitive(status.quota != "unavailable");
}

fn refresh_maintenance(
    parent: &adw::PreferencesWindow,
    scrub_row: &adw::ActionRow,
    scrub_button: &gtk::Button,
    balance_row: &adw::ActionRow,
    balance_button: &gtk::Button,
    defrag_button: &gtk::Button,
) {
    let weak = parent.downgrade();
    let scrub_row = scrub_row.clone();
    let scrub_button = scrub_button.clone();
    let balance_row = balance_row.clone();
    let balance_button = balance_button.clone();
    let defrag_button = defrag_button.clone();
    glib::spawn_future_local(async move {
        let status = gio::spawn_blocking(|| {
            SnapshotsManagerHelperClient::new()?.get_btrfs_filesystem_status()
        })
        .await
        .ok()
        .and_then(Result::ok);
        if weak.upgrade().is_none() {
            return;
        }
        let Some(status) = status else {
            scrub_row.set_subtitle(&tr("Status unavailable"));
            balance_row.set_subtitle(&tr("Status unavailable"));
            return;
        };
        if !status.available {
            scrub_row.set_subtitle(&tr("Status unavailable"));
            balance_row.set_subtitle(&tr("Status unavailable"));
            return;
        }
        let scrub_running = status.scrub == "running";
        scrub_row.set_subtitle(&maintenance_status(&status.scrub));
        scrub_button.set_label(&if scrub_running {
            tr("Cancel")
        } else {
            tr("Start Scrub")
        });
        scrub_button.set_widget_name(if scrub_running {
            "scrub-cancel"
        } else {
            "scrub-start"
        });
        scrub_button.set_sensitive(status.scrub != "unavailable");
        let balance_running = matches!(status.balance.as_str(), "running" | "paused");
        balance_row.set_subtitle(&maintenance_status(&status.balance));
        balance_button.set_label(&if balance_running {
            tr("Cancel")
        } else {
            tr("Start Balance")
        });
        balance_button.set_widget_name(if balance_running {
            "balance-cancel"
        } else {
            "balance-start"
        });
        balance_button.set_sensitive(status.balance != "unavailable");
        defrag_button.set_sensitive(true);
    });
}

fn maintenance_status(status: &str) -> String {
    match status {
        "running" => tr("Running…"),
        "paused" => tr("Paused"),
        "idle" => tr("Not running"),
        "never-run" => tr("No completed run recorded"),
        "finished-clean" => tr("Last run completed without errors"),
        value if value.starts_with("finished-repaired:") => tr("Last run repaired errors"),
        value if value.starts_with("finished-with-errors:") => {
            tr("Last run found uncorrectable errors")
        }
        _ => tr("Status unavailable"),
    }
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

fn run_maintenance(
    parent: &adw::PreferencesWindow,
    button: &gtk::Button,
    row: &adw::ActionRow,
    action: &'static str,
) {
    button.set_sensitive(false);
    let old_label = button.label().unwrap_or_default();
    button.set_label(&tr("Working…"));
    let parent = parent.clone();
    let button = button.clone();
    let row = row.clone();
    glib::spawn_future_local(async move {
        let result = gio::spawn_blocking(move || {
            SnapshotsManagerHelperClient::new()?.run_btrfs_maintenance_action(action)
        })
        .await
        .map_err(|_| anyhow::anyhow!("The maintenance operation stopped unexpectedly"))
        .and_then(|result| result);
        let succeeded = result.is_ok();
        show_result(&parent, result);
        button.set_label(&old_label);
        button.set_sensitive(true);
        if !succeeded {
            return;
        }
        match action {
            "scrub-start" => {
                row.set_subtitle(&tr("Running…"));
                button.set_label(&tr("Cancel"));
                button.set_widget_name("scrub-cancel");
            }
            "scrub-cancel" => {
                row.set_subtitle(&tr("Not running"));
                button.set_label(&tr("Start Scrub"));
                button.set_widget_name("scrub-start");
            }
            "balance-start" => {
                row.set_subtitle(&tr("Running…"));
                button.set_label(&tr("Cancel"));
                button.set_widget_name("balance-cancel");
            }
            "balance-cancel" => {
                row.set_subtitle(&tr("Not running"));
                button.set_label(&tr("Start Balance"));
                button.set_widget_name("balance-start");
            }
            "defrag-home" => row.set_subtitle(&tr("Last run completed")),
            _ => {}
        }
    });
}

fn confirmation(
    parent: &adw::PreferencesWindow,
    heading: &str,
    body: &str,
    action_label: &str,
    destructive: bool,
) -> adw::MessageDialog {
    let dialog = adw::MessageDialog::new(Some(parent), Some(heading), Some(body));
    dialog.add_response("cancel", &tr("Cancel"));
    dialog.add_response("run", action_label);
    dialog.set_default_response(Some("cancel"));
    dialog.set_close_response("cancel");
    dialog.set_response_appearance(
        "run",
        if destructive {
            adw::ResponseAppearance::Destructive
        } else {
            adw::ResponseAppearance::Suggested
        },
    );
    dialog
}

fn show_result(parent: &adw::PreferencesWindow, result: anyhow::Result<String>) {
    let (heading, body) = match result {
        Ok(message) => (tr("Btrfs request completed"), message),
        Err(error) => (tr("Btrfs operation failed"), error.to_string()),
    };
    let dialog = adw::MessageDialog::new(Some(parent), Some(&heading), Some(&body));
    dialog.add_response("close", &tr("Close"));
    dialog.present();
}

fn format_bytes(bytes: u64) -> String {
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
    fn renders_native_maintenance_states() {
        assert_eq!(maintenance_status("running"), tr("Running…"));
        assert_eq!(
            maintenance_status("finished-clean"),
            tr("Last run completed without errors")
        );
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
