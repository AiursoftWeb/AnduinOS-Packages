use adw::prelude::*;
use gtk::prelude::*;
use libadwaita as adw;

use crate::i18n::tr;

/// The system recovery scope is an AnduinOS storage ABI, not a free-form list
/// of Btrfs subvolumes. Showing it read-only keeps the UI aligned with the
/// trusted helper and avoids configurations that cannot be restored safely.
pub fn create_recovery_scope_page() -> adw::PreferencesPage {
    let page = adw::PreferencesPage::new();
    page.set_title(&tr("Recovery Scope"));
    page.set_icon_name(Some("drive-harddisk-symbolic"));

    let system = adw::PreferencesGroup::new();
    system.set_title(&tr("System Recovery"));
    system.set_description(Some(&tr(
        "Every recovery point contains the complete immutable AnduinOS system deployment.",
    )));
    let root = adw::ActionRow::new();
    root.set_title(&tr("System"));
    root.set_subtitle(&tr("@root · always included"));
    let included = gtk::Image::from_icon_name("emblem-ok-symbolic");
    included.add_css_class("success");
    root.add_suffix(&included);
    system.add(&root);
    page.add(&system);

    let personal = adw::PreferencesGroup::new();
    personal.set_title(&tr("Personal Files"));
    personal.set_description(Some(&tr(
        "Personal Files use an independent @home history. Browse and recover files without changing the System deployment.",
    )));
    let home = adw::ActionRow::new();
    home.set_title(&tr("Separate Personal Files recovery"));
    home.set_subtitle(&tr(
        "Available from Personal Files History; System restore still leaves @home unchanged",
    ));
    let available = gtk::Image::from_icon_name("emblem-ok-symbolic");
    available.add_css_class("success");
    home.add_suffix(&available);
    personal.add(&home);
    page.add(&personal);

    let excluded = adw::PreferencesGroup::new();
    excluded.set_title(&tr("Always Persistent"));
    excluded.set_description(Some(&tr(
        "Logs, recovery points, swap, containers, virtual-machine images, and the EFI System Partition are never copied into a system recovery point.",
    )));
    page.add(&excluded);

    page
}

pub fn create_package_changes_page() -> adw::PreferencesPage {
    let page = adw::PreferencesPage::new();
    page.set_title(&tr("Package Changes"));
    page.set_icon_name(Some("system-software-update-symbolic"));

    let group = adw::PreferencesGroup::new();
    group.set_title(&tr("APT Recovery Points"));
    group.set_description(Some(&tr(
        "These event-based recovery points are independent of automatic hourly, daily, weekly, and monthly schedules.",
    )));

    let before_row = adw::ActionRow::new();
    before_row.set_title(&tr("Before installing or updating packages"));
    before_row.set_subtitle(&tr(
        "Recommended · provides a recovery point if a package change causes problems",
    ));
    let before = gtk::CheckButton::new();
    before_row.add_suffix(&before);
    before_row.set_activatable_widget(Some(&before));

    let after_row = adw::ActionRow::new();
    after_row.set_title(&tr("After installing or updating packages"));
    after_row.set_subtitle(&tr(
        "Optional · records the resulting package state for comparison and auditing",
    ));
    let after = gtk::CheckButton::new();
    after_row.add_suffix(&after);
    after_row.set_activatable_widget(Some(&after));
    let error_row = adw::ActionRow::new();
    error_row.set_title(&tr("Could not save the APT recovery-point setting"));
    error_row.set_subtitle(&tr("The previous setting remains active."));
    error_row.add_css_class("error");
    error_row.set_visible(false);

    let policy = crate::dbus_client::WaypointHelperClient::new()
        .and_then(|client| client.get_apt_snapshot_policy())
        .unwrap_or((true, false));
    before.set_active(policy.0);
    after.set_active(policy.1);
    group.add(&before_row);
    group.add(&after_row);
    group.add(&error_row);
    page.add(&group);

    let error_for_save = error_row.clone();
    let save = std::rc::Rc::new(move |snapshot_before: bool, snapshot_after: bool| -> bool {
        let result = crate::dbus_client::WaypointHelperClient::new().and_then(|client| {
            let result = client.save_apt_snapshot_policy(snapshot_before, snapshot_after)?;
            if result.0 {
                Ok(())
            } else {
                anyhow::bail!(result.1)
            }
        });
        error_for_save.set_visible(result.is_err());
        if let Err(error) = &result {
            log::error!("Failed to save APT snapshot policy: {error}");
        }
        result.is_ok()
    });
    let changing = std::rc::Rc::new(std::cell::Cell::new(false));
    let after_for_before = after.clone();
    let save_before = save.clone();
    let changing_before = changing.clone();
    before.connect_toggled(move |row| {
        if changing_before.replace(true) {
            return;
        }
        if !save_before(row.is_active(), after_for_before.is_active()) {
            row.set_active(!row.is_active());
        }
        changing_before.set(false);
    });
    let before_for_after = before.clone();
    after.connect_toggled(move |row| {
        if changing.replace(true) {
            return;
        }
        if !save(before_for_after.is_active(), row.is_active()) {
            row.set_active(!row.is_active());
        }
        changing.set(false);
    });

    page
}
