import Gio from 'gi://Gio';
import GObject from 'gi://GObject';

import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import { Extension, gettext as _ } from 'resource:///org/gnome/shell/extensions/extension.js';

const LOCATION_SCHEMA = "org.gnome.system.location";
const LOCATION_ENABLED_KEY = "enabled";

const LocationMenuToggle = GObject.registerClass(
class LocationMenuToggle extends QuickSettings.QuickMenuToggle {
    _init(extensionObject) {
        super._init({
            title: _("Location Services"),
            iconName: "location-active-symbolic",
        });

        this.menu.setHeader("system-location-symbolic", _("Location Services"));
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this.menu.addAction(_("Privacy Settings"), () => {
            this._openPrivacySettings();
        });
        this.connect('clicked', () => this._toggleEnabled(extensionObject));

        this._reflectSettings(extensionObject);
    }

    _toggleEnabled(extensionObject) {
        const currentEnabled =
            extensionObject.locationSettings.get_boolean(LOCATION_ENABLED_KEY);
        extensionObject.locationSettings.set_boolean(LOCATION_ENABLED_KEY, !currentEnabled);
    }

    _reflectSettings(extensionObject) {
        this.checked = extensionObject.locationSettings.get_boolean(LOCATION_ENABLED_KEY);
    }

    _openPrivacySettings() {
        try {
            Gio.Subprocess.new(
                ['gnome-control-center', 'privacy'],
                Gio.SubprocessFlags.NONE,
            );
        } catch (e) {
            logError(e, _("Failed to open Privacy Settings"));
        }
    }
});

export default class LocationSwitcherExtension extends Extension {
    enable() {
        const schemaSource = Gio.SettingsSchemaSource.get_default();
        const schema = schemaSource?.lookup(LOCATION_SCHEMA, true);
        if (!schema) {
            throw new Error(`Schema "${LOCATION_SCHEMA}" not found.`);
        }
        this.locationSettings = new Gio.Settings({ settings_schema: schema });

        this._switcherMenu = new LocationMenuToggle(this);

        this._indicator = new QuickSettings.SystemIndicator();
        this._indicator.quickSettingsItems.push(this._switcherMenu);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);

        this.settingsConnectionId = this.locationSettings.connect(
            `changed::${LOCATION_ENABLED_KEY}`,
            () => this._switcherMenu?._reflectSettings(this),
        );
    }

    disable() {
        if (this.locationSettings && this.settingsConnectionId) {
            this.locationSettings.disconnect(this.settingsConnectionId);
            this.settingsConnectionId = null;
        }
        this.locationSettings = null;

        if (this._indicator) {
            this._indicator.quickSettingsItems.forEach(item => item.destroy());
            this._indicator.destroy();
            this._indicator = null;
        }

        this._switcherMenu = null;
    }
}
