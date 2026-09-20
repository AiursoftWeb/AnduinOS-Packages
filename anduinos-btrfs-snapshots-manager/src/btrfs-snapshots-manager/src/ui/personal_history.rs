use std::cell::{Cell, RefCell};
use std::io::{Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

use adw::prelude::*;
use gtk::{gio, glib};
use libadwaita as adw;

use crate::dbus_client::{PersonalDirectoryEntry, PersonalSnapshot, SnapshotsManagerHelperClient};
use crate::file_history_request::{HistoryTarget, HistoryTargetKind};
use crate::i18n::{tr, trf};

pub fn show_snapshot_browser(parent: &adw::ApplicationWindow, id: &str, title: &str) {
    show_browser(parent, BrowserScope::Home, id, title, "", None);
}

pub fn show_system_snapshot_browser(parent: &adw::ApplicationWindow, id: &str, title: &str) {
    let weak_parent = parent.downgrade();
    let id = id.to_string();
    let title = title.to_string();
    glib::spawn_future_local(async move {
        let id_for_authorization = id.clone();
        let result = gio::spawn_blocking(move || {
            SnapshotsManagerHelperClient::new()
                .and_then(|client| client.begin_system_snapshot_browse(id_for_authorization))
        })
        .await
        .map_err(|_| anyhow::anyhow!("System snapshot authorization stopped unexpectedly"))
        .and_then(|result| result);
        let Some(parent) = weak_parent.upgrade() else {
            return;
        };
        match result {
            Ok(token) => show_browser(
                &parent,
                BrowserScope::System(Arc::new(SystemBrowserLease::new(token))),
                &id,
                &title,
                "",
                None,
            ),
            Err(error) => {
                let dialog = adw::MessageDialog::new(
                    Some(&parent),
                    Some(&tr("Could Not Browse System Snapshot")),
                    Some(&error.to_string()),
                );
                dialog.add_response("close", &tr("Close"));
                dialog.present();
            }
        }
    });
}

#[derive(Clone)]
enum BrowserScope {
    Home,
    System(Arc<SystemBrowserLease>),
}

impl BrowserScope {
    fn authorize_export(
        &self,
        client: &SnapshotsManagerHelperClient,
        snapshot_id: &str,
    ) -> anyhow::Result<Self> {
        match self {
            Self::Home => Ok(Self::Home),
            Self::System(_) => client
                .begin_system_snapshot_browse(snapshot_id.to_string())
                .map(|token| Self::System(Arc::new(SystemBrowserLease::new(token)))),
        }
    }
}

#[derive(Clone)]
struct BrowserUi {
    window: glib::WeakRef<adw::Window>,
    scope: BrowserScope,
    snapshot_id: String,
    current_path: Rc<RefCell<String>>,
    path_label: gtk::Label,
    list: gtk::ListBox,
    generation: Rc<Cell<u64>>,
}

impl BrowserUi {
    fn window(&self) -> Option<adw::Window> {
        self.window.upgrade()
    }
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
            let token = self.token.clone();
            let _ = std::thread::Builder::new()
                .name("btrfs-snapshots-manager-browse-release".into())
                .spawn(move || {
                    if let Err(error) = SnapshotsManagerHelperClient::new()
                        .and_then(|client| client.end_system_snapshot_browse(token))
                    {
                        log::debug!("Could not release system browser lease: {error}");
                    }
                });
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
/// Nautilus, and all historical reads still go through SnapshotsManagerHelperClient.
pub fn show_target(app: &adw::Application, target: HistoryTarget) {
    let window = adw::Window::new();
    window.set_application(Some(app));
    window.set_title(Some(&match target.kind {
        HistoryTargetKind::File => tr("File History"),
        HistoryTargetKind::Directory => tr("Folder History"),
    }));
    window.set_default_size(820, 680);

    let toolbar = adw::ToolbarView::new();
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
    toolbar.add_top_bar(&header);

    let root = gtk::Box::new(gtk::Orientation::Vertical, 0);

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
    scrolled.set_overlay_scrolling(false);
    scrolled.set_hscrollbar_policy(gtk::PolicyType::Never);
    scrolled.set_vscrollbar_policy(gtk::PolicyType::Automatic);
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
    toolbar.set_content(Some(&root));
    window.set_content(Some(&toolbar));

    let generation = Rc::new(Cell::new(0_u64));
    load_target_versions(&window, &stack, &list, target.clone(), &generation);
    let window_refresh = window.clone();
    let stack_refresh = stack.clone();
    let list_refresh = list.clone();
    let generation_refresh = generation.clone();
    refresh.connect_clicked(move |_| {
        load_target_versions(
            &window_refresh,
            &stack_refresh,
            &list_refresh,
            target.clone(),
            &generation_refresh,
        );
    });
    window.present();
}

fn load_target_versions(
    window: &adw::Window,
    stack: &gtk::Stack,
    list: &gtk::ListBox,
    target: HistoryTarget,
    generation: &Rc<Cell<u64>>,
) {
    let request_generation = generation.get().wrapping_add(1);
    generation.set(request_generation);
    stack.set_visible_child_name("loading");
    clear_list(list);
    let target_for_query = target.clone();
    let weak_window = window.downgrade();
    let stack = stack.clone();
    let list = list.clone();
    let generation = generation.clone();
    glib::spawn_future_local(async move {
        let result = gio::spawn_blocking(move || -> anyhow::Result<(Vec<TargetVersion>, usize)> {
            let client = SnapshotsManagerHelperClient::new()?;
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
        })
        .await
        .map_err(|_| anyhow::anyhow!("The file-history query stopped unexpectedly"))
        .and_then(|result| result);
        if generation.get() != request_generation {
            return;
        }
        let Some(window) = weak_window.upgrade() else {
            return;
        };
        match result {
            Ok((versions, issue_count)) => {
                clear_list(&list);
                if versions.is_empty() {
                    let row = adw::ActionRow::new();
                    row.set_title(&tr("No earlier version was found"));
                    row.set_subtitle(&tr(
                        "This item was not present in the available Home snapshots.",
                    ));
                    list.append(&row);
                } else {
                    for version in versions {
                        append_target_version_row(&window, &list, version, &target);
                    }
                }
                if issue_count > 0 {
                    let row = adw::ActionRow::new();
                    row.set_title(&tr("Some snapshots could not be loaded"));
                    row.set_subtitle(&trf(
                        "{0} damaged metadata entries were ignored",
                        &[&issue_count.to_string()],
                    ));
                    list.append(&row);
                }
                stack.set_visible_child_name("content");
            }
            Err(error) => {
                error_dialog(
                    &window,
                    &tr("Personal History Unavailable"),
                    &error.to_string(),
                );
                stack.set_visible_child_name("content");
            }
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
            "Snapshot {0} · {1} bytes · modified {2}",
            &[&created, &entry.size.to_string(), &modified],
        ));
    } else {
        row.set_subtitle(&trf("Snapshot {0} · Folder", &[&created]));
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
    let toolbar = adw::ToolbarView::new();
    let header = adw::HeaderBar::new();
    header.set_title_widget(Some(&adw::WindowTitle::new(&browser_title, title)));
    let up = gtk::Button::from_icon_name("go-up-symbolic");
    up.set_tooltip_text(Some(&tr("Parent folder")));
    header.pack_start(&up);
    let recover_folder = gtk::Button::with_label(&match &scope {
        BrowserScope::Home => tr("Recover This Folder…"),
        BrowserScope::System(_) => tr("Copy This Folder…"),
    });
    header.pack_end(&recover_folder);
    toolbar.add_top_bar(&header);

    let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
    let path_label = gtk::Label::new(None);
    path_label.set_text("~/");
    path_label.set_halign(gtk::Align::Start);
    path_label.set_margin_top(8);
    path_label.set_margin_start(16);
    path_label.set_margin_end(16);
    path_label.add_css_class("dim-label");
    root.append(&path_label);
    let scrolled = gtk::ScrolledWindow::new();
    scrolled.set_overlay_scrolling(false);
    scrolled.set_hscrollbar_policy(gtk::PolicyType::Never);
    scrolled.set_vscrollbar_policy(gtk::PolicyType::Automatic);
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
    toolbar.set_content(Some(&root));
    window.set_content(Some(&toolbar));

    let browser = BrowserUi {
        window: window.downgrade(),
        scope: scope.clone(),
        snapshot_id: snapshot_id.to_string(),
        current_path: Rc::new(RefCell::new(initial_path.to_string())),
        path_label,
        list,
        generation: Rc::new(Cell::new(0_u64)),
    };
    load_directory(&browser, highlighted_name);

    let browser_up = browser.clone();
    up.connect_clicked(move |_| {
        let next = browser_up
            .current_path
            .borrow()
            .rsplit_once('/')
            .map(|(parent, _)| parent.to_string())
            .unwrap_or_default();
        *browser_up.current_path.borrow_mut() = next;
        load_directory(&browser_up, None);
    });

    let browser_folder = browser.clone();
    recover_folder.connect_clicked(move |_| {
        if let Some(window) = browser_folder.window() {
            choose_folder_destination(
                &window,
                browser_folder.scope.clone(),
                &browser_folder.snapshot_id,
                &browser_folder.current_path.borrow(),
            );
        }
    });
    window.present();
}

fn load_directory(browser: &BrowserUi, highlighted_name: Option<String>) {
    let request_generation = browser.generation.get().wrapping_add(1);
    browser.generation.set(request_generation);
    clear_list(&browser.list);
    let loading = adw::ActionRow::new();
    loading.set_title(&tr("Loading folder…"));
    browser.list.append(&loading);
    let id = browser.snapshot_id.clone();
    let path = browser.current_path.borrow().clone();
    browser
        .path_label
        .set_text(&browser_path(&browser.scope, &path));
    let scope_worker = browser.scope.clone();
    let browser = browser.clone();
    glib::spawn_future_local(async move {
        let result = gio::spawn_blocking(move || {
            SnapshotsManagerHelperClient::new()
                .and_then(|client| list_files(&client, &scope_worker, id.clone(), path))
                .map(|entries| (entries, id))
        })
        .await
        .map_err(|_| anyhow::anyhow!("The snapshot folder query stopped unexpectedly"))
        .and_then(|result| result);
        if browser.generation.get() != request_generation {
            return;
        }
        let Some(window) = browser.window() else {
            return;
        };
        match result {
            Ok((entries, id)) => {
                clear_list(&browser.list);
                if entries.is_empty() {
                    let row = adw::ActionRow::new();
                    row.set_title(&tr("This folder is empty"));
                    browser.list.append(&row);
                }
                for entry in entries {
                    debug_assert_eq!(browser.snapshot_id, id);
                    append_file_row(&browser, entry, highlighted_name.as_deref());
                }
            }
            Err(error) => {
                clear_list(&browser.list);
                error_dialog(&window, &tr("Could Not Browse History"), &error.to_string());
            }
        }
    });
}

fn append_file_row(
    browser: &BrowserUi,
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
    browser.list.append(&row);
    if entry.kind == "directory" {
        let browser = browser.clone();
        let name = entry.name;
        action.connect_clicked(move |_| {
            let next = join_relative(&browser.current_path.borrow(), &name);
            *browser.current_path.borrow_mut() = next;
            load_directory(&browser, None);
        });
    } else {
        let browser = browser.clone();
        let relative = join_relative(&browser.current_path.borrow(), &entry.name);
        let name = entry.name;
        action.connect_clicked(move |_| {
            if let Some(window) = browser.window() {
                choose_file_destination(
                    &window,
                    browser.scope.clone(),
                    &browser.snapshot_id,
                    &relative,
                    &name,
                );
            }
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
                let client = SnapshotsManagerHelperClient::new()?;
                let scope = scope.authorize_export(&client, &id)?;
                restore_directory(&client, scope, &id, &relative, &destination)
            });
        },
    );
}

fn run_restore<F>(window: &adw::Window, operation: F)
where
    F: FnOnce() -> anyhow::Result<()> + Send + 'static,
{
    let progress = adw::Window::builder()
        .transient_for(window)
        .modal(true)
        .deletable(false)
        .resizable(false)
        .default_width(360)
        .default_height(140)
        .title(tr("Recovering Files"))
        .build();
    let content = gtk::Box::new(gtk::Orientation::Vertical, 16);
    content.set_margin_top(24);
    content.set_margin_bottom(24);
    content.set_margin_start(28);
    content.set_margin_end(28);
    let spinner = gtk::Spinner::new();
    spinner.set_spinning(true);
    spinner.set_halign(gtk::Align::Center);
    let label = gtk::Label::new(Some(&tr("Recovering files…")));
    label.add_css_class("heading");
    content.append(&spinner);
    content.append(&label);
    progress.set_content(Some(&content));
    progress.present();

    let weak_window = window.downgrade();
    let weak_progress = progress.downgrade();
    glib::spawn_future_local(async move {
        let result = gio::spawn_blocking(operation)
            .await
            .map_err(|_| anyhow::anyhow!("The file recovery stopped unexpectedly"))
            .and_then(|result| result);
        if let Some(progress) = weak_progress.upgrade() {
            progress.close();
        }
        let Some(window) = weak_window.upgrade() else {
            return;
        };
        match result {
            Ok(()) => {
                let dialog = adw::MessageDialog::new(
                    Some(&window),
                    Some(&tr("Files Recovered")),
                    Some(&tr("The selected files were recovered successfully.")),
                );
                dialog.add_response("close", &tr("Close"));
                dialog.present();
            }
            Err(error) => error_dialog(&window, &tr("Recovery Failed"), &error.to_string()),
        }
    });
}

fn restore_one_file(
    scope: BrowserScope,
    snapshot_id: &str,
    relative: &str,
    destination: &Path,
) -> anyhow::Result<()> {
    let client = SnapshotsManagerHelperClient::new()?;
    let scope = scope.authorize_export(&client, snapshot_id)?;
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
            ".{leaf}.anduinos-btrfs-snapshots-manager-{}-{sequence}.tmp",
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
    client: &SnapshotsManagerHelperClient,
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
    client: &SnapshotsManagerHelperClient,
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
    client: &SnapshotsManagerHelperClient,
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
    client: &SnapshotsManagerHelperClient,
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

fn browser_path(scope: &BrowserScope, relative: &str) -> String {
    match scope {
        BrowserScope::Home if relative.is_empty() => "~/".into(),
        BrowserScope::Home => format!("~/{relative}"),
        BrowserScope::System(_) if relative.is_empty() => "/".into(),
        BrowserScope::System(_) => format!("/{relative}"),
    }
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
            "btrfs-snapshots-manager-personal-destination-{}",
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
            "btrfs-snapshots-manager-failed-file-recovery-{}",
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
            "btrfs-snapshots-manager-successful-file-recovery-{}",
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
