use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;
use std::cell::RefCell;

use crate::i18n::i18n;
use crate::ufw::stats;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct DashboardView {
        pub total_events_label: RefCell<Option<gtk::Label>>,
        pub blocked_events_label: RefCell<Option<gtk::Label>>,
        pub top_ips_group: RefCell<Option<adw::PreferencesGroup>>,
        pub top_ports_group: RefCell<Option<adw::PreferencesGroup>>,
        pub listening_group: RefCell<Option<adw::PreferencesGroup>>,
        pub log_list: RefCell<Option<gtk::ListBox>>,
        pub log_count_combo: RefCell<Option<adw::ComboRow>>,
        pub refresh_btn: RefCell<Option<gtk::Button>>,
        // Track added rows for each group so we can remove them on refresh
        pub top_ips_rows: RefCell<Vec<gtk::Widget>>,
        pub top_ports_rows: RefCell<Vec<gtk::Widget>>,
        pub listening_rows: RefCell<Vec<gtk::Widget>>,
        pub log_rows: RefCell<Vec<gtk::Widget>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for DashboardView {
        const NAME: &'static str = "DashboardView";
        type Type = super::DashboardView;
        type ParentType = adw::PreferencesPage;
    }

    impl ObjectImpl for DashboardView {
        fn constructed(&self) {
            self.parent_constructed();
            self.obj().setup_ui();
        }
    }

    impl WidgetImpl for DashboardView {}
    impl PreferencesPageImpl for DashboardView {}
}

glib::wrapper! {
    pub struct DashboardView(ObjectSubclass<imp::DashboardView>)
        @extends gtk::Widget, adw::PreferencesPage,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl DashboardView {
    pub fn new() -> Self {
        glib::Object::builder()
            .property("title", i18n("Dashboard"))
            .property("icon-name", "network-firewall-symbolic")
            .build()
    }

    fn setup_ui(&self) {
        let imp = self.imp();

        // Summary Cards
        let cards_box = gtk::Box::builder()
            .orientation(gtk::Orientation::Horizontal)
            .spacing(12)
            .homogeneous(true)
            .margin_bottom(12)
            .build();

        // Card 1: Total Events
        let total_card = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .css_classes(["card"])
            .build();
        let total_title = gtk::Label::builder()
            .label(i18n("Recent Events Parsed"))
            .css_classes(["dim-label", "caption"])
            .halign(gtk::Align::Start)
            .margin_start(16).margin_top(16).build();
        let total_events_label = gtk::Label::builder()
            .label("0")
            .css_classes(["title-1"])
            .halign(gtk::Align::Start)
            .margin_start(16).margin_bottom(16).build();
        total_card.append(&total_title);
        total_card.append(&total_events_label);

        // Card 2: Blocked Events
        let blocked_card = gtk::Box::builder()
            .orientation(gtk::Orientation::Vertical)
            .css_classes(["card"])
            .build();
        let blocked_title = gtk::Label::builder()
            .label(i18n("Blocked Events"))
            .css_classes(["dim-label", "caption", "error"])
            .halign(gtk::Align::Start)
            .margin_start(16).margin_top(16).build();
        let blocked_events_label = gtk::Label::builder()
            .label("0")
            .css_classes(["title-1", "error"])
            .halign(gtk::Align::Start)
            .margin_start(16).margin_bottom(16).build();
        blocked_card.append(&blocked_title);
        blocked_card.append(&blocked_events_label);

        cards_box.append(&total_card);
        cards_box.append(&blocked_card);
        
        let summary_group = adw::PreferencesGroup::builder().build();
        summary_group.add(&cards_box);
        self.add(&summary_group);

        // Top blocked connections
        let top_ips_group = adw::PreferencesGroup::builder()
            .title(i18n("Top Blocked Connections"))
            .build();
        self.add(&top_ips_group);

        // Top blocked ports
        let top_ports_group = adw::PreferencesGroup::builder()
            .title(i18n("Top Blocked Ports"))
            .build();
        self.add(&top_ports_group);

        // Listening ports
        let listening_group = adw::PreferencesGroup::builder()
            .title(i18n("Listening Ports"))
            .build();
        self.add(&listening_group);

        // Log viewer
        let log_group = adw::PreferencesGroup::builder()
            .title(i18n("Recent Blocked Events"))
            .build();

        let log_count_combo = adw::ComboRow::builder()
            .title(i18n("Show"))
            .model(&gtk::StringList::new(&["50", "100", "500", "1000"]))
            .build();
        log_count_combo.set_selected(0);
        log_group.add(&log_count_combo);

        let log_list = gtk::ListBox::builder()
            .css_classes(["boxed-list"])
            .build();
        log_group.add(&log_list);

        self.add(&log_group);

        // Refresh button (wrapped in a group)
        let refresh_btn = gtk::Button::builder()
            .label(i18n("Refresh"))
            .halign(gtk::Align::Center)
            .margin_top(12)
            .margin_bottom(12)
            .build();
        let refresh_group = adw::PreferencesGroup::builder().build();
        refresh_group.add(&refresh_btn);
        self.add(&refresh_group);

        // Connect signals
        let weak_self = self.downgrade();
        log_count_combo.connect_selected_notify(move |_| {
            if let Some(view) = weak_self.upgrade() {
                view.refresh_data();
            }
        });

        let weak_self = self.downgrade();
        refresh_btn.connect_clicked(move |_| {
            if let Some(view) = weak_self.upgrade() {
                view.refresh_data();
            }
        });

        *imp.total_events_label.borrow_mut() = Some(total_events_label);
        *imp.blocked_events_label.borrow_mut() = Some(blocked_events_label);
        *imp.top_ips_group.borrow_mut() = Some(top_ips_group);
        *imp.top_ports_group.borrow_mut() = Some(top_ports_group);
        *imp.listening_group.borrow_mut() = Some(listening_group);
        *imp.log_list.borrow_mut() = Some(log_list);
        *imp.log_count_combo.borrow_mut() = Some(log_count_combo);
        *imp.refresh_btn.borrow_mut() = Some(refresh_btn);
    }

    pub fn refresh_data(&self) {
        let imp = self.imp();
        let limit = match imp.log_count_combo.borrow().as_ref().unwrap().selected() {
            0 => 50,
            1 => 100,
            2 => 500,
            _ => 1000,
        };

        if let Some(btn) = imp.refresh_btn.borrow().as_ref() {
            btn.set_sensitive(false);
        }

        let weak_self = self.downgrade();
        glib::spawn_future_local(async move {
            let events = tokio::task::spawn_blocking(move || {
                stats::read_blocked_events(limit)
            })
            .await
            .unwrap();

            let listening = tokio::task::spawn_blocking(|| stats::read_listening_ports())
                .await
                .unwrap();

            if let Some(view) = weak_self.upgrade() {
                view.populate(events, listening);
            }
        });
    }

    fn populate(
        &self,
        events: Result<Vec<stats::BlockedEvent>, crate::ufw::types::UfwError>,
        listening: Result<Vec<stats::ListeningPort>, crate::ufw::types::UfwError>,
    ) {
        let imp = self.imp();

        let events = events.unwrap_or_default();
        let listening = listening.unwrap_or_default();
        let blocked_count = events.iter().filter(|e| e.action == "BLOCK").count();

        if let Some(label) = imp.total_events_label.borrow().as_ref() {
            label.set_text(&events.len().to_string());
        }
        if let Some(label) = imp.blocked_events_label.borrow().as_ref() {
            label.set_text(&blocked_count.to_string());
        }

        // Top blocked IPs
        if let Some(group) = imp.top_ips_group.borrow().as_ref() {
            for row in imp.top_ips_rows.borrow().iter() {
                group.remove(row);
            }
            imp.top_ips_rows.borrow_mut().clear();

            let top = stats::top_blocked_connections(&events, 7);
            if top.is_empty() {
                let row = adw::ActionRow::builder()
                    .title(i18n("No blocked connections in recent events"))
                    .build();
                group.add(&row);
                imp.top_ips_rows.borrow_mut().push(row.upcast::<gtk::Widget>());
            } else {
                let max_count = top.first().map(|(_, c)| *c).unwrap_or(1);
                for (ip, count) in &top {
                    let box_widget = gtk::Box::builder()
                        .orientation(gtk::Orientation::Vertical)
                        .spacing(4)
                        .margin_top(8)
                        .margin_bottom(8)
                        .margin_start(12)
                        .margin_end(12)
                        .build();

                    let label_box = gtk::Box::builder()
                        .orientation(gtk::Orientation::Horizontal)
                        .build();
                    let title = gtk::Label::builder().label(ip).halign(gtk::Align::Start).hexpand(true).build();
                    let count_label = gtk::Label::builder().label(&count.to_string()).css_classes(["dim-label"]).build();
                    label_box.append(&title);
                    label_box.append(&count_label);

                    let progress = gtk::ProgressBar::builder()
                        .fraction(*count as f64 / max_count as f64)
                        .build();
                    progress.add_css_class("error");

                    box_widget.append(&label_box);
                    box_widget.append(&progress);

                    let row = adw::ActionRow::builder().child(&box_widget).build();
                    group.add(&row);
                    imp.top_ips_rows.borrow_mut().push(row.upcast::<gtk::Widget>());
                }
            }
        }

        // Top targeted ports
        if let Some(group) = imp.top_ports_group.borrow().as_ref() {
            for row in imp.top_ports_rows.borrow().iter() {
                group.remove(row);
            }
            imp.top_ports_rows.borrow_mut().clear();

            let top = stats::top_blocked_ports(&events, 7);
            if top.is_empty() {
                let row = adw::ActionRow::builder()
                    .title(i18n("No blocked ports in recent events"))
                    .build();
                group.add(&row);
                imp.top_ports_rows.borrow_mut().push(row.upcast::<gtk::Widget>());
            } else {
                let max_count = top.first().map(|(_, c)| *c).unwrap_or(1);
                for (port, count) in &top {
                    let box_widget = gtk::Box::builder()
                        .orientation(gtk::Orientation::Vertical)
                        .spacing(4)
                        .margin_top(8)
                        .margin_bottom(8)
                        .margin_start(12)
                        .margin_end(12)
                        .build();

                    let label_box = gtk::Box::builder()
                        .orientation(gtk::Orientation::Horizontal)
                        .build();
                    let title = gtk::Label::builder().label(port).halign(gtk::Align::Start).hexpand(true).build();
                    let count_label = gtk::Label::builder().label(&count.to_string()).css_classes(["dim-label"]).build();
                    label_box.append(&title);
                    label_box.append(&count_label);

                    let progress = gtk::ProgressBar::builder()
                        .fraction(*count as f64 / max_count as f64)
                        .build();
                    progress.add_css_class("warning");

                    box_widget.append(&label_box);
                    box_widget.append(&progress);

                    let row = adw::ActionRow::builder().child(&box_widget).build();
                    group.add(&row);
                    imp.top_ports_rows.borrow_mut().push(row.upcast::<gtk::Widget>());
                }
            }
        }

        // Listening ports
        if let Some(group) = imp.listening_group.borrow().as_ref() {
            for row in imp.listening_rows.borrow().iter() {
                group.remove(row);
            }
            imp.listening_rows.borrow_mut().clear();

            if listening.is_empty() {
                let row = adw::ActionRow::builder()
                    .title(i18n("No listening ports detected"))
                    .build();
                group.add(&row);
                imp.listening_rows.borrow_mut().push(row.upcast::<gtk::Widget>());
            } else {
                for lp in &listening {
                    let proc_display = if lp.process.is_empty() {
                        String::new()
                    } else {
                        format!(" ({})", lp.process)
                    };
                    
                    let icon = gtk::Image::from_icon_name("network-server-symbolic");
                    
                    let row = adw::ActionRow::builder()
                        .title(&format!("{}/{} {}{}", lp.port, lp.protocol, lp.local_address, proc_display))
                        .build();
                    row.add_prefix(&icon);
                    
                    group.add(&row);
                    imp.listening_rows.borrow_mut().push(row.upcast::<gtk::Widget>());
                }
            }
        }

        // Log viewer
        if let Some(list) = imp.log_list.borrow().as_ref() {
            for row in imp.log_rows.borrow().iter() {
                list.remove(row);
            }
            imp.log_rows.borrow_mut().clear();

            for event in &events {
                let is_block = event.action == "BLOCK";
                let icon_name = if is_block { "security-high-symbolic" } else { "security-low-symbolic" };
                let icon = gtk::Image::from_icon_name(icon_name);
                if is_block {
                    icon.add_css_class("error");
                } else {
                    icon.add_css_class("success");
                }
                
                let row = adw::ActionRow::builder()
                    .title(&format!(
                        "{} → {} ({})",
                        event.src_ip,
                        event.dst_port,
                        event.protocol
                    ))
                    .subtitle(&event.timestamp)
                    .build();
                row.add_prefix(&icon);
                
                list.append(&row);
                imp.log_rows.borrow_mut().push(row.upcast::<gtk::Widget>());
            }
        }

        // Re-enable refresh
        if let Some(btn) = imp.refresh_btn.borrow().as_ref() {
            btn.set_sensitive(true);
        }
    }
}
