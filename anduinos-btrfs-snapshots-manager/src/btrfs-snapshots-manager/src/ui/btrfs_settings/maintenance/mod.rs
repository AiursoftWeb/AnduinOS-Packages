mod balance;
mod defrag;
mod scrub;
mod task_dialog;

use adw::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use crate::dbus_client::{BtrfsFilesystemStatus, SnapshotsManagerHelperClient};
use crate::i18n::tr;

#[derive(Clone)]
pub(super) struct MaintenanceControl {
    pub(super) row: adw::ActionRow,
    pub(super) button: gtk::Button,
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
            "Scrub reads allocated data and metadata, verifies checksums, and reports damage without modifying file data.",
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
    scrub::connect(parent, &controls.scrub);
    balance::connect(parent, &controls.balance);
    defrag::connect(parent, &controls.defrag);
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

fn refresh_maintenance(parent: &adw::PreferencesWindow, controls: &MaintenanceControls) {
    let weak_parent = parent.downgrade();
    let controls = controls.clone();
    glib::spawn_future_local(async move {
        let status = query_btrfs_status().await.ok();
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
        scrub::update_control(&controls.scrub, &status.scrub);
        task_dialog::update_control(
            &controls.balance,
            &status.balance,
            task_dialog::TaskKind::Balance,
        );
        task_dialog::update_control(
            &controls.defrag,
            &status.defrag,
            task_dialog::TaskKind::Defrag,
        );
    });
}

fn set_status_unavailable(controls: &MaintenanceControls) {
    controls.scrub.row.set_subtitle(&tr("Status unavailable"));
    controls.balance.row.set_subtitle(&tr("Status unavailable"));
    controls.defrag.row.set_subtitle(&tr("Status unavailable"));
}

pub(super) async fn query_btrfs_status() -> anyhow::Result<BtrfsFilesystemStatus> {
    gio::spawn_blocking(|| SnapshotsManagerHelperClient::new()?.get_btrfs_filesystem_status())
        .await
        .map_err(|_| anyhow::anyhow!("The Btrfs status query stopped unexpectedly"))
        .and_then(|result| result)
}
