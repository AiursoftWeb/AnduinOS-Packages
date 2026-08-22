import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

const SCHEDULE_SCHEMA = 'com.anduinos.ThemeSchedule';
const INTERFACE_SCHEMA = 'org.gnome.desktop.interface';

const DarkStyleMenuToggle = GObject.registerClass(
class DarkStyleMenuToggle extends QuickSettings.QuickMenuToggle {
    _init(scheduleSettings) {
        super._init({
            title: _('Dark Style'),
            iconName: 'dark-mode-symbolic',
        });

        this._schedule = scheduleSettings;
        this._interface = new Gio.Settings({schema_id: INTERFACE_SCHEMA});

        this.menu.setHeader('dark-mode-symbolic', _('Dark Style'));
        this._offItem = new PopupMenu.PopupMenuItem(_('Off'));
        this._onItem = new PopupMenu.PopupMenuItem(_('On'));
        this._autoItem = new PopupMenu.PopupMenuItem(_('Sunset to Sunrise'));
        this.menu.addMenuItem(this._offItem);
        this.menu.addMenuItem(this._onItem);
        this.menu.addMenuItem(this._autoItem);

        this._offItem.connect('activate', () => this._setManual(false));
        this._onItem.connect('activate', () => this._setManual(true));
        this._autoItem.connect('activate', () => {
            this._schedule.set_string('mode', 'sunset-sunrise');
        });
        this.connect('clicked', () => this._toggleCurrent());

        this._syncIds = [
            this._interface.connect('changed::color-scheme', () => this._sync()),
            this._schedule.connect('changed::mode', () => this._sync()),
        ];
        this.connect('destroy', () => {
            this._interface.disconnect(this._syncIds[0]);
            this._schedule.disconnect(this._syncIds[1]);
            this._interface.run_dispose();
        });
        this._sync();
    }

    _isDark() {
        return this._interface.get_string('color-scheme') === 'prefer-dark';
    }

    _isAuto() {
        return this._schedule.get_string('mode') === 'sunset-sunrise';
    }

    _setManual(dark) {
        this._schedule.set_string('mode', 'manual');
        Main.layoutManager.screenTransition.run();
        this._interface.set_string('color-scheme', dark ? 'prefer-dark' : 'default');
        this._maybeSetYaru(dark);
    }

    _toggleCurrent() {
        const dark = !this._isDark();
        Main.layoutManager.screenTransition.run();
        this._interface.set_string('color-scheme', dark ? 'prefer-dark' : 'default');
        this._maybeSetYaru(dark);
    }

    _maybeSetYaru(preferDark) {
        if (St.Settings.get().gtkTheme !== 'Yaru')
            return;
        const currentlyDark =
            this._interface.get_string('gtk-theme').endsWith('-dark') &&
            this._interface.get_string('icon-theme').endsWith('-dark');
        if (currentlyDark === preferDark)
            return;
        const theme = preferDark ? 'Yaru-dark' : 'Yaru';
        this._interface.set_string('gtk-theme', theme);
        this._interface.set_string('icon-theme', theme);
    }

    _sync() {
        const dark = this._isDark();
        const auto = this._isAuto();
        this.set({checked: dark});
        this.subtitle = auto ? _('Sunset to Sunrise') : (dark ? _('On') : _('Off'));
        this._offItem.setOrnament(!auto && !dark
            ? PopupMenu.Ornament.DOT
            : PopupMenu.Ornament.NONE);
        this._onItem.setOrnament(!auto && dark
            ? PopupMenu.Ornament.DOT
            : PopupMenu.Ornament.NONE);
        this._autoItem.setOrnament(auto
            ? PopupMenu.Ornament.DOT
            : PopupMenu.Ornament.NONE);
    }
});

export default class DarkStyleScheduleExtension extends Extension {
    enable() {
        const source = Gio.SettingsSchemaSource.get_default();
        const schema = source?.lookup(SCHEDULE_SCHEMA, true);
        if (!schema)
            throw new Error(`Schema "${SCHEDULE_SCHEMA}" not found.`);
        this._schedule = new Gio.Settings({settings_schema: schema});

        this._idleId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._idleId = null;
            this._replaceToggle();
            return GLib.SOURCE_REMOVE;
        });
        GLib.Source.set_name_by_id(this._idleId, '[AnduinOS] Dark Style schedule');
    }

    _replaceToggle() {
        const qs = Main.panel.statusArea.quickSettings;
        if (!qs)
            return;

        this._hidden = [];
        for (const item of qs._darkMode?.quickSettingsItems ?? []) {
            item.visible = false;
            this._hidden.push(item);
        }

        this._toggle = new DarkStyleMenuToggle(this._schedule);
        this._indicator = new QuickSettings.SystemIndicator();
        this._indicator.quickSettingsItems.push(this._toggle);

        const sibling = qs._doNotDisturb?.quickSettingsItems?.[0]
            ?? qs._darkMode?.quickSettingsItems?.[0]
            ?? null;
        if (sibling && qs._addItemsBefore)
            qs._addItemsBefore([this._toggle], sibling, 1);
        else
            qs.addExternalIndicator(this._indicator);
    }

    disable() {
        if (this._idleId) {
            GLib.Source.remove(this._idleId);
            this._idleId = null;
        }
        for (const item of this._hidden ?? [])
            item.visible = true;
        this._hidden = [];
        this._toggle?.destroy();
        this._toggle = null;
        this._indicator?.destroy();
        this._indicator = null;
        this._schedule = null;
    }
}
