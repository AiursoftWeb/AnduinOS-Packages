// SPDX-License-Identifier: GPL-3.0-or-later
import Gio from 'gi://Gio';
import {fluentThemeName, UnlockThemeController} from '../assets/unlock-theme@anduinos.com/policy.js';

let cases = 0;
function assert(condition, message) {
    if (!condition)
        throw new Error(message);
}
function test(name, callback) {
    callback();
    cases++;
    print(`PASS ${name}`);
}
function fixture() {
    const state = {current: null, overlay: false, writes: [], errors: [], fail: null};
    const controller = new UnlockThemeController({
        current: () => state.current,
        set: path => {
            state.current = path;
            state.writes.push(path);
            if (path && state.fail === 'theme')
                throw new Error('bad theme');
        },
        addReadability: () => {
            if (state.fail === 'overlay')
                throw new Error('bad overlay');
            state.overlay = true;
        },
        removeReadability: () => { state.overlay = false; },
        report: error => state.errors.push(error),
    });
    return {state, controller};
}

test('accept supported Fluent names only', () => {
    for (const name of ['Fluent', 'Fluent-round-Dark', 'Fluent-purple-round-Light'])
        assert(fluentThemeName(name), name);
    for (const name of ['', null, '../Fluent', 'Fluent/../../x', 'Adwaita',
        'Fluent.css', 'Fluent-', 'Fluent-' + 'a'.repeat(128)])
        assert(!fluentThemeName(name), String(name));
});
test('load selected theme and readability once', () => {
    const {state, controller} = fixture();
    controller.update('/theme/Fluent.css');
    controller.update('/theme/Fluent.css');
    assert(state.current === '/theme/Fluent.css' && state.overlay, 'theme not applied');
    assert(state.writes.length === 1, 'unchanged theme reloaded');
});
test('high contrast, missing theme and disabled User Themes use native fallback', () => {
    const {state, controller} = fixture();
    controller.update('/theme/Fluent.css');
    controller.update(null);
    assert(state.current === null && !state.overlay, 'fallback not restored');
});
test('changing theme while locked replaces overlay and stylesheet', () => {
    const {state, controller} = fixture();
    controller.update('/theme/Fluent-Dark.css');
    controller.update('/theme/Fluent-Light.css');
    assert(state.current === '/theme/Fluent-Light.css' && state.overlay, 'change failed');
});
test('theme load failure rolls back to native, then can retry', () => {
    const {state, controller} = fixture();
    state.fail = 'theme';
    controller.update('/theme/Fluent.css');
    assert(state.current === null && !state.overlay && state.errors.length === 1, 'failure leaked');
    state.fail = null;
    controller.update('/theme/Fluent.css');
    assert(state.overlay, 'retry failed');
});
test('overlay load failure also rolls back theme', () => {
    const {state, controller} = fixture();
    state.fail = 'overlay';
    controller.update('/theme/Fluent.css');
    assert(state.current === null && !state.overlay && state.errors.length === 1, 'overlay failure leaked');
});
test('do not take over another extension stylesheet', () => {
    const {state, controller} = fixture();
    state.current = '/other/theme.css';
    controller.update('/theme/Fluent.css');
    assert(state.current === '/other/theme.css' && state.writes.length === 0, 'clobbered owner');
});
test('do not reset another owner during disable', () => {
    const {state, controller} = fixture();
    controller.update('/theme/Fluent.css');
    state.current = '/other/theme.css';
    controller.reset();
    assert(state.current === '/other/theme.css' && !state.overlay, 'clobbered owner on disable');
});
test('ownership loss removes only our overlay', () => {
    const {state, controller} = fixture();
    controller.update('/theme/Fluent.css');
    state.current = '/other/theme.css';
    controller.update('/theme/Fluent-Light.css');
    assert(state.current === '/other/theme.css' && !state.overlay, 'ownership loss mishandled');
});
test('repeated enable-disable cycles do not retain stylesheets', () => {
    const {state, controller} = fixture();
    for (let i = 0; i < 20; i++) {
        controller.update('/theme/Fluent.css');
        controller.reset();
        controller.reset();
        assert(state.current === null && !state.overlay, 'teardown failed');
    }
});
function extensionFixture() {
    const path = Gio.File.new_for_uri(import.meta.url).get_parent().get_parent()
        .get_child('assets/unlock-theme@anduinos.com/extension.js');
    const [, bytes] = path.load_contents(null);
    const source = new TextDecoder().decode(bytes)
        .replace(/^import .*;\s*$/gm, '')
        .replace('export default class', 'class');
    const {state} = fixture();
    const pending = new Map();
    const signals = new Map();
    let serial = 0;
    const settings = {
        high_contrast: false,
        name: 'Fluent-round-Light',
        connect: (signal, callback) => {
            signals.set(++serial, callback);
            return serial;
        },
        disconnect: id => signals.delete(id),
        get_string: () => settings.name,
    };
    state.schema = true;
    state.userEnabled = true;
    state.files = ['/system/themes/Fluent-round-Light/gnome-shell/gnome-shell.css'];
    const theme = {
        load_stylesheet: () => { state.overlay = true; },
        unload_stylesheet: () => { state.overlay = false; },
    };
    const GioStub = {
        SettingsSchemaSource: {get_default: () => ({lookup: () => state.schema ? {} : null})},
        Settings: class { constructor() { return settings; } },
        File: {new_for_path: name => ({query_exists: () => state.files.includes(name)})},
    };
    const GLibStub = {
        build_filenamev: parts => parts.join('/'),
        get_home_dir: () => '/home/test',
        get_user_data_dir: () => '/home/test/.local/share',
        get_system_data_dirs: () => ['/system'],
        idle_add: (_priority, callback) => { pending.set(++serial, callback); return serial; },
        Source: {remove: id => pending.delete(id)},
    };
    const StStub = {
        Settings: {get: () => settings},
        ThemeContext: {get_for_stage: () => ({get_theme: () => theme})},
    };
    const MainStub = {
        getThemeStylesheet: () => state.current ? {get_path: () => state.current} : null,
        setThemeStylesheet: value => { state.current = value; state.writes.push(value); },
        loadTheme: () => {},
        extensionManager: {lookup: () => ({enabled: state.userEnabled})},
    };
    const Base = class { constructor() { this.dir = {get_child: name => name}; } };
    const Class = new Function('Gio', 'GLib', 'St', 'Extension', 'Main',
        'fluentThemeName', 'UnlockThemeController', 'global', `${source}; return UnlockTheme;`)(
        GioStub, GLibStub, StStub, Base, MainStub, fluentThemeName, UnlockThemeController, {});
    const flush = () => {
        const callbacks = [...pending.values()];
        pending.clear();
        for (const callback of callbacks)
            callback();
    };
    return {extension: new Class(), state, settings, signals, pending, flush};
}
test('real extension applies and disconnects all signals on disable', () => {
    const {extension, state, signals} = extensionFixture();
    extension.enable();
    assert(state.overlay && signals.size === 2, 'enable failed');
    extension.disable();
    extension.disable();
    assert(!state.overlay && state.current === null && signals.size === 0, 'teardown leaked');
});
test('real extension reacts to high contrast during lock', () => {
    const {extension, state, settings, signals, flush} = extensionFixture();
    extension.enable();
    settings.high_contrast = true;
    for (const callback of signals.values())
        callback();
    flush();
    assert(state.current === null && !state.overlay, 'high contrast overridden');
    settings.high_contrast = false;
    for (const callback of signals.values())
        callback();
    flush();
    assert(state.overlay, 'Fluent not restored');
    extension.disable();
});
test('real extension falls back when schema, theme, or User Themes unavailable', () => {
    for (const variant of ['schema', 'files', 'userEnabled', 'highContrast', 'nonFluent']) {
        const {extension, state, settings} = extensionFixture();
        if (variant === 'files')
            state.files = [];
        else if (variant === 'highContrast')
            settings.high_contrast = true;
        else if (variant === 'nonFluent')
            settings.name = 'Adwaita';
        else
            state[variant] = false;
        extension.enable();
        assert(state.current === null && !state.overlay, `${variant} fallback failed`);
        extension.disable();
    }
});
test('real extension preserves User Themes search precedence', () => {
    const {extension, state} = extensionFixture();
    const userPath = '/home/test/.themes/Fluent-round-Light/gnome-shell/gnome-shell.css';
    state.files.push(userPath);
    extension.enable();
    assert(state.current === userPath, 'precedence differs from desktop User Themes');
    extension.disable();
});
test('settings notifications coalesce and pending work is canceled on disable', () => {
    const {extension, state, settings, signals, pending, flush} = extensionFixture();
    extension.enable();
    settings.high_contrast = true;
    for (let i = 0; i < 10; i++) {
        for (const callback of signals.values())
            callback();
    }
    assert(pending.size === 1 && state.overlay, 'reloaded inside a settings notification');
    extension.disable();
    assert(pending.size === 0, 'idle callback survived disable');
    flush();
    assert(state.current === null && !state.overlay, 'disabled extension reapplied theme');
});
print(`Unlock theme tests passed (${cases} cases)`);
