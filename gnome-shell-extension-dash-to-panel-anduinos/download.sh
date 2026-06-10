#!/usr/bin/env bash
# Pre-build: downloads extension for each supported suite/GNOME version,
# then applies AnduinOS customizations.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/gnome-versions.sh"

UUID="dash-to-panel@jderose9.github.com"

for SUITE in "${!GNOME_TARGETS[@]}"; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Resolving $UUID for GNOME $TARGET..."
    python3 "$SCRIPT_DIR/../lib/resolve-gnome-ext.py" "$UUID" --target "$TARGET" --download --out "$DEPLOY_DIR"

    # ── AnduinOS customizations ───────────────────────────────────────────
    echo "[$SUITE] Applying AnduinOS panel layout to $DEPLOY_DIR/panelPositions.js..."

    sed -i '/export const defaults = \[/,/^\]$/c\
// AnduinOS custom default panel layout\
export const defaults = [\
  { element: LEFT_BOX, visible: true, position: STACKED_TL },\
  { element: CENTER_BOX, visible: true, position: CENTERED_MONITOR },\
  { element: TASKBAR, visible: true, position: CENTERED_MONITOR },\
  { element: RIGHT_BOX, visible: true, position: STACKED_BR },\
  { element: SYSTEM_MENU, visible: true, position: STACKED_BR },\
  { element: DATE_MENU, visible: true, position: STACKED_BR },\
  { element: DESKTOP_BTN, visible: true, position: STACKED_BR },\
];' \
        "$DEPLOY_DIR/panelPositions.js"

    # ── Source patch: rename msgid in JavaScript ──
    echo "[$SUITE] Patching appIcons.js: Dash to Panel Settings → Taskbar Settings"
    sed -i "s/_('Dash to Panel Settings')/_('Taskbar Settings')/g" "$DEPLOY_DIR/appIcons.js"

    # ── Locale patch: rename "Dash to Panel Settings" → "Taskbar Settings" ──
    # Updates both msgid (to match patched JS source) and msgstr (translated).
    declare -A TASKBAR_SETTINGS=(
        ["cs"]="Nastavení panelu úloh"
        ["de"]="Taskleisteneinstellungen"
        ["es"]="Configuración de la barra de tareas"
        ["fa"]="تنظیمات نوار وظیفه"
        ["fr"]="Paramètres de la barre des tâches"
        ["gl"]="Configuración da barra de tarefas"
        ["hu"]="Tálcabeállítások"
        ["it"]="Impostazioni barra delle applicazioni"
        ["ja"]="タスクバー設定"
        ["ka"]="ამოცანების პანელის პარამეტრები"
        ["kk"]="Тапсырмалар тақтасының параметрлері"
        ["ko"]="작업 표시줄 설정"
        ["nl"]="Taakbalkinstellingen"
        ["pl"]="Ustawienia paska zadań"
        ["pt_BR"]="Configurações da barra de tarefas"
        ["ru"]="Настройки панели задач"
        ["sk"]="Nastavenia panela úloh"
        ["sv"]="Aktivitetsfältsinställningar"
        ["tr"]="Görev çubuğu ayarları"
        ["uk"]="Налаштування панелі завдань"
        ["zh_CN"]="任务栏设置"
        ["zh_TW"]="工作列設定"
    )

    locale_dir="$DEPLOY_DIR/locale"
    patched=0

    if [[ -d "$locale_dir" ]]; then
        for lang_dir in "$locale_dir"/*/; do
            lang=$(basename "$lang_dir")
            mo_file="$lang_dir/LC_MESSAGES/dash-to-panel.mo"

            if [[ -f "$mo_file" ]] && [[ -n "${TASKBAR_SETTINGS[$lang]+isset}" ]]; then
                echo "[$SUITE] Patching dash-to-panel locale: $lang"
                msgunfmt "$mo_file" -o /tmp/dash-to-panel.po
                sed -i 's/msgid "Dash to Panel Settings"/msgid "Taskbar Settings"/' /tmp/dash-to-panel.po
                sed -i '/msgid "Taskbar Settings"/{n;s/.*/msgstr "'"${TASKBAR_SETTINGS[$lang]}"'"/}' /tmp/dash-to-panel.po
                msgfmt /tmp/dash-to-panel.po -o "$mo_file"
                rm -f /tmp/dash-to-panel.po
                patched=$((patched + 1))
            fi
        done
        echo "[$SUITE] Patched dash-to-panel.mo for $patched languages"
    fi
done

echo "Done."
