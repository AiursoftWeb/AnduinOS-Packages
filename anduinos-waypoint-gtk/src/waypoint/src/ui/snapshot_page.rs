use std::cell::RefCell;
use std::collections::HashSet;
use std::rc::Rc;
use std::sync::mpsc;
use std::time::Duration;

use adw::prelude::*;
use gtk::prelude::*;
use libadwaita as adw;

use crate::dbus_client::{PersonalSnapshot, RecoveryDeployment, WaypointHelperClient};
use crate::i18n::{tr, trf};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SnapshotScope {
    System,
    Home,
}

impl SnapshotScope {
    fn noun(self) -> String {
        match self {
            Self::System => tr("system snapshot"),
            Self::Home => tr("Home snapshot"),
        }
    }
}

#[derive(Debug, Clone)]
struct SnapshotItem {
    id: String,
    title: String,
    created_at: chrono::DateTime<chrono::Utc>,
    reason: String,
    kind: String,
    state: String,
    keep_forever: bool,
    kernel: Option<String>,
    summary: Option<String>,
}

pub struct SnapshotPage {
    root: gtk::Widget,
    refresh: Rc<dyn Fn()>,
}

impl SnapshotPage {
    pub fn new(parent: &adw::ApplicationWindow, scope: SnapshotScope) -> Self {
        let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let controls = gtk::Box::new(gtk::Orientation::Horizontal, 10);
        controls.set_margin_top(18);
        controls.set_margin_bottom(12);
        controls.set_margin_start(18);
        controls.set_margin_end(18);

        let create = gtk::Button::with_label(&tr("Create Snapshot Now"));
        create.add_css_class("suggested-action");
        create.set_tooltip_text(Some(&trf("Create a {0} now", &[&scope.noun()])));
        let automate = gtk::Button::with_label(&tr("Automatic Snapshots"));
        let search = gtk::SearchEntry::new();
        search.set_placeholder_text(Some(&tr("Search snapshots")));
        search.set_hexpand(true);
        search.set_halign(gtk::Align::End);
        search.set_width_request(260);
        controls.append(&create);
        controls.append(&automate);
        controls.append(&search);
        root.append(&controls);

        let selected_bar = gtk::Box::new(gtk::Orientation::Horizontal, 12);
        selected_bar.add_css_class("toolbar");
        selected_bar.set_margin_start(18);
        selected_bar.set_margin_end(18);
        selected_bar.set_margin_bottom(8);
        let selected_label = gtk::Label::new(Some(&tr("No snapshots selected")));
        selected_label.set_hexpand(true);
        selected_label.set_halign(gtk::Align::Start);
        let delete_selected = gtk::Button::with_label(&tr("Delete Selected"));
        delete_selected.add_css_class("destructive-action");
        delete_selected.set_sensitive(false);
        selected_bar.append(&selected_label);
        selected_bar.append(&delete_selected);
        root.append(&selected_bar);

        let stack = gtk::Stack::new();
        stack.set_vexpand(true);
        let loading = adw::StatusPage::new();
        loading.set_icon_name(Some("content-loading-symbolic"));
        loading.set_title(&tr("Loading snapshots…"));
        stack.add_named(&loading, Some("loading"));
        let empty = adw::StatusPage::new();
        empty.set_icon_name(Some("document-open-recent-symbolic"));
        empty.set_title(&tr("No snapshots yet"));
        empty.set_description(Some(&tr("Create one now or turn on automatic snapshots.")));
        stack.add_named(&empty, Some("empty"));
        let error = adw::StatusPage::new();
        error.set_icon_name(Some("dialog-error-symbolic"));
        error.set_title(&tr("Snapshots are unavailable"));
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
        root.append(&stack);

        let items = Rc::new(RefCell::new(Vec::<SnapshotItem>::new()));
        let selected = Rc::new(RefCell::new(HashSet::<String>::new()));
        let query = Rc::new(RefCell::new(String::new()));
        let refresh_slot = Rc::new(RefCell::new(None::<Rc<dyn Fn()>>));

        let render: Rc<dyn Fn()> = {
            let parent = parent.clone();
            let list = list.clone();
            let stack = stack.clone();
            let items = items.clone();
            let selected = selected.clone();
            let query = query.clone();
            let selected_label = selected_label.clone();
            let delete_selected = delete_selected.clone();
            let refresh_slot = refresh_slot.clone();
            Rc::new(move || {
                while let Some(child) = list.first_child() {
                    list.remove(&child);
                }
                let needle = query.borrow().to_lowercase();
                let visible = items
                    .borrow()
                    .iter()
                    .filter(|item| {
                        needle.is_empty()
                            || item.title.to_lowercase().contains(&needle)
                            || item.reason.to_lowercase().contains(&needle)
                            || item.kind.to_lowercase().contains(&needle)
                    })
                    .cloned()
                    .collect::<Vec<_>>();
                if visible.is_empty() {
                    stack.set_visible_child_name("empty");
                } else {
                    stack.set_visible_child_name("content");
                }
                for item in visible {
                    list.append(&snapshot_row(
                        &parent,
                        scope,
                        &item,
                        &selected,
                        &selected_label,
                        &delete_selected,
                        &refresh_slot,
                    ));
                }
                update_selection(&selected, &selected_label, &delete_selected);
            })
        };

        let refresh: Rc<dyn Fn()> = {
            let stack = stack.clone();
            let error_page = error.clone();
            let items = items.clone();
            let selected = selected.clone();
            let render = render.clone();
            Rc::new(move || {
                stack.set_visible_child_name("loading");
                let (sender, receiver) = mpsc::channel();
                std::thread::spawn(move || {
                    let result = WaypointHelperClient::new()
                        .and_then(|client| client.recovery_engine_status())
                        .map(|status| match scope {
                            SnapshotScope::System => status
                                .deployments
                                .into_iter()
                                .map(|value| {
                                    let count =
                                        status.system_package_counts.get(&value.id).copied();
                                    let mut item = SnapshotItem::from(value);
                                    item.summary = count
                                        .map(|count| trf("{0} packages", &[&count.to_string()]));
                                    item
                                })
                                .collect::<Vec<_>>(),
                            SnapshotScope::Home => status
                                .personal_snapshots
                                .into_iter()
                                .map(|value| {
                                    let size = status
                                        .personal_sizes
                                        .get(&value.id)
                                        .and_then(|space| space.referenced_bytes)
                                        .map(waypoint_common::format_bytes);
                                    let mut item = SnapshotItem::from(value);
                                    item.summary = size;
                                    item
                                })
                                .collect::<Vec<_>>(),
                        });
                    let _ = sender.send(result);
                });
                let stack = stack.clone();
                let error_page = error_page.clone();
                let items = items.clone();
                let selected = selected.clone();
                let render = render.clone();
                glib::timeout_add_local(Duration::from_millis(80), move || {
                    match receiver.try_recv() {
                        Ok(Ok(mut loaded)) => {
                            loaded.sort_by_key(|item| std::cmp::Reverse(item.created_at));
                            *items.borrow_mut() = loaded;
                            selected.borrow_mut().clear();
                            render();
                            glib::ControlFlow::Break
                        }
                        Ok(Err(problem)) => {
                            error_page.set_description(Some(&problem.to_string()));
                            stack.set_visible_child_name("error");
                            glib::ControlFlow::Break
                        }
                        Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                        Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
                    }
                });
            })
        };
        *refresh_slot.borrow_mut() = Some(refresh.clone());

        let render_search = render.clone();
        let query_search = query.clone();
        search.connect_search_changed(move |entry| {
            *query_search.borrow_mut() = entry.text().to_string();
            render_search();
        });
        let create_parent = parent.clone();
        let create_refresh = refresh.clone();
        create.connect_clicked(move |_| show_create_dialog(&create_parent, scope, &create_refresh));
        let automate_parent = parent.clone();
        automate.connect_clicked(move |_| super::automation_dialog::show(&automate_parent, scope));
        let delete_parent = parent.clone();
        let delete_scope = scope;
        let delete_selected_ids = selected.clone();
        let delete_refresh = refresh.clone();
        delete_selected.connect_clicked(move |_| {
            confirm_bulk_delete(
                &delete_parent,
                delete_scope,
                delete_selected_ids.borrow().iter().cloned().collect(),
                &delete_refresh,
            );
        });

        Self {
            root: root.upcast(),
            refresh,
        }
    }

    pub fn widget(&self) -> &gtk::Widget {
        &self.root
    }

    pub fn refresh(&self) {
        (self.refresh)();
    }

    pub fn refresh_handle(&self) -> Rc<dyn Fn()> {
        self.refresh.clone()
    }
}

impl From<RecoveryDeployment> for SnapshotItem {
    fn from(value: RecoveryDeployment) -> Self {
        Self {
            id: value.id,
            title: value.title,
            created_at: value.created_at,
            reason: value.reason,
            kind: value.kind,
            state: value.state,
            keep_forever: value.pinned,
            kernel: value.kernel_release,
            summary: None,
        }
    }
}

impl From<PersonalSnapshot> for SnapshotItem {
    fn from(value: PersonalSnapshot) -> Self {
        Self {
            id: value.id,
            title: value.title,
            created_at: value.created_at,
            reason: value.reason,
            kind: value.kind,
            state: value.state,
            keep_forever: value.pinned,
            kernel: None,
            summary: None,
        }
    }
}

fn snapshot_row(
    parent: &adw::ApplicationWindow,
    scope: SnapshotScope,
    item: &SnapshotItem,
    selected: &Rc<RefCell<HashSet<String>>>,
    selected_label: &gtk::Label,
    delete_selected: &gtk::Button,
    refresh_slot: &Rc<RefCell<Option<Rc<dyn Fn()>>>>,
) -> adw::ActionRow {
    let row = adw::ActionRow::new();
    row.set_title(&item.title);
    let time = item
        .created_at
        .with_timezone(&chrono::Local)
        .format("%Y-%m-%d %H:%M")
        .to_string();
    let details = match (scope, item.kernel.as_deref(), item.summary.as_deref()) {
        (SnapshotScope::System, Some(kernel), Some(summary)) => trf(
            "{0} · {1} · Kernel {2} · {3}",
            &[&time, &localized_reason(item), kernel, summary],
        ),
        (SnapshotScope::System, Some(kernel), None) => trf(
            "{0} · {1} · Kernel {2}",
            &[&time, &localized_reason(item), kernel],
        ),
        (_, _, Some(summary)) => trf(
            "{0} · {1} · {2}",
            &[&time, &localized_reason(item), summary],
        ),
        _ => trf("{0} · {1}", &[&time, &localized_reason(item)]),
    };
    row.set_subtitle(&details);

    let check = gtk::CheckButton::new();
    check.set_valign(gtk::Align::Center);
    check.set_sensitive(item.state == "ready" && !item.keep_forever);
    row.add_prefix(&check);
    if item.keep_forever {
        let pin = gtk::Image::from_icon_name("view-pin-symbolic");
        pin.set_tooltip_text(Some(&tr("Keep Forever")));
        row.add_prefix(&pin);
    }

    let main_action = gtk::Button::with_label(&match scope {
        SnapshotScope::System => tr("Roll Back"),
        SnapshotScope::Home => tr("Browse Files"),
    });
    main_action.set_valign(gtk::Align::Center);
    main_action.set_sensitive(item.state == "ready");
    if scope == SnapshotScope::System {
        main_action.add_css_class("suggested-action");
    }
    row.add_suffix(&main_action);

    let menu = gtk::MenuButton::builder()
        .icon_name("view-more-symbolic")
        .valign(gtk::Align::Center)
        .build();
    let popover = gtk::Popover::new();
    let actions = gtk::Box::new(gtk::Orientation::Vertical, 4);
    actions.set_margin_top(6);
    actions.set_margin_bottom(6);
    actions.set_margin_start(6);
    actions.set_margin_end(6);
    let rename = menu_action(&tr("Rename"), "document-edit-symbolic");
    let browse = menu_action(&tr("Browse Files"), "folder-open-symbolic");
    let verify = menu_action(&tr("Check Snapshot Availability"), "emblem-ok-symbolic");
    let keep = menu_action(
        &if item.keep_forever {
            tr("Allow Smart Cleanup")
        } else {
            tr("Keep Forever")
        },
        "view-pin-symbolic",
    );
    let delete = menu_action(&tr("Delete Snapshot"), "user-trash-symbolic");
    delete.add_css_class("destructive-action");
    delete.set_sensitive(!item.keep_forever && item.state == "ready");
    actions.append(&rename);
    actions.append(&browse);
    actions.append(&verify);
    actions.append(&keep);
    actions.append(&delete);
    popover.set_child(Some(&actions));
    menu.set_popover(Some(&popover));
    row.add_suffix(&menu);

    let id_select = item.id.clone();
    let selected = selected.clone();
    let label = selected_label.clone();
    let delete_button = delete_selected.clone();
    check.connect_toggled(move |check| {
        if check.is_active() {
            selected.borrow_mut().insert(id_select.clone());
        } else {
            selected.borrow_mut().remove(&id_select);
        }
        update_selection(&selected, &label, &delete_button);
    });

    let parent_main = parent.clone();
    let id_main = item.id.clone();
    let title_main = item.title.clone();
    main_action.connect_clicked(move |_| match scope {
        SnapshotScope::System => confirm_rollback(&parent_main, &id_main, &title_main),
        SnapshotScope::Home => {
            super::personal_history::show_snapshot_browser(&parent_main, &id_main, &title_main)
        }
    });
    let parent_browse = parent.clone();
    let id_browse = item.id.clone();
    let title_browse = item.title.clone();
    browse.connect_clicked(move |_| match scope {
        SnapshotScope::Home => super::personal_history::show_snapshot_browser(
            &parent_browse,
            &id_browse,
            &title_browse,
        ),
        SnapshotScope::System => super::personal_history::show_system_snapshot_browser(
            &parent_browse,
            &id_browse,
            &title_browse,
        ),
    });
    let parent_rename = parent.clone();
    let id_rename = item.id.clone();
    let title_rename = item.title.clone();
    let refresh_rename = refresh_slot.clone();
    rename.connect_clicked(move |_| {
        if let Some(refresh) = refresh_rename.borrow().clone() {
            show_rename_dialog(&parent_rename, scope, &id_rename, &title_rename, &refresh);
        }
    });
    let parent_verify = parent.clone();
    let id_verify = item.id.clone();
    verify.connect_clicked(move |_| verify_snapshot(&parent_verify, scope, &id_verify));
    let parent_keep = parent.clone();
    let id_keep = item.id.clone();
    let desired = !item.keep_forever;
    let refresh_keep = refresh_slot.clone();
    keep.connect_clicked(move |_| {
        let refresh = refresh_keep.borrow().clone();
        let id = id_keep.clone();
        mutate(
            &parent_keep,
            move || {
                let client = WaypointHelperClient::new()?;
                match scope {
                    SnapshotScope::System => {
                        let result = client.set_deployment_pinned(id.clone(), desired)?;
                        if !result.0 {
                            anyhow::bail!(result.1);
                        }
                    }
                    SnapshotScope::Home => {
                        client.set_personal_snapshot_pinned(id.clone(), desired)?;
                    }
                }
                Ok(())
            },
            refresh,
        );
    });
    let parent_delete = parent.clone();
    let id_delete = item.id.clone();
    let refresh_delete = refresh_slot.clone();
    delete.connect_clicked(move |_| {
        if let Some(refresh) = refresh_delete.borrow().clone() {
            confirm_bulk_delete(&parent_delete, scope, vec![id_delete.clone()], &refresh);
        }
    });
    row
}

fn menu_action(label: &str, icon: &str) -> gtk::Button {
    let button = gtk::Button::builder()
        .label(label)
        .icon_name(icon)
        .halign(gtk::Align::Fill)
        .build();
    button.add_css_class("flat");
    button
}

fn localized_reason(item: &SnapshotItem) -> String {
    match item.kind.as_str() {
        "automatic" => tr("Automatic"),
        "package" => tr("Package Change"),
        "rollback-safety" => tr("Before Rollback"),
        "manual" => tr("Manual"),
        _ if !item.reason.trim().is_empty() => item.reason.clone(),
        _ => tr("Snapshot"),
    }
}

fn update_selection(
    selected: &Rc<RefCell<HashSet<String>>>,
    label: &gtk::Label,
    delete: &gtk::Button,
) {
    let count = selected.borrow().len();
    label.set_label(&if count == 0 {
        tr("No snapshots selected")
    } else {
        trf("{0} snapshot(s) selected", &[&count.to_string()])
    });
    delete.set_sensitive(count > 0);
}

fn show_create_dialog(
    parent: &adw::ApplicationWindow,
    scope: SnapshotScope,
    refresh: &Rc<dyn Fn()>,
) {
    let dialog = adw::MessageDialog::new(
        Some(parent),
        Some(&trf("Create {0}", &[&scope.noun()])),
        Some(&tr("The snapshot is created immediately.")),
    );
    let content = gtk::Box::new(gtk::Orientation::Vertical, 10);
    let name = adw::EntryRow::new();
    name.set_title(&tr("Name (optional)"));
    let keep = adw::SwitchRow::new();
    keep.set_title(&tr("Keep Forever"));
    keep.set_subtitle(&tr("Otherwise Smart Cleanup may remove it later."));
    keep.set_active(false);
    content.append(&name);
    content.append(&keep);
    dialog.set_extra_child(Some(&content));
    dialog.add_response("cancel", &tr("Cancel"));
    dialog.add_response("create", &tr("Create"));
    dialog.set_response_appearance("create", adw::ResponseAppearance::Suggested);
    dialog.set_default_response(Some("create"));
    let parent = parent.clone();
    let refresh = refresh.clone();
    dialog.connect_response(None, move |_, response| {
        if response != "create" {
            return;
        }
        let title = if name.text().trim().is_empty() {
            trf(
                "{0} · Manual Snapshot",
                &[&chrono::Local::now().format("%Y-%m-%d %H:%M").to_string()],
            )
        } else {
            name.text().trim().to_string()
        };
        let pinned = keep.is_active();
        mutate(
            &parent,
            move || {
                let client = WaypointHelperClient::new()?;
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
            },
            Some(refresh.clone()),
        );
    });
    dialog.present();
}

fn confirm_bulk_delete(
    parent: &adw::ApplicationWindow,
    scope: SnapshotScope,
    ids: Vec<String>,
    refresh: &Rc<dyn Fn()>,
) {
    if ids.is_empty() {
        return;
    }
    let dialog = adw::MessageDialog::new(
        Some(parent),
        Some(&tr("Delete Snapshots?")),
        Some(&trf(
            "Delete {0} selected snapshot(s)? This cannot be undone.",
            &[&ids.len().to_string()],
        )),
    );
    dialog.add_response("cancel", &tr("Cancel"));
    dialog.add_response("delete", &tr("Delete"));
    dialog.set_response_appearance("delete", adw::ResponseAppearance::Destructive);
    let parent = parent.clone();
    let refresh = refresh.clone();
    dialog.connect_response(None, move |_, response| {
        if response != "delete" {
            return;
        }
        let ids = ids.clone();
        mutate(
            &parent,
            move || {
                let client = WaypointHelperClient::new()?;
                match scope {
                    SnapshotScope::System => client.delete_deployments(ids),
                    SnapshotScope::Home => client.delete_personal_snapshots(ids),
                }
            },
            Some(refresh.clone()),
        );
    });
    dialog.present();
}

fn confirm_rollback(parent: &adw::ApplicationWindow, id: &str, title: &str) {
    let dialog = adw::MessageDialog::new(
        Some(parent),
        Some(&trf("Roll Back to {0}?", &[title])),
        Some(&tr(
            "Waypoint will first preserve the current system. Personal files will not change. A restart is required.",
        )),
    );
    dialog.add_response("cancel", &tr("Cancel"));
    dialog.add_response("rollback", &tr("Prepare Rollback"));
    dialog.set_response_appearance("rollback", adw::ResponseAppearance::Destructive);
    let parent = parent.clone();
    let id = id.to_string();
    dialog.connect_response(None, move |_, response| {
        if response != "rollback" {
            return;
        }
        schedule_rollback(&parent, id.clone());
    });
    dialog.present();
}

fn schedule_rollback(parent: &adw::ApplicationWindow, id: String) {
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let result = WaypointHelperClient::new().and_then(|client| {
            let result = client.schedule_deployment_restore(id)?;
            if !result.0 {
                anyhow::bail!(result.1);
            }
            Ok(())
        });
        let _ = sender.send(result);
    });
    let parent = parent.clone();
    glib::timeout_add_local(Duration::from_millis(80), move || {
        match receiver.try_recv() {
            Ok(Ok(())) => {
                let dialog = adw::MessageDialog::new(
                    Some(&parent),
                    Some(&tr("Rollback Is Ready")),
                    Some(&tr(
                        "The current system was preserved permanently. Restart now to roll back, or cancel the pending rollback.",
                    )),
                );
                dialog.add_response("cancel-rollback", &tr("Cancel Rollback"));
                dialog.add_response("later", &tr("Restart Later"));
                dialog.add_response("restart", &tr("Restart Now"));
                dialog.set_response_appearance("restart", adw::ResponseAppearance::Suggested);
                let parent_response = parent.clone();
                dialog.connect_response(None, move |_, response| match response {
                    "cancel-rollback" => {
                        mutate(
                            &parent_response,
                            move || {
                                let result =
                                    WaypointHelperClient::new()?.cancel_deployment_restore()?;
                                if !result.0 {
                                    anyhow::bail!(result.1);
                                }
                                Ok(())
                            },
                            None,
                        );
                    }
                    "restart" => {
                        if let Err(error) = std::process::Command::new("/usr/bin/systemctl")
                            .arg("reboot")
                            .spawn()
                        {
                            show_error(&parent_response, &error.to_string());
                        }
                    }
                    _ => {}
                });
                dialog.present();
                glib::ControlFlow::Break
            }
            Ok(Err(error)) => {
                show_error(&parent, &error.to_string());
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
        }
    });
}

fn verify_snapshot(parent: &adw::ApplicationWindow, scope: SnapshotScope, id: &str) {
    let parent = parent.clone();
    let id = id.to_string();
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let result = WaypointHelperClient::new().and_then(|client| match scope {
            SnapshotScope::System => client.verify_snapshot(id),
            SnapshotScope::Home => client.verify_personal_snapshot(id),
        });
        let _ = sender.send(result);
    });
    glib::timeout_add_local(Duration::from_millis(80), move || {
        match receiver.try_recv() {
            Ok(Ok(result)) => {
                show_information(
                    &parent,
                    &tr("Snapshot Check Complete"),
                    &if result.is_valid {
                        tr("This snapshot is available for recovery.")
                    } else {
                        result.errors.join("\n")
                    },
                );
                glib::ControlFlow::Break
            }
            Ok(Err(problem)) => {
                show_error(&parent, &problem.to_string());
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
        }
    });
}

fn show_rename_dialog(
    parent: &adw::ApplicationWindow,
    scope: SnapshotScope,
    id: &str,
    current_title: &str,
    refresh: &Rc<dyn Fn()>,
) {
    let dialog = adw::MessageDialog::new(
        Some(parent),
        Some(&tr("Rename Snapshot")),
        Some(&tr("Only the display name changes.")),
    );
    let entry = adw::EntryRow::new();
    entry.set_title(&tr("Name"));
    entry.set_text(current_title);
    dialog.set_extra_child(Some(&entry));
    dialog.add_response("cancel", &tr("Cancel"));
    dialog.add_response("rename", &tr("Rename"));
    dialog.set_response_appearance("rename", adw::ResponseAppearance::Suggested);
    dialog.set_default_response(Some("rename"));
    let parent = parent.clone();
    let id = id.to_string();
    let refresh = refresh.clone();
    dialog.connect_response(None, move |_, response| {
        if response != "rename" || entry.text().trim().is_empty() {
            return;
        }
        let title = entry.text().trim().to_string();
        let id = id.clone();
        mutate(
            &parent,
            move || {
                let client = WaypointHelperClient::new()?;
                match scope {
                    SnapshotScope::System => client.rename_deployment(id, title),
                    SnapshotScope::Home => client.rename_personal_snapshot(id, title),
                }
            },
            Some(refresh.clone()),
        );
    });
    dialog.present();
}

fn mutate<F>(parent: &adw::ApplicationWindow, operation: F, refresh: Option<Rc<dyn Fn()>>)
where
    F: FnOnce() -> anyhow::Result<()> + Send + 'static,
{
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let _ = sender.send(operation());
    });
    let parent = parent.clone();
    glib::timeout_add_local(Duration::from_millis(80), move || {
        match receiver.try_recv() {
            Ok(Ok(())) => {
                if let Some(refresh) = &refresh {
                    refresh();
                }
                glib::ControlFlow::Break
            }
            Ok(Err(problem)) => {
                show_error(&parent, &problem.to_string());
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
        }
    });
}

fn show_error(parent: &adw::ApplicationWindow, message: &str) {
    show_information(parent, &tr("Operation Failed"), message);
}

fn show_information(parent: &adw::ApplicationWindow, title: &str, message: &str) {
    let dialog = adw::MessageDialog::new(Some(parent), Some(title), Some(message));
    dialog.add_response("close", &tr("Close"));
    dialog.present();
}
