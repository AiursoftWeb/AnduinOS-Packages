use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::gio;
use gtk::glib;

use std::cell::RefCell;

use crate::application::SwapcontrolApplication;
use crate::i18n::i18n;
use crate::views::{
    dashboard_view::DashboardView, stress_test_view::StressTestView, swap_view::SwapView,
    zram_view::ZramView,
};

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct SwapcontrolWindow {
        pub stack: RefCell<Option<gtk::Stack>>,
        pub dashboard_view: RefCell<Option<DashboardView>>,
        pub swap_view: RefCell<Option<SwapView>>,
        pub zram_view: RefCell<Option<ZramView>>,
        pub stress_test_view: RefCell<Option<StressTestView>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for SwapcontrolWindow {
        const NAME: &'static str = "SwapcontrolWindow";
        type Type = super::SwapcontrolWindow;
        type ParentType = adw::ApplicationWindow;
    }

    impl ObjectImpl for SwapcontrolWindow {
        fn constructed(&self) {
            self.parent_constructed();
            let obj = self.obj();
            obj.setup_ui();
        }
    }

    impl WidgetImpl for SwapcontrolWindow {}
    impl WindowImpl for SwapcontrolWindow {}
    impl ApplicationWindowImpl for SwapcontrolWindow {}
    impl AdwApplicationWindowImpl for SwapcontrolWindow {}
}

glib::wrapper! {
    pub struct SwapcontrolWindow(ObjectSubclass<imp::SwapcontrolWindow>)
        @extends gtk::Widget, gtk::Window, gtk::ApplicationWindow, adw::ApplicationWindow,
        @implements gio::ActionGroup, gio::ActionMap, gtk::Accessible, gtk::Buildable,
                    gtk::ConstraintTarget, gtk::Native, gtk::Root, gtk::ShortcutManager;
}

impl SwapcontrolWindow {
    pub fn new(app: &SwapcontrolApplication) -> Self {
        glib::Object::builder()
            .property("application", app)
            .property("title", i18n("Swap Control"))
            .property("default-width", 1230)
            .property("default-height", 750)
            .property("icon-name", "com.anduinos.swapcontrol")
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

            let icon = gtk::Image::builder().icon_name(icon_name).build();

            let label = gtk::Label::builder()
                .label(title)
                .halign(gtk::Align::Start)
                .hexpand(true)
                .build();

            box_.append(&icon);
            box_.append(&label);

            gtk::ListBoxRow::builder().child(&box_).build()
        };

        let dashboard_row =
            create_sidebar_row("utilities-system-monitor-symbolic", &i18n("Dashboard"));
        let zram_row = create_sidebar_row("media-flash-symbolic", &i18n("Zram"));
        let swap_row = create_sidebar_row("drive-harddisk-symbolic", &i18n("Swap"));
        let stress_row =
            create_sidebar_row("applications-engineering-symbolic", &i18n("Stress Test"));

        sidebar_list.append(&dashboard_row);
        sidebar_list.append(&zram_row);
        sidebar_list.append(&swap_row);
        sidebar_list.append(&stress_row);
        sidebar_list.select_row(Some(&dashboard_row));

        let sidebar_header = adw::HeaderBar::builder()
            .show_end_title_buttons(false)
            .show_start_title_buttons(true)
            .build();

        let sidebar_toolbar = adw::ToolbarView::builder().content(&sidebar_list).build();
        sidebar_toolbar.add_top_bar(&sidebar_header);

        // Content Setup
        let stack = gtk::Stack::builder()
            .transition_type(gtk::StackTransitionType::Crossfade)
            .build();

        let dashboard_view = DashboardView::new();
        let swap_view = SwapView::new();
        let zram_view = ZramView::new();
        let stress_test_view = StressTestView::new();

        stack.add_named(&dashboard_view, Some("dashboard"));
        stack.add_named(&swap_view, Some("swap"));
        stack.add_named(&zram_view, Some("zram"));
        stack.add_named(&stress_test_view, Some("stress"));

        let content_header = adw::HeaderBar::builder()
            .show_start_title_buttons(false)
            .show_end_title_buttons(true)
            .build();
        content_header.pack_end(&menu_button);

        let content_toolbar = adw::ToolbarView::builder().content(&stack).build();
        content_toolbar.add_top_bar(&content_header);

        // Split view
        let split_view = adw::OverlaySplitView::builder()
            .sidebar(&sidebar_toolbar)
            .content(&content_toolbar)
            .max_sidebar_width(260.0)
            .min_sidebar_width(200.0)
            .build();

        // Connect hamburger menu
        let toggle_button = gtk::ToggleButton::builder()
            .icon_name("sidebar-show-symbolic")
            .build();
        content_header.pack_start(&toggle_button);

        split_view
            .bind_property("show-sidebar", &toggle_button, "active")
            .sync_create()
            .bidirectional()
            .build();

        split_view
            .bind_property("collapsed", &toggle_button, "visible")
            .sync_create()
            .build();

        split_view
            .bind_property("collapsed", &sidebar_header, "show-start-title-buttons")
            .sync_create()
            .invert_boolean()
            .build();

        // Collapse before the sidebar and content exceed their combined natural width,
        // keeping the content centered and the right-side window controls visible.
        let compact = adw::Breakpoint::new(
            adw::BreakpointCondition::parse("max-width: 1050px")
                .expect("the compact window breakpoint must be valid"),
        );
        compact.add_setter(&split_view, "collapsed", Some(&true.to_value()));
        compact.add_setter(&split_view, "show-sidebar", Some(&false.to_value()));
        self.add_breakpoint(compact);

        // Connect list box to stack — switch page and refresh on every tab change
        let stack_clone = stack.clone();
        let weak_for_switch = self.downgrade();
        sidebar_list.connect_row_selected(move |_, row| {
            if let Some(row) = row {
                let index = row.index();
                let name = match index {
                    0 => "dashboard",
                    1 => "zram",
                    2 => "swap",
                    3 => "stress",
                    _ => return,
                };
                stack_clone.set_visible_child_name(name);
                // Refresh the newly visible view
                if let Some(win) = weak_for_switch.upgrade() {
                    win.refresh_views();
                }
            }
        });

        // Set content and save refs
        self.set_content(Some(&split_view));

        *imp.stack.borrow_mut() = Some(stack);
        *imp.dashboard_view.borrow_mut() = Some(dashboard_view);
        *imp.swap_view.borrow_mut() = Some(swap_view);
        *imp.zram_view.borrow_mut() = Some(zram_view);
        *imp.stress_test_view.borrow_mut() = Some(stress_test_view);

        // Defer initial refresh until main loop is running (needed for polkit auth dialog)
        let weak = self.downgrade();
        glib::idle_add_local_once(move || {
            if let Some(win) = weak.upgrade() {
                win.refresh_views();
            }
        });
    }

    pub fn refresh_views(&self) {
        let imp = self.imp();

        let visible = imp
            .stack
            .borrow()
            .as_ref()
            .and_then(|s| s.visible_child_name())
            .map(|n| n.as_str().to_string())
            .unwrap_or_default();
        match visible.as_str() {
            "dashboard" => {
                if let Some(v) = imp.dashboard_view.borrow().as_ref() {
                    v.refresh_data();
                }
            }
            "zram" => {
                if let Some(v) = imp.zram_view.borrow().as_ref() {
                    v.refresh_data();
                }
            }
            "swap" => {
                if let Some(v) = imp.swap_view.borrow().as_ref() {
                    v.refresh_data();
                }
            }
            "stress" => {
                if let Some(v) = imp.stress_test_view.borrow().as_ref() {
                    v.refresh_data();
                }
            }
            _ => {}
        }
    }
}
