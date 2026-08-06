use std::cell::RefCell;
use std::io::{Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc;
use std::time::Duration;

use adw::prelude::*;
use gtk::prelude::*;
use libadwaita as adw;

use crate::dbus_client::{PersonalDirectoryEntry, PersonalSnapshot, WaypointHelperClient};
use crate::file_history_request::{HistoryTarget, HistoryTargetKind};
use crate::i18n::{tr, trf};

pub fn show(parent: &adw::ApplicationWindow) {
    let window = adw::Window::new();
    window.set_title(Some(&tr("Saved Personal File Versions")));
    window.set_default_size(820, 680);
    window.set_modal(true);
    window.set_transient_for(Some(parent));
    let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
    let header = adw::HeaderBar::new();
    header.set_title_widget(Some(&adw::WindowTitle::new(
        &tr("Saved Personal File Versions"),
        &tr("Choose a saved time, then find the file or folder you need"),
    )));
    let create = gtk::Button::with_label(&tr("Save Now"));
    create.add_css_class("suggested-action");
    header.pack_start(&create);
    let refresh = gtk::Button::from_icon_name("view-refresh-symbolic");
    refresh.set_tooltip_text(Some(&tr("Refresh Personal Files history")));
    header.pack_end(&refresh);
    root.append(&header);

    let banner = adw::Banner::new(&tr(
        "Personal history is stored on the same disk. Keep an external backup for disk failure or theft.",
    ));
    banner.set_revealed(true);
    root.append(&banner);

    let stack = gtk::Stack::new();
    stack.set_vexpand(true);
    let loading = adw::StatusPage::new();
    loading.set_title(&tr("Loading Personal Files history…"));
    loading.set_icon_name(Some("folder-documents-symbolic"));
    stack.add_named(&loading, Some("loading"));

    let scrolled = gtk::ScrolledWindow::new();
    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(760);
    let list = gtk::ListBox::new();
    list.set_selection_mode(gtk::SelectionMode::None);
    list.add_css_class("boxed-list");
    list.set_margin_top(24);
    list.set_margin_bottom(24);
    list.set_margin_start(12);
    list.set_margin_end(12);
    clamp.set_child(Some(&list));
    scrolled.set_child(Some(&clamp));
    stack.add_named(&scrolled, Some("content"));
    root.append(&stack);
    window.set_content(Some(&root));

    load_snapshots(&window, &stack, &list);

    let window_refresh = window.clone();
    let stack_refresh = stack.clone();
    let list_refresh = list.clone();
    refresh
        .connect_clicked(move |_| load_snapshots(&window_refresh, &stack_refresh, &list_refresh));

    let window_create = window.clone();
    let stack_create = stack.clone();
    let list_create = list.clone();
    create.connect_clicked(move |button| {
        button.set_sensitive(false);
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let result = (|| -> anyhow::Result<()> {
                let client = WaypointHelperClient::new()?;
                let now = chrono::Local::now();
                client.create_personal_snapshot(
                    format!("Personal Files · {}", now.format("%Y-%m-%d %H:%M")),
                    "Manual Personal Files history point".into(),
                    false,
                )?;
                Ok(())
            })();
            let _ = sender.send(result);
        });
        let button = button.clone();
        let window = window_create.clone();
        let stack = stack_create.clone();
        let list = list_create.clone();
        glib::timeout_add_local(Duration::from_millis(80), move || {
            match receiver.try_recv() {
                Ok(Ok(())) => {
                    button.set_sensitive(true);
                    toast(&window, &tr("Personal Files history point created"));
                    load_snapshots(&window, &stack, &list);
                    glib::ControlFlow::Break
                }
                Ok(Err(error)) => {
                    button.set_sensitive(true);
                    error_dialog(
                        &window,
                        &tr("Could Not Create History Point"),
                        &error.to_string(),
                    );
                    glib::ControlFlow::Break
                }
                Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
            }
        });
    });

    window.present();
}

pub fn show_snapshot_browser(parent: &adw::ApplicationWindow, id: &str, title: &str) {
    show_browser(parent, BrowserScope::Home, id, title, "", None);
}

pub fn show_system_snapshot_browser(parent: &adw::ApplicationWindow, id: &str, title: &str) {
    match WaypointHelperClient::new()
        .and_then(|client| client.begin_system_snapshot_browse(id.to_string()))
    {
        Ok(token) => show_browser(
            parent,
            BrowserScope::System(Arc::new(SystemBrowserLease::new(token))),
            id,
            title,
            "",
            None,
        ),
        Err(error) => {
            let dialog = adw::MessageDialog::new(
                Some(parent),
                Some(&tr("Could Not Browse System Snapshot")),
                Some(&error.to_string()),
            );
            dialog.add_response("close", &tr("Close"));
            dialog.present();
        }
    }
}

#[derive(Clone)]
enum BrowserScope {
    Home,
    System(Arc<SystemBrowserLease>),
}

struct SystemBrowserLease {
    token: String,
    released: std::sync::atomic::AtomicBool,
}

impl SystemBrowserLease {
    fn new(token: String) -> Self {
        Self {
            token,
            released: std::sync::atomic::AtomicBool::new(false),
        }
    }

    fn release(&self) {
        if !self.released.swap(true, Ordering::AcqRel) {
            let _ = WaypointHelperClient::new()
                .and_then(|client| client.end_system_snapshot_browse(self.token.clone()));
        }
    }
}

impl Drop for SystemBrowserLease {
    fn drop(&mut self) {
        self.release();
    }
}

struct TargetVersion {
    snapshot: PersonalSnapshot,
    entry: Option<PersonalDirectoryEntry>,
}

/// Open the focused File History surface used by the Nautilus extension. It is
/// an application-owned top-level window, never a widget injected into
/// Nautilus, and all historical reads still go through WaypointHelperClient.
pub fn show_target(app: &adw::Application, target: HistoryTarget) {
    let window = adw::Window::new();
    window.set_application(Some(app));
    window.set_title(Some(&match target.kind {
        HistoryTargetKind::File => tr("File History"),
        HistoryTargetKind::Directory => tr("Folder History"),
    }));
    window.set_default_size(820, 680);

    let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
    let header = adw::HeaderBar::new();
    let display_path = display_relative_path(&target.relative_path);
    header.set_title_widget(Some(&adw::WindowTitle::new(
        &match target.kind {
            HistoryTargetKind::File => tr("File History"),
            HistoryTargetKind::Directory => tr("Folder History"),
        },
        &display_path,
    )));
    let refresh = gtk::Button::from_icon_name("view-refresh-symbolic");
    refresh.set_tooltip_text(Some(&tr("Refresh file history")));
    header.pack_end(&refresh);
    root.append(&header);

    let banner = adw::Banner::new(&tr(
        "Choose an earlier version to browse or recover. Your current files will not be changed automatically.",
    ));
    banner.set_revealed(true);
    root.append(&banner);

    let stack = gtk::Stack::new();
    stack.set_vexpand(true);
    let loading = adw::StatusPage::new();
    loading.set_title(&tr("Looking for earlier versions…"));
    loading.set_description(Some(&display_path));
    loading.set_icon_name(Some("document-open-recent-symbolic"));
    stack.add_named(&loading, Some("loading"));

    let scrolled = gtk::ScrolledWindow::new();
    let clamp = adw::Clamp::new();
    clamp.set_maximum_size(760);
    let list = gtk::ListBox::new();
    list.set_selection_mode(gtk::SelectionMode::None);
    list.add_css_class("boxed-list");
    list.set_margin_top(24);
    list.set_margin_bottom(24);
    list.set_margin_start(12);
    list.set_margin_end(12);
    clamp.set_child(Some(&list));
    scrolled.set_child(Some(&clamp));
    stack.add_named(&scrolled, Some("content"));
    root.append(&stack);
    window.set_content(Some(&root));

    load_target_versions(&window, &stack, &list, target.clone());
    let window_refresh = window.clone();
    let stack_refresh = stack.clone();
    let list_refresh = list.clone();
    refresh.connect_clicked(move |_| {
        load_target_versions(
            &window_refresh,
            &stack_refresh,
            &list_refresh,
            target.clone(),
        );
    });
    window.present();
}

fn load_target_versions(
    window: &adw::Window,
    stack: &gtk::Stack,
    list: &gtk::ListBox,
    target: HistoryTarget,
) {
    stack.set_visible_child_name("loading");
    clear_list(list);
    let (sender, receiver) = mpsc::channel();
    let target_for_query = target.clone();
    std::thread::spawn(move || {
        let result = (|| -> anyhow::Result<(Vec<TargetVersion>, usize)> {
            let client = WaypointHelperClient::new()?;
            let status = client.recovery_engine_status()?;
            let mut versions = Vec::new();
            for snapshot in status
                .personal_snapshots
                .into_iter()
                .filter(|snapshot| snapshot.state == "ready")
            {
                match target_for_query.kind {
                    HistoryTargetKind::Directory => {
                        match client.list_personal_files(
                            snapshot.id.clone(),
                            target_for_query.relative_path.clone(),
                        ) {
                            Ok(_) => versions.push(TargetVersion {
                                snapshot,
                                entry: None,
                            }),
                            Err(error) if history_query_failed(&error) => return Err(error),
                            Err(_) => {}
                        }
                    }
                    HistoryTargetKind::File => {
                        let (parent, name) = split_file_target(&target_for_query.relative_path);
                        let entry = match client
                            .list_personal_files(snapshot.id.clone(), parent.to_string())
                        {
                            Ok(entries) => entries
                                .into_iter()
                                .find(|entry| entry.name == name && entry.kind != "directory"),
                            Err(error) if history_query_failed(&error) => return Err(error),
                            Err(_) => None,
                        };
                        if let Some(entry) = entry {
                            versions.push(TargetVersion {
                                snapshot,
                                entry: Some(entry),
                            });
                        }
                    }
                }
            }
            versions
                .sort_by(|left, right| right.snapshot.created_at.cmp(&left.snapshot.created_at));
            Ok((versions, status.personal_issues.len()))
        })();
        let _ = sender.send(result);
    });

    let window = window.clone();
    let stack = stack.clone();
    let list = list.clone();
    glib::timeout_add_local(Duration::from_millis(80), move || {
        match receiver.try_recv() {
            Ok(Ok((versions, issue_count))) => {
                clear_list(&list);
                if versions.is_empty() {
                    let row = adw::ActionRow::new();
                    row.set_title(&tr("No earlier version was found"));
                    row.set_subtitle(&tr(
                        "This item was not present in the available Personal Files history points.",
                    ));
                    list.append(&row);
                } else {
                    for version in versions {
                        append_target_version_row(&window, &list, version, &target);
                    }
                }
                if issue_count > 0 {
                    let row = adw::ActionRow::new();
                    row.set_title(&tr("Some history points could not be loaded"));
                    row.set_subtitle(&trf(
                        "{0} damaged metadata entries were ignored",
                        &[&issue_count.to_string()],
                    ));
                    list.append(&row);
                }
                stack.set_visible_child_name("content");
                glib::ControlFlow::Break
            }
            Ok(Err(error)) => {
                error_dialog(
                    &window,
                    &tr("Personal History Unavailable"),
                    &error.to_string(),
                );
                stack.set_visible_child_name("content");
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
        }
    });
}

fn append_target_version_row(
    window: &adw::Window,
    list: &gtk::ListBox,
    version: TargetVersion,
    target: &HistoryTarget,
) {
    let row = adw::ActionRow::new();
    row.set_title(&version.snapshot.title);
    let created = version
        .snapshot
        .created_at
        .with_timezone(&chrono::Local)
        .format("%Y-%m-%d %H:%M")
        .to_string();
    if let Some(entry) = &version.entry {
        let modified = chrono::DateTime::from_timestamp(entry.modified_unix_seconds, 0)
            .map(|time| {
                time.with_timezone(&chrono::Local)
                    .format("%Y-%m-%d %H:%M")
                    .to_string()
            })
            .unwrap_or_else(|| tr("Unknown date"));
        row.set_subtitle(&trf(
            "History point {0} · {1} bytes · modified {2}",
            &[&created, &entry.size.to_string(), &modified],
        ));
    } else {
        row.set_subtitle(&trf("History point {0} · Folder", &[&created]));
    }

    let browse = gtk::Button::with_label(&tr("Browse"));
    browse.set_valign(gtk::Align::Center);
    row.add_suffix(&browse);
    if target.kind == HistoryTargetKind::File {
        let recover = gtk::Button::with_label(&tr("Recover…"));
        recover.set_valign(gtk::Align::Center);
        recover.add_css_class("suggested-action");
        row.add_suffix(&recover);
        let recovery_window = window.clone();
        let recovery_id = version.snapshot.id.clone();
        let recovery_relative = target.relative_path.clone();
        let (_, recovery_name) = split_file_target(&target.relative_path);
        let recovery_name = recovery_name.to_string();
        recover.connect_clicked(move |_| {
            choose_file_destination(
                &recovery_window,
                BrowserScope::Home,
                &recovery_id,
                &recovery_relative,
                &recovery_name,
            );
        });
    }
    list.append(&row);

    let browser_window = window.clone();
    let browser_id = version.snapshot.id;
    let browser_title = version.snapshot.title;
    let (initial_path, highlighted) = match target.kind {
        HistoryTargetKind::File => {
            let (parent, name) = split_file_target(&target.relative_path);
            (parent.to_string(), Some(name.to_string()))
        }
        HistoryTargetKind::Directory => (target.relative_path.clone(), None),
    };
    browse.connect_clicked(move |_| {
        show_browser(
            &browser_window,
            BrowserScope::Home,
            &browser_id,
            &browser_title,
            &initial_path,
            highlighted.clone(),
        );
    });
}

fn split_file_target(relative_path: &str) -> (&str, &str) {
    relative_path
        .rsplit_once('/')
        .map_or(("", relative_path), |(parent, name)| (parent, name))
}

fn history_query_failed(error: &anyhow::Error) -> bool {
    let message = error.to_string();
    message.contains("Authorization failed")
        || message.contains("Failed to browse historical Personal Files")
}

fn display_relative_path(relative_path: &str) -> String {
    if relative_path.is_empty() {
        "~/".to_string()
    } else {
        format!("~/{relative_path}")
    }
}

fn load_snapshots(window: &adw::Window, stack: &gtk::Stack, list: &gtk::ListBox) {
    stack.set_visible_child_name("loading");
    clear_list(list);
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let result = WaypointHelperClient::new().and_then(|client| {
            client
                .recovery_engine_status()
                .map(|status| (status.personal_snapshots, status.personal_issues))
        });
        let _ = sender.send(result);
    });
    let window = window.clone();
    let stack = stack.clone();
    let list = list.clone();
    glib::timeout_add_local(Duration::from_millis(80), move || {
        match receiver.try_recv() {
            Ok(Ok((snapshots, issues))) => {
                if snapshots.is_empty() {
                    let row = adw::ActionRow::new();
                    row.set_title(&tr("No saved personal file versions yet"));
                    row.set_subtitle(&tr("Save your files now or turn on automatic protection."));
                    list.append(&row);
                } else {
                    for snapshot in snapshots {
                        append_snapshot_row(&window, &stack, &list, snapshot);
                    }
                }
                if !issues.is_empty() {
                    let row = adw::ActionRow::new();
                    row.set_title(&tr("Some history points could not be loaded"));
                    row.set_subtitle(&trf(
                        "{0} damaged metadata entries were ignored",
                        &[&issues.len().to_string()],
                    ));
                    list.append(&row);
                }
                stack.set_visible_child_name("content");
                glib::ControlFlow::Break
            }
            Ok(Err(error)) => {
                error_dialog(
                    &window,
                    &tr("Personal History Unavailable"),
                    &error.to_string(),
                );
                stack.set_visible_child_name("content");
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
        }
    });
}

fn append_snapshot_row(
    window: &adw::Window,
    stack: &gtk::Stack,
    list: &gtk::ListBox,
    snapshot: PersonalSnapshot,
) {
    let row = adw::ActionRow::new();
    row.set_title(&snapshot.title);
    row.set_subtitle(&format!(
        "{} · {} · {}{}",
        snapshot
            .created_at
            .with_timezone(&chrono::Local)
            .format("%Y-%m-%d %H:%M"),
        snapshot.state,
        snapshot.reason,
        if snapshot.pinned { " · Protected" } else { "" }
    ));
    let browse = gtk::Button::with_label(&tr("Find Files"));
    browse.set_valign(gtk::Align::Center);
    browse.add_css_class("suggested-action");
    browse.set_sensitive(snapshot.state == "ready");
    row.add_suffix(&browse);
    let protect = gtk::Button::from_icon_name(if snapshot.pinned {
        "starred-symbolic"
    } else {
        "non-starred-symbolic"
    });
    let protect_tooltip = if snapshot.pinned {
        tr("Unprotect history point")
    } else {
        tr("Protect history point")
    };
    protect.set_tooltip_text(Some(&protect_tooltip));
    protect.set_valign(gtk::Align::Center);
    row.add_suffix(&protect);
    let delete = gtk::Button::from_icon_name("user-trash-symbolic");
    delete.add_css_class("destructive-action");
    delete.set_sensitive(!snapshot.pinned);
    delete.set_valign(gtk::Align::Center);
    row.add_suffix(&delete);
    list.append(&row);

    let parent = window.clone();
    let id = snapshot.id.clone();
    let title = snapshot.title.clone();
    browse
        .connect_clicked(move |_| show_browser(&parent, BrowserScope::Home, &id, &title, "", None));

    let window_pin = window.clone();
    let stack_pin = stack.clone();
    let list_pin = list.clone();
    let id_pin = snapshot.id.clone();
    let pinned = snapshot.pinned;
    protect.connect_clicked(move |button| {
        button.set_sensitive(false);
        let id = id_pin.clone();
        mutate_then_reload(&window_pin, &stack_pin, &list_pin, move |client| {
            client.set_personal_snapshot_pinned(id, !pinned).map(|_| ())
        });
    });

    let window_delete = window.clone();
    let stack_delete = stack.clone();
    let list_delete = list.clone();
    let id_delete = snapshot.id;
    delete.connect_clicked(move |button| {
        button.set_sensitive(false);
        let id = id_delete.clone();
        mutate_then_reload(&window_delete, &stack_delete, &list_delete, move |client| {
            client.delete_personal_snapshot(id)
        });
    });
}

fn mutate_then_reload<F>(
    window: &adw::Window,
    stack: &gtk::Stack,
    list: &gtk::ListBox,
    operation: F,
) where
    F: FnOnce(WaypointHelperClient) -> anyhow::Result<()> + Send + 'static,
{
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let result = WaypointHelperClient::new().and_then(operation);
        let _ = sender.send(result);
    });
    let window = window.clone();
    let stack = stack.clone();
    let list = list.clone();
    glib::timeout_add_local(Duration::from_millis(80), move || {
        match receiver.try_recv() {
            Ok(Ok(())) => {
                load_snapshots(&window, &stack, &list);
                glib::ControlFlow::Break
            }
            Ok(Err(error)) => {
                error_dialog(
                    &window,
                    &tr("Personal History Operation Failed"),
                    &error.to_string(),
                );
                load_snapshots(&window, &stack, &list);
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
        }
    });
}

fn show_browser(
    parent: &impl IsA<gtk::Window>,
    scope: BrowserScope,
    snapshot_id: &str,
    title: &str,
    initial_path: &str,
    highlighted_name: Option<String>,
) {
    let window = adw::Window::new();
    if let BrowserScope::System(lease) = &scope {
        let lease = lease.clone();
        window.connect_close_request(move |_| {
            lease.release();
            glib::Propagation::Proceed
        });
    }
    let browser_title = match scope {
        BrowserScope::Home => tr("Recover Personal Files"),
        BrowserScope::System(_) => tr("Browse System Snapshot"),
    };
    window.set_title(Some(&browser_title));
    window.set_default_size(780, 640);
    window.set_modal(true);
    window.set_transient_for(Some(parent));
    let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
    let header = adw::HeaderBar::new();
    header.set_title_widget(Some(&adw::WindowTitle::new(&browser_title, title)));
    let up = gtk::Button::from_icon_name("go-up-symbolic");
    up.set_tooltip_text(Some(&tr("Parent folder")));
    header.pack_start(&up);
    let recover_folder = gtk::Button::with_label(&tr("Recover This Folder…"));
    header.pack_end(&recover_folder);
    root.append(&header);
    let path_label = gtk::Label::new(None);
    path_label.set_text("~/");
    path_label.set_halign(gtk::Align::Start);
    path_label.set_margin_top(8);
    path_label.set_margin_start(16);
    path_label.set_margin_end(16);
    path_label.add_css_class("dim-label");
    root.append(&path_label);
    let scrolled = gtk::ScrolledWindow::new();
    scrolled.set_vexpand(true);
    let list = gtk::ListBox::new();
    list.set_selection_mode(gtk::SelectionMode::None);
    list.add_css_class("boxed-list");
    list.set_margin_top(12);
    list.set_margin_bottom(24);
    list.set_margin_start(16);
    list.set_margin_end(16);
    scrolled.set_child(Some(&list));
    root.append(&scrolled);
    window.set_content(Some(&root));

    let current_path = Rc::new(RefCell::new(initial_path.to_string()));
    load_directory(
        &window,
        scope.clone(),
        snapshot_id,
        &current_path,
        &path_label,
        &list,
        highlighted_name,
    );

    let window_up = window.clone();
    let id_up = snapshot_id.to_string();
    let path_up = current_path.clone();
    let label_up = path_label.clone();
    let list_up = list.clone();
    let scope_up = scope.clone();
    up.connect_clicked(move |_| {
        let next = path_up
            .borrow()
            .rsplit_once('/')
            .map(|(parent, _)| parent.to_string())
            .unwrap_or_default();
        *path_up.borrow_mut() = next;
        load_directory(
            &window_up,
            scope_up.clone(),
            &id_up,
            &path_up,
            &label_up,
            &list_up,
            None,
        );
    });

    let window_folder = window.clone();
    let id_folder = snapshot_id.to_string();
    let path_folder = current_path.clone();
    let scope_folder = scope.clone();
    recover_folder.connect_clicked(move |_| {
        choose_folder_destination(
            &window_folder,
            scope_folder.clone(),
            &id_folder,
            &path_folder.borrow(),
        );
    });
    window.present();
}

fn load_directory(
    window: &adw::Window,
    scope: BrowserScope,
    snapshot_id: &str,
    current_path: &Rc<RefCell<String>>,
    path_label: &gtk::Label,
    list: &gtk::ListBox,
    highlighted_name: Option<String>,
) {
    clear_list(list);
    let loading = adw::ActionRow::new();
    loading.set_title(&tr("Loading folder…"));
    list.append(&loading);
    let id = snapshot_id.to_string();
    let path = current_path.borrow().clone();
    path_label.set_text(&format!("~/{}", path));
    let (sender, receiver) = mpsc::channel();
    let scope_worker = scope.clone();
    std::thread::spawn(move || {
        let result = WaypointHelperClient::new()
            .and_then(|client| list_files(&client, &scope_worker, id, path));
        let _ = sender.send(result);
    });
    let window = window.clone();
    let id = snapshot_id.to_string();
    let current_path = current_path.clone();
    let path_label = path_label.clone();
    let list = list.clone();
    glib::timeout_add_local(Duration::from_millis(80), move || {
        match receiver.try_recv() {
            Ok(Ok(entries)) => {
                clear_list(&list);
                if entries.is_empty() {
                    let row = adw::ActionRow::new();
                    row.set_title(&tr("This folder is empty"));
                    list.append(&row);
                }
                for entry in entries {
                    append_file_row(
                        &window,
                        scope.clone(),
                        &id,
                        &current_path,
                        &path_label,
                        &list,
                        entry,
                        highlighted_name.as_deref(),
                    );
                }
                glib::ControlFlow::Break
            }
            Ok(Err(error)) => {
                clear_list(&list);
                error_dialog(&window, &tr("Could Not Browse History"), &error.to_string());
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
        }
    });
}

fn append_file_row(
    window: &adw::Window,
    scope: BrowserScope,
    snapshot_id: &str,
    current_path: &Rc<RefCell<String>>,
    path_label: &gtk::Label,
    list: &gtk::ListBox,
    entry: PersonalDirectoryEntry,
    highlighted_name: Option<&str>,
) {
    let row = adw::ActionRow::new();
    row.set_title(&entry.name);
    let modified = chrono::DateTime::from_timestamp(entry.modified_unix_seconds, 0)
        .map(|time| {
            time.with_timezone(&chrono::Local)
                .format("%Y-%m-%d %H:%M")
                .to_string()
        })
        .unwrap_or_else(|| tr("Unknown date"));
    let subtitle = if entry.kind == "directory" {
        trf("Folder · {0}", &[&modified])
    } else {
        trf("{0} bytes · {1}", &[&entry.size.to_string(), &modified])
    };
    if highlighted_name == Some(entry.name.as_str()) {
        row.add_css_class("file-history-target");
        row.set_subtitle(&trf("Selected file · {0}", &[&subtitle]));
    } else {
        row.set_subtitle(&subtitle);
    }
    let action_label = if entry.kind == "directory" {
        tr("Open")
    } else {
        tr("Recover…")
    };
    let action = gtk::Button::with_label(&action_label);
    action.set_valign(gtk::Align::Center);
    row.add_suffix(&action);
    list.append(&row);
    if entry.kind == "directory" {
        let window = window.clone();
        let id = snapshot_id.to_string();
        let path = current_path.clone();
        let label = path_label.clone();
        let list = list.clone();
        let name = entry.name;
        action.connect_clicked(move |_| {
            let next = join_relative(&path.borrow(), &name);
            *path.borrow_mut() = next;
            load_directory(&window, scope.clone(), &id, &path, &label, &list, None);
        });
    } else {
        let window = window.clone();
        let id = snapshot_id.to_string();
        let relative = join_relative(&current_path.borrow(), &entry.name);
        let name = entry.name;
        action.connect_clicked(move |_| {
            choose_file_destination(&window, scope.clone(), &id, &relative, &name)
        });
    }
}

fn choose_file_destination(
    window: &adw::Window,
    scope: BrowserScope,
    snapshot_id: &str,
    relative: &str,
    name: &str,
) {
    let dialog = gtk::FileDialog::new();
    dialog.set_title(&tr("Recover Historical File"));
    dialog.set_initial_name(Some(name));
    let window_clone = window.clone();
    let id = snapshot_id.to_string();
    let relative = relative.to_string();
    dialog.save(
        Some(window),
        None::<&gtk::gio::Cancellable>,
        move |result| {
            let Ok(destination) = result else { return };
            let Some(path) = destination.path() else {
                error_dialog(
                    &window_clone,
                    &tr("Unsupported Destination"),
                    &tr("Choose a local filesystem destination."),
                );
                return;
            };
            run_restore(&window_clone, move || {
                restore_one_file(scope, &id, &relative, &path)
            });
        },
    );
}

fn choose_folder_destination(
    window: &adw::Window,
    scope: BrowserScope,
    snapshot_id: &str,
    relative: &str,
) {
    let dialog = gtk::FileDialog::new();
    dialog.set_title(&tr("Choose Where to Recover This Folder"));
    let window_clone = window.clone();
    let id = snapshot_id.to_string();
    let relative = relative.to_string();
    dialog.select_folder(
        Some(window),
        None::<&gtk::gio::Cancellable>,
        move |result| {
            let Ok(destination) = result else { return };
            let Some(parent) = destination.path() else {
                error_dialog(
                    &window_clone,
                    &tr("Unsupported Destination"),
                    &tr("Choose a local filesystem destination."),
                );
                return;
            };
            let leaf = Path::new(&relative)
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or("Recovered Personal Files");
            let destination = unique_destination(&parent, leaf);
            run_restore(&window_clone, move || {
                let client = WaypointHelperClient::new()?;
                restore_directory(&client, scope, &id, &relative, &destination)
            });
        },
    );
}

fn run_restore<F>(window: &adw::Window, operation: F)
where
    F: FnOnce() -> anyhow::Result<()> + Send + 'static,
{
    toast(window, &tr("Recovering Personal Files…"));
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let _ = sender.send(operation());
    });
    let window = window.clone();
    glib::timeout_add_local(Duration::from_millis(80), move || {
        match receiver.try_recv() {
            Ok(Ok(())) => {
                toast(&window, &tr("Personal Files recovered successfully"));
                glib::ControlFlow::Break
            }
            Ok(Err(error)) => {
                error_dialog(&window, &tr("Recovery Failed"), &error.to_string());
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
        }
    });
}

fn restore_one_file(
    scope: BrowserScope,
    snapshot_id: &str,
    relative: &str,
    destination: &Path,
) -> anyhow::Result<()> {
    let client = WaypointHelperClient::new()?;
    let mut source = export_file(
        &client,
        &scope,
        snapshot_id.to_string(),
        relative.to_string(),
    )?;
    write_recovered_file(&mut source, destination)
}

fn write_recovered_file(source: &mut impl Read, destination: &Path) -> anyhow::Result<()> {
    let parent = destination
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let (temporary, mut output) = create_recovery_temp_file(destination)?;
    let result = (|| -> anyhow::Result<()> {
        std::io::copy(source, &mut output)?;
        output.flush()?;
        output.sync_all()?;
        drop(output);
        std::fs::rename(&temporary, destination)?;
        std::fs::File::open(parent)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}

fn create_recovery_temp_file(destination: &Path) -> anyhow::Result<(PathBuf, std::fs::File)> {
    static NEXT_TEMPORARY: AtomicU64 = AtomicU64::new(0);

    let parent = destination
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let leaf = destination
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("recovered-file");
    for _ in 0..1_024 {
        let sequence = NEXT_TEMPORARY.fetch_add(1, Ordering::Relaxed);
        let temporary = parent.join(format!(
            ".{leaf}.anduinos-waypoint-{}-{sequence}.tmp",
            std::process::id()
        ));
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(&temporary)
        {
            Ok(file) => return Ok((temporary, file)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.into()),
        }
    }
    anyhow::bail!("Could not allocate a temporary recovery file")
}

fn restore_directory(
    client: &WaypointHelperClient,
    scope: BrowserScope,
    snapshot_id: &str,
    relative: &str,
    destination: &Path,
) -> anyhow::Result<()> {
    let mut recovered_entries = 0usize;
    std::fs::create_dir(destination)?;
    let result = restore_directory_bounded(
        client,
        scope,
        snapshot_id,
        relative,
        destination,
        0,
        &mut recovered_entries,
    );
    if let Err(error) = result {
        return match std::fs::remove_dir_all(destination) {
            Ok(()) => Err(error),
            Err(cleanup) => Err(anyhow::anyhow!(
                "{error}; could not remove the incomplete recovery folder: {cleanup}"
            )),
        };
    }
    Ok(())
}

fn restore_directory_bounded(
    client: &WaypointHelperClient,
    scope: BrowserScope,
    snapshot_id: &str,
    relative: &str,
    destination: &Path,
    depth: usize,
    recovered_entries: &mut usize,
) -> anyhow::Result<()> {
    const MAX_RECOVERY_DEPTH: usize = 256;
    const MAX_RECOVERY_ENTRIES: usize = 100_000;
    anyhow::ensure!(
        depth <= MAX_RECOVERY_DEPTH,
        "Historical folder exceeds the recovery depth limit"
    );
    for entry in list_files(
        client,
        &scope,
        snapshot_id.to_string(),
        relative.to_string(),
    )? {
        *recovered_entries = recovered_entries.saturating_add(1);
        anyhow::ensure!(
            *recovered_entries <= MAX_RECOVERY_ENTRIES,
            "Historical folder exceeds the recovery entry limit"
        );
        let source = join_relative(relative, &entry.name);
        let target = destination.join(&entry.name);
        if entry.kind == "directory" {
            std::fs::create_dir(&target)?;
            restore_directory_bounded(
                client,
                scope.clone(),
                snapshot_id,
                &source,
                &target,
                depth + 1,
                recovered_entries,
            )?;
        } else {
            let mut input = export_file(client, &scope, snapshot_id.to_string(), source)?;
            let mut output = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
                .open(&target)?;
            std::io::copy(&mut input, &mut output)?;
            output.flush()?;
            output.sync_all()?;
        }
    }
    std::fs::File::open(destination)?.sync_all()?;
    Ok(())
}

fn list_files(
    client: &WaypointHelperClient,
    scope: &BrowserScope,
    id: String,
    path: String,
) -> anyhow::Result<Vec<PersonalDirectoryEntry>> {
    match scope {
        BrowserScope::Home => client.list_personal_files(id, path),
        BrowserScope::System(lease) => {
            client.list_system_snapshot_files(lease.token.clone(), id, path)
        }
    }
}

fn export_file(
    client: &WaypointHelperClient,
    scope: &BrowserScope,
    id: String,
    path: String,
) -> anyhow::Result<std::fs::File> {
    match scope {
        BrowserScope::Home => client.export_personal_file(id, path),
        BrowserScope::System(lease) => {
            client.export_system_snapshot_file(lease.token.clone(), id, path)
        }
    }
}

fn unique_destination(parent: &Path, leaf: &str) -> PathBuf {
    let first = parent.join(leaf);
    if !first.exists() {
        return first;
    }
    for suffix in 1..=10_000 {
        let candidate = parent.join(format!("{leaf} (Recovered {suffix})"));
        if !candidate.exists() {
            return candidate;
        }
    }
    parent.join(format!(
        "Recovered Personal Files {}",
        chrono::Local::now().timestamp()
    ))
}

fn join_relative(parent: &str, name: &str) -> String {
    if parent.is_empty() {
        name.to_string()
    } else {
        format!("{parent}/{name}")
    }
}

fn clear_list(list: &gtk::ListBox) {
    while let Some(child) = list.first_child() {
        list.remove(&child);
    }
}

fn toast(window: &adw::Window, message: &str) {
    let dialog = adw::ToastOverlay::new();
    if let Some(content) = window.content() {
        window.set_content(None::<&gtk::Widget>);
        dialog.set_child(Some(&content));
        window.set_content(Some(&dialog));
    }
    dialog.add_toast(adw::Toast::new(message));
}

fn error_dialog(window: &adw::Window, title: &str, message: &str) {
    let dialog = adw::MessageDialog::new(Some(window), Some(title), Some(message));
    dialog.add_response("close", &tr("Close"));
    dialog.present();
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io;

    #[test]
    fn relative_join_never_adds_a_leading_separator() {
        assert_eq!(join_relative("", "Documents"), "Documents");
        assert_eq!(
            join_relative("Documents", "report.odt"),
            "Documents/report.odt"
        );
    }

    #[test]
    fn recovery_destination_does_not_select_an_existing_path() {
        let root = std::env::temp_dir().join(format!(
            "waypoint-personal-destination-{}",
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        std::fs::create_dir(&root).unwrap();
        std::fs::create_dir(root.join("Documents")).unwrap();
        assert_eq!(
            unique_destination(&root, "Documents"),
            root.join("Documents (Recovered 1)")
        );
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn focused_file_history_splits_root_and_nested_files() {
        assert_eq!(split_file_target("notes.txt"), ("", "notes.txt"));
        assert_eq!(
            split_file_target("Documents/Reports/report.odt"),
            ("Documents/Reports", "report.odt")
        );
    }

    #[test]
    fn focused_history_does_not_hide_authorization_or_transport_failures() {
        assert!(history_query_failed(&anyhow::anyhow!(
            "Authorization failed: dismissed"
        )));
        assert!(history_query_failed(&anyhow::anyhow!(
            "Failed to browse historical Personal Files"
        )));
        assert!(!history_query_failed(&anyhow::anyhow!(
            "Could not open personal path: not found"
        )));
    }

    struct FailingReader {
        emitted: bool,
    }

    impl Read for FailingReader {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            if self.emitted {
                return Err(io::Error::other("injected failure"));
            }
            self.emitted = true;
            let partial = b"partial";
            buffer[..partial.len()].copy_from_slice(partial);
            Ok(partial.len())
        }
    }

    #[test]
    fn failed_file_recovery_preserves_the_existing_destination() {
        let directory = std::env::temp_dir().join(format!(
            "waypoint-failed-file-recovery-{}",
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        std::fs::create_dir(&directory).unwrap();
        let destination = directory.join("document.txt");
        std::fs::write(&destination, b"original").unwrap();

        let result = write_recovered_file(&mut FailingReader { emitted: false }, &destination);

        assert!(result.is_err());
        assert_eq!(std::fs::read(&destination).unwrap(), b"original");
        assert_eq!(std::fs::read_dir(&directory).unwrap().count(), 1);
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn successful_file_recovery_atomically_replaces_the_destination() {
        let directory = std::env::temp_dir().join(format!(
            "waypoint-successful-file-recovery-{}",
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        std::fs::create_dir(&directory).unwrap();
        let destination = directory.join("document.txt");
        std::fs::write(&destination, b"original").unwrap();

        write_recovered_file(&mut &b"recovered"[..], &destination).unwrap();

        assert_eq!(std::fs::read(&destination).unwrap(), b"recovered");
        assert_eq!(std::fs::read_dir(&directory).unwrap().count(), 1);
        std::fs::remove_dir_all(directory).unwrap();
    }
}
