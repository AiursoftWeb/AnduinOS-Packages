'use strict';

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const POWER_SUPPLY_PATH = '/sys/class/power_supply';
const COMMON_BATTERY_NAMES = ['BAT0', 'BAT1'];

function hasBattery() {
    for (const batteryName of COMMON_BATTERY_NAMES) {
        if (Gio.File.new_for_path(`${POWER_SUPPLY_PATH}/${batteryName}`).query_exists(null))
            return true;
    }

    let enumerator = null;
    try {
        const dir = Gio.File.new_for_path(POWER_SUPPLY_PATH);
        enumerator = dir.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NONE, null);
        let info;
        while ((info = enumerator.next_file(null)) !== null) {
            if (info.get_name().startsWith('BAT'))
                return true;
        }
    } catch (e) {
        logError(e);
    } finally {
        enumerator?.close(null);
    }

    return false;
}

export default class NoPowerIconExtension extends Extension {
    enable() {
        this._systemIndicator = Main.panel.statusArea.quickSettings?._system ?? null;
        this._systemIndicatorHidden = false;
        this._idleSourceId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._idleSourceId = null;

            if (!this._systemIndicator || hasBattery())
                return GLib.SOURCE_REMOVE;

            this._systemIndicator.hide();
            this._systemIndicatorHidden = true;
            return GLib.SOURCE_REMOVE;
        });
        GLib.Source.set_name_by_id(this._idleSourceId, '[AnduinOS] hide power icon');
    }

    disable() {
        if (this._idleSourceId) {
            GLib.Source.remove(this._idleSourceId);
            this._idleSourceId = null;
        }

        if (this._systemIndicator && this._systemIndicatorHidden)
            this._systemIndicator.show();

        this._systemIndicator = null;
        this._systemIndicatorHidden = false;
    }
}
