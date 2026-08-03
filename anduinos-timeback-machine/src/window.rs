use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;
use std::sync::mpsc;
use std::time::Duration;

use adw::prelude::*;
use gtk::{gdk, gio, glib};

use anduinos_timeback::automation::{
    AutomaticConfiguration, AutomaticPolicy, TargetAutomaticStatus,
};
use anduinos_timeback::layout::{self, LayoutReport, LayoutSupport};
use anduinos_timeback::lineage::{LineageRelation, SystemLineage};
use anduinos_timeback::model::{DeploymentKind, DeploymentRecord, DeploymentState};
use anduinos_timeback::retention::RetentionPlan;
use anduinos_timeback::store::DiscoveryReport;
use anduinos_timeback::targets;
use anduinos_timeback::{client, DEPLOYMENT_SCHEMA_VERSION};

use crate::application::TimebackApplication;
use crate::config;
use crate::i18n::{i18n, i18n_fmt};

const HERO_SVG: &[u8] = include_bytes!("../data/timeback-hero.svg");

struct Page {
    name: &'static str,
    title: String,
    icon: &'static str,
    widget: gtk::Widget,
}

struct TimelineHeading {
    widget: gtk::Box,
    count: gtk::Label,
}

struct DiscoveryState {
    report: DiscoveryReport,
    error: Option<String>,
}

struct RetentionState {
    plan: Option<RetentionPlan>,
    error: Option<String>,
}

struct HistoryState {
    history: Option<SystemLineage>,
    error: Option<String>,
}

struct HomeDiscoveryState {
    snapshots: Vec<anduinos_timeback::automatic_home::HomeSnapshotRecord>,
    error: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ProtectionHealth {
    Active,
    SetupNeeded,
    Attention,
}

struct ProtectionChecklist {
    health: ProtectionHealth,
    system_error: bool,
    system_snapshot: bool,
    home_available: bool,
    home_error: bool,
    home_snapshot: bool,
    system_automatic: bool,
    home_automatic: bool,
    automatic_error: bool,
}

pub fn build(app: &TimebackApplication) -> adw::ApplicationWindow {
    build_with_notice(app, None)
}

fn build_with_notice(
    app: &TimebackApplication,
    success_notice: Option<&str>,
) -> adw::ApplicationWindow {
    let demo = std::env::var_os("ANDUINOS_TIMEBACK_DEMO").is_some();
    let report = Rc::new(if demo {
        demo_layout()
    } else {
        layout::inspect_current()
    });
    let discovery = Rc::new(if demo || !report.is_supported() {
        empty_discovery()
    } else {
        match client::list_deployments() {
            Ok(report) => DiscoveryState {
                report,
                error: None,
            },
            Err(error) => DiscoveryState {
                report: empty_discovery().report,
                error: Some(error.to_string()),
            },
        }
    });
    let retention = if demo || !report.is_supported() {
        RetentionState {
            plan: None,
            error: None,
        }
    } else {
        match client::inspect_retention() {
            Ok(plan) => RetentionState {
                plan: Some(plan),
                error: None,
            },
            Err(error) => RetentionState {
                plan: None,
                error: Some(error.to_string()),
            },
        }
    };
    let automatic = if demo || !report.is_supported() {
        None
    } else {
        client::inspect_automatic().ok()
    };
    let history = if demo || !report.is_supported() {
        HistoryState {
            history: None,
            error: None,
        }
    } else {
        match client::inspect_system_history() {
            Ok(history) => HistoryState {
                history: Some(history),
                error: None,
            },
            Err(error) => HistoryState {
                history: None,
                error: Some(error.to_string()),
            },
        }
    };
    let home_discovery = if demo || !report.is_supported() {
        HomeDiscoveryState {
            snapshots: Vec::new(),
            error: None,
        }
    } else {
        match client::list_home_snapshots() {
            Ok(snapshots) => HomeDiscoveryState {
                snapshots,
                error: None,
            },
            Err(error) => HomeDiscoveryState {
                snapshots: Vec::new(),
                error: Some(error.to_string()),
            },
        }
    };

    let window = adw::ApplicationWindow::builder()
        .application(app)
        .title(i18n("AnduinOS Timeback Machine"))
        .default_width(1040)
        .default_height(720)
        .width_request(390)
        .height_request(560)
        .icon_name(config::APP_ID)
        .build();

    if !demo && report.support == LayoutSupport::OtherFilesystem {
        build_unavailable_window(&window, &report);
        return window;
    }

    let toast_overlay = adw::ToastOverlay::new();
    let pages = vec![
        Page {
            name: "overview",
            title: i18n("Overview"),
            icon: "go-home-symbolic",
            widget: build_overview(
                &window,
                &report,
                &discovery,
                automatic.as_ref(),
                &home_discovery,
                &history,
                demo,
            )
            .upcast(),
        },
        Page {
            name: "history",
            title: i18n("System History"),
            icon: "document-open-recent-symbolic",
            widget: build_system_history(&window, &report, &discovery, &history, demo).upcast(),
        },
        Page {
            name: "files",
            title: i18n("Recover Files"),
            icon: "folder-open-symbolic",
            widget: build_recover_files(&window, &report, &discovery, &home_discovery, demo)
                .upcast(),
        },
        Page {
            name: "automatic",
            title: i18n("Automatic Protection"),
            icon: "alarm-symbolic",
            widget: build_automatic_snapshots(&window, &report, automatic.as_ref()).upcast(),
        },
    ];

    let sidebar = gtk::ListBox::builder()
        .selection_mode(gtk::SelectionMode::Single)
        .css_classes(["navigation-sidebar"])
        .build();
    let stack = gtk::Stack::builder()
        .transition_type(gtk::StackTransitionType::Crossfade)
        .hexpand(true)
        .vexpand(true)
        .build();
    for page in &pages {
        sidebar.append(&navigation_row(page.icon, &page.title));
        stack.add_named(&page.widget, Some(page.name));
    }
    stack.add_named(&build_storage(&report, demo), Some("storage"));
    stack.add_named(&build_activity(&report, &discovery, demo), Some("activity"));
    stack.add_named(&build_settings(&retention, demo), Some("settings"));
    sidebar.select_row(sidebar.row_at_index(0).as_ref());

    let sidebar_header = adw::HeaderBar::builder()
        .show_end_title_buttons(false)
        .build();
    sidebar_header.set_title_widget(Some(&adw::WindowTitle::new(
        &i18n("Timeback Machine"),
        &i18n("System Recovery"),
    )));
    let sidebar_toolbar = adw::ToolbarView::builder().content(&sidebar).build();
    sidebar_toolbar.add_top_bar(&sidebar_header);

    let menu = gio::Menu::new();
    let advanced = gio::Menu::new();
    advanced.append(Some(&i18n("Storage & Retention")), Some("win.storage"));
    advanced.append(Some(&i18n("Activity")), Some("win.activity"));
    advanced.append(Some(&i18n("Advanced Settings")), Some("win.settings"));
    menu.append_section(None, &advanced);
    let help = gio::Menu::new();
    help.append(Some(&i18n("Keyboard Shortcuts")), Some("win.shortcuts"));
    menu.append_section(None, &help);
    let about = gio::Menu::new();
    about.append(Some(&i18n("About Timeback Machine")), Some("app.about"));
    menu.append_section(None, &about);
    let menu_button = gtk::MenuButton::builder()
        .icon_name("open-menu-symbolic")
        .tooltip_text(i18n("Main Menu"))
        .menu_model(&menu)
        .build();
    let refresh_button = gtk::Button::builder()
        .icon_name("view-refresh-symbolic")
        .tooltip_text(i18n("Refresh system status"))
        .action_name("win.refresh")
        .build();

    let header = adw::HeaderBar::new();
    let title = adw::WindowTitle::new(&i18n("Overview"), "");
    header.set_title_widget(Some(&title));
    header.pack_end(&menu_button);
    header.pack_end(&refresh_button);

    let content_toolbar = adw::ToolbarView::builder().content(&stack).build();
    content_toolbar.add_top_bar(&header);
    toast_overlay.set_child(Some(&content_toolbar));

    let split = adw::OverlaySplitView::builder()
        .sidebar(&sidebar_toolbar)
        .content(&toast_overlay)
        .min_sidebar_width(210.0)
        .max_sidebar_width(275.0)
        .build();
    let toggle = gtk::ToggleButton::builder()
        .icon_name("sidebar-show-symbolic")
        .tooltip_text(i18n("Show navigation"))
        .build();
    header.pack_start(&toggle);
    split
        .bind_property("show-sidebar", &toggle, "active")
        .sync_create()
        .bidirectional()
        .build();
    split
        .bind_property("collapsed", &toggle, "visible")
        .sync_create()
        .build();
    split
        .bind_property("collapsed", &sidebar_header, "show-start-title-buttons")
        .sync_create()
        .invert_boolean()
        .build();
    let compact = adw::Breakpoint::new(
        adw::BreakpointCondition::parse("max-width: 700px")
            .expect("the compact window breakpoint must be valid"),
    );
    compact.add_setter(&split, "collapsed", Some(&true.to_value()));
    compact.add_setter(&split, "show-sidebar", Some(&false.to_value()));
    window.add_breakpoint(compact);

    let titles = pages
        .iter()
        .map(|page| (page.name, page.title.clone()))
        .collect::<Vec<_>>();
    let stack_clone = stack.clone();
    let title_clone = title.clone();
    sidebar.connect_row_selected(move |_, row| {
        let Some(row) = row else {
            return;
        };
        let index = row.index() as usize;
        if let Some((name, page_title)) = titles.get(index) {
            stack_clone.set_visible_child_name(name);
            title_clone.set_title(page_title);
        }
    });

    for (action_name, page_name, page_title) in [
        ("overview", "overview", i18n("Overview")),
        ("system-history", "history", i18n("System History")),
        ("recover-files", "files", i18n("Recover Files")),
        ("automatic", "automatic", i18n("Automatic Protection")),
        ("storage", "storage", i18n("Storage & Retention")),
        ("activity", "activity", i18n("Activity")),
        ("settings", "settings", i18n("Advanced Settings")),
    ] {
        let action = gio::SimpleAction::new(action_name, None);
        let stack = stack.clone();
        let sidebar = sidebar.clone();
        let title = title.clone();
        action.connect_activate(move |_, _| {
            stack.set_visible_child_name(page_name);
            title.set_title(&page_title);
            match page_name {
                "overview" => sidebar.select_row(sidebar.row_at_index(0).as_ref()),
                "history" => sidebar.select_row(sidebar.row_at_index(1).as_ref()),
                "files" => sidebar.select_row(sidebar.row_at_index(2).as_ref()),
                "automatic" => sidebar.select_row(sidebar.row_at_index(3).as_ref()),
                _ => sidebar.unselect_all(),
            }
        });
        window.add_action(&action);
    }
    let refresh_action = gio::SimpleAction::new("refresh", None);
    let app_for_refresh = app.clone();
    let window_for_refresh = window.clone();
    refresh_action.connect_activate(move |_, _| {
        let refreshed = build(&app_for_refresh);
        refreshed.present();
        window_for_refresh.close();
    });
    window.add_action(&refresh_action);
    let shortcuts_action = gio::SimpleAction::new("shortcuts", None);
    let window_for_shortcuts = window.clone();
    shortcuts_action.connect_activate(move |_, _| {
        show_keyboard_shortcuts(&window_for_shortcuts);
    });
    window.add_action(&shortcuts_action);
    for (action, accelerators) in [
        ("win.overview", &["<Primary>1"][..]),
        ("win.system-history", &["<Primary>2"][..]),
        ("win.recover-files", &["<Primary>3"][..]),
        ("win.automatic", &["<Primary>4"][..]),
        ("win.refresh", &["F5"][..]),
        ("win.settings", &["<Primary>comma"][..]),
    ] {
        app.set_accels_for_action(action, accelerators);
    }

    window.set_content(Some(&split));
    if let Some(notice) = success_notice {
        toast_overlay.add_toast(adw::Toast::new(&i18n(notice)));
    }
    window
}

fn show_keyboard_shortcuts(parent: &adw::ApplicationWindow) {
    let list = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(8)
        .margin_top(6)
        .build();
    for (title, accelerator) in [
        (i18n("Open Overview"), "<Primary>1"),
        (i18n("Open System History"), "<Primary>2"),
        (i18n("Open Recover Files"), "<Primary>3"),
        (i18n("Open Automatic Protection"), "<Primary>4"),
        (i18n("Refresh System Status"), "F5"),
        (i18n("Open Advanced Settings"), "<Primary>comma"),
    ] {
        let row = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(18)
            .build();
        row.append(
            &gtk::Label::builder()
                .label(title)
                .halign(gtk::Align::Start)
                .xalign(0.0)
                .hexpand(true)
                .build(),
        );
        row.append(
            &gtk::ShortcutLabel::builder()
                .accelerator(accelerator)
                .build(),
        );
        list.append(&row);
    }
    let dialog = adw::AlertDialog::builder()
        .heading(i18n("Keyboard Shortcuts"))
        .extra_child(&list)
        .close_response("close")
        .build();
    dialog.add_response("close", &i18n("Close"));
    dialog.present(Some(parent));
}

fn build_unavailable_window(window: &adw::ApplicationWindow, report: &LayoutReport) {
    window.set_default_size(760, 620);

    let menu = gio::Menu::new();
    menu.append(Some(&i18n("About Timeback Machine")), Some("app.about"));
    let menu_button = gtk::MenuButton::builder()
        .icon_name("open-menu-symbolic")
        .tooltip_text(i18n("Main Menu"))
        .menu_model(&menu)
        .build();
    let header = adw::HeaderBar::new();
    header.set_title_widget(Some(&adw::WindowTitle::new(
        &i18n("Timeback Machine"),
        &i18n("System Recovery"),
    )));
    header.pack_end(&menu_button);

    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(18)
        .halign(gtk::Align::Fill)
        .valign(gtk::Align::Center)
        .vexpand(true)
        .margin_start(28)
        .margin_end(28)
        .margin_top(36)
        .margin_bottom(48)
        .build();
    content.append(
        &gtk::Image::builder()
            .icon_name(config::APP_ID)
            .pixel_size(128)
            .halign(gtk::Align::Center)
            .valign(gtk::Align::Center)
            .build(),
    );
    content.append(
        &gtk::Label::builder()
            .label(i18n("Temporarily unavailable"))
            .css_classes(["title-1"])
            .justify(gtk::Justification::Center)
            .wrap(true)
            .build(),
    );
    let filesystem = report.root_filesystem.as_deref().unwrap_or("unknown");
    content.append(
        &gtk::Label::builder()
            .label(i18n_fmt(
                &i18n(
                    "Timeback Machine currently requires an AnduinOS installation using Btrfs. This system uses {0}.",
                ),
                &[filesystem],
            ))
            .css_classes(["dim-label", "unavailable-description"])
            .justify(gtk::Justification::Center)
            .wrap(true)
            .max_width_chars(58)
            .build(),
    );

    let filesystem_card = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(14)
        .hexpand(true)
        .css_classes(["timeback-card", "unavailable-card"])
        .build();
    filesystem_card.append(
        &gtk::Image::builder()
            .icon_name("drive-harddisk-symbolic")
            .pixel_size(28)
            .css_classes(["accent"])
            .build(),
    );
    let identity = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(2)
        .hexpand(true)
        .build();
    identity.append(
        &gtk::Label::builder()
            .label(i18n("Root filesystem"))
            .css_classes(["heading"])
            .halign(gtk::Align::Start)
            .build(),
    );
    let root_source = report
        .root_source
        .clone()
        .unwrap_or_else(|| i18n("Unknown"));
    identity.append(
        &gtk::Label::builder()
            .label(&root_source)
            .css_classes(["caption", "dim-label"])
            .halign(gtk::Align::Start)
            .build(),
    );
    filesystem_card.append(&identity);
    filesystem_card.append(
        &gtk::Label::builder()
            .label(filesystem)
            .css_classes(["pill", "caption", "filesystem-badge"])
            .valign(gtk::Align::Center)
            .build(),
    );
    content.append(&filesystem_card);

    let unchanged = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(8)
        .halign(gtk::Align::Center)
        .build();
    unchanged.append(
        &gtk::Image::builder()
            .icon_name("security-high-symbolic")
            .css_classes(["success"])
            .build(),
    );
    unchanged.append(
        &gtk::Label::builder()
            .label(i18n("Your files and system have not been changed."))
            .css_classes(["caption", "dim-label"])
            .build(),
    );
    content.append(&unchanged);

    let clamp = adw::Clamp::builder()
        .maximum_size(620)
        .tightening_threshold(480)
        .child(&content)
        .build();
    let toolbar = adw::ToolbarView::builder().content(&clamp).build();
    toolbar.add_top_bar(&header);
    window.set_content(Some(&toolbar));
}

fn build_overview(
    window: &adw::ApplicationWindow,
    report: &LayoutReport,
    discovery: &DiscoveryState,
    automatic: Option<&anduinos_timeback::automation::AutomaticStatus>,
    home: &HomeDiscoveryState,
    history: &HistoryState,
    demo: bool,
) -> gtk::ScrolledWindow {
    let content = page_content();
    let checklist = protection_checklist_state(report, discovery, automatic, home, demo);
    content.append(&overview_hero(window, report, demo, checklist.health));

    if demo {
        let banner = adw::Banner::builder()
            .title(i18n("Design preview — no system changes can be made"))
            .revealed(true)
            .build();
        content.append(&banner);
    } else if discovery.error.is_some() {
        content.append(
            &adw::Banner::builder()
                .title(i18n(
                    "The recovery service could not be reached; no system data was changed",
                ))
                .revealed(true)
                .build(),
        );
    } else if !report.is_supported() && !is_ext4(report) {
        content.append(&unsupported_banner(report));
    }

    if let Some(restore_status) = restore_status_overview(window, discovery, demo) {
        content.append(&restore_status);
    }
    content.append(&protection_checklist(window, &checklist));
    content.append(&current_system_overview(history, demo));
    content.append(&automatic_overview(automatic, demo));

    let supported = report.is_supported() || demo;
    let heading = section_heading(
        &i18n("Recent recovery points"),
        &i18n("A clear history of system changes"),
    );
    content.append(&heading);
    if demo {
        let list = gtk::ListBox::builder()
            .selection_mode(gtk::SelectionMode::None)
            .css_classes(["boxed-list"])
            .build();
        list.append(&recovery_row(
            "software-update-available-symbolic",
            &i18n("After system update"),
            &i18n("Today, 14:32 · Kernel 7.0.0-28"),
            &i18n("Current"),
            "accent",
            false,
        ));
        list.append(&recovery_row(
            "document-revert-symbolic",
            &i18n("Before system update"),
            &i18n("Today, 14:27 · 12 packages changed"),
            &i18n("Protected"),
            "success",
            false,
        ));
        list.append(&recovery_row(
            "starred-symbolic",
            &i18n("Before graphics driver"),
            &i18n("Yesterday, 20:41 · Manual recovery point"),
            &i18n("Pinned"),
            "warning",
            false,
        ));
        content.append(&list);
    } else if !discovery.report.deployments.is_empty() {
        let list = gtk::ListBox::builder()
            .selection_mode(gtk::SelectionMode::None)
            .css_classes(["boxed-list"])
            .build();
        for deployment in discovery.report.deployments.iter().take(3) {
            list.append(&deployment_row(deployment, false));
        }
        content.append(&list);
    } else {
        let empty = adw::StatusPage::builder()
            .icon_name(if supported {
                "document-open-recent-symbolic"
            } else {
                "drive-harddisk-symbolic"
            })
            .title(if supported {
                i18n("No recovery points yet")
            } else if is_ext4(report) {
                i18n("Temporarily unavailable")
            } else {
                i18n("Recovery points are unavailable")
            })
            .description(if supported {
                i18n("Use “Create First Point” in the checklist above to make system recovery ready.")
            } else if is_ext4(report) {
                i18n(
                    "This installation uses ext4. Timeback Machine recovery points currently require Btrfs.",
                )
            } else {
                i18n("Timeback Machine has not changed this installation.")
            })
            .css_classes(["compact-status"])
            .build();
        content.append(&empty);
    }
    wrap_page(content)
}

fn protection_checklist_state(
    report: &LayoutReport,
    discovery: &DiscoveryState,
    automatic: Option<&anduinos_timeback::automation::AutomaticStatus>,
    home: &HomeDiscoveryState,
    demo: bool,
) -> ProtectionChecklist {
    if demo {
        return ProtectionChecklist {
            health: ProtectionHealth::Active,
            system_error: false,
            system_snapshot: true,
            home_available: true,
            home_error: false,
            home_snapshot: true,
            system_automatic: true,
            home_automatic: true,
            automatic_error: false,
        };
    }
    let system_snapshot = discovery.report.deployments.iter().any(|record| {
        record.snapshot_uuid.is_some()
            && !matches!(
                record.state,
                DeploymentState::Creating
                    | DeploymentState::Incomplete
                    | DeploymentState::FailedReverted
                    | DeploymentState::Broken
                    | DeploymentState::Deleting
            )
    });
    let home_available = targets::discover_targets(report)
        .iter()
        .any(|target| target.kind == targets::TargetKind::Home && target.available);
    let home_snapshot = home.snapshots.iter().any(|snapshot| !snapshot.deleting);
    let (system_automatic, home_automatic, automatic_error) = automatic
        .map(|status| {
            (
                status.configuration.system.enabled,
                status.configuration.home.enabled,
                status.system.last_error.is_some() || status.home.last_error.is_some(),
            )
        })
        .unwrap_or((false, false, true));
    let attention = discovery.error.is_some()
        || home.error.is_some()
        || automatic_error
        || !report.is_supported()
        || !home_available;
    ProtectionChecklist {
        health: classify_protection_health(
            attention,
            system_snapshot,
            home_available,
            home_snapshot,
            system_automatic,
            home_automatic,
        ),
        system_error: discovery.error.is_some(),
        system_snapshot,
        home_available,
        home_error: home.error.is_some(),
        home_snapshot,
        system_automatic,
        home_automatic,
        automatic_error,
    }
}

fn classify_protection_health(
    attention: bool,
    system_snapshot: bool,
    home_available: bool,
    home_snapshot: bool,
    system_automatic: bool,
    home_automatic: bool,
) -> ProtectionHealth {
    if attention || !home_available {
        ProtectionHealth::Attention
    } else if !system_snapshot || !system_automatic || !home_snapshot || !home_automatic {
        ProtectionHealth::SetupNeeded
    } else {
        ProtectionHealth::Active
    }
}

fn protection_checklist(
    window: &adw::ApplicationWindow,
    checklist: &ProtectionChecklist,
) -> adw::PreferencesGroup {
    let (title, description) = match checklist.health {
        ProtectionHealth::Active => (
            i18n("Protection Checklist — Complete"),
            i18n("System recovery and earlier personal files are ready when you need them."),
        ),
        ProtectionHealth::SetupNeeded => (
            i18n("Finish Setting Up Protection"),
            i18n("Complete the remaining steps now so recovery is ready before something goes wrong."),
        ),
        ProtectionHealth::Attention => (
            i18n("Protection Needs Attention"),
            i18n("One area cannot protect new data right now. Existing snapshots remain unchanged."),
        ),
    };
    let group = adw::PreferencesGroup::builder()
        .title(title)
        .description(description)
        .build();

    let system_detail = if checklist.system_error {
        i18n("The recovery service could not be reached. Refresh after the service is available again.")
    } else if checklist.system_snapshot {
        i18n("At least one usable recovery point exists.")
    } else {
        i18n("Create your first recovery point before the next system change.")
    };
    let system_badge = if checklist.system_error {
        i18n("Unavailable")
    } else if checklist.system_snapshot {
        i18n("Ready")
    } else {
        i18n("Set Up")
    };
    let system_row = checklist_row(
        "drive-harddisk-symbolic",
        &i18n("System Recovery"),
        &system_detail,
        &system_badge,
        if checklist.system_snapshot {
            "success"
        } else {
            "warning"
        },
    );
    if !checklist.system_snapshot && !checklist.system_error {
        let create = gtk::Button::builder()
            .label(i18n("Create First Point"))
            .icon_name("list-add-symbolic")
            .valign(gtk::Align::Center)
            .css_classes(["suggested-action"])
            .build();
        let parent = window.clone();
        create.connect_clicked(move |_| show_create_dialog(&parent));
        system_row.add_suffix(&create);
    }
    group.add(&system_row);

    let (home_detail, home_badge, home_class) = if checklist.home_error {
        (
            i18n("Personal-file snapshot status could not be loaded. Existing files and snapshots were not changed."),
            i18n("Unavailable"),
            "warning",
        )
    } else if !checklist.home_available {
        (
            i18n(
                "Personal Files requires /home to be an independent Btrfs subvolume; system snapshots do not silently include it.",
            ),
            i18n("Unavailable"),
            "warning",
        )
    } else if checklist.home_snapshot {
        (
            i18n("An earlier copy of your personal files can be browsed and copied out."),
            i18n("Ready"),
            "success",
        )
    } else if checklist.home_automatic {
        (
            i18n(
                "Automatic protection is scheduled and waiting for its first successful snapshot.",
            ),
            i18n("Starting"),
            "accent",
        )
    } else {
        (
            i18n("Turn on Personal Files protection to create browsable earlier copies."),
            i18n("Set Up"),
            "warning",
        )
    };
    let home_row = checklist_row(
        "user-home-symbolic",
        &i18n("Personal Files"),
        &home_detail,
        &home_badge,
        home_class,
    );
    if !checklist.home_error
        && checklist.home_available
        && (!checklist.home_snapshot || !checklist.home_automatic)
    {
        home_row.add_suffix(&checklist_action_button(&i18n("Set Up")));
    }
    group.add(&home_row);

    let (automatic_detail, automatic_badge, automatic_class) = if checklist.automatic_error {
        (
            i18n("The automatic schedule could not be read or its last operation failed."),
            i18n("Check"),
            "warning",
        )
    } else if checklist.system_automatic && (checklist.home_automatic || !checklist.home_available)
    {
        (
            i18n("New snapshots are scheduled without requiring you to remember."),
            i18n("Active"),
            "success",
        )
    } else if checklist.system_automatic || checklist.home_automatic {
        (
            i18n("Automatic protection is enabled for only one data area."),
            i18n("Partial"),
            "warning",
        )
    } else {
        (
            i18n("No new snapshots will be created automatically."),
            i18n("Off"),
            "warning",
        )
    };
    let automatic_row = checklist_row(
        "alarm-symbolic",
        &i18n("Automatic Protection"),
        &automatic_detail,
        &automatic_badge,
        automatic_class,
    );
    if checklist.automatic_error
        || !checklist.system_automatic
        || (checklist.home_available && !checklist.home_automatic)
    {
        automatic_row.add_suffix(&checklist_action_button(&i18n("Review Policy")));
    }
    group.add(&automatic_row);
    group
}

fn checklist_row(
    icon: &str,
    title: &str,
    subtitle: &str,
    badge: &str,
    badge_class: &str,
) -> adw::ActionRow {
    let row = status_row(title, subtitle, badge, badge_class);
    row.add_prefix(&gtk::Image::builder().icon_name(icon).pixel_size(24).build());
    row
}

fn checklist_action_button(label: &str) -> gtk::Button {
    gtk::Button::builder()
        .label(label)
        .icon_name("go-next-symbolic")
        .action_name("win.automatic")
        .valign(gtk::Align::Center)
        .build()
}

fn restore_status_overview(
    window: &adw::ApplicationWindow,
    discovery: &DiscoveryState,
    demo: bool,
) -> Option<adw::PreferencesGroup> {
    let pending = discovery
        .report
        .deployments
        .iter()
        .find(|record| record.state == DeploymentState::PendingRollback);
    let confirming = discovery
        .report
        .deployments
        .iter()
        .find(|record| record.state == DeploymentState::BootedUnconfirmed);
    if pending.is_none() && confirming.is_none() && !demo {
        return None;
    }

    let group = adw::PreferencesGroup::builder()
        .title(i18n("System Restore Status"))
        .build();
    if let Some(confirming) = confirming {
        group.add(&status_row(
            &i18n("Checking the Restored System"),
            &i18n_fmt(
                &i18n(
                    "“{0}” has started. Timeback Machine is confirming its identity before removing the protected previous system.",
                ),
                &[&confirming.title],
            ),
            &i18n("Safety Check"),
            "warning",
        ));
        return Some(group);
    }

    let target_title = pending
        .map(|record| record.title.clone())
        .unwrap_or_else(|| i18n("Before system update"));
    let card = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(10)
        .css_classes(["timeback-card", "restore-pending-card"])
        .build();
    let heading = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(10)
        .build();
    heading.append(
        &gtk::Image::builder()
            .icon_name("document-revert-symbolic")
            .pixel_size(28)
            .css_classes(["warning"])
            .build(),
    );
    let copy = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(3)
        .hexpand(true)
        .build();
    copy.append(
        &gtk::Label::builder()
            .label(i18n("System Restore Is Prepared"))
            .halign(gtk::Align::Start)
            .xalign(0.0)
            .wrap(true)
            .css_classes(["title-4"])
            .build(),
    );
    copy.append(
        &gtk::Label::builder()
            .label(i18n_fmt(
                &i18n(
                    "Target: “{0}” · Your running system has not changed. The recovery entry is selected for one boot only.",
                ),
                &[&target_title],
            ))
            .halign(gtk::Align::Start)
            .xalign(0.0)
            .wrap(true)
            .css_classes(["dim-label"])
            .build(),
    );
    heading.append(&copy);
    card.append(&heading);

    let actions = gtk::FlowBox::builder()
        .selection_mode(gtk::SelectionMode::None)
        .min_children_per_line(1)
        .max_children_per_line(2)
        .column_spacing(8)
        .row_spacing(8)
        .build();
    let explain = gtk::Button::builder()
        .label(i18n("What Happens Next?"))
        .icon_name("help-about-symbolic")
        .build();
    let parent = window.clone();
    let target_for_explanation = target_title.clone();
    explain.connect_clicked(move |_| {
        show_pending_restore_explanation(&parent, &target_for_explanation);
    });
    let cancel = gtk::Button::builder()
        .label(i18n("Cancel Prepared Restore"))
        .icon_name("process-stop-symbolic")
        .css_classes(["destructive-action"])
        .build();
    let parent = window.clone();
    cancel.connect_clicked(move |_| {
        if demo {
            show_history_demo_action(&parent, &i18n("Cancel Prepared Restore"));
        } else {
            run_ui_mutation(
                &parent,
                &i18n("Cancelling System Restore"),
                UiMutation::CancelRestore,
            );
        }
    });
    actions.insert(&explain, -1);
    actions.insert(&cancel, -1);
    card.append(&actions);
    group.add(&card);
    Some(group)
}

fn show_pending_restore_explanation(parent: &adw::ApplicationWindow, target: &str) {
    let steps = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(10)
        .margin_top(6)
        .build();
    for (icon, title, detail) in [
        (
            "security-high-symbolic",
            i18n("Your Current System Is Protected"),
            i18n("A safety recovery point was created before anything was scheduled."),
        ),
        (
            "media-playback-start-symbolic",
            i18n("The Recovery Entry Runs Once"),
            i18n_fmt(
                &i18n("The next selected recovery boot tries “{0}”. It does not permanently replace the normal GRUB default."),
                &[target],
            ),
        ),
        (
            "emblem-ok-symbolic",
            i18n("Success Creates a New Branch"),
            i18n("After a verified boot, your new changes continue from the restored point."),
        ),
        (
            "edit-undo-symbolic",
            i18n("Failure Returns Automatically"),
            i18n("If recovery cannot finish safely, the protected previous system returns on the following boot."),
        ),
        (
            "system-reboot-symbolic",
            i18n("Changed Your Mind at GRUB?"),
            i18n("Choose a normal AnduinOS entry to skip recovery. After logging in, cancel the pending request here before preparing another restore."),
        ),
    ] {
        let row = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(10)
            .build();
        row.append(
            &gtk::Image::builder()
                .icon_name(icon)
                .pixel_size(20)
                .css_classes(["accent"])
                .build(),
        );
        let text = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(2)
            .hexpand(true)
            .build();
        text.append(
            &gtk::Label::builder()
                .label(title)
                .halign(gtk::Align::Start)
                .xalign(0.0)
                .wrap(true)
                .css_classes(["heading"])
                .build(),
        );
        text.append(
            &gtk::Label::builder()
                .label(detail)
                .halign(gtk::Align::Start)
                .xalign(0.0)
                .wrap(true)
                .css_classes(["caption", "dim-label"])
                .build(),
        );
        row.append(&text);
        steps.append(&row);
    }
    let dialog = adw::AlertDialog::builder()
        .heading(i18n("What Happens After Restart"))
        .body(i18n(
            "Before restarting, you can cancel from this page. The normal AnduinOS entries also remain available in the GRUB menu.",
        ))
        .extra_child(&steps)
        .close_response("close")
        .build();
    dialog.add_response("close", &i18n("Got It"));
    dialog.present(Some(parent));
}

fn current_system_overview(history: &HistoryState, demo: bool) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::builder()
        .title(i18n("Current System"))
        .description(i18n(
            "This is the system state that will continue when you restart normally.",
        ))
        .build();
    let (subtitle, state, class) = if demo {
        (
            i18n("Developed from “After system update” · Your newer changes are still here"),
            i18n("You Are Here"),
            "success",
        )
    } else if let Some(history) = &history.history {
        if let Some(head) = history.current_head_id.and_then(|id| {
            history
                .nodes
                .iter()
                .find(|node| node.recovery_point_id == id)
        }) {
            (
                i18n_fmt(
                    &i18n("Developed from “{0}” · View its branch in System History"),
                    &[&head.title],
                ),
                i18n("You Are Here"),
                "success",
            )
        } else {
            (
                i18n("The current branch will be recorded with your next recovery point."),
                i18n("Current"),
                "accent",
            )
        }
    } else if history.error.is_some() {
        (
            i18n("The system branch could not be loaded. No changes were made."),
            i18n("Unavailable"),
            "warning",
        )
    } else {
        (
            i18n("Create a recovery point to begin a visible system history."),
            i18n("Current"),
            "accent",
        )
    };
    let row = status_row(
        &i18n("Current System — You Are Here"),
        &subtitle,
        &state,
        class,
    );
    row.set_activatable(true);
    row.set_action_name(Some("win.system-history"));
    group.add(&row);
    group
}

fn automatic_overview(
    status: Option<&anduinos_timeback::automation::AutomaticStatus>,
    demo: bool,
) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::builder()
        .title(i18n("Snapshot Timing"))
        .description(i18n(
            "The last successful snapshot and the next planned run for each protected area.",
        ))
        .build();
    if demo {
        group.add(&automatic_overview_row(
            &i18n("System and User Data"),
            &i18n("Today, 14:00"),
            &i18n("Today, 16:00"),
            true,
        ));
        return group;
    }
    let Some(status) = status else {
        group.add(&status_row(
            &i18n("Automatic snapshots"),
            &i18n("Automatic snapshot status could not be loaded."),
            &i18n("Unavailable"),
            "warning",
        ));
        return group;
    };
    if status.configuration.policies_linked {
        let last = match (status.system.last_success, status.home.last_success) {
            (Some(system), Some(home)) => Some(system.min(home)),
            _ => None,
        };
        let next = match (status.system.next_run, status.home.next_run) {
            (Some(system), Some(home)) => Some(system.min(home)),
            _ => None,
        };
        group.add(&automatic_overview_row(
            &i18n("System and User Data"),
            &automatic_time(last, &i18n("No complete shared snapshot yet")),
            &automatic_time(next, &i18n("Not scheduled")),
            status.configuration.system.enabled,
        ));
    } else {
        group.add(&automatic_overview_row(
            &i18n("System"),
            &automatic_time(status.system.last_success, &i18n("Never")),
            &automatic_time(status.system.next_run, &i18n("Not scheduled")),
            status.configuration.system.enabled,
        ));
        group.add(&automatic_overview_row(
            &i18n("User Data"),
            &automatic_time(status.home.last_success, &i18n("Never")),
            &automatic_time(status.home.next_run, &i18n("Not scheduled")),
            status.configuration.home.enabled,
        ));
    }
    group
}

fn automatic_overview_row(
    title: &str,
    last_snapshot: &str,
    next_snapshot: &str,
    enabled: bool,
) -> adw::ActionRow {
    let subtitle = i18n_fmt(
        &i18n("Last snapshot: {0} · Next snapshot: {1}"),
        &[last_snapshot, next_snapshot],
    );
    let state = if enabled { i18n("Active") } else { i18n("Off") };
    status_row(
        title,
        &subtitle,
        &state,
        if enabled { "success" } else { "warning" },
    )
}

fn automatic_time(time: Option<chrono::DateTime<chrono::Utc>>, missing: &str) -> String {
    time.map(|time| {
        time.with_timezone(&chrono::Local)
            .format("%Y-%m-%d %H:%M")
            .to_string()
    })
    .unwrap_or_else(|| missing.to_string())
}

fn overview_hero(
    window: &adw::ApplicationWindow,
    report: &LayoutReport,
    demo: bool,
    health: ProtectionHealth,
) -> gtk::FlowBox {
    let supported = report.is_supported() || demo;
    let (hero_title, hero_description) = if supported {
        match health {
            ProtectionHealth::Active => (
                i18n("Protection Is Active"),
                i18n("System recovery and earlier personal files are ready when you need them."),
            ),
            ProtectionHealth::SetupNeeded => (
                i18n("Finish Setting Up Protection"),
                i18n("A few clear steps remain before automatic recovery is fully ready."),
            ),
            ProtectionHealth::Attention => (
                i18n("Protection Needs Attention"),
                i18n("Your existing data is untouched. Review the checklist below to fix the unavailable area."),
            ),
        }
    } else if is_ext4(report) {
        (
            i18n("Temporarily unavailable"),
            i18n("This AnduinOS installation uses ext4. Timeback Machine recovery points currently require Btrfs."),
        )
    } else {
        (
            i18n("System recovery is unavailable"),
            i18n("This installation does not use the complete AnduinOS Btrfs recovery layout."),
        )
    };
    let hero = gtk::FlowBox::builder()
        .selection_mode(gtk::SelectionMode::None)
        .min_children_per_line(1)
        .max_children_per_line(2)
        .column_spacing(24)
        .row_spacing(18)
        .homogeneous(false)
        .css_classes(["timeback-hero"])
        .build();
    let emblem = gtk::Box::builder()
        .width_request(92)
        .height_request(92)
        .halign(gtk::Align::Center)
        .valign(gtk::Align::Center)
        .css_classes(["timeback-emblem"])
        .build();
    if supported {
        emblem.append(&svg_picture(HERO_SVG, 58, 58));
    } else {
        emblem.append(
            &gtk::Image::builder()
                .icon_name("drive-harddisk-symbolic")
                .pixel_size(48)
                .build(),
        );
    }
    hero.insert(&emblem, -1);

    let copy = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(8)
        .width_request(260)
        .hexpand(true)
        .valign(gtk::Align::Center)
        .build();
    copy.append(
        &gtk::Label::builder()
            .label(hero_title)
            .css_classes(["title-1", "hero-title"])
            .halign(gtk::Align::Start)
            .wrap(true)
            .xalign(0.0)
            .build(),
    );
    copy.append(
        &gtk::Label::builder()
            .label(hero_description)
            .css_classes(["hero-subtitle"])
            .halign(gtk::Align::Start)
            .wrap(true)
            .xalign(0.0)
            .build(),
    );
    let action_content = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(8)
        .build();
    action_content.append(
        &gtk::Image::builder()
            .icon_name("list-add-symbolic")
            .pixel_size(18)
            .build(),
    );
    action_content.append(
        &gtk::Label::builder()
            .label(i18n("Create Recovery Point"))
            .build(),
    );
    let action = gtk::Button::builder()
        .child(&action_content)
        .css_classes(["suggested-action", "pill", "hero-action"])
        .halign(gtk::Align::Start)
        .margin_top(8)
        .valign(gtk::Align::Center)
        .sensitive(supported)
        .visible(supported)
        .build();
    let parent = window.clone();
    action.connect_clicked(move |_| {
        if demo {
            let dialog = adw::AlertDialog::builder()
                .heading(i18n("Design preview"))
                .body(i18n(
                    "No snapshot can be created while demo data is active.",
                ))
                .close_response("close")
                .build();
            dialog.add_response("close", &i18n("Close"));
            dialog.present(Some(&parent));
        } else {
            show_create_dialog(&parent);
        }
    });
    let actions = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(8)
        .margin_top(8)
        .build();
    action.set_margin_top(0);
    actions.append(&action);
    actions.append(
        &gtk::Button::builder()
            .label(i18n("Find a File"))
            .icon_name("folder-open-symbolic")
            .action_name("win.recover-files")
            .css_classes(["pill"])
            .visible(supported)
            .build(),
    );
    copy.append(&actions);
    hero.insert(&copy, -1);
    hero
}

fn unsupported_banner(report: &LayoutReport) -> adw::Banner {
    let title = match report.support {
        LayoutSupport::OtherFilesystem if is_ext4(report) => {
            i18n("Timeback Machine is temporarily unavailable on this ext4 installation")
        }
        LayoutSupport::OtherFilesystem => i18n_fmt(
            &i18n("Timeback Machine requires Btrfs; this system uses {0}"),
            &[report.root_filesystem.as_deref().unwrap_or("unknown")],
        ),
        LayoutSupport::IncompatibleBtrfs => {
            i18n("Btrfs detected, but the AnduinOS recovery layout is incomplete")
        }
        LayoutSupport::Unavailable => i18n("The system storage layout could not be inspected"),
        LayoutSupport::Supported => i18n("Btrfs recovery is available"),
    };
    adw::Banner::builder().title(title).revealed(true).build()
}

fn build_system_history(
    window: &adw::ApplicationWindow,
    report: &LayoutReport,
    discovery: &DiscoveryState,
    history: &HistoryState,
    demo: bool,
) -> gtk::ScrolledWindow {
    let content = page_content();
    content.append(&section_heading(
        &i18n("System History"),
        &i18n("See which system state you are using and where each recovery path began"),
    ));
    if !(report.is_supported() || demo) {
        content.append(
            &adw::StatusPage::builder()
                .icon_name("drive-harddisk-symbolic")
                .title(if is_ext4(report) {
                    i18n("Temporarily unavailable")
                } else {
                    i18n("A compatible Btrfs layout is required")
                })
                .description(if is_ext4(report) {
                    i18n(
                        "This installation uses ext4. Nothing is wrong with your system, but recovery points currently require Btrfs.",
                    )
                } else {
                    i18n("Personal files and disks will not be modified by this application.")
                })
                .build(),
        );
        return wrap_page(content);
    }

    content.append(&system_branch_map(window, discovery, history, demo));

    if demo {
        let today_heading = timeline_heading(&i18n("Today"), 3);
        let today_list = gtk::ListBox::builder()
            .selection_mode(gtk::SelectionMode::None)
            .css_classes(["boxed-list", "timeline-list"])
            .build();
        let yesterday_heading = timeline_heading(&i18n("Yesterday"), 1);
        let yesterday_list = gtk::ListBox::builder()
            .selection_mode(gtk::SelectionMode::None)
            .css_classes(["boxed-list", "timeline-list"])
            .build();

        let points = Rc::new(vec![
            (
                demo_recovery_row(
                    window,
                    "software-update-available-symbolic",
                    &i18n("After system update"),
                    &i18n("14:32 · Current system · Kernel 7.0.0-28"),
                    &i18n("Current"),
                    "accent",
                ),
                true,
                true,
                false,
            ),
            (
                demo_recovery_row(
                    window,
                    "document-revert-symbolic",
                    &i18n("Before system update"),
                    &i18n("14:27 · Automatic · 12 packages changed"),
                    &i18n("Ready"),
                    "success",
                ),
                true,
                true,
                false,
            ),
            (
                demo_recovery_row(
                    window,
                    "camera-photo-symbolic",
                    &i18n("Morning checkpoint"),
                    &i18n("09:10 · Manual recovery point"),
                    &i18n("Ready"),
                    "success",
                ),
                true,
                false,
                false,
            ),
            (
                demo_recovery_row(
                    window,
                    "starred-symbolic",
                    &i18n("Before graphics driver"),
                    &i18n("20:41 · Manual · Kernel 7.0.0-27"),
                    &i18n("Pinned"),
                    "warning",
                ),
                false,
                false,
                true,
            ),
        ]);
        for (row, today, _, _) in points.iter() {
            if *today {
                today_list.append(row);
            } else {
                yesterday_list.append(row);
            }
        }

        let filters = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(8)
            .build();
        let mut first_button: Option<gtk::ToggleButton> = None;
        for (index, title) in [
            i18n("All"),
            i18n("Automatic"),
            i18n("Manual"),
            i18n("Pinned"),
        ]
        .into_iter()
        .enumerate()
        {
            let button = gtk::ToggleButton::builder()
                .label(title)
                .css_classes(["pill"])
                .active(index == 0)
                .build();
            if let Some(first) = &first_button {
                button.set_group(Some(first));
            } else {
                first_button = Some(button.clone());
            }
            let points = points.clone();
            let today_group = today_heading.widget.clone();
            let today_count = today_heading.count.clone();
            let today_list_clone = today_list.clone();
            let yesterday_group = yesterday_heading.widget.clone();
            let yesterday_count = yesterday_heading.count.clone();
            let yesterday_list_clone = yesterday_list.clone();
            button.connect_toggled(move |button| {
                if !button.is_active() {
                    return;
                }
                let mut today_visible = 0;
                let mut yesterday_visible = 0;
                for (row, today, automatic, pinned) in points.iter() {
                    let visible = match index {
                        1 => *automatic,
                        2 => !*automatic,
                        3 => *pinned,
                        _ => true,
                    };
                    row.set_visible(visible);
                    if visible && *today {
                        today_visible += 1;
                    } else if visible {
                        yesterday_visible += 1;
                    }
                }
                today_count.set_label(&today_visible.to_string());
                today_group.set_visible(today_visible > 0);
                today_list_clone.set_visible(today_visible > 0);
                yesterday_count.set_label(&yesterday_visible.to_string());
                yesterday_group.set_visible(yesterday_visible > 0);
                yesterday_list_clone.set_visible(yesterday_visible > 0);
            });
            filters.append(&button);
        }

        content.append(&filters);
        content.append(&today_heading.widget);
        content.append(&today_list);
        content.append(&yesterday_heading.widget);
        content.append(&yesterday_list);
    } else if let Some(error) = &discovery.error {
        content.append(
            &adw::StatusPage::builder()
                .icon_name("network-error-symbolic")
                .title(i18n("The recovery service is unavailable"))
                .description(error)
                .build(),
        );
    } else if !discovery.report.deployments.is_empty() {
        let heading = timeline_heading(
            &i18n("Deployment metadata"),
            discovery.report.deployments.len(),
        );
        content.append(&heading.widget);
        let list = gtk::ListBox::builder()
            .selection_mode(gtk::SelectionMode::None)
            .css_classes(["boxed-list", "timeline-list"])
            .build();
        for deployment in &discovery.report.deployments {
            list.append(&interactive_deployment_row(window, deployment));
        }
        content.append(&list);
    } else {
        let create = gtk::Button::builder()
            .label(i18n("Create First Recovery Point"))
            .icon_name("list-add-symbolic")
            .halign(gtk::Align::Center)
            .css_classes(["suggested-action", "pill"])
            .build();
        let parent = window.clone();
        create.connect_clicked(move |_| show_create_dialog(&parent));
        content.append(
            &adw::StatusPage::builder()
                .icon_name("document-open-recent-symbolic")
                .title(i18n("Create Your First Recovery Point"))
                .description(i18n(
                    "Create a recovery point before a system change so you can return to a known state.",
                ))
                .child(&create)
                .build(),
        );
    }
    wrap_page(content)
}

fn system_branch_map(
    window: &adw::ApplicationWindow,
    discovery: &DiscoveryState,
    history: &HistoryState,
    demo: bool,
) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::builder()
        .title(i18n("System Branch Map"))
        .description(i18n(
            "Time flows downward. Connected points have a verified parent relationship; a fork is another path you can still return to.",
        ))
        .build();
    if demo {
        let (details, on_select) = history_action_panel(window, discovery, true);
        group.add(&crate::history_graph::build_demo(on_select));
        group.add(&details);
        return group;
    }
    let Some(lineage) = &history.history else {
        group.add(&status_row(
            &i18n("System branch unavailable"),
            history
                .error
                .as_deref()
                .unwrap_or(&i18n("Create a recovery point to begin the branch map.")),
            &i18n("No Changes Made"),
            "warning",
        ));
        return group;
    };
    let (details, on_select) = history_action_panel(window, discovery, false);
    group.add(&crate::history_graph::build(lineage, on_select));
    group.add(&details);

    let mut legacy = lineage
        .nodes
        .iter()
        .filter(|node| node.relation == LineageRelation::LegacyUnknown)
        .collect::<Vec<_>>();
    legacy.sort_by_key(|node| std::cmp::Reverse(node.created_at));
    if !legacy.is_empty() {
        group.add(
            &gtk::Label::builder()
                .label(i18n("Older Points — Relationship Unknown"))
                .halign(gtk::Align::Start)
                .xalign(0.0)
                .margin_top(8)
                .css_classes(["heading"])
                .build(),
        );
        group.add(
            &gtk::Label::builder()
                .label(i18n(
                    "These points predate branch tracking. They remain usable, but Timeback Machine will not guess where to connect them.",
                ))
                .halign(gtk::Align::Start)
                .xalign(0.0)
                .wrap(true)
                .css_classes(["caption", "dim-label"])
                .build(),
        );
        for node in legacy.iter().take(12) {
            let time = node
                .created_at
                .with_timezone(&chrono::Local)
                .format("%Y-%m-%d %H:%M")
                .to_string();
            let badge = if node.snapshot_available {
                i18n("Available")
            } else {
                i18n("History Only")
            };
            group.add(&status_row(
                &node.title,
                &time,
                &badge,
                if node.snapshot_available {
                    "accent"
                } else {
                    ""
                },
            ));
        }
        if legacy.len() > 12 {
            group.add(&status_row(
                &i18n("More older points"),
                &i18n_fmt(
                    &i18n("{0} additional points remain available in the timeline below."),
                    &[&(legacy.len() - 12).to_string()],
                ),
                &i18n("Timeline"),
                "",
            ));
        }
    }
    group
}

fn history_action_panel(
    window: &adw::ApplicationWindow,
    discovery: &DiscoveryState,
    demo: bool,
) -> (
    gtk::Box,
    impl Fn(crate::history_graph::HistorySelection) + 'static,
) {
    let panel = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(10)
        .css_classes(["timeback-card", "history-actions"])
        .build();
    let title = gtk::Label::builder()
        .label(i18n("Select a Point"))
        .halign(gtk::Align::Start)
        .xalign(0.0)
        .wrap(true)
        .css_classes(["title-4"])
        .build();
    let description = gtk::Label::builder()
        .label(i18n(
            "Choose a card above to see what you can safely do with that moment.",
        ))
        .halign(gtk::Align::Start)
        .xalign(0.0)
        .wrap(true)
        .css_classes(["dim-label"])
        .build();
    panel.append(&title);
    panel.append(&description);

    let actions = gtk::FlowBox::builder()
        .selection_mode(gtk::SelectionMode::None)
        .min_children_per_line(1)
        .max_children_per_line(3)
        .column_spacing(8)
        .row_spacing(8)
        .build();
    let browse = gtk::Button::builder()
        .label(i18n("Browse Files"))
        .icon_name("folder-open-symbolic")
        .tooltip_text(i18n("Open this recovery point without changing the system"))
        .sensitive(false)
        .build();
    let verify = gtk::Button::builder()
        .label(i18n("Verify"))
        .icon_name("security-high-symbolic")
        .tooltip_text(i18n(
            "Check that this recovery point is complete and unchanged",
        ))
        .sensitive(false)
        .build();
    let restore = gtk::Button::builder()
        .label(i18n("Prepare Restore"))
        .icon_name("document-revert-symbolic")
        .tooltip_text(i18n(
            "Review what changes before preparing a one-time restore",
        ))
        .css_classes(["suggested-action"])
        .sensitive(false)
        .build();
    actions.insert(&browse, -1);
    actions.insert(&verify, -1);
    actions.insert(&restore, -1);
    panel.append(&actions);

    type SelectedPoint = (
        crate::history_graph::HistorySelection,
        Option<DeploymentRecord>,
    );
    let selected = Rc::new(RefCell::new(None::<SelectedPoint>));
    let records = discovery
        .report
        .deployments
        .iter()
        .cloned()
        .map(|record| (record.id, record))
        .collect::<HashMap<_, _>>();

    {
        let selected = selected.clone();
        let parent = window.clone();
        browse.connect_clicked(move |_| {
            let selected = selected.borrow();
            let Some((selection, deployment)) = selected.as_ref() else {
                return;
            };
            if demo {
                show_history_demo_action(&parent, &i18n("Browse Files"));
            } else if let Some(deployment) = deployment {
                crate::snapshot_browser::present(
                    &parent,
                    "system",
                    &deployment.id.to_string(),
                    &selection.title,
                );
            }
        });
    }
    {
        let selected = selected.clone();
        let parent = window.clone();
        verify.connect_clicked(move |_| {
            let selected = selected.borrow();
            let Some((_, deployment)) = selected.as_ref() else {
                return;
            };
            if demo {
                show_history_demo_action(&parent, &i18n("Verify Recovery Point"));
            } else if let Some(deployment) = deployment {
                run_ui_mutation(
                    &parent,
                    &i18n("Verifying Recovery Point"),
                    UiMutation::Verify {
                        deployment_id: deployment.id.to_string(),
                    },
                );
            }
        });
    }
    {
        let selected = selected.clone();
        let parent = window.clone();
        restore.connect_clicked(move |_| {
            let selected = selected.borrow();
            let Some((selection, deployment)) = selected.as_ref() else {
                return;
            };
            if demo {
                show_history_demo_action(&parent, &i18n("Prepare System Restore"));
            } else if let Some(deployment) = deployment {
                if deployment.state == DeploymentState::PendingRollback {
                    run_ui_mutation(
                        &parent,
                        &i18n("Cancelling System Restore"),
                        UiMutation::CancelRestore,
                    );
                } else {
                    show_restore_dialog(&parent, &deployment.id.to_string(), &selection.title);
                }
            }
        });
    }

    let callback = move |selection: crate::history_graph::HistorySelection| {
        let deployment = selection
            .recovery_point_id
            .and_then(|id| records.get(&id).cloned());
        title.set_label(&selection.title);
        if selection.current {
            description.set_label(&i18n(
                "You are using this system now. A normal restart continues here, including changes made after the latest recovery point.",
            ));
            browse.set_sensitive(false);
            verify.set_sensitive(false);
            restore.set_sensitive(false);
            restore.set_label(&i18n("Prepare Restore"));
        } else if demo {
            description.set_label(&i18n(
                "This recovery point can be browsed, verified, or prepared for restore. Demo mode never changes the system.",
            ));
            browse.set_sensitive(selection.available);
            verify.set_sensitive(selection.available);
            restore.set_sensitive(selection.available);
            restore.set_label(&i18n("Prepare Restore"));
        } else if let Some(record) = &deployment {
            let browse_available = record.snapshot_uuid.is_some()
                && record.state != DeploymentState::Creating
                && record.state != DeploymentState::Deleting;
            let restore_available = record.can_restore();
            let explanation = if record.state == DeploymentState::PendingRollback {
                i18n(
                    "This restore is prepared for the next restart. You can still cancel it without changing the running system.",
                )
            } else if restore_available {
                i18n(
                    "Browsing and verification do not change anything. Restore only prepares a one-time boot and explains every affected area first.",
                )
            } else if browse_available {
                i18n(
                    "You can still copy files from this point, but its current state is not eligible for a full system restore.",
                )
            } else {
                i18n(
                    "The snapshot data is no longer available. Its card remains only to explain system history.",
                )
            };
            description.set_label(&explanation);
            browse.set_sensitive(browse_available);
            verify.set_sensitive(restore_available);
            restore.set_sensitive(
                restore_available || record.state == DeploymentState::PendingRollback,
            );
            let restore_label = if record.state == DeploymentState::PendingRollback {
                i18n("Cancel Prepared Restore")
            } else {
                i18n("Prepare Restore")
            };
            restore.set_label(&restore_label);
        } else {
            description.set_label(&i18n(
                "This point remains in the branch record, but its snapshot is no longer available.",
            ));
            browse.set_sensitive(false);
            verify.set_sensitive(false);
            restore.set_sensitive(false);
            restore.set_label(&i18n("Prepare Restore"));
        }
        selected.replace(Some((selection, deployment)));
    };
    (panel, callback)
}

fn show_history_demo_action(parent: &adw::ApplicationWindow, action: &str) {
    let dialog = adw::AlertDialog::builder()
        .heading(action)
        .body(i18n(
            "This action is available here on a supported system. Design preview never reads or changes snapshots.",
        ))
        .close_response("close")
        .build();
    dialog.add_response("close", &i18n("Close"));
    dialog.present(Some(parent));
}

fn build_recover_files(
    window: &adw::ApplicationWindow,
    report: &LayoutReport,
    discovery: &DiscoveryState,
    home: &HomeDiscoveryState,
    demo: bool,
) -> gtk::ScrolledWindow {
    let content = page_content();
    content.append(&section_heading(
        &i18n("Recover Files"),
        &i18n("Open an earlier moment like a normal folder, then copy out only what you need"),
    ));
    content.append(
        &adw::Banner::builder()
            .title(i18n(
                "Browsing is read-only. Copying a file out does not change the snapshot or roll back your system.",
            ))
            .revealed(true)
            .build(),
    );
    if !(report.is_supported() || demo) {
        content.append(
            &adw::StatusPage::builder()
                .icon_name("folder-open-symbolic")
                .title(i18n("Earlier files are unavailable"))
                .description(i18n(
                    "A compatible AnduinOS Btrfs layout is required. No files have been changed.",
                ))
                .build(),
        );
        return wrap_page(content);
    }

    let user_group = adw::PreferencesGroup::builder()
        .title(i18n("Personal Files"))
        .description(i18n(
            "Choose when the file still existed. The snapshot opens at your home folder.",
        ))
        .build();
    if demo {
        user_group.add(&demo_file_snapshot_row(
            window,
            &i18n("Today, 15:00"),
            &i18n("Automatic · Personal files"),
            "user-home-symbolic",
        ));
        user_group.add(&demo_file_snapshot_row(
            window,
            &i18n("Today, 14:00"),
            &i18n("Automatic · Personal files"),
            "user-home-symbolic",
        ));
    } else if !home.snapshots.is_empty() {
        for snapshot in home.snapshots.iter().rev() {
            let title = snapshot
                .created_at
                .with_timezone(&chrono::Local)
                .format("%Y-%m-%d %H:%M")
                .to_string();
            user_group.add(&file_snapshot_row(
                window,
                &title,
                &i18n("Automatic · Personal files"),
                "user-home-symbolic",
                "home",
                &snapshot.id.to_string(),
                &i18n("Personal Files Snapshot"),
            ));
        }
    } else {
        let state = if home.error.is_some() {
            i18n("Unavailable")
        } else {
            i18n("Waiting")
        };
        let row = status_row(
            &i18n("No personal-file snapshots yet"),
            home.error
                .as_deref()
                .unwrap_or("Automatic Protection can create these for you."),
            &state,
            if home.error.is_some() { "warning" } else { "" },
        );
        if home.error.is_none() {
            row.add_suffix(&checklist_action_button(&i18n("Set Up")));
        }
        user_group.add(&row);
    }
    content.append(&user_group);

    let system_group = adw::PreferencesGroup::builder()
        .title(i18n("System Files"))
        .description(i18n(
            "Use this for an earlier configuration or system file. This does not restore the whole system.",
        ))
        .build();
    if demo {
        system_group.add(&demo_file_snapshot_row(
            window,
            &i18n("After system update"),
            &i18n("Today, 14:32 · Automatic system recovery point"),
            "drive-harddisk-symbolic",
        ));
        system_group.add(&demo_file_snapshot_row(
            window,
            &i18n("Before system update"),
            &i18n("Today, 14:27 · Automatic system recovery point"),
            "drive-harddisk-symbolic",
        ));
    } else {
        let snapshots = discovery
            .report
            .deployments
            .iter()
            .filter(|deployment| {
                deployment.snapshot_uuid.is_some()
                    && deployment.state != DeploymentState::Creating
                    && deployment.state != DeploymentState::Deleting
            })
            .collect::<Vec<_>>();
        if snapshots.is_empty() {
            let state = if discovery.error.is_some() {
                i18n("Unavailable")
            } else {
                i18n("Waiting")
            };
            let row = status_row(
                &i18n("No system snapshots available"),
                discovery.error.as_deref().unwrap_or(
                    "Create a recovery point before a system change to browse its files later.",
                ),
                &state,
                if discovery.error.is_some() {
                    "warning"
                } else {
                    ""
                },
            );
            if discovery.error.is_none() {
                let create = gtk::Button::builder()
                    .label(i18n("Create Recovery Point"))
                    .icon_name("list-add-symbolic")
                    .valign(gtk::Align::Center)
                    .build();
                let parent = window.clone();
                create.connect_clicked(move |_| show_create_dialog(&parent));
                row.add_suffix(&create);
            }
            system_group.add(&row);
        } else {
            for deployment in snapshots {
                let subtitle = format!(
                    "{} · {}",
                    deployment_time(deployment),
                    deployment_kind(deployment.kind)
                );
                system_group.add(&file_snapshot_row(
                    window,
                    &deployment.title,
                    &subtitle,
                    "drive-harddisk-symbolic",
                    "system",
                    &deployment.id.to_string(),
                    &deployment.title,
                ));
            }
        }
    }
    content.append(&system_group);
    wrap_page(content)
}

fn file_snapshot_row(
    window: &adw::ApplicationWindow,
    title: &str,
    subtitle: &str,
    icon: &str,
    kind: &'static str,
    id: &str,
    browser_title: &str,
) -> adw::ActionRow {
    let row = adw::ActionRow::builder()
        .title(title)
        .subtitle(subtitle)
        .build();
    row.add_prefix(&gtk::Image::builder().icon_name(icon).pixel_size(26).build());
    let browse = gtk::Button::builder()
        .label(i18n("Browse"))
        .icon_name("folder-open-symbolic")
        .tooltip_text(i18n("Browse and copy files from this snapshot"))
        .valign(gtk::Align::Center)
        .css_classes(["flat"])
        .build();
    let parent = window.clone();
    let id = id.to_string();
    let browser_title = browser_title.to_string();
    browse.connect_clicked(move |_| {
        crate::snapshot_browser::present(&parent, kind, &id, &browser_title);
    });
    row.add_suffix(&browse);
    row
}

fn demo_file_snapshot_row(
    window: &adw::ApplicationWindow,
    title: &str,
    subtitle: &str,
    icon: &str,
) -> adw::ActionRow {
    let row = adw::ActionRow::builder()
        .title(title)
        .subtitle(subtitle)
        .build();
    row.add_prefix(&gtk::Image::builder().icon_name(icon).pixel_size(26).build());
    let browse = gtk::Button::builder()
        .label(i18n("Browse"))
        .icon_name("folder-open-symbolic")
        .valign(gtk::Align::Center)
        .css_classes(["flat"])
        .build();
    let parent = window.clone();
    browse.connect_clicked(move |_| {
        let dialog = adw::AlertDialog::builder()
            .heading(i18n("Design Preview"))
            .body(i18n(
                "The real file browser opens here on a supported system. Demo mode never reads or changes your files.",
            ))
            .close_response("close")
            .build();
        dialog.add_response("close", &i18n("Close"));
        dialog.present(Some(&parent));
    });
    row.add_suffix(&browse);
    row
}

fn demo_recovery_row(
    parent: &adw::ApplicationWindow,
    icon: &str,
    title: &str,
    subtitle: &str,
    badge: &str,
    badge_class: &str,
) -> adw::ActionRow {
    let row = recovery_row(icon, title, subtitle, badge, badge_class, true);
    let parent = parent.clone();
    let title = title.to_string();
    let subtitle = subtitle.to_string();
    row.connect_activated(move |_| {
        show_recovery_preview(&parent, &title, &subtitle);
    });
    row
}

fn show_recovery_preview(parent: &adw::ApplicationWindow, title: &str, subtitle: &str) {
    let details = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(10)
        .margin_top(6)
        .build();
    for text in [
        i18n("System software and settings will return to this point."),
        i18n("Personal files, logs, containers, and virtual machines stay unchanged."),
        i18n("A protected safety point will be created before restart."),
    ] {
        let row = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(10)
            .build();
        row.append(
            &gtk::Image::builder()
                .icon_name("emblem-ok-symbolic")
                .css_classes(["success"])
                .build(),
        );
        row.append(
            &gtk::Label::builder()
                .label(text)
                .wrap(true)
                .xalign(0.0)
                .hexpand(true)
                .build(),
        );
        details.append(&row);
    }
    let dialog = adw::AlertDialog::builder()
        .heading(title)
        .body(subtitle)
        .extra_child(&details)
        .close_response("close")
        .build();
    dialog.add_response("close", &i18n("Close"));
    dialog.add_response("restore", &i18n("Restore to This Point"));
    dialog.set_response_appearance("restore", adw::ResponseAppearance::Suggested);
    dialog.set_response_enabled("restore", false);
    dialog.present(Some(parent));
}

fn build_storage(report: &LayoutReport, demo: bool) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::builder()
        .title(i18n("Storage"))
        .icon_name("drive-harddisk-symbolic")
        .build();
    let overview = adw::PreferencesGroup::builder()
        .title(if is_ext4(report) && !demo {
            i18n("Recovery Storage")
        } else {
            i18n("Btrfs Storage")
        })
        .description(if is_ext4(report) && !demo {
            i18n("Recovery point storage is temporarily unavailable on ext4.")
        } else {
            i18n("Space is shared between the system and recovery points.")
        })
        .build();
    overview.add(&property_row(
        &i18n("Filesystem"),
        if report.is_supported() || demo {
            "Btrfs"
        } else {
            report.root_filesystem.as_deref().unwrap_or("Unknown")
        },
        "drive-harddisk-symbolic",
    ));
    overview.add(&property_row(
        &i18n("Device"),
        if demo {
            "/dev/nvme0n1p4"
        } else {
            report.root_source.as_deref().unwrap_or("Unknown")
        },
        "media-flash-symbolic",
    ));
    overview.add(&property_row(
        &i18n("Recovery storage"),
        if demo {
            "8.4 GB estimated"
        } else {
            "Not measured"
        },
        "document-open-recent-symbolic",
    ));
    page.add(&overview);

    let capacity = adw::PreferencesGroup::builder()
        .title(i18n("Capacity"))
        .description(i18n(
            "Shared Btrfs extents make per-recovery-point sizes approximate.",
        ))
        .build();
    let meter = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(8)
        .margin_start(12)
        .margin_end(12)
        .margin_top(10)
        .margin_bottom(14)
        .build();
    let labels = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    labels.append(
        &gtk::Label::builder()
            .label(if demo {
                i18n("42.6 GB used")
            } else {
                i18n("Measurement pending")
            })
            .halign(gtk::Align::Start)
            .hexpand(true)
            .build(),
    );
    labels.append(
        &gtk::Label::builder()
            .label(if demo {
                i18n("77.4 GB available")
            } else {
                i18n("—")
            })
            .css_classes(["dim-label"])
            .build(),
    );
    meter.append(&labels);
    let progress = gtk::ProgressBar::builder()
        .fraction(if demo { 0.355 } else { 0.0 })
        .css_classes(["storage-meter"])
        .build();
    meter.append(&progress);
    capacity.add(&meter);
    page.add(&capacity);
    page
}

fn build_activity(
    report: &LayoutReport,
    discovery: &DiscoveryState,
    demo: bool,
) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::builder()
        .title(i18n("Activity"))
        .icon_name("view-list-symbolic")
        .build();
    let group = adw::PreferencesGroup::builder()
        .title(i18n("System Recovery Activity"))
        .description(i18n("Persistent, human-readable recovery events"))
        .build();
    let state = if report.is_supported() || demo {
        i18n("Compatible AnduinOS Btrfs layout found")
    } else {
        i18n("Recovery features remain disabled")
    };
    let row = adw::ActionRow::builder()
        .title(i18n("Storage layout inspected"))
        .subtitle(state)
        .build();
    row.add_prefix(
        &gtk::Image::builder()
            .icon_name(if report.is_supported() || demo {
                "security-high-symbolic"
            } else {
                "dialog-information-symbolic"
            })
            .pixel_size(24)
            .css_classes(if report.is_supported() || demo {
                ["success"]
            } else {
                ["accent"]
            })
            .build(),
    );
    row.add_suffix(
        &gtk::Label::builder()
            .label(i18n("Just now"))
            .css_classes(["dim-label", "caption"])
            .valign(gtk::Align::Center)
            .build(),
    );
    group.add(&row);
    if let Some(error) = &discovery.error {
        let row = adw::ActionRow::builder()
            .title(i18n("Recovery service unavailable"))
            .subtitle(error)
            .build();
        row.add_prefix(
            &gtk::Image::builder()
                .icon_name("dialog-warning-symbolic")
                .pixel_size(24)
                .css_classes(["warning"])
                .build(),
        );
        group.add(&row);
    }
    for issue in &discovery.report.issues {
        let row = adw::ActionRow::builder()
            .title(i18n("Recovery point metadata needs attention"))
            .subtitle(format!("{}: {}", issue.entry, issue.message))
            .build();
        row.add_prefix(
            &gtk::Image::builder()
                .icon_name("dialog-warning-symbolic")
                .pixel_size(24)
                .css_classes(["warning"])
                .build(),
        );
        group.add(&row);
    }
    page.add(&group);
    page
}

fn build_settings(retention_state: &RetentionState, demo: bool) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::builder()
        .title(i18n("Settings"))
        .icon_name("preferences-system-symbolic")
        .build();
    let automatic = adw::PreferencesGroup::builder()
        .title(i18n("Automatic Protection"))
        .description(i18n(
            "APT-managed package changes are surrounded by fail-open recovery points.",
        ))
        .build();
    automatic.add(&status_row(
        &i18n("Before system updates"),
        &i18n("Create a recovery point before APT changes packages"),
        &i18n("Active"),
        "success",
    ));
    automatic.add(&status_row(
        &i18n("After successful updates"),
        &i18n("Keep a verified post-update deployment"),
        &i18n("Active"),
        "success",
    ));
    page.add(&automatic);

    let retention = adw::PreferencesGroup::builder()
        .title(i18n("Retention"))
        .description(i18n(
            "Cleanup runs after package transactions and rechecks free space after every deletion.",
        ))
        .build();
    retention.add(&status_row(
        &i18n("Balanced retention"),
        &i18n("Keep at least two update pairs and one known-good recovery point"),
        &i18n("Active"),
        "success",
    ));
    if let Some(plan) = &retention_state.plan {
        let subtitle = i18n_fmt(
            &i18n("{0} available · cleanup target {1}"),
            &[
                &format_bytes(plan.space.available_bytes),
                &format_bytes(plan.free_space_target_bytes),
            ],
        );
        retention.add(&status_row(
            &i18n("Btrfs free-space reserve"),
            &subtitle,
            &if plan.under_space_pressure {
                i18n("Low space")
            } else {
                i18n("Healthy")
            },
            if plan.under_space_pressure {
                "warning"
            } else {
                "success"
            },
        ));
        retention.add(&status_row(
            &i18n("Next cleanup"),
            &i18n("Only eligible automatic package recovery points are considered"),
            &i18n_fmt(&i18n("{0} point(s)"), &[&plan.actions.len().to_string()]),
            if plan.actions.is_empty() {
                "success"
            } else {
                "warning"
            },
        ));
    } else if let Some(error) = &retention_state.error {
        retention.add(&status_row(
            &i18n("Retention status"),
            error,
            &i18n("Unavailable"),
            "warning",
        ));
    } else if demo {
        retention.add(&status_row(
            &i18n("Btrfs free-space reserve"),
            &i18n("Live free-space accounting appears on a Btrfs installation"),
            &i18n("Preview"),
            "planned-badge",
        ));
    }
    page.add(&retention);
    page
}

fn build_automatic_snapshots(
    parent: &adw::ApplicationWindow,
    report: &LayoutReport,
    automatic_status: Option<&anduinos_timeback::automation::AutomaticStatus>,
) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::builder()
        .title(i18n(concat!("Automatic ", "Snapshots")))
        .icon_name("alarm-symbolic")
        .build();
    let intro = adw::PreferencesGroup::builder()
        .title(i18n("Snapshot Policies"))
        .description(i18n(
            "Choose one policy for both volumes, or manage System and User Data independently.",
        ))
        .build();
    page.add(&intro);

    let Some(status) = automatic_status else {
        intro.add(&status_row(
            &i18n("Automatic snapshot service"),
            &i18n("The system service did not return its automatic snapshot configuration."),
            &i18n("Unavailable"),
            "error",
        ));
        return page;
    };
    let configuration = status.configuration.clone();
    let targets = targets::discover_targets(report);
    let system_available = targets
        .iter()
        .any(|target| target.kind == targets::TargetKind::System && target.available);
    let home_available = targets
        .iter()
        .any(|target| target.kind == targets::TargetKind::Home && target.available);

    let linked = gtk::CheckButton::builder()
        .label(i18n(
            "Keep System and User Data snapshot policies identical",
        ))
        .tooltip_text(i18n(
            "When linking different policies, the current System values become the shared policy.",
        ))
        .active(configuration.policies_linked)
        .build();
    linked.set_sensitive(system_available && home_available);
    intro.add(&linked);

    let stack = gtk::Stack::builder()
        .transition_type(gtk::StackTransitionType::Crossfade)
        .hexpand(true)
        .build();
    let switcher = gtk::StackSwitcher::builder()
        .stack(&stack)
        .halign(gtk::Align::Center)
        .build();
    intro.add(&switcher);

    let shared_status = TargetAutomaticStatus {
        last_success: match (status.system.last_success, status.home.last_success) {
            (Some(system), Some(home)) => Some(system.min(home)),
            _ => None,
        },
        last_attempt: match (status.system.last_attempt, status.home.last_attempt) {
            (Some(system), Some(home)) => Some(system.min(home)),
            _ => None,
        },
        last_error: status
            .system
            .last_error
            .clone()
            .or_else(|| status.home.last_error.clone()),
        next_run: match (status.system.next_run, status.home.next_run) {
            (Some(system), Some(home)) => Some(system.min(home)),
            _ => None,
        },
    };

    let shared_editor = automatic_policy_editor(
        parent,
        &configuration,
        PolicyEditorMode::Shared,
        &configuration.system,
        &shared_status,
        system_available && home_available,
    );
    let system_editor = automatic_policy_editor(
        parent,
        &configuration,
        PolicyEditorMode::System,
        &configuration.system,
        &status.system,
        system_available,
    );
    let home_editor = automatic_policy_editor(
        parent,
        &configuration,
        PolicyEditorMode::Home,
        &configuration.home,
        &status.home,
        home_available,
    );
    stack.add_titled(&shared_editor, Some("shared"), &i18n("Shared Policy"));
    stack.add_titled(&system_editor, Some("system"), &i18n("System"));
    stack.add_titled(&home_editor, Some("home"), &i18n("User Data"));

    if configuration.policies_linked {
        stack.set_visible_child_name("shared");
        switcher.set_visible(false);
    } else {
        stack.set_visible_child_name("system");
        switcher.set_visible(true);
    }
    let stack_for_link = stack.clone();
    let switcher_for_link = switcher.clone();
    linked.connect_toggled(move |linked| {
        if linked.is_active() {
            stack_for_link.set_visible_child_name("shared");
            switcher_for_link.set_visible(false);
        } else {
            stack_for_link.set_visible_child_name("system");
            switcher_for_link.set_visible(true);
        }
    });
    intro.add(&stack);
    page
}

#[derive(Clone, Copy)]
enum PolicyEditorMode {
    Shared,
    System,
    Home,
}

fn automatic_policy_editor(
    parent: &adw::ApplicationWindow,
    configuration: &AutomaticConfiguration,
    mode: PolicyEditorMode,
    policy: &AutomaticPolicy,
    status: &TargetAutomaticStatus,
    available: bool,
) -> gtk::Box {
    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(18)
        .margin_top(12)
        .build();
    let schedule = adw::PreferencesGroup::builder()
        .title(match mode {
            PolicyEditorMode::Shared => i18n("System and User Data"),
            PolicyEditorMode::System => i18n("System"),
            PolicyEditorMode::Home => i18n("User Data"),
        })
        .description(if available {
            match mode {
                PolicyEditorMode::Shared => {
                    i18n("This policy is applied to both snapshot streams.")
                }
                PolicyEditorMode::System => {
                    i18n("System snapshots can be used for a full system restore.")
                }
                PolicyEditorMode::Home => {
                    i18n("User Data snapshots preserve the independent Home subvolume.")
                }
            }
        } else {
            i18n("This volume is not an independent compatible Btrfs subvolume.")
        })
        .build();
    let enabled = adw::SwitchRow::builder()
        .title(i18n("Create snapshots automatically"))
        .active(policy.enabled)
        .sensitive(available)
        .build();
    schedule.add(&enabled);
    let interval_adjustment = gtk::Adjustment::new(
        f64::from(policy.interval_minutes) / 60.0,
        0.25,
        720.0,
        0.25,
        1.0,
        0.0,
    );
    let interval = adw::SpinRow::builder()
        .title(i18n("Create every"))
        .subtitle(i18n("Hours between successful snapshots"))
        .adjustment(&interval_adjustment)
        .digits(2)
        .build();
    schedule.add(&interval);
    schedule.add(&automatic_overview_row(
        &i18n("Schedule status"),
        &automatic_time(status.last_success, &i18n("Never")),
        &automatic_time(status.next_run, &i18n("Not scheduled")),
        policy.enabled,
    ));
    content.append(&schedule);

    let retention = adw::PreferencesGroup::builder()
        .title(i18n("Tiered Retention"))
        .description(i18n(
            "Keep recent snapshots densely, then retain the first snapshot in each calendar period.",
        ))
        .build();
    let keep_all = policy_spin_row(
        &i18n("Keep every snapshot for"),
        &i18n("Hours"),
        1.0,
        8_760.0,
        policy.keep_all_hours,
    );
    let daily = policy_spin_row(
        &i18n("Keep the first snapshot of each day until"),
        &i18n("Days after creation"),
        1.0,
        36_500.0,
        policy.keep_daily_days,
    );
    let weekly = policy_spin_row(
        &i18n("Keep the first snapshot of each week until"),
        &i18n("Days after creation"),
        1.0,
        36_500.0,
        policy.keep_weekly_days,
    );
    let monthly = policy_spin_row(
        &i18n("Keep the first snapshot of each month until"),
        &i18n("Days after creation"),
        1.0,
        36_500.0,
        policy.keep_monthly_days,
    );
    let delete_after = policy_spin_row(
        &i18n("Delete all snapshots older than"),
        &i18n("Days after creation"),
        1.0,
        36_500.0,
        policy.delete_after_days,
    );
    for row in [&keep_all, &daily, &weekly, &monthly, &delete_after] {
        row.set_sensitive(available);
        retention.add(row);
    }
    content.append(&retention);

    let actions = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(12)
        .halign(gtk::Align::End)
        .build();
    let reset = gtk::Button::builder()
        .label(i18n("Restore Recommended Values"))
        .sensitive(available)
        .build();
    let save = gtk::Button::builder()
        .label(i18n("Save Policy"))
        .css_classes(["suggested-action"])
        .sensitive(available)
        .build();
    actions.append(&reset);
    actions.append(&save);
    content.append(&actions);

    if let Some(error) = &status.last_error {
        content.append(&status_row(
            &i18n("Last automatic operation"),
            error,
            &i18n("Failed"),
            "error",
        ));
    }

    let mut preset = match mode {
        PolicyEditorMode::Home => AutomaticPolicy::home_preset(),
        PolicyEditorMode::Shared | PolicyEditorMode::System => AutomaticPolicy::system_preset(),
    };
    // Resetting retention values must not unexpectedly turn protection off.
    preset.enabled = policy.enabled;
    {
        let enabled = enabled.clone();
        let interval = interval.clone();
        let keep_all = keep_all.clone();
        let daily = daily.clone();
        let weekly = weekly.clone();
        let monthly = monthly.clone();
        let delete_after = delete_after.clone();
        reset.connect_clicked(move |_| {
            enabled.set_active(preset.enabled);
            interval.set_value(f64::from(preset.interval_minutes) / 60.0);
            keep_all.set_value(f64::from(preset.keep_all_hours));
            daily.set_value(f64::from(preset.keep_daily_days));
            weekly.set_value(f64::from(preset.keep_weekly_days));
            monthly.set_value(f64::from(preset.keep_monthly_days));
            delete_after.set_value(f64::from(preset.delete_after_days));
        });
    }

    let parent = parent.clone();
    let base = configuration.clone();
    save.connect_clicked(move |button| {
        let updated_policy = AutomaticPolicy {
            enabled: enabled.is_active(),
            interval_minutes: (interval.value() * 60.0).round() as u32,
            keep_all_hours: keep_all.value() as u32,
            keep_daily_days: daily.value() as u32,
            keep_weekly_days: weekly.value() as u32,
            keep_monthly_days: monthly.value() as u32,
            delete_after_days: delete_after.value() as u32,
        };
        if updated_policy.validate().is_err() {
            show_policy_error(
                &parent,
                &i18n(
                    "Retention periods must progress from keep-all to daily, weekly, monthly, and final deletion.",
                ),
            );
            return;
        }
        let mut updated = base.clone();
        match mode {
            PolicyEditorMode::Shared => {
                updated.policies_linked = true;
                updated.system = updated_policy.clone();
                updated.home = updated_policy;
            }
            PolicyEditorMode::System => {
                updated.policies_linked = false;
                updated.system = updated_policy;
            }
            PolicyEditorMode::Home => {
                updated.policies_linked = false;
                updated.home = updated_policy;
            }
        }
        save_automatic_configuration(&parent, button, updated);
    });
    content
}

fn policy_spin_row(
    title: &str,
    subtitle: &str,
    minimum: f64,
    maximum: f64,
    value: u32,
) -> adw::SpinRow {
    let adjustment = gtk::Adjustment::new(f64::from(value), minimum, maximum, 1.0, 10.0, 0.0);
    adw::SpinRow::builder()
        .title(title)
        .subtitle(subtitle)
        .adjustment(&adjustment)
        .digits(0)
        .build()
}

fn save_automatic_configuration(
    parent: &adw::ApplicationWindow,
    button: &gtk::Button,
    configuration: AutomaticConfiguration,
) {
    button.set_sensitive(false);
    let (sender, receiver) = mpsc::channel();
    let spawn = std::thread::Builder::new()
        .name("timeback-automatic-configuration".into())
        .spawn(move || {
            let result = client::set_automatic_configuration(&configuration, |_| {})
                .map_err(|error| error.to_string());
            let _ = sender.send(result);
        });
    if let Err(error) = spawn {
        button.set_sensitive(true);
        show_operation_error(
            parent,
            &format!("Could not start the automatic-policy worker: {error}"),
        );
        return;
    }
    let parent = parent.clone();
    let button = button.clone();
    glib::timeout_add_local(Duration::from_millis(50), move || {
        match receiver.try_recv() {
            Ok(Ok(result)) if result.success => {
                reload_window(&parent, Some(&i18n("Automatic snapshot policies saved")));
                glib::ControlFlow::Break
            }
            Ok(Ok(result)) => {
                button.set_sensitive(true);
                show_operation_error(
                    &parent,
                    &format!("{}: {}", result.error_code, result.message),
                );
                glib::ControlFlow::Break
            }
            Ok(Err(error)) => {
                button.set_sensitive(true);
                show_operation_error(&parent, &error);
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => {
                button.set_sensitive(true);
                show_operation_error(&parent, &i18n("The recovery worker disconnected"));
                glib::ControlFlow::Break
            }
        }
    });
}

fn show_policy_error(parent: &adw::ApplicationWindow, message: &str) {
    let dialog = adw::AlertDialog::builder()
        .heading(i18n("Check Snapshot Policy"))
        .body(message)
        .close_response("close")
        .build();
    dialog.add_response("close", &i18n("Close"));
    dialog.present(Some(parent.upcast_ref::<gtk::Widget>()));
}

fn status_row(title: &str, subtitle: &str, status: &str, style: &str) -> adw::ActionRow {
    let row = adw::ActionRow::builder()
        .title(title)
        .subtitle(subtitle)
        .build();
    row.add_suffix(
        &gtk::Label::builder()
            .label(status)
            .css_classes(["pill", "caption", style])
            .valign(gtk::Align::Center)
            .build(),
    );
    row
}

fn format_bytes(bytes: u64) -> String {
    const KIB: f64 = 1024.0;
    const MIB: f64 = KIB * 1024.0;
    const GIB: f64 = MIB * 1024.0;
    const TIB: f64 = GIB * 1024.0;
    let bytes = bytes as f64;
    if bytes >= TIB {
        format!("{:.1} TiB", bytes / TIB)
    } else if bytes >= GIB {
        format!("{:.1} GiB", bytes / GIB)
    } else if bytes >= MIB {
        format!("{:.1} MiB", bytes / MIB)
    } else if bytes >= KIB {
        format!("{:.1} KiB", bytes / KIB)
    } else {
        format!("{} B", bytes as u64)
    }
}

fn property_row(title: &str, value: &str, icon: &str) -> adw::ActionRow {
    let row = adw::ActionRow::builder().title(title).build();
    row.add_prefix(&gtk::Image::builder().icon_name(icon).pixel_size(24).build());
    row.add_suffix(
        &gtk::Label::builder()
            .label(value)
            .css_classes(["dim-label"])
            .valign(gtk::Align::Center)
            .selectable(true)
            .build(),
    );
    row
}

fn navigation_row(icon: &str, title: &str) -> gtk::ListBoxRow {
    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(12)
        .margin_start(12)
        .margin_end(12)
        .margin_top(10)
        .margin_bottom(10)
        .build();
    content.append(&gtk::Image::builder().icon_name(icon).pixel_size(18).build());
    content.append(
        &gtk::Label::builder()
            .label(title)
            .halign(gtk::Align::Start)
            .hexpand(true)
            .build(),
    );
    gtk::ListBoxRow::builder().child(&content).build()
}

fn page_content() -> gtk::Box {
    gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(20)
        .margin_top(28)
        .margin_bottom(36)
        .margin_start(24)
        .margin_end(24)
        .build()
}

fn wrap_page(content: gtk::Box) -> gtk::ScrolledWindow {
    let clamp = adw::Clamp::builder()
        .maximum_size(920)
        .tightening_threshold(720)
        .child(&content)
        .build();
    gtk::ScrolledWindow::builder()
        .hscrollbar_policy(gtk::PolicyType::Never)
        .child(&clamp)
        .build()
}

fn section_heading(title: &str, subtitle: &str) -> gtk::Box {
    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(3)
        .build();
    content.append(
        &gtk::Label::builder()
            .label(title)
            .css_classes(["title-2"])
            .halign(gtk::Align::Start)
            .build(),
    );
    content.append(
        &gtk::Label::builder()
            .label(subtitle)
            .css_classes(["dim-label"])
            .halign(gtk::Align::Start)
            .wrap(true)
            .xalign(0.0)
            .build(),
    );
    content
}

fn recovery_row(
    icon: &str,
    title: &str,
    subtitle: &str,
    badge: &str,
    badge_class: &str,
    activatable: bool,
) -> adw::ActionRow {
    let row = adw::ActionRow::builder()
        .title(title)
        .subtitle(subtitle)
        .activatable(activatable)
        .build();
    let marker = gtk::Box::builder()
        .width_request(40)
        .height_request(40)
        .halign(gtk::Align::Center)
        .valign(gtk::Align::Center)
        .css_classes(["timeline-marker"])
        .build();
    marker.append(&gtk::Image::builder().icon_name(icon).pixel_size(20).build());
    row.add_prefix(&marker);
    row.add_suffix(
        &gtk::Label::builder()
            .label(badge)
            .css_classes(["pill", "caption", badge_class])
            .valign(gtk::Align::Center)
            .build(),
    );
    if activatable {
        row.add_suffix(
            &gtk::Image::builder()
                .icon_name("go-next-symbolic")
                .valign(gtk::Align::Center)
                .css_classes(["dim-label"])
                .build(),
        );
    }
    row
}

fn timeline_heading(title: &str, count: usize) -> TimelineHeading {
    let row = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    row.append(
        &gtk::Label::builder()
            .label(title)
            .css_classes(["heading"])
            .hexpand(true)
            .halign(gtk::Align::Start)
            .build(),
    );
    let count = gtk::Label::builder()
        .label(count.to_string())
        .css_classes(["pill", "caption"])
        .build();
    row.append(&count);
    TimelineHeading { widget: row, count }
}

#[derive(Clone, Debug)]
enum UiMutation {
    Create {
        title: String,
        reason: String,
        pinned: bool,
    },
    SetPinned {
        deployment_id: String,
        pinned: bool,
    },
    Delete {
        deployment_id: String,
    },
    Verify {
        deployment_id: String,
    },
    Restore {
        deployment_id: String,
    },
    CancelRestore,
}

enum UiMutationEvent {
    Progress(client::OperationProgress),
    Finished(Result<client::OperationResult, String>, bool),
}

fn show_create_dialog(parent: &adw::ApplicationWindow) {
    let form = adw::PreferencesGroup::new();
    let title = adw::EntryRow::builder()
        .title(i18n("Name"))
        .text(i18n("Manual recovery point"))
        .build();
    title.set_max_length(120);
    let reason = adw::EntryRow::builder()
        .title(i18n("Description"))
        .text(i18n("Created before a manual system change"))
        .build();
    reason.set_max_length(500);
    let pinned = adw::SwitchRow::builder()
        .title(i18n("Keep until I delete it"))
        .subtitle(i18n(
            "Pinned recovery points are never cleaned automatically",
        ))
        .build();
    form.add(&title);
    form.add(&reason);
    form.add(&pinned);

    let dialog = adw::AlertDialog::builder()
        .heading(i18n("Create Recovery Point"))
        .body(i18n(
            "The operating system will be captured without changing personal files, logs, containers, or virtual machines.",
        ))
        .extra_child(&form)
        .close_response("cancel")
        .default_response("create")
        .build();
    dialog.add_response("cancel", &i18n("Cancel"));
    dialog.add_response("create", &i18n("Create"));
    dialog.set_response_appearance("create", adw::ResponseAppearance::Suggested);
    dialog.set_response_enabled("create", true);

    let dialog_for_title = dialog.clone();
    let reason_for_title = reason.clone();
    title.connect_changed(move |title| {
        dialog_for_title.set_response_enabled(
            "create",
            !title.text().trim().is_empty() && !reason_for_title.text().trim().is_empty(),
        );
    });
    let dialog_for_reason = dialog.clone();
    let title_for_reason = title.clone();
    reason.connect_changed(move |reason| {
        dialog_for_reason.set_response_enabled(
            "create",
            !title_for_reason.text().trim().is_empty() && !reason.text().trim().is_empty(),
        );
    });

    let response_parent = parent.clone();
    dialog.connect_response(None, move |_dialog, response| {
        if response != "create" {
            return;
        }
        run_ui_mutation(
            &response_parent,
            &i18n("Creating Recovery Point"),
            UiMutation::Create {
                title: title.text().to_string(),
                reason: reason.text().to_string(),
                pinned: pinned.is_active(),
            },
        );
    });
    dialog.present(Some(parent.upcast_ref::<gtk::Widget>()));
}

fn interactive_deployment_row(
    parent: &adw::ApplicationWindow,
    deployment: &DeploymentRecord,
) -> adw::ActionRow {
    let row = deployment_row(deployment, false);
    if deployment.snapshot_uuid.is_some()
        && deployment.state != DeploymentState::Creating
        && deployment.state != DeploymentState::Deleting
    {
        let browse = gtk::Button::builder()
            .icon_name("folder-open-symbolic")
            .tooltip_text(i18n("Browse snapshot files"))
            .valign(gtk::Align::Center)
            .css_classes(["flat"])
            .build();
        let parent_for_browse = parent.clone();
        let id_for_browse = deployment.id.to_string();
        let title_for_browse = deployment.title.clone();
        browse.connect_clicked(move |_| {
            crate::snapshot_browser::present(
                &parent_for_browse,
                "system",
                &id_for_browse,
                &title_for_browse,
            );
        });
        row.add_suffix(&browse);
    }
    if deployment.state == DeploymentState::Ready && deployment.can_restore() {
        let restore = gtk::Button::builder()
            .icon_name("document-revert-symbolic")
            .tooltip_text(i18n("Restore to this recovery point"))
            .valign(gtk::Align::Center)
            .css_classes(["flat"])
            .build();
        let parent_for_restore = parent.clone();
        let id_for_restore = deployment.id.to_string();
        let title_for_restore = deployment.title.clone();
        restore.connect_clicked(move |_| {
            show_restore_dialog(&parent_for_restore, &id_for_restore, &title_for_restore);
        });
        row.add_suffix(&restore);
    } else if deployment.state == DeploymentState::PendingRollback {
        let cancel = gtk::Button::builder()
            .icon_name("process-stop-symbolic")
            .tooltip_text(i18n("Cancel pending system restore"))
            .valign(gtk::Align::Center)
            .css_classes(["flat"])
            .build();
        let parent_for_cancel = parent.clone();
        cancel.connect_clicked(move |_| {
            run_ui_mutation(
                &parent_for_cancel,
                &i18n("Cancelling System Restore"),
                UiMutation::CancelRestore,
            );
        });
        row.add_suffix(&cancel);
    }
    let verify = gtk::Button::builder()
        .icon_name("security-high-symbolic")
        .tooltip_text(i18n("Verify recovery point"))
        .valign(gtk::Align::Center)
        .sensitive(deployment.can_restore())
        .css_classes(["flat"])
        .build();
    let parent_for_verify = parent.clone();
    let id_for_verify = deployment.id.to_string();
    verify.connect_clicked(move |_| {
        run_ui_mutation(
            &parent_for_verify,
            &i18n("Verifying Recovery Point"),
            UiMutation::Verify {
                deployment_id: id_for_verify.clone(),
            },
        );
    });
    row.add_suffix(&verify);

    let pin = gtk::Button::builder()
        .icon_name(if deployment.pinned {
            "starred-symbolic"
        } else {
            "non-starred-symbolic"
        })
        .tooltip_text(if deployment.pinned {
            i18n("Unpin recovery point")
        } else {
            i18n("Pin recovery point")
        })
        .valign(gtk::Align::Center)
        .sensitive(deployment.state != DeploymentState::Deleting)
        .css_classes(["flat"])
        .build();
    let parent_for_pin = parent.clone();
    let id_for_pin = deployment.id.to_string();
    let pinned = deployment.pinned;
    pin.connect_clicked(move |_| {
        let heading = if pinned {
            i18n("Unpinning Recovery Point")
        } else {
            i18n("Pinning Recovery Point")
        };
        run_ui_mutation(
            &parent_for_pin,
            &heading,
            UiMutation::SetPinned {
                deployment_id: id_for_pin.clone(),
                pinned: !pinned,
            },
        );
    });
    row.add_suffix(&pin);

    let delete = gtk::Button::builder()
        .icon_name("user-trash-symbolic")
        .tooltip_text(i18n("Delete recovery point"))
        .valign(gtk::Align::Center)
        .sensitive(deployment.can_delete() || deployment.state == DeploymentState::Deleting)
        .css_classes(["flat"])
        .build();
    let parent_for_delete = parent.clone();
    let id_for_delete = deployment.id.to_string();
    let title_for_delete = deployment.title.clone();
    delete.connect_clicked(move |_| {
        show_delete_dialog(&parent_for_delete, &id_for_delete, &title_for_delete);
    });
    row.add_suffix(&delete);
    row
}

fn show_restore_dialog(parent: &adw::ApplicationWindow, deployment_id: &str, title: &str) {
    let details = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(10)
        .margin_top(6)
        .build();
    for text in [
        i18n("System software and settings will return to this point."),
        i18n("Personal files, logs, containers, and virtual machines stay unchanged."),
        i18n("The current system will be protected before restart."),
        i18n("The restored system must boot successfully or the previous system returns automatically."),
        i18n("Normal AnduinOS entries remain in GRUB if you change your mind at boot."),
    ] {
        let item = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(10)
            .build();
        item.append(
            &gtk::Image::builder()
                .icon_name("emblem-ok-symbolic")
                .css_classes(["success"])
                .build(),
        );
        item.append(
            &gtk::Label::builder()
                .label(text)
                .wrap(true)
                .xalign(0.0)
                .hexpand(true)
                .build(),
        );
        details.append(&item);
    }
    let dialog = adw::AlertDialog::builder()
        .heading(i18n_fmt(&i18n("Restore “{0}”?"), &[title]))
        .body(i18n(
            "A one-time recovery boot will be prepared. Nothing changes until you restart.",
        ))
        .extra_child(&details)
        .close_response("cancel")
        .default_response("cancel")
        .build();
    dialog.add_response("cancel", &i18n("Cancel"));
    dialog.add_response("restore", &i18n("Prepare System Restore"));
    dialog.set_response_appearance("restore", adw::ResponseAppearance::Suggested);
    let response_parent = parent.clone();
    let deployment_id = deployment_id.to_string();
    dialog.connect_response(None, move |_dialog, response| {
        if response == "restore" {
            run_ui_mutation(
                &response_parent,
                &i18n("Preparing System Restore"),
                UiMutation::Restore {
                    deployment_id: deployment_id.clone(),
                },
            );
        }
    });
    dialog.present(Some(parent.upcast_ref::<gtk::Widget>()));
}

fn show_delete_dialog(parent: &adw::ApplicationWindow, deployment_id: &str, title: &str) {
    let dialog = adw::AlertDialog::builder()
        .heading(i18n("Delete Recovery Point?"))
        .body(i18n_fmt(
            &i18n(
                "“{0}” will be permanently removed. The running system and personal files will not change.",
            ),
            &[title],
        ))
        .close_response("cancel")
        .default_response("cancel")
        .build();
    dialog.add_response("cancel", &i18n("Cancel"));
    dialog.add_response("delete", &i18n("Delete"));
    dialog.set_response_appearance("delete", adw::ResponseAppearance::Destructive);
    let response_parent = parent.clone();
    let deployment_id = deployment_id.to_string();
    dialog.connect_response(None, move |_dialog, response| {
        if response == "delete" {
            run_ui_mutation(
                &response_parent,
                &i18n("Deleting Recovery Point"),
                UiMutation::Delete {
                    deployment_id: deployment_id.clone(),
                },
            );
        }
    });
    dialog.present(Some(parent.upcast_ref::<gtk::Widget>()));
}

fn run_ui_mutation(parent: &adw::ApplicationWindow, heading: &str, mutation: UiMutation) {
    let progress = gtk::ProgressBar::builder()
        .show_text(false)
        .fraction(0.0)
        .build();
    let initial_status = if matches!(&mutation, UiMutation::Verify { .. }) {
        i18n("Preparing integrity verification…")
    } else {
        i18n("Waiting for authorization…")
    };
    let status = gtk::Label::builder()
        .label(initial_status)
        .wrap(true)
        .xalign(0.0)
        .css_classes(["dim-label"])
        .build();
    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(12)
        .build();
    content.append(&progress);
    content.append(&status);
    let dialog = adw::AlertDialog::builder()
        .heading(heading)
        .body(i18n(
            "Keep this window open. Recovery metadata is committed atomically.",
        ))
        .extra_child(&content)
        .can_close(false)
        .build();
    dialog.present(Some(parent.upcast_ref::<gtk::Widget>()));

    let reboot_after_success = matches!(&mutation, UiMutation::Restore { .. });
    let (sender, receiver) = mpsc::channel::<UiMutationEvent>();
    let worker_sender = sender.clone();
    let spawn = std::thread::Builder::new()
        .name("timeback-ui-operation".into())
        .spawn(move || {
            let progress_sender = worker_sender.clone();
            let result = match mutation {
                UiMutation::Create {
                    title,
                    reason,
                    pinned,
                } => client::create_recovery_point(&title, &reason, pinned, move |progress| {
                    let _ = progress_sender.send(UiMutationEvent::Progress(progress));
                }),
                UiMutation::SetPinned {
                    deployment_id,
                    pinned,
                } => client::set_pinned(&deployment_id, pinned, move |progress| {
                    let _ = progress_sender.send(UiMutationEvent::Progress(progress));
                }),
                UiMutation::Delete { deployment_id } => {
                    client::delete_recovery_point(&deployment_id, move |progress| {
                        let _ = progress_sender.send(UiMutationEvent::Progress(progress));
                    })
                }
                UiMutation::Verify { deployment_id } => {
                    client::verify_recovery_point(&deployment_id, move |progress| {
                        let _ = progress_sender.send(UiMutationEvent::Progress(progress));
                    })
                }
                UiMutation::Restore { deployment_id } => {
                    client::schedule_rollback(&deployment_id, move |progress| {
                        let _ = progress_sender.send(UiMutationEvent::Progress(progress));
                    })
                }
                UiMutation::CancelRestore => client::cancel_pending_rollback(move |progress| {
                    let _ = progress_sender.send(UiMutationEvent::Progress(progress));
                }),
            };
            let _ = worker_sender.send(UiMutationEvent::Finished(
                result.map_err(|error| error.to_string()),
                reboot_after_success,
            ));
        });
    if let Err(error) = spawn {
        dialog.force_close();
        show_operation_error(parent, &format!("Could not start the UI worker: {error}"));
        return;
    }

    let parent = parent.clone();
    glib::timeout_add_local(Duration::from_millis(50), move || {
        loop {
            match receiver.try_recv() {
                Ok(UiMutationEvent::Progress(update)) => {
                    progress.set_fraction(update.fraction.clamp(0.0, 1.0));
                    status.set_label(&update.message);
                }
                Ok(UiMutationEvent::Finished(result, reboot_ready)) => {
                    dialog.force_close();
                    match result {
                        Ok(result) if result.success => {
                            let refreshed = reload_window(&parent, Some(&result.message));
                            if reboot_ready {
                                show_restart_dialog(&refreshed);
                            }
                        }
                        Ok(result) => {
                            let refreshed = reload_window(&parent, None);
                            show_operation_error(
                                &refreshed,
                                &format!("{}: {}", result.error_code, result.message),
                            );
                        }
                        Err(error) => {
                            let refreshed = reload_window(&parent, None);
                            show_operation_error(&refreshed, &error);
                        }
                    }
                    return glib::ControlFlow::Break;
                }
                Err(mpsc::TryRecvError::Empty) => break,
                Err(mpsc::TryRecvError::Disconnected) => {
                    dialog.force_close();
                    show_operation_error(&parent, &i18n("The recovery worker disconnected"));
                    return glib::ControlFlow::Break;
                }
            }
        }
        glib::ControlFlow::Continue
    });
}

fn show_restart_dialog(parent: &adw::ApplicationWindow) {
    let dialog = adw::AlertDialog::builder()
        .heading(i18n("System Restore Is Ready"))
        .body(i18n(
            "Restart to try the recovery entry once. You can still cancel from the Overview before restarting, or choose a normal AnduinOS entry in GRUB.",
        ))
        .close_response("later")
        .default_response("later")
        .build();
    dialog.add_response("later", &i18n("Restart Later"));
    dialog.add_response("restart", &i18n("Restart Now"));
    dialog.set_response_appearance("restart", adw::ResponseAppearance::Suggested);
    let response_parent = parent.clone();
    dialog.connect_response(None, move |_dialog, response| {
        if response != "restart" {
            return;
        }
        if let Err(error) = std::process::Command::new("/usr/bin/systemctl")
            .arg("reboot")
            .spawn()
        {
            show_operation_error(
                &response_parent,
                &i18n_fmt(
                    &i18n("Could not request a restart: {0}"),
                    &[&error.to_string()],
                ),
            );
        }
    });
    dialog.present(Some(parent.upcast_ref::<gtk::Widget>()));
}

fn reload_window(
    window: &adw::ApplicationWindow,
    success_notice: Option<&str>,
) -> adw::ApplicationWindow {
    let Some(application) = window
        .application()
        .and_then(|application| application.downcast::<TimebackApplication>().ok())
    else {
        return window.clone();
    };
    let refreshed = build_with_notice(&application, success_notice);
    refreshed.present();
    window.close();
    refreshed
}

fn show_operation_error(parent: &adw::ApplicationWindow, message: &str) {
    let dialog = adw::AlertDialog::builder()
        .heading(i18n("Recovery Operation Failed"))
        .body(message)
        .close_response("close")
        .build();
    dialog.add_response("close", &i18n("Close"));
    dialog.present(Some(parent.upcast_ref::<gtk::Widget>()));
}

fn deployment_row(deployment: &DeploymentRecord, activatable: bool) -> adw::ActionRow {
    let (badge, badge_class) = deployment_badge(deployment);
    let kernel = deployment.kernel_release.clone().unwrap_or_else(|| {
        if deployment.can_restore() {
            "—".into()
        } else {
            i18n("Unverified")
        }
    });
    let subtitle = format!(
        "{} · {} · {}",
        deployment_time(deployment),
        deployment_kind(deployment.kind),
        kernel
    );
    let row = recovery_row(
        deployment_icon(deployment),
        &deployment.title,
        &subtitle,
        &badge,
        badge_class,
        activatable,
    );
    let source = gtk::Label::builder()
        .label(deployment_source(deployment.kind))
        .css_classes(["pill", "caption", "neutral"])
        .valign(gtk::Align::Center)
        .tooltip_text(i18n("Snapshot source"))
        .build();
    row.add_suffix(&source);
    row
}

fn deployment_time(deployment: &DeploymentRecord) -> String {
    deployment
        .created_at
        .format("%Y-%m-%d %H:%M UTC")
        .to_string()
}

fn deployment_kind(kind: DeploymentKind) -> String {
    match kind {
        DeploymentKind::Factory => i18n("Factory"),
        DeploymentKind::Manual => i18n("Manual"),
        DeploymentKind::Automatic => i18n("Automatic"),
        DeploymentKind::AptPre => i18n("Before update"),
        DeploymentKind::AptPost => i18n("After update"),
        DeploymentKind::PreRollback => i18n("Before rollback"),
    }
}

fn deployment_source(kind: DeploymentKind) -> String {
    match kind {
        DeploymentKind::Manual => i18n("Manual snapshot"),
        DeploymentKind::Factory => i18n("Factory snapshot"),
        DeploymentKind::Automatic => i18n("Automatic snapshot"),
        DeploymentKind::AptPre | DeploymentKind::AptPost => i18n("Update snapshot"),
        DeploymentKind::PreRollback => i18n("Restore safety snapshot"),
    }
}

fn deployment_badge(deployment: &DeploymentRecord) -> (String, &'static str) {
    if deployment.pinned {
        return (i18n("Pinned"), "warning");
    }
    match deployment.state {
        DeploymentState::Current => (i18n("Current"), "accent"),
        DeploymentState::Ready => (i18n("Ready"), "success"),
        DeploymentState::FallbackProtected => (i18n("Protected"), "success"),
        DeploymentState::PendingRollback => (i18n("Pending"), "warning"),
        DeploymentState::BootedUnconfirmed => (i18n("Confirming"), "warning"),
        DeploymentState::Creating => (i18n("Creating"), "accent"),
        DeploymentState::Incomplete => (i18n("Incomplete"), "warning"),
        DeploymentState::FailedReverted => (i18n("Reverted"), "warning"),
        DeploymentState::Broken => (i18n("Broken"), "error"),
        DeploymentState::Deleting => (i18n("Deleting"), "accent"),
    }
}

fn deployment_icon(deployment: &DeploymentRecord) -> &'static str {
    if deployment.pinned {
        return "starred-symbolic";
    }
    match deployment.kind {
        DeploymentKind::Factory => "emblem-default-symbolic",
        DeploymentKind::Manual => "camera-photo-symbolic",
        DeploymentKind::Automatic => "alarm-symbolic",
        DeploymentKind::AptPre => "document-revert-symbolic",
        DeploymentKind::AptPost => "software-update-available-symbolic",
        DeploymentKind::PreRollback => "document-revert-symbolic",
    }
}

fn empty_discovery() -> DiscoveryState {
    DiscoveryState {
        report: DiscoveryReport {
            deployment_schema_version: DEPLOYMENT_SCHEMA_VERSION,
            deployments: Vec::new(),
            issues: Vec::new(),
        },
        error: None,
    }
}

fn demo_layout() -> LayoutReport {
    LayoutReport {
        support: LayoutSupport::Supported,
        root_filesystem: Some("btrfs".into()),
        root_source: Some("/dev/nvme0n1p4".into()),
        issues: Vec::new(),
        mounts: Vec::new(),
    }
}

fn is_ext4(report: &LayoutReport) -> bool {
    report.support == LayoutSupport::OtherFilesystem
        && report.root_filesystem.as_deref() == Some("ext4")
}

fn svg_picture(svg: &'static [u8], width: i32, height: i32) -> gtk::Picture {
    let picture = gtk::Picture::builder()
        .width_request(width)
        .height_request(height)
        .content_fit(gtk::ContentFit::Contain)
        .can_shrink(true)
        .build();
    if let Ok(texture) = gdk::Texture::from_bytes(&glib::Bytes::from_static(svg)) {
        picture.set_paintable(Some(&texture));
    }
    picture
}

#[cfg(test)]
mod tests {
    use super::{classify_protection_health, ProtectionHealth};

    #[test]
    fn complete_system_home_and_automation_are_active() {
        assert_eq!(
            classify_protection_health(false, true, true, true, true, true),
            ProtectionHealth::Active
        );
    }

    #[test]
    fn any_missing_first_run_step_requires_setup() {
        assert_eq!(
            classify_protection_health(false, true, true, false, true, true),
            ProtectionHealth::SetupNeeded
        );
        assert_eq!(
            classify_protection_health(false, false, true, true, true, true),
            ProtectionHealth::SetupNeeded
        );
    }

    #[test]
    fn errors_and_unavailable_home_require_attention() {
        assert_eq!(
            classify_protection_health(true, true, true, true, true, true),
            ProtectionHealth::Attention
        );
        assert_eq!(
            classify_protection_health(false, true, false, false, true, false),
            ProtectionHealth::Attention
        );
    }
}
