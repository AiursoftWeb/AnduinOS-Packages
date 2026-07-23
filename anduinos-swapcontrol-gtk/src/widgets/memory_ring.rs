use adw::subclass::prelude::*;
use gtk::cairo;
use gtk::glib;
use gtk::prelude::*;
use std::cell::RefCell;

#[derive(Clone, Default)]
pub struct Segment {
    pub label: String,
    pub value: f64,
    pub color: (f64, f64, f64),
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct MemoryRing {
        pub segments: RefCell<Vec<Segment>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for MemoryRing {
        const NAME: &'static str = "MemoryRing";
        type Type = super::MemoryRing;
        type ParentType = gtk::DrawingArea;
    }

    impl ObjectImpl for MemoryRing {}
    impl WidgetImpl for MemoryRing {}
    impl DrawingAreaImpl for MemoryRing {}
}

glib::wrapper! {
    pub struct MemoryRing(ObjectSubclass<imp::MemoryRing>)
        @extends gtk::Widget, gtk::DrawingArea,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl MemoryRing {
    pub fn new() -> Self {
        let obj: Self = glib::Object::builder().build();
        obj.set_content_height(240);
        obj.set_content_width(240);
        obj.set_halign(gtk::Align::Center);
        obj.set_valign(gtk::Align::Center);

        let weak = obj.downgrade();
        obj.set_draw_func(move |_, cr, w, h| {
            if let Some(this) = weak.upgrade() {
                Self::draw(cr, w as f64, h as f64, &this.imp().segments.borrow());
            }
        });
        obj
    }

    pub fn set_segments(&self, segments: Vec<Segment>) {
        *self.imp().segments.borrow_mut() = segments;
        self.queue_draw();
    }

    fn draw(cr: &cairo::Context, w: f64, h: f64, segments: &[Segment]) {
        let cx = w / 2.0;
        let cy = h / 2.0;
        let radius = (w.min(h) / 2.0 - 20.0).max(30.0);
        let ring_width = 28.0;

        let total: f64 = segments.iter().map(|s| s.value).sum();
        if total <= 0.0 || segments.is_empty() {
            // Empty ring
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.3);
            cr.set_line_width(ring_width);
            cr.arc(cx, cy, radius, 0.0, 2.0 * std::f64::consts::PI);
            cr.stroke().ok();
            return;
        }

        let mut start_angle = -std::f64::consts::PI / 2.0;

        for seg in segments {
            let sweep = (seg.value / total) * 2.0 * std::f64::consts::PI;
            if sweep <= 0.001 {
                continue;
            }

            cr.set_source_rgb(seg.color.0, seg.color.1, seg.color.2);
            cr.set_line_width(ring_width);
            cr.arc(cx, cy, radius, start_angle, start_angle + sweep);
            cr.stroke().ok();

            start_angle += sweep;
        }

        // Center text
        let total_gb = total / (1024.0 * 1024.0 * 1024.0);
        cr.set_source_rgb(1.0, 1.0, 1.0);
        cr.select_font_face("Sans", cairo::FontSlant::Normal, cairo::FontWeight::Bold);
        cr.set_font_size(18.0);

        let text = format!("{:.1} GiB", total_gb);
        let extents = cr.text_extents(&text).unwrap();
        cr.move_to(cx - extents.width() / 2.0, cy + extents.height() / 2.0);
        cr.show_text(&text).ok();
    }
}
