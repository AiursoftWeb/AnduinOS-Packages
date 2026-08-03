use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use adw::prelude::*;
use gtk::{cairo, glib};

use anduinos_timeback::lineage::{LineageNode, LineageRelation, SystemLineage};
use anduinos_timeback::model::{DeploymentId, DeploymentKind};

use crate::i18n::{i18n, i18n_fmt};

const MAX_VISUAL_NODES: usize = 80;
const NODE_WIDTH: i32 = 224;
const NODE_HEIGHT: i32 = 78;
const LANE_GAP: i32 = 44;
const ROW_GAP: i32 = 30;
const PADDING: i32 = 28;

#[derive(Clone)]
struct VisualNode {
    id: Option<DeploymentId>,
    parent: Option<DeploymentId>,
    title: String,
    subtitle: String,
    badge: String,
    lane: usize,
    row: usize,
    available: bool,
    current: bool,
}

struct GraphLayout {
    nodes: Vec<VisualNode>,
    width: i32,
    height: i32,
    truncated: usize,
}

#[derive(Clone, Debug)]
pub struct HistorySelection {
    pub recovery_point_id: Option<DeploymentId>,
    pub title: String,
    pub available: bool,
    pub current: bool,
}

pub fn build(
    history: &SystemLineage,
    on_select: impl Fn(HistorySelection) + 'static,
) -> gtk::Widget {
    build_layout_widget(layout(history), on_select)
}

pub fn build_demo(on_select: impl Fn(HistorySelection) + 'static) -> gtk::Widget {
    let first = "00000000-0000-4000-8000-000000000001"
        .parse()
        .expect("demo recovery-point ID must be valid");
    let second = "00000000-0000-4000-8000-000000000002"
        .parse()
        .expect("demo recovery-point ID must be valid");
    let branch = "00000000-0000-4000-8000-000000000003"
        .parse()
        .expect("demo recovery-point ID must be valid");
    let now = chrono::Utc::now();
    let history = SystemLineage {
        schema_version: anduinos_timeback::lineage::LINEAGE_SCHEMA_VERSION,
        revision: 1,
        current_head_id: Some(second),
        nodes: vec![
            demo_node(
                first,
                None,
                now - chrono::Duration::hours(8),
                &i18n("Before system update"),
                DeploymentKind::Automatic,
            ),
            demo_node(
                second,
                Some(first),
                now - chrono::Duration::hours(7),
                &i18n("After system update"),
                DeploymentKind::Automatic,
            ),
            demo_node(
                branch,
                Some(first),
                now - chrono::Duration::hours(3),
                &i18n("Graphics experiment"),
                DeploymentKind::Manual,
            ),
        ],
        activations: Vec::new(),
    };
    build(&history, on_select)
}

fn demo_node(
    id: DeploymentId,
    parent_id: Option<DeploymentId>,
    created_at: chrono::DateTime<chrono::Utc>,
    title: &str,
    kind: DeploymentKind,
) -> LineageNode {
    LineageNode {
        recovery_point_id: id,
        parent_id,
        relation: LineageRelation::Exact,
        created_at,
        kind,
        title: title.to_string(),
        snapshot_available: true,
        removed_at: None,
    }
}

fn layout(history: &SystemLineage) -> GraphLayout {
    let mut exact = history
        .nodes
        .iter()
        .filter(|node| node.relation == LineageRelation::Exact)
        .collect::<Vec<_>>();
    exact.sort_by_key(|node| (node.created_at, node.recovery_point_id.to_string()));

    let truncated = exact.len().saturating_sub(MAX_VISUAL_NODES);
    if truncated > 0 {
        exact.drain(0..truncated);
    }
    if let Some(head) = history.current_head_id {
        if !exact.iter().any(|node| node.recovery_point_id == head) {
            if let Some(head_node) = history
                .nodes
                .iter()
                .find(|node| node.recovery_point_id == head)
                .filter(|node| node.relation == LineageRelation::Exact)
            {
                if exact.len() == MAX_VISUAL_NODES {
                    exact.remove(0);
                }
                exact.push(head_node);
                exact.sort_by_key(|node| (node.created_at, node.recovery_point_id.to_string()));
            }
        }
    }

    let visible_ids = exact
        .iter()
        .map(|node| node.recovery_point_id)
        .collect::<HashSet<_>>();
    let mut lanes = HashMap::<DeploymentId, usize>::new();
    let mut child_counts = HashMap::<DeploymentId, usize>::new();
    let mut next_lane = 0usize;
    let mut nodes = Vec::with_capacity(exact.len() + 1);

    for node in exact {
        let visible_parent = node.parent_id.filter(|parent| visible_ids.contains(parent));
        let lane = if let Some(parent) = visible_parent {
            let parent_lane = lanes.get(&parent).copied().unwrap_or_else(|| {
                let lane = next_lane;
                next_lane += 1;
                lane
            });
            let children = child_counts.entry(parent).or_default();
            let lane = if *children == 0 {
                parent_lane
            } else {
                let lane = next_lane;
                next_lane += 1;
                lane
            };
            *children += 1;
            lane
        } else {
            let lane = next_lane;
            next_lane += 1;
            lane
        };
        lanes.insert(node.recovery_point_id, lane);
        let time = node
            .created_at
            .with_timezone(&chrono::Local)
            .format("%Y-%m-%d %H:%M")
            .to_string();
        let kind = match node.kind {
            DeploymentKind::Automatic | DeploymentKind::AptPre | DeploymentKind::AptPost => {
                i18n("Automatic")
            }
            DeploymentKind::PreRollback => i18n("Safety Point"),
            DeploymentKind::Factory => i18n("Factory"),
            DeploymentKind::Manual => i18n("Manual"),
        };
        nodes.push(VisualNode {
            id: Some(node.recovery_point_id),
            parent: visible_parent,
            title: node.title.clone(),
            subtitle: format!("{time} · {kind}"),
            badge: if history.current_head_id == Some(node.recovery_point_id) {
                i18n("Current Branch Base")
            } else if node.snapshot_available {
                i18n("Available")
            } else {
                i18n("History Only")
            },
            lane,
            row: nodes.len(),
            available: node.snapshot_available,
            current: false,
        });
    }

    let head_lane = history
        .current_head_id
        .and_then(|head| lanes.get(&head).copied())
        .unwrap_or_else(|| {
            let lane = next_lane;
            next_lane += 1;
            lane
        });
    let current_lane = if history
        .current_head_id
        .and_then(|head| child_counts.get(&head))
        .is_some_and(|children| *children > 0)
    {
        let lane = next_lane;
        next_lane += 1;
        lane
    } else {
        head_lane
    };
    nodes.push(VisualNode {
        id: None,
        parent: history
            .current_head_id
            .filter(|head| visible_ids.contains(head)),
        title: i18n("Current System — You Are Here"),
        subtitle: i18n("Newer changes continue from the recovery point above"),
        badge: i18n("You Are Here"),
        lane: current_lane,
        row: nodes.len(),
        available: true,
        current: true,
    });

    let lane_count = next_lane.max(1);
    let width = PADDING * 2
        + i32::try_from(lane_count).unwrap_or(i32::MAX / 2) * NODE_WIDTH
        + i32::try_from(lane_count.saturating_sub(1)).unwrap_or_default() * LANE_GAP;
    let height = PADDING * 2
        + i32::try_from(nodes.len()).unwrap_or(i32::MAX / 2) * NODE_HEIGHT
        + i32::try_from(nodes.len().saturating_sub(1)).unwrap_or_default() * ROW_GAP;
    GraphLayout {
        nodes,
        width,
        height,
        truncated,
    }
}

fn build_layout_widget(
    layout: GraphLayout,
    on_select: impl Fn(HistorySelection) + 'static,
) -> gtk::Widget {
    let outer = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(10)
        .build();
    let on_select = Rc::new(on_select);
    let selected_card = Rc::new(RefCell::new(None::<gtk::Button>));
    let current_lane = layout
        .nodes
        .iter()
        .find(|node| node.current)
        .map(|node| node.lane)
        .unwrap_or_default();
    let fixed = gtk::Fixed::builder()
        .width_request(layout.width)
        .height_request(layout.height)
        .build();
    let drawing = gtk::DrawingArea::builder()
        .content_width(layout.width)
        .content_height(layout.height)
        .build();
    let edges = layout
        .nodes
        .iter()
        .filter_map(|node| {
            node.parent.map(|parent| {
                let parent_node = layout
                    .nodes
                    .iter()
                    .find(|candidate| candidate.id == Some(parent));
                (parent_node.cloned(), node.clone())
            })
        })
        .filter_map(|(parent, child)| parent.map(|parent| (parent, child)))
        .collect::<Vec<_>>();
    drawing.set_draw_func(move |_, context, _, _| draw_edges(context, &edges));
    fixed.put(&drawing, 0.0, 0.0);

    for node in &layout.nodes {
        let card = history_card(node);
        let on_select = on_select.clone();
        let selected_card = selected_card.clone();
        let selection = HistorySelection {
            recovery_point_id: node.id,
            title: node.title.clone(),
            available: node.available,
            current: node.current,
        };
        card.connect_clicked(move |card| {
            if let Some(previous) = selected_card.replace(Some(card.clone())) {
                previous.remove_css_class("history-selected");
            }
            card.add_css_class("history-selected");
            on_select(selection.clone());
        });
        fixed.put(
            &card,
            f64::from(node_x(node.lane)),
            f64::from(node_y(node.row)),
        );
    }

    let scroller = gtk::ScrolledWindow::builder()
        .child(&fixed)
        .height_request(460)
        .min_content_height(320)
        .max_content_height(560)
        .hscrollbar_policy(gtk::PolicyType::Automatic)
        .vscrollbar_policy(gtk::PolicyType::Automatic)
        .css_classes(["history-graph"])
        .build();
    let initially_positioned = Rc::new(Cell::new(false));
    scroller.connect_map(move |scroller| {
        if initially_positioned.replace(true) {
            return;
        }
        let scroller = scroller.clone();
        glib::idle_add_local_once(move || {
            let vertical = scroller.vadjustment();
            vertical.set_value((vertical.upper() - vertical.page_size()).max(vertical.lower()));
            let horizontal = scroller.hadjustment();
            let current_center = f64::from(node_x(current_lane) + NODE_WIDTH / 2);
            let target = current_center - horizontal.page_size() / 2.0;
            horizontal.set_value(target.clamp(
                horizontal.lower(),
                (horizontal.upper() - horizontal.page_size()).max(horizontal.lower()),
            ));
        });
    });
    outer.append(&scroller);
    if layout.truncated > 0 {
        outer.append(
            &gtk::Label::builder()
                .label(i18n_fmt(
                    &i18n("Showing the newest branch points · {0} older points are hidden"),
                    &[&layout.truncated.to_string()],
                ))
                .halign(gtk::Align::Start)
                .wrap(true)
                .xalign(0.0)
                .css_classes(["caption", "warning"])
                .build(),
        );
    }
    outer.upcast()
}

fn history_card(node: &VisualNode) -> gtk::Button {
    let content = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(3)
        .margin_start(12)
        .margin_end(12)
        .margin_top(9)
        .margin_bottom(9)
        .build();
    content.append(
        &gtk::Label::builder()
            .label(&node.title)
            .halign(gtk::Align::Start)
            .xalign(0.0)
            .ellipsize(gtk::pango::EllipsizeMode::End)
            .css_classes(["heading"])
            .build(),
    );
    content.append(
        &gtk::Label::builder()
            .label(&node.subtitle)
            .halign(gtk::Align::Start)
            .xalign(0.0)
            .ellipsize(gtk::pango::EllipsizeMode::End)
            .css_classes(["caption", "dim-label"])
            .build(),
    );
    content.append(
        &gtk::Label::builder()
            .label(&node.badge)
            .halign(gtk::Align::Start)
            .xalign(0.0)
            .css_classes(if node.current {
                ["caption", "success"]
            } else if node.available {
                ["caption", "accent"]
            } else {
                ["caption", "dim-label"]
            })
            .build(),
    );
    gtk::Button::builder()
        .child(&content)
        .width_request(NODE_WIDTH)
        .height_request(NODE_HEIGHT)
        .tooltip_text(i18n("Select this history point"))
        .css_classes(if node.current {
            ["history-node", "history-current"]
        } else {
            ["history-node", "history-point"]
        })
        .build()
}

fn draw_edges(context: &cairo::Context, edges: &[(VisualNode, VisualNode)]) {
    context.set_line_width(3.0);
    context.set_line_cap(cairo::LineCap::Round);
    context.set_line_join(cairo::LineJoin::Round);
    for (parent, child) in edges {
        let start_x = f64::from(node_x(parent.lane) + NODE_WIDTH / 2);
        let start_y = f64::from(node_y(parent.row) + NODE_HEIGHT);
        let end_x = f64::from(node_x(child.lane) + NODE_WIDTH / 2);
        let end_y = f64::from(node_y(child.row));
        let middle_y = start_y + (end_y - start_y) / 2.0;
        if child.current {
            context.set_source_rgba(0.20, 0.72, 0.38, 0.92);
        } else {
            context.set_source_rgba(0.21, 0.52, 0.89, 0.72);
        }
        context.move_to(start_x, start_y);
        context.line_to(start_x, middle_y);
        context.line_to(end_x, middle_y);
        context.line_to(end_x, end_y);
        let _ = context.stroke();
    }
}

fn node_x(lane: usize) -> i32 {
    PADDING + i32::try_from(lane).unwrap_or_default() * (NODE_WIDTH + LANE_GAP)
}

fn node_y(row: usize) -> i32 {
    PADDING + i32::try_from(row).unwrap_or_default() * (NODE_HEIGHT + ROW_GAP)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_second_child_opens_a_new_lane_and_current_follows_the_head() {
        let history_widget = demo_history();
        let graph = layout(&history_widget);
        assert_eq!(graph.nodes[0].lane, 0);
        assert_eq!(graph.nodes[1].lane, 0);
        assert_eq!(graph.nodes[2].lane, 1);
        assert_eq!(graph.nodes.last().unwrap().lane, 0);
        assert!(graph.nodes.last().unwrap().current);
    }

    #[test]
    fn legacy_relationships_are_never_drawn_as_exact_edges() {
        let mut history = demo_history();
        history.nodes[2].relation = LineageRelation::LegacyUnknown;
        history.nodes[2].parent_id = None;
        let graph = layout(&history);
        assert!(!graph
            .nodes
            .iter()
            .any(|node| node.id == Some(history.nodes[2].recovery_point_id)));
    }

    #[test]
    fn returning_to_an_older_point_draws_current_as_a_new_branch() {
        let mut history = demo_history();
        history.current_head_id = Some(history.nodes[0].recovery_point_id);
        let graph = layout(&history);
        let restored_head = graph
            .nodes
            .iter()
            .find(|node| node.id == history.current_head_id)
            .unwrap();
        let current = graph.nodes.last().unwrap();
        assert_ne!(current.lane, restored_head.lane);
        assert_eq!(current.parent, history.current_head_id);
    }

    fn demo_history() -> SystemLineage {
        let first = "00000000-0000-4000-8000-000000000011".parse().unwrap();
        let second = "00000000-0000-4000-8000-000000000012".parse().unwrap();
        let branch = "00000000-0000-4000-8000-000000000013".parse().unwrap();
        let now = chrono::Utc::now();
        SystemLineage {
            schema_version: anduinos_timeback::lineage::LINEAGE_SCHEMA_VERSION,
            revision: 1,
            current_head_id: Some(second),
            nodes: vec![
                demo_node(first, None, now, "First", DeploymentKind::Manual),
                demo_node(
                    second,
                    Some(first),
                    now + chrono::Duration::minutes(1),
                    "Second",
                    DeploymentKind::Manual,
                ),
                demo_node(
                    branch,
                    Some(first),
                    now + chrono::Duration::minutes(2),
                    "Branch",
                    DeploymentKind::Manual,
                ),
            ],
            activations: Vec::new(),
        }
    }
}
