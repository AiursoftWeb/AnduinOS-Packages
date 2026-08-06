set -eu

case "${1:-}" in
    remove|deconfigure)
        # Disable while the unit file still exists. Doing this from postrm
        # produces a misleading "unit does not exist" warning after dpkg has
        # already removed the package payload.
        systemctl disable --now anduinos-waypoint-confirm.service >/dev/null 2>&1 || true
        systemctl disable --now anduinos-waypoint-scheduler.timer >/dev/null 2>&1 || true
        ;;
esac

systemctl stop anduinos-waypoint-scheduler.service >/dev/null 2>&1 || true
systemctl stop anduinos-waypoint-helper.service >/dev/null 2>&1 || true
