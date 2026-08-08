use adw::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use super::shared::{confirmation, show_result};
use crate::dbus_client::SnapshotsManagerHelperClient;
use crate::i18n::tr;

#[derive(Clone)]
struct MaintenanceControl {
    row: adw::ActionRow,
    button: gtk::Button,
}

#[derive(Clone)]
struct MaintenanceControls {
    scrub: MaintenanceControl,
    balance: MaintenanceControl,
    defrag: MaintenanceControl,
}

pub fn maintenance_page(parent: &adw::PreferencesWindow) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::new();
    page.set_title(&tr("Maintenance"));
    page.set_icon_name(Some("emblem-system-symbolic"));

    let (health, scrub) = maintenance_group(
        &tr("Integrity"),
        &tr(
            "Scrub reads allocated data and metadata, verifies checksums, and repairs damage when another valid copy exists.",
        ),
        &tr("Check file system integrity"),
        &tr("Recommended about once a month"),
        "security-high-symbolic",
        &tr("Start Scrub"),
    );
    scrub.button.set_widget_name("scrub-start");
    scrub.button.add_css_class("suggested-action");
    page.add(&health);

    let (allocation, balance) = maintenance_group(
        &tr("Space Allocation"),
        &tr(
            "A limited balance only relocates data and metadata block groups that are at most 50% full.",
        ),
        &tr("Reclaim underused block groups"),
        &tr("Useful after deleting large amounts of data"),
        "drive-harddisk-symbolic",
        &tr("Start Balance"),
    );
    balance.button.set_widget_name("balance-start");
    page.add(&allocation);

    let (files, defrag) = maintenance_group(
        &tr("File Layout"),
        &tr(
            "Defragmentation rewrites file extents and can increase disk usage by breaking shared snapshot or reflink data.",
        ),
        &tr("Defragment Home files"),
        &tr("Only /home · snapshot storage is excluded"),
        "dialog-warning-symbolic",
        &tr("Defragment…"),
    );
    defrag.button.add_css_class("destructive-action");
    page.add(&files);

    let controls = MaintenanceControls {
        scrub,
        balance,
        defrag,
    };
    connect_scrub(parent, &controls.scrub);
    connect_balance(parent, &controls.balance);
    connect_defrag(parent, &controls.defrag);
    for control in [&controls.scrub, &controls.balance, &controls.defrag] {
        control.button.set_sensitive(false);
    }
    refresh_maintenance(parent, &controls);
    page
}

fn maintenance_group(
    group_title: &str,
    description: &str,
    row_title: &str,
    row_subtitle: &str,
    icon: &str,
    button_label: &str,
) -> (adw::PreferencesGroup, MaintenanceControl) {
    let group = adw::PreferencesGroup::new();
    group.set_title(group_title);
    group.set_description(Some(description));
    let row = adw::ActionRow::new();
    row.set_title(row_title);
    row.set_subtitle(row_subtitle);
    row.add_prefix(&gtk::Image::from_icon_name(icon));
    let button = gtk::Button::with_label(button_label);
    button.set_valign(gtk::Align::Center);
    row.add_suffix(&button);
    group.add(&row);
    (group, MaintenanceControl { row, button })
}

fn connect_scrub(parent: &adw::PreferencesWindow, control: &MaintenanceControl) {
    let parent = parent.clone();
    let row = control.row.clone();
    control.button.connect_clicked(move |button| {
        let action = if button.widget_name() == "scrub-cancel" {
            "scrub-cancel"
        } else {
            "scrub-start"
        };
        run_maintenance(&parent, button, &row, action);
    });
}

fn connect_balance(parent: &adw::PreferencesWindow, control: &MaintenanceControl) {
    let parent = parent.clone();
    let control = control.clone();
    control.button.clone().connect_clicked(move |button| {
        if button.widget_name() == "balance-cancel" {
            run_maintenance(&parent, button, &control.row, "balance-cancel");
            return;
        }
        let dialog = confirmation(
            &parent,
            &tr("Start a limited balance?"),
            &tr("Only block groups at most 50% full will be relocated. The operation can use significant disk bandwidth but can be cancelled safely."),
            &tr("Start Balance"),
            false,
        );
        let parent = parent.clone();
        let control = control.clone();
        dialog.connect_response(None, move |dialog, response| {
            dialog.close();
            if response == "run" {
                run_maintenance(&parent, &control.button, &control.row, "balance-start");
            }
        });
        dialog.present();
    });
}

fn connect_defrag(parent: &adw::PreferencesWindow, control: &MaintenanceControl) {
    let parent = parent.clone();
    let control = control.clone();
    control.button.clone().connect_clicked(move |_| {
        let dialog = confirmation(
            &parent,
            &tr("Defragment Home files?"),
            &tr("This rewrites files below /home using ZSTD compression. It does not enter /.snapshots, but shared extents with existing snapshots may become private and consume more space."),
            &tr("Defragment"),
            true,
        );
        let parent = parent.clone();
        let control = control.clone();
        dialog.connect_response(None, move |dialog, response| {
            dialog.close();
            if response == "run" {
                run_maintenance(&parent, &control.button, &control.row, "defrag-home");
            }
        });
        dialog.present();
    });
}

fn refresh_maintenance(parent: &adw::PreferencesWindow, controls: &MaintenanceControls) {
    let weak_parent = parent.downgrade();
    let controls = controls.clone();
    glib::spawn_future_local(async move {
        let status = gio::spawn_blocking(|| {
            SnapshotsManagerHelperClient::new()?.get_btrfs_filesystem_status()
        })
        .await
        .ok()
        .and_then(Result::ok);
        if weak_parent.upgrade().is_none() {
            return;
        }
        let Some(status) = status else {
            set_status_unavailable(&controls);
            return;
        };
        if !status.available {
            set_status_unavailable(&controls);
            return;
        }
        update_running_control(
            &controls.scrub,
            &status.scrub,
            "scrub-start",
            "scrub-cancel",
            &tr("Start Scrub"),
        );
        update_running_control(
            &controls.balance,
            &status.balance,
            "balance-start",
            "balance-cancel",
            &tr("Start Balance"),
        );
        controls.defrag.button.set_sensitive(true);
    });
}

fn set_status_unavailable(controls: &MaintenanceControls) {
    controls.scrub.row.set_subtitle(&tr("Status unavailable"));
    controls.balance.row.set_subtitle(&tr("Status unavailable"));
}

fn update_running_control(
    control: &MaintenanceControl,
    status: &str,
    start_name: &str,
    cancel_name: &str,
    start_label: &str,
) {
    let running = matches!(status, "running" | "paused");
    control.row.set_subtitle(&maintenance_status(status));
    if running {
        control.button.set_label(&tr("Cancel"));
    } else {
        control.button.set_label(start_label);
    }
    control
        .button
        .set_widget_name(if running { cancel_name } else { start_name });
    control.button.set_sensitive(status != "unavailable");
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
            "scrub-start" => set_control_state(&row, &button, "Running…", "Cancel", "scrub-cancel"),
            "scrub-cancel" => {
                set_control_state(&row, &button, "Not running", "Start Scrub", "scrub-start")
            }
            "balance-start" => {
                set_control_state(&row, &button, "Running…", "Cancel", "balance-cancel")
            }
            "balance-cancel" => set_control_state(
                &row,
                &button,
                "Not running",
                "Start Balance",
                "balance-start",
            ),
            "defrag-home" => row.set_subtitle(&tr("Last run completed")),
            _ => {}
        }
    });
}

fn set_control_state(
    row: &adw::ActionRow,
    button: &gtk::Button,
    subtitle: &str,
    label: &str,
    widget_name: &str,
) {
    row.set_subtitle(&tr(subtitle));
    button.set_label(&tr(label));
    button.set_widget_name(widget_name);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn renders_native_maintenance_states() {
        assert_eq!(maintenance_status("running"), tr("Running…"));
        assert_eq!(
            maintenance_status("finished-clean"),
            tr("Last run completed without errors")
        );
    }
}
