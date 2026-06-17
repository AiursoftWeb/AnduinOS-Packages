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

    # ── Source patch: wrap context-menu title in _() for i18n ──
    echo "[$SUITE] Patching appIcons.js: title → _(title) for context-menu i18n"
    sed -i 's/title: e.title/title: _(e.title)/' "$DEPLOY_DIR/appIcons.js"

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

    # ── Inject context-menu translations into dash-to-panel.mo ──────────
    declare -A CM_APPEARANCE=(
        ["cs"]="Vzhled"
        ["de"]="Aussehen"
        ["es"]="Apariencia"
        ["fa"]="ظاهر"
        ["fr"]="Apparence"
        ["gl"]="Aparencia"
        ["hu"]="Megjelenés"
        ["it"]="Aspetto"
        ["ja"]="外観"
        ["ka"]="გარეგნობა"
        ["kk"]="Сыртқы түрі"
        ["ko"]="외관"
        ["nl"]="Uiterlijk"
        ["pl"]="Wygląd"
        ["pt_BR"]="Aparência"
        ["ru"]="Оформление"
        ["sk"]="Vzhľad"
        ["sv"]="Utseende"
        ["tr"]="Görünüm"
        ["uk"]="Оформлення"
        ["zh_CN"]="外观"
        ["zh_TW"]="外觀"
    )
    declare -A CM_TERMINAL=(
        ["cs"]="Terminál"
        ["de"]="Terminal"
        ["es"]="Terminal"
        ["fa"]="ترمینال"
        ["fr"]="Terminal"
        ["gl"]="Terminal"
        ["hu"]="Terminál"
        ["it"]="Terminale"
        ["ja"]="端末設定"
        ["ka"]="ტერმინალი"
        ["kk"]="Терминал"
        ["ko"]="터미널 설정"
        ["nl"]="Terminal"
        ["pl"]="Terminal"
        ["pt_BR"]="Terminal"
        ["ru"]="Терминал"
        ["sk"]="Terminál"
        ["sv"]="Terminal"
        ["tr"]="Terminal"
        ["uk"]="Термінал"
        ["zh_CN"]="终端设置"
        ["zh_TW"]="終端機設定"
    )
    declare -A CM_TASKMAN=(
        ["cs"]="Správce úloh"
        ["de"]="Task-Manager"
        ["es"]="Administrador de tareas"
        ["fa"]="مدیر وظیفه"
        ["fr"]="Gestionnaire des tâches"
        ["gl"]="Xestor de tarefas"
        ["hu"]="Feladatkezelő"
        ["it"]="Gestione attività"
        ["ja"]="タスクマネージャー"
        ["ka"]="ამოცანების მენეჯერი"
        ["kk"]="Тапсырмалар менеджері"
        ["ko"]="작업 관리자"
        ["nl"]="Taakbeheer"
        ["pl"]="Menedżer zadań"
        ["pt_BR"]="Gerenciador de tarefas"
        ["ru"]="Диспетчер задач"
        ["sk"]="Správca úloh"
        ["sv"]="Aktivitetshanteraren"
        ["tr"]="Görev yöneticisi"
        ["uk"]="Диспетчер завдань"
        ["zh_CN"]="任务管理器"
        ["zh_TW"]="工作管理員"
    )
    declare -A CM_GNOME_SETTINGS=(
        ["cs"]="Nastavení GNOME"
        ["de"]="GNOME-Einstellungen"
        ["es"]="Configuración de GNOME"
        ["fa"]="تنظیمات گنوم"
        ["fr"]="Paramètres GNOME"
        ["gl"]="Configuración de GNOME"
        ["hu"]="GNOME beállítások"
        ["it"]="Impostazioni GNOME"
        ["ja"]="GNOME 設定"
        ["ka"]="GNOME-ის პარამეტრები"
        ["kk"]="GNOME параметрлері"
        ["ko"]="GNOME 설정"
        ["nl"]="GNOME-instellingen"
        ["pl"]="Ustawienia GNOME"
        ["pt_BR"]="Configurações do GNOME"
        ["ru"]="Настройки GNOME"
        ["sk"]="Nastavenia GNOME"
        ["sv"]="GNOME-inställningar"
        ["tr"]="GNOME Ayarları"
        ["uk"]="Налаштування GNOME"
        ["zh_CN"]="GNOME 设置"
        ["zh_TW"]="GNOME 設定"
    )

    if [[ -d "$locale_dir" ]]; then
        cm_patched=0
        for lang_dir in "$locale_dir"/*/; do
            lang=$(basename "$lang_dir")
            mo_file="$lang_dir/LC_MESSAGES/dash-to-panel.mo"

            if [[ -f "$mo_file" ]] && [[ -n "${CM_APPEARANCE[$lang]+isset}" ]]; then
                echo "[$SUITE] Injecting context-menu translations: $lang"
                msgunfmt "$mo_file" -o /tmp/dtp_cm.po

                for pair in "Appearance:${CM_APPEARANCE[$lang]}" "Terminal:${CM_TERMINAL[$lang]}" "Task manager:${CM_TASKMAN[$lang]}" "Gnome Settings:${CM_GNOME_SETTINGS[$lang]}"; do
                    mid="${pair%%:*}"
                    mstr="${pair#*:}"
                    if grep -q "msgid \"$mid\"" /tmp/dtp_cm.po; then
                        # Replace existing msgstr for this msgid
                        sed -i "/msgid \"$mid\"$/{n;s/msgstr \".*\"/msgstr \"$mstr\"/}" /tmp/dtp_cm.po
                    else
                        # Append new entry
                        printf '\nmsgid "%s"\nmsgstr "%s"\n' "$mid" "$mstr" >> /tmp/dtp_cm.po
                    fi
                done

                msgfmt /tmp/dtp_cm.po -o "$mo_file"
                rm -f /tmp/dtp_cm.po
                cm_patched=$((cm_patched + 1))
            fi
        done
        echo "[$SUITE] Injected context-menu translations for $cm_patched languages"
    fi
done

echo "Done."
