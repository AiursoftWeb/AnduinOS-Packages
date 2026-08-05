use crate::performance;

/// Return estimated reclaimable bytes for trusted deployments.
///
/// The helper uses each snapshot's exact level-zero qgroup. Missing qgroup accounting produces
/// an unknown value; apparent `du` sizes are deliberately never substituted.
pub fn get_all_snapshot_sizes(deployment_ids: &[String]) -> std::collections::HashMap<String, u64> {
    use std::collections::HashMap;

    let _timer = performance::tracker().start("get_all_snapshot_sizes");

    if let Ok(client) = crate::dbus_client::WaypointHelperClient::new() {
        if !deployment_ids.is_empty() {
            if let Ok(spaces_by_name) = client.get_deployment_spaces(deployment_ids.to_vec()) {
                let mut result = HashMap::new();
                for deployment_id in deployment_ids {
                    if let Some(size) = spaces_by_name
                        .get(deployment_id)
                        .and_then(|space| space.estimated_reclaimable_bytes())
                    {
                        result.insert(deployment_id.clone(), size);
                    }
                }
                return result;
            } else {
                log::warn!("Btrfs qgroup accounting is unavailable");
            }
        }
    } else {
        log::warn!("Could not connect to the recovery helper for Btrfs accounting");
    }
    HashMap::new()
}
