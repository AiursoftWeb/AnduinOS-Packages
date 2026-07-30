use crate::git_signing;
use crate::i18n::{i18n, i18n_fmt};
use crate::model::{Enrollment, YubiKey};
use adw::prelude::*;
use gtk::{gdk, glib};
use std::collections::BTreeSet;
use std::rc::Rc;

const ONBOARDING_SVG: &[u8] = include_bytes!("../data/security-key-onboarding.svg");
const CONNECTED_SVG: &[u8] = include_bytes!("../data/security-key-connected.svg");

#[derive(Clone, Debug)]
pub struct HomeSnapshot {
    pub username: String,
    pub devices: Result<Vec<YubiKey>, String>,
    pub enrollments: Vec<Enrollment>,
    pub passwordless_sudo: bool,
    pub git_status: git_signing::GitStatus,
}

#[derive(Clone)]
pub struct HomePage {
    root: gtk::ScrolledWindow,
    content: gtk::Box,
    navigate: Rc<dyn Fn(&str)>,
    retry: Rc<dyn Fn()>,
}

impl HomePage {
    pub fn new(navigate: Rc<dyn Fn(&str)>, retry: Rc<dyn Fn()>) -> Self {
        let content = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(24)
            .margin_start(24)
            .margin_end(24)
            .margin_top(24)
            .margin_bottom(32)
            .build();
        let clamp = adw::Clamp::builder()
            .maximum_size(980)
            .tightening_threshold(760)
            .child(&content)
            .build();
        let root = gtk::ScrolledWindow::builder()
            .hscrollbar_policy(gtk::PolicyType::Never)
            .child(&clamp)
            .build();
        let page = Self {
            root,
            content,
            navigate,
            retry,
        };
        page.show_loading();
        page
    }

    pub fn widget(&self) -> &gtk::ScrolledWindow {
        &self.root
    }

    pub fn show_loading(&self) {
        clear_box(&self.content);
        let status = adw::StatusPage::builder()
            .title(i18n("Checking connected YubiKeys"))
            .description(i18n("This will only take a moment."))
            .child(
                &gtk::Spinner::builder()
                    .spinning(true)
                    .width_request(30)
                    .height_request(30)
                    .halign(gtk::Align::Center)
                    .build(),
            )
            .vexpand(true)
            .build();
        self.content.append(&status);
    }

    pub fn render(&self, snapshot: &HomeSnapshot, inspected_ssh_keys: Option<usize>) {
        clear_box(&self.content);
        match &snapshot.devices {
            Err(error) => self.render_error(error),
            Ok(devices) if devices.is_empty() => {
                self.render_disconnected(snapshot, inspected_ssh_keys)
            }
            Ok(devices) => self.render_connected(snapshot, devices, inspected_ssh_keys),
        }
    }

    fn render_error(&self, error: &str) {
        let retry = gtk::Button::builder()
            .label(i18n("Try Again"))
            .css_classes(["suggested-action", "pill"])
            .halign(gtk::Align::Center)
            .build();
        let callback = self.retry.clone();
        retry.connect_clicked(move |_| callback());
        let status = adw::StatusPage::builder()
            .icon_name("dialog-warning-symbolic")
            .title(i18n("Could not update security keys"))
            .description(error)
            .child(&retry)
            .vexpand(true)
            .build();
        self.content.append(&status);
    }

    fn render_disconnected(&self, snapshot: &HomeSnapshot, inspected_ssh_keys: Option<usize>) {
        let pam_configured = snapshot
            .enrollments
            .iter()
            .any(|item| item.username == snapshot.username);
        let configured = pam_configured
            || snapshot.git_status.enabled()
            || inspected_ssh_keys.is_some_and(|count| count > 0);
        let hero = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(12)
            .halign(gtk::Align::Center)
            .build();
        hero.append(&illustration(ONBOARDING_SVG, 270, 170));
        let title = if configured {
            i18n("Connect your YubiKey")
        } else {
            i18n("AnduinOS is safer with a YubiKey")
        };
        hero.append(&centered_label(
            &title,
            &["title-1"],
            700,
        ));
        let description = if configured {
            i18n("Your security settings are ready. Connect a configured YubiKey when you need it.")
        } else {
            i18n("Sign in, authorize sudo, and protect SSH and Git identities with a physical touch.")
        };
        hero.append(&centered_label(
            &description,
            &["dim-label"],
            620,
        ));
        let prompt = gtk::Box::builder()
            .css_classes(["card"])
            .halign(gtk::Align::Center)
            .build();
        let prompt_content = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(10)
            .margin_top(9)
            .margin_start(14)
            .margin_end(14)
            .margin_bottom(9)
            .build();
        prompt_content.append(
            &gtk::Image::builder()
                .icon_name("drive-removable-media-usb-symbolic")
                .pixel_size(20)
                .build(),
        );
        prompt_content.append(
            &gtk::Label::builder()
                .label(if configured {
                    i18n("Waiting for a configured YubiKey")
                } else {
                    i18n("Connect a YubiKey to get started")
                })
                .css_classes(["heading"])
                .build(),
        );
        prompt.append(&prompt_content);
        hero.append(&prompt);
        self.content.append(&hero);

        if configured {
            self.content.append(
                &gtk::Label::builder()
                    .label(i18n_fmt(
                        &i18n("Protection for {0}"),
                        &[&snapshot.username],
                    ))
                    .css_classes(["title-2"])
                    .halign(gtk::Align::Start)
                    .build(),
            );
            self.content.append(&capability_grid(
                snapshot,
                inspected_ssh_keys,
                self.navigate.clone(),
            ));
            if pam_configured {
                self.content
                    .append(&configured_keys_group(snapshot, &snapshot.username));
            }
        } else {
            self.content.append(&intro_capabilities());
        }
    }

    fn render_connected(
        &self,
        snapshot: &HomeSnapshot,
        devices: &[YubiKey],
        inspected_ssh_keys: Option<usize>,
    ) {
        let hero = gtk::FlowBox::builder()
            .selection_mode(gtk::SelectionMode::None)
            .max_children_per_line(2)
            .min_children_per_line(1)
            .column_spacing(24)
            .row_spacing(12)
            .homogeneous(false)
            .css_classes(["card"])
            .build();
        let image = illustration(CONNECTED_SVG, 230, 165);
        image.set_margin_start(20);
        image.set_margin_end(20);
        hero.insert(&image, -1);

        let details = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(8)
            .margin_start(20)
            .margin_end(28)
            .margin_top(24)
            .margin_bottom(24)
            .valign(gtk::Align::Center)
            .hexpand(true)
            .build();
        let badge = gtk::Label::builder()
            .label(i18n("Connected"))
            .css_classes(["success", "pill", "caption"])
            .halign(gtk::Align::Start)
            .build();
        details.append(&badge);
        details.append(
            &gtk::Label::builder()
                .label(if devices.len() == 1 {
                    i18n("YubiKey connected")
                } else {
                    i18n_fmt(
                        &i18n("{0} YubiKeys connected"),
                        &[&devices.len().to_string()],
                    )
                })
                .css_classes(["title-1"])
                .halign(gtk::Align::Start)
                .wrap(true)
                .build(),
        );
        details.append(
            &gtk::Label::builder()
                .label(i18n_fmt(
                    &i18n("Ready to protect {0}"),
                    &[&snapshot.username],
                ))
                .css_classes(["dim-label"])
                .halign(gtk::Align::Start)
                .wrap(true)
                .build(),
        );
        if devices.len() == 1 {
            details.append(
                &gtk::Label::builder()
                    .label(device_identity(&devices[0]))
                    .css_classes(["caption", "dim-label"])
                    .halign(gtk::Align::Start)
                    .wrap(true)
                    .build(),
            );
        }
        hero.insert(&details, -1);
        self.content.append(&hero);

        let heading = gtk::Label::builder()
            .label(i18n_fmt(
                &i18n("Protection for {0}"),
                &[&snapshot.username],
            ))
            .css_classes(["title-2"])
            .halign(gtk::Align::Start)
            .build();
        self.content.append(&heading);
        self.content.append(&capability_grid(
            snapshot,
            inspected_ssh_keys,
            self.navigate.clone(),
        ));

        let group = adw::PreferencesGroup::builder()
            .title(i18n("Connected YubiKeys"))
            .build();
        for key in devices {
            let row = adw::ActionRow::builder()
                .title(&key.name)
                .subtitle(device_identity(key))
                .build();
            row.add_prefix(
                &gtk::Image::builder()
                    .icon_name("com.anduinos.yubikeymanager-security-key-symbolic")
                    .pixel_size(24)
                    .build(),
            );
            row.add_suffix(
                &gtk::Label::builder()
                    .label(i18n("Connected"))
                    .css_classes(["success", "pill", "caption"])
                    .valign(gtk::Align::Center)
                    .build(),
            );
            group.add(&row);
        }
        self.content.append(&group);
    }
}

fn intro_capabilities() -> gtk::FlowBox {
    let flow = capability_flow();
    for (icon, title, subtitle) in [
        (
            "system-lock-screen-symbolic",
            i18n("Sign-in"),
            i18n("Unlock your account with a touch"),
        ),
        (
            "security-high-symbolic",
            i18n("Administrator access"),
            i18n("Secure sudo authorization"),
        ),
        (
            "network-server-symbolic",
            i18n("SSH & Git"),
            i18n("Protect authentication and commit signing"),
        ),
    ] {
        flow.insert(&information_card(icon, &title, &subtitle), -1);
    }
    flow
}

fn capability_grid(
    snapshot: &HomeSnapshot,
    inspected_ssh_keys: Option<usize>,
    navigate: Rc<dyn Fn(&str)>,
) -> gtk::FlowBox {
    let flow = capability_flow();
    let gdm_count = enrollment_count(snapshot, "gdm");
    let sudo_count = enrollment_count(snapshot, "sudo");
    let git_enabled = snapshot.git_status.enabled();
    let ssh_state = inspected_ssh_keys
        .map(|count| {
            i18n_fmt(
                &i18n("{0} resident credentials inspected"),
                &[&count.to_string()],
            )
        })
        .unwrap_or_else(|| i18n("Ready to inspect"));

    let cards = [
        (
            "login",
            "system-lock-screen-symbolic",
            i18n("Sign-in"),
            if gdm_count > 0 {
                i18n("Enabled")
            } else {
                i18n("Not configured")
            },
            if gdm_count > 0 {
                i18n("YubiKey or account password")
            } else {
                i18n("Account password")
            },
            gdm_count > 0,
            false,
        ),
        (
            "sudo",
            "security-high-symbolic",
            i18n("Administrator access"),
            if snapshot.passwordless_sudo {
                i18n("Passwordless")
            } else if sudo_count > 0 {
                i18n("Enabled")
            } else {
                i18n("Not configured")
            },
            if snapshot.passwordless_sudo {
                i18n("sudo authentication is bypassed")
            } else if sudo_count > 0 {
                i18n("YubiKey or account password")
            } else {
                i18n("Account password")
            },
            sudo_count > 0,
            snapshot.passwordless_sudo,
        ),
        (
            "ssh",
            "network-server-symbolic",
            i18n("SSH keys"),
            i18n("Available"),
            ssh_state,
            inspected_ssh_keys.is_some(),
            false,
        ),
        (
            "git",
            "document-edit-symbolic",
            i18n("Git signing"),
            if git_enabled {
                i18n("Enabled")
            } else {
                i18n("Not configured")
            },
            if git_enabled {
                i18n("SSH commit signing")
            } else {
                i18n("Choose a YubiKey-backed SSH key")
            },
            git_enabled,
            false,
        ),
    ];
    for (target, icon, title, state, subtitle, success, warning) in cards {
        flow.insert(
            &capability_button(
                target,
                icon,
                &title,
                &state,
                &subtitle,
                success,
                warning,
                navigate.clone(),
            ),
            -1,
        );
    }
    flow
}

fn capability_flow() -> gtk::FlowBox {
    gtk::FlowBox::builder()
        .selection_mode(gtk::SelectionMode::None)
        .max_children_per_line(2)
        .min_children_per_line(1)
        .column_spacing(16)
        .row_spacing(16)
        .homogeneous(true)
        .build()
}

fn information_card(icon: &str, title: &str, subtitle: &str) -> gtk::Box {
    let card = gtk::Box::builder()
        .width_request(220)
        .css_classes(["card"])
        .build();
    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(8)
        .margin_start(18)
        .margin_end(18)
        .margin_top(18)
        .margin_bottom(18)
        .build();
    content.append(
        &gtk::Image::builder()
            .icon_name(icon)
            .pixel_size(28)
            .halign(gtk::Align::Start)
            .css_classes(["accent"])
            .build(),
    );
    content.append(
        &gtk::Label::builder()
            .label(title)
            .css_classes(["heading"])
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
    card.append(&content);
    card
}

#[allow(clippy::too_many_arguments)]
fn capability_button(
    target: &'static str,
    icon: &str,
    title: &str,
    state: &str,
    subtitle: &str,
    success: bool,
    warning: bool,
    navigate: Rc<dyn Fn(&str)>,
) -> gtk::Button {
    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(14)
        .margin_start(18)
        .margin_end(18)
        .margin_top(16)
        .margin_bottom(16)
        .width_request(280)
        .build();
    content.append(
        &gtk::Image::builder()
            .icon_name(icon)
            .pixel_size(28)
            .valign(gtk::Align::Start)
            .css_classes(["accent"])
            .build(),
    );
    let labels = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(4)
        .hexpand(true)
        .build();
    let title_row = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(8)
        .build();
    title_row.append(
        &gtk::Label::builder()
            .label(title)
            .css_classes(["heading"])
            .halign(gtk::Align::Start)
            .hexpand(true)
            .wrap(true)
            .xalign(0.0)
            .build(),
    );
    let mut state_classes = vec!["caption", "pill"];
    if warning {
        state_classes.push("warning");
    } else if success {
        state_classes.push("success");
    } else {
        state_classes.push("dim-label");
    }
    title_row.append(
        &gtk::Label::builder()
            .label(state)
            .css_classes(state_classes)
            .valign(gtk::Align::Center)
            .build(),
    );
    labels.append(&title_row);
    labels.append(
        &gtk::Label::builder()
            .label(subtitle)
            .css_classes(["dim-label", "caption"])
            .halign(gtk::Align::Start)
            .wrap(true)
            .xalign(0.0)
            .build(),
    );
    content.append(&labels);
    content.append(
        &gtk::Image::builder()
            .icon_name("go-next-symbolic")
            .valign(gtk::Align::Center)
            .css_classes(["dim-label"])
            .build(),
    );
    let button = gtk::Button::builder()
        .child(&content)
        .has_frame(false)
        .css_classes(["card"])
        .hexpand(true)
        .build();
    button.connect_clicked(move |_| navigate(target));
    button
}

fn configured_keys_group(snapshot: &HomeSnapshot, username: &str) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::builder()
        .title(i18n("Configured YubiKeys"))
        .description(i18n("These keys are trusted by this account but are not currently connected."))
        .build();
    let serials = snapshot
        .enrollments
        .iter()
        .filter(|item| item.username == username)
        .map(|item| item.serial.clone())
        .collect::<BTreeSet<_>>();
    for serial in serials {
        let purposes = snapshot
            .enrollments
            .iter()
            .filter(|item| item.username == username && item.serial == serial)
            .map(|item| match item.purpose.as_str() {
                "sudo" => "sudo".to_string(),
                _ => i18n("Sign-in"),
            })
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>()
            .join(" · ");
        let row = adw::ActionRow::builder()
            .title(if serial.starts_with("usb-") {
                i18n("YubiKey without hardware serial")
            } else {
                i18n_fmt(&i18n("YubiKey · Serial {0}"), &[&serial])
            })
            .subtitle(i18n_fmt(
                &i18n("Configured for {0} · Not connected"),
                &[&purposes],
            ))
            .build();
        row.add_prefix(
            &gtk::Image::builder()
                .icon_name("com.anduinos.yubikeymanager-security-key-symbolic")
                .pixel_size(24)
                .css_classes(["dim-label"])
                .build(),
        );
        group.add(&row);
    }
    group
}

fn enrollment_count(snapshot: &HomeSnapshot, purpose: &str) -> usize {
    snapshot
        .enrollments
        .iter()
        .filter(|item| item.username == snapshot.username && item.purpose == purpose)
        .count()
}

fn device_identity(key: &YubiKey) -> String {
    let serial = if key.serial.starts_with("usb-") {
        i18n("No hardware serial")
    } else {
        i18n_fmt(&i18n("Serial {0}"), &[&key.serial])
    };
    if key.firmware.is_empty() {
        serial
    } else {
        format!(
            "{} · {}",
            serial,
            i18n_fmt(&i18n("Firmware {0}"), &[&key.firmware])
        )
    }
}

fn centered_label(text: &str, classes: &[&str], max_width: i32) -> gtk::Label {
    gtk::Label::builder()
        .label(text)
        .css_classes(classes)
        .halign(gtk::Align::Center)
        .justify(gtk::Justification::Center)
        .wrap(true)
        .max_width_chars(max_width / 10)
        .build()
}

fn illustration(svg: &'static [u8], width: i32, height: i32) -> gtk::Picture {
    let bytes = glib::Bytes::from_static(svg);
    let picture = gtk::Picture::builder()
        .width_request(width)
        .height_request(height)
        .content_fit(gtk::ContentFit::Contain)
        .can_shrink(true)
        .build();
    if let Ok(texture) = gdk::Texture::from_bytes(&bytes) {
        picture.set_paintable(Some(&texture));
    }
    picture
}

fn clear_box(container: &gtk::Box) {
    while let Some(child) = container.last_child() {
        container.remove(&child);
    }
}
