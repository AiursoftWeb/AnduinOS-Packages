import Gio from 'gi://Gio';
import GObject from 'gi://GObject';

import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

import {isProxyEnabled, ProxyMode} from './state.js';

const PROXY_SCHEMA = 'org.gnome.system.proxy';
const PROXY_MODE_KEY = 'mode';
const ACTIVE_MODE_KEY = 'active-mode';
const PROXY_ICON = 'preferences-system-network-proxy-symbolic';

const MODE_TEXT = {
    [ProxyMode.NONE]: 'Off',
    [ProxyMode.MANUAL]: 'Manual',
    [ProxyMode.AUTO]: 'Automatic',
};

const ProxyMenuToggle = GObject.registerClass(
class ProxyMenuToggle extends QuickSettings.QuickMenuToggle {
    _init(proxySettings, extensionSettings) {
        super._init({
            title: _('Proxy'),
            iconName: PROXY_ICON,
        });

        this._proxySettings = proxySettings;
        this._extensionSettings = extensionSettings;
        this.menu.setHeader(PROXY_ICON, _('Proxy'));

        const section = new PopupMenu.PopupMenuSection();
        this._modeItems = {};
        for (const mode of Object.values(ProxyMode)) {
            this._modeItems[mode] = section.addAction(
                _(MODE_TEXT[mode]),
                () => this._proxySettings.set_string(PROXY_MODE_KEY, mode),
            );
        }
        this.menu.addMenuItem(section);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addSettingsAction(
            _('Network Settings'), 'gnome-network-panel.desktop');

        this.connect('clicked', () => this._toggle());
        this.connect('destroy', () => {
            this._modeItems = null;
            this._proxySettings = null;
            this._extensionSettings = null;
        });

        this.sync();
    }

    _toggle() {
        const mode = this.checked
            ? ProxyMode.NONE
            : this._extensionSettings.get_string(ACTIVE_MODE_KEY);
        this._proxySettings.set_string(PROXY_MODE_KEY, mode);
    }

    sync() {
        const mode = this._proxySettings.get_string(PROXY_MODE_KEY);
        const enabled = isProxyEnabled(mode);

        this.checked = enabled;
        this.subtitle = enabled ? _(MODE_TEXT[mode]) : null;
        if (enabled)
            this._extensionSettings.set_string(ACTIVE_MODE_KEY, mode);

        for (const itemMode of Object.values(ProxyMode)) {
            this._modeItems[itemMode].setOrnament(
                mode === itemMode
                    ? PopupMenu.Ornament.DOT
                    : PopupMenu.Ornament.NONE,
            );
        }
    }
});

export default class ProxySwitcherExtension extends Extension {
    enable() {
        const proxySchema = Gio.SettingsSchemaSource.get_default()
            ?.lookup(PROXY_SCHEMA, true);
        if (!proxySchema)
            throw new Error(`Schema "${PROXY_SCHEMA}" not found.`);

        this._proxySettings = new Gio.Settings({settings_schema: proxySchema});
        this._extensionSettings = this.getSettings();
        this._toggle = new ProxyMenuToggle(
            this._proxySettings, this._extensionSettings);

        this._indicator = new QuickSettings.SystemIndicator();
        const statusIcon = this._indicator._addIndicator();
        statusIcon.icon_name = PROXY_ICON;
        this._toggle.bind_property(
            'checked', statusIcon, 'visible', GObject.BindingFlags.SYNC_CREATE);

        this._indicator.quickSettingsItems.push(this._toggle);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);

        this._settingsSignal = this._proxySettings.connect(
            `changed::${PROXY_MODE_KEY}`, () => this._toggle?.sync());
    }

    disable() {
        if (this._proxySettings && this._settingsSignal) {
            this._proxySettings.disconnect(this._settingsSignal);
            this._settingsSignal = null;
        }

        if (this._indicator) {
            this._indicator.quickSettingsItems.forEach(item => item.destroy());
            this._indicator.destroy();
            this._indicator = null;
        }

        this._toggle = null;
        this._proxySettings = null;
        this._extensionSettings = null;
    }
}
