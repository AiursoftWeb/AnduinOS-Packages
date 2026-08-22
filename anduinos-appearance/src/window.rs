#![allow(deprecated)]

use std::cell::{Cell, RefCell};
use std::process::Command;
use std::rc::Rc;
use std::time::Duration;

use adw::prelude::*;
use gtk::gio;
use gtk::glib;
use gtk::glib::object::Cast;

use crate::application::AppearanceApplication;
use crate::config;
use crate::display;
use crate::i18n::{i18n, i18n_replace};
use crate::layout::{self, Position, Style};
use crate::preview;



pub struct Ui {
    pub window: adw::PreferencesWindow,
    style: Cell<Style>,
    position: Cell<Position>,
    preview: gtk::DrawingArea,
    btn_classic: gtk::Button,
    btn_separated: gtk::Button,
    btn_centered: gtk::Button,
    pos_buttons: Vec<(Position, gtk::ToggleButton)>,
    group_group: adw::PreferencesGroup,
    group_expander: adw::ExpanderRow,
    group_row: adw::SwitchRow,
    vista_row: adw::SwitchRow,
    widget_rows: Vec<(adw::SwitchRow, &'static str)>,
    activities_row: adw::SwitchRow,
    ext_rows: Vec<(adw::ActionRow, &'static str)>,
    busy: Cell<bool>,
    wallpaper_path: RefCell<String>,
    preview_img: gtk::Picture,
    applied_label: gtk::Label,
    hide_applied: RefCell<Option<glib::SourceId>>,
    committed: Cell<(Style, Position)>,
    pending_apply: RefCell<Option<glib::SourceId>>,
    apply_in_flight: Cell<bool>,
}

impl Ui {
    pub fn refresh(&self) {
        let (style, position) = layout::detect_current();
        self.style.set(style);
        self.position.set(position);
        self.committed.set((style, position));
        self.sync_highlights();
        self.preview.queue_draw();
        self.sync_group_visibility();
        self.refresh_widgets();
        self.refresh_ext_rows();
    }

    fn toast(&self, message: &str) {
        let toast = adw::Toast::new(message);
        toast.set_timeout(2);
        toast.set_priority(adw::ToastPriority::High);
        self.window.add_toast(toast);
        make_toasts_instant(self.window.upcast_ref());
    }

    fn show_applied(self: &Rc<Self>, message: &str) {
        self.applied_label.set_label(message);
        self.applied_label.set_visible(true);
        self.applied_label.set_opacity(1.0);
        if let Some(source) = self.hide_applied.borrow_mut().take() {
            source.remove();
        }
        let ui = Rc::clone(self);
        let source = glib::timeout_add_local_once(Duration::from_secs(2), move || {
            ui.applied_label.set_opacity(0.0);
            ui.applied_label.set_visible(false);
            ui.hide_applied.borrow_mut().take();
        });
        *self.hide_applied.borrow_mut() = Some(source);
    }

    fn style_label(style: Style) -> String {
        match style {
            Style::Eleven => i18n("Centered"),
            Style::Seperated => i18n("Seperated"),
            Style::Classic => i18n("Classic"),
        }
    }

    fn position_label(position: Position) -> String {
        match position {
            Position::Bottom => i18n("Bottom"),
            Position::Top => i18n("Top"),
            Position::Left => i18n("Left"),
            Position::Right => i18n("Right"),
        }
    }

    fn sync_highlights(&self) {
        let style = self.style.get();
        toggle_suggested(&self.btn_classic, style == Style::Classic);
        toggle_suggested(&self.btn_separated, style == Style::Seperated);
        toggle_suggested(&self.btn_centered, style == Style::Eleven);

        self.busy.set(true);
        for (position, button) in &self.pos_buttons {
            let active = *position == self.position.get();
            button.set_active(active);
            toggle_suggested(button, active);
        }
        self.busy.set(false);
    }

    fn sync_group_visibility(&self) {
        if self.style.get().uses_group_apps() {
            self.group_group.set_visible(true);
            self.group_expander.set_enable_expansion(true);
            self.busy.set(true);
            self.group_row.set_active(layout::read_group_apps());
            self.busy.set(false);
            self.sync_vista_visibility();
        } else {
            self.group_group.set_visible(false);
        }
    }

    fn sync_vista_visibility(&self) {
        if layout::read_group_apps() {
            self.vista_row.set_visible(false);
        } else {
            self.vista_row.set_visible(true);
            self.busy.set(true);
            self.vista_row.set_active(layout::read_use_launchers());
            self.busy.set(false);
        }
    }

    fn refresh_widgets(&self) {
        self.busy.set(true);
        for (row, uuid) in &self.widget_rows {
            row.set_active(layout::extension_enabled(uuid));
        }
        self.activities_row
            .set_active(layout::dconf_read(&format!("{}/show-activities-button", layout::ARC)).as_deref() != Some("false"));
        self.busy.set(false);
    }

    fn refresh_ext_rows(&self) {
        for (row, uuid) in &self.ext_rows {
            row.set_visible(layout::extension_enabled(uuid));
        }
    }

    fn apply_style(self: &Rc<Self>, style: Style) {
        self.style.set(style);
        self.sync_highlights();
        self.preview.queue_draw();
        self.show_applied(&i18n_replace(
            "✓ Applied — {style} | {pos}",
            &[
                ("style", &Self::style_label(style)),
                ("pos", &Self::position_label(self.position.get())),
            ],
        ));
        self.queue_layout_commit();
    }

    fn apply_position(self: &Rc<Self>, position: Position) {
        self.position.set(position);
        self.sync_highlights();
        self.preview.queue_draw();
        self.show_applied(&i18n_replace(
            "✓ Applied — {style} | {pos}",
            &[
                ("style", &Self::style_label(self.style.get())),
                ("pos", &Self::position_label(position)),
            ],
        ));
        self.queue_layout_commit();
    }

    fn queue_layout_commit(self: &Rc<Self>) {
        if let Some(source) = self.pending_apply.borrow_mut().take() {
            source.remove();
        }
        if (self.style.get(), self.position.get()) == self.committed.get() {
            return;
        }
        let ui = Rc::clone(self);
        // Human rapid clicks are ~100–200ms apart. Wait until they pause so
        // Dash-to-Panel rebuilds once instead of on every click.
        let source = glib::timeout_add_local_once(Duration::from_millis(350), move || {
            ui.pending_apply.borrow_mut().take();
            ui.commit_layout();
        });
        *self.pending_apply.borrow_mut() = Some(source);
    }

    fn commit_layout(self: &Rc<Self>) {
        let next = (self.style.get(), self.position.get());
        if next == self.committed.get() {
            self.sync_group_visibility();
            return;
        }
        if self.apply_in_flight.get() {
            return;
        }
        self.apply_in_flight.set(true);
        let height = display::smallest_monitor_height();
        let (tx, rx) = async_channel::bounded(1);
        std::thread::spawn(move || {
            let ok = layout::apply_style_and_position_with_height(next.0, next.1, height);
            let _ = tx.send_blocking(ok);
        });
        let ui = Rc::clone(self);
        glib::spawn_future_local(async move {
            let ok = rx.recv().await.unwrap_or(false);
            ui.apply_in_flight.set(false);
            if ok {
                ui.committed.set(next);
            } else {
                ui.show_applied(&i18n("✗ Failed to apply style"));
            }
            if (ui.style.get(), ui.position.get()) != ui.committed.get() {
                ui.commit_layout();
            } else {
                ui.sync_group_visibility();
            }
        });
    }
}

fn load_applied_css() {
    static LOADED: std::sync::Once = std::sync::Once::new();
    LOADED.call_once(|| {
        let Some(display) = gtk::gdk::Display::default() else {
            return;
        };
        let provider = gtk::CssProvider::new();
        provider.load_from_data(
            ".applied-status {
                padding: 6px 16px;
                border-radius: 9999px;
                background-color: alpha(@window_bg_color, 0.92);
                box-shadow: 0 2px 10px alpha(black, 0.28);
                font-weight: 600;
            }
            toast-overlay > revealer {
                transition: none;
            }",
        );
        gtk::style_context_add_provider_for_display(
            &display,
            &provider,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
    });
}

fn overlay_applied_status(window: &adw::PreferencesWindow, label: &gtk::Label) {
    let Some(content) = window.content() else {
        return;
    };
    window.set_content(gtk::Widget::NONE);
    let overlay = gtk::Overlay::new();
    overlay.set_child(Some(&content));
    overlay.add_overlay(label);
    window.set_content(Some(&overlay));
}

fn make_toasts_instant(widget: &gtk::Widget) {
    if let Ok(revealer) = widget.clone().downcast::<gtk::Revealer>() {
        revealer.set_transition_duration(0);
        revealer.set_transition_type(gtk::RevealerTransitionType::None);
    }
    let mut child = widget.first_child();
    while let Some(node) = child {
        make_toasts_instant(&node);
        child = node.next_sibling();
    }
}

fn toggle_suggested(widget: &impl WidgetExt, on: bool) {
    if on {
        widget.add_css_class("suggested-action");
    } else {
        widget.remove_css_class("suggested-action");
    }
}

fn find_headerbar(widget: &gtk::Widget) -> Option<adw::HeaderBar> {
    if let Ok(header) = widget.clone().downcast::<adw::HeaderBar>() {
        return Some(header);
    }
    let mut child = widget.first_child();
    while let Some(node) = child {
        if let Some(found) = find_headerbar(&node) {
            return Some(found);
        }
        child = node.next_sibling();
    }
    None
}

pub fn build(app: &AppearanceApplication, resident: bool) -> Rc<Ui> {
    let (style, position) = layout::detect_current();
    let window = adw::PreferencesWindow::builder()
        .application(app)
        .title(i18n("AnduinOS Appearance"))
        .default_width(780)
        .default_height(560)
        .icon_name(config::ICON_NAME)
        .build();

    let preview = gtk::DrawingArea::new();
    preview.set_content_width(340);
    preview.set_content_height(110);
    preview.set_size_request(340, 110);

    let btn_classic = gtk::Button::with_label(&format!("  {}  ", i18n("Classic")));
    let btn_separated = gtk::Button::with_label(&format!("  {}  ", i18n("Seperated")));
    let btn_centered = gtk::Button::with_label(&format!("  {}  ", i18n("Centered")));

    let mut pos_buttons = Vec::new();
    for pos in Position::all() {
        pos_buttons.push((pos, gtk::ToggleButton::with_label(&Ui::position_label(pos))));
    }

    let group_expander = adw::ExpanderRow::builder()
        .title(i18n("Group apps"))
        .subtitle(i18n("Combine multiple windows of one app into a single icon"))
        .build();
    let group_row = adw::SwitchRow::builder()
        .title(i18n("Enable grouping"))
        .active(layout::read_group_apps())
        .build();
    let vista_row = adw::SwitchRow::builder()
        .title(i18n("Use Vista taskbar behavior"))
        .subtitle(i18n("Keep launchers separate from running apps"))
        .active(layout::read_use_launchers())
        .build();
    group_expander.add_row(&group_row);
    group_expander.add_row(&vista_row);
    let group_group = adw::PreferencesGroup::new();
    group_group.add(&group_expander);

    let activities_row = adw::SwitchRow::builder()
        .title(i18n("Show Activities Button"))
        .subtitle(i18n("Display the Activities button on the taskbar"))
        .active(layout::dconf_read(&format!("{}/show-activities-button", layout::ARC)).as_deref() != Some("false"))
        .build();

    let mut widget_rows = Vec::new();
    for (title, uuid) in [
        (i18n("Show Weather"), "simple-weather@romanlefler.com"),
        (i18n("Show Network"), "network-stats@gnome.noroadsleft.xyz"),
        (i18n("Show Desktop Icons"), "ding@rastersoft.com"),
    ] {
        let row = adw::SwitchRow::builder()
            .title(title)
            .subtitle(uuid)
            .active(layout::extension_enabled(uuid))
            .build();
        widget_rows.push((row, uuid));
    }

    let mut ext_rows = Vec::new();
    for (title, uuid) in [
        (i18n("ArcMenu"), "arcmenu@arcmenu.com"),
        (i18n("Dash-to-Panel"), "dash-to-panel@jderose9.github.com"),
        (i18n("Simple Weather"), "simple-weather@romanlefler.com"),
        (i18n("Network Stats"), "network-stats@gnome.noroadsleft.xyz"),
    ] {
        let row = adw::ActionRow::builder().title(title).build();
        let button = gtk::Button::with_label(&i18n("Open"));
        button.set_valign(gtk::Align::Center);
        button.connect_clicked(move |_| {
            let _ = Command::new("gnome-extensions").args(["prefs", uuid]).spawn();
        });
        row.add_suffix(&button);
        row.set_visible(layout::extension_enabled(uuid));
        ext_rows.push((row, uuid));
    }

    let preview_img = gtk::Picture::new();
    preview_img.set_size_request(340, 190);
    preview_img.set_content_fit(gtk::ContentFit::Cover);
    preview_img.add_css_class("card");
    preview_img.set_visible(false);

    let applied_label = gtk::Label::new(None);
    applied_label.add_css_class("applied-status");
    applied_label.set_halign(gtk::Align::Center);
    applied_label.set_valign(gtk::Align::End);
    applied_label.set_margin_bottom(24);
    applied_label.set_can_target(false);
    applied_label.set_visible(false);
    applied_label.set_opacity(0.0);
    load_applied_css();

    let ui = Rc::new(Ui {
        window: window.clone(),
        style: Cell::new(style),
        position: Cell::new(position),
        preview: preview.clone(),
        btn_classic: btn_classic.clone(),
        btn_separated: btn_separated.clone(),
        btn_centered: btn_centered.clone(),
        pos_buttons,
        group_group: group_group.clone(),
        group_expander,
        group_row: group_row.clone(),
        vista_row: vista_row.clone(),
        widget_rows,
        activities_row: activities_row.clone(),
        ext_rows,
        busy: Cell::new(false),
        wallpaper_path: RefCell::new(String::new()),
        preview_img: preview_img.clone(),
        applied_label: applied_label.clone(),
        hide_applied: RefCell::new(None),
        committed: Cell::new((style, position)),
        pending_apply: RefCell::new(None),
        apply_in_flight: Cell::new(false),
    });

    let draw_ui = ui.clone();
    preview.set_draw_func(move |_, cr, w, h| {
        preview::draw(cr, w, h, draw_ui.style.get(), draw_ui.position.get());
    });

    let style_page = build_style_page(&ui, &preview, &btn_classic, &btn_separated, &btn_centered, &group_group);
    let widgets_page = build_widgets_page(&ui, &activities_row);
    let gdm_page = build_gdm_page(&ui, &preview_img);
    let advanced_page = build_advanced_page(&ui);

    window.add(&style_page);
    window.add(&widgets_page);
    window.add(&gdm_page);
    window.add(&advanced_page);

    ui.sync_highlights();
    ui.sync_group_visibility();

    let injected = Rc::new(Cell::new(false));
    let applied_for_overlay = applied_label.clone();
    window.connect_realize(move |win| {
        if injected.replace(true) {
            return;
        }
        let menu = gio::Menu::new();
        menu.append(Some(&i18n("About AnduinOS Appearance")), Some("app.about"));
        let menu_btn = gtk::MenuButton::builder()
            .icon_name("open-menu-symbolic")
            .menu_model(&menu)
            .build();
        make_toasts_instant(win.upcast_ref());
        if let Some(header) = find_headerbar(win.upcast_ref()) {
            header.pack_end(&menu_btn);
        }
        overlay_applied_status(win, &applied_for_overlay);
    });

    if resident {
        window.connect_close_request(|win| {
            win.set_visible(false);
            glib::Propagation::Stop
        });
    }

    ui
}

fn build_style_page(
    ui: &Rc<Ui>,
    preview: &gtk::DrawingArea,
    btn_classic: &gtk::Button,
    btn_separated: &gtk::Button,
    btn_centered: &gtk::Button,
    group_group: &adw::PreferencesGroup,
) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::builder()
        .title(i18n("Taskbar Style"))
        .icon_name("preferences-desktop-theme-symbolic")
        .build();

    let preview_group = adw::PreferencesGroup::builder()
        .title(i18n("Preview"))
        .description(i18n("Choose the visual style of your taskbar."))
        .build();
    let card = gtk::Frame::new(None);
    card.add_css_class("card");
    card.set_child(Some(preview));
    preview_group.add(&card);
    page.add(&preview_group);

    let layout_group = adw::PreferencesGroup::builder()
        .title(i18n("Layout"))
        .build();
    let box_ = gtk::Box::new(gtk::Orientation::Horizontal, 12);
    box_.append(btn_classic);
    box_.append(btn_separated);
    box_.append(btn_centered);
    layout_group.add(&box_);

    let pos_box = gtk::FlowBox::builder()
        .homogeneous(false)
        .selection_mode(gtk::SelectionMode::None)
        .row_spacing(6)
        .column_spacing(6)
        .margin_top(12)
        .build();
    for (position, button) in &ui.pos_buttons {
        let ui = ui.clone();
        let position = *position;
        button.connect_toggled(move |btn| {
            if ui.busy.get() {
                return;
            }
            if !btn.is_active() {
                if position == ui.position.get() {
                    ui.busy.set(true);
                    btn.set_active(true);
                    ui.busy.set(false);
                }
                return;
            }
            ui.busy.set(true);
            for (other_pos, other) in &ui.pos_buttons {
                if *other_pos != position {
                    other.set_active(false);
                }
            }
            ui.busy.set(false);
            ui.apply_position(position);
        });
        pos_box.insert(button, -1);
    }
    layout_group.add(&pos_box);
    page.add(&layout_group);
    page.add(group_group);

    {
        let ui = ui.clone();
        btn_classic.connect_clicked(move |_| ui.apply_style(Style::Classic));
    }
    {
        let ui = ui.clone();
        btn_separated.connect_clicked(move |_| ui.apply_style(Style::Seperated));
    }
    {
        let ui = ui.clone();
        btn_centered.connect_clicked(move |_| ui.apply_style(Style::Eleven));
    }
    {
        let group_row = ui.group_row.clone();
        let ui = ui.clone();
        group_row.connect_active_notify(move |row| {
            if ui.busy.get() {
                return;
            }
            if ui.style.get().uses_group_apps() {
                let _ = layout::write_group_apps(row.is_active());
                ui.sync_vista_visibility();
            }
        });
    }
    {
        let vista_row = ui.vista_row.clone();
        let ui = ui.clone();
        vista_row.connect_active_notify(move |row| {
            if ui.busy.get() {
                return;
            }
            if ui.style.get().uses_group_apps() {
                let _ = layout::write_use_launchers(row.is_active());
            }
        });
    }

    page
}

fn build_widgets_page(ui: &Rc<Ui>, activities_row: &adw::SwitchRow) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::builder()
        .title(i18n("Panel Widgets"))
        .icon_name("application-x-addon-symbolic")
        .build();

    let group = adw::PreferencesGroup::builder()
        .title(i18n("Panel Indicators"))
        .description(i18n("Toggle panel indicator widgets on or off."))
        .build();
    for (row, uuid) in &ui.widget_rows {
        let ui = ui.clone();
        let uuid = *uuid;
        let title = row.title();
        row.connect_active_notify(move |row| {
            if ui.busy.get() {
                return;
            }
            let enabled = row.is_active();
            let action = if enabled { "enable" } else { "disable" };
            match Command::new("gnome-extensions").args([action, uuid]).status() {
                Ok(status) if status.success() => {
                    ui.toast(&i18n_replace(
                        "✓ {label} — {action}d",
                        &[("label", title.as_str()), ("action", action)],
                    ));
                }
                _ => {
                    ui.toast(&i18n_replace(
                        "✗ Failed to {action} {label}",
                        &[("action", action), ("label", title.as_str())],
                    ));
                    ui.busy.set(true);
                    row.set_active(!enabled);
                    ui.busy.set(false);
                }
            }
        });
        group.add(row);
    }
    page.add(&group);

    let tb_group = adw::PreferencesGroup::builder()
        .title(i18n("Taskbar Elements"))
        .description(i18n("Show or hide built-in panel elements."))
        .build();
    {
        let ui = ui.clone();
        activities_row.connect_active_notify(move |row| {
            if ui.busy.get() {
                return;
            }
            let visible = row.is_active();
            let value = if visible { "true" } else { "false" };
            match Command::new("dconf")
                .args(["write", &format!("{}/show-activities-button", layout::ARC), value])
                .status()
            {
                Ok(status) if status.success() => {
                    layout::apply_style_and_position(ui.style.get(), ui.position.get());
                    let action = if visible { "shown" } else { "hidden" };
                    ui.toast(&i18n_replace(
                        "✓ Activities button — {action}n",
                        &[("action", action)],
                    ));
                }
                _ => {
                    ui.toast(&i18n("✗ Failed to toggle activities button"));
                    ui.busy.set(true);
                    row.set_active(!visible);
                    ui.busy.set(false);
                }
            }
        });
    }
    tb_group.add(activities_row);
    page.add(&tb_group);
    page
}

fn build_gdm_page(ui: &Rc<Ui>, preview_img: &gtk::Picture) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::builder()
        .title(i18n("GDM Wallpaper"))
        .icon_name("preferences-desktop-wallpaper-symbolic")
        .build();

    let choose = adw::PreferencesGroup::builder()
        .title(i18n("Login Screen Wallpaper"))
        .description(i18n("Choose an image for the GDM login screen background."))
        .build();
    choose.add(preview_img);

    let btn_row = adw::ActionRow::builder()
        .title(i18n("Select image"))
        .build();
    let choose_btn = gtk::Button::with_label(&i18n("Choose…"));
    choose_btn.set_valign(gtk::Align::Center);
    {
        let ui = ui.clone();
        choose_btn.connect_clicked(move |_| {
            let window = ui.window.clone();
            let ui = ui.clone();
            glib::spawn_future_local(async move {
                let dialog = gtk::FileDialog::new();
                let filter = gtk::FileFilter::new();
                filter.set_name(Some(&i18n("Images")));
                filter.add_mime_type("image/png");
                filter.add_mime_type("image/jpeg");
                let filters = gio::ListStore::new::<gtk::FileFilter>();
                filters.append(&filter);
                dialog.set_filters(Some(&filters));
                if let Ok(file) = dialog.open_future(Some(&window)).await {
                    if let Some(path) = file.path() {
                        let path = path.to_string_lossy().into_owned();
                        ui.preview_img.set_filename(Some(&path));
                        ui.preview_img.set_visible(true);
                        *ui.wallpaper_path.borrow_mut() = path;
                    }
                }
            });
        });
    }
    btn_row.add_suffix(&choose_btn);
    choose.add(&btn_row);
    page.add(&choose);

    let apply_grp = adw::PreferencesGroup::new();
    let apply_btn = gtk::Button::with_label(&i18n("Apply to Login Screen"));
    apply_btn.add_css_class("suggested-action");
    apply_btn.set_halign(gtk::Align::Center);
    apply_btn.set_margin_top(12);
    apply_btn.set_margin_bottom(12);
    {
        let ui = ui.clone();
        apply_btn.connect_clicked(move |btn| {
            let path = ui.wallpaper_path.borrow().clone();
            if path.is_empty() {
                ui.toast(&i18n("✗ Please select an image first"));
                return;
            }
            btn.set_sensitive(false);
            let argv = [
                std::ffi::OsStr::new("pkexec"),
                std::ffi::OsStr::new("anduinos-gdm-set-wallpaper"),
                std::ffi::OsStr::new("--wallpaper"),
                std::ffi::OsStr::new(&path),
                std::ffi::OsStr::new("--output"),
                std::ffi::OsStr::new(config::GDM_OUTPUT),
            ];
            match gio::Subprocess::newv(&argv, gio::SubprocessFlags::NONE) {
                Ok(proc) => {
                    let ui = ui.clone();
                    let btn = btn.clone();
                    glib::spawn_future_local(async move {
                        let ok = proc.wait_future().await.is_ok();
                        btn.set_sensitive(true);
                        if ok {
                            ui.toast(&i18n("✓ GDM wallpaper applied"));
                        } else {
                            ui.toast(&i18n("✗ Failed to set GDM wallpaper"));
                        }
                    });
                }
                Err(_) => {
                    btn.set_sensitive(true);
                    ui.toast(&i18n("✗ Failed to set GDM wallpaper"));
                }
            }
        });
    }
    apply_grp.add(&apply_btn);
    page.add(&apply_grp);
    page
}

fn build_advanced_page(ui: &Rc<Ui>) -> adw::PreferencesPage {
    let page = adw::PreferencesPage::builder()
        .title(i18n("Advanced"))
        .icon_name("preferences-other-symbolic")
        .build();

    let appear = adw::PreferencesGroup::builder()
        .title(i18n("Appearance"))
        .build();
    let wallpaper_row = adw::ActionRow::builder()
        .title(i18n("Wallpaper"))
        .build();
    let wallpaper_btn = gtk::Button::with_label(&i18n("Open"));
    wallpaper_btn.set_valign(gtk::Align::Center);
    wallpaper_btn.connect_clicked(|_| {
        let _ = Command::new("gnome-control-center").arg("background").spawn();
    });
    wallpaper_row.add_suffix(&wallpaper_btn);
    appear.add(&wallpaper_row);
    if display::command_exists("gnome-tweaks") {
        let tweaks_row = adw::ActionRow::builder().title(i18n("Tweaks")).build();
        let tweaks_btn = gtk::Button::with_label(&i18n("Open"));
        tweaks_btn.set_valign(gtk::Align::Center);
        tweaks_btn.connect_clicked(|_| {
            let _ = Command::new("gnome-tweaks").spawn();
        });
        tweaks_row.add_suffix(&tweaks_btn);
        appear.add(&tweaks_row);
    }
    page.add(&appear);

    let ext = adw::PreferencesGroup::builder()
        .title(i18n("Extension Settings"))
        .description(i18n("Open the preference windows of the panel extensions."))
        .build();
    for (row, _) in &ui.ext_rows {
        ext.add(row);
    }
    page.add(&ext);
    {
        let ui = ui.clone();
        page.connect_map(move |_| ui.refresh_ext_rows());
    }

    let danger = adw::PreferencesGroup::builder()
        .title(i18n("Danger Zone"))
        .build();
    let reset_row = adw::ActionRow::builder()
        .title(i18n("Reset desktop settings"))
        .subtitle(i18n("Restore all GNOME Shell settings to factory defaults"))
        .build();
    let reset_btn = gtk::Button::with_label(&i18n("Reset"));
    reset_btn.set_valign(gtk::Align::Center);
    reset_btn.add_css_class("destructive-action");
    {
        let ui = ui.clone();
        reset_btn.connect_clicked(move |_| {
            let dialog = adw::AlertDialog::builder()
                .heading(i18n("Reset desktop settings?"))
                .body(i18n(
                    "This will reset all GNOME Shell settings to factory defaults.\nYour extensions, panel layout, and preferences will be lost.",
                ))
                .default_response("cancel")
                .close_response("cancel")
                .build();
            dialog.add_response("cancel", &i18n("Cancel"));
            dialog.add_response("reset", &i18n("Reset"));
            dialog.set_response_appearance("reset", adw::ResponseAppearance::Destructive);
            let ui_for_response = ui.clone();
            dialog.connect_response(None, move |_, response| {
                if response != "reset" {
                    return;
                }
                let reset = Command::new("dconf")
                    .args(["reset", "-f", "/org/gnome/shell/"])
                    .status();
                let _ = Command::new("dconf").arg("update").status();
                if reset.map(|s| s.success()).unwrap_or(false) {
                    ui_for_response.toast(&i18n("✓ Desktop settings reset. Restart Shell to apply."));
                } else {
                    ui_for_response.toast(&i18n("✗ Failed to reset"));
                }
            });
            dialog.present(Some(&ui.window));
        });
    }
    reset_row.add_suffix(&reset_btn);
    danger.add(&reset_row);
    page.add(&danger);
    page
}
