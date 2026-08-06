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

pub fn show_personal_history_target(app: &adw::Application, target: HistoryTarget) {
    personal_history::show_target(app, target);
}

pub struct MainWindow;

impl MainWindow {
    pub fn build(
        app: &adw::Application,
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
        let compact_title = adw::WindowTitle::new(&tr("AnduinOS Waypoint"), "");
        let title_stack = gtk::Stack::new();
        title_stack.add_named(&switcher, Some("pages"));
        title_stack.add_named(&compact_title, Some("title"));
        title_stack.set_visible_child_name("pages");

        let switcher_bar = adw::ViewSwitcherBar::new();
        switcher_bar.set_stack(Some(&pages));
        let settings = gtk::Button::from_icon_name("preferences-system-symbolic");
        settings.set_tooltip_text(Some(&tr("Advanced Settings")));
        let header = adw::HeaderBar::new();
        header.set_title_widget(Some(&title_stack));
        header.pack_end(&settings);

        let view = adw::ToolbarView::new();
        view.add_top_bar(&header);
        view.add_bottom_bar(&switcher_bar);
        view.set_content(Some(&pages));
        let toasts = adw::ToastOverlay::new();
        toasts.set_child(Some(&view));
        window.set_content(Some(&toasts));

        // Follow the standard adaptive Adwaita pattern: page navigation lives
        // in the header when there is room and moves to a bottom bar on narrow
        // windows. Breakpoint setters restore the original values on unapply.
        let narrow = adw::Breakpoint::new(adw::BreakpointCondition::new_length(
            adw::BreakpointConditionLengthType::MaxWidth,
            650.0,
            adw::LengthUnit::Sp,
        ));
        narrow.add_setter(
            &title_stack,
            "visible-child-name",
            Some(&"title".to_value()),
        );
        narrow.add_setter(&switcher_bar, "reveal", Some(&true.to_value()));
        window.add_breakpoint(narrow);

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
