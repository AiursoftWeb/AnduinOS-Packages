use crate::application::YubiKeyManagerApplication;
use crate::backend;
use crate::i18n::{i18n, i18n_fmt};
use crate::model::YubiKey;
use crate::progress_dialog;
use crate::ssh;
use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::{gio, glib};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;
use zeroize::Zeroizing;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct YubiKeyManagerWindow {
        pub stack: RefCell<Option<gtk::Stack>>,
        pub home: RefCell<Option<adw::PreferencesPage>>,
        pub login: RefCell<Option<adw::PreferencesPage>>,
        pub sudo: RefCell<Option<adw::PreferencesPage>>,
        pub ssh: RefCell<Option<adw::PreferencesPage>>,
        pub home_groups: RefCell<Vec<adw::PreferencesGroup>>,
        pub login_groups: RefCell<Vec<adw::PreferencesGroup>>,
        pub sudo_groups: RefCell<Vec<adw::PreferencesGroup>>,
        pub ssh_groups: RefCell<Vec<adw::PreferencesGroup>>,
        pub ssh_results:
            RefCell<HashMap<String, Result<Vec<ssh::ResidentSshCredential>, String>>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for YubiKeyManagerWindow {
        const NAME: &'static str = "YubiKeyManagerWindow";
        type Type = super::YubiKeyManagerWindow;
        type ParentType = adw::ApplicationWindow;
    }

    impl ObjectImpl for YubiKeyManagerWindow {
        fn constructed(&self) {
            self.parent_constructed();
            self.obj().setup_ui();
        }
    }
    impl WidgetImpl for YubiKeyManagerWindow {}
    impl WindowImpl for YubiKeyManagerWindow {}
    impl ApplicationWindowImpl for YubiKeyManagerWindow {}
    impl AdwApplicationWindowImpl for YubiKeyManagerWindow {}
}

glib::wrapper! {
    pub struct YubiKeyManagerWindow(ObjectSubclass<imp::YubiKeyManagerWindow>)
        @extends gtk::Widget, gtk::Window, gtk::ApplicationWindow, adw::ApplicationWindow,
        @implements gio::ActionGroup, gio::ActionMap, gtk::Accessible, gtk::Buildable,
                    gtk::ConstraintTarget, gtk::Native, gtk::Root, gtk::ShortcutManager;
}

impl YubiKeyManagerWindow {
    pub fn new(app: &YubiKeyManagerApplication) -> Self {
        glib::Object::builder()
            .property("application", app)
            .property("title", i18n("YubiKey Manager"))
            .property("default-width", 900)
            .property("default-height", 650)
            .property("icon-name", "com.anduinos.yubikeymanager")
            .build()
    }

    fn setup_ui(&self) {
        let sidebar = gtk::ListBox::builder()
            .css_classes(["navigation-sidebar"])
            .selection_mode(gtk::SelectionMode::Single)
            .build();
        let home_row = nav_row("go-home-symbolic", &i18n("Home"));
        let login_row = nav_row("system-lock-screen-symbolic", &i18n("Unlock GDM"));
        let sudo_row = nav_row("security-high-symbolic", &i18n("Unlock sudo"));
        let ssh_row = nav_row("network-server-symbolic", &i18n("SSH Keys"));
        sidebar.append(&home_row);
        sidebar.append(&login_row);
        sidebar.append(&sudo_row);
        sidebar.append(&ssh_row);
        sidebar.select_row(Some(&home_row));

        let sidebar_header = adw::HeaderBar::builder()
            .show_end_title_buttons(false)
            .build();
        let sidebar_toolbar = adw::ToolbarView::builder().content(&sidebar).build();
        sidebar_toolbar.add_top_bar(&sidebar_header);

        let stack = gtk::Stack::builder()
            .transition_type(gtk::StackTransitionType::Crossfade)
            .build();
        let home = adw::PreferencesPage::builder()
            .title(i18n("Home"))
            .icon_name("go-home-symbolic")
            .build();
        let login = adw::PreferencesPage::builder()
            .title(i18n("Unlock GDM"))
            .icon_name("system-lock-screen-symbolic")
            .build();
        let sudo = adw::PreferencesPage::builder()
            .title(i18n("Unlock sudo"))
            .icon_name("security-high-symbolic")
            .build();
        let ssh = adw::PreferencesPage::builder()
            .title(i18n("SSH Keys"))
            .icon_name("network-server-symbolic")
            .build();
        stack.add_named(&home, Some("home"));
        stack.add_named(&login, Some("login"));
        stack.add_named(&sudo, Some("sudo"));
        stack.add_named(&ssh, Some("ssh"));

        let menu = gio::Menu::new();
        menu.append(Some(&i18n("About")), Some("app.about"));
        let menu_button = gtk::MenuButton::builder()
            .icon_name("open-menu-symbolic")
            .menu_model(&menu)
            .build();
        let header = adw::HeaderBar::new();
        header.pack_end(&menu_button);
        let refresh_button = gtk::Button::builder()
            .icon_name("view-refresh-symbolic")
            .tooltip_text(i18n("Refresh connected YubiKeys"))
            .build();
        let weak = self.downgrade();
        refresh_button.connect_clicked(move |_| {
            if let Some(window) = weak.upgrade() {
                window.refresh();
            }
        });
        header.pack_end(&refresh_button);
        let toolbar = adw::ToolbarView::builder().content(&stack).build();
        toolbar.add_top_bar(&header);

        let split = adw::OverlaySplitView::builder()
            .sidebar(&sidebar_toolbar)
            .content(&toolbar)
            .min_sidebar_width(190.0)
            .max_sidebar_width(260.0)
            .build();
        let toggle = gtk::ToggleButton::builder()
            .icon_name("sidebar-show-symbolic")
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

        let stack_clone = stack.clone();
        let weak = self.downgrade();
        sidebar.connect_row_selected(move |_, row| {
            if let Some(row) = row {
                stack_clone.set_visible_child_name(match row.index() {
                    0 => "home",
                    1 => "login",
                    2 => "sudo",
                    _ => "ssh",
                });
                if let Some(window) = weak.upgrade() {
                    window.refresh();
                }
            }
        });

        AdwApplicationWindowExt::set_content(self, Some(&split));
        *self.imp().stack.borrow_mut() = Some(stack);
        *self.imp().home.borrow_mut() = Some(home);
        *self.imp().login.borrow_mut() = Some(login);
        *self.imp().sudo.borrow_mut() = Some(sudo);
        *self.imp().ssh.borrow_mut() = Some(ssh);

        let weak = self.downgrade();
        glib::idle_add_local_once(move || {
            if let Some(window) = weak.upgrade() {
                window.refresh();
            }
        });
    }

    fn refresh(&self) {
        let visible = self
            .imp()
            .stack
            .borrow()
            .as_ref()
            .and_then(|stack| stack.visible_child_name())
            .unwrap_or_default();
        let username = backend::current_user().unwrap_or_else(|_| i18n("unknown"));
        let devices = backend::list_yubikeys();
        if visible.as_str() == "ssh" {
            if let Some(page) = self.imp().ssh.borrow().as_ref() {
                clear_groups(page, &self.imp().ssh_groups);
                *self.imp().ssh_groups.borrow_mut() = rebuild_ssh(self, page);
            }
        } else if visible.as_str() == "sudo" {
            if let Some(page) = self.imp().sudo.borrow().as_ref() {
                clear_groups(page, &self.imp().sudo_groups);
                *self.imp().sudo_groups.borrow_mut() =
                    rebuild_sudo(self, page, &username, devices);
            }
        } else if visible.as_str() == "login" {
            if let Some(page) = self.imp().login.borrow().as_ref() {
                clear_groups(page, &self.imp().login_groups);
                *self.imp().login_groups.borrow_mut() =
                    rebuild_login(self, page, &username, devices);
            }
        } else if let Some(page) = self.imp().home.borrow().as_ref() {
            clear_groups(page, &self.imp().home_groups);
            *self.imp().home_groups.borrow_mut() = rebuild_home(page, &username, devices);
        }
    }
}

fn nav_row(icon: &str, title: &str) -> gtk::ListBoxRow {
    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(12)
        .margin_start(12)
        .margin_end(12)
        .margin_top(10)
        .margin_bottom(10)
        .build();
    content.append(&gtk::Image::builder().icon_name(icon).build());
    content.append(
        &gtk::Label::builder()
            .label(title)
            .halign(gtk::Align::Start)
            .hexpand(true)
            .build(),
    );
    gtk::ListBoxRow::builder().child(&content).build()
}

fn clear_groups(
    page: &adw::PreferencesPage,
    groups: &RefCell<Vec<adw::PreferencesGroup>>,
) {
    for group in groups.borrow_mut().drain(..) {
        page.remove(&group);
    }
}

fn rebuild_home(
    page: &adw::PreferencesPage,
    username: &str,
    devices: Result<Vec<YubiKey>, String>,
) -> Vec<adw::PreferencesGroup> {
    let enrollments = backend::enrollments();
    let passwordless_sudo = backend::passwordless_sudo();
    let summary = adw::PreferencesGroup::builder()
        .title(i18n("Security keys"))
        .description(i18n_fmt(&i18n("Current user: {0}"), &[username]))
        .build();
    match devices {
        Ok(keys)
            if keys.is_empty()
                && enrollments
                    .iter()
                    .all(|item| item.username != username) =>
        {
            summary.add(&action_row_with_icon(
                &i18n("No YubiKey detected"),
                &i18n("Insert a YubiKey, then press Refresh."),
                "dialog-information-symbolic",
            ));
        }
        Ok(mut keys) => {
            add_disconnected_enrollments(&mut keys, username, "gdm");
            add_disconnected_enrollments(&mut keys, username, "sudo");
            for key in keys {
                let gdm_enabled = enrollments.iter().any(|item| {
                    item.username == username
                        && item.serial == key.serial
                        && item.purpose == "gdm"
                });
                let sudo_enabled = enrollments.iter().any(|item| {
                    item.username == username
                        && item.serial == key.serial
                        && item.purpose == "sudo"
                });
                let identity = if key.serial.starts_with("usb-") {
                    i18n_fmt(&i18n("No hardware serial · {0}"), &[&key.serial])
                } else {
                    i18n_fmt(&i18n("Serial {0}"), &[&key.serial])
                };
                let subtitle = format!(
                    "{} · {}{}",
                    identity,
                    if key.firmware.is_empty() {
                        i18n("Not currently connected")
                    } else {
                        i18n_fmt(&i18n("Firmware {0}"), &[&key.firmware])
                    },
                    if key.interfaces.is_empty() {
                        String::new()
                    } else {
                        format!(" · {}", key.interfaces)
                    }
                );
                let row = action_row_with_icon(
                    &key.name,
                    &subtitle,
                    "dialog-password-symbolic",
                );
                let badges = gtk::Box::builder()
                    .orientation(gtk::Orientation::Horizontal)
                    .spacing(8)
                    .valign(gtk::Align::Center)
                    .build();
                badges.append(&capability_badge(
                    "GDM",
                    if gdm_enabled {
                        CapabilityState::Enabled
                    } else {
                        CapabilityState::Disabled
                    },
                ));
                badges.append(&capability_badge(
                    "sudo",
                    if sudo_enabled && passwordless_sudo {
                        CapabilityState::Bypassed
                    } else if sudo_enabled {
                        CapabilityState::Enabled
                    } else {
                        CapabilityState::Disabled
                    },
                ));
                row.add_suffix(&badges);
                summary.add(&row);
            }
        }
        Err(error) => {
            summary.add(&action_row_with_icon(
                &i18n("YubiKey Manager is unavailable"),
                &error,
                "dialog-warning-symbolic",
            ));
        }
    }
    page.add(&summary);

    let features = adw::PreferencesGroup::builder()
        .title(i18n("Configured features"))
        .build();
    let gdm_count = enrollments
        .iter()
        .filter(|item| item.username == username && item.purpose == "gdm")
        .count();
    let gdm_subtitle = if gdm_count == 0 {
        i18n("Password sign-in only")
    } else {
        i18n("YubiKey touch or password can unlock this user")
    };
    features.add(&action_row_with_icon(
        &i18n("AnduinOS sign-in"),
        &gdm_subtitle,
        if gdm_count == 0 {
            "changes-prevent-symbolic"
        } else {
            "emblem-ok-symbolic"
        },
    ));
    let sudo_count = enrollments
        .iter()
        .filter(|item| item.username == username && item.purpose == "sudo")
        .count();
    let sudo_subtitle = if passwordless_sudo && sudo_count > 0 {
        i18n("YubiKey configured, but sudo currently bypasses authentication")
    } else if passwordless_sudo {
        i18n("sudo currently allows administrator access without authentication")
    } else if sudo_count > 0 {
        i18n("YubiKey touch or account password can authorize sudo")
    } else {
        i18n("Account password is required for sudo")
    };
    features.add(&action_row_with_icon(
        &i18n("sudo authentication"),
        &sudo_subtitle,
        if passwordless_sudo {
            "dialog-warning-symbolic"
        } else if sudo_count > 0 {
            "emblem-ok-symbolic"
        } else {
            "changes-prevent-symbolic"
        },
    ));
    page.add(&features);
    vec![summary, features]
}

#[derive(Clone, Copy)]
enum CapabilityState {
    Enabled,
    Bypassed,
    Disabled,
}

fn capability_badge(name: &str, state: CapabilityState) -> gtk::Label {
    let (symbol, css_class, tooltip) = match state {
        CapabilityState::Enabled => (
            "✓",
            "success",
            i18n_fmt(&i18n("{0} is enabled for this YubiKey"), &[name]),
        ),
        CapabilityState::Bypassed => (
            "!",
            "warning",
            i18n_fmt(
                &i18n("{0} is configured, but another policy currently bypasses it"),
                &[name],
            ),
        ),
        CapabilityState::Disabled => (
            "—",
            "dim-label",
            i18n_fmt(
                &i18n("{0} is not configured for this YubiKey"),
                &[name],
            ),
        ),
    };
    gtk::Label::builder()
        .label(format!("{name} {symbol}"))
        .tooltip_text(&tooltip)
        .css_classes(["caption", "pill", css_class])
        .accessible_role(gtk::AccessibleRole::Status)
        .build()
}

fn rebuild_login(
    window: &YubiKeyManagerWindow,
    page: &adw::PreferencesPage,
    username: &str,
    devices: Result<Vec<YubiKey>, String>,
) -> Vec<adw::PreferencesGroup> {
    let group = adw::PreferencesGroup::builder()
        .title(i18n("Unlock the current user"))
        .description(i18n_fmt(
            &i18n("Choose which YubiKeys may unlock {0}. Password sign-in remains available."),
            &[username],
        ))
        .build();
    match devices {
        Ok(keys) if keys.is_empty() && backend::enrollments().iter().all(|item| item.username != username) => group.add(
            &adw::ActionRow::builder()
                .title(i18n("Insert a YubiKey"))
                .subtitle(i18n("A key must be connected before it can be enrolled."))
                .build(),
        ),
        Ok(mut keys) => {
            add_disconnected_enrollments(&mut keys, username, "gdm");
            for key in keys {
                let active = backend::is_enrolled(username, &key.serial);
                let connected = !key.firmware.is_empty();
                let row = adw::SwitchRow::builder()
                    .title(if key.serial.starts_with("usb-") {
                        i18n_fmt(&i18n("{0} · no hardware serial"), &[&key.name])
                    } else {
                        i18n_fmt(&i18n("{0} · {1}"), &[&key.name, &key.serial])
                    })
                    .subtitle(if active && !connected {
                        i18n("Allowed to unlock this user · not currently connected")
                    } else if active {
                        i18n("Allowed to unlock this user")
                    } else {
                        i18n("Touch this key when prompted to enroll it")
                    })
                    .active(active)
                    .build();
                let serial = key.serial.clone();
                let user = username.to_string();
                let weak = window.downgrade();
                let busy = Rc::new(Cell::new(false));
                row.connect_active_notify(move |switch| {
                    if busy.get() {
                        return;
                    }
                    busy.set(true);
                    switch.set_sensitive(false);
                    let requested = switch.is_active();
                    let serial = serial.clone();
                    let user = user.clone();
                    let switch_clone = switch.clone();
                    let weak = weak.clone();
                    let busy = busy.clone();
                    let parent = switch.root().and_downcast::<gtk::Window>();
                    glib::spawn_future_local(async move {
                        let task = move || {
                            if requested {
                                backend::register_credential("gdm", &user, &serial)
                            } else {
                                backend::remove_credential("gdm", &user, &serial)
                            }
                        };
                        let result = if let Some(parent) = parent {
                            let progress_message = if requested {
                                i18n("Touch your security key to continue.")
                            } else {
                                i18n("Updating sign-in settings…")
                            };
                            progress_dialog::run_with_progress(
                                &parent,
                                &progress_message,
                                task,
                            )
                            .await
                        } else {
                            gio::spawn_blocking(task)
                                .await
                                .unwrap_or_else(|_| Err(i18n("The enrollment task failed.")))
                        };
                        switch_clone.set_sensitive(true);
                        if let Err(error) = result {
                            switch_clone.set_active(!requested);
                            show_error(&switch_clone, &error);
                        }
                        busy.set(false);
                        if let Some(window) = weak.upgrade() {
                            window.refresh();
                        }
                    });
                });
                group.add(&row);
            }
        }
        Err(error) => group.add(
            &adw::ActionRow::builder()
                .title(i18n("Could not list YubiKeys"))
                .subtitle(&error)
                .build(),
        ),
    }
    page.add(&group);

    let note = adw::PreferencesGroup::builder()
        .title(i18n("Enrollment safety"))
        .description(i18n("During enrollment, disconnect all other security keys. The selected key will blink; touch it to continue. Multiple keys can be enrolled one at a time."))
        .build();
    page.add(&note);
    vec![group, note]
}

fn rebuild_sudo(
    window: &YubiKeyManagerWindow,
    page: &adw::PreferencesPage,
    username: &str,
    devices: Result<Vec<YubiKey>, String>,
) -> Vec<adw::PreferencesGroup> {
    let passwordless = backend::passwordless_sudo();
    let policy_group = adw::PreferencesGroup::builder()
        .title(i18n("sudo authentication policy"))
        .description(i18n("This setting applies only to the current user."))
        .build();
    let passwordless_row = adw::SwitchRow::builder()
        .title(i18n("Allow sudo without authentication"))
        .subtitle(if passwordless {
            i18n("Enabled · programs can obtain administrator privileges without confirmation")
        } else {
            i18n("Disabled · sudo must authenticate with a YubiKey or account password")
        })
        .active(passwordless)
        .build();
    passwordless_row.add_css_class(if passwordless { "warning" } else { "success" });
    let policy_busy = Rc::new(Cell::new(false));
    let weak = window.downgrade();
    let user = username.to_string();
    passwordless_row.connect_active_notify(move |row| {
        if policy_busy.get() {
            return;
        }
        policy_busy.set(true);
        row.set_sensitive(false);
        let requested = row.is_active();
        let row_clone = row.clone();
        let busy = policy_busy.clone();
        let weak = weak.clone();
        let user = user.clone();
        let parent = row.root().and_downcast::<gtk::Window>();
        glib::spawn_future_local(async move {
            let task = move || backend::set_passwordless_sudo(&user, requested);
            let result = if let Some(parent) = parent {
                let progress_message = if requested {
                    i18n("Enabling passwordless sudo…")
                } else {
                    i18n("Enabling sudo authentication…")
                };
                progress_dialog::run_with_progress(
                    &parent,
                    &progress_message,
                    task,
                )
                .await
            } else {
                gio::spawn_blocking(task)
                    .await
                    .unwrap_or_else(|_| Err(i18n("The sudo policy task failed.")))
            };
            if let Err(error) = result {
                row_clone.set_active(!requested);
                show_error(&row_clone, &error);
            }
            busy.set(false);
            row_clone.set_sensitive(true);
            if let Some(window) = weak.upgrade() {
                window.refresh();
            }
        });
    });
    policy_group.add(&passwordless_row);
    page.add(&policy_group);

    let key_group = adw::PreferencesGroup::builder()
        .title(i18n("Use YubiKey to unlock sudo"))
        .description(if passwordless {
            i18n("You may enroll keys now, but sudo bypasses PAM while passwordless access is enabled.")
        } else {
            i18n("An enrolled YubiKey or the account password can authorize sudo.")
        })
        .build();
    match devices {
        Ok(keys)
            if keys.is_empty()
                && backend::enrollments()
                    .iter()
                    .all(|item| item.username != username || item.purpose != "sudo") =>
        {
            key_group.add(
                &adw::ActionRow::builder()
                    .title(i18n("Insert a YubiKey"))
                    .subtitle(i18n("A key must be connected before it can be enrolled."))
                    .build(),
            );
        }
        Ok(mut keys) => {
            add_disconnected_enrollments(&mut keys, username, "sudo");
            for key in keys {
                let active = backend::is_enrolled_for("sudo", username, &key.serial);
                let connected = !key.firmware.is_empty();
                let row = adw::SwitchRow::builder()
                    .title(if key.serial.starts_with("usb-") {
                        i18n_fmt(&i18n("{0} · no hardware serial"), &[&key.name])
                    } else {
                        i18n_fmt(&i18n("{0} · {1}"), &[&key.name, &key.serial])
                    })
                    .subtitle(if active && passwordless {
                        i18n("Enrolled · inactive while sudo authentication is bypassed")
                    } else if active && !connected {
                        i18n("Allowed for sudo · not currently connected")
                    } else if active {
                        i18n("Allowed to authorize sudo")
                    } else {
                        i18n("Touch this key when prompted to enroll it for sudo")
                    })
                    .active(active)
                    .build();
                let serial = key.serial.clone();
                let user = username.to_string();
                let weak = window.downgrade();
                let busy = Rc::new(Cell::new(false));
                row.connect_active_notify(move |switch| {
                    if busy.get() {
                        return;
                    }
                    busy.set(true);
                    switch.set_sensitive(false);
                    let requested = switch.is_active();
                    let switch_clone = switch.clone();
                    let serial = serial.clone();
                    let user = user.clone();
                    let weak = weak.clone();
                    let busy = busy.clone();
                    let parent = switch.root().and_downcast::<gtk::Window>();
                    glib::spawn_future_local(async move {
                        let task = move || {
                            if requested {
                                backend::register_credential("sudo", &user, &serial)
                            } else {
                                backend::remove_credential("sudo", &user, &serial)
                            }
                        };
                        let result = if let Some(parent) = parent {
                            let progress_message = if requested {
                                i18n("Touch your security key to continue.")
                            } else {
                                i18n("Updating sudo settings…")
                            };
                            progress_dialog::run_with_progress(
                                &parent,
                                &progress_message,
                                task,
                            )
                            .await
                        } else {
                            gio::spawn_blocking(task)
                                .await
                                .unwrap_or_else(|_| Err(i18n("The enrollment task failed.")))
                        };
                        if let Err(error) = result {
                            switch_clone.set_active(!requested);
                            show_error(&switch_clone, &error);
                        }
                        busy.set(false);
                        switch_clone.set_sensitive(true);
                        if let Some(window) = weak.upgrade() {
                            window.refresh();
                        }
                    });
                });
                key_group.add(&row);
            }
        }
        Err(error) => key_group.add(
            &adw::ActionRow::builder()
                .title(i18n("Could not list YubiKeys"))
                .subtitle(&error)
                .build(),
        ),
    }
    page.add(&key_group);
    vec![policy_group, key_group]
}

fn add_disconnected_enrollments(
    keys: &mut Vec<YubiKey>,
    username: &str,
    purpose: &str,
) {
    for enrollment in backend::enrollments()
        .into_iter()
        .filter(|item| item.username == username && item.purpose == purpose)
    {
        if !keys.iter().any(|key| key.serial == enrollment.serial) {
            keys.push(YubiKey {
                name: i18n("YubiKey"),
                serial: enrollment.serial,
                firmware: String::new(),
                interfaces: String::new(),
            });
        }
    }
}

fn rebuild_ssh(
    window: &YubiKeyManagerWindow,
    page: &adw::PreferencesPage,
) -> Vec<adw::PreferencesGroup> {
    let agent = ssh::agent_status();
    let agent_group = adw::PreferencesGroup::builder()
        .title(i18n("SSH agent"))
        .build();
    let agent_description = if agent.available {
        let identity_count = agent.identity_count.to_string();
        let socket = if agent.socket.is_empty() {
            i18n("unknown socket")
        } else {
            agent.socket.clone()
        };
        i18n_fmt(
            &i18n("{0} identities · {1}"),
            &[&identity_count, &socket],
        )
    } else {
        agent
            .error
            .clone()
            .unwrap_or_else(|| i18n("No SSH agent was detected"))
    };
    let agent_title = if agent.available {
        i18n("SSH agent connected")
    } else {
        i18n("SSH agent unavailable")
    };
    agent_group.add(&action_row_with_icon(
        &agent_title,
        &agent_description,
        if agent.available {
            "emblem-ok-symbolic"
        } else {
            "dialog-warning-symbolic"
        },
    ));
    page.add(&agent_group);

    let fido_devices = ssh::list_fido_devices();
    let create_group = rebuild_ssh_create(window, &fido_devices);
    page.add(&create_group);

    let devices_group = adw::PreferencesGroup::builder()
        .title(i18n("Resident SSH credentials"))
        .description(i18n("Inspect, load, export, test, or remove resident SSH credentials."))
        .build();
    match fido_devices {
        Ok(devices) if devices.is_empty() => devices_group.add(
            &adw::ActionRow::builder()
                .title(i18n("No FIDO security key detected"))
                .subtitle(i18n("Insert a YubiKey, then press Refresh."))
                .build(),
        ),
        Ok(devices) => {
            let single_device = devices.len() == 1;
            for device in devices {
                let inspected = window
                    .imp()
                    .ssh_results
                    .borrow()
                    .get(&device.path)
                    .cloned();
                let all_loaded = matches!(
                    &inspected,
                    Some(Ok(credentials))
                        if !credentials.is_empty()
                            && credentials.iter().all(|credential| credential.loaded_in_agent)
                );
                let expected_fingerprints = match &inspected {
                    Some(Ok(credentials)) => credentials
                        .iter()
                        .map(|credential| credential.fingerprint.clone())
                        .collect::<Vec<_>>(),
                    _ => Vec::new(),
                };
                let row = adw::ActionRow::builder()
                    .title(&device.description)
                    .subtitle(&device.path)
                    .build();
                row.add_prefix(
                    &gtk::Image::builder()
                        .icon_name("dialog-password-symbolic")
                        .build(),
                );
                let inspect = gtk::Button::builder()
                    .label(i18n("Inspect"))
                    .valign(gtk::Align::Center)
                    .build();
                let path = device.path.clone();
                let weak = window.downgrade();
                inspect.connect_clicked(move |button| {
                    let Some(parent) = button.root().and_downcast::<gtk::Window>() else {
                        return;
                    };
                    let path = path.clone();
                    let weak = weak.clone();
                    glib::spawn_future_local(async move {
                        let Some(pin) = request_fido_pin(&parent).await else {
                            return;
                        };
                        let task_path = path.clone();
                        let result = progress_dialog::run_with_progress(
                            &parent,
                            &i18n("Inspecting resident SSH credentials…"),
                            move || ssh::inspect_resident_ssh(&task_path, pin.as_str()),
                        )
                        .await;
                        if let Some(window) = weak.upgrade() {
                            window
                                .imp()
                                .ssh_results
                                .borrow_mut()
                                .insert(path, result);
                            window.refresh();
                        }
                    });
                });
                row.add_suffix(&inspect);
                let load = gtk::Button::builder()
                    .label(if all_loaded {
                        i18n("Already loaded")
                    } else {
                        i18n("Load into agent")
                    })
                    .valign(gtk::Align::Center)
                    .sensitive(single_device && agent.available && !all_loaded)
                    .tooltip_text(if all_loaded {
                        i18n("All inspected resident credentials from this key are already in the agent")
                    } else if single_device {
                        i18n("Run ssh-add -K for this connected security key")
                    } else {
                        i18n("Connect exactly one FIDO security key to choose an unambiguous source")
                    })
                    .build();
                let weak = window.downgrade();
                load.connect_clicked(move |button| {
                    let Some(parent) = button.root().and_downcast::<gtk::Window>() else {
                        return;
                    };
                    let weak = weak.clone();
                    let expected_fingerprints = expected_fingerprints.clone();
                    glib::spawn_future_local(async move {
                        let Some(pin) = request_fido_pin_for(
                            &parent,
                            &i18n("Load resident SSH keys"),
                            &i18n("The PIN is sent directly to ssh-add and is never stored."),
                            &i18n("Load"),
                        )
                        .await
                        else {
                            return;
                        };
                        let result = progress_dialog::run_with_progress(
                            &parent,
                            &i18n("Touch the YubiKey when prompted. Loading resident keys into the SSH agent…"),
                            move || {
                                ssh::load_resident_keys(pin.as_str(), &expected_fingerprints)
                            },
                        )
                        .await;
                        match result {
                            Ok(load_result) => {
                                if let Some(window) = weak.upgrade() {
                                    refresh_cached_agent_matches(&window);
                                    window.refresh();
                                    match load_result {
                                        ssh::LoadResult::AlreadyLoaded => show_message(
                                            &window,
                                            &i18n("Already loaded"),
                                            &i18n("The inspected resident SSH credentials are already available in this agent."),
                                        ),
                                        ssh::LoadResult::Loaded { added } => show_message(
                                            &window,
                                            &i18n("Resident keys loaded"),
                                            &if added == 1 {
                                                i18n_fmt(&i18n("{0} new SSH identity was added to the agent."), &[&added.to_string()])
                                            } else {
                                                i18n_fmt(&i18n("{0} new SSH identities were added to the agent."), &[&added.to_string()])
                                            },
                                        ),
                                    }
                                }
                            }
                            Err(error) => show_error(&parent, &error),
                        }
                    });
                });
                row.add_suffix(&load);
                devices_group.add(&row);

                if let Some(result) = inspected {
                    match result {
                        Ok(credentials) if credentials.is_empty() => devices_group.add(
                            &adw::ActionRow::builder()
                                .title(i18n("No resident SSH credentials"))
                                .subtitle(i18n("No ssh:* discoverable credentials were found on this key."))
                                .build(),
                        ),
                        Ok(credentials) => {
                            for credential in credentials {
                                let credential_title = if credential.username.is_empty() {
                                    i18n("Unnamed SSH credential")
                                } else {
                                    credential.username.clone()
                                };
                                let credential_row = adw::ActionRow::builder()
                                    .title(credential_title)
                                    .subtitle(format!(
                                        "{} · {} · {}",
                                        credential.algorithm,
                                        credential.application,
                                        credential.fingerprint
                                    ))
                                    .build();
                                credential_row.add_prefix(
                                    &gtk::Image::builder()
                                        .icon_name("network-server-symbolic")
                                        .build(),
                                );
                                credential_row.add_suffix(&capability_badge(
                                    &i18n("agent"),
                                    if credential.loaded_in_agent {
                                        CapabilityState::Enabled
                                    } else {
                                        CapabilityState::Disabled
                                    },
                                ));
                                credential_row.add_suffix(&credential_actions(
                                    window,
                                    &credential,
                                ));
                                devices_group.add(&credential_row);
                            }
                        }
                        Err(error) => devices_group.add(
                            &adw::ActionRow::builder()
                                .title(i18n("Could not inspect this YubiKey"))
                                .subtitle(error)
                                .build(),
                        ),
                    }
                }
            }
        }
        Err(error) => devices_group.add(
            &adw::ActionRow::builder()
                .title(i18n("FIDO device discovery is unavailable"))
                .subtitle(error)
                .build(),
        ),
    }
    page.add(&devices_group);
    vec![agent_group, create_group, devices_group]
}

fn rebuild_ssh_create(
    window: &YubiKeyManagerWindow,
    devices: &Result<Vec<ssh::FidoDevice>, String>,
) -> adw::PreferencesGroup {
    let group = adw::PreferencesGroup::builder()
        .title(i18n("Create a resident SSH key"))
        .description(i18n(
            "The private key is generated by the selected YubiKey and cannot be exported. \
             A recoverable resident credential and local OpenSSH handle files are created.",
        ))
        .build();
    let row = action_row_with_icon(
        &i18n("New hardware-backed SSH key"),
        &i18n("ECDSA-SK · resident · touch required · safe compatibility defaults"),
        "list-add-symbolic",
    );
    let create = gtk::Button::builder()
        .label(i18n("Create…"))
        .valign(gtk::Align::Center)
        .sensitive(matches!(devices, Ok(items) if !items.is_empty()))
        .tooltip_text(match devices {
            Ok(items) if items.is_empty() => i18n("Connect a FIDO security key first"),
            Err(_) => i18n("FIDO device discovery is unavailable"),
            _ => i18n("Create a resident SSH credential on a selected device"),
        })
        .build();
    let devices = devices.clone().unwrap_or_default();
    let weak = window.downgrade();
    create.connect_clicked(move |button| {
        let Some(parent) = button.root().and_downcast::<gtk::Window>() else {
            return;
        };
        let devices = devices.clone();
        let weak = weak.clone();
        glib::spawn_future_local(async move {
            let username = backend::current_user().unwrap_or_else(|_| i18n("user"));
            let Some(options) =
                request_ssh_create_options(&parent, &devices, &username).await
            else {
                return;
            };
            if let Err(error) = ssh::validate_create_options(&options) {
                show_error(&parent, &error);
                return;
            }
            let Some(pin) = request_fido_pin_for(
                &parent,
                &i18n("Enter the selected YubiKey FIDO PIN"),
                &i18n("The PIN is sent through a private pipe to OpenSSH. It is never stored or placed in command arguments."),
                &i18n("Create"),
            )
            .await
            else {
                return;
            };
            let device_path = options.device.clone();
            let progress_device = options.device.clone();
            let result = progress_dialog::run_with_progress(
                &parent,
                &i18n_fmt(
                    &i18n("Touch the selected YubiKey. Creating an SSH resident key on {0}… Do not remove the key."),
                    &[&progress_device],
                ),
                move || ssh::create_resident_key(&options, pin.as_str()),
            )
            .await;
            match result {
                Ok(outcome) => {
                    if let Some(window) = weak.upgrade() {
                        window
                            .imp()
                            .ssh_results
                            .borrow_mut()
                            .insert(device_path, Ok(outcome.credentials.clone()));
                        window.refresh();
                        show_message(
                            &window,
                            &i18n("Resident SSH key created"),
                            &i18n_fmt(
                                &i18n("The non-exportable private key remains protected by the selected YubiKey.\n\nAlgorithm: {0}\nApplication: {1}\nResident username: {2}\nFingerprint: {3}\n\nLocal key handle: {4}\nPublic key: {5}\n\nThe key is not automatically loaded into the SSH agent."),
                                &[
                                    &outcome.credential.algorithm,
                                    &outcome.credential.application,
                                    &outcome.credential.username,
                                    &outcome.credential.fingerprint,
                                    &outcome.private_path.to_string_lossy(),
                                    &outcome.public_path.to_string_lossy(),
                                ],
                            ),
                        );
                    }
                }
                Err(error) => show_error(&parent, &error),
            }
        });
    });
    row.add_suffix(&create);
    group.add(&row);
    group
}

async fn request_ssh_create_options(
    parent: &gtk::Window,
    devices: &[ssh::FidoDevice],
    current_user: &str,
) -> Option<ssh::CreateOptions> {
    let device_labels = devices
        .iter()
        .map(|device| format!("{} · {}", device.description, device.path))
        .collect::<Vec<_>>();
    let device_refs = device_labels.iter().map(String::as_str).collect::<Vec<_>>();
    let device_model = gtk::StringList::new(&device_refs);
    let device_row = adw::ComboRow::builder()
        .title(i18n("YubiKey"))
        .subtitle(i18n("The credential will be created only on this exact device"))
        .model(&device_model)
        .selected(0)
        .build();
    let label = adw::EntryRow::builder()
        .title(i18n("Local key label"))
        .text(format!("{}@{}", current_user, glib::host_name()))
        .build();

    let algorithm_labels = [
        i18n("ECDSA-SK (recommended compatibility)"),
        i18n("Ed25519-SK (newer YubiKeys)"),
    ];
    let algorithm_refs = algorithm_labels
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>();
    let algorithm_model = gtk::StringList::new(&algorithm_refs);
    let algorithm = adw::ComboRow::builder()
        .title(i18n("Algorithm"))
        .subtitle(i18n("ECDSA-SK works with the widest range of YubiKeys"))
        .model(&algorithm_model)
        .selected(0)
        .build();
    let application = adw::EntryRow::builder()
        .title(i18n("Application"))
        .text("ssh:anduinos")
        .build();
    let resident_user = adw::EntryRow::builder()
        .title(i18n("Resident username"))
        .text(current_user)
        .build();
    let output_path = adw::EntryRow::builder()
        .title(i18n("Local key handle path"))
        .text(ssh::default_key_path().to_string_lossy())
        .build();
    let browse = gtk::Button::builder()
        .icon_name("folder-open-symbolic")
        .valign(gtk::Align::Center)
        .tooltip_text(i18n("Choose a local key-handle file"))
        .css_classes(["flat"])
        .build();
    let output_path_clone = output_path.clone();
    browse.connect_clicked(move |button| {
        let Some(parent) = button.root().and_downcast::<gtk::Window>() else {
            return;
        };
        let output_path = output_path_clone.clone();
        glib::spawn_future_local(async move {
            let dialog = gtk::FileDialog::builder()
                .title(i18n("Choose local SSH key-handle path"))
                .initial_name("id_ecdsa_sk_yubikey")
                .build();
            if let Ok(file) = dialog.save_future(Some(&parent)).await {
                if let Some(path) = file.path() {
                    output_path.set_text(&path.to_string_lossy());
                }
            }
        });
    });
    output_path.add_suffix(&browse);
    let verify = adw::SwitchRow::builder()
        .title(i18n("Require verification for every signature"))
        .subtitle(i18n("Requires the FIDO PIN each time this key signs"))
        .active(false)
        .build();
    let advanced = adw::ExpanderRow::builder()
        .title(i18n("Advanced options"))
        .subtitle(i18n("OpenSSH/FIDO credential metadata and local file location"))
        .build();
    for child in [
        algorithm.upcast_ref::<gtk::Widget>(),
        application.upcast_ref::<gtk::Widget>(),
        resident_user.upcast_ref::<gtk::Widget>(),
        output_path.upcast_ref::<gtk::Widget>(),
        verify.upcast_ref::<gtk::Widget>(),
    ] {
        advanced.add_row(child);
    }

    let group = adw::PreferencesGroup::new();
    group.add(&device_row);
    group.add(&label);
    group.add(&advanced);
    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .width_request(560)
        .build();
    content.append(&group);
    let dialog = adw::AlertDialog::builder()
        .heading(i18n("Create a resident SSH key"))
        .body(i18n("Safe defaults use ECDSA-SK, require touch, and store a discoverable credential on the selected YubiKey."))
        .extra_child(&content)
        .close_response("cancel")
        .default_response("continue")
        .build();
    dialog.add_response("cancel", &i18n("Cancel"));
    dialog.add_response("continue", &i18n("Continue"));
    dialog.set_response_appearance("continue", adw::ResponseAppearance::Suggested);
    if dialog.choose_future(Some(parent)).await.as_str() != "continue" {
        return None;
    }
    let device = devices.get(device_row.selected() as usize)?.path.clone();
    Some(ssh::CreateOptions {
        device,
        algorithm: if algorithm.selected() == 0 {
            "ecdsa-sk".into()
        } else {
            "ed25519-sk".into()
        },
        application: application.text().trim().to_string(),
        username: resident_user.text().trim().to_string(),
        comment: label.text().trim().to_string(),
        output_path: output_path.text().trim().into(),
        verify_required: verify.is_active(),
    })
}

async fn request_fido_pin(parent: &gtk::Window) -> Option<Zeroizing<String>> {
    request_fido_pin_for(
        parent,
        &i18n("Enter the YubiKey FIDO PIN"),
        &i18n("The PIN is used only for this read-only inspection and is never stored."),
        &i18n("Inspect"),
    )
    .await
}

async fn request_fido_pin_for(
    parent: &gtk::Window,
    heading: &str,
    body: &str,
    accept_label: &str,
) -> Option<Zeroizing<String>> {
    let entry = gtk::PasswordEntry::builder()
        .show_peek_icon(true)
        .placeholder_text(i18n("FIDO PIN"))
        .activates_default(true)
        .build();
    let dialog = adw::AlertDialog::builder()
        .heading(heading)
        .body(body)
        .extra_child(&entry)
        .close_response("cancel")
        .default_response("accept")
        .build();
    dialog.add_response("cancel", &i18n("Cancel"));
    dialog.add_response("accept", accept_label);
    dialog.set_response_appearance("accept", adw::ResponseAppearance::Suggested);
    let response = dialog.clone().choose_future(Some(parent)).await;
    if response.as_str() != "accept" || entry.text().is_empty() {
        return None;
    }
    Some(Zeroizing::new(entry.text().to_string()))
}

fn credential_actions(
    window: &YubiKeyManagerWindow,
    credential: &ssh::ResidentSshCredential,
) -> gtk::MenuButton {
    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(4)
        .margin_start(6)
        .margin_end(6)
        .margin_top(6)
        .margin_bottom(6)
        .build();
    let copy = gtk::Button::with_label(&i18n("Copy public key"));
    let export = gtk::Button::with_label(&i18n("Export .pub"));
    let test = gtk::Button::with_label(&i18n("Test signing"));
    let remove = gtk::Button::with_label(&i18n("Remove from agent"));
    remove.set_sensitive(credential.loaded_in_agent);
    for button in [&copy, &export, &test, &remove] {
        button.set_halign(gtk::Align::Fill);
        content.append(button);
    }
    let popover = gtk::Popover::builder().child(&content).build();
    let menu = gtk::MenuButton::builder()
        .icon_name("view-more-symbolic")
        .popover(&popover)
        .valign(gtk::Align::Center)
        .tooltip_text(i18n("SSH key actions"))
        .build();

    let public_key = credential.public_key.clone();
    copy.connect_clicked(move |button| {
        button.clipboard().set_text(&public_key);
        if let Some(popover) = button.ancestor(gtk::Popover::static_type()).and_downcast::<gtk::Popover>() {
            popover.popdown();
        }
    });

    let public_key = credential.public_key.clone();
    let username = credential.username.clone();
    export.connect_clicked(move |button| {
        let Some(parent) = button.root().and_downcast::<gtk::Window>() else {
            return;
        };
        let public_key = public_key.clone();
        let suggested = safe_pub_filename(&username);
        glib::spawn_future_local(async move {
            let dialog = gtk::FileDialog::builder()
                .title(i18n("Export SSH public key"))
                .initial_name(&suggested)
                .build();
            let Ok(file) = dialog.save_future(Some(&parent)).await else {
                return;
            };
            if let Err((_, error)) = file
                .replace_contents_future(
                    format!("{public_key}\n").into_bytes(),
                    None,
                    false,
                    gio::FileCreateFlags::REPLACE_DESTINATION,
                )
                .await
            {
                show_error(
                    &parent,
                    &i18n_fmt(
                        &i18n("Could not export the public key: {0}"),
                        &[&error.to_string()],
                    ),
                );
            }
        });
    });

    let public_key = credential.public_key.clone();
    test.connect_clicked(move |button| {
        let Some(parent) = button.root().and_downcast::<gtk::Window>() else {
            return;
        };
        let public_key = public_key.clone();
        glib::spawn_future_local(async move {
            let result = progress_dialog::run_with_progress(
                &parent,
                &i18n("Touch the YubiKey. Testing SSH signing and verification…"),
                move || ssh::test_signing(&public_key),
            )
            .await;
            match result {
                Ok(()) => show_message(
                    &parent,
                    &i18n("Signing test passed"),
                    &i18n("The SSH agent successfully signed and verified data with this key."),
                ),
                Err(error) => show_error(&parent, &error),
            }
        });
    });

    let public_key = credential.public_key.clone();
    let weak = window.downgrade();
    remove.connect_clicked(move |button| {
        let Some(parent) = button.root().and_downcast::<gtk::Window>() else {
            return;
        };
        let public_key = public_key.clone();
        let weak = weak.clone();
        glib::spawn_future_local(async move {
            let result = progress_dialog::run_with_progress(
                &parent,
                &i18n("Removing this key from the SSH agent…"),
                move || ssh::remove_from_agent(&public_key),
            )
            .await;
            match result {
                Ok(()) => {
                    if let Some(window) = weak.upgrade() {
                        refresh_cached_agent_matches(&window);
                        window.refresh();
                    }
                }
                Err(error) => show_error(&parent, &error),
            }
        });
    });
    menu
}

fn refresh_cached_agent_matches(window: &YubiKeyManagerWindow) {
    for result in window.imp().ssh_results.borrow_mut().values_mut() {
        if let Ok(credentials) = result {
            ssh::refresh_agent_matches(credentials);
        }
    }
}

fn safe_pub_filename(username: &str) -> String {
    let stem: String = username
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.') {
                character
            } else {
                '_'
            }
        })
        .collect();
    format!("{}.pub", if stem.is_empty() { "id_security_key" } else { &stem })
}

fn show_message<W: IsA<gtk::Widget>>(widget: &W, heading: &str, body: &str) {
    let dialog = adw::AlertDialog::builder()
        .heading(heading)
        .body(body)
        .build();
    dialog.add_response("close", &i18n("Close"));
    dialog.present(widget.root().and_downcast_ref::<gtk::Window>());
}

fn action_row_with_icon(title: &str, subtitle: &str, icon: &str) -> adw::ActionRow {
    let row = adw::ActionRow::builder()
        .title(title)
        .subtitle(subtitle)
        .build();
    row.add_prefix(&gtk::Image::builder().icon_name(icon).build());
    row
}

fn show_error<W: IsA<gtk::Widget>>(widget: &W, message: &str) {
    let dialog = adw::AlertDialog::builder()
        .heading(i18n("YubiKey configuration failed"))
        .body(message)
        .build();
    dialog.add_response("close", &i18n("Close"));
    dialog.present(widget.root().and_downcast_ref::<gtk::Window>());
}
