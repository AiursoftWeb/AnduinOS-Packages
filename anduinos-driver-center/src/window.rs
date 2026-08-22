#![allow(deprecated)]

use std::cell::{Cell, RefCell};
use std::process::Command;
use std::rc::Rc;


use adw::prelude::*;
use gtk::gio;
use gtk::glib;
use gtk::glib::object::Cast;
use gtk::glib::prelude::ToValue;
use serde_json::Value;

use crate::application::DriverCenterApplication;
use crate::config;
use crate::core_scan::{
    self, AudioState, DriverOption, GraphicsScan, HardwareDevice, PrintingState, SystemScan,
    XboxStatus, XboxState,
};
use crate::firmware::{self, FirmwareSnapshot};
use crate::helper::{self, HelperResult};
use crate::i18n::{i18n, ni18n};
use crate::secureboot::{DkmsState, SecureBootState};

pub struct Ui {
    pub window: adw::ApplicationWindow,
    split: adw::OverlaySplitView,
    stack: gtk::Stack,
    device_list: gtk::ListBox,
    page_title: adw::WindowTitle,
    refresh_button: gtk::Button,
    sidebar_toggle: gtk::ToggleButton,
    rebuilding: Cell<bool>,
    selected: RefCell<String>,
}

impl Ui {
    pub fn refresh(self: &Rc<Self>) {
        self.refresh_button.set_sensitive(false);
        self.device_list.set_sensitive(false);
        self.show_loading();
        let (tx, rx) = async_channel::bounded(1);
        std::thread::spawn(move || {
            let scan = core_scan::scan_system();
            let firmware = firmware::snapshot();
            let _ = tx.send_blocking((scan, firmware));
        });
        let ui = Rc::clone(self);
        glib::spawn_future_local(async move {
            if let Ok((scan, firmware)) = rx.recv().await {
                ui.apply_scan(scan, firmware);
            }
        });
    }

    fn show_loading(&self) {
        while let Some(child) = self.stack.first_child() {
            self.stack.remove(&child);
        }
        let title = i18n("AnduinOS Driver Center");
        self.page_title.set_title(&title);
        let status = adw::StatusPage::builder()
            .title(i18n("Scanning for drivers"))
            .description(i18n("Checking hardware and Secure Boot status…"))
            .build();
        let spinner = gtk::Spinner::new();
        spinner.set_spinning(true);
        spinner.set_size_request(48, 48);
        status.set_child(Some(&spinner));
        self.stack.add_named(&status, Some("loading"));
        self.stack.set_visible_child_name("loading");
    }

    fn apply_scan(self: &Rc<Self>, scan: SystemScan, firmware: FirmwareSnapshot) {
        self.refresh_button.set_sensitive(true);
        self.device_list.set_sensitive(true);
        while let Some(child) = self.device_list.first_child() {
            self.device_list.remove(&child);
        }
        while let Some(child) = self.stack.first_child() {
            self.stack.remove(&child);
        }
        self.rebuilding.set(true);

        self.add_page(
            "home",
            "go-home-symbolic",
            &i18n("Home"),
            &i18n("System status"),
            home_page(self, &scan, &firmware),
        );
        for (index, device) in scan.graphics.devices.iter().enumerate() {
            let name = format!("graphics-{index}");
            self.add_page(
                &name,
                "video-display-symbolic",
                &device.title(),
                &device.vendor,
                graphics_page(self, device, &scan.secure_boot),
            );
        }
        let audio_subtitle = if scan.audio.ready() {
            i18n("Audio support ready")
        } else {
            i18n("Support needs attention")
        };
        self.add_page(
            "audio",
            "audio-card-symbolic",
            &i18n("Audio"),
            &audio_subtitle,
            audio_page(self, &scan.audio),
        );
        let printing_subtitle = printing_nav_subtitle(&scan.printing);
        self.add_page(
            "printing",
            "printer-symbolic",
            &i18n("Printers"),
            &printing_subtitle,
            printing_page(self, &scan.printing),
        );
        let xbox_subtitle = match scan.xbox.status {
            XboxStatus::Loaded | XboxStatus::Ready => i18n("xpadneo installed"),
            XboxStatus::NotInstalled => i18n("Optional Bluetooth driver"),
            _ => i18n("Support needs attention"),
        };
        self.add_page(
            "xbox",
            "input-gaming-symbolic",
            &i18n("Xbox Controller"),
            &xbox_subtitle,
            xbox_page(self, &scan.xbox, &scan.secure_boot),
        );
        if !scan.secure_boot.enforcement_inactive {
            let secure_subtitle = if scan.secure_boot.ready {
                i18n("Trust established")
            } else {
                i18n("Action required")
            };
            self.add_page(
                "secure-boot",
                "security-high-symbolic",
                &i18n("Secure Boot"),
                &secure_subtitle,
                secure_boot_page(self, &scan.secure_boot, &scan.dkms),
            );
        }
        let firmware_subtitle = firmware_nav_subtitle(&firmware);
        self.add_page(
            "firmware",
            "application-x-firmware-symbolic",
            &i18n("Device Firmware"),
            &firmware_subtitle,
            firmware_page(self, &firmware),
        );

        self.rebuilding.set(false);
        let wanted = self.selected.borrow().clone();
        select_named_row(&self.device_list, &wanted);
    }

    fn add_page(
        self: &Rc<Self>,
        name: &str,
        icon: &str,
        title: &str,
        subtitle: &str,
        page: gtk::Widget,
    ) {
        let row = device_row(icon, title, subtitle);
        row.set_widget_name(name);
        self.device_list.append(&row);
        self.stack.add_named(&page, Some(name));
    }

    fn select_page(&self, name: &str) {
        select_named_row(&self.device_list, name);
    }

    fn run_action(self: &Rc<Self>, button: &gtk::Button, arguments: Vec<String>) {
        self.run_action_with_success(button, arguments, None);
    }

    fn run_action_with_success(
        self: &Rc<Self>,
        button: &gtk::Button,
        arguments: Vec<String>,
        success_message: Option<String>,
    ) {
        if arguments.is_empty() {
            return;
        }
        button.set_sensitive(false);
        let original = button.label().unwrap_or_else(|| i18n("Apply").into());
        button.set_label(&i18n("Working…"));
        let (tx, rx) = async_channel::bounded(1);
        std::thread::spawn(move || {
            let args: Vec<&str> = arguments.iter().map(String::as_str).collect();
            let _ = tx.send_blocking(helper::run(&args));
        });
        let ui = Rc::clone(self);
        let button = button.clone();
        glib::spawn_future_local(async move {
            if let Ok(result) = rx.recv().await {
                button.set_label(&original);
                button.set_sensitive(true);
                let message = if result.ok {
                    success_message.unwrap_or_else(|| {
                        if result.message.is_empty() {
                            i18n("Driver changes completed. Restart may be required.")
                        } else {
                            result.message
                        }
                    })
                } else {
                    format!(
                        "{}{}",
                        i18n("Driver operation failed: "),
                        if result.message.is_empty() {
                            i18n("unknown error")
                        } else {
                            result.message
                        }
                    )
                };
                ui.alert(&message);
                if result.ok {
                    ui.refresh();
                }
            }
        });
    }

    fn alert(&self, message: &str) {
        let dialog = adw::AlertDialog::builder().heading(message).build();
        let ok = i18n("OK");
        dialog.add_response("ok", &ok);
        dialog.present(Some(&self.window));
    }

    fn confirm(
        self: &Rc<Self>,
        heading: &str,
        body: &str,
        action: &str,
        action_label: &str,
        on_confirm: impl Fn() + 'static,
    ) {
        let dialog = adw::AlertDialog::builder()
            .heading(heading)
            .body(body)
            .build();
        let cancel = i18n("Cancel");
        dialog.add_response("cancel", &cancel);
        dialog.add_response(action, action_label);
        dialog.set_response_appearance(action, adw::ResponseAppearance::Suggested);
        dialog.set_close_response("cancel");
        dialog.set_default_response(Some(action));
        let action = action.to_string();
        dialog.connect_response(None, move |_, response| {
            if response == action {
                on_confirm();
            }
        });
        dialog.present(Some(&self.window));
    }
}

fn select_named_row(list: &gtk::ListBox, name: &str) {
    let mut index = 0;
    while let Some(row) = list.row_at_index(index) {
        if row.widget_name() == name {
            list.select_row(Some(&row));
            return;
        }
        index += 1;
    }
    if let Some(first) = list.row_at_index(0) {
        list.select_row(Some(&first));
    }
}

fn device_row(icon: &str, title: &str, subtitle: &str) -> gtk::ListBoxRow {
    let row = gtk::ListBoxRow::new();
    let box_ = gtk::Box::new(gtk::Orientation::Horizontal, 12);
    box_.set_margin_top(10);
    box_.set_margin_bottom(10);
    box_.set_margin_start(10);
    box_.set_margin_end(10);
    let image = gtk::Image::from_icon_name(icon);
    image.set_pixel_size(28);
    let labels = gtk::Box::new(gtk::Orientation::Vertical, 2);
    let name = gtk::Label::new(Some(title));
    name.set_xalign(0.0);
    name.add_css_class("heading");
    name.set_ellipsize(gtk::pango::EllipsizeMode::End);
    let detail = gtk::Label::new(Some(subtitle));
    detail.set_xalign(0.0);
    detail.add_css_class("dim-label");
    detail.set_ellipsize(gtk::pango::EllipsizeMode::End);
    labels.append(&name);
    labels.append(&detail);
    box_.append(&image);
    box_.append(&labels);
    row.set_child(Some(&box_));
    row
}

fn pill(text: impl AsRef<str>, class: &str) -> gtk::Label {
    let label = gtk::Label::new(Some(text.as_ref()));
    label.add_css_class("status-pill");
    label.add_css_class(class);
    label.set_valign(gtk::Align::Center);
    label
}

fn warning_banner(title: &str, action_label: &str, on_click: impl Fn() + 'static) -> adw::Banner {
    let banner = adw::Banner::new(title);
    banner.set_button_label(Some(action_label));
    banner.set_revealed(true);
    banner.connect_button_clicked(move |_| on_click());
    banner
}

fn page_shell(
    title: impl AsRef<str>,
    description: impl AsRef<str>,
    artwork: Option<&str>,
    max_size: i32,
) -> (gtk::ScrolledWindow, gtk::Box) {
    let scroll = gtk::ScrolledWindow::new();
    scroll.set_policy(gtk::PolicyType::Never, gtk::PolicyType::Automatic);
    let content = gtk::Box::new(gtk::Orientation::Vertical, 18);
    content.set_margin_top(32);
    content.set_margin_bottom(32);
    content.set_margin_start(24);
    content.set_margin_end(24);
    let hero = gtk::Box::new(gtk::Orientation::Horizontal, 24);
    let hero_text = gtk::Box::new(gtk::Orientation::Vertical, 10);
    hero_text.set_hexpand(true);
    hero_text.set_valign(gtk::Align::Center);
    let heading = gtk::Label::new(Some(title.as_ref()));
    heading.add_css_class("title-1");
    heading.set_xalign(0.0);
    heading.set_wrap(true);
    let intro = gtk::Label::new(Some(description.as_ref()));
    intro.add_css_class("dim-label");
    intro.set_xalign(0.0);
    intro.set_wrap(true);
    hero_text.append(&heading);
    hero_text.append(&intro);
    hero.append(&hero_text);
    if let Some(name) = artwork {
        if let Some(path) = config::illustration(name) {
            let picture = gtk::Picture::for_filename(path);
            picture.set_content_fit(gtk::ContentFit::Contain);
            picture.set_size_request(112, 112);
            picture.set_halign(gtk::Align::End);
            picture.set_valign(gtk::Align::Center);
            hero.append(&picture);
        }
    }
    content.append(&hero);
    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(max_size);
    clamp.set_tightening_threshold(if max_size > 800 { 760 } else { 500 });
    clamp.set_child(Some(&content));
    scroll.set_child(Some(&clamp));
    (scroll, content)
}

fn state_row(
    group: &adw::PreferencesGroup,
    title: impl AsRef<str>,
    subtitle: impl AsRef<str>,
    good: Option<bool>,
    action: Option<(String, Box<dyn Fn(&gtk::Button)>)>,
) {
    let row = adw::ActionRow::builder()
        .title(title.as_ref())
        .subtitle(subtitle.as_ref())
        .build();
    match good {
        Some(true) => row.add_suffix(&pill(i18n("Ready"), "success-pill")),
        Some(false) => row.add_suffix(&pill(i18n("Needs attention"), "warning-pill")),
        None => {}
    }
    if let Some((label, callback)) = action {
        let button = gtk::Button::with_label(&label);
        button.set_valign(gtk::Align::Center);
        button.add_css_class("suggested-action");
        button.connect_clicked(move |button| callback(button));
        row.add_suffix(&button);
    }
    group.add(&row);
}

fn count_label(singular: &str, plural: &str, n: usize) -> String {
    ni18n(singular, plural, n as u32).replacen("%d", &n.to_string(), 1)
}

fn printing_nav_subtitle(state: &PrintingState) -> String {
    if !state.service_running {
        return if state.startup_enabled {
            i18n("Printing service stopped")
        } else {
            i18n("Printing support disabled.")
        };
    }
    if state.missing_required() {
        return i18n("Support needs attention");
    }
    if !state.disabled_printers.is_empty() {
        return i18n("Some queues are paused");
    }
    if state.printers.is_empty() {
        return i18n("No printers configured");
    }
    count_label(
        "%d printer configured",
        "%d printers configured",
        state.printers.len(),
    )
}

fn firmware_nav_subtitle(state: &FirmwareSnapshot) -> String {
    if state.error.is_some() {
        return i18n("Support needs attention");
    }
    let updates = state.updates().len();
    if updates > 0 {
        return count_label(
            "%d firmware update available",
            "%d firmware updates available",
            updates,
        );
    }
    if state.devices.is_empty() {
        i18n("No supported devices")
    } else {
        i18n("Firmware is up to date")
    }
}

fn version_summary(option: &DriverOption) -> String {
    let mut details = vec![option.package.clone()];
    if let Some(version) = &option.installed_version {
        details.push(format!("{}: {version}", i18n("Installed")));
    }
    if let Some(version) = &option.candidate_version {
        details.push(format!("{}: {version}", i18n("Available")));
    }
    details.join(" · ")
}

fn recommended_pairs(scan: &GraphicsScan) -> Vec<(&HardwareDevice, &DriverOption)> {
    scan.devices
        .iter()
        .filter_map(|device| {
            device
                .options
                .iter()
                .find(|option| option.recommended)
                .map(|option| (device, option))
        })
        .collect()
}

fn home_page(ui: &Rc<Ui>, scan: &SystemScan, firmware: &FirmwareSnapshot) -> gtk::Widget {
    let scroll = gtk::ScrolledWindow::new();
    scroll.set_policy(gtk::PolicyType::Never, gtk::PolicyType::Automatic);
    let content = gtk::Box::new(gtk::Orientation::Vertical, 24);
    content.set_margin_top(24);
    content.set_margin_bottom(32);
    content.set_margin_start(24);
    content.set_margin_end(24);
    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(980);
    clamp.set_tightening_threshold(760);
    clamp.set_child(Some(&content));
    scroll.set_child(Some(&clamp));

    let recommendations = recommended_pairs(&scan.graphics);
    let missing: Vec<_> = recommendations
        .iter()
        .copied()
        .filter(|(_, option)| !option.installed)
        .collect();
    let updates: Vec<_> = recommendations
        .iter()
        .copied()
        .filter(|(_, option)| option.update_available)
        .collect();
    let waiting: Vec<_> = recommendations
        .iter()
        .copied()
        .filter(|(device, option)| {
            option.installed
                && !option.update_available
                && !option.active
                && device.driver_state_known
                && !device
                    .active_driver
                    .as_deref()
                    .is_some_and(|driver| driver.to_lowercase().replace('_', "-").starts_with("nvidia"))
        })
        .collect();
    let unhealthy: Vec<_> = scan
        .graphics
        .devices
        .iter()
        .filter(|device| device.active_driver_healthy == Some(false))
        .collect();

    let (badge_text, badge_class, heading, description, action) =
        if let Some(error) = &scan.graphics.error {
            (
                i18n("Action required"),
                "warning-pill",
                i18n("Driver status"),
                format!("{}{error}", i18n("Driver operation failed: ")),
                Some((i18n("Scan again"), "refresh".to_string())),
            )
        } else if (!missing.is_empty() || !updates.is_empty()) && !scan.secure_boot.ready {
            (
                i18n("Action required"),
                "warning-pill",
                i18n("Secure Boot"),
                i18n("Secure Boot status or trust must be resolved before installing a third-party driver."),
                Some((i18n("Secure Boot"), "secure-boot".into())),
            )
        } else if let Some((device, _)) = missing.first() {
            (
                i18n("Recommended"),
                "recommended-pill",
                device.title(),
                i18n("Choose the driver used by this device. AnduinOS marks the hardware-tested recommendation."),
                Some((i18n("Apply Changes"), "install-recommended".into())),
            )
        } else if let Some((device, option)) = updates.first() {
            (
                i18n("Update available"),
                "recommended-pill",
                device.title(),
                version_summary(option),
                Some((i18n("Apply Changes"), "install-recommended".into())),
            )
        } else if let Some((device, _)) = waiting.first() {
            (
                i18n("Reboot Required"),
                "warning-pill",
                device.title(),
                i18n("Driver changes completed. Restart may be required."),
                None,
            )
        } else if let Some(device) = unhealthy.first() {
            (
                i18n("Support needs attention"),
                "warning-pill",
                device.title(),
                device
                    .active_driver_error
                    .clone()
                    .unwrap_or_else(|| i18n("Support needs attention")),
                Some((
                    i18n("Available drivers"),
                    format!(
                        "graphics-{}",
                        scan.graphics
                            .devices
                            .iter()
                            .position(|item| item.identifier == device.identifier)
                            .unwrap_or(0)
                    ),
                )),
            )
        } else {
            (
                i18n("Ready"),
                "success-pill",
                scan.graphics
                    .devices
                    .first()
                    .map(HardwareDevice::title)
                    .unwrap_or_else(|| i18n("Driver status")),
                i18n("No additional drivers are needed."),
                None,
            )
        };

    let hero = gtk::Box::new(gtk::Orientation::Horizontal, 12);
    hero.add_css_class("card");
    let artwork = gtk::Image::from_icon_name(config::APP_ID);
    artwork.set_pixel_size(144);
    artwork.set_size_request(260, 180);
    artwork.set_margin_start(20);
    artwork.set_margin_end(20);
    artwork.set_margin_top(12);
    artwork.set_margin_bottom(12);
    hero.append(&artwork);
    let details = gtk::Box::new(gtk::Orientation::Vertical, 8);
    details.set_margin_start(20);
    details.set_margin_end(28);
    details.set_margin_top(28);
    details.set_margin_bottom(28);
    details.set_valign(gtk::Align::Center);
    details.set_hexpand(true);
    let badge = pill(&badge_text, badge_class);
    badge.set_halign(gtk::Align::Start);
    details.append(&badge);
    let heading_label = gtk::Label::new(Some(&heading));
    heading_label.add_css_class("title-1");
    heading_label.set_xalign(0.0);
    heading_label.set_wrap(true);
    details.append(&heading_label);
    let intro = gtk::Label::new(Some(&description));
    intro.add_css_class("dim-label");
    intro.set_xalign(0.0);
    intro.set_wrap(true);
    details.append(&intro);
    if let Some((label, target)) = action {
        let button = gtk::Button::with_label(&label);
        button.set_halign(gtk::Align::Start);
        button.set_margin_top(8);
        if target == "install-recommended" {
            button.add_css_class("suggested-action");
            button.add_css_class("pill");
            let ui = Rc::clone(ui);
            let button_ref = button.clone();
            button.connect_clicked(move |_| confirm_recommended_install(&ui, &button_ref));
        } else if target == "refresh" {
            let ui = Rc::clone(ui);
            button.connect_clicked(move |_| ui.refresh());
        } else {
            let ui = Rc::clone(ui);
            button.connect_clicked(move |_| ui.select_page(&target));
        }
        details.append(&button);
    }
    hero.append(&details);
    content.append(&hero);

    let section = gtk::Label::new(Some(&i18n("System status")));
    section.add_css_class("title-2");
    section.set_xalign(0.0);
    content.append(&section);

    let cards = gtk::FlowBox::builder()
        .selection_mode(gtk::SelectionMode::None)
        .max_children_per_line(2)
        .min_children_per_line(1)
        .column_spacing(16)
        .row_spacing(16)
        .homogeneous(true)
        .build();

    let (graphics_state, graphics_subtitle, graphics_class, graphics_target) =
        if scan.graphics.error.is_some() {
            (
                i18n("Action required"),
                i18n("Not detected"),
                "warning-pill",
                None,
            )
        } else if let Some((device, _)) = missing.first() {
            (
                i18n("Recommended"),
                device.title(),
                "recommended-pill",
                Some(graphics_name(&scan.graphics, device)),
            )
        } else if let Some((device, option)) = updates.first() {
            (
                i18n("Update available"),
                version_summary(option),
                "recommended-pill",
                Some(graphics_name(&scan.graphics, device)),
            )
        } else if let Some((device, _)) = waiting.first() {
            (
                i18n("Reboot Required"),
                device.title(),
                "warning-pill",
                Some(graphics_name(&scan.graphics, device)),
            )
        } else if let Some(device) = unhealthy.first() {
            (
                i18n("Support needs attention"),
                device.title(),
                "warning-pill",
                Some(graphics_name(&scan.graphics, device)),
            )
        } else {
            (
                i18n("Ready"),
                scan.graphics
                    .devices
                    .first()
                    .map(HardwareDevice::title)
                    .unwrap_or_else(|| i18n("No additional drivers are needed.")),
                "success-pill",
                scan.graphics
                    .devices
                    .first()
                    .map(|_| "graphics-0".to_string()),
            )
        };
    cards.append(&overview_card(
        ui,
        "video-display-symbolic",
        i18n("Available drivers"),
        graphics_state,
        graphics_subtitle,
        graphics_class,
        graphics_target,
    ));
    cards.append(&overview_card(
        ui,
        "audio-card-symbolic",
        i18n("Audio Support"),
        if scan.audio.ready() {
            i18n("Ready")
        } else {
            i18n("Needs attention")
        },
        if scan.audio.ready() {
            i18n("Audio support ready")
        } else {
            i18n("Support needs attention")
        },
        if scan.audio.ready() {
            "success-pill"
        } else {
            "warning-pill"
        },
        Some("audio".into()),
    ));
    let printing_ready = scan.printing.service_running
        && !scan.printing.missing_required()
        && scan.printing.disabled_printers.is_empty();
    let (printing_state, printing_subtitle, printing_class) = if !scan.printing.startup_enabled {
        (
            i18n("Disabled"),
            i18n("Printing support disabled."),
            "installed-pill",
        )
    } else if printing_ready {
        (
            i18n("Ready"),
            if scan.printing.printers.is_empty() {
                i18n("No printers configured")
            } else {
                printing_nav_subtitle(&scan.printing)
            },
            "success-pill",
        )
    } else {
        (
            i18n("Needs attention"),
            i18n("Support needs attention"),
            "warning-pill",
        )
    };
    cards.append(&overview_card(
        ui,
        "printer-symbolic",
        i18n("Printing Support"),
        printing_state,
        printing_subtitle,
        printing_class,
        Some("printing".into()),
    ));
    let xbox_ready = matches!(scan.xbox.status, XboxStatus::Loaded | XboxStatus::Ready);
    let xbox_optional = scan.xbox.status == XboxStatus::NotInstalled;
    cards.append(&overview_card(
        ui,
        "input-gaming-symbolic",
        i18n("Xbox Controller Support"),
        if xbox_ready {
            i18n("Ready")
        } else if xbox_optional {
            i18n("Not installed")
        } else {
            i18n("Needs attention")
        },
        if xbox_ready {
            i18n("xpadneo installed")
        } else if xbox_optional {
            i18n("Optional Bluetooth driver")
        } else {
            i18n("Support needs attention")
        },
        if xbox_ready {
            "success-pill"
        } else if xbox_optional {
            "installed-pill"
        } else {
            "warning-pill"
        },
        Some("xbox".into()),
    ));
    if !scan.secure_boot.enforcement_inactive {
        cards.append(&overview_card(
            ui,
            "security-high-symbolic",
            i18n("Secure Boot"),
            if scan.secure_boot.ready {
                i18n("Trusted")
            } else {
                i18n("Action required")
            },
            if scan.secure_boot.ready {
                i18n("Trust established")
            } else {
                i18n("Support needs attention")
            },
            if scan.secure_boot.ready {
                "success-pill"
            } else {
                "warning-pill"
            },
            Some("secure-boot".into()),
        ));
    }
    let (firmware_state, firmware_subtitle, firmware_class) = firmware_card_state(firmware);
    cards.append(&overview_card(
        ui,
        "application-x-firmware-symbolic",
        i18n("Device Firmware"),
        firmware_state,
        firmware_subtitle,
        firmware_class,
        Some("firmware".into()),
    ));
    content.append(&cards);

    if !recommendations.is_empty() {
        let group = adw::PreferencesGroup::builder()
            .title(i18n("Available drivers"))
            .build();
        for (device, option) in recommendations {
            let row = adw::ActionRow::builder()
                .title(device.title())
                .subtitle(version_summary(option))
                .build();
            let icon = gtk::Image::from_icon_name("video-display-symbolic");
            icon.set_pixel_size(24);
            row.add_prefix(&icon);
            let (state, class) = if !option.installed || option.update_available {
                (i18n("Recommended"), "recommended-pill")
            } else if waiting
                .iter()
                .any(|(item, _)| item.identifier == device.identifier)
            {
                (i18n("Reboot Required"), "warning-pill")
            } else {
                (i18n("Ready"), "success-pill")
            };
            row.add_suffix(&pill(state, class));
            group.add(&row);
        }
        content.append(&group);
    }

    let actions = adw::PreferencesGroup::builder()
        .title(i18n("Driver status"))
        .build();
    let refresh_row = adw::ActionRow::builder()
        .title(i18n("Check for Driver Updates"))
        .subtitle(i18n(
            "Refresh software sources and compare the recommended driver version.",
        ))
        .build();
    let refresh_icon = gtk::Image::from_icon_name("view-refresh-symbolic");
    refresh_icon.set_pixel_size(24);
    refresh_row.add_prefix(&refresh_icon);
    let refresh = gtk::Button::with_label(&i18n("Scan again"));
    refresh.set_valign(gtk::Align::Center);
    let ui_refresh = Rc::clone(ui);
    refresh.connect_clicked(move |button| {
        ui_refresh.run_action_with_success(
            button,
            vec!["refresh-driver-info".into()],
            Some(i18n("Driver information updated.")),
        );
    });
    refresh_row.add_suffix(&refresh);
    actions.add(&refresh_row);

    let install_row = adw::ActionRow::builder()
        .title("ubuntu-drivers install")
        .subtitle(i18n(
            "AnduinOS will update software sources and install the drivers recommended for this hardware.",
        ))
        .build();
    let install_icon = gtk::Image::from_icon_name("system-run-symbolic");
    install_icon.set_pixel_size(24);
    install_row.add_prefix(&install_icon);
    if scan.secure_boot.ready {
        let install = gtk::Button::with_label(&i18n("Apply Changes"));
        install.add_css_class("suggested-action");
        install.set_valign(gtk::Align::Center);
        let ui_install = Rc::clone(ui);
        let install_ref = install.clone();
        install.connect_clicked(move |_| confirm_recommended_install(&ui_install, &install_ref));
        install_row.add_suffix(&install);
    } else {
        let secure = gtk::Button::with_label(&i18n("Secure Boot"));
        secure.set_valign(gtk::Align::Center);
        let ui_secure = Rc::clone(ui);
        secure.connect_clicked(move |_| ui_secure.select_page("secure-boot"));
        install_row.add_suffix(&secure);
    }
    actions.add(&install_row);
    content.append(&actions);
    scroll.upcast()
}

fn graphics_name(scan: &GraphicsScan, device: &HardwareDevice) -> String {
    format!(
        "graphics-{}",
        scan.devices
            .iter()
            .position(|item| item.identifier == device.identifier)
            .unwrap_or(0)
    )
}

fn firmware_card_state(state: &FirmwareSnapshot) -> (String, String, &'static str) {
    if let Some(error) = &state.error {
        return (
            i18n("Needs attention"),
            error.clone(),
            "warning-pill",
        );
    }
    let updates = state.updates().len();
    if updates > 0 {
        return (
            i18n("Update available"),
            count_label(
                "%d firmware update available",
                "%d firmware updates available",
                updates,
            ),
            "recommended-pill",
        );
    }
    if state.devices.is_empty() {
        (
            i18n("Not detected"),
            i18n("No supported firmware devices"),
            "installed-pill",
        )
    } else {
        (
            i18n("Ready"),
            count_label(
                "%d device is up to date",
                "%d devices are up to date",
                state.devices.len(),
            ),
            "success-pill",
        )
    }
}

fn confirm_recommended_install(ui: &Rc<Ui>, button: &gtk::Button) {
    let heading = i18n("Apply Changes");
    let body = i18n(
        "AnduinOS will update software sources and install the drivers recommended for this hardware.",
    );
    let apply = i18n("Apply");
    let ui = Rc::clone(ui);
    let button = button.clone();
    ui.clone().confirm(&heading, &body, "install", &apply, move || {
        ui.run_action(&button, vec!["install-recommended".into()]);
    });
}

fn overview_card(
    ui: &Rc<Ui>,
    icon: &str,
    title: impl AsRef<str>,
    state: impl AsRef<str>,
    subtitle: impl AsRef<str>,
    class: &str,
    target: Option<String>,
) -> gtk::Widget {
    let content = gtk::Box::new(gtk::Orientation::Horizontal, 14);
    content.set_margin_start(18);
    content.set_margin_end(18);
    content.set_margin_top(16);
    content.set_margin_bottom(16);
    let image = gtk::Image::from_icon_name(icon);
    image.set_pixel_size(28);
    content.append(&image);
    let labels = gtk::Box::new(gtk::Orientation::Vertical, 4);
    labels.set_hexpand(true);
    let title_row = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    let heading = gtk::Label::new(Some(title.as_ref()));
    heading.add_css_class("heading");
    heading.set_xalign(0.0);
    heading.set_hexpand(true);
    title_row.append(&heading);
    title_row.append(&pill(state, class));
    labels.append(&title_row);
    let detail = gtk::Label::new(Some(subtitle.as_ref()));
    detail.add_css_class("dim-label");
    detail.set_xalign(0.0);
    detail.set_wrap(true);
    labels.append(&detail);
    content.append(&labels);
    if let Some(target) = target {
        let arrow = gtk::Image::from_icon_name("go-next-symbolic");
        arrow.add_css_class("dim-label");
        content.append(&arrow);
        let button = gtk::Button::new();
        button.set_has_frame(false);
        button.add_css_class("card");
        button.add_css_class("overview-card");
        button.set_child(Some(&content));
        let ui = Rc::clone(ui);
        button.connect_clicked(move |_| ui.select_page(&target));
        button.upcast()
    } else {
        let card = gtk::Box::new(gtk::Orientation::Vertical, 0);
        card.add_css_class("card");
        card.append(&content);
        card.upcast()
    }
}

fn graphics_page(ui: &Rc<Ui>, device: &HardwareDevice, secure_boot: &SecureBootState) -> gtk::Widget {
    let artwork = if device.vendor.to_lowercase().contains("nvidia") {
        Some("nvidia.svg")
    } else {
        None
    };
    let (scroll, content) = page_shell(
        device.title(),
        i18n("Choose the driver used by this device. AnduinOS marks the hardware-tested recommendation."),
        artwork,
        650,
    );
    if !secure_boot.ready {
        let ui = Rc::clone(ui);
        content.append(&warning_banner(
            &i18n("Secure Boot status or trust must be resolved before installing a third-party driver."),
            &i18n("Secure Boot"),
            move || ui.select_page("secure-boot"),
        ));
    }
    let group = adw::PreferencesGroup::builder()
        .title(i18n("Available drivers"))
        .build();
    let selected = Rc::new(RefCell::new(
        device
            .options
            .iter()
            .find(|option| option.active)
            .or_else(|| device.options.iter().find(|option| option.recommended))
            .map(|option| option.package.clone()),
    ));
    let apply = gtk::Button::with_label(&i18n("Apply Changes"));
    apply.add_css_class("suggested-action");
    apply.set_sensitive(false);
    let active_package = device
        .options
        .iter()
        .find(|option| option.active)
        .map(|option| option.package.clone());
    let mut group_source: Option<gtk::CheckButton> = None;
    let primary: Vec<_> = device
        .options
        .iter()
        .filter(|option| option.installed || option.recommended || option.builtin)
        .collect();
    let advanced: Vec<_> = device
        .options
        .iter()
        .filter(|option| !(option.installed || option.recommended || option.builtin))
        .collect();

    let add_option = |group: &adw::PreferencesGroup,
                      option: &DriverOption,
                      group_source: &mut Option<gtk::CheckButton>| {
        let mut traits = vec![if option.free {
            i18n("open source")
        } else {
            i18n("proprietary")
        }];
        if option.builtin {
            traits.push(i18n("built in"));
        }
        let row = adw::ActionRow::builder()
            .title(&option.package)
            .subtitle(traits.join(" · "))
            .build();
        let check = gtk::CheckButton::new();
        if let Some(source) = group_source {
            check.set_group(Some(source));
        } else {
            *group_source = Some(check.clone());
        }
        if selected.borrow().as_deref() == Some(option.package.as_str()) {
            check.set_active(true);
        }
        let package = option.package.clone();
        let selected = Rc::clone(&selected);
        let apply_btn = apply.clone();
        let ready = secure_boot.ready;
        let active = active_package.clone();
        check.connect_toggled(move |check| {
            if check.is_active() {
                selected.replace(Some(package.clone()));
                apply_btn.set_sensitive(ready && Some(package.as_str()) != active.as_deref());
            }
        });
        row.add_prefix(&check);
        if option.active {
            row.add_suffix(&pill(i18n("In use"), "in-use-pill"));
        } else {
            if option.installed {
                row.add_suffix(&pill(i18n("Installed"), "installed-pill"));
            }
            if option.recommended {
                row.add_suffix(&pill(i18n("Recommended"), "recommended-pill"));
            }
        }
        group.add(&row);
    };

    for option in &primary {
        add_option(&group, option, &mut group_source);
    }
    content.append(&group);
    if !device.driver_state_known {
        let ui = Rc::clone(ui);
        content.append(&warning_banner(
            &format!("{}: {}", i18n("Kernel module"), i18n("Not detected")),
            &i18n("Scan again"),
            move || ui.refresh(),
        ));
    } else if device
        .active_driver
        .as_deref()
        .is_some_and(|driver| driver.to_lowercase().replace('_', "-").starts_with("nvidia"))
        && device.active_driver_healthy == Some(false)
    {
        let detail = device
            .active_driver_error
            .clone()
            .unwrap_or_else(|| "nvidia-smi".into());
        let title = format!(
            "{}: nvidia · {}{detail}",
            i18n("Kernel module"),
            i18n("Driver operation failed: ")
        );
        if let Some(package) = active_package.clone() {
            let ui = Rc::clone(ui);
            let apply_btn = apply.clone();
            content.append(&warning_banner(
                &title,
                &i18n("Repair & Reinstall"),
                move || ui.run_action(&apply_btn, vec!["repair-nvidia".into(), package.clone()]),
            ));
        } else {
            let apply_btn = apply.clone();
            content.append(&warning_banner(&title, &i18n("Apply Changes"), move || {
                apply_btn.emit_clicked();
            }));
        }
    }

    if !advanced.is_empty() {
        let wrap = adw::PreferencesGroup::new();
        let expander = adw::ExpanderRow::builder()
            .title(i18n("Advanced driver versions"))
            .subtitle(i18n("Older, newer, and server-oriented packages"))
            .build();
        wrap.add(&expander);
        content.append(&wrap);
        let advanced_group = adw::PreferencesGroup::new();
        for option in advanced {
            add_option(&advanced_group, option, &mut group_source);
        }
        expander.add_row(&advanced_group);
    }

    let ui = Rc::clone(ui);
    apply.connect_clicked(move |button| {
        if let Some(package) = selected.borrow().clone() {
            ui.run_action(button, vec!["install".into(), package]);
        }
    });
    let footer = gtk::Box::new(gtk::Orientation::Horizontal, 12);
    footer.set_margin_top(8);
    let status = gtk::Label::new(Some(&i18n("Select another driver to apply changes.")));
    status.add_css_class("dim-label");
    status.set_xalign(0.0);
    status.set_hexpand(true);
    footer.append(&status);
    footer.append(&apply);
    content.append(&footer);
    scroll.upcast()
}

fn audio_page(ui: &Rc<Ui>, state: &AudioState) -> gtk::Widget {
    let (scroll, content) = page_shell(
        i18n("Audio Support"),
        i18n("AnduinOS provides Intel SOF firmware and ALSA UCM profiles for reliable audio initialization and routing."),
        Some("audio.svg"),
        650,
    );
    let packages = adw::PreferencesGroup::builder()
        .title(i18n("Support packages"))
        .build();
    let action = if state.packages_installed() {
        vec!["repair-audio".to_string()]
    } else {
        vec!["install-audio".to_string()]
    };
    let label = if state.packages_installed() {
        i18n("Repair & Reinstall")
    } else {
        i18n("Install Audio Support")
    };
    let add_audio_action = |installed: bool| -> Option<(String, Box<dyn Fn(&gtk::Button)>)> {
        if installed {
            return None;
        }
        let ui = Rc::clone(ui);
        let action = action.clone();
        Some((
            label.clone(),
            Box::new(move |button| ui.run_action(button, action.clone())),
        ))
    };
    state_row(
        &packages,
        i18n("Intel SOF firmware"),
        state
            .sof_package
            .version
            .as_deref()
            .unwrap_or(&i18n("Not installed")),
        Some(state.sof_package.installed),
        add_audio_action(state.sof_package.installed),
    );
    state_row(
        &packages,
        i18n("ALSA UCM profiles"),
        state
            .ucm_package
            .version
            .as_deref()
            .unwrap_or(&i18n("Not installed")),
        Some(state.ucm_package.installed),
        add_audio_action(state.ucm_package.installed),
    );
    content.append(&packages);

    let runtime = adw::PreferencesGroup::builder()
        .title(i18n("Runtime status"))
        .build();
    let repair = |missing: bool| -> Option<(String, Box<dyn Fn(&gtk::Button)>)> {
        if !missing {
            return None;
        }
        let ui = Rc::clone(ui);
        let action = action.clone();
        Some((
            i18n("Repair & Reinstall"),
            Box::new(move |button| ui.run_action(button, action.clone())),
        ))
    };
    state_row(
        &runtime,
        i18n("SOF firmware files"),
        if state.firmware_present {
            i18n("Available")
        } else {
            i18n("Missing")
        },
        Some(state.firmware_present),
        repair(!state.firmware_present),
    );
    state_row(
        &runtime,
        i18n("UCM configuration files"),
        if state.ucm_profiles_present {
            i18n("Available")
        } else {
            i18n("Missing")
        },
        Some(state.ucm_profiles_present),
        repair(!state.ucm_profiles_present),
    );
    state_row(
        &runtime,
        i18n("SOF kernel modules"),
        if state.sof_modules.is_empty() {
            i18n("Not currently loaded")
        } else {
            state.sof_modules.join(", ")
        },
        if state.sof_modules.is_empty() {
            None
        } else {
            Some(true)
        },
        None,
    );
    state_row(
        &runtime,
        i18n("Active audio drivers"),
        if state.active_drivers.is_empty() {
            i18n("Not detected")
        } else {
            state.active_drivers.join(", ")
        },
        if state.active_drivers.is_empty() {
            None
        } else {
            Some(true)
        },
        None,
    );
    content.append(&runtime);
    scroll.upcast()
}

fn printing_page(ui: &Rc<Ui>, state: &PrintingState) -> gtk::Widget {
    let (scroll, content) = page_shell(
        i18n("Printing Support"),
        i18n("Inspect the local print service, configured queues, and the packages that provide modern and legacy printer support."),
        Some("printer.svg"),
        650,
    );
    let availability = adw::PreferencesGroup::new();
    let enable = adw::SwitchRow::builder()
        .title(i18n("Enable Printing Support"))
        .subtitle(i18n("Allow local, network, and USB printing services to run."))
        .active(state.startup_enabled)
        .build();
    let ui_switch = Rc::clone(ui);
    enable.connect_active_notify(move |row| {
        row.set_sensitive(false);
        let enabled = row.is_active();
        ui_switch.run_action(
            &gtk::Button::new(),
            vec![
                "set-printing-enabled".into(),
                if enabled { "true" } else { "false" }.into(),
            ],
        );
    });
    availability.add(&enable);
    content.append(&availability);
    if state.service_running {
        let add = gtk::Button::with_label(&i18n("Add Printer"));
        add.add_css_class("suggested-action");
        add.set_halign(gtk::Align::End);
        add.connect_clicked(|_| {
            let _ = Command::new("gnome-control-center").arg("printers").spawn();
        });
        content.append(&add);
    }
    let overview = adw::PreferencesGroup::builder()
        .title(i18n("System status"))
        .build();
    let cups_action = if state.startup_enabled && !state.service_running {
        let ui = Rc::clone(ui);
        Some((
            i18n("Enable Printing Support"),
            Box::new(move |button: &gtk::Button| {
                ui.run_action(button, vec!["set-printing-enabled".into(), "true".into()]);
            }) as Box<dyn Fn(&gtk::Button)>,
        ))
    } else {
        None
    };
    state_row(
        &overview,
        i18n("CUPS service"),
        if state.service_running {
            i18n("Running")
        } else {
            i18n("Stopped")
        },
        if state.startup_enabled {
            Some(state.service_running)
        } else {
            None
        },
        cups_action,
    );
    state_row(
        &overview,
        i18n("Start at boot"),
        if state.startup_enabled {
            i18n("Enabled")
        } else {
            i18n("Disabled")
        },
        if state.startup_enabled {
            Some(true)
        } else {
            None
        },
        None,
    );
    state_row(
        &overview,
        i18n("Configured printers"),
        count_label(
            "%d configured printer",
            "%d configured printers",
            state.printers.len(),
        ),
        if state.printers.is_empty() {
            None
        } else {
            Some(true)
        },
        None,
    );
    state_row(
        &overview,
        i18n("Default printer"),
        state
            .default_printer
            .as_deref()
            .unwrap_or(&i18n("Not set")),
        if state.default_printer.is_some() {
            Some(true)
        } else {
            None
        },
        None,
    );
    let (queue_summary, queue_good) = if state.printers.is_empty() {
        (i18n("No configured queues"), None)
    } else if state.disabled_printers.is_empty() {
        (i18n("All queues enabled"), Some(true))
    } else {
        (
            count_label(
                "%d queue paused",
                "%d queues paused",
                state.disabled_printers.len(),
            ),
            Some(false),
        )
    };
    let resume = if queue_good == Some(false) {
        let ui = Rc::clone(ui);
        Some((
            i18n("Apply Changes"),
            Box::new(move |button: &gtk::Button| {
                ui.run_action(button, vec!["resume-print-queues".into()]);
            }) as Box<dyn Fn(&gtk::Button)>,
        ))
    } else {
        None
    };
    state_row(
        &overview,
        i18n("Print queues"),
        queue_summary,
        queue_good,
        resume,
    );
    content.append(&overview);

    for (title, description, packages, required) in [
        (
            i18n("Core printing"),
            i18n("Required for the local print service and command-line clients."),
            &state.core_packages,
            true,
        ),
        (
            i18n("Driverless printing"),
            i18n("Modern IPP drivers, document filters, and capability tools."),
            &state.driverless_packages,
            true,
        ),
        (
            i18n("Network discovery"),
            i18n("Automatic discovery of printers advertised on the local network."),
            &state.discovery_packages,
            false,
        ),
        (
            i18n("Optional compatibility"),
            i18n("USB IPP, administrative authorization, legacy drivers, and network scanning."),
            &state.optional_packages,
            false,
        ),
    ] {
        let group = adw::PreferencesGroup::builder()
            .title(title)
            .description(description)
            .build();
        for package in packages {
            let action = if required && !package.installed {
                let ui = Rc::clone(ui);
                Some((
                    i18n("Install Missing Packages"),
                    Box::new(move |button: &gtk::Button| {
                        ui.run_action(button, vec!["install-printing-support".into()]);
                    }) as Box<dyn Fn(&gtk::Button)>,
                ))
            } else {
                None
            };
            state_row(
                &group,
                &package.name,
                package
                    .version
                    .as_deref()
                    .unwrap_or(&i18n("Not installed")),
                if required {
                    Some(package.installed)
                } else if package.installed {
                    Some(true)
                } else {
                    None
                },
                action,
            );
        }
        content.append(&group);
    }
    scroll.upcast()
}

fn xbox_page(ui: &Rc<Ui>, state: &XboxState, secure_boot: &SecureBootState) -> gtk::Widget {
    let (scroll, content) = page_shell(
        i18n("Xbox Controller Support"),
        i18n("xpadneo improves Bluetooth mapping, rumble, battery reporting and compatibility for modern Xbox controllers."),
        Some("input-gaming.svg"),
        650,
    );
    let group = adw::PreferencesGroup::builder()
        .title(i18n("Driver status"))
        .build();
    let install = if !state.installed {
        let ui = Rc::clone(ui);
        Some((
            i18n("Install Driver"),
            Box::new(move |button: &gtk::Button| {
                ui.run_action(button, vec!["install-xbox".into()]);
            }) as Box<dyn Fn(&gtk::Button)>,
        ))
    } else {
        None
    };
    state_row(
        &group,
        i18n("Driver package"),
        if state.installed {
            i18n("Installed")
        } else {
            i18n("Not installed")
        },
        Some(state.installed),
        install,
    );
    if !secure_boot.enforcement_inactive {
        let (signature_text, signature_good, signature_action) = match state.status {
            XboxStatus::NotInstalled | XboxStatus::ModuleMissing => {
                (i18n("Not detected"), None, None)
            }
            XboxStatus::SecureBootUnknown => (
                i18n("Secure Boot state could not be determined"),
                Some(false),
                Some("secure-boot"),
            ),
            XboxStatus::EnrollmentPending => (
                i18n("Pending enrollment in blue screen (MOKManager)"),
                Some(false),
                Some("secure-boot"),
            ),
            XboxStatus::TrustSetupRequired => (
                i18n("Certificate is not trusted by motherboard"),
                Some(false),
                Some("secure-boot"),
            ),
            XboxStatus::SignatureMismatch => (
                i18n("Some DKMS modules need to be re-signed"),
                Some(false),
                if secure_boot.configuration_present {
                    Some("repair-xbox")
                } else {
                    Some("secure-boot")
                },
            ),
            _ => (i18n("Trusted"), Some(true), None),
        };
        let action = match signature_action {
            Some("repair-xbox") => {
                let ui = Rc::clone(ui);
                Some((
                    i18n("Repair & Reinstall"),
                    Box::new(move |button: &gtk::Button| {
                        ui.run_action(button, vec!["repair-xbox".into()]);
                    }) as Box<dyn Fn(&gtk::Button)>,
                ))
            }
            Some("secure-boot") => {
                let ui = Rc::clone(ui);
                Some((
                    i18n("Secure Boot"),
                    Box::new(move |_button: &gtk::Button| ui.select_page("secure-boot"))
                        as Box<dyn Fn(&gtk::Button)>,
                ))
            }
            _ => None,
        };
        state_row(
            &group,
            i18n("Module signature"),
            signature_text,
            signature_good,
            action,
        );
    }
    let (module_text, module_good, module_action) = if state.status == XboxStatus::ModuleMissing {
        let ui = Rc::clone(ui);
        (
            i18n("Missing"),
            Some(false),
            Some((
                i18n("Repair & Reinstall"),
                Box::new(move |button: &gtk::Button| {
                    ui.run_action(button, vec!["repair-xbox".into()]);
                }) as Box<dyn Fn(&gtk::Button)>,
            )),
        )
    } else if state.status == XboxStatus::LoadStateUnknown {
        let ui = Rc::clone(ui);
        (
            i18n("Not detected"),
            Some(false),
            Some((
                i18n("Scan again"),
                Box::new(move |_button: &gtk::Button| ui.refresh()) as Box<dyn Fn(&gtk::Button)>,
            )),
        )
    } else if state.status == XboxStatus::Loaded {
        (i18n("Loaded"), Some(true), None)
    } else if state.module_available {
        let good = if matches!(
            state.status,
            XboxStatus::SecureBootUnknown
                | XboxStatus::EnrollmentPending
                | XboxStatus::TrustSetupRequired
                | XboxStatus::SignatureMismatch
        ) {
            None
        } else {
            Some(true)
        };
        (i18n("Standing by"), good, None)
    } else {
        (i18n("Not installed"), None, None)
    };
    state_row(
        &group,
        i18n("Kernel module"),
        module_text,
        module_good,
        module_action,
    );
    content.append(&group);
    let bluetooth = gtk::Button::with_label(&i18n("Bluetooth Settings"));
    bluetooth.set_halign(gtk::Align::End);
    bluetooth.connect_clicked(|_| {
        let _ = Command::new("gnome-control-center")
            .arg("bluetooth")
            .spawn();
    });
    content.append(&bluetooth);
    scroll.upcast()
}

fn set_status_icon(icon: &gtk::Image, name: &str, class: &str) {
    icon.set_icon_name(Some(name));
    for candidate in ["success", "warning", "error", "dim-label"] {
        icon.remove_css_class(candidate);
    }
    icon.add_css_class(class);
}

fn secure_boot_page(ui: &Rc<Ui>, state: &SecureBootState, dkms: &DkmsState) -> gtk::Widget {
    let scroll = gtk::ScrolledWindow::new();
    scroll.set_policy(gtk::PolicyType::Never, gtk::PolicyType::Automatic);
    let page = gtk::Box::new(gtk::Orientation::Vertical, 0);
    page.set_margin_start(48);
    page.set_margin_end(48);
    page.set_margin_top(24);
    page.set_margin_bottom(24);
    let center = gtk::Box::new(gtk::Orientation::Vertical, 0);
    center.set_halign(gtk::Align::Fill);
    if let Some(path) = config::illustration("secureboot-chip.svg") {
        let picture = gtk::Picture::for_filename(path);
        picture.set_content_fit(gtk::ContentFit::Contain);
        picture.set_size_request(72, 72);
        picture.set_halign(gtk::Align::Center);
        picture.set_margin_bottom(12);
        center.append(&picture);
    }
    let title = gtk::Label::new(Some(&i18n("Secure Boot Configuration")));
    title.add_css_class("title-1");
    title.set_halign(gtk::Align::Center);
    title.set_margin_bottom(8);
    center.append(&title);
    let description = gtk::Label::new(Some(&format!(
        "{}\n{}",
        i18n("Secure Boot is a motherboard security standard that ensures only trusted software loads at startup."),
        i18n("It protects your system from low-level malware and rootkits.")
    )));
    description.add_css_class("dim-label");
    description.set_halign(gtk::Align::Center);
    description.set_justify(gtk::Justification::Center);
    description.set_wrap(true);
    description.set_margin_bottom(24);
    center.append(&description);

    let group = adw::PreferencesGroup::builder()
        .title(i18n("System Trust Status"))
        .build();
    let add_trust_row = |title: &str| {
        let row = adw::ActionRow::builder().title(title).build();
        let icon = gtk::Image::from_icon_name("dialog-information-symbolic");
        icon.set_pixel_size(16);
        row.add_suffix(&icon);
        group.add(&row);
        (row, icon)
    };
    let (sb_row, sb_icon) = add_trust_row(&i18n("Secure Boot Enabled"));
    let (cert_row, cert_icon) = add_trust_row(&i18n("Local MOK Certificate"));
    let (enroll_row, enroll_icon) = add_trust_row(&i18n("UEFI Firmware Trust"));
    let (drivers_row, drivers_icon) = add_trust_row(&i18n("Third-party Drivers"));
    center.append(&group);

    match state.status.as_str() {
        "enabled" => {
            sb_row.set_subtitle(&i18n("Motherboard hardware protection is active"));
            set_status_icon(&sb_icon, "emblem-ok-symbolic", "success");
        }
        "unsupported" => {
            sb_row.set_subtitle(&i18n("Firmware does not support Secure Boot"));
            set_status_icon(&sb_icon, "dialog-information-symbolic", "dim-label");
        }
        "unknown" => {
            sb_row.set_subtitle(&i18n("Secure Boot state could not be determined"));
            set_status_icon(&sb_icon, "dialog-error-symbolic", "error");
        }
        _ => {
            sb_row.set_subtitle(&i18n("Secure Boot is disabled"));
            set_status_icon(&sb_icon, "dialog-information-symbolic", "dim-label");
        }
    }

    let has_certificate = state.key_present && state.certificate_present;
    if state.enforcement_inactive {
        let note = i18n("Not required without firmware enforcement");
        cert_row.set_subtitle(&note);
        enroll_row.set_subtitle(&note);
        set_status_icon(&cert_icon, "dialog-information-symbolic", "dim-label");
        set_status_icon(&enroll_icon, "dialog-information-symbolic", "dim-label");
    } else if !state.state_known {
        let note = i18n("Not checked because Secure Boot state is unknown");
        cert_row.set_subtitle(&note);
        enroll_row.set_subtitle(&note);
        set_status_icon(&cert_icon, "dialog-warning-symbolic", "warning");
        set_status_icon(&enroll_icon, "dialog-warning-symbolic", "warning");
    } else {
        cert_row.set_subtitle(&if has_certificate {
            i18n("Certificate generated locally")
        } else {
            i18n("Missing local certificate")
        });
        set_status_icon(
            &cert_icon,
            if has_certificate {
                "emblem-ok-symbolic"
            } else {
                "dialog-warning-symbolic"
            },
            if has_certificate { "success" } else { "warning" },
        );
        if state.enrolled {
            enroll_row.set_subtitle(&i18n("Certificate is trusted by motherboard"));
            set_status_icon(&enroll_icon, "emblem-ok-symbolic", "success");
        } else if state.enrollment_pending {
            enroll_row.set_subtitle(&i18n("Pending enrollment in blue screen (MOKManager)"));
            set_status_icon(&enroll_icon, "dialog-warning-symbolic", "warning");
        } else {
            enroll_row.set_subtitle(&i18n("Certificate is not trusted by motherboard"));
            set_status_icon(&enroll_icon, "dialog-error-symbolic", "error");
        }
    }

    if state.enforcement_inactive {
        drivers_row.set_subtitle(&i18n("Kernel signature enforcement is inactive"));
        set_status_icon(&drivers_icon, "dialog-information-symbolic", "dim-label");
    } else if !state.state_known {
        drivers_row.set_subtitle(&i18n("Driver trust cannot be verified"));
        set_status_icon(&drivers_icon, "dialog-warning-symbolic", "warning");
    } else if dkms.modules.is_empty() && state.trust_ready && state.configuration_present {
        drivers_row.set_subtitle(&i18n(
            "Secure Boot trust is ready. No third-party kernel modules are currently installed.",
        ));
        set_status_icon(&drivers_icon, "emblem-ok-symbolic", "success");
    } else if dkms.ready() && state.trust_ready && state.configuration_present {
        drivers_row.set_subtitle(&i18n("Drivers are signed and ready to load"));
        set_status_icon(&drivers_icon, "emblem-ok-symbolic", "success");
    } else if dkms.ready() && state.trust_ready && !state.configuration_present {
        drivers_row.set_subtitle(&i18n(
            "Drivers are trusted, but automatic DKMS signing needs repair",
        ));
        set_status_icon(&drivers_icon, "dialog-warning-symbolic", "warning");
    } else if dkms.modules.is_empty() {
        drivers_row.set_subtitle(&i18n("No signed third-party drivers detected"));
        set_status_icon(&drivers_icon, "dialog-information-symbolic", "dim-label");
    } else {
        drivers_row.set_subtitle(&i18n("Some DKMS modules need to be re-signed"));
        set_status_icon(&drivers_icon, "dialog-warning-symbolic", "warning");
    }

    let status = gtk::Label::new(None);
    status.set_justify(gtk::Justification::Center);
    status.set_halign(gtk::Align::Center);
    status.set_wrap(true);
    let status_text = if state.status == "unsupported" {
        i18n("This firmware does not provide Secure Boot. No certificate is required.")
    } else if !state.state_known {
        i18n("Secure Boot status could not be read. Driver trust operations are blocked until detection succeeds.")
    } else if !state.enabled {
        i18n("No certificate is required while Secure Boot is disabled.")
    } else if state.ready && dkms.ready() {
        status.add_css_class("title-4");
        i18n("System Trust Established. Third-party drivers will load securely.")
    } else if state.trust_ready && dkms.ready() && !state.configuration_present {
        format!(
            "{}\n{}",
            i18n("The certificate is enrolled and current drivers are trusted."),
            i18n("Repair automatic DKMS signing before future driver updates.")
        )
    } else if state.trust_ready {
        i18n("The certificate is enrolled, but some modules are not signed with it.")
    } else if state.enrollment_pending {
        format!(
            "{}\n{}",
            i18n("A certificate is waiting for enrollment."),
            i18n("Restart and enter password 123456 in MOKManager.")
        )
    } else if has_certificate {
        format!(
            "{}\n{}",
            i18n("The local trust certificate is not yet enrolled."),
            i18n("You must configure this to use third-party drivers like NVIDIA.")
        )
    } else {
        format!(
            "{}\n{}",
            i18n("The local trust certificate is missing."),
            i18n("You must configure this to use third-party drivers like NVIDIA.")
        )
    };
    status.set_label(&status_text);
    center.append(&status);

    let action_box = gtk::Box::new(gtk::Orientation::Vertical, 12);
    action_box.set_halign(gtk::Align::Center);
    let enroll_button = gtk::Button::new();
    enroll_button.set_halign(gtk::Align::Center);
    enroll_button.add_css_class("suggested-action");
    enroll_button.add_css_class("pill");
    enroll_button.set_size_request(240, 48);
    enroll_button.set_margin_top(16);
    let enroll_spinner = gtk::Spinner::new();
    let enroll_label = gtk::Label::new(Some(&if has_certificate {
        i18n("Enroll Existing Certificate")
    } else {
        i18n("Create & Enroll Certificate")
    }));
    let enroll_content = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    enroll_content.set_halign(gtk::Align::Center);
    enroll_content.append(&enroll_spinner);
    enroll_content.append(&enroll_label);
    enroll_button.set_child(Some(&enroll_content));
    enroll_button.set_visible(state.enrollment_required);
    action_box.append(&enroll_button);

    let repair_button = gtk::Button::with_label(&if dkms.ready() && !state.configuration_present {
        i18n("Repair Automatic DKMS Signing")
    } else {
        i18n("Repair Module Signatures")
    });
    repair_button.set_halign(gtk::Align::Center);
    repair_button.add_css_class("suggested-action");
    repair_button.add_css_class("pill");
    repair_button.set_size_request(240, 48);
    repair_button.set_visible(
        state.enabled
            && state.enrolled
            && (!state.configuration_present || (state.dkms_available && !dkms.ready())),
    );
    action_box.append(&repair_button);

    let reboot_note = gtk::Label::new(Some(&i18n(
        "Note: Use password <b>123456</b> after rebooting.",
    )));
    reboot_note.set_use_markup(true);
    reboot_note.set_halign(gtk::Align::Center);
    reboot_note.set_visible(state.enrollment_pending);
    action_box.append(&reboot_note);
    let reboot_button = gtk::Button::with_label(&i18n("Reboot & Configure Secure Boot"));
    reboot_button.set_halign(gtk::Align::Center);
    reboot_button.add_css_class("suggested-action");
    reboot_button.add_css_class("pill");
    reboot_button.set_size_request(280, 48);
    reboot_button.set_visible(state.enrollment_pending);
    action_box.append(&reboot_button);
    center.append(&action_box);

    let refresh_button = gtk::Button::with_label(&i18n("  Check Again  "));
    refresh_button.set_halign(gtk::Align::Center);
    refresh_button.set_margin_top(8);
    refresh_button.set_visible(
        !state.state_known || (state.enabled && (!state.ready || !dkms.ready())),
    );
    let ui_refresh = Rc::clone(ui);
    refresh_button.connect_clicked(move |_| ui_refresh.refresh());
    center.append(&refresh_button);

    let ui_enroll = Rc::clone(ui);
    let enroll_spinner_c = enroll_spinner.clone();
    let enroll_label_c = enroll_label.clone();
    enroll_button.connect_clicked(move |button| {
        run_secureboot_action(
            &ui_enroll,
            button,
            "prepare",
            Some((enroll_spinner_c.clone(), enroll_label_c.clone())),
        );
    });
    let ui_repair = Rc::clone(ui);
    repair_button.connect_clicked(move |button| {
        run_secureboot_action(&ui_repair, button, "repair-dkms", None);
    });
    let ui_reboot = Rc::clone(ui);
    reboot_button.connect_clicked(move |_| confirm_reboot(&ui_reboot));

    page.append(&center);
    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(650);
    clamp.set_tightening_threshold(500);
    clamp.set_child(Some(&page));
    scroll.set_child(Some(&clamp));
    scroll.upcast()
}

fn run_secureboot_action(
    ui: &Rc<Ui>,
    button: &gtk::Button,
    action: &'static str,
    enroll: Option<(gtk::Spinner, gtk::Label)>,
) {
    button.set_sensitive(false);
    if let Some((spinner, label)) = &enroll {
        spinner.start();
        label.set_label(&i18n("Generating & Signing..."));
    }
    let (tx, rx) = async_channel::bounded(1);
    std::thread::spawn(move || {
        let _ = tx.send_blocking(helper::run_secureboot(action));
    });
    let ui = Rc::clone(ui);
    let button = button.clone();
    glib::spawn_future_local(async move {
        if let Ok(result) = rx.recv().await {
            if let Some((spinner, label)) = &enroll {
                spinner.stop();
                label.set_label(&i18n("Create & Enroll Certificate"));
            }
            button.set_sensitive(true);
            present_secureboot_result(&ui, action, &result);
            ui.refresh();
        }
    });
}

fn present_secureboot_result(ui: &Rc<Ui>, action: &str, result: &HelperResult) {
    let steps = result
        .payload
        .get("steps")
        .cloned()
        .unwrap_or(Value::Null);
    let firmware_status = steps
        .pointer("/firmware_state/status")
        .and_then(Value::as_str)
        .unwrap_or("");
    let enrollment = steps
        .pointer("/enrollment_queued/status")
        .and_then(Value::as_str);
    let key_created = steps.pointer("/key_created/status").and_then(Value::as_str);
    let trust_prepared = matches!(enrollment, Some("success" | "skipped"))
        && matches!(key_created, Some("success" | "skipped"));
    if firmware_status == "skipped" {
        ui.alert(&i18n("Firmware signature enforcement is not active."));
        return;
    }
    if action == "prepare" && trust_prepared {
        let modules_failed = steps
            .pointer("/modules_rebuilt/status")
            .and_then(Value::as_str)
            == Some("failed");
        let warning = if modules_failed {
            Some(i18n(
                "The certificate is ready, but one or more DKMS modules could not be rebuilt. You can repair them after enrollment.",
            ))
        } else {
            None
        };
        if result
            .payload
            .get("reboot_required")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            show_reboot_prompt(ui, warning.as_deref());
        } else if let Some(warning) = warning {
            ui.alert(&warning);
        } else {
            ui.alert(&i18n("Drivers are signed and ready to load"));
        }
        return;
    }
    if result.ok {
        ui.alert(&i18n("Drivers are signed and ready to load"));
        return;
    }
    let detail = result
        .payload
        .get("error")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|item| !item.is_empty())
        .unwrap_or_else(|| {
            if result.message.is_empty() {
                i18n("Please check the advanced output.")
            } else {
                result.message.clone()
            }
        });
    let dialog = adw::AlertDialog::builder()
        .heading(i18n("Configuration failed. Please try again."))
        .body(detail)
        .build();
    let ok = i18n("OK");
    dialog.add_response("ok", &ok);
    dialog.present(Some(&ui.window));
}

fn show_reboot_prompt(ui: &Rc<Ui>, extra: Option<&str>) {
    let mut body = i18n("Success! When you reboot, a blue screen will appear.");
    if let Some(extra) = extra {
        body.push_str("\n\n");
        body.push_str(extra);
    }
    body.push('\n');
    body.push_str(&i18n(
        "Select 'Enroll MOK' → 'Continue' → 'Yes', and enter password: 123456",
    ));
    let heading = i18n("Certificate Created");
    let later = i18n("Later");
    let reboot = i18n("Reboot & Configure Secure Boot");
    let dialog = adw::AlertDialog::builder()
        .heading(heading)
        .body(body)
        .build();
    dialog.add_response("later", &later);
    dialog.add_response("reboot", &reboot);
    dialog.set_response_appearance("reboot", adw::ResponseAppearance::Suggested);
    dialog.connect_response(None, move |_, response| {
        if response == "reboot" {
            reboot_now();
        }
    });
    dialog.present(Some(&ui.window));
}

fn confirm_reboot(ui: &Rc<Ui>) {
    let heading = i18n("Reboot Required");
    let body = i18n("Please trust the certificate upon reboot using password 123456.");
    let cancel = i18n("Cancel");
    let reboot = i18n("Reboot");
    let dialog = adw::AlertDialog::builder()
        .heading(heading)
        .body(body)
        .build();
    dialog.add_response("cancel", &cancel);
    dialog.add_response("reboot", &reboot);
    dialog.set_response_appearance("reboot", adw::ResponseAppearance::Destructive);
    dialog.connect_response(None, move |_, response| {
        if response == "reboot" {
            reboot_now();
        }
    });
    dialog.present(Some(&ui.window));
}

fn reboot_now() {
    let _ = Command::new("gnome-session-quit")
        .args(["--reboot", "--no-prompt"])
        .spawn();
}

fn firmware_page(ui: &Rc<Ui>, state: &FirmwareSnapshot) -> gtk::Widget {
    let (scroll, content) = page_shell(
        i18n("Device Firmware"),
        i18n("Keep system firmware and supported hardware up to date through the Linux Vendor Firmware Service."),
        Some("firmware.svg"),
        650,
    );
    if let Some(error) = &state.error {
        let ui = Rc::clone(ui);
        content.append(&warning_banner(
            &format!("{}{error}", i18n("Firmware operation failed: ")),
            &i18n("Check Again"),
            move || ui.refresh(),
        ));
    }
    let overview = adw::PreferencesGroup::builder()
        .title(i18n("System status"))
        .build();
    state_row(
        &overview,
        i18n("Firmware service"),
        if state.connected {
            state
                .daemon_version
                .as_ref()
                .map(|version| format!("fwupd {version}"))
                .unwrap_or_else(|| i18n("Ready"))
        } else {
            i18n("Not available")
        },
        Some(state.connected),
        None,
    );
    state_row(
        &overview,
        i18n("Supported devices"),
        count_label(
            "%d device detected",
            "%d devices detected",
            state.devices.len(),
        ),
        None,
        None,
    );
    let updates = state.updates().len();
    state_row(
        &overview,
        i18n("Available firmware updates"),
        count_label(
            "%d update available",
            "%d updates available",
            updates,
        ),
        Some(updates == 0),
        None,
    );
    content.append(&overview);

    let actions = adw::PreferencesGroup::builder()
        .title(i18n("Firmware actions"))
        .build();
    let refresh_row = adw::ActionRow::builder()
        .title(i18n("Refresh Firmware Metadata"))
        .subtitle(i18n(
            "Download the latest metadata from enabled firmware sources.",
        ))
        .build();
    let refresh = gtk::Button::with_label(&i18n("Refresh"));
    refresh.set_valign(gtk::Align::Center);
    refresh.set_sensitive(state.connected);
    let ui_refresh = Rc::clone(ui);
    refresh.connect_clicked(move |button| {
        run_firmware(&ui_refresh, button, FirmwareOp::Refresh);
    });
    refresh_row.add_suffix(&refresh);
    actions.add(&refresh_row);
    let check_row = adw::ActionRow::builder()
        .title(i18n("Check for Firmware Updates"))
        .subtitle(i18n(
            "Compare connected devices with the available metadata.",
        ))
        .build();
    let check = gtk::Button::with_label(&i18n("Check Again"));
    check.set_valign(gtk::Align::Center);
    check.set_sensitive(state.connected);
    let ui_check = Rc::clone(ui);
    check.connect_clicked(move |_| ui_check.refresh());
    check_row.add_suffix(&check);
    actions.add(&check_row);
    if updates > 0 {
        let update_row = adw::ActionRow::builder()
            .title(i18n("Update All Firmware"))
            .subtitle(count_label(
                "Install %d available update.",
                "Install all %d available updates.",
                updates,
            ))
            .build();
        let update_all = gtk::Button::with_label(&i18n("Update All"));
        update_all.add_css_class("suggested-action");
        update_all.set_valign(gtk::Align::Center);
        let ui_update = Rc::clone(ui);
        update_all.connect_clicked(move |button| {
            run_firmware(&ui_update, button, FirmwareOp::UpdateAll);
        });
        update_row.add_suffix(&update_all);
        actions.add(&update_row);
    }
    content.append(&actions);

    let devices = adw::PreferencesGroup::builder()
        .title(i18n("Firmware Devices"))
        .description(i18n(
            "Expand a device to inspect installed and available firmware versions.",
        ))
        .build();
    if state.devices.is_empty() {
        state_row(
            &devices,
            i18n("No supported firmware devices"),
            i18n("The firmware service did not report any manageable devices."),
            None,
            None,
        );
    }
    for device in &state.devices {
        let subtitle = match (&device.version, &device.update_version) {
            (Some(current), Some(update)) => format!("{current} → {update}"),
            (Some(current), None) => current.clone(),
            _ => i18n("Not detected"),
        };
        let action = device.update_version.as_ref().map(|_| {
            let ui = Rc::clone(ui);
            let id = device.device_id.clone();
            (
                i18n("Apply Changes"),
                Box::new(move |button: &gtk::Button| {
                    run_firmware(&ui, button, FirmwareOp::Update(id.clone()));
                }) as Box<dyn Fn(&gtk::Button)>,
            )
        });
        state_row(
            &devices,
            &device.name,
            subtitle,
            Some(device.update_version.is_none()),
            action,
        );
    }
    content.append(&devices);

    let history = adw::PreferencesGroup::builder()
        .title(i18n("Firmware Update History"))
        .description(i18n("Results reported by the fwupd service."))
        .build();
    if state.history.is_empty() {
        state_row(
            &history,
            i18n("No firmware update history"),
            i18n("Completed firmware operations will appear here."),
            None,
            None,
        );
    } else {
        for entry in state.history.iter().take(20) {
            let (state_text, class) = match entry.state {
                2 => (i18n("Installed"), "success-pill"),
                3 => (i18n("Needs attention"), "warning-pill"),
                4 => (i18n("Reboot Required"), "warning-pill"),
                _ => (i18n("Not detected"), "installed-pill"),
            };
            let mut details = Vec::new();
            if let Some(timestamp) = entry.timestamp {
                if let Some(formatted) = format_timestamp(timestamp) {
                    details.push(formatted);
                }
            } else {
                details.push(i18n("Unknown time"));
            }
            if let Some(version) = &entry.version {
                details.push(format!("{}: {version}", i18n("Installed")));
            }
            if let Some(error) = &entry.error {
                details.push(error.clone());
            }
            let row = adw::ActionRow::builder()
                .title(&entry.name)
                .subtitle(details.join(" · "))
                .build();
            row.add_suffix(&pill(state_text, class));
            history.add(&row);
        }
    }
    content.append(&history);
    scroll.upcast()
}

enum FirmwareOp {
    Refresh,
    Update(String),
    UpdateAll,
}

fn run_firmware(ui: &Rc<Ui>, button: &gtk::Button, op: FirmwareOp) {
    button.set_sensitive(false);
    let original = button.label().unwrap_or_else(|| i18n("Apply").into());
    button.set_label(&i18n("Working…"));
    let (tx, rx) = async_channel::bounded(1);
    std::thread::spawn(move || {
        let result = match op {
            FirmwareOp::Refresh => helper::refresh_firmware(),
            FirmwareOp::Update(id) => helper::update_firmware(&id),
            FirmwareOp::UpdateAll => helper::update_all_firmware(),
        };
        let _ = tx.send_blocking(result);
    });
    let ui = Rc::clone(ui);
    let button = button.clone();
    glib::spawn_future_local(async move {
        if let Ok(result) = rx.recv().await {
            button.set_label(&original);
            button.set_sensitive(true);
            if helper::needs_shutdown(&result.message) || helper::needs_reboot(&result.message) {
                firmware_restart_dialog(&ui, helper::needs_shutdown(&result.message));
            } else {
                ui.alert(&result.message);
            }
            if result.ok {
                ui.refresh();
            }
        }
    });
}

fn firmware_restart_dialog(ui: &Rc<Ui>, shutdown: bool) {
    let heading = if shutdown {
        i18n("Shutdown Required")
    } else {
        i18n("Restart Required")
    };
    let body = if shutdown {
        i18n("The firmware update has been prepared. Save your work and shut down the computer to finish installing it.")
    } else {
        i18n("The firmware update has been prepared. Save your work and restart the computer to finish installing it.")
    };
    let later = i18n("Later");
    let now = if shutdown {
        i18n("Shut Down Now")
    } else {
        i18n("Restart Now")
    };
    let dialog = adw::AlertDialog::builder()
        .heading(heading)
        .body(body)
        .build();
    dialog.add_response("later", &later);
    dialog.add_response("now", &now);
    dialog.set_response_appearance("now", adw::ResponseAppearance::Destructive);
    dialog.connect_response(None, move |_, response| {
        if response == "now" {
            if shutdown {
                let _ = Command::new("gnome-session-quit")
                    .args(["--power-off", "--no-prompt"])
                    .spawn();
            } else {
                reboot_now();
            }
        }
    });
    dialog.present(Some(&ui.window));
}

fn format_timestamp(seconds: u64) -> Option<String> {
    let datetime = glib::DateTime::from_unix_local(seconds as i64).ok()?;
    datetime
        .format("%Y-%m-%d %H:%M")
        .ok()
        .map(|value| value.to_string())
}

pub fn build(app: &DriverCenterApplication, resident: bool) -> Rc<Ui> {
    let window = adw::ApplicationWindow::builder()
        .application(app)
        .title(i18n("AnduinOS Driver Center"))
        .default_width(1000)
        .default_height(700)
        .icon_name(config::APP_ID)
        .build();
    window.set_size_request(720, 520);

    let css = gtk::CssProvider::new();
    css.load_from_data(
        ".status-pill { border-radius: 999px; padding: 3px 9px; font-weight: 600; }
         .recommended-pill { color: @accent_color; background-color: alpha(@accent_color, 0.15); }
         .in-use-pill { color: @success_color; background-color: alpha(@success_color, 0.15); }
         .installed-pill { color: @window_fg_color; background-color: alpha(@window_fg_color, 0.10); }
         .success-pill { color: @success_color; background-color: alpha(@success_color, 0.14); }
         .warning-pill { color: @warning_color; background-color: alpha(@warning_color, 0.14); }
         .overview-card { padding: 0; }
         list.navigation-list { background: transparent; }
         list.navigation-list row { border: none; border-radius: 10px; margin: 2px 0; }
         list.navigation-list row:selected { background-color: alpha(@accent_color, 0.28); }",
    );
    if let Some(display) = gtk::gdk::Display::default() {
        gtk::style_context_add_provider_for_display(
            &display,
            &css,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
    }

    let split = adw::OverlaySplitView::new();
    split.set_min_sidebar_width(220.0);
    split.set_max_sidebar_width(290.0);
    split.set_sidebar_width_fraction(0.28);
    window.set_content(Some(&split));

    let sidebar_header = adw::HeaderBar::new();
    sidebar_header.set_show_end_title_buttons(false);
    let sidebar_title = i18n("AnduinOS Driver Center");
    sidebar_header.set_title_widget(Some(&adw::WindowTitle::new(&sidebar_title, "AnduinOS")));
    let sidebar_toolbar = adw::ToolbarView::new();
    sidebar_toolbar.add_top_bar(&sidebar_header);
    let sidebar = gtk::Box::new(gtk::Orientation::Vertical, 0);
    sidebar.set_margin_top(12);
    sidebar.set_margin_bottom(12);
    sidebar.set_margin_start(12);
    sidebar.set_margin_end(12);
    let device_list = gtk::ListBox::new();
    device_list.set_selection_mode(gtk::SelectionMode::Single);
    device_list.add_css_class("navigation-list");
    sidebar.append(&device_list);
    sidebar_toolbar.set_content(Some(&sidebar));
    split.set_sidebar(Some(&sidebar_toolbar));

    let content_toolbar = adw::ToolbarView::new();
    let header = adw::HeaderBar::new();
    let home_title = i18n("Home");
    let page_title = adw::WindowTitle::new(&home_title, "");
    header.set_title_widget(Some(&page_title));
    let refresh_button = gtk::Button::from_icon_name("view-refresh-symbolic");
    let scan_again = i18n("Scan again");
    refresh_button.set_tooltip_text(Some(&scan_again));
    header.pack_end(&refresh_button);
    let menu = gio::Menu::new();
    let about_label = i18n("About Driver Center");
    menu.append(Some(&about_label), Some("app.about"));
    let menu_button = gtk::MenuButton::builder()
        .icon_name("open-menu-symbolic")
        .tooltip_text(i18n("Main Menu"))
        .menu_model(&menu)
        .build();
    header.pack_end(&menu_button);
    let sidebar_toggle = gtk::ToggleButton::builder()
        .icon_name("sidebar-show-symbolic")
        .build();
    header.pack_start(&sidebar_toggle);
    content_toolbar.add_top_bar(&header);
    let stack = gtk::Stack::new();
    stack.set_transition_type(gtk::StackTransitionType::Crossfade);
    stack.set_vexpand(true);
    content_toolbar.set_content(Some(&stack));
    split.set_content(Some(&content_toolbar));

    let compact = adw::Breakpoint::new(adw::BreakpointCondition::parse("max-width: 700px").unwrap());
    let collapsed = true.to_value();
    let hide_sidebar = false.to_value();
    compact.add_setter(&split, "collapsed", Some(&collapsed));
    compact.add_setter(&split, "show-sidebar", Some(&hide_sidebar));
    window.add_breakpoint(compact);

    let ui = Rc::new(Ui {
        window: window.clone(),
        split: split.clone(),
        stack,
        device_list: device_list.clone(),
        page_title,
        refresh_button: refresh_button.clone(),
        sidebar_toggle: sidebar_toggle.clone(),
        rebuilding: Cell::new(false),
        selected: RefCell::new("home".into()),
    });

    let ui_refresh = Rc::clone(&ui);
    refresh_button.connect_clicked(move |_| ui_refresh.refresh());
    let ui_toggle = Rc::clone(&ui);
    sidebar_toggle.connect_toggled(move |button| {
        ui_toggle.split.set_show_sidebar(button.is_active());
    });
    let ui_list = Rc::clone(&ui);
    device_list.connect_row_selected(move |_, row| {
        if ui_list.rebuilding.get() {
            return;
        }
        if let Some(row) = row {
            let name = row.widget_name().to_string();
            ui_list.selected.replace(name.clone());
            ui_list.stack.set_visible_child_name(&name);
            ui_list.page_title.set_title(&name_to_title(&name));
            if ui_list.split.is_collapsed() {
                ui_list.split.set_show_sidebar(false);
            }
        }
    });

    if resident {
        window.connect_close_request(|win| {
            win.set_visible(false);
            glib::Propagation::Stop
        });
    }
    ui.refresh();
    ui
}

fn name_to_title(name: &str) -> String {
    match name {
        "home" => i18n("Home"),
        "audio" => i18n("Audio"),
        "printing" => i18n("Printers"),
        "xbox" => i18n("Xbox Controller"),
        "secure-boot" => i18n("Secure Boot"),
        "firmware" => i18n("Device Firmware"),
        _ => i18n("Available drivers"),
    }
}
