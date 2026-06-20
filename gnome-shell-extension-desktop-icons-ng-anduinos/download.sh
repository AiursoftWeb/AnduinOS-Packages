#!/usr/bin/env bash
# Pre-build: downloads Desktop Icons NG (DING) for each supported suite,
# then applies AnduinOS customizations.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/gnome-versions.sh"

UUID="ding@rastersoft.com"

# ── Localization table ──────────────────────────────────────────────────

declare -A DING_APPEARANCE=(
    ["ar"]="إعدادات مظهر AnduinOS"
    ["be"]="Налады вонкавага выгляду AnduinOS"
    ["ca"]="Paràmetres de l'aparença d'AnduinOS"
    ["cs"]="Nastavení vzhledu AnduinOS"
    ["da"]="Indstillinger for AnduinOS-udseende"
    ["de"]="AnduinOS-Aussehens-Einstellungen"
    ["es"]="Preferencias de la apariencia de AnduinOS"
    ["fi"]="AnduinOS-ulkonäön asetukset"
    ["fr"]="Préférences de l'apparence d'AnduinOS"
    ["fur"]="AnduinOS Appearance Settings"
    ["he"]="הגדרות המראה של AnduinOS"
    ["hr"]="AnduinOS Appearance Settings"
    ["hu"]="AnduinOS Appearance Settings"
    ["id"]="Pengaturan Tampilan AnduinOS"
    ["it"]="Impostazioni dell'aspetto di AnduinOS"
    ["ja"]="AnduinOS の外観の設定"
    ["ka"]="AnduinOS-ის გარეგნობის პარამეტრები"
    ["ko"]="AnduinOS 외관 설정"
    ["nl"]="AnduinOS-uiterlijk-instellingen"
    ["oc"]="Paramètres de l'aparéncia d'AnduinOS"
    ["pl"]="Ustawienia wyglądu AnduinOS"
    ["pt_BR"]="Configurações da Aparência do AnduinOS"
    ["ro"]="Setările aspectului AnduinOS"
    ["ru"]="Параметры внешнего вида AnduinOS"
    ["sk"]="Nastavenia vzhľadu AnduinOS"
    ["sv"]="Inställningar för AnduinOS-utseende"
    ["tr"]="AnduinOS Görünümü Ayarları"
    ["uk"]="Налаштування оформлення AnduinOS"
    ["zh_CN"]="AnduinOS 外观设置"
    ["zh_TW"]="AnduinOS 外觀設定"
)

for SUITE in "${!GNOME_TARGETS[@]}"; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Resolving $UUID for GNOME $TARGET..."
    python3 "$SCRIPT_DIR/../lib/resolve-gnome-ext.py" "$UUID" --target "$TARGET" --download --out "$DEPLOY_DIR"

    # ── AnduinOS customizations ───────────────────────────────────────────
    echo "[$SUITE] Patching desktopManager.js: Desktop Icons Settings → AnduinOS Appearance Settings"

    sed -i "s/label: _('Desktop Icons Settings')/label: _('AnduinOS Appearance Settings')/" \
        "$DEPLOY_DIR/app/desktopManager.js"

    sed -i 's/this._settingsMenuItem.connect("activate", () => Prefs.showPreferences());/this._settingsMenuItem.connect("activate", () => { GLib.spawn_command_line_async('\''anduinos-appearance'\''); });/' \
        "$DEPLOY_DIR/app/desktopManager.js"

    echo "[$SUITE] JS patch applied successfully."

    # ── DING v84 spawns ding.js as a child process, needs +x ──────────
    chmod +x "$DEPLOY_DIR/app/ding.js"

    # ── Inject "AnduinOS Appearance Settings" into ding.mo ────────────────
    locale_dir="$DEPLOY_DIR/locale"
    found=0

    if [[ -d "$locale_dir" ]]; then
        for lang_dir in "$locale_dir"/*/; do
            lang=$(basename "$lang_dir")
            mo_file="$lang_dir/LC_MESSAGES/ding.mo"

            if [[ -f "$mo_file" ]] && [[ -n "${DING_APPEARANCE[$lang]+isset}" ]]; then
                echo "[$SUITE] Patching ding.mo locale: $lang"
                msgunfmt "$mo_file" -o /tmp/ding.po

                cat << EOF >> /tmp/ding.po
msgid "AnduinOS Appearance Settings"
msgstr "${DING_APPEARANCE[$lang]}"

EOF
                msgfmt /tmp/ding.po -o "$mo_file"
                rm -f /tmp/ding.po
                found=$((found + 1))
            fi
        done
        echo "[$SUITE] Patched ding.mo for $found languages"
    fi
done

echo "Done."

# Pre-compile GSettings schemas at build time so postinst is unnecessary
for suite_dir in deploy/*/; do
    schema_dir="${suite_dir}ding@rastersoft.com/schemas"
    [ -d "$schema_dir" ] && glib-compile-schemas "$schema_dir" || true
done
