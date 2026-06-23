use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;
use std::cell::{Cell, RefCell};
use std::collections::HashSet;
use std::rc::Rc;

use crate::i18n::i18n;
use crate::ufw::show_error;
use crate::ufw::types::{AppProfile, UfwRule};
use crate::ufw::backend;

mod imp {
    use super::*;

    pub struct ProfilesView {
        pub stack: RefCell<Option<gtk::Stack>>,
        pub profiles_group: RefCell<Option<adw::PreferencesGroup>>,
        pub added_rows: RefCell<Vec<gtk::Widget>>,
        pub current_rules: RefCell<Vec<UfwRule>>,
        pub is_updating: Rc<Cell<bool>>,
        pub quick_port_entry: RefCell<Option<adw::EntryRow>>,
        /// Profiles currently being toggled (prevents race conditions).
        pub in_flight: Rc<RefCell<HashSet<String>>>,
    }

    impl Default for ProfilesView {
        fn default() -> Self {
            Self {
                stack: Default::default(),
                profiles_group: Default::default(),
                added_rows: Default::default(),
                current_rules: Default::default(),
                is_updating: Rc::new(Cell::new(false)),
                quick_port_entry: Default::default(),
                in_flight: Rc::new(RefCell::new(HashSet::new())),
            }
        }
    }

    #[glib::object_subclass]
    impl ObjectSubclass for ProfilesView {
        const NAME: &'static str = "ProfilesView";
        type Type = super::ProfilesView;
        type ParentType = adw::Bin;
    }

    impl ObjectImpl for ProfilesView {
        fn constructed(&self) {
            self.parent_constructed();
            self.obj().setup_ui();
        }
    }
    
    impl WidgetImpl for ProfilesView {}
    impl BinImpl for ProfilesView {}
}

glib::wrapper! {
    pub struct ProfilesView(ObjectSubclass<imp::ProfilesView>)
        @extends gtk::Widget, adw::Bin,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl ProfilesView {
    pub fn new() -> Self {
        glib::Object::builder()
            .build()
    }

    fn setup_ui(&self) {
        let imp = self.imp();

        let page = adw::PreferencesPage::new();
        let profiles_group = adw::PreferencesGroup::builder()
            .title(i18n("Application Profiles"))
            .description(i18n("Allow applications with predefined rules."))
            .build();

        // Quick-add row: manual port entry at top of profiles
        let quick_add = adw::ActionRow::builder()
            .title(i18n("Quick Allow Port"))
            .subtitle(i18n("e.g. 80,443/tcp or 8080"))
            .build();

        let port_entry = adw::EntryRow::builder()
            .title(i18n("Port / Protocol"))
            .build();
        quick_add.add_prefix(&port_entry);

        let allow_btn = gtk::Button::builder()
            .label(i18n("Allow"))
            .css_classes(["suggested-action"])
            .valign(gtk::Align::Center)
            .build();
        quick_add.add_suffix(&allow_btn);

        profiles_group.add(&quick_add);
        page.add(&profiles_group);

        // Connect quick-add button
        let weak_port_entry = port_entry.downgrade();
        let weak_self = self.downgrade();
        allow_btn.connect_clicked(move |_| {
            if let (Some(entry), Some(view)) = (weak_port_entry.upgrade(), weak_self.upgrade()) {
                view.on_quick_add(&entry);
            }
        });

        // Enter key in entry also triggers add
        let weak_self2 = self.downgrade();
        port_entry.connect_activate(move |entry| {
            if let Some(view) = weak_self2.upgrade() {
                view.on_quick_add(entry);
            }
        });

        let empty_state = adw::StatusPage::builder()
            .title(i18n("No Profiles"))
            .description(i18n("No application profiles found"))
            .icon_name("applications-system-symbolic")
            .build();

        let stack = gtk::Stack::builder()
            .transition_type(gtk::StackTransitionType::Crossfade)
            .build();
        stack.add_named(&page, Some("profiles"));
        stack.add_named(&empty_state, Some("empty"));

        self.set_child(Some(&stack));

        *imp.quick_port_entry.borrow_mut() = Some(port_entry);
        *imp.profiles_group.borrow_mut() = Some(profiles_group);
        *imp.stack.borrow_mut() = Some(stack);
    }

    fn on_quick_add(&self, entry: &adw::EntryRow) {
        let port = entry.text().to_string();
        if port.trim().is_empty() {
            return;
        }

        let params = crate::ufw::types::RuleParams {
            port: port.trim().to_string(),
            action: crate::ufw::types::Action::Allow,
            direction: None,
            protocol: None,
            from: None,
            to: None,
            interface: None,
            comment: None,
            insert_position: None,
        };

        entry.set_sensitive(false);
        let weak_entry = entry.downgrade();
        let weak_self = self.downgrade();

        glib::spawn_future_local(async move {
            let result = tokio::task::spawn_blocking(move || {
                backend::add_rule(&params)
            }).await.unwrap();

            if let Some(entry) = weak_entry.upgrade() {
                entry.set_sensitive(true);
                if result.is_ok() {
                    entry.set_text("");
                } else if let Err(e) = result {
                    if let Some(view) = weak_self.upgrade() {
                        show_error(&view, &i18n("Error"), &e.to_string());
                    }
                }
            }
        });
    }

    pub fn update_rules(&self, rules: &[UfwRule]) {
        let imp = self.imp();
        *imp.current_rules.borrow_mut() = rules.to_vec();
    }

    pub fn update_profiles(&self, profiles: Vec<AppProfile>) {
        let imp = self.imp();
        imp.is_updating.set(true);

        if let Some(group) = imp.profiles_group.borrow().as_ref() {
            for row in imp.added_rows.borrow().iter() {
                group.remove(row);
            }
            imp.added_rows.borrow_mut().clear();

            if profiles.is_empty() {
                if let Some(stack) = imp.stack.borrow().as_ref() {
                    stack.set_visible_child_name("empty");
                }
            } else {
                if let Some(stack) = imp.stack.borrow().as_ref() {
                    stack.set_visible_child_name("profiles");
                }

                let rules = imp.current_rules.borrow();

                for profile in profiles {
                    let is_allowed = backend::is_app_allowed(&rules, &profile);
                    
                    let switch = adw::SwitchRow::builder()
                        .title(&profile.title)
                        .subtitle(&format!("{} ({})", profile.description, profile.ports))
                        .active(is_allowed)
                        .build();

                    let is_updating = imp.is_updating.clone();
                    let profile_ports = profile.ports.clone();
                    let in_flight = imp.in_flight.clone();
                    switch.connect_active_notify(move |sw| {
                        if is_updating.get() { return; }
                        let active = sw.is_active();
                        let ports_clone = profile_ports.clone();

                        // Prevent concurrent toggles on the same profile
                        if in_flight.borrow().contains(&ports_clone) {
                            // Revert the switch immediately — already being toggled
                            let was = !active;
                            if is_updating.get() { return; }
                            is_updating.set(true);
                            sw.set_active(was);
                            is_updating.set(false);
                            return;
                        }
                        in_flight.borrow_mut().insert(ports_clone.clone());

                        let sw_clone = sw.clone();
                        let in_flight_clone = in_flight.clone();
                        let ports_for_cleanup = ports_clone.clone();
                        glib::spawn_future_local(glib::clone!(
                            #[weak] sw_clone,
                            async move {
                                sw_clone.set_sensitive(false);

                                let result = tokio::task::spawn_blocking(move || {
                                    if active {
                                        backend::allow_app(&ports_clone)
                                    } else {
                                        backend::delete_app(&ports_clone)
                                    }
                                }).await.unwrap();

                                // Remove from in-flight tracking
                                in_flight_clone.borrow_mut().remove(&ports_for_cleanup);

                                if let Err(e) = result {
                                    show_error(&sw_clone, &i18n("Error"), &e.to_string());
                                }
                                // Don't re-enable — file monitor will rebuild with fresh state
                            }
                        ));
                    });

                    group.add(&switch);
                    imp.added_rows.borrow_mut().push(switch.upcast::<gtk::Widget>());
                }
            }
        }

        imp.is_updating.set(false);
    }
}

impl Default for ProfilesView {
    fn default() -> Self {
        Self::new()
    }
}
