use std::cell::RefCell;
use std::rc::Rc;
use std::sync::mpsc;
use std::time::Duration;

use adw::prelude::*;
use gtk::{gio, glib};

use anduinos_timeback::browsing::{BrowserEntry, DirectoryListing, EntryKind};
use anduinos_timeback::{client, copy_out};

use crate::i18n::i18n;

struct BrowserLocation {
    token: String,
    display_name: String,
}

struct SnapshotBrowser {
    snapshot_kind: String,
    snapshot_id: String,
    window: adw::Window,
    overlay: adw::ToastOverlay,
    list: gtk::ListBox,
    path_label: gtk::Label,
    up: gtk::Button,
    hidden: gtk::ToggleButton,
    location: RefCell<Vec<BrowserLocation>>,
}

pub fn present(
    parent: &adw::ApplicationWindow,
    snapshot_kind: &str,
    snapshot_id: &str,
    snapshot_title: &str,
) {
    let window = adw::Window::builder()
        .title(i18n("Snapshot Files"))
        .default_width(860)
        .default_height(620)
        .transient_for(parent)
        .build();
    let overlay = adw::ToastOverlay::new();
    let list = gtk::ListBox::builder()
        .selection_mode(gtk::SelectionMode::None)
        .css_classes(["boxed-list"])
        .margin_top(12)
        .margin_bottom(18)
        .margin_start(18)
        .margin_end(18)
        .build();
    let path_label = gtk::Label::builder()
        .label("/")
        .ellipsize(gtk::pango::EllipsizeMode::Middle)
        .hexpand(true)
        .xalign(0.0)
        .css_classes(["heading"])
        .build();
    let up = gtk::Button::builder()
        .icon_name("go-up-symbolic")
        .tooltip_text(i18n("Parent Folder"))
        .sensitive(false)
        .build();
    let hidden = gtk::ToggleButton::builder()
        .icon_name("view-reveal-symbolic")
        .tooltip_text(i18n("Show Hidden Files"))
        .build();

    let header = adw::HeaderBar::new();
    header.pack_start(&up);
    header.pack_end(&hidden);
    header.set_title_widget(Some(&adw::WindowTitle::new(
        snapshot_title,
        &i18n("Read-only snapshot"),
    )));

    let path_bar = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(8)
        .margin_start(18)
        .margin_end(18)
        .margin_top(10)
        .margin_bottom(2)
        .build();
    path_bar.append(&gtk::Image::builder().icon_name("folder-symbolic").build());
    path_bar.append(&path_label);

    let scrolled = gtk::ScrolledWindow::builder()
        .hscrollbar_policy(gtk::PolicyType::Never)
        .vexpand(true)
        .child(&list)
        .build();
    let content = gtk::Box::new(gtk::Orientation::Vertical, 0);
    content.append(&path_bar);
    content.append(&scrolled);
    overlay.set_child(Some(&content));
    let toolbar = adw::ToolbarView::builder().content(&overlay).build();
    toolbar.add_top_bar(&header);
    window.set_content(Some(&toolbar));

    let browser = Rc::new(SnapshotBrowser {
        snapshot_kind: snapshot_kind.into(),
        snapshot_id: snapshot_id.into(),
        window,
        overlay,
        list,
        path_label,
        up,
        hidden,
        location: RefCell::new(Vec::new()),
    });
    let browser_for_up = browser.clone();
    browser.up.connect_clicked(move |_| {
        browser_for_up.location.borrow_mut().pop();
        browser_for_up.refresh();
    });
    let browser_for_hidden = browser.clone();
    browser
        .hidden
        .connect_toggled(move |_| browser_for_hidden.refresh());
    browser.refresh();
    browser.window.present();
}

impl SnapshotBrowser {
    fn path_tokens(&self) -> Vec<String> {
        self.location
            .borrow()
            .iter()
            .map(|part| part.token.clone())
            .collect()
    }

    fn refresh(self: &Rc<Self>) {
        while let Some(child) = self.list.first_child() {
            self.list.remove(&child);
        }
        let path = self.path_tokens();
        self.up.set_sensitive(!path.is_empty());
        let display_path = self
            .location
            .borrow()
            .iter()
            .map(|part| part.display_name.as_str())
            .collect::<Vec<_>>()
            .join("/");
        self.path_label.set_label(&format!("/{display_path}"));
        match client::list_snapshot_directory(&self.snapshot_kind, &self.snapshot_id, &path) {
            Ok(listing) => self.populate(listing),
            Err(error) => {
                self.list.append(
                    &adw::StatusPage::builder()
                        .icon_name("dialog-error-symbolic")
                        .title(i18n("Could Not Open Folder"))
                        .description(error.to_string())
                        .build(),
                );
            }
        }
    }

    fn populate(self: &Rc<Self>, listing: DirectoryListing) {
        let show_hidden = self.hidden.is_active();
        let mut visible = 0usize;
        for entry in listing.entries {
            if entry.hidden && !show_hidden {
                continue;
            }
            visible += 1;
            self.add_entry(entry);
        }
        if visible == 0 {
            self.list.append(
                &adw::StatusPage::builder()
                    .icon_name("folder-symbolic")
                    .title(i18n("This Folder Is Empty"))
                    .description(i18n("There are no files to display."))
                    .build(),
            );
        } else if listing.truncated {
            self.overlay.add_toast(adw::Toast::new(&i18n(
                "This folder is very large; only the first 1,000 items are shown.",
            )));
        }
    }

    fn add_entry(self: &Rc<Self>, entry: BrowserEntry) {
        let row = adw::ActionRow::builder()
            .title(&entry.display_name)
            .subtitle(entry_subtitle(&entry))
            .activatable(entry.kind == EntryKind::Directory)
            .build();
        row.add_prefix(
            &gtk::Image::builder()
                .icon_name(entry_icon(entry.kind))
                .pixel_size(28)
                .build(),
        );
        match entry.kind {
            EntryKind::Directory => {
                row.add_suffix(
                    &gtk::Image::builder()
                        .icon_name("go-next-symbolic")
                        .css_classes(["dim-label"])
                        .build(),
                );
                let browser = self.clone();
                let token = entry.token;
                let display_name = entry.display_name;
                row.connect_activated(move |_| {
                    browser.location.borrow_mut().push(BrowserLocation {
                        token: token.clone(),
                        display_name: display_name.clone(),
                    });
                    browser.refresh();
                });
            }
            EntryKind::File => {
                let copy = gtk::Button::builder()
                    .icon_name("document-save-symbolic")
                    .tooltip_text(i18n("Copy Out…"))
                    .valign(gtk::Align::Center)
                    .css_classes(["flat"])
                    .build();
                let browser = self.clone();
                let token = entry.token;
                let display_name = entry.display_name;
                copy.connect_clicked(move |_| {
                    browser.copy_out(&token, &display_name);
                });
                row.add_suffix(&copy);
            }
            EntryKind::Symlink | EntryKind::Special => {
                row.add_suffix(
                    &gtk::Label::builder()
                        .label(i18n("Not copyable"))
                        .css_classes(["caption", "dim-label"])
                        .build(),
                );
            }
        }
        self.list.append(&row);
    }

    fn copy_out(self: &Rc<Self>, token: &str, display_name: &str) {
        let mut path = self.path_tokens();
        path.push(token.to_string());
        let (source, metadata) =
            match client::open_snapshot_file(&self.snapshot_kind, &self.snapshot_id, &path) {
                Ok(opened) => opened,
                Err(error) => {
                    self.overlay.add_toast(adw::Toast::new(&error.to_string()));
                    return;
                }
            };
        let dialog = gtk::FileDialog::builder()
            .title(i18n("Copy File Out of Snapshot"))
            .initial_name(display_name)
            .accept_label(i18n("Copy"))
            .build();
        let browser = self.clone();
        dialog.save(
            Some(&self.window),
            None::<&gio::Cancellable>,
            move |result| {
                let destination = match result {
                    Ok(file) => match file.path() {
                        Some(path) => path,
                        None => {
                            browser.overlay.add_toast(adw::Toast::new(&i18n(
                                "Choose a location on this computer.",
                            )));
                            return;
                        }
                    },
                    Err(error) if error.matches(gtk::DialogError::Dismissed) => return,
                    Err(error) => {
                        browser
                            .overlay
                            .add_toast(adw::Toast::new(&error.to_string()));
                        return;
                    }
                };
                let (sender, receiver) = mpsc::channel();
                std::thread::spawn(move || {
                    let result = copy_out::copy_file_atomic(source, &metadata, &destination)
                        .map_err(|error| error.to_string());
                    let _ = sender.send(result);
                });
                let browser_for_result = browser.clone();
                glib::timeout_add_local(Duration::from_millis(100), move || {
                    match receiver.try_recv() {
                        Ok(Ok(bytes)) => {
                            browser_for_result
                                .overlay
                                .add_toast(adw::Toast::new(&format!(
                                    "{} ({})",
                                    i18n("File copied successfully"),
                                    format_size(bytes)
                                )));
                            glib::ControlFlow::Break
                        }
                        Ok(Err(error)) => {
                            browser_for_result
                                .overlay
                                .add_toast(adw::Toast::new(&format!(
                                    "{}: {error}",
                                    i18n("Could not copy the file")
                                )));
                            glib::ControlFlow::Break
                        }
                        Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                        Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
                    }
                });
            },
        );
    }
}

fn entry_icon(kind: EntryKind) -> &'static str {
    match kind {
        EntryKind::Directory => "folder-symbolic",
        EntryKind::File => "text-x-generic-symbolic",
        EntryKind::Symlink => "emblem-symbolic-link-symbolic",
        EntryKind::Special => "application-x-executable-symbolic",
    }
}

fn entry_subtitle(entry: &BrowserEntry) -> String {
    match entry.kind {
        EntryKind::Directory => i18n("Folder"),
        EntryKind::File => format_size(entry.size),
        EntryKind::Symlink => i18n("Symbolic link — not followed for safety"),
        EntryKind::Special => i18n("Special file — cannot be copied"),
    }
}

fn format_size(bytes: u64) -> String {
    if bytes < 1_024 {
        format!("{bytes} B")
    } else if bytes < 1_048_576 {
        format!("{:.1} KB", bytes as f64 / 1_024.0)
    } else if bytes < 1_073_741_824 {
        format!("{:.1} MB", bytes as f64 / 1_048_576.0)
    } else {
        format!("{:.1} GB", bytes as f64 / 1_073_741_824.0)
    }
}
