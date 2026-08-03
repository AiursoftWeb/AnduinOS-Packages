# shellcheck shell=bash
# shellcheck disable=SC2154 # ble.sh-owned editor and execution state
# Small, typed session context for high-value completions. This intentionally
# keeps state in the current shell only and never captures terminal output.

_anduinos_guess_previous_docker_filter=
_anduinos_guess_previous_process_filter=
_anduinos_guess_previous_service_filter=
_anduinos_guess_docker_cache_time=-100
_anduinos_guess_docker_cache_sudo=
_anduinos_guess_docker_cache=()
_anduinos_guess_process_cache_time=-100
_anduinos_guess_process_cache=()
_anduinos_guess_service_cache_time=-100
_anduinos_guess_service_cache=()
_anduinos_guess_git_cache_time=-100
_anduinos_guess_git_cache_pwd=
_anduinos_guess_git_cache=()
_anduinos_guess_network_sandbox=(unshare --user --map-root-user --net)

_anduinos_guess_duration_seconds() {
    local value=${1-80ms} fallback=${2-80} milliseconds
    [[ $value =~ ^([1-9][0-9]{0,2})ms$ ]] || value=${fallback}ms
    milliseconds=$((10#${value%ms}))
    printf '0.%03d' "$milliseconds"
}

_anduinos_guess_observe_command() {
    local command=${1-} head tail word kind=
    _anduinos_guess_previous_docker_filter=
    _anduinos_guess_previous_process_filter=
    _anduinos_guess_previous_service_filter=
    ((_ble_edit_exec_lastexit == 0)) || return 0
    [[ $command == *'|'* ]] || return 0

    head=${command%%|*}
    head=${head#"${head%%[![:space:]]*}"}
    head=${head%"${head##*[![:space:]]}"}
    case $head in
        'docker ps'*|'docker container ls'*|\
        'sudo docker ps'*|'sudo docker container ls'*) kind=docker ;;
        'ps '*|'sudo ps '*) kind=process ;;
        'systemctl list-units'*|'systemctl --user list-units'*|\
        'sudo systemctl list-units'*) kind=service ;;
        *) return 0 ;;
    esac

    tail=${command##*|}
    tail=${tail#"${tail%%[![:space:]]*}"}
    local -a words=()
    builtin read -r -a words <<<"$tail"
    [[ ${words[0]-} == grep ]] || return 0
    for word in "${words[@]:1}"; do
        [[ $word == -* ]] && continue
        if [[ $word == \"*\" && $word == *\" ]]; then
            word=${word:1:${#word}-2}
        elif [[ $word == \'*\' && $word == *\' ]]; then
            word=${word:1:${#word}-2}
        fi
        if [[ $word ]]; then
            if [[ $kind == docker ]]; then
                _anduinos_guess_previous_docker_filter=$word
            elif [[ $kind == process ]]; then
                _anduinos_guess_previous_process_filter=$word
            else
                _anduinos_guess_previous_service_filter=$word
            fi
        fi
        break
    done
}

_anduinos_guess_refresh_docker() {
    local use_sudo=$1 now=$SECONDS timeout_seconds output
    if ((now - _anduinos_guess_docker_cache_time < 2)) &&
       [[ $_anduinos_guess_docker_cache_sudo == "$use_sudo" ]]; then
        ((${#_anduinos_guess_docker_cache[@]}))
        return
    fi

    _anduinos_guess_docker_cache=()
    _anduinos_guess_docker_cache_time=$now
    _anduinos_guess_docker_cache_sudo=$use_sudo
    timeout_seconds="$(_anduinos_guess_duration_seconds \
        "${ANDUINOS_GUESS_CONTEXT_TIMEOUT:-80ms}" 80)"

    local -a docker_command=("${_anduinos_guess_network_sandbox[@]}" docker)
    [[ $use_sudo ]] && docker_command=(sudo -n unshare --net docker)
    command -v "${docker_command[0]}" >/dev/null 2>&1 || return 1
    output="$(
        timeout --signal=TERM --kill-after=0.020 "$timeout_seconds" \
            "${docker_command[@]}" container ls \
            --format '{{.ID}}\t{{.Names}}\t{{.Image}}' 2>/dev/null
    )" || return 1
    [[ $output ]] || return 1
    builtin mapfile -t _anduinos_guess_docker_cache <<<"$output"
}

_anduinos_guess_common_prefix() {
    local common=$1 value
    shift
    for value; do
        while [[ $value != "$common"* ]]; do
            common=${common::${#common}-1}
            [[ $common ]] || break 2
        done
    done
    printf '%s' "$common"
}

_anduinos_guess_docker_exec_candidate() {
    local text=$1 rest=$1 use_sudo='' args current before word need_value=''
    case $rest in
        'sudo docker '*) use_sudo=1; rest=${rest#sudo } ;;
        'docker '*) ;;
        *) return 1 ;;
    esac
    rest=${rest#docker }
    [[ $rest == 'container '* ]] && rest=${rest#container }
    [[ $rest == 'exec '* ]] || return 1
    args=${rest#exec }

    if [[ $args == *' ' ]]; then
        current=
        before=$args
    else
        current=${args##* }
        before=${args::${#args}-${#current}}
    fi
    [[ $current != -* ]] || return 1

    local -a words=()
    builtin read -r -a words <<<"$before"
    for word in "${words[@]}"; do
        if [[ $need_value ]]; then
            need_value=
            continue
        fi
        case $word in
            -u|-e|-w|--user|--env|--env-file|--workdir|--detach-keys)
                need_value=1 ;;
            --user=*|--env=*|--env-file=*|--workdir=*|--detach-keys=*|\
            -d|-i|-t|-it|-ti|--detach|--interactive|--tty|--privileged)
                ;;
            --) ;;
            -*) ;;
            *) return 1 ;;
        esac
    done
    [[ ! $need_value ]] || return 1

    _anduinos_guess_refresh_docker "$use_sudo" || return 1
    local entry id name image haystack filter=${_anduinos_guess_previous_docker_filter,,}
    local -a matches=()
    for entry in "${_anduinos_guess_docker_cache[@]}"; do
        IFS=$'\t' builtin read -r id name image <<<"$entry"
        [[ $id && $name ]] || continue
        if [[ $filter ]]; then
            haystack=${id,,}' '${name,,}' '${image,,}
            [[ $haystack == *"$filter"* ]] || continue
        fi
        if [[ $current ]]; then
            [[ $id == "$current"* ]] && matches+=("$id")
            [[ $name == "$current"* ]] && matches+=("$name")
        else
            matches+=("$name")
        fi
    done
    ((${#matches[@]})) || return 1

    local candidate
    if ((${#matches[@]} == 1)); then
        candidate=${matches[0]}
    else
        candidate="$(_anduinos_guess_common_prefix "${matches[@]}")"
        ((${#candidate} > ${#current})) || return 1
    fi
    [[ $candidate != "$current" ]] || return 1
    REPLY=$text${candidate:${#current}}
}

_anduinos_guess_refresh_processes() {
    local now=$SECONDS timeout_seconds output
    if ((now - _anduinos_guess_process_cache_time < 2)); then
        ((${#_anduinos_guess_process_cache[@]}))
        return
    fi
    _anduinos_guess_process_cache=()
    _anduinos_guess_process_cache_time=$now
    timeout_seconds="$(_anduinos_guess_duration_seconds \
        "${ANDUINOS_GUESS_CONTEXT_TIMEOUT:-80ms}" 80)"
    output="$(
        timeout --signal=TERM --kill-after=0.020 "$timeout_seconds" \
            "${_anduinos_guess_network_sandbox[@]}" ps -eo pid=,comm= \
            2>/dev/null
    )" || return 1
    [[ $output ]] || return 1
    builtin mapfile -t _anduinos_guess_process_cache <<<"$output"
}

_anduinos_guess_kill_candidate() {
    local text=$1 rest=$1 args current before word need_value=''
    case $rest in
        'sudo kill '*) rest=${rest#sudo } ;;
        'kill '*) ;;
        *) return 1 ;;
    esac
    args=${rest#kill }
    if [[ $args == *' ' ]]; then
        current=
        before=$args
    else
        current=${args##* }
        before=${args::${#args}-${#current}}
    fi
    [[ $current != -* ]] || return 1
    [[ $current || $_anduinos_guess_previous_process_filter ]] || return 1

    local -a words=()
    builtin read -r -a words <<<"$before"
    for word in "${words[@]}"; do
        if [[ $need_value ]]; then
            need_value=
            continue
        fi
        case $word in
            -s|--signal|--timeout) need_value=1 ;;
            -*) ;;
            *) [[ $word =~ ^[0-9]+$ ]] || return 1 ;;
        esac
    done
    [[ ! $need_value ]] || return 1

    _anduinos_guess_refresh_processes || return 1
    local entry pid process filter=${_anduinos_guess_previous_process_filter,,}
    local -a matches=()
    for entry in "${_anduinos_guess_process_cache[@]}"; do
        builtin read -r pid process _ <<<"$entry"
        [[ $pid =~ ^[0-9]+$ && $pid != "$$" && $pid != "$PPID" ]] || continue
        [[ ! $filter || ${process,,} == *"$filter"* ]] || continue
        [[ $pid == "$current"* ]] && matches+=("$pid")
    done
    ((${#matches[@]})) || return 1

    local candidate
    if ((${#matches[@]} == 1)); then
        candidate=${matches[0]}
    else
        candidate="$(_anduinos_guess_common_prefix "${matches[@]}")"
        ((${#candidate} > ${#current})) || return 1
    fi
    REPLY=$text${candidate:${#current}}
}

_anduinos_guess_refresh_services() {
    local now=$SECONDS timeout_seconds output
    if ((now - _anduinos_guess_service_cache_time < 3)); then
        ((${#_anduinos_guess_service_cache[@]}))
        return
    fi
    _anduinos_guess_service_cache=()
    _anduinos_guess_service_cache_time=$now
    timeout_seconds="$(_anduinos_guess_duration_seconds \
        "${ANDUINOS_GUESS_CONTEXT_TIMEOUT:-80ms}" 80)"
    output="$(
        timeout --signal=TERM --kill-after=0.020 "$timeout_seconds" \
            systemctl list-units --all --type=service --plain --no-legend \
            --no-pager 2>/dev/null
    )" || return 1
    [[ $output ]] || return 1
    local line unit
    while IFS= builtin read -r line; do
        builtin read -r unit _ <<<"$line"
        [[ $unit == *.service ]] && _anduinos_guess_service_cache+=("$unit")
    done <<<"$output"
    ((${#_anduinos_guess_service_cache[@]}))
}

_anduinos_guess_service_candidate() {
    local text=$1 rest=$1 user_scope='' verb args current unit
    case $rest in
        'sudo systemctl '*) rest=${rest#sudo } ;;
        'systemctl '*) ;;
        *) return 1 ;;
    esac
    rest=${rest#systemctl }
    if [[ $rest == '--user '* ]]; then
        user_scope=1
        rest=${rest#--user }
    fi
    verb=${rest%% *}
    case $verb in
        status|start|restart|reload|stop|enable|disable) ;;
        *) return 1 ;;
    esac
    [[ $rest == "$verb "* ]] || return 1
    args=${rest#"$verb "}
    if [[ $args == *' ' ]]; then
        current=
    else
        current=${args##* }
    fi
    [[ $current != -* ]] || return 1
    [[ $current || $_anduinos_guess_previous_service_filter ]] || return 1

    # User units and system units live in different managers. Keep this first
    # implementation conservative: Carapace handles --user completions while
    # the typed context source handles the system manager.
    [[ ! $user_scope ]] || return 1
    _anduinos_guess_refresh_services || return 1
    local filter=${_anduinos_guess_previous_service_filter,,}
    local -a matches=()
    for unit in "${_anduinos_guess_service_cache[@]}"; do
        [[ ! $filter || ${unit,,} == *"$filter"* ]] || continue
        [[ $unit == "$current"* ]] && matches+=("$unit")
    done
    ((${#matches[@]})) || return 1

    local candidate
    if ((${#matches[@]} == 1)); then
        candidate=${matches[0]}
    else
        candidate="$(_anduinos_guess_common_prefix "${matches[@]}")"
        ((${#candidate} > ${#current})) || return 1
    fi
    REPLY=$text${candidate:${#current}}
}

_anduinos_guess_refresh_git_branches() {
    local now=$SECONDS timeout_seconds output
    if ((now - _anduinos_guess_git_cache_time < 2)) &&
       [[ $_anduinos_guess_git_cache_pwd == "$PWD" ]]; then
        ((${#_anduinos_guess_git_cache[@]}))
        return
    fi
    _anduinos_guess_git_cache=()
    _anduinos_guess_git_cache_time=$now
    _anduinos_guess_git_cache_pwd=$PWD
    timeout_seconds="$(_anduinos_guess_duration_seconds \
        "${ANDUINOS_GUESS_CONTEXT_TIMEOUT:-80ms}" 80)"
    output="$(
        timeout --signal=TERM --kill-after=0.020 "$timeout_seconds" \
            "${_anduinos_guess_network_sandbox[@]}" git for-each-ref \
            --format='%(refname:short)' refs/heads 2>/dev/null
    )" || return 1
    [[ $output ]] || return 1
    builtin mapfile -t _anduinos_guess_git_cache <<<"$output"
}

_anduinos_guess_git_candidate() {
    local text=$1 rest=$1 verb args current before word
    [[ $rest == 'git '* ]] || return 1
    rest=${rest#git }
    verb=${rest%% *}
    case $verb in
        checkout|switch|merge|rebase) ;;
        *) return 1 ;;
    esac
    [[ $rest == "$verb "* ]] || return 1
    args=${rest#"$verb "}
    if [[ $args == *' ' ]]; then
        current=
        before=$args
    else
        current=${args##* }
        before=${args::${#args}-${#current}}
    fi
    [[ $current && $current != -* ]] || return 1
    local -a words=()
    builtin read -r -a words <<<"$before"
    for word in "${words[@]}"; do
        [[ $word == -* ]] || return 1
    done

    _anduinos_guess_refresh_git_branches || return 1
    local branch
    local -a matches=()
    for branch in "${_anduinos_guess_git_cache[@]}"; do
        [[ $branch == "$current"* ]] && matches+=("$branch")
    done
    ((${#matches[@]})) || return 1

    local candidate
    if ((${#matches[@]} == 1)); then
        candidate=${matches[0]}
    else
        candidate="$(_anduinos_guess_common_prefix "${matches[@]}")"
        ((${#candidate} > ${#current})) || return 1
    fi
    REPLY=$text${candidate:${#current}}
}

function ble/complete/auto-complete/source:anduinos-context {
    [[ ${ANDUINOS_GUESS_CONTEXT:-1} != 0 ]] || return 1
    ((_ble_edit_ind == ${#_ble_edit_str})) || return 1
    local REPLY
    _anduinos_guess_docker_exec_candidate "$_ble_edit_str" ||
        _anduinos_guess_kill_candidate "$_ble_edit_str" ||
        _anduinos_guess_service_candidate "$_ble_edit_str" ||
        _anduinos_guess_git_candidate "$_ble_edit_str" || return 1
    [[ $REPLY == "$_ble_edit_str"* ]] || return 1
    ble/complete/auto-complete/enter \
        h 0 "${REPLY:${#_ble_edit_str}}" '' "$REPLY"
}

_anduinos_guess_is_sensitive() {
    local text=${1,,}
    case $text in
        *'authorization:'*|*'bearer '*|*'--password='*|*'--password '*|\
        *'--passwd='*|*'--passwd '*|*'--token='*|*'--token '*|\
        *'--api-key='*|*'--api-key '*|*'--secret-key='*|*'--secret-key '*|\
        *'--access-key='*|*'--access-key '*) return 0 ;;
    esac
    [[ $text =~ (^|[[:space:];])(password|passwd|token|api_?key|secret_?key|access_?key|private_?key)= ]] &&
        return 0
    [[ $text =~ ://[^[:space:]@/]+:[^[:space:]@]+@ ]] && return 0
    [[ $text =~ (^|[[:space:]])mysql[[:space:]].*-p[^[:space:]]+ ]] && return 0
    return 1
}

_anduinos_guess_danger_penalty() {
    local text=${1#"${1%%[![:space:]]*}"}
    case $text in
        rm\ *|rmdir\ *|shred\ *|dd\ *|mkfs*\ *|wipefs\ *|\
        shutdown*|reboot*|poweroff*|halt*|\
        'docker rm '*|'docker container rm '*|\
        'systemctl stop '*|'systemctl disable '*|'kubectl delete '*)
            REPLY=1000 ;;
        mv\ *|chmod\ *|chown\ *|'docker stop '*|'docker kill '*)
            REPLY=350 ;;
        *) REPLY=0 ;;
    esac
}

function ble/complete/auto-complete/source:anduinos-policy {
    # Returning success without entering a suggestion stops the remaining
    # sources. The user's line stays completely untouched.
    _anduinos_guess_is_sensitive "$_ble_edit_str" || return 1
    return 0
}

function ble/complete/auto-complete/source:anduinos-history {
    [[ $bleopt_complete_auto_history ]] || return 1
    ((_ble_edit_ind == ${#_ble_edit_str})) || return 1
    local text=$_ble_edit_str
    [[ $text && $text != [-0-9#?!]* ]] || return 1

    local scan=${ANDUINOS_GUESS_HISTORY_SCAN:-300}
    [[ $scan =~ ^[1-9][0-9]{1,3}$ ]] || scan=300
    ((scan > 2000)) && scan=2000

    local count start stop index candidate distance frequency score penalty position
    ble/history/get-count -v count
    ((start=count-1, stop=start-scan+1, stop<0 && (stop=0)))
    local -a candidates=() frequencies=() recent_distances=()
    for ((index=start; index>=stop; index--)); do
        ble/history/get-edited-entry -v candidate "$index" || continue
        [[ $candidate == "$text"?* && $candidate != *$'\n'* ]] || continue
        _anduinos_guess_is_sensitive "$candidate" && continue
        position=-1
        for ((frequency=0; frequency<${#candidates[@]}; frequency++)); do
            if [[ ${candidates[frequency]} == "$candidate" ]]; then
                position=$frequency
                break
            fi
        done
        if ((position < 0)); then
            # Bound exact-comparison work for very broad prefixes such as
            # "e". Once 32 distinct matches exist, searching older history
            # adds little value and would delay the visible fast path.
            ((${#candidates[@]} < 32)) || break
            candidates+=("$candidate")
            frequencies+=(1)
            recent_distances+=("$((start-index))")
        else
            ((frequencies[position]++))
        fi
    done
    ((${#candidates[@]})) || return 1

    local best='' best_score=-1 best_distance=$scan
    for ((position=0; position<${#candidates[@]}; position++)); do
        candidate=${candidates[position]}
        frequency=${frequencies[position]}
        ((frequency > 8)) && frequency=8
        distance=${recent_distances[position]}
        # One repeat should compensate for roughly two hundred entries of
        # recency. This resists accidental one-off commands without making old
        # habits impossible to replace.
        _anduinos_guess_danger_penalty "$candidate"
        penalty=$REPLY
        ((score=frequency*220+scan-distance-penalty))
        if ((score > best_score ||
             score == best_score && distance < best_distance)); then
            best=$candidate
            best_score=$score
            best_distance=$distance
        fi
    done
    [[ $best ]] || return 1
    ble/complete/auto-complete/enter \
        h 0 "${best:${#text}}" '' "$best"
}

_anduinos_guess_install_context_source() {
    local source
    local -a sources=(anduinos-policy anduinos-context anduinos-history)
    for source in "${_ble_complete_auto_source[@]}"; do
        case $source in
            anduinos-policy|anduinos-context|anduinos-history|history) ;;
            *) sources+=("$source") ;;
        esac
    done
    _ble_complete_auto_source=("${sources[@]}")
}

# A typed, unique live entity is more relevant than a stale history line. All
# other input falls through untouched to ranked history and syntax sources.
# core-complete is normally lazy-loaded after this package file is sourced, so
# install the source through its load hook instead of mutating a temporary
# pre-load array.
blehook complete_load!=_anduinos_guess_install_context_source
if declare -p _ble_complete_auto_source &>/dev/null &&
   ble/is-function ble/complete/auto-complete.impl; then
    _anduinos_guess_install_context_source
fi
if [[ ${ANDUINOS_GUESS_CONTEXT:-1} != 0 ]]; then
    blehook POSTEXEC!=_anduinos_guess_observe_command
fi
