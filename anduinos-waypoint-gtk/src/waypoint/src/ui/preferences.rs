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
