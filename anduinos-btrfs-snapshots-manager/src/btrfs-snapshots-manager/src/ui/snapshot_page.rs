use std::cell::{Cell, RefCell};
use std::collections::HashSet;

use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use crate::dbus_client::{RecoveryEngineStatus, SnapshotsManagerHelperClient, VerificationResult};
use crate::i18n::{tr, trf};

use super::personal_history;
use super::snapshot_model::{PagePresentation, SnapshotCapabilities, SnapshotItem, SnapshotScope};

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct SnapshotPage {
        pub scope: Cell<SnapshotScope>,
        pub parent: glib::WeakRef<adw::ApplicationWindow>,
        pub items: RefCell<Vec<SnapshotItem>>,
        pub selected: RefCell<HashSet<String>>,
        pub query: RefCell<String>,
        pub available: Cell<bool>,
        pub refreshing: Cell<bool>,
        pub refresh_pending: Cell<bool>,
        pub selection_mode: Cell<bool>,
        pub controls: RefCell<Option<gtk::Box>>,
        pub search: RefCell<Option<gtk::SearchEntry>>,
        pub create: RefCell<Option<gtk::Button>>,
        pub selection_toggle: RefCell<Option<gtk::ToggleButton>>,
        pub selection_revealer: RefCell<Option<gtk::Revealer>>,
        pub selection_label: RefCell<Option<gtk::Label>>,
        pub delete_selected: RefCell<Option<gtk::Button>>,
        pub stack: RefCell<Option<gtk::Stack>>,
        pub list: RefCell<Option<gtk::ListBox>>,
        pub empty: RefCell<Option<adw::StatusPage>>,
        pub no_matches: RefCell<Option<adw::StatusPage>>,
        pub unsupported: RefCell<Option<adw::StatusPage>>,
        pub error: RefCell<Option<adw::StatusPage>>,
        pub issue_banner: RefCell<Option<adw::Banner>>,
        pub pending_banner: RefCell<Option<adw::Banner>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for SnapshotPage {
        const NAME: &'static str = "SnapshotsManagerSnapshotPage";
        type Type = super::SnapshotPage;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for SnapshotPage {
        fn dispose(&self) {
            self.items.borrow_mut().clear();
            self.selected.borrow_mut().clear();
            self.controls.borrow_mut().take();
            self.search.borrow_mut().take();
            self.create.borrow_mut().take();
            self.selection_toggle.borrow_mut().take();
            self.selection_revealer.borrow_mut().take();
            self.selection_label.borrow_mut().take();
            self.delete_selected.borrow_mut().take();
            self.stack.borrow_mut().take();
            self.list.borrow_mut().take();
            self.empty.borrow_mut().take();
            self.no_matches.borrow_mut().take();
            self.unsupported.borrow_mut().take();
            self.error.borrow_mut().take();
            self.issue_banner.borrow_mut().take();
            self.pending_banner.borrow_mut().take();
        }
    }

    impl WidgetImpl for SnapshotPage {}
    impl BoxImpl for SnapshotPage {}
}

glib::wrapper! {
    pub struct SnapshotPage(ObjectSubclass<imp::SnapshotPage>)
        @extends gtk::Widget, gtk::Box,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl SnapshotScope {
    fn noun(self) -> String {
        match self {
            Self::System => tr("system snapshot"),
            Self::Home => tr("Home snapshot"),
        }
    }

    fn automatic_title(self) -> String {
        match self {
            Self::System => tr("Automatic System Snapshots"),
            Self::Home => tr("Automatic Home Snapshots"),
        }
    }
}

impl SnapshotPage {
    pub fn new(parent: &adw::ApplicationWindow, scope: SnapshotScope) -> Self {
        let page: Self = glib::Object::builder()
            .property("orientation", gtk::Orientation::Vertical)
            .build();
        page.imp().scope.set(scope);
        page.imp().parent.set(Some(parent));
        page.setup_ui();
        page
    }

    pub fn widget(&self) -> &gtk::Widget {
        self.upcast_ref()
    }

    pub fn focus_search(&self) {
        if let Some(search) = self.imp().search.borrow().as_ref() {
            search.grab_focus();
        }
    }

    pub fn create_snapshot(&self) {
        if let Some(button) = self.imp().create.borrow().as_ref() {
            button.emit_clicked();
        }
    }

    pub fn add_compact_setters(&self, breakpoint: &adw::Breakpoint) {
        if let Some(controls) = self.imp().controls.borrow().as_ref() {
            breakpoint.add_setter(
                controls,
                "orientation",
                Some(&gtk::Orientation::Vertical.to_value()),
            );
        }
    }

    fn setup_ui(&self) {
        self.set_spacing(0);
        let scope = self.imp().scope.get();

        let issue_banner = adw::Banner::new("");
        issue_banner.set_revealed(false);
        self.append(&issue_banner);
        let pending_banner = adw::Banner::new("");
        pending_banner.set_button_label(Some(&tr("Cancel Rollback")));
        pending_banner.set_revealed(false);
        let weak = self.downgrade();
        pending_banner.connect_button_clicked(move |_| {
            if let Some(page) = weak.upgrade() {
                page.cancel_pending_rollback();
            }
        });
        self.append(&pending_banner);

        let controls = gtk::Box::new(gtk::Orientation::Horizontal, 10);
        controls.set_margin_top(18);
        controls.set_margin_bottom(12);
        controls.set_margin_start(18);
        controls.set_margin_end(18);
        let primary_actions = gtk::Box::new(gtk::Orientation::Horizontal, 10);
        let create = gtk::Button::with_label(&tr("Create Snapshot Now"));
        create.add_css_class("suggested-action");
        create.set_tooltip_text(Some(&trf("Create a {0} now", &[&scope.noun()])));
        let automate = gtk::Button::with_label(&tr("Automatic Snapshots"));
        automate.set_tooltip_text(Some(&scope.automatic_title()));
        primary_actions.append(&create);
        primary_actions.append(&automate);
        controls.append(&primary_actions);

        let search_actions = gtk::Box::new(gtk::Orientation::Horizontal, 6);
        search_actions.set_hexpand(true);
        search_actions.set_halign(gtk::Align::Fill);
        let search = gtk::SearchEntry::new();
        search.set_placeholder_text(Some(&tr("Search snapshots")));
        search.set_hexpand(true);
        let selection_toggle = gtk::ToggleButton::new();
        selection_toggle.set_icon_name("object-select-symbolic");
        selection_toggle.set_tooltip_text(Some(&tr("Select Snapshots")));
        search_actions.append(&search);
        search_actions.append(&selection_toggle);
        controls.append(&search_actions);

        let controls_clamp = adw::Clamp::new();
        controls_clamp.set_maximum_size(920);
        controls_clamp.set_child(Some(&controls));
        self.append(&controls_clamp);

        let selection_bar = gtk::Box::new(gtk::Orientation::Horizontal, 12);
        selection_bar.add_css_class("toolbar");
        selection_bar.set_margin_start(18);
        selection_bar.set_margin_end(18);
        selection_bar.set_margin_bottom(8);
        let selection_label = gtk::Label::new(Some(&tr("No snapshots selected")));
        selection_label.set_hexpand(true);
        selection_label.set_halign(gtk::Align::Start);
        let cancel_selection = gtk::Button::with_label(&tr("Cancel"));
        cancel_selection.add_css_class("flat");
        let delete_selected = gtk::Button::with_label(&tr("Delete Selected"));
        delete_selected.add_css_class("destructive-action");
        delete_selected.set_sensitive(false);
        selection_bar.append(&selection_label);
        selection_bar.append(&cancel_selection);
        selection_bar.append(&delete_selected);
        let selection_revealer = gtk::Revealer::new();
        selection_revealer.set_transition_type(gtk::RevealerTransitionType::SlideDown);
        selection_revealer.set_child(Some(&selection_bar));
        self.append(&selection_revealer);

        let stack = gtk::Stack::new();
        stack.set_vexpand(true);
        stack.set_transition_type(gtk::StackTransitionType::Crossfade);
        let loading = status_page("content-loading-symbolic", &tr("Loading snapshots…"), None);
        stack.add_named(&loading, Some("loading"));
        let empty = status_page(
            "document-open-recent-symbolic",
            &tr("No snapshots yet"),
            Some(&tr("Create one now or turn on automatic snapshots.")),
        );
        stack.add_named(&empty, Some("empty"));
        let no_matches = status_page(
            "system-search-symbolic",
            &tr("No matching snapshots"),
            Some(&tr("Try a different name, date, or snapshot reason.")),
        );
        stack.add_named(&no_matches, Some("no-matches"));
        let unsupported = status_page(
            "drive-harddisk-symbolic",
            &tr("Snapshots are not available on this computer"),
            Some(&tr(
                "Disk Snapshots Manager requires the standard AnduinOS Btrfs layout.",
            )),
        );
        stack.add_named(&unsupported, Some("unsupported"));
        let error = status_page(
            "dialog-error-symbolic",
            &tr("Snapshots could not be loaded"),
            None,
        );
        stack.add_named(&error, Some("error"));

        let scrolled = gtk::ScrolledWindow::new();
        let clamp = adw::Clamp::new();
        clamp.set_maximum_size(880);
        let list = gtk::ListBox::new();
        list.add_css_class("boxed-list");
        list.set_selection_mode(gtk::SelectionMode::None);
        list.set_margin_top(6);
        list.set_margin_bottom(24);
        list.set_margin_start(18);
        list.set_margin_end(18);
        clamp.set_child(Some(&list));
        scrolled.set_child(Some(&clamp));
        stack.add_named(&scrolled, Some("content"));
        self.append(&stack);

        *self.imp().controls.borrow_mut() = Some(controls);
        *self.imp().search.borrow_mut() = Some(search.clone());
        *self.imp().create.borrow_mut() = Some(create.clone());
        *self.imp().selection_toggle.borrow_mut() = Some(selection_toggle.clone());
        *self.imp().selection_revealer.borrow_mut() = Some(selection_revealer);
        *self.imp().selection_label.borrow_mut() = Some(selection_label);
        *self.imp().delete_selected.borrow_mut() = Some(delete_selected.clone());
        *self.imp().stack.borrow_mut() = Some(stack);
        *self.imp().list.borrow_mut() = Some(list);
        *self.imp().empty.borrow_mut() = Some(empty);
        *self.imp().no_matches.borrow_mut() = Some(no_matches);
        *self.imp().unsupported.borrow_mut() = Some(unsupported);
        *self.imp().error.borrow_mut() = Some(error);
        *self.imp().issue_banner.borrow_mut() = Some(issue_banner);
        *self.imp().pending_banner.borrow_mut() = Some(pending_banner);

        let weak = self.downgrade();
        search.connect_search_changed(move |entry| {
            if let Some(page) = weak.upgrade() {
                *page.imp().query.borrow_mut() = entry.text().to_string();
                page.render();
            }
        });
        let weak = self.downgrade();
        create.connect_clicked(move |_| {
            if let Some(page) = weak.upgrade() {
                page.show_create_dialog();
            }
        });
        let weak = self.downgrade();
        automate.connect_clicked(move |_| {
            if let Some(page) = weak.upgrade()
                && let Some(parent) = page.parent()
            {
                super::automation_dialog::show(&parent, page.imp().scope.get());
            }
        });
        let weak = self.downgrade();
        selection_toggle.connect_toggled(move |toggle| {
            if let Some(page) = weak.upgrade() {
                page.set_selection_mode(toggle.is_active());
            }
        });
        let weak = self.downgrade();
        cancel_selection.connect_clicked(move |_| {
            if let Some(page) = weak.upgrade() {
                page.set_selection_mode(false);
            }
        });
        let weak = self.downgrade();
        delete_selected.connect_clicked(move |_| {
            if let Some(page) = weak.upgrade() {
                page.confirm_selected_delete();
            }
        });

        let key = gtk::EventControllerKey::new();
        let weak = self.downgrade();
        key.connect_key_pressed(move |_, key, _, _| {
            if key == gtk::gdk::Key::Escape
                && let Some(page) = weak.upgrade()
                && page.imp().selection_mode.get()
            {
                page.set_selection_mode(false);
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        self.add_controller(key);
    }

    fn parent(&self) -> Option<adw::ApplicationWindow> {
        self.imp().parent.upgrade()
    }

    pub fn refresh(&self) {
        if self.imp().refreshing.replace(true) {
            self.imp().refresh_pending.set(true);
            return;
        }
        if self.imp().items.borrow().is_empty()
            && let Some(stack) = self.imp().stack.borrow().as_ref()
        {
            stack.set_visible_child_name("loading");
        }
        let weak = self.downgrade();
        glib::spawn_future_local(async move {
            let loaded = gio::spawn_blocking(|| {
                SnapshotsManagerHelperClient::new()
                    .and_then(|client| client.recovery_engine_status())
            })
            .await
            .map_err(|_| anyhow::anyhow!("The snapshot query stopped unexpectedly"))
            .and_then(|result| result);
            let Some(page) = weak.upgrade() else {
                return;
            };
            page.imp().refreshing.set(false);
            match loaded {
                Ok(status) => page.apply_status(status),
                Err(problem) => page.show_load_error(&problem.to_string()),
            }
            if page.imp().refresh_pending.replace(false) {
                page.refresh();
            }
        });
    }

    fn apply_status(&self, mut status: RecoveryEngineStatus) {
        self.imp().available.set(status.available);
        if !status.available {
            let description = status
                .error
                .clone()
                .or_else(|| {
                    (!status.layout.issues.is_empty()).then(|| status.layout.issues.join("\n"))
                })
                .unwrap_or_else(|| {
                    if let Some(filesystem) = status.layout.root_filesystem.as_deref() {
                        trf("The root filesystem is {0}.", &[filesystem])
                    } else if !status.layout.support.is_empty() {
                        status.layout.support.clone()
                    } else {
                        tr("Disk Snapshots Manager requires the standard AnduinOS Btrfs layout.")
                    }
                });
            if let Some(page) = self.imp().unsupported.borrow().as_ref() {
                page.set_description(Some(&description));
            }
            if let Some(stack) = self.imp().stack.borrow().as_ref() {
                stack.set_visible_child_name("unsupported");
            }
            self.update_banners(&status);
            return;
        }

        let scope = self.imp().scope.get();
        self.update_banners(&status);
        let mut items = match scope {
            SnapshotScope::System => status
                .deployments
                .drain(..)
                .map(|value| {
                    let count = status.system_package_counts.get(&value.id).copied();
                    let mut item = SnapshotItem::from(value);
                    item.summary = count.map(|count| trf("{0} packages", &[&count.to_string()]));
                    item
                })
                .collect::<Vec<_>>(),
            SnapshotScope::Home => status
                .personal_snapshots
                .drain(..)
                .map(|value| {
                    let size = status
                        .personal_sizes
                        .get(&value.id)
                        .and_then(|space| space.referenced_bytes)
                        .map(snapshots_manager_common::format_bytes);
                    let mut item = SnapshotItem::from(value);
                    item.summary = size;
                    item
                })
                .collect::<Vec<_>>(),
        };
        items.sort_by_key(|item| std::cmp::Reverse(item.created_at));
        *self.imp().items.borrow_mut() = items;
        self.imp().selected.borrow_mut().clear();
        self.set_selection_mode(false);
        self.render();
    }

    fn update_banners(&self, status: &RecoveryEngineStatus) {
        let scope = self.imp().scope.get();
        let issue_count = match scope {
            SnapshotScope::System => status.issues.len(),
            SnapshotScope::Home => status.personal_issues.len(),
        };
        if let Some(banner) = self.imp().issue_banner.borrow().as_ref() {
            banner.set_title(&trf(
                "{0} snapshot record(s) need attention",
                &[&issue_count.to_string()],
            ));
            banner.set_revealed(issue_count > 0);
        }
        if let Some(banner) = self.imp().pending_banner.borrow().as_ref() {
            if scope == SnapshotScope::System {
                if let Some(pending) = &status.pending {
                    let target = status
                        .deployments
                        .iter()
                        .find(|item| item.id == pending.target_deployment_id)
                        .map(|item| item.title.as_str())
                        .unwrap_or(&pending.target_deployment_id);
                    banner.set_title(&trf(
                        "Rollback to {0} is prepared ({1})",
                        &[target, &pending.phase],
                    ));
                    banner.set_revealed(true);
                } else {
                    banner.set_revealed(false);
                }
            } else {
                banner.set_revealed(false);
            }
        }
    }

    fn show_load_error(&self, message: &str) {
        self.imp().available.set(false);
        if let Some(page) = self.imp().error.borrow().as_ref() {
            page.set_description(Some(message));
        }
        if let Some(stack) = self.imp().stack.borrow().as_ref() {
            stack.set_visible_child_name("error");
        }
    }

    fn render(&self) {
        if !self.imp().available.get() {
            return;
        }
        let Some(list) = self.imp().list.borrow().clone() else {
            return;
        };
        while let Some(child) = list.first_child() {
            list.remove(&child);
        }
        let needle = self.imp().query.borrow().trim().to_lowercase();
        let visible = self
            .imp()
            .items
            .borrow()
            .iter()
            .filter(|item| {
                needle.is_empty()
                    || item.title.to_lowercase().contains(&needle)
                    || item.reason.to_lowercase().contains(&needle)
                    || item.kind.to_lowercase().contains(&needle)
                    || item.state.to_lowercase().contains(&needle)
            })
            .cloned()
            .collect::<Vec<_>>();
        let presentation = PagePresentation::after_load(
            true,
            self.imp().items.borrow().len(),
            visible.len(),
            &needle,
        );
        if let Some(stack) = self.imp().stack.borrow().as_ref() {
            stack.set_visible_child_name(match presentation {
                PagePresentation::Empty => "empty",
                PagePresentation::NoMatches => "no-matches",
                _ => "content",
            });
        }
        for item in visible {
            list.append(&self.snapshot_row(&item));
        }
        self.update_selection();
    }

    fn snapshot_row(&self, item: &SnapshotItem) -> adw::ActionRow {
        let scope = self.imp().scope.get();
        let capabilities = SnapshotCapabilities::for_item(scope, item);
        let row = adw::ActionRow::new();
        row.set_title(&item.title);
        row.set_subtitle(&snapshot_details(scope, item));

        if self.imp().selection_mode.get() {
            let check = gtk::CheckButton::new();
            check.set_valign(gtk::Align::Center);
            check.set_sensitive(capabilities.can_select());
            check.set_active(self.imp().selected.borrow().contains(&item.id));
            check.set_tooltip_text(Some(&tr("Select snapshot")));
            let id = item.id.clone();
            let weak = self.downgrade();
            check.connect_toggled(move |check| {
                if let Some(page) = weak.upgrade() {
                    if check.is_active() {
                        page.imp().selected.borrow_mut().insert(id.clone());
                    } else {
                        page.imp().selected.borrow_mut().remove(&id);
                    }
                    page.update_selection();
                }
            });
            row.add_prefix(&check);
        }

        let state_icon = gtk::Image::from_icon_name(snapshot_icon(item));
        state_icon.set_tooltip_text(Some(&snapshot_state(item)));
        row.add_prefix(&state_icon);

        let main_action = gtk::Button::with_label(&match scope {
            SnapshotScope::System => tr("Roll Back"),
            SnapshotScope::Home => tr("Browse Files"),
        });
        main_action.set_valign(gtk::Align::Center);
        main_action.set_sensitive(match scope {
            SnapshotScope::System => capabilities.can_restore,
            SnapshotScope::Home => capabilities.can_browse,
        });
        main_action.set_tooltip_text(Some(&match scope {
            SnapshotScope::System => tr("Prepare a safe system rollback"),
            SnapshotScope::Home => tr("Browse files in this snapshot"),
        }));
        if scope == SnapshotScope::System {
            main_action.add_css_class("suggested-action");
        }
        let weak = self.downgrade();
        let item_main = item.clone();
        main_action.connect_clicked(move |_| {
            if let Some(page) = weak.upgrade() {
                match page.imp().scope.get() {
                    SnapshotScope::System => page.verify_then_confirm_rollback(item_main.clone()),
                    SnapshotScope::Home => page.browse(&item_main),
                }
            }
        });
        row.add_suffix(&main_action);

        let group = gio::SimpleActionGroup::new();
        let weak = self.downgrade();
        let item_browse = item.clone();
        add_row_action(&group, "browse", capabilities.can_browse, move || {
            if let Some(page) = weak.upgrade() {
                page.browse(&item_browse);
            }
        });
        let weak = self.downgrade();
        let item_rename = item.clone();
        add_row_action(&group, "rename", capabilities.can_rename, move || {
            if let Some(page) = weak.upgrade() {
                page.show_rename_dialog(&item_rename);
            }
        });
        let weak = self.downgrade();
        let item_verify = item.clone();
        add_row_action(&group, "verify", capabilities.can_verify, move || {
            if let Some(page) = weak.upgrade() {
                page.verify_snapshot(&item_verify.id);
            }
        });
        let weak = self.downgrade();
        let item_pin = item.clone();
        add_row_action(&group, "pin", capabilities.can_pin, move || {
            if let Some(page) = weak.upgrade() {
                page.set_pinned(&item_pin, !item_pin.keep_forever);
            }
        });
        let weak = self.downgrade();
        let item_delete = item.clone();
        add_row_action(&group, "delete", capabilities.can_delete, move || {
            if let Some(page) = weak.upgrade() {
                page.confirm_delete(vec![item_delete.id.clone()]);
            }
        });
        row.insert_action_group("snapshot", Some(&group));

        let menu_model = gio::Menu::new();
        menu_model.append(Some(&tr("Rename")), Some("snapshot.rename"));
        menu_model.append(Some(&tr("Browse Files")), Some("snapshot.browse"));
        menu_model.append(
            Some(&tr("Check Snapshot Availability")),
            Some("snapshot.verify"),
        );
        menu_model.append(
            Some(&if item.keep_forever {
                tr("Allow Smart Cleanup")
            } else {
                tr("Keep Forever")
            }),
            Some("snapshot.pin"),
        );
        let destructive = gio::Menu::new();
        destructive.append(Some(&tr("Delete Snapshot")), Some("snapshot.delete"));
        menu_model.append_section(None, &destructive);
        let menu = gtk::MenuButton::builder()
            .icon_name("view-more-symbolic")
            .tooltip_text(tr("Snapshot Actions"))
            .valign(gtk::Align::Center)
            .menu_model(&menu_model)
            .build();
        row.add_suffix(&menu);
        row
    }

    fn browse(&self, item: &SnapshotItem) {
        let Some(parent) = self.parent() else {
            return;
        };
        match self.imp().scope.get() {
            SnapshotScope::System => {
                personal_history::show_system_snapshot_browser(&parent, &item.id, &item.title)
            }
            SnapshotScope::Home => {
                personal_history::show_snapshot_browser(&parent, &item.id, &item.title)
            }
        }
    }

    fn set_selection_mode(&self, active: bool) {
        if self.imp().selection_mode.replace(active) == active {
            return;
        }
        if !active {
            self.imp().selected.borrow_mut().clear();
        }
        if let Some(toggle) = self.imp().selection_toggle.borrow().as_ref()
            && toggle.is_active() != active
        {
            toggle.set_active(active);
        }
        if let Some(revealer) = self.imp().selection_revealer.borrow().as_ref() {
            revealer.set_reveal_child(active);
        }
        self.render();
    }

    fn update_selection(&self) {
        let count = self.imp().selected.borrow().len();
        if let Some(label) = self.imp().selection_label.borrow().as_ref() {
            label.set_label(&if count == 0 {
                tr("No snapshots selected")
            } else {
                trf("{0} snapshot(s) selected", &[&count.to_string()])
            });
        }
        if let Some(delete) = self.imp().delete_selected.borrow().as_ref() {
            delete.set_sensitive(count > 0);
        }
    }

    fn show_create_dialog(&self) {
        let Some(parent) = self.parent() else {
            return;
        };
        let scope = self.imp().scope.get();
        let dialog = adw::MessageDialog::new(
            Some(&parent),
            Some(&trf("Create {0}", &[&scope.noun()])),
            Some(&tr("The snapshot is created immediately.")),
        );
        let list = gtk::ListBox::new();
        list.add_css_class("boxed-list");
        let name = adw::EntryRow::new();
        name.set_title(&tr("Name (optional)"));
        let keep = adw::SwitchRow::new();
        keep.set_title(&tr("Keep Forever"));
        keep.set_subtitle(&tr("Otherwise Smart Cleanup may remove it later."));
        list.append(&name);
        list.append(&keep);
        dialog.set_extra_child(Some(&list));
        dialog.add_response("cancel", &tr("Cancel"));
        dialog.add_response("create", &tr("Create"));
        dialog.set_response_appearance("create", adw::ResponseAppearance::Suggested);
        dialog.set_default_response(Some("create"));
        let weak = self.downgrade();
        dialog.connect_response(None, move |_, response| {
            if response != "create" {
                return;
            }
            let Some(page) = weak.upgrade() else {
                return;
            };
            let title = if name.text().trim().is_empty() {
                trf(
                    "{0} · Manual Snapshot",
                    &[&chrono::Local::now().format("%Y-%m-%d %H:%M").to_string()],
                )
            } else {
                name.text().trim().to_string()
            };
            let pinned = keep.is_active();
            page.run_mutation(&tr("Creating snapshot…"), move || {
                let client = SnapshotsManagerHelperClient::new()?;
                match scope {
                    SnapshotScope::System => {
                        let result = client.create_deployment(title, "Manual".into(), pinned)?;
                        if !result.0 {
                            anyhow::bail!(result.1);
                        }
                    }
                    SnapshotScope::Home => {
                        client.create_personal_snapshot(title, "Manual".into(), pinned)?;
                    }
                }
                Ok(())
            });
        });
        dialog.present();
    }

    fn confirm_selected_delete(&self) {
        let ids = self.imp().selected.borrow().iter().cloned().collect();
        self.confirm_delete(ids);
    }

    fn confirm_delete(&self, ids: Vec<String>) {
        if ids.is_empty() {
            return;
        }
        let Some(parent) = self.parent() else {
            return;
        };
        let dialog = adw::MessageDialog::new(
            Some(&parent),
            Some(&tr("Delete Snapshots?")),
            Some(&trf(
                "Delete {0} selected snapshot(s)? This cannot be undone.",
                &[&ids.len().to_string()],
            )),
        );
        dialog.add_response("cancel", &tr("Cancel"));
        dialog.add_response("delete", &tr("Delete"));
        dialog.set_response_appearance("delete", adw::ResponseAppearance::Destructive);
        let weak = self.downgrade();
        dialog.connect_response(None, move |_, response| {
            if response != "delete" {
                return;
            }
            let Some(page) = weak.upgrade() else {
                return;
            };
            let ids = ids.clone();
            let scope = page.imp().scope.get();
            page.run_mutation(&tr("Deleting snapshots…"), move || {
                let client = SnapshotsManagerHelperClient::new()?;
                match scope {
                    SnapshotScope::System => client.delete_deployments(ids),
                    SnapshotScope::Home => client.delete_personal_snapshots(ids),
                }
            });
        });
        dialog.present();
    }

    fn verify_snapshot(&self, id: &str) {
        let id = id.to_string();
        let scope = self.imp().scope.get();
        let Some(parent) = self.parent() else {
            return;
        };
        run_operation(
            &parent,
            &tr("Checking snapshot…"),
            move || {
                let client = SnapshotsManagerHelperClient::new()?;
                match scope {
                    SnapshotScope::System => client.verify_snapshot(id),
                    SnapshotScope::Home => client.verify_personal_snapshot(id),
                }
            },
            move |parent, result| match result {
                Ok(result) => show_verification_result(parent, result),
                Err(problem) => show_error(parent, &problem.to_string()),
            },
        );
    }

    fn show_rename_dialog(&self, item: &SnapshotItem) {
        let Some(parent) = self.parent() else {
            return;
        };
        let dialog = adw::MessageDialog::new(
            Some(&parent),
            Some(&tr("Rename Snapshot")),
            Some(&tr("Only the display name changes.")),
        );
        let list = gtk::ListBox::new();
        list.add_css_class("boxed-list");
        let entry = adw::EntryRow::new();
        entry.set_title(&tr("Name"));
        entry.set_text(&item.title);
        list.append(&entry);
        dialog.set_extra_child(Some(&list));
        dialog.add_response("cancel", &tr("Cancel"));
        dialog.add_response("rename", &tr("Rename"));
        dialog.set_response_appearance("rename", adw::ResponseAppearance::Suggested);
        dialog.set_default_response(Some("rename"));
        let weak = self.downgrade();
        let id = item.id.clone();
        dialog.connect_response(None, move |_, response| {
            let title = entry.text().trim().to_string();
            if response != "rename" || title.is_empty() {
                return;
            }
            let Some(page) = weak.upgrade() else {
                return;
            };
            let id = id.clone();
            let scope = page.imp().scope.get();
            page.run_mutation(&tr("Renaming snapshot…"), move || {
                let client = SnapshotsManagerHelperClient::new()?;
                match scope {
                    SnapshotScope::System => client.rename_deployment(id, title),
                    SnapshotScope::Home => client.rename_personal_snapshot(id, title),
                }
            });
        });
        dialog.present();
    }

    fn set_pinned(&self, item: &SnapshotItem, pinned: bool) {
        let id = item.id.clone();
        let scope = self.imp().scope.get();
        self.run_mutation(&tr("Updating snapshot protection…"), move || {
            let client = SnapshotsManagerHelperClient::new()?;
            match scope {
                SnapshotScope::System => {
                    let result = client.set_deployment_pinned(id, pinned)?;
                    if !result.0 {
                        anyhow::bail!(result.1);
                    }
                }
                SnapshotScope::Home => {
                    client.set_personal_snapshot_pinned(id, pinned)?;
                }
            }
            Ok(())
        });
    }

    fn verify_then_confirm_rollback(&self, item: SnapshotItem) {
        let Some(parent) = self.parent() else {
            return;
        };
        let id = item.id.clone();
        let weak = self.downgrade();
        run_operation(
            &parent,
            &tr("Checking rollback safety…"),
            move || SnapshotsManagerHelperClient::new()?.verify_snapshot(id),
            move |parent, result| match result {
                Ok(result) if result.is_valid => {
                    if let Some(page) = weak.upgrade() {
                        page.confirm_rollback_impact(&item);
                    }
                }
                Ok(result) => show_verification_result(parent, result),
                Err(problem) => show_error(parent, &problem.to_string()),
            },
        );
    }

    fn confirm_rollback_impact(&self, item: &SnapshotItem) {
        let Some(parent) = self.parent() else {
            return;
        };
        let dialog = adw::MessageDialog::new(
            Some(&parent),
            Some(&trf("Roll Back to {0}?", &[&item.title])),
            Some(&tr(
                "Review what will happen before preparing the rollback.",
            )),
        );
        let list = gtk::ListBox::new();
        list.add_css_class("boxed-list");
        list.append(&impact_row(
            &tr("System files and packages"),
            &tr("Return to the selected snapshot"),
            "drive-harddisk-symbolic",
        ));
        list.append(&impact_row(
            &tr("Kernel"),
            item.kernel
                .as_deref()
                .unwrap_or(&tr("Recorded snapshot kernel")),
            "computer-symbolic",
        ));
        list.append(&impact_row(
            &tr("Personal files"),
            &tr("Will not change"),
            "folder-documents-symbolic",
        ));
        list.append(&impact_row(
            &tr("Current system"),
            &tr("Preserved permanently as a fallback"),
            "view-pin-symbolic",
        ));
        list.append(&impact_row(
            &tr("Restart"),
            &tr("Required to complete the rollback"),
            "system-reboot-symbolic",
        ));
        dialog.set_extra_child(Some(&list));
        dialog.add_response("cancel", &tr("Cancel"));
        dialog.add_response("rollback", &tr("Prepare Rollback"));
        dialog.set_response_appearance("rollback", adw::ResponseAppearance::Destructive);
        let weak = self.downgrade();
        let id = item.id.clone();
        dialog.connect_response(None, move |_, response| {
            if response == "rollback"
                && let Some(page) = weak.upgrade()
            {
                page.schedule_rollback(id.clone());
            }
        });
        dialog.present();
    }

    fn schedule_rollback(&self, id: String) {
        let Some(parent) = self.parent() else {
            return;
        };
        let weak = self.downgrade();
        run_operation(
            &parent,
            &tr("Preparing safe rollback…"),
            move || {
                let result =
                    SnapshotsManagerHelperClient::new()?.schedule_deployment_restore(id)?;
                if !result.0 {
                    anyhow::bail!(result.1);
                }
                Ok(())
            },
            move |parent, result| match result {
                Ok(()) => {
                    if let Some(page) = weak.upgrade() {
                        page.refresh();
                    }
                    show_rollback_ready(parent);
                }
                Err(problem) => show_error(parent, &problem.to_string()),
            },
        );
    }

    fn cancel_pending_rollback(&self) {
        let Some(parent) = self.parent() else {
            return;
        };
        let weak = self.downgrade();
        run_operation(
            &parent,
            &tr("Cancelling rollback…"),
            move || {
                let result = SnapshotsManagerHelperClient::new()?.cancel_deployment_restore()?;
                if !result.0 {
                    anyhow::bail!(result.1);
                }
                Ok(())
            },
            move |parent, result| match result {
                Ok(()) => {
                    if let Some(page) = weak.upgrade() {
                        page.refresh();
                    }
                }
                Err(problem) => show_error(parent, &problem.to_string()),
            },
        );
    }

    fn run_mutation<F>(&self, title: &str, operation: F)
    where
        F: FnOnce() -> anyhow::Result<()> + Send + 'static,
    {
        let Some(parent) = self.parent() else {
            return;
        };
        let weak = self.downgrade();
        run_operation(
            &parent,
            title,
            operation,
            move |parent, result| match result {
                Ok(()) => {
                    if let Some(page) = weak.upgrade() {
                        page.refresh();
                    }
                }
                Err(problem) => show_error(parent, &problem.to_string()),
            },
        );
    }
}

fn status_page(icon: &str, title: &str, description: Option<&str>) -> adw::StatusPage {
    let page = adw::StatusPage::new();
    page.set_icon_name(Some(icon));
    page.set_title(title);
    page.set_description(description);
    page
}

fn add_row_action<F>(group: &gio::SimpleActionGroup, name: &str, enabled: bool, callback: F)
where
    F: Fn() + 'static,
{
    let action = gio::SimpleAction::new(name, None);
    action.set_enabled(enabled);
    action.connect_activate(move |_, _| callback());
    group.add_action(&action);
}

fn snapshot_details(scope: SnapshotScope, item: &SnapshotItem) -> String {
    let time = item
        .created_at
        .with_timezone(&chrono::Local)
        .format("%Y-%m-%d %H:%M")
        .to_string();
    let reason = localized_reason(item);
    let state = snapshot_state(item);
    let mut parts = vec![time, reason];
    if scope == SnapshotScope::System
        && let Some(kernel) = &item.kernel
    {
        parts.push(trf("Kernel {0}", &[kernel]));
    }
    if let Some(summary) = &item.summary {
        parts.push(summary.clone());
    }
    if item.state != "ready" {
        parts.push(state);
    }
    parts.join(" · ")
}

fn localized_reason(item: &SnapshotItem) -> String {
    match item.kind.as_str() {
        "automatic" => tr("Automatic"),
        "apt-pre" => tr("Before Package Change"),
        "apt-post" => tr("After Package Change"),
        "pre-rollback" => tr("Before Rollback"),
        "factory" => tr("Factory"),
        "manual" => tr("Manual"),
        "imported" => tr("Imported"),
        _ if !item.reason.trim().is_empty() => item.reason.clone(),
        _ => tr("Snapshot"),
    }
}

fn snapshot_state(item: &SnapshotItem) -> String {
    if item.keep_forever
        || matches!(
            item.state.as_str(),
            "current" | "pending-rollback" | "booted-unconfirmed" | "fallback-protected"
        )
    {
        return tr("Permanently protected");
    }
    match item.state.as_str() {
        "ready" => tr("Ready"),
        "creating" => tr("Creating"),
        "current" => tr("Current system"),
        "pending-rollback" => tr("Pending rollback"),
        "booted-unconfirmed" => tr("Awaiting boot confirmation"),
        "fallback-protected" => tr("Protected fallback"),
        "incomplete" => tr("Incomplete"),
        "failed-reverted" => tr("Rollback reverted"),
        "broken" => tr("Damaged"),
        "deleting" => tr("Deleting"),
        _ => tr("Unknown state"),
    }
}

fn snapshot_icon(item: &SnapshotItem) -> &'static str {
    if item.keep_forever
        || matches!(
            item.state.as_str(),
            "current" | "pending-rollback" | "booted-unconfirmed" | "fallback-protected"
        )
    {
        "view-pin-symbolic"
    } else {
        match item.state.as_str() {
            "ready" => "emblem-ok-symbolic",
            "creating" | "deleting" => "content-loading-symbolic",
            "broken" => "dialog-error-symbolic",
            "incomplete" | "failed-reverted" => "dialog-warning-symbolic",
            _ => "document-open-recent-symbolic",
        }
    }
}

fn impact_row(title: &str, subtitle: &str, icon: &str) -> adw::ActionRow {
    let row = adw::ActionRow::new();
    row.set_title(title);
    row.set_subtitle(subtitle);
    row.add_prefix(&gtk::Image::from_icon_name(icon));
    row
}

fn run_operation<F, T, C>(parent: &adw::ApplicationWindow, title: &str, operation: F, complete: C)
where
    F: FnOnce() -> anyhow::Result<T> + Send + 'static,
    T: Send + 'static,
    C: FnOnce(&adw::ApplicationWindow, anyhow::Result<T>) + 'static,
{
    let progress = adw::Window::builder()
        .transient_for(parent)
        .modal(true)
        .deletable(false)
        .resizable(false)
        .default_width(360)
        .default_height(140)
        .title(tr("Disk Snapshots Manager"))
        .build();
    let content = gtk::Box::new(gtk::Orientation::Vertical, 16);
    content.set_margin_top(24);
    content.set_margin_bottom(24);
    content.set_margin_start(28);
    content.set_margin_end(28);
    let spinner = gtk::Spinner::new();
    spinner.set_spinning(true);
    spinner.set_halign(gtk::Align::Center);
    let label = gtk::Label::new(Some(title));
    label.add_css_class("heading");
    label.set_wrap(true);
    content.append(&spinner);
    content.append(&label);
    progress.set_content(Some(&content));
    progress.present();

    let weak_parent = parent.downgrade();
    let weak_progress = progress.downgrade();
    glib::spawn_future_local(async move {
        let result = gio::spawn_blocking(operation)
            .await
            .map_err(|_| anyhow::anyhow!("The background operation stopped unexpectedly"))
            .and_then(|result| result);
        if let Some(progress) = weak_progress.upgrade() {
            progress.close();
        }
        if let Some(parent) = weak_parent.upgrade() {
            complete(&parent, result);
        }
    });
}

fn show_verification_result(parent: &adw::ApplicationWindow, result: VerificationResult) {
    let mut details = if result.is_valid {
        tr("This snapshot is available for recovery.")
    } else if result.errors.is_empty() {
        tr("This snapshot is not available for recovery.")
    } else {
        result.errors.join("\n")
    };
    if !result.warnings.is_empty() {
        details.push_str("\n\n");
        details.push_str(&tr("Warnings"));
        details.push_str(":\n");
        details.push_str(&result.warnings.join("\n"));
    }
    show_information(parent, &tr("Snapshot Check Complete"), &details);
}

fn show_rollback_ready(parent: &adw::ApplicationWindow) {
    let dialog = adw::MessageDialog::new(
        Some(parent),
        Some(&tr("Rollback Is Ready")),
        Some(&tr(
            "The current system was preserved permanently. Restart now to roll back, or cancel from the System Recovery banner.",
        )),
    );
    dialog.add_response("later", &tr("Restart Later"));
    dialog.add_response("restart", &tr("Restart Now"));
    dialog.set_response_appearance("restart", adw::ResponseAppearance::Suggested);
    let weak = parent.downgrade();
    dialog.connect_response(None, move |_, response| {
        if response == "restart"
            && let Err(problem) = std::process::Command::new("/usr/bin/systemctl")
                .arg("reboot")
                .spawn()
            && let Some(parent) = weak.upgrade()
        {
            show_error(&parent, &problem.to_string());
        }
    });
    dialog.present();
}

fn show_error(parent: &adw::ApplicationWindow, message: &str) {
    show_information(parent, &tr("Operation Failed"), message);
}

fn show_information(parent: &adw::ApplicationWindow, title: &str, message: &str) {
    let dialog = adw::MessageDialog::new(Some(parent), Some(title), Some(message));
    dialog.add_response("close", &tr("Close"));
    dialog.present();
}
