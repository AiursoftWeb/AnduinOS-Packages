use std::rc::Rc;

use adw::prelude::*;
use gtk::{gdk, gio, glib};

use anduinos_timeback::layout::{self, LayoutReport, LayoutSupport};

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

pub fn build(app: &TimebackApplication) -> adw::ApplicationWindow {
    let demo = std::env::var_os("ANDUINOS_TIMEBACK_DEMO").is_some();
    let report = Rc::new(if demo {
        demo_layout()
    } else {
        layout::inspect_current()
    });

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
            widget: build_overview(&window, &toast_overlay, &report, demo).upcast(),
        },
        Page {
            name: "points",
            title: i18n("Recovery Points"),
            icon: "document-open-recent-symbolic",
            widget: build_recovery_points(&window, &report, demo).upcast(),
        },
        Page {
            name: "storage",
            title: i18n("Storage"),
            icon: "drive-harddisk-symbolic",
            widget: build_storage(&report, demo).upcast(),
        },
        Page {
            name: "activity",
            title: i18n("Activity"),
            icon: "view-list-symbolic",
            widget: build_activity(&report, demo).upcast(),
        },
        Page {
            name: "settings",
            title: i18n("Settings"),
            icon: "preferences-system-symbolic",
            widget: build_settings().upcast(),
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
    menu.append(Some(&i18n("About Timeback Machine")), Some("app.about"));
    let menu_button = gtk::MenuButton::builder()
        .icon_name("open-menu-symbolic")
        .menu_model(&menu)
        .build();
    let refresh_button = gtk::Button::builder()
        .icon_name("view-refresh-symbolic")
        .tooltip_text(i18n("Refresh system status"))
        .build();
    let toast = toast_overlay.clone();
    refresh_button.connect_clicked(move |_| {
        toast.add_toast(adw::Toast::new(&i18n("System status refreshed")));
    });

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

    window.set_content(Some(&split));
    window
}

fn build_unavailable_window(window: &adw::ApplicationWindow, report: &LayoutReport) {
    window.set_default_size(760, 620);

    let menu = gio::Menu::new();
    menu.append(Some(&i18n("About Timeback Machine")), Some("app.about"));
    let menu_button = gtk::MenuButton::builder()
        .icon_name("open-menu-symbolic")
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
    toasts: &adw::ToastOverlay,
    report: &LayoutReport,
    demo: bool,
) -> gtk::ScrolledWindow {
    let content = page_content();
    content.append(&overview_hero(window, toasts, report, demo));

    if demo {
        let banner = adw::Banner::builder()
            .title(i18n("Design preview — no system changes can be made"))
            .revealed(true)
            .build();
        content.append(&banner);
    } else if !report.is_supported() && !is_ext4(report) {
        content.append(&unsupported_banner(report));
    }

    let metrics = gtk::FlowBox::builder()
        .selection_mode(gtk::SelectionMode::None)
        .min_children_per_line(1)
        .max_children_per_line(3)
        .column_spacing(14)
        .row_spacing(14)
        .homogeneous(true)
        .build();
    let supported = report.is_supported() || demo;
    let values = if supported {
        [
            (
                "document-open-recent-symbolic",
                i18n("Latest point"),
                if demo {
                    i18n("Today, 14:32")
                } else {
                    i18n("Not created yet")
                },
                i18n("System recovery"),
            ),
            (
                "drive-harddisk-symbolic",
                i18n("Recovery storage"),
                if demo { i18n("8.4 GB") } else { i18n("Ready") },
                i18n("Estimated exclusive data"),
            ),
            (
                "security-high-symbolic",
                i18n("Boot safety"),
                i18n("Verified"),
                i18n("Kernel and initramfs"),
            ),
        ]
    } else {
        [
            (
                "dialog-warning-symbolic",
                i18n("Protection"),
                if is_ext4(report) {
                    i18n("Temporarily unavailable")
                } else {
                    i18n("Unavailable")
                },
                if is_ext4(report) {
                    i18n("Available on Btrfs installations")
                } else {
                    i18n("Btrfs layout required")
                },
            ),
            (
                "drive-harddisk-symbolic",
                i18n("Root filesystem"),
                report
                    .root_filesystem
                    .clone()
                    .unwrap_or_else(|| i18n("Unknown")),
                i18n("Detected locally"),
            ),
            (
                "security-medium-symbolic",
                i18n("System changes"),
                i18n("Disabled"),
                i18n("Your system is untouched"),
            ),
        ]
    };
    for (icon, label, value, detail) in values {
        metrics.insert(&metric_card(icon, &label, &value, &detail), -1);
    }
    content.append(&metrics);

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
                i18n("The system is ready for the manual recovery milestone.")
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

fn overview_hero(
    window: &adw::ApplicationWindow,
    toasts: &adw::ToastOverlay,
    report: &LayoutReport,
    demo: bool,
) -> gtk::FlowBox {
    let supported = report.is_supported() || demo;
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
            .label(if supported {
                i18n("Your system can travel back")
            } else if is_ext4(report) {
                i18n("Temporarily unavailable")
            } else {
                i18n("System recovery is unavailable")
            })
            .css_classes(["title-1", "hero-title"])
            .halign(gtk::Align::Start)
            .wrap(true)
            .xalign(0.0)
            .build(),
    );
    copy.append(
        &gtk::Label::builder()
            .label(if supported {
                i18n("Recovery points protect the operating system while your personal files stay in the present.")
            } else if is_ext4(report) {
                i18n("This AnduinOS installation uses ext4. Timeback Machine recovery points currently require Btrfs.")
            } else {
                i18n("This installation does not use the complete AnduinOS Btrfs recovery layout.")
            })
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
    let toast_overlay = toasts.clone();
    action.connect_clicked(move |_| {
        let dialog = adw::AlertDialog::builder()
            .heading(i18n("Recovery engine coming next"))
            .body(i18n("The storage contract and safety checks are ready. Creating recovery points will be enabled in TM-2 after the privileged backend is complete."))
            .close_response("close")
            .build();
        dialog.add_response("close", &i18n("Got it"));
        dialog.present(Some(&parent));
        toast_overlay.add_toast(adw::Toast::new(&i18n(
            "No snapshot was created in this design preview",
        )));
    });
    copy.append(&action);
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

fn build_recovery_points(
    window: &adw::ApplicationWindow,
    report: &LayoutReport,
    demo: bool,
) -> gtk::ScrolledWindow {
    let content = page_content();
    content.append(&section_heading(
        &i18n("Recovery Points"),
        &i18n("Return the operating system to a known-good moment"),
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
    } else {
        content.append(
            &adw::StatusPage::builder()
                .icon_name("document-open-recent-symbolic")
                .title(i18n("The timeline is ready"))
                .description(i18n(
                    "Recovery points will appear here after the snapshot backend is enabled.",
                ))
                .build(),
        );
    }
    wrap_page(content)
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

fn build_activity(report: &LayoutReport, demo: bool) -> adw::PreferencesPage {
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
    page.add(&group);
    page
}

fn build_settings() -> adw::PreferencesPage {
    let page = adw::PreferencesPage::builder()
        .title(i18n("Settings"))
        .icon_name("preferences-system-symbolic")
        .build();
    let automatic = adw::PreferencesGroup::builder()
        .title(i18n("Automatic Protection"))
        .description(i18n(
            "Automatic recovery points will become available after package-manager integration.",
        ))
        .build();
    automatic.add(&planned_row(
        &i18n("Before system updates"),
        &i18n("Create a recovery point before APT changes packages"),
    ));
    automatic.add(&planned_row(
        &i18n("After successful updates"),
        &i18n("Keep a verified post-update deployment"),
    ));
    page.add(&automatic);

    let retention = adw::PreferencesGroup::builder()
        .title(i18n("Retention"))
        .description(i18n(
            "Recovery point cleanup will remain conservative until low-space handling is ready.",
        ))
        .build();
    let policy = adw::ActionRow::builder()
        .title(i18n("Balanced"))
        .subtitle(i18n("Protect pinned and boot-critical recovery points"))
        .build();
    policy.add_prefix(
        &gtk::Image::builder()
            .icon_name("preferences-system-time-symbolic")
            .pixel_size(24)
            .build(),
    );
    policy.add_suffix(
        &gtk::Label::builder()
            .label(i18n("Planned"))
            .css_classes(["pill", "caption", "planned-badge"])
            .valign(gtk::Align::Center)
            .build(),
    );
    retention.add(&policy);
    page.add(&retention);
    page
}

fn planned_row(title: &str, subtitle: &str) -> adw::ActionRow {
    let row = adw::ActionRow::builder()
        .title(title)
        .subtitle(subtitle)
        .build();
    row.add_suffix(
        &gtk::Label::builder()
            .label(i18n("Planned"))
            .css_classes(["pill", "caption", "planned-badge"])
            .valign(gtk::Align::Center)
            .build(),
    );
    row
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

fn metric_card(icon: &str, label: &str, value: &str, detail: &str) -> gtk::Box {
    let card = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(6)
        .width_request(170)
        .css_classes(["timeback-card"])
        .build();
    card.append(
        &gtk::Image::builder()
            .icon_name(icon)
            .pixel_size(25)
            .halign(gtk::Align::Start)
            .css_classes(["accent"])
            .build(),
    );
    card.append(
        &gtk::Label::builder()
            .label(label)
            .css_classes(["caption", "dim-label"])
            .halign(gtk::Align::Start)
            .build(),
    );
    card.append(
        &gtk::Label::builder()
            .label(value)
            .css_classes(["title-3"])
            .halign(gtk::Align::Start)
            .wrap(true)
            .xalign(0.0)
            .build(),
    );
    card.append(
        &gtk::Label::builder()
            .label(detail)
            .css_classes(["caption", "dim-label"])
            .halign(gtk::Align::Start)
            .wrap(true)
            .xalign(0.0)
            .build(),
    );
    card
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
