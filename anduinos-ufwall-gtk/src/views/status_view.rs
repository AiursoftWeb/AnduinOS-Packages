use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;
use std::cell::{Cell, RefCell};
use std::rc::Rc;

use crate::i18n::i18n;
use crate::ufw::show_error;
use crate::ufw::types::{UfwStatus, Policy, Direction};
use crate::ufw::backend;

mod imp {
    use super::*;

    pub struct StatusView {
        pub active_switch: RefCell<Option<adw::SwitchRow>>,
        pub policy_group: RefCell<Option<adw::PreferencesGroup>>,
        pub incoming_combo: RefCell<Option<adw::ComboRow>>,
        pub outgoing_combo: RefCell<Option<adw::ComboRow>>,
        pub logging_combo: RefCell<Option<adw::ComboRow>>,
        pub sw_in_flight: Rc<Cell<bool>>,
        pub in_in_flight: Rc<Cell<bool>>,
        pub out_in_flight: Rc<Cell<bool>>,
        pub log_in_flight: Rc<Cell<bool>>,
        pub sw_handler: RefCell<Option<glib::SignalHandlerId>>,
        pub in_handler: RefCell<Option<glib::SignalHandlerId>>,
        pub out_handler: RefCell<Option<glib::SignalHandlerId>>,
        pub log_handler: RefCell<Option<glib::SignalHandlerId>>,
    }

    impl Default for StatusView {
        fn default() -> Self {
            Self {
                active_switch: Default::default(),
                policy_group: Default::default(),
                incoming_combo: Default::default(),
                outgoing_combo: Default::default(),
                logging_combo: Default::default(),
                sw_in_flight: Rc::new(Cell::new(false)),
                in_in_flight: Rc::new(Cell::new(false)),
                out_in_flight: Rc::new(Cell::new(false)),
                log_in_flight: Rc::new(Cell::new(false)),
                sw_handler: Default::default(),
                in_handler: Default::default(),
                out_handler: Default::default(),
                log_handler: Default::default(),
            }
        }
    }

    #[glib::object_subclass]
    impl ObjectSubclass for StatusView {
        const NAME: &'static str = "StatusView";
        type Type = super::StatusView;
        type ParentType = adw::PreferencesPage;
    }

    impl ObjectImpl for StatusView {
        fn constructed(&self) {
            self.parent_constructed();
            self.obj().setup_ui();
        }
    }

    impl WidgetImpl for StatusView {}
    impl PreferencesPageImpl for StatusView {}
}

glib::wrapper! {
    pub struct StatusView(ObjectSubclass<imp::StatusView>)
        @extends gtk::Widget, adw::PreferencesPage,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl StatusView {
    pub fn new() -> Self {
        glib::Object::builder()
            .property("title", i18n("Status"))
            .property("icon-name", "security-high-symbolic")
            .build()
    }

    fn setup_ui(&self) {
        let imp = self.imp();

        let status_group = adw::PreferencesGroup::builder().build();

        let active_switch = adw::SwitchRow::builder()
            .title(i18n("Firewall"))
            .subtitle(i18n("Inactive"))
            .build();

        status_group.add(&active_switch);
        self.add(&status_group);

        let policy_group = adw::PreferencesGroup::builder()
            .title(i18n("Default Policies"))
            .sensitive(false)
            .build();

        let incoming_combo = adw::ComboRow::builder()
            .title(i18n("Incoming"))
            .model(&gtk::StringList::new(&[
                &i18n("Deny"),
                &i18n("Allow"),
                &i18n("Reject"),
            ]))
            .build();

        let outgoing_combo = adw::ComboRow::builder()
            .title(i18n("Outgoing"))
            .model(&gtk::StringList::new(&[
                &i18n("Deny"),
                &i18n("Allow"),
                &i18n("Reject"),
            ]))
            .build();

        policy_group.add(&incoming_combo);
        policy_group.add(&outgoing_combo);
        self.add(&policy_group);

        let logging_group = adw::PreferencesGroup::builder()
            .title(i18n("Logging"))
            .build();

        let logging_combo = adw::ComboRow::builder()
            .title(i18n("Log Level"))
            .model(&gtk::StringList::new(&[
                &i18n("Off"),
                &i18n("Low"),
                &i18n("Medium"),
                &i18n("High"),
                &i18n("Full"),
            ]))
            .build();

        logging_group.add(&logging_combo);
        self.add(&logging_group);

        // ── Signal handlers with in_flight guard against concurrent clicks ──

        let sw_in_flight = imp.sw_in_flight.clone();
        let weak_view = self.downgrade();
        let sw_handler = active_switch.connect_active_notify(move |sw| {
            if sw_in_flight.get() {
                // Revert — another operation is already in progress
                sw.set_active(!sw.is_active());
                return;
            }
            sw_in_flight.set(true);
            let active = sw.is_active();
            let switch_clone = sw.clone();
            let v = weak_view.clone();
            let f = sw_in_flight.clone();
            glib::spawn_future_local(async move {
                switch_clone.set_sensitive(false);
                let result = tokio::task::spawn_blocking(move || {
                    backend::set_enabled(active)
                }).await.unwrap();
                f.set(false); // unlock BEFORE refresh to prevent revert
                if let Err(e) = &result {
                    show_error(&switch_clone, &i18n("Error"), &e.to_string());
                }
                if let Some(view) = v.upgrade() {
                    view.force_refresh();
                }
                switch_clone.set_sensitive(true);
            });
        });

        let in_in_flight = imp.in_in_flight.clone();
        let w2 = self.downgrade();
        let in_handler = incoming_combo.connect_selected_notify(move |combo| {
            if in_in_flight.get() {
                let prev = combo.selected();
                combo.set_selected(1 - prev); // crude revert
                return;
            }
            in_in_flight.set(true);
            let policy = Policy::from_index(combo.selected());
            let combo_clone = combo.clone();
            let v = w2.clone();
            let f = in_in_flight.clone();
            glib::spawn_future_local(async move {
                combo_clone.set_sensitive(false);
                let _ = tokio::task::spawn_blocking(move || {
                    backend::set_default_policy(Direction::In, policy)
                }).await.unwrap();
                f.set(false);
                if let Some(view) = v.upgrade() {
                    view.force_refresh();
                }
                combo_clone.set_sensitive(true);
            });
        });

        let out_in_flight = imp.out_in_flight.clone();
        let w3 = self.downgrade();
        let out_handler = outgoing_combo.connect_selected_notify(move |combo| {
            if out_in_flight.get() { return; }
            out_in_flight.set(true);
            let policy = Policy::from_index(combo.selected());
            let combo_clone = combo.clone();
            let v = w3.clone();
            let f = out_in_flight.clone();
            glib::spawn_future_local(async move {
                combo_clone.set_sensitive(false);
                let _ = tokio::task::spawn_blocking(move || {
                    backend::set_default_policy(Direction::Out, policy)
                }).await.unwrap();
                f.set(false);
                if let Some(view) = v.upgrade() {
                    view.force_refresh();
                }
                combo_clone.set_sensitive(true);
            });
        });

        let log_in_flight = imp.log_in_flight.clone();
        let w4 = self.downgrade();
        let log_handler = logging_combo.connect_selected_notify(move |combo| {
            if log_in_flight.get() { return; }
            log_in_flight.set(true);
            let level = match combo.selected() {
                0 => "off", 1 => "low", 2 => "medium", 3 => "high", _ => "full",
            };
            let combo_clone = combo.clone();
            let v = w4.clone();
            let f = log_in_flight.clone();
            glib::spawn_future_local(async move {
                combo_clone.set_sensitive(false);
                let _ = tokio::task::spawn_blocking(move || {
                    backend::set_logging(level)
                }).await.unwrap();
                f.set(false);
                if let Some(view) = v.upgrade() {
                    view.force_refresh();
                }
                combo_clone.set_sensitive(true);
            });
        });

        *imp.sw_handler.borrow_mut() = Some(sw_handler);
        *imp.in_handler.borrow_mut() = Some(in_handler);
        *imp.out_handler.borrow_mut() = Some(out_handler);
        *imp.log_handler.borrow_mut() = Some(log_handler);
        *imp.active_switch.borrow_mut() = Some(active_switch);
        *imp.policy_group.borrow_mut() = Some(policy_group);
        *imp.incoming_combo.borrow_mut() = Some(incoming_combo);
        *imp.outgoing_combo.borrow_mut() = Some(outgoing_combo);
        *imp.logging_combo.borrow_mut() = Some(logging_combo);
    }

    /// Force a status re-read and UI update after an operation completes.
    fn force_refresh(&self) {
        let weak_self = self.downgrade();
        let imp = self.imp();
        // Disable all widgets while refreshing
        if let Some(sw) = imp.active_switch.borrow().as_ref() { sw.set_sensitive(false); }
        if let Some(c) = imp.incoming_combo.borrow().as_ref() { c.set_sensitive(false); }
        if let Some(c) = imp.outgoing_combo.borrow().as_ref() { c.set_sensitive(false); }
        if let Some(c) = imp.logging_combo.borrow().as_ref() { c.set_sensitive(false); }

        glib::spawn_future_local(async move {
            let status = tokio::task::spawn_blocking(|| {
                backend::read_status()
            }).await.unwrap();
            if let (Ok(status), Some(view)) = (status, weak_self.upgrade()) {
                view.apply_status(&status);
            }
        });
    }

    /// Apply status to widgets. Uses block_signal to prevent recursion.
    fn apply_status(&self, status: &UfwStatus) {
        let imp = self.imp();
        if let (Some(sw), Some(h)) = (imp.active_switch.borrow().as_ref(), imp.sw_handler.borrow().as_ref()) {
            sw.block_signal(h);
            sw.set_active(status.active);
            sw.set_sensitive(true);
            let sub = if status.active { i18n("Active") } else { i18n("Inactive") };
            sw.set_subtitle(&sub);
            sw.unblock_signal(h);
        }
        if let Some(g) = imp.policy_group.borrow().as_ref() {
            g.set_sensitive(status.active);
        }
        if let (Some(c), Some(h)) = (imp.incoming_combo.borrow().as_ref(), imp.in_handler.borrow().as_ref()) {
            c.block_signal(h);
            c.set_selected(status.default_incoming.index());
            c.set_sensitive(true);
            c.unblock_signal(h);
        }
        if let (Some(c), Some(h)) = (imp.outgoing_combo.borrow().as_ref(), imp.out_handler.borrow().as_ref()) {
            c.block_signal(h);
            c.set_selected(status.default_outgoing.index());
            c.set_sensitive(true);
            c.unblock_signal(h);
        }
        if let (Some(c), Some(h)) = (imp.logging_combo.borrow().as_ref(), imp.log_handler.borrow().as_ref()) {
            c.block_signal(h);
            let idx = match status.logging.to_lowercase().as_str() {
                "low" => 1, "medium" => 2, "high" => 3, "full" => 4, _ => 0,
            };
            c.set_selected(idx);
            c.set_sensitive(status.active);
            c.unblock_signal(h);
        }
    }

    /// Called from window's refresh_views (file monitor).
    pub fn update(&self, status: &UfwStatus) {
        self.apply_status(status);
    }
}
