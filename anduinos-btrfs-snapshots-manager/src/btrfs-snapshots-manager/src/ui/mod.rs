mod advanced_settings;
mod automation_dialog;
mod personal_history;
mod snapshot_model;
mod snapshot_page;

use std::cell::{Cell, RefCell};
use std::time::Duration;

use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use crate::application::SnapshotsManagerApplication;
use crate::file_history_request::HistoryTarget;
use crate::i18n::{tr, trf};
use crate::signal_listener::SnapshotSignalMonitor;

pub use snapshot_model::SnapshotScope;

pub fn show_personal_history_target(app: &SnapshotsManagerApplication, target: HistoryTarget) {
    personal_history::show_target(app.upcast_ref(), target);
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct MainWindow {
        pub pages: RefCell<Option<adw::ViewStack>>,
        pub system_page: RefCell<Option<snapshot_page::SnapshotPage>>,
        pub home_page: RefCell<Option<snapshot_page::SnapshotPage>>,
        pub signal_monitor: RefCell<Option<SnapshotSignalMonitor>>,
        pub signal_source: RefCell<Option<glib::SourceId>>,
        pub last_system_generation: Cell<u64>,
        pub last_home_generation: Cell<u64>,
        pub space_refreshing: Cell<bool>,
        pub space_revealer: RefCell<Option<gtk::Revealer>>,
        pub space_progress: RefCell<Option<gtk::ProgressBar>>,
        pub space_label: RefCell<Option<gtk::Label>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for MainWindow {
        const NAME: &'static str = "SnapshotsManagerMainWindow";
        type Type = super::MainWindow;
        type ParentType = adw::ApplicationWindow;
    }

    impl ObjectImpl for MainWindow {
        fn dispose(&self) {
            if let Some(source) = self.signal_source.borrow_mut().take() {
                source.remove();
            }
            self.system_page.borrow_mut().take();
            self.home_page.borrow_mut().take();
            self.pages.borrow_mut().take();
            self.signal_monitor.borrow_mut().take();
            self.space_revealer.borrow_mut().take();
            self.space_progress.borrow_mut().take();
            self.space_label.borrow_mut().take();
        }
    }

    impl WidgetImpl for MainWindow {}
    impl WindowImpl for MainWindow {}
    impl ApplicationWindowImpl for MainWindow {}
    impl AdwApplicationWindowImpl for MainWindow {}
}

glib::wrapper! {
    pub struct MainWindow(ObjectSubclass<imp::MainWindow>)
        @extends gtk::Widget, gtk::Window, gtk::ApplicationWindow, adw::ApplicationWindow,
        @implements gio::ActionGroup, gio::ActionMap, gtk::Accessible, gtk::Buildable,
                    gtk::ConstraintTarget, gtk::Native, gtk::Root, gtk::ShortcutManager;
}

impl MainWindow {
    pub fn new(app: &SnapshotsManagerApplication, monitor: SnapshotSignalMonitor) -> Self {
        let window: Self = glib::Object::builder()
            .property("application", app)
            .property("title", tr("Disk Snapshots Manager"))
            .property("default-width", 920)
            .property("default-height", 720)
            .property("icon-name", crate::application::APP_ID)
            .build();
        window.setup_ui(monitor);
        window
    }

    pub fn show_advanced_settings(&self) {
        advanced_settings::show(self.upcast_ref());
    }

    fn setup_ui(&self, monitor: SnapshotSignalMonitor) {
        let pages = adw::ViewStack::new();
        pages.set_vexpand(true);
        let system = snapshot_page::SnapshotPage::new(self.upcast_ref(), SnapshotScope::System);
        let home = snapshot_page::SnapshotPage::new(self.upcast_ref(), SnapshotScope::Home);
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
        let compact_title = adw::WindowTitle::new(&tr("Disk Snapshots Manager"), "");
        let title_stack = gtk::Stack::new();
        title_stack.add_named(&switcher, Some("pages"));
        title_stack.add_named(&compact_title, Some("title"));
        title_stack.set_visible_child_name("pages");

        let switcher_bar = adw::ViewSwitcherBar::new();
        switcher_bar.set_stack(Some(&pages));

        let space_bar = gtk::Box::new(gtk::Orientation::Horizontal, 10);
        space_bar.set_margin_top(6);
        space_bar.set_margin_bottom(6);
        space_bar.set_margin_start(18);
        space_bar.set_margin_end(18);
        let space_icon = gtk::Image::from_icon_name("drive-harddisk-symbolic");
        space_icon.add_css_class("dim-label");
        let space_progress = gtk::ProgressBar::new();
        space_progress.set_hexpand(true);
        space_progress.set_valign(gtk::Align::Center);
        let space_label = gtk::Label::new(None);
        space_label.add_css_class("caption");
        space_label.add_css_class("dim-label");
        space_bar.append(&space_icon);
        space_bar.append(&space_progress);
        space_bar.append(&space_label);
        let space_revealer = gtk::Revealer::new();
        space_revealer.set_transition_type(gtk::RevealerTransitionType::Crossfade);
        space_revealer.set_child(Some(&space_bar));
        let refresh = gtk::Button::from_icon_name("view-refresh-symbolic");
        refresh.set_tooltip_text(Some(&tr("Refresh snapshots")));
        refresh.set_action_name(Some("win.refresh"));
        let menu_model = gio::Menu::new();
        menu_model.append(Some(&tr("Advanced Settings")), Some("app.preferences"));
        menu_model.append(Some(&tr("About Disk Snapshots Manager")), Some("app.about"));
        let menu = gtk::MenuButton::builder()
            .icon_name("open-menu-symbolic")
            .tooltip_text(tr("Main Menu"))
            .menu_model(&menu_model)
            .build();
        let header = adw::HeaderBar::new();
        header.set_title_widget(Some(&title_stack));
        header.pack_end(&menu);
        header.pack_end(&refresh);

        let view = adw::ToolbarView::new();
        view.add_top_bar(&header);
        view.add_bottom_bar(&space_revealer);
        view.add_bottom_bar(&switcher_bar);
        view.set_content(Some(&pages));
        let toasts = adw::ToastOverlay::new();
        toasts.set_child(Some(&view));
        self.set_content(Some(&toasts));

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
        system.add_compact_setters(&narrow);
        home.add_compact_setters(&narrow);
        self.add_breakpoint(narrow);

        *self.imp().pages.borrow_mut() = Some(pages);
        *self.imp().system_page.borrow_mut() = Some(system);
        *self.imp().home_page.borrow_mut() = Some(home);
        *self.imp().space_revealer.borrow_mut() = Some(space_revealer);
        *self.imp().space_progress.borrow_mut() = Some(space_progress);
        *self.imp().space_label.borrow_mut() = Some(space_label);
        self.install_actions();
        self.start_signal_refresh(monitor);
        self.refresh_all();
    }

    fn install_actions(&self) {
        let refresh = gio::SimpleAction::new("refresh", None);
        let weak = self.downgrade();
        refresh.connect_activate(move |_, _| {
            if let Some(window) = weak.upgrade() {
                window.refresh_current();
            }
        });
        self.add_action(&refresh);

        let search = gio::SimpleAction::new("search", None);
        let weak = self.downgrade();
        search.connect_activate(move |_, _| {
            if let Some(window) = weak.upgrade()
                && let Some(page) = window.current_page()
            {
                page.focus_search();
            }
        });
        self.add_action(&search);

        let create = gio::SimpleAction::new("create", None);
        let weak = self.downgrade();
        create.connect_activate(move |_, _| {
            if let Some(window) = weak.upgrade()
                && let Some(page) = window.current_page()
            {
                page.create_snapshot();
            }
        });
        self.add_action(&create);

        let close = gio::SimpleAction::new("close", None);
        let weak = self.downgrade();
        close.connect_activate(move |_, _| {
            if let Some(window) = weak.upgrade() {
                window.close();
            }
        });
        self.add_action(&close);
    }

    fn current_page(&self) -> Option<snapshot_page::SnapshotPage> {
        let is_home = self
            .imp()
            .pages
            .borrow()
            .as_ref()
            .and_then(|pages| pages.visible_child_name())
            .as_deref()
            == Some("home");
        if is_home {
            self.imp().home_page.borrow().clone()
        } else {
            self.imp().system_page.borrow().clone()
        }
    }

    fn refresh_current(&self) {
        if let Some(page) = self.current_page() {
            page.refresh_and_remeasure_spaces();
        }
        self.refresh_filesystem_space();
    }

    fn refresh_all(&self) {
        if let Some(page) = self.imp().system_page.borrow().as_ref() {
            page.refresh();
        }
        if let Some(page) = self.imp().home_page.borrow().as_ref() {
            page.refresh();
        }
        self.refresh_filesystem_space();
    }

    fn refresh_filesystem_space(&self) {
        if self.imp().space_refreshing.replace(true) {
            return;
        }
        let weak = self.downgrade();
        glib::spawn_future_local(async move {
            let result = gio::spawn_blocking(probe_filesystem_space)
                .await
                .map_err(|_| "Filesystem space query stopped unexpectedly".to_string())
                .and_then(|result| result.map_err(|error| error.to_string()));
            let Some(window) = weak.upgrade() else {
                return;
            };
            window.imp().space_refreshing.set(false);
            match result {
                Ok((total, available)) if total > 0 => {
                    let used = total.saturating_sub(available);
                    if let Some(progress) = window.imp().space_progress.borrow().as_ref() {
                        progress.set_fraction((used as f64 / total as f64).clamp(0.0, 1.0));
                        progress.set_tooltip_text(Some(&trf(
                            "{0} available",
                            &[&snapshots_manager_common::format_bytes(available)],
                        )));
                    }
                    if let Some(label) = window.imp().space_label.borrow().as_ref() {
                        label.set_label(&format!(
                            "{} / {}",
                            snapshots_manager_common::format_bytes(used),
                            snapshots_manager_common::format_bytes(total)
                        ));
                    }
                    if let Some(revealer) = window.imp().space_revealer.borrow().as_ref() {
                        revealer.set_reveal_child(true);
                    }
                }
                Ok(_) => log::warn!("Filesystem reported a zero total size"),
                Err(error) => log::warn!("Could not query filesystem space: {error}"),
            }
        });
    }

    fn start_signal_refresh(&self, monitor: SnapshotSignalMonitor) {
        self.imp()
            .last_system_generation
            .set(monitor.system_generation());
        self.imp()
            .last_home_generation
            .set(monitor.home_generation());
        *self.imp().signal_monitor.borrow_mut() = Some(monitor);
        let weak = self.downgrade();
        let source = glib::timeout_add_local(Duration::from_millis(250), move || {
            let Some(window) = weak.upgrade() else {
                return glib::ControlFlow::Break;
            };
            let Some(monitor) = window.imp().signal_monitor.borrow().clone() else {
                return glib::ControlFlow::Break;
            };
            let system = monitor.system_generation();
            if system != window.imp().last_system_generation.replace(system)
                && let Some(page) = window.imp().system_page.borrow().as_ref()
            {
                page.refresh();
            }
            let home = monitor.home_generation();
            if home != window.imp().last_home_generation.replace(home)
                && let Some(page) = window.imp().home_page.borrow().as_ref()
            {
                page.refresh();
            }
            glib::ControlFlow::Continue
        });
        *self.imp().signal_source.borrow_mut() = Some(source);
    }
}

fn probe_filesystem_space() -> std::io::Result<(u64, u64)> {
    let path = std::ffi::CString::new("/").expect("the root path contains no NUL byte");
    let mut values = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    if unsafe { libc::statvfs(path.as_ptr(), values.as_mut_ptr()) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    let values = unsafe { values.assume_init() };
    let total = values
        .f_blocks
        .checked_mul(values.f_frsize)
        .ok_or_else(|| std::io::Error::other("filesystem size overflow"))?;
    let available = values
        .f_bavail
        .checked_mul(values.f_frsize)
        .ok_or_else(|| std::io::Error::other("available space overflow"))?;
    Ok((total, available))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filesystem_footer_probe_reports_a_bounded_value() {
        let (total, available) = probe_filesystem_space().unwrap();
        assert!(total > 0);
        assert!(available <= total);
    }
}
