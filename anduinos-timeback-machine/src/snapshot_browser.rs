use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet, VecDeque};
use std::io::Read;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::time::Duration;

use adw::prelude::*;
use gtk::gdk_pixbuf::prelude::PixbufLoaderExt;
use gtk::{gio, glib};

use anduinos_timeback::browser_preferences::{
    BrowserConflictPolicy, BrowserPreferences, BrowserSortMode, BrowserViewMode,
};
use anduinos_timeback::browsing::{encode_name_token, BrowserEntry, DirectoryListing, EntryKind};
use anduinos_timeback::copy_out::{
    ConflictPolicy, ExportError, ExportProgress, ExportReport, ExportSelection, TreeStatistics,
    TreeStatisticsError,
};
use anduinos_timeback::snapshot_search::{
    self, SnapshotSearchError, SnapshotSearchHit, SnapshotSearchReport,
};
use anduinos_timeback::{client, copy_out};

use crate::i18n::i18n;

#[derive(Clone, Eq, PartialEq)]
struct BrowserLocation {
    token: String,
    display_name: String,
}

#[derive(Clone)]
struct HistoryEntry {
    location: Vec<BrowserLocation>,
    search: String,
}

#[derive(Clone)]
struct QuickLocation {
    label: String,
    icon: &'static str,
    location: Vec<BrowserLocation>,
}

enum ExportEvent {
    Progress(ExportProgress),
    Finished(Result<ExportReport, ExportError>),
}

#[derive(Clone)]
struct ThumbnailRequest {
    key: String,
    path: Vec<String>,
}

struct ThumbnailPixels {
    key: String,
    width: i32,
    height: i32,
    stride: usize,
    has_alpha: bool,
    pixels: Vec<u8>,
}

enum ThumbnailEvent {
    Ready(ThumbnailPixels),
    Finished,
}

#[derive(Default)]
struct ThumbnailCache {
    textures: HashMap<String, gtk::gdk::Texture>,
    order: VecDeque<String>,
}

impl ThumbnailCache {
    fn get(&mut self, key: &str) -> Option<gtk::gdk::Texture> {
        let texture = self.textures.get(key)?.clone();
        self.order.retain(|candidate| candidate != key);
        self.order.push_back(key.to_string());
        Some(texture)
    }

    fn insert(&mut self, key: String, texture: gtk::gdk::Texture) {
        const MAX_CACHED_THUMBNAILS: usize = 96;
        self.order.retain(|candidate| candidate != &key);
        self.order.push_back(key.clone());
        self.textures.insert(key, texture);
        while self.textures.len() > MAX_CACHED_THUMBNAILS {
            if let Some(oldest) = self.order.pop_front() {
                self.textures.remove(&oldest);
            }
        }
    }
}

struct SnapshotBrowser {
    session_id: String,
    window: adw::Window,
    overlay: adw::ToastOverlay,
    list: gtk::ListBox,
    grid: gtk::FlowBox,
    search_list: gtk::ListBox,
    results: gtk::Stack,
    places: gtk::ListBox,
    breadcrumbs: gtk::Box,
    search: gtk::SearchEntry,
    search_spinner: gtk::Spinner,
    up: gtk::Button,
    back: gtk::Button,
    forward: gtk::Button,
    hidden: gtk::ToggleButton,
    grid_mode: gtk::ToggleButton,
    sort: gtk::DropDown,
    descending: gtk::ToggleButton,
    copy_selected: gtk::Button,
    selection_label: gtk::Label,
    conflict: gtk::DropDown,
    action_bar: gtk::ActionBar,
    progress_revealer: gtk::Revealer,
    progress: gtk::ProgressBar,
    progress_label: gtk::Label,
    cancel: gtk::Button,
    location: RefCell<Vec<BrowserLocation>>,
    back_history: RefCell<Vec<HistoryEntry>>,
    forward_history: RefCell<Vec<HistoryEntry>>,
    listing: RefCell<Option<DirectoryListing>>,
    selected: RefCell<HashMap<String, BrowserEntry>>,
    active_cancel: RefCell<Option<Arc<AtomicBool>>>,
    quick_locations: Vec<QuickLocation>,
    thumbnail_generation: Cell<u64>,
    thumbnail_cancel: RefCell<Option<Arc<AtomicBool>>>,
    thumbnail_requests: RefCell<Vec<ThumbnailRequest>>,
    thumbnail_targets: RefCell<HashMap<String, Vec<gtk::Picture>>>,
    thumbnail_cache: RefCell<ThumbnailCache>,
    search_generation: Cell<u64>,
    search_cancel: RefCell<Option<Arc<AtomicBool>>>,
    search_report: RefCell<Option<SnapshotSearchReport>>,
    reveal_entry: RefCell<Option<BrowserEntry>>,
    reveal_focus_pending: Cell<bool>,
}

pub fn present(
    parent: &adw::ApplicationWindow,
    snapshot_kind: &str,
    snapshot_id: &str,
    snapshot_title: &str,
) {
    let session_id = match client::begin_snapshot_browse(snapshot_kind, snapshot_id) {
        Ok(session_id) => session_id,
        Err(error) => {
            let dialog = adw::AlertDialog::builder()
                .heading(i18n("Could Not Browse Snapshot"))
                .body(error.to_string())
                .close_response("close")
                .build();
            dialog.add_response("close", &i18n("Close"));
            dialog.present(Some(parent));
            return;
        }
    };
    let quick_locations = discover_quick_locations(snapshot_kind, &session_id);
    let preferences = BrowserPreferences::load();
    let window = adw::Window::builder()
        .title(i18n("Snapshot Files"))
        .default_width(900)
        .default_height(660)
        .transient_for(parent)
        .build();
    let overlay = adw::ToastOverlay::new();
    let list = gtk::ListBox::builder()
        .selection_mode(gtk::SelectionMode::None)
        .css_classes(["boxed-list"])
        .margin_top(10)
        .margin_bottom(18)
        .margin_start(18)
        .margin_end(18)
        .build();
    let grid = gtk::FlowBox::builder()
        .selection_mode(gtk::SelectionMode::None)
        .min_children_per_line(2)
        .max_children_per_line(8)
        .column_spacing(12)
        .row_spacing(12)
        .homogeneous(true)
        .margin_top(14)
        .margin_bottom(18)
        .margin_start(18)
        .margin_end(18)
        .build();
    let breadcrumbs = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(2)
        .hexpand(true)
        .build();
    let breadcrumb_scroll = gtk::ScrolledWindow::builder()
        .hscrollbar_policy(gtk::PolicyType::Automatic)
        .vscrollbar_policy(gtk::PolicyType::Never)
        .hexpand(true)
        .child(&breadcrumbs)
        .build();
    let search = gtk::SearchEntry::builder()
        .placeholder_text(i18n("Search This Snapshot Location"))
        .hexpand(true)
        .build();
    let search_spinner = gtk::Spinner::builder()
        .tooltip_text(i18n("Searching Snapshot…"))
        .visible(false)
        .build();
    let up = gtk::Button::builder()
        .icon_name("go-up-symbolic")
        .tooltip_text(i18n("Parent Folder"))
        .sensitive(false)
        .build();
    let back = gtk::Button::builder()
        .icon_name("go-previous-symbolic")
        .tooltip_text(i18n("Back"))
        .sensitive(false)
        .build();
    let forward = gtk::Button::builder()
        .icon_name("go-next-symbolic")
        .tooltip_text(i18n("Forward"))
        .sensitive(false)
        .build();
    let hidden = gtk::ToggleButton::builder()
        .icon_name("view-reveal-symbolic")
        .tooltip_text(i18n("Show Hidden Files"))
        .active(preferences.show_hidden)
        .build();
    let grid_mode = gtk::ToggleButton::builder()
        .icon_name(if preferences.view_mode == BrowserViewMode::Grid {
            "view-list-symbolic"
        } else {
            "view-grid-symbolic"
        })
        .tooltip_text(if preferences.view_mode == BrowserViewMode::Grid {
            i18n("List View")
        } else {
            i18n("Grid View")
        })
        .active(preferences.view_mode == BrowserViewMode::Grid)
        .build();
    let sort = gtk::DropDown::from_strings(&[&i18n("Name"), &i18n("Modified"), &i18n("Size")]);
    sort.set_selected(match preferences.sort_mode {
        BrowserSortMode::Name => 0,
        BrowserSortMode::Modified => 1,
        BrowserSortMode::Size => 2,
    });
    sort.set_tooltip_text(Some(&i18n("Sort files")));
    let descending = gtk::ToggleButton::builder()
        .icon_name("view-sort-descending-symbolic")
        .tooltip_text(i18n("Reverse Sort Order"))
        .active(preferences.descending)
        .build();
    let copy_selected = gtk::Button::builder()
        .label(i18n("Copy Selected…"))
        .icon_name("edit-copy-symbolic")
        .css_classes(["suggested-action"])
        .sensitive(false)
        .build();
    let selection_label = gtk::Label::builder()
        .label(i18n("No items selected"))
        .css_classes(["dim-label"])
        .hexpand(true)
        .xalign(0.0)
        .build();
    let conflict =
        gtk::DropDown::from_strings(&[&i18n("Keep Both"), &i18n("Replace"), &i18n("Skip")]);
    conflict.set_selected(match preferences.conflict_policy {
        BrowserConflictPolicy::KeepBoth => 0,
        BrowserConflictPolicy::Replace => 1,
        BrowserConflictPolicy::Skip => 2,
    });
    conflict.set_tooltip_text(Some(&i18n("When an item already exists")));

    let header = adw::HeaderBar::new();
    header.pack_start(&back);
    header.pack_start(&forward);
    header.pack_start(&up);
    header.pack_end(&grid_mode);
    header.pack_end(&hidden);
    header.set_title_widget(Some(&adw::WindowTitle::new(
        snapshot_title,
        &i18n("Read-only snapshot"),
    )));

    let location_bar = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(8)
        .margin_start(18)
        .margin_end(18)
        .margin_top(10)
        .build();
    location_bar.append(&breadcrumb_scroll);
    location_bar.append(&sort);
    location_bar.append(&descending);
    let search_box = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(8)
        .margin_start(18)
        .margin_end(18)
        .margin_top(8)
        .build();
    search_box.append(&search);
    search_box.append(&search_spinner);

    let scrolled = gtk::ScrolledWindow::builder()
        .hscrollbar_policy(gtk::PolicyType::Never)
        .vexpand(true)
        .child(&list)
        .build();
    let grid_scrolled = gtk::ScrolledWindow::builder()
        .hscrollbar_policy(gtk::PolicyType::Never)
        .vexpand(true)
        .child(&grid)
        .build();
    let search_list = gtk::ListBox::builder()
        .selection_mode(gtk::SelectionMode::None)
        .css_classes(["boxed-list"])
        .margin_top(10)
        .margin_bottom(18)
        .margin_start(18)
        .margin_end(18)
        .build();
    let search_scrolled = gtk::ScrolledWindow::builder()
        .hscrollbar_policy(gtk::PolicyType::Never)
        .vexpand(true)
        .child(&search_list)
        .build();
    let results = gtk::Stack::builder()
        .transition_type(gtk::StackTransitionType::Crossfade)
        .vexpand(true)
        .build();
    results.add_named(&scrolled, Some("list"));
    results.add_named(&grid_scrolled, Some("grid"));
    results.add_named(&search_scrolled, Some("search"));
    results.set_visible_child_name(if preferences.view_mode == BrowserViewMode::Grid {
        "grid"
    } else {
        "list"
    });
    let places = gtk::ListBox::builder()
        .selection_mode(gtk::SelectionMode::Single)
        .css_classes(["navigation-sidebar"])
        .build();
    for place in &quick_locations {
        let content = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(10)
            .margin_top(8)
            .margin_bottom(8)
            .margin_start(10)
            .margin_end(10)
            .build();
        content.append(
            &gtk::Image::builder()
                .icon_name(place.icon)
                .pixel_size(20)
                .build(),
        );
        content.append(
            &gtk::Label::builder()
                .label(&place.label)
                .xalign(0.0)
                .ellipsize(gtk::pango::EllipsizeMode::End)
                .build(),
        );
        places.append(&gtk::ListBoxRow::builder().child(&content).build());
    }
    let places_title = gtk::Label::builder()
        .label(i18n("Places"))
        .xalign(0.0)
        .css_classes(["heading"])
        .margin_top(14)
        .margin_bottom(6)
        .margin_start(14)
        .margin_end(10)
        .build();
    let places_scrolled = gtk::ScrolledWindow::builder()
        .hscrollbar_policy(gtk::PolicyType::Never)
        .vscrollbar_policy(gtk::PolicyType::Automatic)
        .width_request(190)
        .vexpand(true)
        .child(&places)
        .build();
    let places_panel = gtk::Box::new(gtk::Orientation::Vertical, 0);
    places_panel.append(&places_title);
    places_panel.append(&places_scrolled);
    places_panel.add_css_class("view");
    let browser_pane = gtk::Paned::new(gtk::Orientation::Horizontal);
    browser_pane.set_start_child(Some(&places_panel));
    browser_pane.set_end_child(Some(&results));
    browser_pane.set_position(190);
    browser_pane.set_resize_start_child(false);
    browser_pane.set_shrink_start_child(false);
    let action_bar = gtk::ActionBar::new();
    action_bar.pack_start(&selection_label);
    action_bar.pack_end(&copy_selected);
    action_bar.pack_end(&conflict);

    let progress = gtk::ProgressBar::builder().hexpand(true).build();
    let progress_label = gtk::Label::builder()
        .label(i18n("Preparing copy…"))
        .xalign(0.0)
        .hexpand(true)
        .build();
    let cancel = gtk::Button::builder()
        .label(i18n("Cancel"))
        .css_classes(["destructive-action"])
        .build();
    let progress_row = gtk::Box::builder()
        .orientation(gtk::Orientation::Horizontal)
        .spacing(12)
        .margin_start(18)
        .margin_end(18)
        .margin_top(8)
        .margin_bottom(8)
        .build();
    let progress_copy = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(5)
        .hexpand(true)
        .build();
    progress_copy.append(&progress_label);
    progress_copy.append(&progress);
    progress_row.append(&progress_copy);
    progress_row.append(&cancel);
    let progress_revealer = gtk::Revealer::builder()
        .transition_type(gtk::RevealerTransitionType::SlideUp)
        .child(&progress_row)
        .reveal_child(false)
        .build();

    let content = gtk::Box::new(gtk::Orientation::Vertical, 0);
    content.append(&location_bar);
    content.append(&search_box);
    content.append(&browser_pane);
    content.append(&action_bar);
    content.append(&progress_revealer);
    overlay.set_child(Some(&content));
    let toolbar = adw::ToolbarView::builder().content(&overlay).build();
    toolbar.add_top_bar(&header);
    window.set_content(Some(&toolbar));

    let browser = Rc::new(SnapshotBrowser {
        session_id,
        window,
        overlay,
        list,
        grid,
        search_list,
        results,
        places,
        breadcrumbs,
        search,
        search_spinner,
        up,
        back,
        forward,
        hidden,
        grid_mode,
        sort,
        descending,
        copy_selected,
        selection_label,
        conflict,
        action_bar,
        progress_revealer,
        progress,
        progress_label,
        cancel,
        location: RefCell::new(Vec::new()),
        back_history: RefCell::new(Vec::new()),
        forward_history: RefCell::new(Vec::new()),
        listing: RefCell::new(None),
        selected: RefCell::new(HashMap::new()),
        active_cancel: RefCell::new(None),
        quick_locations,
        thumbnail_generation: Cell::new(0),
        thumbnail_cancel: RefCell::new(None),
        thumbnail_requests: RefCell::new(Vec::new()),
        thumbnail_targets: RefCell::new(HashMap::new()),
        thumbnail_cache: RefCell::new(ThumbnailCache::default()),
        search_generation: Cell::new(0),
        search_cancel: RefCell::new(None),
        search_report: RefCell::new(None),
        reveal_entry: RefCell::new(None),
        reveal_focus_pending: Cell::new(false),
    });
    let browser_for_places = browser.clone();
    browser.places.connect_row_activated(move |_, row| {
        let Some(place) = browser_for_places.quick_locations.get(row.index() as usize) else {
            return;
        };
        browser_for_places.navigate_to(place.location.clone());
    });
    let browser_for_up = browser.clone();
    browser.up.connect_clicked(move |_| {
        let mut target = browser_for_up.location.borrow().clone();
        target.pop();
        browser_for_up.navigate_to(target);
    });
    let browser_for_back = browser.clone();
    browser
        .back
        .connect_clicked(move |_| browser_for_back.navigate_back());
    let browser_for_forward = browser.clone();
    browser
        .forward
        .connect_clicked(move |_| browser_for_forward.navigate_forward());
    let browser_for_hidden = browser.clone();
    browser.hidden.connect_toggled(move |_| {
        browser_for_hidden.persist_preferences();
        if browser_for_hidden.search_active() {
            browser_for_hidden.schedule_search();
        } else {
            browser_for_hidden.render_listing();
        }
    });
    let browser_for_grid = browser.clone();
    browser.grid_mode.connect_toggled(move |button| {
        if !browser_for_grid.search_active() {
            browser_for_grid
                .results
                .set_visible_child_name(if button.is_active() { "grid" } else { "list" });
        }
        button.set_icon_name(if button.is_active() {
            "view-list-symbolic"
        } else {
            "view-grid-symbolic"
        });
        button.set_tooltip_text(Some(&if button.is_active() {
            i18n("List View")
        } else {
            i18n("Grid View")
        }));
        browser_for_grid.persist_preferences();
        if !browser_for_grid.search_active() {
            browser_for_grid.render_listing();
        }
    });
    let browser_for_sort = browser.clone();
    browser.sort.connect_selected_notify(move |_| {
        browser_for_sort.persist_preferences();
        if browser_for_sort.search_active() {
            browser_for_sort.render_search_report();
        } else {
            browser_for_sort.refresh_preserving_selection();
        }
    });
    let browser_for_descending = browser.clone();
    browser.descending.connect_toggled(move |_| {
        browser_for_descending.persist_preferences();
        if browser_for_descending.search_active() {
            browser_for_descending.render_search_report();
        } else {
            browser_for_descending.refresh_preserving_selection();
        }
    });
    let browser_for_conflict = browser.clone();
    browser
        .conflict
        .connect_selected_notify(move |_| browser_for_conflict.persist_preferences());
    let browser_for_search = browser.clone();
    browser
        .search
        .connect_search_changed(move |_| browser_for_search.schedule_search());
    let browser_for_copy = browser.clone();
    browser
        .copy_selected
        .connect_clicked(move |_| browser_for_copy.choose_export_folder());
    let browser_for_cancel = browser.clone();
    browser.cancel.connect_clicked(move |button| {
        if let Some(cancelled) = browser_for_cancel.active_cancel.borrow().as_ref() {
            cancelled.store(true, Ordering::Release);
            button.set_sensitive(false);
            browser_for_cancel
                .progress_label
                .set_label(&i18n("Cancelling…"));
        }
    });
    let browser_for_close = browser.clone();
    browser.window.connect_close_request(move |_| {
        if let Some(cancelled) = browser_for_close.active_cancel.borrow().as_ref() {
            cancelled.store(true, Ordering::Release);
            browser_for_close.overlay.add_toast(adw::Toast::new(&i18n(
                "Cancelling the active copy before closing…",
            )));
            return glib::Propagation::Stop;
        }
        if let Some(cancelled) = browser_for_close.thumbnail_cancel.borrow().as_ref() {
            cancelled.store(true, Ordering::Release);
        }
        if let Some(cancelled) = browser_for_close.search_cancel.borrow().as_ref() {
            cancelled.store(true, Ordering::Release);
        }
        let _ = client::close_snapshot_browse(&browser_for_close.session_id);
        glib::Propagation::Proceed
    });
    let weak_browser = Rc::downgrade(&browser);
    glib::timeout_add_local(Duration::from_secs(2 * 60), move || {
        let Some(browser) = weak_browser.upgrade() else {
            return glib::ControlFlow::Break;
        };
        if !browser.window.is_visible() {
            return glib::ControlFlow::Break;
        }
        if let Err(error) = client::keep_snapshot_browse_alive(&browser.session_id) {
            browser
                .overlay
                .add_toast(adw::Toast::new(&error.to_string()));
            return glib::ControlFlow::Break;
        }
        glib::ControlFlow::Continue
    });
    browser.install_shortcuts();
    browser.refresh();
    browser.window.present();
}

impl SnapshotBrowser {
    fn persist_preferences(&self) {
        let preferences = BrowserPreferences {
            view_mode: if self.grid_mode.is_active() {
                BrowserViewMode::Grid
            } else {
                BrowserViewMode::List
            },
            show_hidden: self.hidden.is_active(),
            sort_mode: match self.sort.selected() {
                1 => BrowserSortMode::Modified,
                2 => BrowserSortMode::Size,
                _ => BrowserSortMode::Name,
            },
            descending: self.descending.is_active(),
            conflict_policy: match self.conflict.selected() {
                1 => BrowserConflictPolicy::Replace,
                2 => BrowserConflictPolicy::Skip,
                _ => BrowserConflictPolicy::KeepBoth,
            },
            ..BrowserPreferences::default()
        };
        if let Err(error) = preferences.save() {
            self.overlay.add_toast(adw::Toast::new(&format!(
                "{}: {error}",
                i18n("Could not save browser preferences")
            )));
        }
    }

    fn install_shortcuts(self: &Rc<Self>) {
        let controller = gtk::ShortcutController::new();
        controller.set_scope(gtk::ShortcutScope::Local);
        for (trigger, action) in [
            (
                "<Control>f",
                gtk::CallbackAction::new({
                    let browser = self.clone();
                    move |_, _| {
                        browser.search.grab_focus();
                        glib::Propagation::Stop
                    }
                }),
            ),
            (
                "<Control>a",
                gtk::CallbackAction::new({
                    let browser = self.clone();
                    move |_, _| {
                        browser.select_all_visible();
                        glib::Propagation::Stop
                    }
                }),
            ),
            (
                "<Control>c",
                gtk::CallbackAction::new({
                    let browser = self.clone();
                    move |_, _| {
                        if !browser.selected.borrow().is_empty()
                            && browser.active_cancel.borrow().is_none()
                        {
                            browser.choose_export_folder();
                        }
                        glib::Propagation::Stop
                    }
                }),
            ),
            (
                "BackSpace",
                gtk::CallbackAction::new({
                    let browser = self.clone();
                    move |_, _| {
                        if !browser.location.borrow().is_empty() {
                            let mut target = browser.location.borrow().clone();
                            target.pop();
                            browser.navigate_to(target);
                        }
                        glib::Propagation::Stop
                    }
                }),
            ),
            (
                "<Alt>Left",
                gtk::CallbackAction::new({
                    let browser = self.clone();
                    move |_, _| {
                        browser.navigate_back();
                        glib::Propagation::Stop
                    }
                }),
            ),
            (
                "<Alt>Right",
                gtk::CallbackAction::new({
                    let browser = self.clone();
                    move |_, _| {
                        browser.navigate_forward();
                        glib::Propagation::Stop
                    }
                }),
            ),
            (
                "Escape",
                gtk::CallbackAction::new({
                    let browser = self.clone();
                    move |_, _| {
                        if let Some(cancelled) = browser.active_cancel.borrow().as_ref() {
                            cancelled.store(true, Ordering::Release);
                        } else if !browser.search.text().is_empty() {
                            browser.search.set_text("");
                        }
                        glib::Propagation::Stop
                    }
                }),
            ),
        ] {
            if let Some(trigger) = gtk::ShortcutTrigger::parse_string(trigger) {
                controller.add_shortcut(gtk::Shortcut::new(Some(trigger), Some(action)));
            }
        }
        self.window.add_controller(controller);
    }

    fn select_all_visible(self: &Rc<Self>) {
        if self.search_active() {
            return;
        }
        let show_hidden = self.hidden.is_active();
        if let Some(listing) = self.listing.borrow().as_ref() {
            let mut selected = self.selected.borrow_mut();
            for entry in &listing.entries {
                if matches!(entry.kind, EntryKind::Directory | EntryKind::File)
                    && (show_hidden || !entry.hidden)
                {
                    selected.insert(entry.token.clone(), entry.clone());
                }
            }
        }
        self.update_selection_status();
        self.render_listing();
    }

    fn path_tokens(&self) -> Vec<String> {
        self.location
            .borrow()
            .iter()
            .map(|part| part.token.clone())
            .collect()
    }

    fn navigate_to(self: &Rc<Self>, target: Vec<BrowserLocation>) {
        let current = self.location.borrow().clone();
        if current == target {
            return;
        }
        self.back_history.borrow_mut().push(self.history_entry());
        self.forward_history.borrow_mut().clear();
        *self.location.borrow_mut() = target;
        self.reveal_entry.borrow_mut().take();
        self.reveal_focus_pending.set(false);
        *self.listing.borrow_mut() = None;
        self.search.set_text("");
        self.refresh();
    }

    fn navigate_to_revealing(self: &Rc<Self>, target: Vec<BrowserLocation>, entry: BrowserEntry) {
        let current = self.location.borrow().clone();
        let was_searching = self.search_active();
        if current != target || was_searching {
            self.back_history.borrow_mut().push(self.history_entry());
            self.forward_history.borrow_mut().clear();
        }
        *self.location.borrow_mut() = target;
        *self.reveal_entry.borrow_mut() = Some(entry);
        self.reveal_focus_pending.set(true);
        *self.listing.borrow_mut() = None;
        self.search.set_text("");
        self.refresh();
    }

    fn history_entry(&self) -> HistoryEntry {
        HistoryEntry {
            location: self.location.borrow().clone(),
            search: self.search.text().to_string(),
        }
    }

    fn restore_history_entry(self: &Rc<Self>, entry: HistoryEntry) {
        *self.location.borrow_mut() = entry.location;
        self.reveal_entry.borrow_mut().take();
        self.reveal_focus_pending.set(false);
        *self.listing.borrow_mut() = None;
        self.search.set_text(&entry.search);
        self.refresh();
    }

    fn navigate_back(self: &Rc<Self>) {
        let Some(target) = self.back_history.borrow_mut().pop() else {
            return;
        };
        self.forward_history.borrow_mut().push(self.history_entry());
        self.restore_history_entry(target);
    }

    fn navigate_forward(self: &Rc<Self>) {
        let Some(target) = self.forward_history.borrow_mut().pop() else {
            return;
        };
        self.back_history.borrow_mut().push(self.history_entry());
        self.restore_history_entry(target);
    }

    fn sort_mode(&self) -> &'static str {
        match self.sort.selected() {
            1 => "modified",
            2 => "size",
            _ => "name",
        }
    }

    fn refresh(self: &Rc<Self>) {
        self.reload(false);
    }

    fn refresh_preserving_selection(self: &Rc<Self>) {
        self.reload(true);
    }

    fn reload(self: &Rc<Self>, preserve_selection: bool) {
        if !preserve_selection {
            self.selected.borrow_mut().clear();
        }
        self.update_selection_status();
        self.up.set_sensitive(!self.location.borrow().is_empty());
        self.back
            .set_sensitive(!self.back_history.borrow().is_empty());
        self.forward
            .set_sensitive(!self.forward_history.borrow().is_empty());
        self.sync_place_selection();
        self.rebuild_breadcrumbs();
        let path = self.path_tokens();
        match client::list_snapshot_directory_session(
            &self.session_id,
            &path,
            0,
            1_000,
            self.sort_mode(),
            self.descending.is_active(),
        ) {
            Ok(mut listing) => {
                if let Some(entry) = self.reveal_entry.borrow().as_ref() {
                    ensure_entry_visible(
                        &mut listing.entries,
                        entry,
                        self.sort_mode(),
                        self.descending.is_active(),
                    );
                }
                *self.listing.borrow_mut() = Some(listing);
                if self.search_active() {
                    self.schedule_search();
                } else {
                    self.render_listing();
                }
            }
            Err(error) => {
                self.reveal_entry.borrow_mut().take();
                self.reveal_focus_pending.set(false);
                *self.listing.borrow_mut() = None;
                self.clear_results();
                let status = adw::StatusPage::builder()
                    .icon_name("dialog-error-symbolic")
                    .title(i18n("Could Not Open Folder"))
                    .description(error.to_string())
                    .build();
                self.append_result(&status);
            }
        }
    }

    fn sync_place_selection(&self) {
        let current = self.location.borrow();
        let selected = self
            .quick_locations
            .iter()
            .position(|place| place.location == *current)
            .and_then(|index| self.places.row_at_index(index as i32));
        self.places.select_row(selected.as_ref());
    }

    fn rebuild_breadcrumbs(self: &Rc<Self>) {
        while let Some(child) = self.breadcrumbs.first_child() {
            self.breadcrumbs.remove(&child);
        }
        let root = gtk::Button::builder()
            .icon_name("drive-harddisk-symbolic")
            .tooltip_text(i18n("Snapshot Root"))
            .css_classes(["flat"])
            .build();
        let browser = self.clone();
        root.connect_clicked(move |_| {
            browser.navigate_to(Vec::new());
        });
        self.breadcrumbs.append(&root);
        let locations = self.location.borrow().clone();
        for (index, location) in locations.into_iter().enumerate() {
            self.breadcrumbs.append(
                &gtk::Image::builder()
                    .icon_name("go-next-symbolic")
                    .css_classes(["dim-label"])
                    .build(),
            );
            let button = gtk::Button::builder()
                .label(&location.display_name)
                .css_classes(["flat"])
                .build();
            let browser = self.clone();
            button.connect_clicked(move |_| {
                let mut target = browser.location.borrow().clone();
                target.truncate(index + 1);
                browser.navigate_to(target);
            });
            self.breadcrumbs.append(&button);
        }
    }

    fn clear_results(&self) {
        while let Some(child) = self.list.first_child() {
            self.list.remove(&child);
        }
        while let Some(child) = self.grid.first_child() {
            self.grid.remove(&child);
        }
    }

    fn clear_search_results(&self) {
        while let Some(child) = self.search_list.first_child() {
            self.search_list.remove(&child);
        }
    }

    fn append_result(&self, child: &impl IsA<gtk::Widget>) {
        if self.grid_mode.is_active() {
            self.grid.insert(child, -1);
        } else {
            self.list.append(child);
        }
    }

    fn render_listing(self: &Rc<Self>) {
        let thumbnail_generation = self.begin_thumbnail_render();
        self.results
            .set_visible_child_name(if self.grid_mode.is_active() {
                "grid"
            } else {
                "list"
            });
        self.action_bar.set_visible(true);
        self.clear_results();
        let show_hidden = self.hidden.is_active();
        let listing = self.listing.borrow().clone();
        let Some(listing) = listing else {
            return;
        };
        let mut visible = 0usize;
        for entry in listing.entries {
            if entry.hidden && !show_hidden {
                continue;
            }
            visible += 1;
            self.add_entry(entry);
        }
        if let Some(offset) = listing.next_offset {
            let load_more = gtk::Button::builder()
                .label(i18n("Load More"))
                .tooltip_text(format!(
                    "{} / {}",
                    offset.min(listing.total_entries),
                    listing.total_entries
                ))
                .halign(gtk::Align::Center)
                .margin_top(12)
                .margin_bottom(12)
                .build();
            let browser = self.clone();
            load_more.connect_clicked(move |button| {
                button.set_sensitive(false);
                browser.load_next_page(offset);
            });
            self.append_result(&load_more);
        }
        if visible == 0 {
            let status = adw::StatusPage::builder()
                .icon_name("folder-symbolic")
                .title(i18n("This Folder Is Empty"))
                .description(i18n("There are no files to display."))
                .build();
            self.append_result(&status);
        } else if listing.truncated {
            self.overlay.add_toast(adw::Toast::new(&i18n(
                "This folder reached the 100,000-item safety limit; some items cannot be shown.",
            )));
        }
        self.start_thumbnail_load(thumbnail_generation);
    }

    fn search_active(&self) -> bool {
        !self.search.text().trim().is_empty()
    }

    fn cancel_search(&self) {
        if let Some(cancelled) = self.search_cancel.borrow_mut().take() {
            cancelled.store(true, Ordering::Release);
        }
    }

    fn schedule_search(self: &Rc<Self>) {
        self.cancel_search();
        let generation = self.search_generation.get().wrapping_add(1);
        self.search_generation.set(generation);
        self.search_report.borrow_mut().take();
        self.search_spinner.stop();
        self.search_spinner.set_visible(false);

        let query = self.search.text().trim().to_string();
        if query.is_empty() {
            self.render_listing();
            return;
        }

        self.begin_thumbnail_render();
        self.results.set_visible_child_name("search");
        self.action_bar.set_visible(false);
        self.clear_search_results();
        if query.chars().count() < 2 {
            self.append_search_status(
                "system-search-symbolic",
                &i18n("Type at Least 2 Characters"),
                &i18n("Enter a longer name to search this snapshot."),
            );
            return;
        }
        if query.chars().count() > 128 || query.chars().any(char::is_control) {
            self.append_search_status(
                "dialog-warning-symbolic",
                &i18n("Search Is Too Long"),
                &i18n("Use a search of no more than 128 characters."),
            );
            return;
        }

        self.search_spinner.set_visible(true);
        self.search_spinner.start();
        self.append_search_status(
            "system-search-symbolic",
            &i18n("Searching Snapshot…"),
            &i18n("Searching this location and all folders below it."),
        );
        let browser = self.clone();
        glib::timeout_add_local_once(Duration::from_millis(300), move || {
            if browser.search_generation.get() == generation {
                browser.start_recursive_search(generation, query);
            }
        });
    }

    fn start_recursive_search(self: &Rc<Self>, generation: u64, query: String) {
        let cancelled = Arc::new(AtomicBool::new(false));
        *self.search_cancel.borrow_mut() = Some(cancelled.clone());
        let session_id = self.session_id.clone();
        let root_tokens = self.path_tokens();
        let root_names = self
            .location
            .borrow()
            .iter()
            .map(|location| location.display_name.clone())
            .collect::<Vec<_>>();
        let show_hidden = self.hidden.is_active();
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let result = snapshot_search::search_snapshot(
                &root_tokens,
                &root_names,
                &query,
                show_hidden,
                &cancelled,
                |path| {
                    client::list_snapshot_directory_session_all(&session_id, path)
                        .map_err(|error| error.to_string())
                },
            );
            let _ = sender.send(result);
        });

        let browser = self.clone();
        glib::timeout_add_local(Duration::from_millis(80), move || {
            if browser.search_generation.get() != generation {
                return glib::ControlFlow::Break;
            }
            match receiver.try_recv() {
                Ok(Ok(report)) => {
                    browser.search_cancel.borrow_mut().take();
                    browser.search_spinner.stop();
                    browser.search_spinner.set_visible(false);
                    *browser.search_report.borrow_mut() = Some(report);
                    browser.render_search_report();
                    glib::ControlFlow::Break
                }
                Ok(Err(SnapshotSearchError::Cancelled)) => glib::ControlFlow::Break,
                Ok(Err(SnapshotSearchError::InvalidQuery)) => {
                    browser.finish_search_error(&i18n("Enter a valid search."));
                    glib::ControlFlow::Break
                }
                Ok(Err(SnapshotSearchError::Failed(error))) => {
                    browser.finish_search_error(&error);
                    glib::ControlFlow::Break
                }
                Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(mpsc::TryRecvError::Disconnected) => {
                    browser.finish_search_error(&i18n("The search process stopped unexpectedly."));
                    glib::ControlFlow::Break
                }
            }
        });
    }

    fn finish_search_error(&self, description: &str) {
        self.search_cancel.borrow_mut().take();
        self.search_spinner.stop();
        self.search_spinner.set_visible(false);
        self.clear_search_results();
        self.append_search_status(
            "dialog-error-symbolic",
            &i18n("Could Not Search Snapshot"),
            description,
        );
    }

    fn append_search_status(&self, icon: &str, title: &str, description: &str) {
        self.search_list.append(
            &adw::StatusPage::builder()
                .icon_name(icon)
                .title(title)
                .description(description)
                .build(),
        );
    }

    fn render_search_report(self: &Rc<Self>) {
        let Some(report) = self.search_report.borrow().clone() else {
            return;
        };
        self.results.set_visible_child_name("search");
        self.action_bar.set_visible(false);
        self.clear_search_results();
        let mut hits = report.hits;
        sort_search_hits(&mut hits, self.sort_mode(), self.descending.is_active());
        if hits.is_empty() {
            self.append_search_status(
                "system-search-symbolic",
                &i18n("No Matching Files"),
                &i18n("No files below this location match your search."),
            );
        } else {
            for hit in hits {
                self.add_search_hit(hit);
            }
        }
        if !report.complete {
            let warning = adw::ActionRow::builder()
                .title(i18n("Search Results May Be Incomplete"))
                .subtitle(i18n(
                    "The search stopped at its 100,000-item or 1,000-result safety limit.",
                ))
                .build();
            warning.add_prefix(&gtk::Image::from_icon_name("dialog-warning-symbolic"));
            self.search_list.append(&warning);
        }
    }

    fn add_search_hit(self: &Rc<Self>, hit: SnapshotSearchHit) {
        let base_location = hit
            .parent_tokens
            .iter()
            .cloned()
            .zip(hit.parent_names.iter().cloned())
            .map(|(token, display_name)| BrowserLocation {
                token,
                display_name,
            })
            .collect::<Vec<_>>();
        let entry = hit.entry;
        let row = adw::ActionRow::builder()
            .title(&entry.display_name)
            .subtitle(format!(
                "{} · {}",
                entry_subtitle(&entry),
                snapshot_display_path(&base_location, &entry.display_name)
            ))
            .activatable(entry.kind == EntryKind::Directory)
            .build();
        row.add_prefix(
            &gtk::Image::builder()
                .icon_name(entry_icon(&entry))
                .pixel_size(32)
                .build(),
        );
        let properties = gtk::Button::builder()
            .icon_name("document-properties-symbolic")
            .tooltip_text(i18n("Properties"))
            .css_classes(["flat"])
            .valign(gtk::Align::Center)
            .build();
        let browser = self.clone();
        let properties_entry = entry.clone();
        let properties_base = base_location.clone();
        properties.connect_clicked(move |_| {
            browser.present_properties_at(&properties_entry, properties_base.clone());
        });
        row.add_suffix(&properties);

        if entry.kind != EntryKind::Directory {
            let containing = gtk::Button::builder()
                .icon_name("folder-open-symbolic")
                .tooltip_text(i18n("Open Containing Folder"))
                .css_classes(["flat"])
                .valign(gtk::Align::Center)
                .build();
            let browser = self.clone();
            let containing_entry = entry.clone();
            let containing_location = base_location.clone();
            containing.connect_clicked(move |_| {
                browser
                    .navigate_to_revealing(containing_location.clone(), containing_entry.clone());
            });
            row.add_suffix(&containing);
        }

        let mut entry_path = hit.parent_tokens;
        entry_path.push(entry.token.clone());
        match entry.kind {
            EntryKind::Directory => {
                let copy = gtk::Button::builder()
                    .icon_name("document-save-symbolic")
                    .tooltip_text(i18n("Copy Out…"))
                    .css_classes(["flat"])
                    .valign(gtk::Align::Center)
                    .build();
                let browser = self.clone();
                let copy_entry = entry.clone();
                let copy_base = entry_path[..entry_path.len() - 1].to_vec();
                copy.connect_clicked(move |_| {
                    browser.choose_export_folder_for(vec![copy_entry.clone()], copy_base.clone());
                });
                row.add_suffix(&copy);
                row.add_suffix(&gtk::Image::from_icon_name("go-next-symbolic"));
                let browser = self.clone();
                let mut target = base_location;
                target.push(BrowserLocation {
                    token: entry.token,
                    display_name: entry.display_name,
                });
                row.connect_activated(move |_| browser.navigate_to(target.clone()));
            }
            EntryKind::File => {
                let preview = gtk::Button::builder()
                    .icon_name("view-reveal-symbolic")
                    .tooltip_text(i18n("Preview"))
                    .css_classes(["flat"])
                    .valign(gtk::Align::Center)
                    .build();
                let browser = self.clone();
                let preview_path = entry_path.clone();
                let preview_name = entry.display_name.clone();
                preview.connect_clicked(move |_| {
                    browser.preview_file_at(preview_path.clone(), &preview_name);
                });
                row.add_suffix(&preview);
                let copy = gtk::Button::builder()
                    .icon_name("document-save-symbolic")
                    .tooltip_text(i18n("Copy Out…"))
                    .css_classes(["flat"])
                    .valign(gtk::Align::Center)
                    .build();
                let browser = self.clone();
                let copy_path = entry_path;
                let copy_name = entry.display_name;
                copy.connect_clicked(move |_| browser.copy_one_at(copy_path.clone(), &copy_name));
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
        self.search_list.append(&row);
    }

    fn begin_thumbnail_render(&self) -> u64 {
        if let Some(cancelled) = self.thumbnail_cancel.borrow_mut().take() {
            cancelled.store(true, Ordering::Release);
        }
        self.thumbnail_requests.borrow_mut().clear();
        self.thumbnail_targets.borrow_mut().clear();
        let generation = self.thumbnail_generation.get().wrapping_add(1);
        self.thumbnail_generation.set(generation);
        generation
    }

    fn register_thumbnail(&self, entry: &BrowserEntry, picture: &gtk::Picture) {
        if !is_thumbnail_candidate(entry) {
            return;
        }
        let mut path = self.path_tokens();
        path.push(entry.token.clone());
        let key = thumbnail_key(&path);
        if let Some(texture) = self.thumbnail_cache.borrow_mut().get(&key) {
            picture.set_paintable(Some(&texture));
            picture.set_visible(true);
            return;
        }
        self.thumbnail_targets
            .borrow_mut()
            .entry(key.clone())
            .or_default()
            .push(picture.clone());
        self.thumbnail_requests
            .borrow_mut()
            .push(ThumbnailRequest { key, path });
    }

    fn start_thumbnail_load(self: &Rc<Self>, generation: u64) {
        const MAX_THUMBNAILS_PER_RENDER: usize = 32;
        let requests = self
            .thumbnail_requests
            .borrow()
            .iter()
            .take(MAX_THUMBNAILS_PER_RENDER)
            .cloned()
            .collect::<Vec<_>>();
        if requests.is_empty() {
            return;
        }
        let cancelled = Arc::new(AtomicBool::new(false));
        *self.thumbnail_cancel.borrow_mut() = Some(cancelled.clone());
        let session_id = self.session_id.clone();
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            for request in requests {
                if cancelled.load(Ordering::Acquire) {
                    break;
                }
                if let Some(thumbnail) = load_thumbnail(&session_id, request, &cancelled) {
                    let _ = sender.send(ThumbnailEvent::Ready(thumbnail));
                }
            }
            let _ = sender.send(ThumbnailEvent::Finished);
        });
        let browser = self.clone();
        glib::timeout_add_local(Duration::from_millis(40), move || loop {
            if browser.thumbnail_generation.get() != generation {
                return glib::ControlFlow::Break;
            }
            match receiver.try_recv() {
                Ok(ThumbnailEvent::Ready(thumbnail)) => {
                    browser.install_thumbnail(thumbnail);
                }
                Ok(ThumbnailEvent::Finished) => {
                    browser.thumbnail_cancel.borrow_mut().take();
                    return glib::ControlFlow::Break;
                }
                Err(mpsc::TryRecvError::Empty) => return glib::ControlFlow::Continue,
                Err(mpsc::TryRecvError::Disconnected) => {
                    browser.thumbnail_cancel.borrow_mut().take();
                    return glib::ControlFlow::Break;
                }
            }
        });
    }

    fn install_thumbnail(&self, thumbnail: ThumbnailPixels) {
        let format = if thumbnail.has_alpha {
            gtk::gdk::MemoryFormat::R8g8b8a8
        } else {
            gtk::gdk::MemoryFormat::R8g8b8
        };
        let bytes = glib::Bytes::from_owned(thumbnail.pixels);
        let texture = gtk::gdk::MemoryTexture::new(
            thumbnail.width,
            thumbnail.height,
            format,
            &bytes,
            thumbnail.stride,
        )
        .upcast::<gtk::gdk::Texture>();
        if let Some(targets) = self.thumbnail_targets.borrow().get(&thumbnail.key) {
            for picture in targets {
                picture.set_paintable(Some(&texture));
                picture.set_visible(true);
            }
        }
        self.thumbnail_cache
            .borrow_mut()
            .insert(thumbnail.key, texture);
    }

    fn load_next_page(self: &Rc<Self>, offset: usize) {
        let path = self.path_tokens();
        match client::list_snapshot_directory_session(
            &self.session_id,
            &path,
            offset,
            1_000,
            self.sort_mode(),
            self.descending.is_active(),
        ) {
            Ok(page) => {
                if let Some(listing) = self.listing.borrow_mut().as_mut() {
                    let known = listing
                        .entries
                        .iter()
                        .map(|entry| entry.token.clone())
                        .collect::<HashSet<_>>();
                    listing.entries.extend(
                        page.entries
                            .into_iter()
                            .filter(|entry| !known.contains(&entry.token)),
                    );
                    sort_browser_entries(
                        &mut listing.entries,
                        self.sort_mode(),
                        self.descending.is_active(),
                    );
                    listing.total_entries = page.total_entries;
                    listing.next_offset = page.next_offset;
                    listing.truncated |= page.truncated;
                }
                self.render_listing();
            }
            Err(error) => self.overlay.add_toast(adw::Toast::new(&error.to_string())),
        }
    }

    fn entry_visual(&self, entry: &BrowserEntry, size: i32) -> gtk::Overlay {
        let icon = gtk::Image::builder()
            .icon_name(entry_icon(entry))
            .pixel_size(size)
            .build();
        let thumbnail = gtk::Picture::builder()
            .can_shrink(true)
            .content_fit(gtk::ContentFit::Cover)
            .width_request(size)
            .height_request(size)
            .visible(false)
            .build();
        let visual = gtk::Overlay::new();
        visual.set_child(Some(&icon));
        visual.add_overlay(&thumbnail);
        visual.set_size_request(size, size);
        self.register_thumbnail(entry, &thumbnail);
        visual
    }

    fn add_entry(self: &Rc<Self>, entry: BrowserEntry) {
        if self.grid_mode.is_active() {
            self.add_grid_entry(entry);
            return;
        }
        let selectable = matches!(entry.kind, EntryKind::Directory | EntryKind::File);
        let row = adw::ActionRow::builder()
            .title(&entry.display_name)
            .subtitle(entry_subtitle(&entry))
            .activatable(entry.kind == EntryKind::Directory)
            .build();
        self.focus_revealed_widget(&entry, &row);
        let check = gtk::CheckButton::builder()
            .tooltip_text(i18n("Select for copying"))
            .sensitive(selectable)
            .active(self.selected.borrow().contains_key(&entry.token))
            .valign(gtk::Align::Center)
            .build();
        let browser = self.clone();
        let selected_entry = entry.clone();
        check.connect_toggled(move |check| {
            if check.is_active() {
                browser
                    .selected
                    .borrow_mut()
                    .insert(selected_entry.token.clone(), selected_entry.clone());
            } else {
                browser.selected.borrow_mut().remove(&selected_entry.token);
            }
            browser.update_selection_status();
        });
        row.add_prefix(&check);
        row.add_prefix(&self.entry_visual(&entry, 32));
        let properties = gtk::Button::builder()
            .icon_name("document-properties-symbolic")
            .tooltip_text(i18n("Properties"))
            .valign(gtk::Align::Center)
            .css_classes(["flat"])
            .build();
        let browser = self.clone();
        let properties_entry = entry.clone();
        properties.connect_clicked(move |_| browser.present_properties(&properties_entry));
        row.add_suffix(&properties);
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
                    let mut target = browser.location.borrow().clone();
                    target.push(BrowserLocation {
                        token: token.clone(),
                        display_name: display_name.clone(),
                    });
                    browser.navigate_to(target);
                });
            }
            EntryKind::File => {
                let preview = gtk::Button::builder()
                    .icon_name("view-reveal-symbolic")
                    .tooltip_text(i18n("Preview"))
                    .valign(gtk::Align::Center)
                    .css_classes(["flat"])
                    .build();
                let browser = self.clone();
                let preview_token = entry.token.clone();
                let preview_name = entry.display_name.clone();
                preview.connect_clicked(move |_| {
                    browser.preview_file(&preview_token, &preview_name);
                });
                row.add_suffix(&preview);
                let copy = gtk::Button::builder()
                    .icon_name("document-save-symbolic")
                    .tooltip_text(i18n("Copy Out…"))
                    .valign(gtk::Align::Center)
                    .css_classes(["flat"])
                    .build();
                let browser = self.clone();
                let token = entry.token;
                let display_name = entry.display_name;
                copy.connect_clicked(move |_| browser.copy_one(&token, &display_name));
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

    fn add_grid_entry(self: &Rc<Self>, entry: BrowserEntry) {
        let selectable = matches!(entry.kind, EntryKind::Directory | EntryKind::File);
        let details = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(6)
            .margin_top(12)
            .margin_bottom(10)
            .margin_start(8)
            .margin_end(8)
            .build();
        details.append(&self.entry_visual(&entry, 72));
        details.append(
            &gtk::Label::builder()
                .label(&entry.display_name)
                .ellipsize(gtk::pango::EllipsizeMode::Middle)
                .max_width_chars(18)
                .tooltip_text(&entry.display_name)
                .build(),
        );
        details.append(
            &gtk::Label::builder()
                .label(entry_subtitle(&entry))
                .css_classes(["caption", "dim-label"])
                .build(),
        );
        let open = gtk::Button::builder()
            .child(&details)
            .css_classes(["flat"])
            .sensitive(entry.kind != EntryKind::Special)
            .build();
        self.focus_revealed_widget(&entry, &open);
        let browser = self.clone();
        let token = entry.token.clone();
        let display_name = entry.display_name.clone();
        match entry.kind {
            EntryKind::Directory => {
                open.connect_clicked(move |_| {
                    let mut target = browser.location.borrow().clone();
                    target.push(BrowserLocation {
                        token: token.clone(),
                        display_name: display_name.clone(),
                    });
                    browser.navigate_to(target);
                });
            }
            EntryKind::File => {
                open.connect_clicked(move |_| {
                    browser.preview_file(&token, &display_name);
                });
            }
            EntryKind::Symlink | EntryKind::Special => {
                open.set_tooltip_text(Some(&i18n("Not opened for safety")));
            }
        }
        let check = gtk::CheckButton::builder()
            .tooltip_text(i18n("Select for copying"))
            .sensitive(selectable)
            .active(self.selected.borrow().contains_key(&entry.token))
            .halign(gtk::Align::Start)
            .valign(gtk::Align::Start)
            .margin_top(6)
            .margin_start(6)
            .build();
        let properties = gtk::Button::builder()
            .icon_name("document-properties-symbolic")
            .tooltip_text(i18n("Properties"))
            .halign(gtk::Align::End)
            .valign(gtk::Align::Start)
            .margin_top(4)
            .margin_end(4)
            .css_classes(["circular", "flat"])
            .build();
        let browser = self.clone();
        let properties_entry = entry.clone();
        properties.connect_clicked(move |_| browser.present_properties(&properties_entry));
        let browser = self.clone();
        let selected_entry = entry;
        check.connect_toggled(move |check| {
            if check.is_active() {
                browser
                    .selected
                    .borrow_mut()
                    .insert(selected_entry.token.clone(), selected_entry.clone());
            } else {
                browser.selected.borrow_mut().remove(&selected_entry.token);
            }
            browser.update_selection_status();
        });
        let overlay = gtk::Overlay::new();
        overlay.set_child(Some(&open));
        overlay.add_overlay(&check);
        overlay.add_overlay(&properties);
        overlay.add_css_class("card");
        self.grid.insert(&overlay, -1);
    }

    fn focus_revealed_widget(&self, entry: &BrowserEntry, widget: &impl IsA<gtk::Widget>) {
        let matches = self
            .reveal_entry
            .borrow()
            .as_ref()
            .is_some_and(|target| target.token == entry.token)
            && self.reveal_focus_pending.get();
        if !matches {
            return;
        }
        self.reveal_focus_pending.set(false);
        let widget = widget.clone().upcast::<gtk::Widget>();
        glib::idle_add_local_once(move || {
            widget.grab_focus();
        });
        self.overlay.add_toast(adw::Toast::new(&format!(
            "{}: {}",
            i18n("Showing in Containing Folder"),
            entry.display_name
        )));
    }

    fn update_selection_status(&self) {
        let count = self.selected.borrow().len();
        self.copy_selected
            .set_sensitive(count > 0 && self.active_cancel.borrow().is_none());
        self.selection_label.set_label(&match count {
            0 => i18n("No items selected"),
            1 => i18n("1 item selected"),
            _ => format!("{count} {}", i18n("items selected")),
        });
    }

    fn present_properties(self: &Rc<Self>, entry: &BrowserEntry) {
        self.present_properties_at(entry, self.location.borrow().clone());
    }

    fn present_properties_at(
        self: &Rc<Self>,
        entry: &BrowserEntry,
        base_location: Vec<BrowserLocation>,
    ) {
        let base_path = base_location
            .iter()
            .map(|location| location.token.clone())
            .collect::<Vec<_>>();
        let mut entry_path = base_path.clone();
        entry_path.push(entry.token.clone());
        let heading = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .spacing(8)
            .margin_top(20)
            .margin_bottom(18)
            .margin_start(24)
            .margin_end(24)
            .build();
        heading.append(
            &gtk::Image::builder()
                .icon_name(entry_icon(entry))
                .pixel_size(64)
                .build(),
        );
        heading.append(
            &gtk::Label::builder()
                .label(&entry.display_name)
                .selectable(true)
                .wrap(true)
                .justify(gtk::Justification::Center)
                .css_classes(["title-2"])
                .build(),
        );
        heading.append(
            &gtk::Label::builder()
                .label(i18n("This item is inside a read-only snapshot."))
                .wrap(true)
                .justify(gtk::Justification::Center)
                .css_classes(["dim-label"])
                .build(),
        );

        let details = adw::PreferencesGroup::new();
        details.add(&property_row(&i18n("Type"), &entry_type_label(entry.kind)));
        details.add(&property_row(
            &i18n("Location"),
            &snapshot_display_path(&base_location, &entry.display_name),
        ));
        let size = property_row(
            &i18n("Size"),
            &if entry.kind == EntryKind::Directory {
                i18n("Calculating…")
            } else {
                entry_property_size(entry)
            },
        );
        details.add(&size);
        let contents = (entry.kind == EntryKind::Directory)
            .then(|| property_row(&i18n("Contents"), &i18n("Calculating…")));
        if let Some(contents) = &contents {
            details.add(contents);
        }
        details.add(&property_row(
            &i18n("Modified"),
            &format_modified(entry.modified_unix),
        ));
        details.add(&property_row(
            &i18n("Permissions"),
            &format_permissions(entry.mode),
        ));
        details.add(&property_row(
            &i18n("Visibility"),
            &if entry.hidden {
                i18n("Hidden")
            } else {
                i18n("Visible")
            },
        ));
        details.add(&property_row(
            &i18n("Copying"),
            &entry_copy_status(entry.kind),
        ));

        let content = gtk::Box::new(gtk::Orientation::Vertical, 0);
        content.append(&heading);
        content.append(&details);
        details.set_margin_bottom(24);
        details.set_margin_start(18);
        details.set_margin_end(18);
        let scrolled = gtk::ScrolledWindow::builder()
            .hscrollbar_policy(gtk::PolicyType::Never)
            .vscrollbar_policy(gtk::PolicyType::Automatic)
            .child(&content)
            .build();
        let toolbar = adw::ToolbarView::builder().content(&scrolled).build();
        let header = adw::HeaderBar::new();
        let copy = gtk::Button::builder()
            .label(i18n("Copy Out…"))
            .icon_name("document-save-symbolic")
            .css_classes(["suggested-action"])
            .sensitive(
                matches!(entry.kind, EntryKind::Directory | EntryKind::File)
                    && self.active_cancel.borrow().is_none(),
            )
            .build();
        header.pack_end(&copy);
        toolbar.add_top_bar(&header);
        let dialog = adw::Dialog::builder()
            .title(i18n("Properties"))
            .content_width(520)
            .content_height(620)
            .child(&toolbar)
            .build();
        let browser = self.clone();
        let copy_entry = entry.clone();
        let copy_entry_path = entry_path.clone();
        let copy_dialog = dialog.clone();
        copy.connect_clicked(move |_| {
            copy_dialog.close();
            if copy_entry.kind == EntryKind::File {
                browser.copy_one_at(copy_entry_path.clone(), &copy_entry.display_name);
            } else if copy_entry.kind == EntryKind::Directory {
                browser.choose_export_folder_for(vec![copy_entry.clone()], base_path.clone());
            }
        });
        dialog.present(Some(&self.window));
        if let Some(contents) = contents {
            self.start_directory_statistics(&dialog, &size, &contents, entry_path);
        }
    }

    fn start_directory_statistics(
        &self,
        dialog: &adw::Dialog,
        size: &adw::ActionRow,
        contents: &adw::ActionRow,
        path: Vec<String>,
    ) {
        let cancelled = Arc::new(AtomicBool::new(false));
        let cancel_on_close = cancelled.clone();
        dialog.connect_closed(move |_| cancel_on_close.store(true, Ordering::Release));
        let session_id = self.session_id.clone();
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let result = copy_out::calculate_tree_statistics(&path, &cancelled, |path| {
                client::list_snapshot_directory_session_all(&session_id, path)
                    .map_err(|error| error.to_string())
            });
            let _ = sender.send(result);
        });
        let weak_dialog = dialog.downgrade();
        let size = size.clone();
        let contents = contents.clone();
        glib::timeout_add_local(Duration::from_millis(80), move || {
            if weak_dialog.upgrade().is_none() {
                return glib::ControlFlow::Break;
            }
            match receiver.try_recv() {
                Ok(Ok(statistics)) => {
                    size.set_subtitle(&format_size(statistics.bytes));
                    contents.set_subtitle(&format_tree_contents(statistics));
                    glib::ControlFlow::Break
                }
                Ok(Err(TreeStatisticsError::Cancelled)) => glib::ControlFlow::Break,
                Ok(Err(TreeStatisticsError::Failed(_))) => {
                    size.set_subtitle(&i18n("Could not calculate folder size"));
                    contents.set_subtitle(&i18n("Could not calculate folder contents"));
                    glib::ControlFlow::Break
                }
                Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(mpsc::TryRecvError::Disconnected) => glib::ControlFlow::Break,
            }
        });
    }

    fn preview_file(self: &Rc<Self>, token: &str, display_name: &str) {
        let mut path = self.path_tokens();
        path.push(token.to_string());
        self.preview_file_at(path, display_name);
    }

    fn preview_file_at(self: &Rc<Self>, path: Vec<String>, display_name: &str) {
        const TEXT_LIMIT: u64 = 1024 * 1024;
        const IMAGE_LIMIT: u64 = 20 * 1024 * 1024;
        let (file, metadata) = match client::open_snapshot_file_session(&self.session_id, &path) {
            Ok(opened) => opened,
            Err(error) => {
                self.overlay.add_toast(adw::Toast::new(&error.to_string()));
                return;
            }
        };
        if metadata.size > IMAGE_LIMIT {
            self.overlay.add_toast(adw::Toast::new(&i18n(
                "This file is too large to preview. You can still copy it out.",
            )));
            return;
        }
        let mut data = Vec::with_capacity(metadata.size as usize);
        if let Err(error) = file.take(IMAGE_LIMIT + 1).read_to_end(&mut data) {
            self.overlay.add_toast(adw::Toast::new(&format!(
                "{}: {error}",
                i18n("Could not read the preview")
            )));
            return;
        }
        let (content_type, _) = gio::content_type_guess(Some(display_name), Some(&data[..]));
        let mime = gio::content_type_get_mime_type(&content_type).unwrap_or_default();
        if mime.starts_with("image/") {
            match gtk::gdk::Texture::from_bytes(&glib::Bytes::from_owned(data)) {
                Ok(texture) => {
                    let picture = gtk::Picture::for_paintable(&texture);
                    picture.set_can_shrink(true);
                    picture.set_content_fit(gtk::ContentFit::Contain);
                    self.present_preview(display_name, &picture);
                }
                Err(error) => self.overlay.add_toast(adw::Toast::new(&format!(
                    "{}: {error}",
                    i18n("Could not decode the image preview")
                ))),
            }
        } else if mime.starts_with("text/")
            || matches!(
                mime.as_str(),
                "application/json"
                    | "application/xml"
                    | "application/javascript"
                    | "application/x-shellscript"
            )
        {
            if metadata.size > TEXT_LIMIT {
                self.overlay.add_toast(adw::Toast::new(&i18n(
                    "Text previews are limited to 1 MB. You can still copy this file out.",
                )));
                return;
            }
            let buffer = gtk::TextBuffer::builder()
                .text(String::from_utf8_lossy(&data))
                .build();
            let text = gtk::TextView::builder()
                .buffer(&buffer)
                .editable(false)
                .cursor_visible(false)
                .monospace(true)
                .wrap_mode(gtk::WrapMode::None)
                .left_margin(12)
                .right_margin(12)
                .top_margin(12)
                .bottom_margin(12)
                .build();
            self.present_preview(display_name, &text);
        } else {
            self.overlay.add_toast(adw::Toast::new(&i18n(
                "No safe preview is available for this file type.",
            )));
        }
    }

    fn present_preview(&self, title: &str, child: &impl IsA<gtk::Widget>) {
        let scrolled = gtk::ScrolledWindow::builder()
            .hscrollbar_policy(gtk::PolicyType::Automatic)
            .vscrollbar_policy(gtk::PolicyType::Automatic)
            .child(child)
            .build();
        let toolbar = adw::ToolbarView::builder().content(&scrolled).build();
        toolbar.add_top_bar(&adw::HeaderBar::new());
        let dialog = adw::Dialog::builder()
            .title(title)
            .content_width(760)
            .content_height(560)
            .child(&toolbar)
            .build();
        dialog.present(Some(&self.window));
    }

    fn choose_export_folder(self: &Rc<Self>) {
        let entries = self.selected.borrow().values().cloned().collect::<Vec<_>>();
        if entries.is_empty() {
            return;
        }
        self.choose_export_folder_for(entries, self.path_tokens());
    }

    fn choose_export_folder_for(
        self: &Rc<Self>,
        entries: Vec<BrowserEntry>,
        base_path: Vec<String>,
    ) {
        let dialog = gtk::FileDialog::builder()
            .title(i18n("Choose Where to Copy the Selected Items"))
            .accept_label(i18n("Copy Here"))
            .build();
        let browser = self.clone();
        dialog.select_folder(
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
                browser.start_export(entries, base_path, destination);
            },
        );
    }

    fn start_export(
        self: &Rc<Self>,
        entries: Vec<BrowserEntry>,
        base_path: Vec<String>,
        destination: std::path::PathBuf,
    ) {
        let selections = entries
            .into_iter()
            .map(|entry| {
                let mut snapshot_path = base_path.clone();
                snapshot_path.push(entry.token.clone());
                ExportSelection {
                    snapshot_path,
                    name_token: entry.token,
                    kind: entry.kind,
                    size: entry.size,
                }
            })
            .collect::<Vec<_>>();
        let policy = match self.conflict.selected() {
            1 => ConflictPolicy::Replace,
            2 => ConflictPolicy::Skip,
            _ => ConflictPolicy::KeepBoth,
        };
        let cancelled = Arc::new(AtomicBool::new(false));
        *self.active_cancel.borrow_mut() = Some(cancelled.clone());
        self.copy_selected.set_sensitive(false);
        self.conflict.set_sensitive(false);
        self.cancel.set_sensitive(true);
        self.progress.set_fraction(0.0);
        self.progress_label
            .set_label(&i18n("Scanning selected items…"));
        self.progress_revealer.set_reveal_child(true);

        let session_id = self.session_id.clone();
        let reveal_destination = destination.clone();
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let progress_sender = sender.clone();
            let result = copy_out::export_items(
                &selections,
                &destination,
                policy,
                &cancelled,
                |path| {
                    client::list_snapshot_directory_session_all(&session_id, path)
                        .map_err(|error| error.to_string())
                },
                |path| {
                    client::open_snapshot_file_session(&session_id, path)
                        .map_err(|error| error.to_string())
                },
                |progress| {
                    let _ = progress_sender.send(ExportEvent::Progress(progress));
                },
            );
            let _ = sender.send(ExportEvent::Finished(result));
        });
        let browser = self.clone();
        glib::timeout_add_local(Duration::from_millis(80), move || loop {
            match receiver.try_recv() {
                Ok(ExportEvent::Progress(progress)) => browser.show_progress(progress),
                Ok(ExportEvent::Finished(result)) => {
                    browser.finish_export(result, &reveal_destination);
                    return glib::ControlFlow::Break;
                }
                Err(mpsc::TryRecvError::Empty) => return glib::ControlFlow::Continue,
                Err(mpsc::TryRecvError::Disconnected) => {
                    browser.finish_export(
                        Err(ExportError::Failed(
                            "The copy worker stopped unexpectedly".into(),
                        )),
                        &reveal_destination,
                    );
                    return glib::ControlFlow::Break;
                }
            }
        });
    }

    fn show_progress(&self, progress: ExportProgress) {
        let fraction = if progress.total_bytes == 0 {
            if progress.total_files == 0 {
                0.0
            } else {
                progress.copied_files as f64 / progress.total_files as f64
            }
        } else {
            progress.copied_bytes as f64 / progress.total_bytes as f64
        };
        self.progress.set_fraction(fraction.clamp(0.0, 1.0));
        self.progress_label.set_label(&format!(
            "{} / {} · {} / {} {}",
            format_size(progress.copied_bytes),
            format_size(progress.total_bytes),
            progress.copied_files,
            progress.total_files,
            i18n("files")
        ));
    }

    fn finish_export(
        &self,
        result: Result<ExportReport, ExportError>,
        destination: &std::path::Path,
    ) {
        *self.active_cancel.borrow_mut() = None;
        self.conflict.set_sensitive(true);
        self.cancel.set_sensitive(true);
        self.progress_revealer.set_reveal_child(false);
        self.update_selection_status();
        match result {
            Ok(report) => self.add_reveal_toast(&export_completion_summary(report), destination),
            Err(ExportError::Cancelled) => self
                .overlay
                .add_toast(adw::Toast::new(&i18n("Copy cancelled"))),
            Err(error) => self.overlay.add_toast(adw::Toast::new(&format!(
                "{}: {error}",
                i18n("Could not copy the selected items")
            ))),
        }
    }

    fn add_reveal_toast(&self, title: &str, destination: &std::path::Path) {
        let toast = adw::Toast::builder()
            .title(title)
            .button_label(i18n("Show in Files"))
            .timeout(8)
            .build();
        let destination = destination.to_path_buf();
        let overlay = self.overlay.clone();
        toast.connect_button_clicked(move |_| {
            let uri = gio::File::for_path(&destination).uri();
            if let Err(error) =
                gio::AppInfo::launch_default_for_uri(&uri, None::<&gio::AppLaunchContext>)
            {
                overlay.add_toast(adw::Toast::new(&format!(
                    "{}: {error}",
                    i18n("Could not open the destination folder")
                )));
            }
        });
        self.overlay.add_toast(toast);
    }

    fn copy_one(self: &Rc<Self>, token: &str, display_name: &str) {
        let mut path = self.path_tokens();
        path.push(token.to_string());
        self.copy_one_at(path, display_name);
    }

    fn copy_one_at(self: &Rc<Self>, path: Vec<String>, display_name: &str) {
        let (source, metadata) = match client::open_snapshot_file_session(&self.session_id, &path) {
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
                        None => return,
                    },
                    Err(error) if error.matches(gtk::DialogError::Dismissed) => return,
                    Err(error) => {
                        browser
                            .overlay
                            .add_toast(adw::Toast::new(&error.to_string()));
                        return;
                    }
                };
                let reveal_destination = destination.parent().map(std::path::Path::to_path_buf);
                let (sender, receiver) = mpsc::channel();
                std::thread::spawn(move || {
                    let _ = sender.send(
                        copy_out::copy_file_atomic(source, &metadata, &destination)
                            .map_err(|error| error.to_string()),
                    );
                });
                let browser = browser.clone();
                glib::timeout_add_local(Duration::from_millis(100), move || {
                    match receiver.try_recv() {
                        Ok(Ok(bytes)) => {
                            let title = format!(
                                "{} ({})",
                                i18n("File copied successfully"),
                                format_size(bytes)
                            );
                            if let Some(destination) = &reveal_destination {
                                browser.add_reveal_toast(&title, destination);
                            } else {
                                browser.overlay.add_toast(adw::Toast::new(&title));
                            }
                            glib::ControlFlow::Break
                        }
                        Ok(Err(error)) => {
                            browser.overlay.add_toast(adw::Toast::new(&format!(
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

const MAX_USER_DIRS_BYTES: u64 = 64 * 1024;

fn discover_quick_locations(snapshot_kind: &str, session_id: &str) -> Vec<QuickLocation> {
    let mut places = vec![QuickLocation {
        label: i18n("Snapshot Root"),
        icon: "drive-harddisk-symbolic",
        location: Vec::new(),
    }];
    let root_directories =
        client::list_snapshot_directory_session(session_id, &[], 0, 1_000, "name", false)
            .ok()
            .map(|listing| {
                listing
                    .entries
                    .into_iter()
                    .filter(|entry| entry.kind == EntryKind::Directory)
                    .map(|entry| entry.token)
                    .collect::<HashSet<_>>()
            });
    if snapshot_kind == "system" {
        for (label, icon, components) in [
            (
                i18n("System Configuration"),
                "preferences-system-symbolic",
                vec!["etc"],
            ),
            (i18n("User Homes"), "user-home-symbolic", vec!["home"]),
            (
                i18n("System Logs"),
                "folder-documents-symbolic",
                vec!["var", "log"],
            ),
        ] {
            if !quick_location_root_exists(&root_directories, components[0]) {
                continue;
            }
            places.push(QuickLocation {
                label,
                icon,
                location: browser_location(&components),
            });
        }
        return places;
    }

    let configured = read_snapshot_user_dirs(session_id);
    for (key, label, icon, fallback) in [
        (
            "XDG_DESKTOP_DIR",
            i18n("Desktop"),
            "user-desktop-symbolic",
            "Desktop",
        ),
        (
            "XDG_DOCUMENTS_DIR",
            i18n("Documents"),
            "folder-documents-symbolic",
            "Documents",
        ),
        (
            "XDG_DOWNLOAD_DIR",
            i18n("Downloads"),
            "folder-download-symbolic",
            "Downloads",
        ),
        (
            "XDG_MUSIC_DIR",
            i18n("Music"),
            "folder-music-symbolic",
            "Music",
        ),
        (
            "XDG_PICTURES_DIR",
            i18n("Pictures"),
            "folder-pictures-symbolic",
            "Pictures",
        ),
        (
            "XDG_VIDEOS_DIR",
            i18n("Videos"),
            "folder-videos-symbolic",
            "Videos",
        ),
    ] {
        let components = match &configured {
            Some(configured) => configured.get(key).cloned(),
            None => Some(vec![fallback.to_string()]),
        };
        let Some(components) = components else {
            continue;
        };
        if components.is_empty() || !quick_location_root_exists(&root_directories, &components[0]) {
            continue;
        }
        let location = browser_location_owned(&components);
        if places.iter().any(|place| place.location == location) {
            continue;
        }
        places.push(QuickLocation {
            label,
            icon,
            location,
        });
    }
    places
}

fn quick_location_root_exists(root_directories: &Option<HashSet<String>>, component: &str) -> bool {
    root_directories
        .as_ref()
        .is_some_and(|directories| directories.contains(&encode_name_token(component.as_bytes())))
}

fn browser_location(components: &[&str]) -> Vec<BrowserLocation> {
    components
        .iter()
        .map(|component| BrowserLocation {
            token: encode_name_token(component.as_bytes()),
            display_name: (*component).to_string(),
        })
        .collect()
}

fn browser_location_owned(components: &[String]) -> Vec<BrowserLocation> {
    components
        .iter()
        .map(|component| BrowserLocation {
            token: encode_name_token(component.as_bytes()),
            display_name: component.clone(),
        })
        .collect()
}

fn read_snapshot_user_dirs(session_id: &str) -> Option<HashMap<String, Vec<String>>> {
    let path = browser_location(&[".config", "user-dirs.dirs"])
        .into_iter()
        .map(|location| location.token)
        .collect::<Vec<_>>();
    let (file, metadata) = client::open_snapshot_file_session(session_id, &path).ok()?;
    if metadata.size > MAX_USER_DIRS_BYTES {
        return None;
    }
    let mut contents = String::new();
    file.take(MAX_USER_DIRS_BYTES + 1)
        .read_to_string(&mut contents)
        .ok()?;
    Some(parse_xdg_user_dirs(&contents))
}

fn parse_xdg_user_dirs(contents: &str) -> HashMap<String, Vec<String>> {
    let mut directories = HashMap::new();
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((key, raw_value)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim();
        if !matches!(
            key,
            "XDG_DESKTOP_DIR"
                | "XDG_DOCUMENTS_DIR"
                | "XDG_DOWNLOAD_DIR"
                | "XDG_MUSIC_DIR"
                | "XDG_PICTURES_DIR"
                | "XDG_VIDEOS_DIR"
        ) {
            continue;
        }
        if let Some(components) = parse_home_relative_xdg_value(raw_value.trim()) {
            directories.insert(key.to_string(), components);
        }
    }
    directories
}

fn parse_home_relative_xdg_value(raw: &str) -> Option<Vec<String>> {
    let quoted = raw.strip_prefix('"')?.strip_suffix('"')?;
    let mut value = String::with_capacity(quoted.len());
    let mut escaped = false;
    for character in quoted.chars() {
        if escaped {
            value.push(character);
            escaped = false;
        } else if character == '\\' {
            escaped = true;
        } else {
            value.push(character);
        }
    }
    if escaped {
        return None;
    }
    let relative = value
        .strip_prefix("$HOME")
        .or_else(|| value.strip_prefix("${HOME}"))?;
    if !relative.is_empty() && !relative.starts_with('/') {
        return None;
    }
    let relative = relative.trim_matches('/');
    if relative.is_empty() {
        return Some(Vec::new());
    }
    let components = relative.split('/').map(str::to_string).collect::<Vec<_>>();
    if components.len() > 32
        || components.iter().any(|component| {
            component.is_empty()
                || component == "."
                || component == ".."
                || component.len() > 255
                || component.chars().any(char::is_control)
        })
    {
        return None;
    }
    Some(components)
}

const MAX_THUMBNAIL_INPUT_BYTES: u64 = 8 * 1024 * 1024;
const THUMBNAIL_EDGE: i32 = 128;

fn thumbnail_key(path: &[String]) -> String {
    path.join("/")
}

fn guessed_mime_type(name: &str) -> String {
    let (content_type, _) = gio::content_type_guess(Some(name), None::<&[u8]>);
    gio::content_type_get_mime_type(&content_type)
        .map(|mime| mime.to_string())
        .unwrap_or_default()
}

fn is_thumbnail_candidate(entry: &BrowserEntry) -> bool {
    if entry.kind != EntryKind::File || entry.size == 0 || entry.size > MAX_THUMBNAIL_INPUT_BYTES {
        return false;
    }
    let mime = guessed_mime_type(&entry.display_name);
    mime.starts_with("image/") && mime != "image/svg+xml"
}

fn load_thumbnail(
    session_id: &str,
    request: ThumbnailRequest,
    cancelled: &AtomicBool,
) -> Option<ThumbnailPixels> {
    let (mut file, metadata) =
        client::open_snapshot_file_session(session_id, &request.path).ok()?;
    if metadata.size == 0 || metadata.size > MAX_THUMBNAIL_INPUT_BYTES {
        return None;
    }
    let mut encoded = Vec::with_capacity(metadata.size as usize);
    let mut buffer = [0u8; 64 * 1024];
    loop {
        if cancelled.load(Ordering::Acquire) {
            return None;
        }
        let read = file.read(&mut buffer).ok()?;
        if read == 0 {
            break;
        }
        if encoded.len().saturating_add(read) > MAX_THUMBNAIL_INPUT_BYTES as usize {
            return None;
        }
        encoded.extend_from_slice(&buffer[..read]);
    }
    decode_thumbnail(request.key, &encoded, cancelled)
}

fn decode_thumbnail(
    key: String,
    encoded: &[u8],
    cancelled: &AtomicBool,
) -> Option<ThumbnailPixels> {
    let loader = gtk::gdk_pixbuf::PixbufLoader::new();
    loader.connect_size_prepared(|loader, width, height| {
        if width <= 0 || height <= 0 {
            return;
        }
        let scale = (THUMBNAIL_EDGE as f64 / width as f64)
            .min(THUMBNAIL_EDGE as f64 / height as f64)
            .min(1.0);
        loader.set_size(
            (width as f64 * scale).round().max(1.0) as i32,
            (height as f64 * scale).round().max(1.0) as i32,
        );
    });
    loader.write(&encoded).ok()?;
    loader.close().ok()?;
    let pixbuf = loader.pixbuf()?;
    let width = pixbuf.width();
    let height = pixbuf.height();
    let channels = pixbuf.n_channels();
    let has_alpha = pixbuf.has_alpha();
    if !(1..=THUMBNAIL_EDGE).contains(&width)
        || !(1..=THUMBNAIL_EDGE).contains(&height)
        || pixbuf.bits_per_sample() != 8
        || !matches!((channels, has_alpha), (3, false) | (4, true))
    {
        return None;
    }
    let source_stride = usize::try_from(pixbuf.rowstride()).ok()?;
    let row_bytes = usize::try_from(width).ok()? * usize::try_from(channels).ok()?;
    let source = pixbuf.read_pixel_bytes();
    let source = source.as_ref();
    let required = source_stride
        .checked_mul(usize::try_from(height - 1).ok()?)?
        .checked_add(row_bytes)?;
    if source_stride < row_bytes || source.len() < required {
        return None;
    }
    let mut pixels = Vec::with_capacity(row_bytes * height as usize);
    for row in 0..height as usize {
        let start = row * source_stride;
        pixels.extend_from_slice(&source[start..start + row_bytes]);
    }
    if cancelled.load(Ordering::Acquire) {
        return None;
    }
    Some(ThumbnailPixels {
        key,
        width,
        height,
        stride: row_bytes,
        has_alpha,
        pixels,
    })
}

fn property_row(title: &str, value: &str) -> adw::ActionRow {
    adw::ActionRow::builder()
        .title(title)
        .subtitle(value)
        .tooltip_text(value)
        .build()
}

fn entry_type_label(kind: EntryKind) -> String {
    match kind {
        EntryKind::Directory => i18n("Folder"),
        EntryKind::File => i18n("Regular File"),
        EntryKind::Symlink => i18n("Symbolic Link"),
        EntryKind::Special => i18n("Special File"),
    }
}

fn entry_property_size(entry: &BrowserEntry) -> String {
    if entry.kind == EntryKind::File {
        format!(
            "{} ({} {})",
            format_size(entry.size),
            entry.size,
            i18n("bytes")
        )
    } else {
        i18n("Not calculated")
    }
}

fn format_tree_contents(statistics: TreeStatistics) -> String {
    let mut summary = format!(
        "{} {} · {} {}",
        statistics.files,
        i18n("files"),
        statistics.directories,
        i18n("folders")
    );
    if statistics.unsupported_items > 0 {
        summary.push_str(&format!(
            " · {} {}",
            statistics.unsupported_items,
            i18n("unsupported items")
        ));
    }
    if !statistics.complete {
        summary.push_str(&format!(
            " · {}",
            i18n("Partial result — 100,000-item limit reached")
        ));
    }
    summary
}

fn export_completion_summary(report: ExportReport) -> String {
    let mut parts = vec![
        i18n("Copy complete"),
        format_size(report.copied_bytes),
        format!("{} {}", report.copied_files, i18n("files")),
        format!("{} {}", report.copied_directories, i18n("folders")),
    ];
    if report.skipped_items > 0 {
        parts.push(format!(
            "{} {}",
            report.skipped_items,
            i18n("items skipped")
        ));
    }
    parts.join(" · ")
}

fn entry_copy_status(kind: EntryKind) -> String {
    match kind {
        EntryKind::Directory => i18n("Can be copied recursively"),
        EntryKind::File => i18n("Can be copied out"),
        EntryKind::Symlink => i18n("Unavailable — symbolic links are not followed"),
        EntryKind::Special => i18n("Unavailable — special files are not copied"),
    }
}

fn snapshot_display_path(location: &[BrowserLocation], entry_name: &str) -> String {
    let mut components = location
        .iter()
        .map(|part| part.display_name.as_str())
        .collect::<Vec<_>>();
    components.push(entry_name);
    format!("/{}", components.join("/"))
}

fn sort_search_hits(hits: &mut [SnapshotSearchHit], mode: &str, descending: bool) {
    hits.sort_by(|left, right| {
        compare_browser_entries(&left.entry, &right.entry, mode, descending)
            .then_with(|| left.parent_tokens.cmp(&right.parent_tokens))
    });
}

fn sort_browser_entries(entries: &mut [BrowserEntry], mode: &str, descending: bool) {
    entries.sort_by(|left, right| compare_browser_entries(left, right, mode, descending));
}

fn ensure_entry_visible(
    entries: &mut Vec<BrowserEntry>,
    target: &BrowserEntry,
    mode: &str,
    descending: bool,
) -> bool {
    if entries.iter().any(|entry| entry.token == target.token) {
        return false;
    }
    entries.push(target.clone());
    sort_browser_entries(entries, mode, descending);
    true
}

fn compare_browser_entries(
    left: &BrowserEntry,
    right: &BrowserEntry,
    mode: &str,
    descending: bool,
) -> std::cmp::Ordering {
    let directory_order = match (
        left.kind == EntryKind::Directory,
        right.kind == EntryKind::Directory,
    ) {
        (true, false) => std::cmp::Ordering::Less,
        (false, true) => std::cmp::Ordering::Greater,
        _ => std::cmp::Ordering::Equal,
    };
    if directory_order != std::cmp::Ordering::Equal {
        return directory_order;
    }
    let primary = match mode {
        "modified" => left.modified_unix.cmp(&right.modified_unix),
        "size" => left.size.cmp(&right.size),
        _ => left
            .display_name
            .to_lowercase()
            .cmp(&right.display_name.to_lowercase()),
    };
    let primary = if descending {
        primary.reverse()
    } else {
        primary
    };
    primary
        .then_with(|| {
            left.display_name
                .to_lowercase()
                .cmp(&right.display_name.to_lowercase())
        })
        .then_with(|| left.token.cmp(&right.token))
}

fn format_modified(timestamp: i64) -> String {
    chrono::DateTime::from_timestamp(timestamp, 0)
        .map(|time| {
            time.with_timezone(&chrono::Local)
                .format("%Y-%m-%d %H:%M:%S %Z")
                .to_string()
        })
        .unwrap_or_else(|| i18n("Unknown date"))
}

fn format_modified_short(timestamp: i64) -> String {
    chrono::DateTime::from_timestamp(timestamp, 0)
        .map(|time| {
            time.with_timezone(&chrono::Local)
                .format("%Y-%m-%d %H:%M")
                .to_string()
        })
        .unwrap_or_else(|| i18n("Unknown date"))
}

fn format_permissions(mode: u32) -> String {
    if mode == 0 {
        return i18n("Unknown");
    }
    let bits = mode & 0o7777;
    let mut text = String::with_capacity(9);
    for (read, write, execute, special, active, inactive) in [
        (0o400, 0o200, 0o100, 0o4000, 's', 'S'),
        (0o040, 0o020, 0o010, 0o2000, 's', 'S'),
        (0o004, 0o002, 0o001, 0o1000, 't', 'T'),
    ] {
        text.push(if bits & read != 0 { 'r' } else { '-' });
        text.push(if bits & write != 0 { 'w' } else { '-' });
        text.push(if bits & special != 0 {
            if bits & execute != 0 {
                active
            } else {
                inactive
            }
        } else if bits & execute != 0 {
            'x'
        } else {
            '-'
        });
    }
    format!("{text} ({bits:04o})")
}

fn entry_icon(entry: &BrowserEntry) -> &'static str {
    match entry.kind {
        EntryKind::Directory => "folder-symbolic",
        EntryKind::File => {
            let mime = guessed_mime_type(&entry.display_name);
            if mime.starts_with("image/") {
                "image-x-generic-symbolic"
            } else if mime.starts_with("audio/") {
                "audio-x-generic-symbolic"
            } else if mime.starts_with("video/") {
                "video-x-generic-symbolic"
            } else if mime == "application/pdf" {
                "application-pdf-symbolic"
            } else if mime.starts_with("text/") {
                "text-x-generic-symbolic"
            } else if matches!(
                mime.as_str(),
                "application/zip"
                    | "application/x-7z-compressed"
                    | "application/x-bzip2"
                    | "application/x-gzip"
                    | "application/x-rar"
                    | "application/x-tar"
            ) {
                "package-x-generic-symbolic"
            } else {
                "text-x-generic-symbolic"
            }
        }
        EntryKind::Symlink => "emblem-symbolic-link-symbolic",
        EntryKind::Special => "application-x-executable-symbolic",
    }
}

fn entry_subtitle(entry: &BrowserEntry) -> String {
    let modified = format_modified_short(entry.modified_unix);
    match entry.kind {
        EntryKind::Directory => format!("{} · {modified}", i18n("Folder")),
        EntryKind::File => format!("{} · {modified}", format_size(entry.size)),
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_localized_and_escaped_xdg_user_directories() {
        let parsed = parse_xdg_user_dirs(
            r#"
            # Generated by xdg-user-dirs-update
            XDG_DESKTOP_DIR="$HOME/桌面"
            XDG_DOCUMENTS_DIR="$HOME/Work\ Files"
            XDG_DOWNLOAD_DIR="${HOME}/Downloads"
            "#,
        );
        assert_eq!(parsed["XDG_DESKTOP_DIR"], ["桌面"]);
        assert_eq!(parsed["XDG_DOCUMENTS_DIR"], ["Work Files"]);
        assert_eq!(parsed["XDG_DOWNLOAD_DIR"], ["Downloads"]);
    }

    #[test]
    fn rejects_xdg_paths_outside_home_or_with_unsafe_components() {
        for value in [
            "\"/etc\"",
            "\"$HOME/../etc\"",
            "\"$HOME/Documents/./Private\"",
            "\"$HOMEsuffix/Documents\"",
            "\"$HOME/unfinished\\\"",
        ] {
            assert_eq!(parse_home_relative_xdg_value(value), None, "{value}");
        }
        assert_eq!(parse_home_relative_xdg_value("\"$HOME/\""), Some(vec![]));
    }

    #[test]
    fn quick_location_tokens_preserve_localized_path_components() {
        let components = vec!["项目".to_string(), "旧文件".to_string()];
        let location = browser_location_owned(&components);
        assert_eq!(location[0].display_name, "项目");
        assert_eq!(location[0].token, encode_name_token("项目".as_bytes()));
        assert_eq!(location[1].display_name, "旧文件");
        assert_eq!(location[1].token, encode_name_token("旧文件".as_bytes()));
    }

    #[test]
    fn formats_snapshot_paths_and_unix_permissions_without_losing_details() {
        let location = browser_location_owned(&["文档".into(), "项目".into()]);
        assert_eq!(
            snapshot_display_path(&location, "计划.txt"),
            "/文档/项目/计划.txt"
        );
        assert_eq!(format_permissions(0o100644), "rw-r--r-- (0644)");
        assert_eq!(format_permissions(0o104755), "rwsr-xr-x (4755)");
        assert_eq!(format_permissions(0o041770), "rwxrwx--T (1770)");
    }

    #[test]
    fn thumbnail_candidates_are_raster_files_with_bounded_input() {
        let mut entry = BrowserEntry {
            token: "70686f746f2e706e67".into(),
            display_name: "photo.png".into(),
            kind: EntryKind::File,
            size: 1024,
            modified_unix: 0,
            mode: 0o100644,
            hidden: false,
        };
        assert!(is_thumbnail_candidate(&entry));
        assert_eq!(entry_icon(&entry), "image-x-generic-symbolic");
        entry.display_name = "vector.svg".into();
        assert!(!is_thumbnail_candidate(&entry));
        entry.display_name = "photo.jpg".into();
        entry.size = MAX_THUMBNAIL_INPUT_BYTES + 1;
        assert!(!is_thumbnail_candidate(&entry));
        entry.kind = EntryKind::Directory;
        entry.size = 1024;
        assert!(!is_thumbnail_candidate(&entry));
    }

    #[test]
    fn thumbnail_decoder_produces_small_packed_pixels() {
        const PNG: &[u8] = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdacd\xf8\x0f\x00\x01\x05\x01\x01'\x18\xe3f\x00\x00\x00\x00IEND\xaeB`\x82";
        let thumbnail = decode_thumbnail("one".into(), PNG, &AtomicBool::new(false)).unwrap();
        assert_eq!((thumbnail.width, thumbnail.height), (1, 1));
        assert!(matches!(thumbnail.pixels.len(), 3 | 4));
        assert_eq!(thumbnail.stride, thumbnail.pixels.len());
    }

    #[test]
    fn folder_summary_discloses_unsupported_and_partial_items() {
        let summary = format_tree_contents(TreeStatistics {
            bytes: 100,
            files: 2,
            directories: 3,
            unsupported_items: 1,
            complete: false,
        });
        for expected in ["2", "3", "1", "100,000"] {
            assert!(summary.contains(expected), "{summary}");
        }
    }

    #[test]
    fn copy_completion_summary_reports_every_outcome() {
        let summary = export_completion_summary(ExportReport {
            copied_bytes: 2048,
            copied_files: 3,
            copied_directories: 2,
            skipped_items: 1,
        });
        for expected in ["2.0 KB", "3", "2", "1"] {
            assert!(summary.contains(expected), "{summary}");
        }
        let without_skips = export_completion_summary(ExportReport {
            skipped_items: 0,
            ..ExportReport::default()
        });
        assert!(!without_skips.contains("skipped"), "{without_skips}");
    }

    #[test]
    fn search_results_keep_folders_first_and_honor_the_chosen_sort() {
        let make_hit = |name: &str, kind: EntryKind, size: u64| SnapshotSearchHit {
            parent_tokens: vec!["parent".into()],
            parent_names: vec!["Parent".into()],
            entry: BrowserEntry {
                token: name.into(),
                display_name: name.into(),
                kind,
                size,
                modified_unix: size as i64,
                mode: 0,
                hidden: false,
            },
        };
        let mut hits = vec![
            make_hit("small", EntryKind::File, 1),
            make_hit("folder", EntryKind::Directory, 0),
            make_hit("large", EntryKind::File, 10),
        ];
        sort_search_hits(&mut hits, "size", true);
        assert_eq!(
            hits.iter()
                .map(|hit| hit.entry.display_name.as_str())
                .collect::<Vec<_>>(),
            ["folder", "large", "small"]
        );
        let target = hits[1].entry.clone();
        let mut page = vec![hits[2].entry.clone()];
        assert!(ensure_entry_visible(&mut page, &target, "size", true));
        assert!(!ensure_entry_visible(&mut page, &target, "size", true));
        assert_eq!(
            page.iter()
                .map(|entry| entry.display_name.as_str())
                .collect::<Vec<_>>(),
            ["large", "small"]
        );
    }
}
