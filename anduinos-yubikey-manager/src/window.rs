use crate::application::YubiKeyManagerApplication;
use crate::backend;
use crate::device_monitor;
use crate::git_signing;
use crate::home::{HomePage, HomeSnapshot};
use crate::i18n::{i18n, i18n_fmt};
use crate::model::{Enrollment, YubiKey};
use crate::progress_dialog;
use crate::ssh;
use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::{gio, glib};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;
use std::time::Duration;
use zeroize::Zeroizing;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct YubiKeyManagerWindow {
        pub stack: RefCell<Option<gtk::Stack>>,
        pub sidebar: RefCell<Option<gtk::ListBox>>,
        pub home: RefCell<Option<HomePage>>,
        pub login: RefCell<Option<adw::PreferencesPage>>,
        pub sudo: RefCell<Option<adw::PreferencesPage>>,
        pub ssh: RefCell<Option<adw::PreferencesPage>>,
        pub git: RefCell<Option<adw::PreferencesPage>>,
        pub login_groups: RefCell<Vec<adw::PreferencesGroup>>,
        pub sudo_groups: RefCell<Vec<adw::PreferencesGroup>>,
        pub ssh_groups: RefCell<Vec<adw::PreferencesGroup>>,
        pub git_groups: RefCell<Vec<adw::PreferencesGroup>>,
        pub home_snapshot: RefCell<Option<HomeSnapshot>>,
        pub home_refreshing: Cell<bool>,
        pub home_device_refreshing: Cell<bool>,
        pub home_device_refresh_pending: Cell<bool>,
        pub home_debounce_source: RefCell<Option<glib::SourceId>>,
        pub home_poll_source: RefCell<Option<glib::SourceId>>,
        pub device_monitor: RefCell<Option<gio::Subprocess>>,
        pub login_refreshing: Cell<bool>,
        pub sudo_refreshing: Cell<bool>,
        pub ssh_refreshing: Cell<bool>,
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

        fn dispose(&self) {
            if let Some(source) = self.home_debounce_source.borrow_mut().take() {
                source.remove();
            }
            if let Some(source) = self.home_poll_source.borrow_mut().take() {
                source.remove();
            }
            if let Some(process) = self.device_monitor.borrow_mut().take() {
                process.force_exit();
            }
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
            .property("title", i18n("AnduinOS YubiKey Security Center"))
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
        let ssh_row = nav_row("network-server-symbolic", &i18n("SSH keys"));
        let git_row = nav_row("document-edit-symbolic", &i18n("Git signing"));
        sidebar.append(&home_row);
        sidebar.append(&login_row);
        sidebar.append(&sudo_row);
        sidebar.append(&ssh_row);
        sidebar.append(&git_row);
        sidebar.select_row(Some(&home_row));

        let sidebar_header = adw::HeaderBar::builder()
            .show_end_title_buttons(false)
            .build();
        sidebar_header.set_title_widget(Some(&adw::WindowTitle::new(
            &i18n("Security Center"),
            "AnduinOS",
        )));
        let sidebar_toolbar = adw::ToolbarView::builder().content(&sidebar).build();
        sidebar_toolbar.add_top_bar(&sidebar_header);

        let stack = gtk::Stack::builder()
            .transition_type(gtk::StackTransitionType::Crossfade)
            .build();
        let weak = self.downgrade();
        let navigate: Rc<dyn Fn(&str)> = Rc::new(move |target| {
            if let Some(window) = weak.upgrade() {
                window.navigate_to(target);
            }
        });
        let weak = self.downgrade();
        let retry: Rc<dyn Fn()> = Rc::new(move || {
            if let Some(window) = weak.upgrade() {
                window.refresh_home_full();
            }
        });
        let home = HomePage::new(navigate, retry);
        let login = adw::PreferencesPage::builder()
            .title(i18n("Unlock GDM"))
            .icon_name("system-lock-screen-symbolic")
            .build();
        let sudo = adw::PreferencesPage::builder()
            .title(i18n("Unlock sudo"))
            .icon_name("security-high-symbolic")
            .build();
        let ssh = adw::PreferencesPage::builder()
            .title(i18n("SSH keys"))
            .icon_name("network-server-symbolic")
            .build();
        let git = adw::PreferencesPage::builder()
            .title(i18n("Git signing"))
            .icon_name("document-edit-symbolic")
            .build();
        stack.add_named(home.widget(), Some("home"));
        stack.add_named(&login, Some("login"));
        stack.add_named(&sudo, Some("sudo"));
        stack.add_named(&ssh, Some("ssh"));
        stack.add_named(&git, Some("git"));

        let menu = gio::Menu::new();
        menu.append(Some(&i18n("About")), Some("app.about"));
        let menu_button = gtk::MenuButton::builder()
            .icon_name("open-menu-symbolic")
            .menu_model(&menu)
            .build();
        let header = adw::HeaderBar::new();
        let page_title = adw::WindowTitle::new(&i18n("Home"), "");
        header.set_title_widget(Some(&page_title));
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
        let page_title_clone = page_title.clone();
        let weak = self.downgrade();
        sidebar.connect_row_selected(move |_, row| {
            if let Some(row) = row {
                let title = match row.index() {
                    0 => i18n("Home"),
                    1 => i18n("Unlock GDM"),
                    2 => i18n("Unlock sudo"),
                    3 => i18n("SSH keys"),
                    _ => i18n("Git signing"),
                };
                page_title_clone.set_title(&title);
                stack_clone.set_visible_child_name(match row.index() {
                    0 => "home",
                    1 => "login",
                    2 => "sudo",
                    3 => "ssh",
                    _ => "git",
                });
                if let Some(window) = weak.upgrade() {
                    window.refresh();
                }
            }
        });

        AdwApplicationWindowExt::set_content(self, Some(&split));
        *self.imp().stack.borrow_mut() = Some(stack);
        *self.imp().sidebar.borrow_mut() = Some(sidebar);
        *self.imp().home.borrow_mut() = Some(home);
        *self.imp().login.borrow_mut() = Some(login);
        *self.imp().sudo.borrow_mut() = Some(sudo);
        *self.imp().ssh.borrow_mut() = Some(ssh);
        *self.imp().git.borrow_mut() = Some(git);

        self.start_device_monitor();

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
        if visible.as_str() == "home" {
            self.refresh_home_full();
        } else if visible.as_str() == "git" {
            if let Some(page) = self.imp().git.borrow().as_ref() {
                clear_groups(page, &self.imp().git_groups);
                *self.imp().git_groups.borrow_mut() = rebuild_git(self, page);
            }
        } else if visible.as_str() == "ssh" {
            self.refresh_ssh();
        } else if matches!(visible.as_str(), "login" | "sudo") {
            self.refresh_security_page(visible.as_str());
        }
    }

    fn navigate_to(&self, target: &str) {
        let index = match target {
            "home" => 0,
            "login" => 1,
            "sudo" => 2,
            "ssh" => 3,
            "git" => 4,
            _ => return,
        };
        if let Some(sidebar) = self.imp().sidebar.borrow().as_ref() {
            if let Some(row) = sidebar.row_at_index(index) {
                sidebar.select_row(Some(&row));
            }
        }
    }

    fn refresh_home_full(&self) {
        if self.imp().home_refreshing.replace(true) {
            return;
        }
        if self.imp().home_snapshot.borrow().is_none() {
            if let Some(home) = self.imp().home.borrow().as_ref() {
                home.show_loading();
            }
        }
        let weak = self.downgrade();
        glib::spawn_future_local(async move {
            let result = gio::spawn_blocking(|| {
                let username = backend::current_user().unwrap_or_else(|_| i18n("unknown"));
                let state = backend::security_state();
                HomeSnapshot {
                    devices: backend::list_yubikeys_fast(),
                    passwordless_sudo: state.passwordless_sudo_for(&username),
                    enrollments: state.enrollments,
                    git_status: git_signing::status(),
                    username,
                }
            })
            .await;
            let Some(window) = weak.upgrade() else {
                return;
            };
            window.imp().home_refreshing.set(false);
            if let Ok(snapshot) = result {
                *window.imp().home_snapshot.borrow_mut() = Some(snapshot);
                window.render_home();
            }
        });
    }

    fn refresh_home_devices(&self) {
        if self.imp().home_device_refreshing.replace(true) {
            self.imp().home_device_refresh_pending.set(true);
            return;
        }
        if self.imp().home_snapshot.borrow().is_none() {
            self.imp().home_device_refreshing.set(false);
            self.refresh_home_full();
            return;
        }
        let weak = self.downgrade();
        glib::spawn_future_local(async move {
            let devices = gio::spawn_blocking(backend::list_yubikeys_fast).await;
            let Some(window) = weak.upgrade() else {
                return;
            };
            window.imp().home_device_refreshing.set(false);
            if let Ok(devices) = devices {
                let changed = {
                    let mut stored = window.imp().home_snapshot.borrow_mut();
                    let Some(snapshot) = stored.as_mut() else {
                        return;
                    };
                    if snapshot.devices.as_ref().ok() != devices.as_ref().ok()
                        || snapshot.devices.as_ref().err() != devices.as_ref().err()
                    {
                        snapshot.devices = devices;
                        true
                    } else {
                        false
                    }
                };
                if changed {
                    window.render_home();
                }
            }
            if window.imp().home_device_refresh_pending.replace(false) {
                window.refresh_home_devices();
            }
        });
    }

    fn render_home(&self) {
        let snapshot = self.imp().home_snapshot.borrow().clone();
        let Some(snapshot) = snapshot else {
            return;
        };
        let inspected = self
            .imp()
            .ssh_results
            .borrow()
            .values()
            .filter_map(|result| result.as_ref().ok())
            .map(Vec::len)
            .reduce(|left, right| left + right);
        if let Some(home) = self.imp().home.borrow().as_ref() {
            home.render(&snapshot, inspected);
        }
    }

    fn schedule_home_device_refresh(&self) {
        if let Some(source) = self.imp().home_debounce_source.borrow_mut().take() {
            source.remove();
        }
        let weak = self.downgrade();
        let source = glib::timeout_add_local_once(Duration::from_millis(350), move || {
            if let Some(window) = weak.upgrade() {
                window.imp().home_debounce_source.borrow_mut().take();
                window.refresh_home_devices();
            }
        });
        *self.imp().home_debounce_source.borrow_mut() = Some(source);
    }

    fn start_device_monitor(&self) {
        let weak = self.downgrade();
        if let Ok(process) = device_monitor::start(move || {
            if let Some(window) = weak.upgrade() {
                window.schedule_home_device_refresh();
            }
        }) {
            *self.imp().device_monitor.borrow_mut() = Some(process);
        }
        let weak = self.downgrade();
        let source = glib::timeout_add_local(Duration::from_secs(5), move || {
            let Some(window) = weak.upgrade() else {
                return glib::ControlFlow::Break;
            };
            let on_home = window
                .imp()
                .stack
                .borrow()
                .as_ref()
                .and_then(|stack| stack.visible_child_name())
                .is_some_and(|name| name.as_str() == "home");
            if on_home && window.is_active() {
                window.refresh_home_devices();
            }
            glib::ControlFlow::Continue
        });
        *self.imp().home_poll_source.borrow_mut() = Some(source);
    }

    fn refresh_security_page(&self, visible: &str) {
        let (page, groups) = match visible {
            "login" if !self.imp().login_refreshing.replace(true) => (
                self.imp().login.borrow().as_ref().cloned(),
                &self.imp().login_groups,
            ),
            "sudo" if !self.imp().sudo_refreshing.replace(true) => (
                self.imp().sudo.borrow().as_ref().cloned(),
                &self.imp().sudo_groups,
            ),
            _ => return,
        };
        let Some(page) = page else {
            match visible {
                "login" => self.imp().login_refreshing.set(false),
                "sudo" => self.imp().sudo_refreshing.set(false),
                _ => {}
            }
            return;
        };
        clear_groups(&page, groups);
        let loading = adw::PreferencesGroup::new();
        loading.add(&action_row_with_icon(
            &i18n("Checking connected YubiKeys"),
            &i18n("Reading account security settings…"),
            "view-refresh-symbolic",
        ));
        page.add(&loading);
        *groups.borrow_mut() = vec![loading];

        let selected_page = visible.to_string();
        let weak = self.downgrade();
        glib::spawn_future_local(async move {
            let needs_sudo_policy = selected_page == "sudo";
            let result = gio::spawn_blocking(move || {
                let username =
                    backend::current_user().unwrap_or_else(|_| i18n("unknown"));
                let state = backend::security_state();
                SecurityPageSnapshot {
                    devices: backend::list_yubikeys_for_security(),
                    passwordless_sudo: needs_sudo_policy
                        .then(|| state.passwordless_sudo_for(&username)),
                    enrollments: state.enrollments,
                    username,
                }
            })
            .await;
            let Some(window) = weak.upgrade() else {
                return;
            };
            match selected_page.as_str() {
                "login" => window.imp().login_refreshing.set(false),
                "sudo" => window.imp().sudo_refreshing.set(false),
                _ => {}
            }
            let groups = match selected_page.as_str() {
                "login" => &window.imp().login_groups,
                "sudo" => &window.imp().sudo_groups,
                _ => return,
            };
            clear_groups(&page, groups);
            match result {
                Ok(snapshot) => {
                    let rebuilt = match selected_page.as_str() {
                        "login" => rebuild_login(
                            &window,
                            &page,
                            &snapshot.username,
                            snapshot.devices,
                            &snapshot.enrollments,
                        ),
                        "sudo" => rebuild_sudo(
                            &window,
                            &page,
                            &snapshot.username,
                            snapshot.devices,
                            &snapshot.enrollments,
                            snapshot.passwordless_sudo.unwrap_or(false),
                        ),
                        _ => Vec::new(),
                    };
                    *groups.borrow_mut() = rebuilt;
                }
                Err(_) => {
                    let group = adw::PreferencesGroup::new();
                    group.add(&action_row_with_icon(
                        &i18n("Could not list YubiKeys"),
                        &i18n("The enrollment task failed."),
                        "dialog-warning-symbolic",
                    ));
                    page.add(&group);
                    *groups.borrow_mut() = vec![group];
                }
            }
        });
    }

    fn refresh_ssh(&self) {
        if self.imp().ssh_refreshing.replace(true) {
            return;
        }
        let Some(page) = self.imp().ssh.borrow().as_ref().cloned() else {
            self.imp().ssh_refreshing.set(false);
            return;
        };
        clear_groups(&page, &self.imp().ssh_groups);
        let loading = adw::PreferencesGroup::new();
        loading.add(&action_row_with_icon(
            &i18n("Checking SSH keys…"),
            &i18n("Touch the YubiKey if it flashes."),
            "view-refresh-symbolic",
        ));
        page.add(&loading);
        *self.imp().ssh_groups.borrow_mut() = vec![loading];

        let parent: gtk::Window = self.clone().upcast();
        let weak = self.downgrade();
        glib::spawn_future_local(async move {
            let result = progress_dialog::run_with_progress(
                &parent,
                &i18n("Checking SSH keys… Touch the YubiKey if it flashes."),
                || Ok((ssh::agent_status(), ssh::list_fido_devices())),
            )
            .await;
            let Some(window) = weak.upgrade() else {
                return;
            };
            window.imp().ssh_refreshing.set(false);
            clear_groups(&page, &window.imp().ssh_groups);
            match result {
                Ok((agent, devices)) => {
                    *window.imp().ssh_groups.borrow_mut() =
                        rebuild_ssh(&window, &page, agent, devices);
                }
                Err(error) => {
                    let group = adw::PreferencesGroup::new();
                    group.add(&action_row_with_icon(
                        &i18n("Could not check SSH keys"),
                        &error,
                        "dialog-warning-symbolic",
                    ));
                    page.add(&group);
                    *window.imp().ssh_groups.borrow_mut() = vec![group];
                }
            }
        });
    }
}

struct SecurityPageSnapshot {
    username: String,
    devices: Result<Vec<YubiKey>, String>,
    enrollments: Vec<Enrollment>,
    passwordless_sudo: Option<bool>,
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

#[derive(Clone, Copy)]
enum CapabilityState {
    Enabled,
    Disabled,
}

fn capability_badge(name: &str, state: CapabilityState) -> gtk::Label {
    let (symbol, css_class, tooltip) = match state {
        CapabilityState::Enabled => (
            "✓",
            "success",
            i18n_fmt(&i18n("{0} is enabled for this YubiKey"), &[name]),
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

fn rebuild_git(
    window: &YubiKeyManagerWindow,
    page: &adw::PreferencesPage,
) -> Vec<adw::PreferencesGroup> {
    let status = git_signing::status();
    let devices_group = adw::PreferencesGroup::builder()
        .title(i18n("YubiKeys"))
        .build();
    match ssh::list_fido_devices() {
        Ok(devices) if devices.is_empty() => devices_group.add(&action_row_with_icon(
            &i18n("No FIDO security key detected"),
            &i18n("Insert a YubiKey, then press Refresh."),
            "dialog-information-symbolic",
        )),
        Ok(devices) => {
            for device in devices {
                let inspected_count = window
                    .imp()
                    .ssh_results
                    .borrow()
                    .get(&device.path)
                    .and_then(|result| result.as_ref().ok())
                    .map(Vec::len);
                let row = adw::ActionRow::builder()
                    .title(&device.description)
                    .subtitle(match inspected_count {
                        Some(0) => i18n("No resident SSH keys found"),
                        Some(count) => i18n_fmt(
                            &i18n("{0} SSH keys loaded"),
                            &[&count.to_string()],
                        ),
                        None => device.path.clone(),
                    })
                    .build();
                row.add_prefix(
                    &gtk::Image::builder()
                        .icon_name("dialog-password-symbolic")
                        .build(),
                );
                let load = gtk::Button::builder()
                    .label(i18n("Load keys"))
                    .valign(gtk::Align::Center)
                    .build();
                let path = device.path.clone();
                let weak = window.downgrade();
                load.connect_clicked(move |button| {
                    let Some(parent) = button.root().and_downcast::<gtk::Window>() else {
                        return;
                    };
                    let path = path.clone();
                    let weak = weak.clone();
                    glib::spawn_future_local(async move {
                        let Some(pin) = request_fido_pin_for(
                            &parent,
                            &i18n("Load SSH keys"),
                            &i18n("Enter the FIDO PIN. Touch the YubiKey if it flashes."),
                            &i18n("Load"),
                        )
                        .await
                        else {
                            return;
                        };
                        let task_path = path.clone();
                        let result = progress_dialog::run_with_progress(
                            &parent,
                            &i18n("Loading SSH keys… Touch the YubiKey if it flashes."),
                            move || ssh::inspect_resident_ssh(&task_path, pin.as_str()),
                        )
                        .await;
                        if let Err(error) = &result {
                            show_error(&parent, error);
                        }
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
                row.add_suffix(&load);
                devices_group.add(&row);
            }
        }
        Err(error) => devices_group.add(&action_row_with_icon(
            &i18n("FIDO device discovery is unavailable"),
            &error,
            "dialog-warning-symbolic",
        )),
    }
    page.add(&devices_group);

    let credentials = window
        .imp()
        .ssh_results
        .borrow()
        .values()
        .filter_map(|result| result.as_ref().ok())
        .flatten()
        .cloned()
        .collect::<Vec<_>>();
    let key_group = adw::PreferencesGroup::builder()
        .title(i18n("Commit signing"))
        .description(i18n("Choose one SSH key. The selection takes effect immediately for this user's Git commits."))
        .build();
    let no_signing = adw::ActionRow::builder()
        .title(i18n("No signing"))
        .subtitle(i18n("Do not sign new commits by default"))
        .activatable(status.available)
        .sensitive(status.available)
        .build();
    let first_check = gtk::CheckButton::new();
    first_check.set_active(!status.enabled());
    no_signing.add_prefix(&first_check);
    no_signing.set_activatable_widget(Some(&first_check));
    key_group.add(&no_signing);
    let weak = window.downgrade();
    first_check.connect_toggled(move |check| {
        if check.is_active() {
            if let Some(window) = weak.upgrade() {
                window.set_git_signing(None, check);
            }
        }
    });

    for credential in &credentials {
        let selector = git_signing::signing_selector(
            &credential.public_key,
            credential.local_handle_path.as_deref(),
            credential.loaded_in_agent,
        );
        let title = credential
            .local_label
            .clone()
            .filter(|label| !label.is_empty())
            .or_else(|| (!credential.username.is_empty()).then(|| credential.username.clone()))
            .unwrap_or_else(|| i18n("Unnamed SSH credential"));
        let availability = if credential.local_handle_path.is_some() {
            i18n("Available through local key handle")
        } else if credential.loaded_in_agent {
            i18n("Available through SSH agent")
        } else {
            i18n("Load this resident key into the SSH agent before signing")
        };
        let row = adw::ActionRow::builder()
            .title(title)
            .subtitle(format!(
                "{} · {} · {}",
                credential.algorithm, credential.fingerprint, availability
            ))
            .activatable(selector.is_ok() && status.available)
            .sensitive(selector.is_ok() && status.available)
            .build();
        let check = gtk::CheckButton::new();
        check.set_group(Some(&first_check));
        check.set_active(
            status.enabled()
                && selector.as_ref().ok().map(String::as_str)
                    == status.values.signing_key.as_deref(),
        );
        row.add_prefix(&check);
        row.set_activatable_widget(Some(&check));
        let credential = credential.clone();
        let weak = window.downgrade();
        check.connect_toggled(move |check| {
            if check.is_active() {
                if let Some(window) = weak.upgrade() {
                    window.set_git_signing(Some(credential.clone()), check);
                }
            }
        });
        key_group.add(&row);
    }
    page.add(&key_group);

    let mut groups = vec![devices_group, key_group];
    if status.enabled() {
        let configured_public_key = git_signing::configured_public_key(&status.values)
            .or_else(|| {
                credentials
                    .iter()
                    .find(|credential| {
                        git_signing::signing_selector(
                            &credential.public_key,
                            credential.local_handle_path.as_deref(),
                            credential.loaded_in_agent,
                        )
                        .ok()
                        .as_deref()
                            == status.values.signing_key.as_deref()
                    })
                    .map(|credential| credential.public_key.clone())
            });
        if let Some(public_key) = configured_public_key {
            let ready_group = adw::PreferencesGroup::new();
            let ready = adw::ActionRow::builder()
                .title(i18n("Git signing is enabled"))
                .subtitle(format!(
                    "{}\n{}",
                    i18n("Add this public key to GitHub as a Signing Key."),
                    public_key
                ))
                .css_classes(["success"])
                .build();
            ready.add_prefix(
                &gtk::Image::builder()
                    .icon_name("emblem-ok-symbolic")
                    .css_classes(["success"])
                    .build(),
            );
            let copy = gtk::Button::builder()
                .label(i18n("Copy public key"))
                .valign(gtk::Align::Center)
                .build();
            let copy_key = public_key.clone();
            copy.connect_clicked(move |button| {
                button.clipboard().set_text(&copy_key);
            });
            ready.add_suffix(&copy);
            if let Some(credential) = credentials.iter().find(|credential| {
                git_signing::signing_selector(
                    &credential.public_key,
                    credential.local_handle_path.as_deref(),
                    credential.loaded_in_agent,
                )
                .ok()
                .as_deref()
                    == status.values.signing_key.as_deref()
            }) {
                let test = gtk::Button::builder()
                    .label(i18n("Test signing"))
                    .valign(gtk::Align::Center)
                    .build();
                let credential = credential.clone();
                let weak = window.downgrade();
                test.connect_clicked(move |button| {
                    if let Some(window) = weak.upgrade() {
                        window.test_git_credential(button, credential.clone());
                    }
                });
                ready.add_suffix(&test);
            }
            ready_group.add(&ready);
            page.add(&ready_group);
            groups.push(ready_group);
        }
    }
    groups
}

impl YubiKeyManagerWindow {
    fn set_git_signing(
        &self,
        credential: Option<ssh::ResidentSshCredential>,
        check: &gtk::CheckButton,
    ) {
        let Some(parent) = check.root().and_downcast::<gtk::Window>() else {
            return;
        };
        let task = match credential {
            Some(credential) => {
                let selector = match git_signing::signing_selector(
                    &credential.public_key,
                    credential.local_handle_path.as_deref(),
                    credential.loaded_in_agent,
                ) {
                    Ok(selector) => selector,
                    Err(error) => {
                        show_error(&parent, &error);
                        self.refresh();
                        return;
                    }
                };
                Box::new(move || git_signing::select_key(&selector))
                    as Box<dyn FnOnce() -> Result<(), String> + Send>
            }
            None => Box::new(git_signing::disable),
        };
        let weak = self.downgrade();
        glib::spawn_future_local(async move {
            let result = progress_dialog::run_with_progress(
                &parent,
                &i18n("Updating Git commit signing…"),
                task,
            )
            .await;
            if let Some(window) = weak.upgrade() {
                window.refresh();
                if let Err(error) = result {
                    show_error(&window, &error);
                }
            }
        });
    }

    fn test_git_credential(
        &self,
        button: &gtk::Button,
        credential: ssh::ResidentSshCredential,
    ) {
        let Some(parent) = button.root().and_downcast::<gtk::Window>() else {
            return;
        };
        glib::spawn_future_local(async move {
            let pin = if credential.local_handle_path.is_some() {
                request_fido_pin_for(
                    &parent,
                    &i18n("Test Git signing"),
                    &i18n("Enter the FIDO PIN, then touch the selected YubiKey when it flashes. No commit will be created."),
                    &i18n("Test"),
                )
                .await
            } else {
                Some(Zeroizing::new(String::new()))
            };
            let Some(pin) = pin else {
                return;
            };
            let public_key = credential.public_key.clone();
            let handle = credential.local_handle_path.clone();
            let result = progress_dialog::run_with_progress(
                &parent,
                &i18n("Touch the YubiKey. Creating and verifying a temporary Git-format SSH signature…"),
                move || {
                    ssh::test_git_signing(
                        &public_key,
                        handle.as_deref(),
                        (!pin.is_empty()).then_some(pin.as_str()),
                    )
                },
            )
            .await;
            match result {
                Ok(()) => show_message(
                    &parent,
                    &i18n("Git signing test passed"),
                    &i18n("The selected credential created a valid Git-format SSH signature. No repository or commit was changed."),
                ),
                Err(error) => show_error(&parent, &error),
            }
        });
    }
}

fn rebuild_login(
    window: &YubiKeyManagerWindow,
    page: &adw::PreferencesPage,
    username: &str,
    devices: Result<Vec<YubiKey>, String>,
    enrollments: &[Enrollment],
) -> Vec<adw::PreferencesGroup> {
    let group = adw::PreferencesGroup::builder()
        .title(i18n("Unlock the current user"))
        .description(i18n_fmt(
            &i18n("Choose which YubiKeys may unlock {0}. Password sign-in remains available."),
            &[username],
        ))
        .build();
    match devices {
        Ok(keys) if keys.is_empty() && enrollments.iter().all(|item| item.username != username) => group.add(
            &adw::ActionRow::builder()
                .title(i18n("Insert a YubiKey"))
                .subtitle(i18n("A key must be connected before it can be enrolled."))
                .build(),
        ),
        Ok(mut keys) => {
            add_disconnected_enrollments(&mut keys, username, "gdm", enrollments);
            for key in keys {
                let active = enrollments.iter().any(|item| {
                    item.username == username
                        && item.serial == key.serial
                        && item.purpose == "gdm"
                });
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
    enrollments: &[Enrollment],
    passwordless: bool,
) -> Vec<adw::PreferencesGroup> {
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
                && enrollments
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
            add_disconnected_enrollments(&mut keys, username, "sudo", enrollments);
            for key in keys {
                let active = enrollments.iter().any(|item| {
                    item.username == username
                        && item.serial == key.serial
                        && item.purpose == "sudo"
                });
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
    enrollments: &[Enrollment],
) {
    for enrollment in enrollments
        .iter()
        .filter(|item| item.username == username && item.purpose == purpose)
    {
        if !keys.iter().any(|key| key.serial == enrollment.serial) {
            keys.push(YubiKey {
                name: i18n("YubiKey"),
                serial: enrollment.serial.clone(),
                firmware: String::new(),
                interfaces: String::new(),
            });
        }
    }
}

fn rebuild_ssh(
    window: &YubiKeyManagerWindow,
    page: &adw::PreferencesPage,
    agent: ssh::AgentStatus,
    fido_devices: Result<Vec<ssh::FidoDevice>, String>,
) -> Vec<adw::PreferencesGroup> {
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

    let create_group = rebuild_ssh_create(window, &fido_devices);
    page.add(&create_group);

    let devices_group = adw::PreferencesGroup::builder()
        .title(i18n("Resident SSH credentials"))
        .description(i18n("Inspect, load, export, test, remove from the agent, or permanently delete resident SSH credentials."))
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
                            &i18n("Inspecting resident SSH credentials… Touch the YubiKey if it flashes."),
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
                                let resident_name = if credential.username.is_empty() {
                                    i18n("Unnamed SSH credential")
                                } else {
                                    credential.username.clone()
                                };
                                let credential_title = credential
                                    .local_label
                                    .clone()
                                    .unwrap_or_else(|| resident_name.clone());
                                let credential_subtitle = if credential.local_label.is_some() {
                                    format!(
                                        "{} · {} · {} · {}",
                                        resident_name,
                                        credential.algorithm,
                                        credential.application,
                                        credential.fingerprint
                                    )
                                } else {
                                    format!(
                                        "{} · {} · {}",
                                        credential.algorithm,
                                        credential.application,
                                        credential.fingerprint
                                    )
                                };
                                let credential_row = adw::ActionRow::builder()
                                    .title(credential_title)
                                    .subtitle(credential_subtitle)
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
                                let git_status = git_signing::status();
                                let used_for_git = git_status.enabled()
                                    && git_status
                                    .values
                                    .signing_key
                                    .as_deref()
                                    .is_some_and(|configured| {
                                        git_signing::signing_selector(
                                            &credential.public_key,
                                            credential.local_handle_path.as_deref(),
                                            credential.loaded_in_agent,
                                        )
                                        .ok()
                                        .as_deref()
                                            == Some(configured)
                                    });
                                if used_for_git {
                                    credential_row.add_suffix(&capability_badge(
                                        "Git",
                                        CapabilityState::Enabled,
                                    ));
                                }
                                credential_row.add_suffix(&credential_actions(
                                    window,
                                    &device,
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
    request_fido_pin_with_appearance(
        parent,
        heading,
        body,
        accept_label,
        adw::ResponseAppearance::Suggested,
    )
    .await
}

async fn request_fido_pin_with_appearance(
    parent: &gtk::Window,
    heading: &str,
    body: &str,
    accept_label: &str,
    appearance: adw::ResponseAppearance,
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
    dialog.set_response_appearance("accept", appearance);
    let response = dialog.clone().choose_future(Some(parent)).await;
    if response.as_str() != "accept" || entry.text().is_empty() {
        return None;
    }
    Some(Zeroizing::new(entry.text().to_string()))
}

fn credential_actions(
    window: &YubiKeyManagerWindow,
    device: &ssh::FidoDevice,
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
    let delete = gtk::Button::with_label(&i18n("Permanently delete from YubiKey…"));
    remove.set_sensitive(credential.loaded_in_agent);
    for button in [&copy, &export, &test, &remove] {
        button.set_halign(gtk::Align::Fill);
        content.append(button);
    }
    content.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
    delete.set_halign(gtk::Align::Fill);
    delete.add_css_class("destructive-action");
    content.append(&delete);
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

    let device = device.clone();
    let credential = credential.clone();
    let weak = window.downgrade();
    delete.connect_clicked(move |button| {
        let Some(parent) = button.root().and_downcast::<gtk::Window>() else {
            return;
        };
        if let Some(popover) = button
            .ancestor(gtk::Popover::static_type())
            .and_downcast::<gtk::Popover>()
        {
            popover.popdown();
        }
        let device = device.clone();
        let credential = credential.clone();
        let weak = weak.clone();
        glib::spawn_future_local(async move {
            if !confirm_resident_deletion(&parent, &device, &credential).await {
                return;
            }
            let Some(pin) = request_fido_pin_with_appearance(
                &parent,
                &i18n("Enter the selected YubiKey FIDO PIN"),
                &i18n("This PIN authorizes permanent deletion of the exact credential shown in the previous step. It is never stored."),
                &i18n("Permanently delete"),
                adw::ResponseAppearance::Destructive,
            )
            .await
            else {
                return;
            };
            let device_path = device.path.clone();
            let task_device = device.path.clone();
            let task_credential = credential.clone();
            let result = progress_dialog::run_with_progress(
                &parent,
                &i18n("Verifying and permanently deleting the selected resident credential. Do not unplug the YubiKey…"),
                move || {
                    ssh::delete_resident_credential(
                        &task_device,
                        &task_credential,
                        pin.as_str(),
                    )
                },
            )
            .await;
            match result {
                Ok(ssh::DeleteOutcome::Deleted { credentials }) => {
                    if let Some(window) = weak.upgrade() {
                        window
                            .imp()
                            .ssh_results
                            .borrow_mut()
                            .insert(device_path, Ok(credentials));
                        window.refresh();
                        show_message(
                            &window,
                            &i18n("Resident credential permanently deleted"),
                            &i18n("The exact resident credential is no longer present on the selected YubiKey. Local .pub or key-handle files and any SSH-agent entry were not removed."),
                        );
                    }
                }
                Ok(ssh::DeleteOutcome::Unknown { message }) => show_error_with_heading(
                    &parent,
                    &i18n("Deletion result is unknown"),
                    &message,
                ),
                Err(error) => show_error(&parent, &error),
            }
        });
    });
    menu
}

async fn confirm_resident_deletion(
    parent: &gtk::Window,
    device: &ssh::FidoDevice,
    credential: &ssh::ResidentSshCredential,
) -> bool {
    let suffix = credential
        .fingerprint
        .chars()
        .rev()
        .take(8)
        .collect::<String>()
        .chars()
        .rev()
        .collect::<String>();
    let username = if credential.username.is_empty() {
        i18n("Unnamed SSH credential")
    } else {
        credential.username.clone()
    };
    let details = gtk::Label::builder()
        .label(i18n_fmt(
            &i18n("YubiKey: {0}\nDevice: {1}\nApplication: {2}\nResident username: {3}\nAlgorithm: {4}\nFingerprint: {5}"),
            &[
                &device.description,
                &device.path,
                &credential.application,
                &username,
                &credential.algorithm,
                &credential.fingerprint,
            ],
        ))
        .selectable(true)
        .xalign(0.0)
        .wrap(true)
        .build();
    let confirmation = adw::EntryRow::builder()
        .title(i18n_fmt(
            &i18n("Type {0} to confirm"),
            &[&suffix],
        ))
        .activates_default(true)
        .build();
    let group = adw::PreferencesGroup::new();
    group.add(&details);
    group.add(&confirmation);
    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(12)
        .width_request(580)
        .build();
    content.append(&group);
    let dialog = adw::AlertDialog::builder()
        .heading(i18n("Permanently delete this resident SSH credential?"))
        .body(i18n("This removes the private credential from the selected YubiKey and cannot be undone. It does not remove local files or an identity already loaded in the SSH agent."))
        .extra_child(&content)
        .close_response("cancel")
        .default_response("delete")
        .build();
    dialog.add_response("cancel", &i18n("Cancel"));
    dialog.add_response("delete", &i18n("Continue to PIN"));
    dialog.set_response_appearance("delete", adw::ResponseAppearance::Destructive);
    dialog.set_response_enabled("delete", false);
    let weak_dialog = dialog.downgrade();
    confirmation.connect_changed(move |entry| {
        if let Some(dialog) = weak_dialog.upgrade() {
            dialog.set_response_enabled("delete", entry.text().as_str() == suffix);
        }
    });
    dialog.choose_future(Some(parent)).await.as_str() == "delete"
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
    show_error_with_heading(widget, &i18n("YubiKey configuration failed"), message);
}

fn show_error_with_heading<W: IsA<gtk::Widget>>(
    widget: &W,
    heading: &str,
    message: &str,
) {
    let dialog = adw::AlertDialog::builder()
        .heading(heading)
        .body(message)
        .build();
    dialog.add_response("close", &i18n("Close"));
    dialog.present(widget.root().and_downcast_ref::<gtk::Window>());
}
