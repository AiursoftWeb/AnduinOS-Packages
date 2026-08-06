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

use crate::application::WaypointApplication;
use crate::file_history_request::HistoryTarget;
use crate::i18n::tr;
use crate::signal_listener::SnapshotSignalMonitor;

pub use snapshot_model::SnapshotScope;

pub fn show_personal_history_target(app: &WaypointApplication, target: HistoryTarget) {
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
    }

    #[glib::object_subclass]
    impl ObjectSubclass for MainWindow {
        const NAME: &'static str = "WaypointMainWindow";
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
    pub fn new(app: &WaypointApplication, monitor: SnapshotSignalMonitor) -> Self {
        let window: Self = glib::Object::builder()
            .property("application", app)
            .property("title", tr("AnduinOS Waypoint"))
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
        let compact_title = adw::WindowTitle::new(&tr("AnduinOS Waypoint"), "");
        let title_stack = gtk::Stack::new();
        title_stack.add_named(&switcher, Some("pages"));
        title_stack.add_named(&compact_title, Some("title"));
        title_stack.set_visible_child_name("pages");

        let switcher_bar = adw::ViewSwitcherBar::new();
        switcher_bar.set_stack(Some(&pages));
        let refresh = gtk::Button::from_icon_name("view-refresh-symbolic");
        refresh.set_tooltip_text(Some(&tr("Refresh snapshots")));
        refresh.set_action_name(Some("win.refresh"));
        let menu_model = gio::Menu::new();
        menu_model.append(Some(&tr("Advanced Settings")), Some("app.preferences"));
        menu_model.append(Some(&tr("About Waypoint")), Some("app.about"));
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
            page.refresh();
        }
    }

    fn refresh_all(&self) {
        if let Some(page) = self.imp().system_page.borrow().as_ref() {
            page.refresh();
        }
        if let Some(page) = self.imp().home_page.borrow().as_ref() {
            page.refresh();
        }
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
