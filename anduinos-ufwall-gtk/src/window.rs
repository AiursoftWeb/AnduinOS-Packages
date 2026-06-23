use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;
use gtk::gio;

use std::cell::RefCell;

use crate::application::UfwallApplication;
use crate::i18n::i18n;
use crate::views::{dashboard_view::DashboardView, profiles_view::ProfilesView, rules_view::RulesView, status_view::StatusView};
use crate::ufw::monitor;
use crate::ufw::show_error;
use crate::ufw::backend;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct UfwallWindow {
        pub split_view: RefCell<Option<adw::OverlaySplitView>>,
        pub stack: RefCell<Option<gtk::Stack>>,
        pub dashboard_view: RefCell<Option<DashboardView>>,
        pub status_view: RefCell<Option<StatusView>>,
        pub rules_view: RefCell<Option<RulesView>>,
        pub profiles_view: RefCell<Option<ProfilesView>>,
        pub rules_row: RefCell<Option<gtk::ListBoxRow>>,
        pub profiles_row: RefCell<Option<gtk::ListBoxRow>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for UfwallWindow {
        const NAME: &'static str = "UfwallWindow";
        type Type = super::UfwallWindow;
        type ParentType = adw::ApplicationWindow;
    }

    impl ObjectImpl for UfwallWindow {
        fn constructed(&self) {
            self.parent_constructed();
            let obj = self.obj();
            obj.setup_ui();
            obj.setup_monitoring();
        }
    }
    
    impl WidgetImpl for UfwallWindow {}
    impl WindowImpl for UfwallWindow {}
    impl ApplicationWindowImpl for UfwallWindow {}
    impl AdwApplicationWindowImpl for UfwallWindow {}
}

glib::wrapper! {
    pub struct UfwallWindow(ObjectSubclass<imp::UfwallWindow>)
        @extends gtk::Widget, gtk::Window, gtk::ApplicationWindow, adw::ApplicationWindow,
        @implements gio::ActionGroup, gio::ActionMap, gtk::Accessible, gtk::Buildable, 
                    gtk::ConstraintTarget, gtk::Native, gtk::Root, gtk::ShortcutManager;
}

impl UfwallWindow {
    pub fn new(app: &UfwallApplication) -> Self {
        glib::Object::builder()
            .property("application", app)
            .property("title", i18n("Firewall"))
            .property("default-width", 900)
            .property("default-height", 650)
            .property("icon-name", "com.anduinos.ufwall")
            .build()
    }

    fn setup_ui(&self) {
        let imp = self.imp();

        // Menu button (right)
        let menu_button = gtk::MenuButton::builder()
            .icon_name("open-menu-symbolic")
            .build();
        menu_button.add_css_class("primary");
        
        let menu_model = gio::Menu::new();
        menu_model.append(Some(&i18n("About")), Some("app.about"));
        menu_button.set_menu_model(Some(&menu_model));

        // Sidebar setup
        let sidebar_list = gtk::ListBox::builder()
            .css_classes(["navigation-sidebar"])
            .selection_mode(gtk::SelectionMode::Single)
            .build();
        
        let create_sidebar_row = |icon_name: &str, title: &str| -> gtk::ListBoxRow {
            let box_ = gtk::Box::builder()
                .orientation(gtk::Orientation::Horizontal)
                .spacing(12)
                .margin_start(12)
                .margin_end(12)
                .margin_top(10)
                .margin_bottom(10)
                .build();
            
            let icon = gtk::Image::builder()
                .icon_name(icon_name)
                .build();
            
            let label = gtk::Label::builder()
                .label(title)
                .halign(gtk::Align::Start)
                .hexpand(true)
                .build();
            
            box_.append(&icon);
            box_.append(&label);
            
            gtk::ListBoxRow::builder().child(&box_).build()
        };

        let dashboard_row = create_sidebar_row("utilities-system-monitor-symbolic", &i18n("Dashboard"));
        let status_row = create_sidebar_row("security-high-symbolic", &i18n("Status"));
        let rules_row = create_sidebar_row("view-list-symbolic", &i18n("Rules"));
        let profiles_row = create_sidebar_row("emblem-system-symbolic", &i18n("Profiles"));

        sidebar_list.append(&dashboard_row);
        sidebar_list.append(&status_row);
        sidebar_list.append(&rules_row);
        sidebar_list.append(&profiles_row);
        sidebar_list.select_row(Some(&dashboard_row));

        let sidebar_header = adw::HeaderBar::builder()
            .show_end_title_buttons(false)
            .show_start_title_buttons(true)
            .build();

        let sidebar_toolbar = adw::ToolbarView::builder()
            .content(&sidebar_list)
            .build();
        sidebar_toolbar.add_top_bar(&sidebar_header);

        // Content Setup
        let stack = gtk::Stack::builder()
            .transition_type(gtk::StackTransitionType::Crossfade)
            .build();
        
        let dashboard_view = DashboardView::new();
        let status_view = StatusView::new();
        let rules_view = RulesView::new();
        let profiles_view = ProfilesView::new();

        stack.add_named(&dashboard_view, Some("dashboard"));
        stack.add_named(&status_view, Some("status"));
        stack.add_named(&rules_view, Some("rules"));
        stack.add_named(&profiles_view, Some("profiles"));

        let content_header = adw::HeaderBar::builder()
            .show_start_title_buttons(false)
            .show_end_title_buttons(true)
            .build();
        content_header.pack_end(&menu_button);

        let content_toolbar = adw::ToolbarView::builder()
            .content(&stack)
            .build();
        content_toolbar.add_top_bar(&content_header);

        // Split view
        let split_view = adw::OverlaySplitView::builder()
            .sidebar(&sidebar_toolbar)
            .content(&content_toolbar)
            .max_sidebar_width(260.0)
            .min_sidebar_width(200.0)
            .build();

        // Connect hamburger menu (shows split view toggle in narrow mode)
        let toggle_button = gtk::ToggleButton::builder()
            .icon_name("sidebar-show-symbolic")
            .build();
        content_header.pack_start(&toggle_button);

        split_view.bind_property("show-sidebar", &toggle_button, "active")
            .sync_create()
            .bidirectional()
            .build();

        // Only show hamburger when collapsed
        split_view.bind_property("collapsed", &toggle_button, "visible")
            .sync_create()
            .build();
        
        // Disable title buttons on sidebar when collapsed
        split_view.bind_property("collapsed", &sidebar_header, "show-start-title-buttons")
            .sync_create()
            .invert_boolean()
            .build();

        // Connect list box to stack
        let stack_clone = stack.clone();
        sidebar_list.connect_row_selected(move |_, row| {
            if let Some(row) = row {
                let index = row.index();
                let name = match index {
                    0 => "dashboard",
                    1 => "status",
                    2 => "rules",
                    3 => "profiles",
                    _ => return,
                };
                stack_clone.set_visible_child_name(name);
            }
        });

        // Set content and save refs
        self.set_content(Some(&split_view));

        *imp.split_view.borrow_mut() = Some(split_view);
        *imp.stack.borrow_mut() = Some(stack);
        *imp.dashboard_view.borrow_mut() = Some(dashboard_view);
        *imp.status_view.borrow_mut() = Some(status_view);
        *imp.rules_view.borrow_mut() = Some(rules_view);
        *imp.profiles_view.borrow_mut() = Some(profiles_view);
        *imp.rules_row.borrow_mut() = Some(rules_row);
        *imp.profiles_row.borrow_mut() = Some(profiles_row);

        // Defer initial refresh until main loop is running (needed for polkit auth dialog)
        let weak = self.downgrade();
        glib::idle_add_local_once(move || {
            if let Some(win) = weak.upgrade() {
                win.refresh_views();
            }
        });
    }

    fn refresh_views(&self) {
        let imp = self.imp();

        // Refresh dashboard data
        if let Some(view) = imp.dashboard_view.borrow().as_ref() {
            view.refresh_data();
        }

        // Read status using backend
        match backend::read_status() {
            Ok(status) => {
                // Show/hide Rules & Profiles sidebar rows based on firewall state
                let active = status.active;
                if let Some(row) = imp.rules_row.borrow().as_ref() { row.set_visible(active); }
                if let Some(row) = imp.profiles_row.borrow().as_ref() { row.set_visible(active); }
                if !active {
                    if let Some(stack) = imp.stack.borrow().as_ref() {
                        if let Some(current) = stack.visible_child_name() {
                            if current == "rules" || current == "profiles" {
                                stack.set_visible_child_name("dashboard");
                            }
                        }
                    }
                }
                if let Some(view) = imp.status_view.borrow().as_ref() {
                    view.update(&status);
                }
                if let Some(view) = imp.rules_view.borrow().as_ref() {
                    view.update(&status);
                }
                if let Some(view) = imp.profiles_view.borrow().as_ref() {
                    // Pass rules for checking allowed profiles
                    view.update_rules(&status.rules);
                }
            }
            Err(e) => {
                show_error(self, &i18n("Error"), &format!("Failed to read firewall status: {}", e));
            }
        }
        
        // Read profiles
        match backend::read_profiles() {
            Ok(profiles) => {
                if let Some(view) = imp.profiles_view.borrow().as_ref() {
                    view.update_profiles(profiles);
                }
            }
            Err(e) => {
                show_error(self, &i18n("Error"), &format!("Failed to read profiles: {}", e));
            }
        }
    }

    fn setup_monitoring(&self) {
        let weak_self = self.downgrade();
        crate::ufw::monitor::UfwMonitor::start(move || {
            if let Some(window) = weak_self.upgrade() {
                window.refresh_views();
            }
        });
    }
}
