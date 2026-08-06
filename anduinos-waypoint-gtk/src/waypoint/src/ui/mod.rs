mod advanced_settings;
mod automation_dialog;
mod personal_history;
mod snapshot_page;

use crate::file_history_request::HistoryTarget;
use crate::i18n::tr;
use adw::prelude::*;
use gtk::prelude::*;
use libadwaita as adw;

pub use snapshot_page::SnapshotScope;

pub fn show_personal_history_target(app: &gtk::Application, target: HistoryTarget) {
    personal_history::show_target(app, target);
}

pub struct MainWindow;

impl MainWindow {
    pub fn build(
        app: &gtk::Application,
        snapshot_created_rx: std::sync::mpsc::Receiver<
            crate::signal_listener::SnapshotCreatedEvent,
        >,
    ) -> adw::ApplicationWindow {
        let window = adw::ApplicationWindow::builder()
            .application(app)
            .title(tr("AnduinOS Waypoint"))
            .default_width(920)
            .default_height(720)
            .build();

        let pages = adw::ViewStack::new();
        pages.set_vexpand(true);
        let system = snapshot_page::SnapshotPage::new(&window, SnapshotScope::System);
        let home = snapshot_page::SnapshotPage::new(&window, SnapshotScope::Home);
        pages.add_titled_with_icon(
            system.widget(),
            Some("system"),
            &tr("System Recovery"),
            "drive-harddisk-symbolic",
        );
        pages.add_titled_with_icon(
            home.widget(),
            Some("home"),
            &tr("Personal Files Recovery"),
            "folder-documents-symbolic",
        );

        let switcher = adw::ViewSwitcher::new();
        switcher.set_policy(adw::ViewSwitcherPolicy::Wide);
        switcher.set_stack(Some(&pages));
        let settings = gtk::Button::from_icon_name("preferences-system-symbolic");
        settings.set_tooltip_text(Some(&tr("Advanced Settings")));
        let header = adw::HeaderBar::new();
        header.set_title_widget(Some(&switcher));
        header.pack_end(&settings);

        let view = adw::ToolbarView::new();
        view.add_top_bar(&header);
        view.set_content(Some(&pages));
        let toasts = adw::ToastOverlay::new();
        toasts.set_child(Some(&view));
        window.set_content(Some(&toasts));

        let settings_parent = window.clone();
        settings.connect_clicked(move |_| advanced_settings::show(&settings_parent));

        let system_refresh = system.refresh_handle();
        let home_refresh = home.refresh_handle();
        glib::timeout_add_local(std::time::Duration::from_millis(250), move || {
            let mut changed = false;
            while snapshot_created_rx.try_recv().is_ok() {
                changed = true;
            }
            if changed {
                system_refresh();
                home_refresh();
            }
            glib::ControlFlow::Continue
        });

        system.refresh();
        home.refresh();
        window
    }
}
