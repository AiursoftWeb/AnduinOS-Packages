use adw::prelude::*;
use libadwaita as adw;

use crate::i18n::tr;

pub fn show(parent: &adw::ApplicationWindow) {
    let window = adw::PreferencesWindow::new();
    window.set_title(Some(&tr("Information")));
    window.set_transient_for(Some(parent));
    window.set_modal(true);
    window.set_default_size(
        super::AUXILIARY_WINDOW_DEFAULT_WIDTH,
        super::AUXILIARY_WINDOW_DEFAULT_HEIGHT,
    );
    window.add(&super::btrfs_settings::filesystem_page(&window));
    window.add(&super::btrfs_settings::health_page(&window));
    window.present();
}
