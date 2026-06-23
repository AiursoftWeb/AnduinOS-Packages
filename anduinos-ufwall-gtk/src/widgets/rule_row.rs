use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::glib;
use std::cell::RefCell;

use crate::i18n::i18n;
use crate::ufw::types::UfwRule;
use crate::ufw::backend;
use crate::ufw::show_error;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct RuleRow {
        pub rule_number: RefCell<u32>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for RuleRow {
        const NAME: &'static str = "RuleRow";
        type Type = super::RuleRow;
        type ParentType = adw::ActionRow;
    }

    impl ObjectImpl for RuleRow {}
    impl WidgetImpl for RuleRow {}
    impl ListBoxRowImpl for RuleRow {}
    impl PreferencesRowImpl for RuleRow {}
    impl ActionRowImpl for RuleRow {}
}

glib::wrapper! {
    pub struct RuleRow(ObjectSubclass<imp::RuleRow>)
        @extends gtk::Widget, gtk::ListBoxRow, adw::PreferencesRow, adw::ActionRow,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Actionable;
}

impl RuleRow {
    pub fn new(rule: &UfwRule, on_edit: Box<dyn Fn(u32) + 'static>) -> Self {
        let obj: Self = glib::Object::builder()
            .property("title", &rule.title())
            .property("subtitle", &rule.subtitle())
            .build();

        *obj.imp().rule_number.borrow_mut() = rule.number;

        // Edit button
        let edit_btn = gtk::Button::builder()
            .icon_name("document-edit-symbolic")
            .valign(gtk::Align::Center)
            .build();
        let num = rule.number;
        edit_btn.connect_clicked(move |_| {
            on_edit(num);
        });
        obj.add_suffix(&edit_btn);

        // Delete button
        let delete_btn = gtk::Button::builder()
            .icon_name("user-trash-symbolic")
            .valign(gtk::Align::Center)
            .css_classes(["destructive-action"])
            .build();

        let weak_obj = obj.downgrade();
        delete_btn.connect_clicked(move |btn| {
            if let Some(row) = weak_obj.upgrade() {
                row.confirm_delete(btn);
            }
        });

        obj.add_suffix(&delete_btn);
        obj
    }

    fn confirm_delete(&self, btn: &gtk::Button) {
        let num = *self.imp().rule_number.borrow();
        let dialog = adw::MessageDialog::builder()
            .heading(i18n("Delete Rule"))
            .body(format!("{}: {}", i18n("Delete rule"), num))
            .modal(true)
            .build();
        dialog.add_response("cancel", &i18n("Cancel"));
        dialog.add_response("delete", &i18n("Delete"));
        dialog.set_response_appearance("delete", adw::ResponseAppearance::Destructive);
        dialog.set_default_response(Some("cancel"));
        dialog.set_close_response("cancel");

        let weak_btn = btn.downgrade();
        let weak_btn2 = weak_btn.clone();
        dialog.connect_response(None, move |dialog, response| {
            if response == "delete" {
                if let Some(btn) = weak_btn2.upgrade() {
                    btn.set_sensitive(false);
                }
                let weak_btn3 = weak_btn2.clone();
                glib::spawn_future_local(async move {
                    let result = tokio::task::spawn_blocking(move || {
                        backend::delete_rule(num)
                    }).await.unwrap();
                    if let Err(e) = result {
                        if let Some(btn) = weak_btn3.upgrade() {
                            show_error(&btn, &i18n("Error"), &e.to_string());
                            btn.set_sensitive(true);
                        }
                    }
                });
            }
            dialog.close();
        });

        if let Some(root) = self.root() {
            if let Some(window) = root.downcast_ref::<gtk::Window>() {
                dialog.set_transient_for(Some(window));
            }
        }
        dialog.present();
    }
}
