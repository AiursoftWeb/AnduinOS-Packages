#!/bin/sh
set -e

refresh_graphical_sessions() {
    if ! systemctl --quiet is-active uresourced.service; then
        rm -f /run/systemd/user/session.slice.d/99-uresourced.conf
        deb-systemd-invoke --user daemon-reload
        deb-systemd-invoke --user stop uresourced.service || true
        return
    fi
    deb-systemd-invoke --user daemon-reload

    # The upstream user unit is static. Only graphical sessions should start it.
    # deb-systemd-invoke --user cannot select an individual user manager.
    if [ -x /usr/sbin/policy-rc.d ]; then
        result=0
        /usr/sbin/policy-rc.d uresourced.service "$session_action" || result=$?
        case "$result" in
            0|104) ;;
            *) echo "User service $session_action skipped by policy-rc.d ($result)." >&2; return ;;
        esac
    fi
    for bus in /run/user/[0-9]*/bus; do
        [ -S "$bus" ] || continue
        uid=${bus%/bus}
        manager="--machine=${uid##*/}@.host"
        systemctl --user "$manager" --quiet is-active graphical-session.target || continue
        if [ "$session_action" = restart ]; then
            systemctl --user "$manager" --quiet is-active uresourced.service || continue
        fi
        case "$(systemctl --user "$manager" is-enabled uresourced.service 2>/dev/null)" in
            masked|masked-runtime) continue ;;
        esac
        systemctl --user "$manager" "$session_action" uresourced.service || echo "Could not $session_action uresourced for ${uid##*/}." >&2
    done
}

if [ "$1" = configure ] && [ -d /run/systemd/system ]; then
    session_action=start
    [ -z "${2:-}" ] || session_action=restart
    # APKG appends the system service actions. Reload the user managers after
    # the system daemon has written the session.slice settings they will read.
    trap 'if [ "$?" -eq 0 ]; then refresh_graphical_sessions; fi' EXIT
fi
