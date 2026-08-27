import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'com.anduinos.VoiceTyping';
const OBJECT_PATH = '/com/anduinos/VoiceTyping';
const SETTINGS_SCHEMA = 'com.anduinos.voice-typing';
const SHORTCUT = 'toggle-shortcut';

const DBUS_XML = `
<node>
  <interface name="com.anduinos.VoiceTyping">
    <method name="Start"/>
    <method name="Toggle"/>
    <method name="Pause"/>
    <method name="Stop"/>
    <method name="Quit"/>
    <method name="GetState">
      <arg name="state" type="s" direction="out"/>
      <arg name="detail" type="s" direction="out"/>
    </method>
    <signal name="StateChanged"><arg type="s"/><arg type="s"/></signal>
    <signal name="LevelChanged"><arg type="d"/></signal>
    <signal name="Transcript"><arg type="s"/><arg type="b"/></signal>
  </interface>
</node>`;

const VoiceProxy = Gio.DBusProxy.makeProxyWrapper(DBUS_XML);

const STATE_TEXT = {
    idle: 'Ready',
    listening: 'Listening…',
    recognizing: 'Recognizing…',
    testing: 'Testing microphone…',
    'no-speech': 'No speech detected',
    error: 'Microphone unavailable',
};

const LANGUAGE_TEXT = {
    auto: 'Auto', zh: 'Chinese', en: 'English', es: 'Spanish',
    fr: 'French', de: 'German', ja: 'Japanese', ko: 'Korean',
    ru: 'Russian', pt: 'Portuguese',
};

export default class VoiceTypingExtension extends Extension {
    enable() {
        this._enabled = true;
        this._proxySignalIds = [];
        this._settings = new Gio.Settings({schema_id: SETTINGS_SCHEMA});
        this._state = 'idle';
        this._detail = _('Ready');
        this._targetWindow = global.display.focus_window;
        this._previewTimer = 0;
        this._pasteTimer = 0;
        this._overlayHidden = false;
        this._liveSettingSignal = this._settings.connect(
            'changed::live-transcription',
            () => {
                if (!this._settings.get_boolean('live-transcription') &&
                    !this._previewTimer)
                    this._preview?.hide();
            },
        );
        this._microphoneGicon = new Gio.FileIcon({
            file: this.dir.get_child('audio-input-microphone.svg'),
        });
        this._buildOverlay();
        this._buildIndicator();
        this._virtualKeyboard = Clutter.get_default_backend()
            .get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._clipboard = St.Clipboard.get_default();
        this._focusSignal = global.display.connect('notify::focus-window', () => {
            const focused = global.display.focus_window;
            if (focused)
                this._targetWindow = focused;
        });
        Main.wm.addKeybinding(
            SHORTCUT,
            this._settings,
            Meta.KeyBindingFlags.IGNORE_AUTOREPEAT,
            Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
            () => this._toggleFromShortcut(),
        );
        this._proxy = new VoiceProxy(
            Gio.DBus.session,
            BUS_NAME,
            OBJECT_PATH,
            (proxy, error) => {
                if (!this._enabled)
                    return;
                if (error) {
                    this._setState('error', error.message);
                    return;
                }
                this._proxySignalIds.push(
                    proxy.connectSignal('StateChanged', (_source, _sender, [state, detail]) =>
                        this._setState(state, detail)),
                    proxy.connectSignal('LevelChanged', (_source, _sender, [level]) =>
                        this._setLevel(level)),
                    proxy.connectSignal('Transcript', (_source, _sender, [text, final]) => {
                        if (final)
                            this._previewAndInsert(text);
                        else
                            this._showPartial(text);
                    }),
                );
                proxy.GetStateRemote((result, callError) => {
                    if (!callError && result)
                        this._setState(result[0], result[1]);
                });
            },
        );
    }

    disable() {
        this._enabled = false;
        Main.wm.removeKeybinding(SHORTCUT);
        if (this._focusSignal)
            global.display.disconnect(this._focusSignal);
        this._focusSignal = 0;
        if (this._monitorSignal)
            Main.layoutManager.disconnect(this._monitorSignal);
        this._monitorSignal = 0;
        if (this._previewTimer)
            GLib.Source.remove(this._previewTimer);
        if (this._pasteTimer)
            GLib.Source.remove(this._pasteTimer);
        this._previewTimer = 0;
        this._pasteTimer = 0;
        if (this._liveSettingSignal)
            this._settings.disconnect(this._liveSettingSignal);
        this._liveSettingSignal = 0;
        if (this._stageDragSignal)
            global.stage.disconnect(this._stageDragSignal);
        this._stageDragSignal = 0;
        this._indicator?.destroy();
        this._indicator = null;
        if (this._proxy) {
            for (const identifier of this._proxySignalIds)
                this._proxy.disconnectSignal(identifier);
        }
        this._proxySignalIds = [];
        if (this._root) {
            Main.layoutManager.removeChrome(this._root);
            this._root.destroy();
        }
        this._root = null;
        this._bar = null;
        this._preview = null;
        this._proxy = null;
        this._virtualKeyboard?.run_dispose();
        this._virtualKeyboard = null;
        this._settings = null;
        this._clipboard = null;
        this._targetWindow = null;
        this._microphoneGicon = null;
    }

    _buildOverlay() {
        this._root = new St.BoxLayout({
            vertical: true,
            style_class: 'voice-typing-root',
            visible: false,
            reactive: false,
        });
        this._bar = new St.BoxLayout({
            style_class: 'voice-typing-bar',
            reactive: true,
            can_focus: false,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._root.add_child(this._bar);

        this._handle = new St.Button({
            child: new St.Icon({icon_name: 'list-drag-handle-symbolic', icon_size: 15}),
            style_class: 'voice-typing-handle',
            reactive: true,
            can_focus: false,
            track_hover: true,
        });
        this._handle.connect('button-press-event', (_actor, event) => this._startDrag(event));
        this._bar.add_child(this._handle);

        this._micButton = new St.Button({
            child: new St.Icon({gicon: this._microphoneGicon, icon_size: 21}),
            style_class: 'voice-typing-microphone',
            reactive: true,
            can_focus: false,
            track_hover: true,
        });
        this._micButton.connect('clicked', () => this._call('Toggle'));
        this._bar.add_child(this._micButton);

        this._wave = new St.BoxLayout({
            style_class: 'voice-typing-wave',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._levelBars = [];
        for (let index = 0; index < 5; index++) {
            const levelBar = new St.Widget({style_class: 'voice-typing-level-bar'});
            this._wave.add_child(levelBar);
            this._levelBars.push(levelBar);
        }
        this._bar.add_child(this._wave);

        this._statusLabel = new St.Label({
            text: _('Ready'),
            style_class: 'voice-typing-status',
            y_align: Clutter.ActorAlign.CENTER,
            x_expand: true,
        });
        this._bar.add_child(this._statusLabel);

        this._languageButton = new St.Button({
            label: _('Auto') + ' ▾',
            style_class: 'voice-typing-language',
            can_focus: false,
            track_hover: true,
        });
        this._languageButton.connect('clicked', () => this._openSettings());
        this._bar.add_child(this._languageButton);

        const close = new St.Button({
            child: new St.Icon({icon_name: 'window-close-symbolic', icon_size: 16}),
            style_class: 'voice-typing-action',
            can_focus: false,
            track_hover: true,
        });
        close.connect('clicked', () => this._dismissOverlay());
        this._bar.add_child(close);

        this._preview = new St.Label({
            style_class: 'voice-typing-preview',
            visible: false,
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._preview.clutter_text.set_line_wrap(true);
        this._preview.clutter_text.set_ellipsize(3);
        this._root.add_child(this._preview);
        Main.layoutManager.addChrome(this._root, {
            affectsStruts: false,
            trackFullscreen: false,
        });
        this._root.connect('notify::width', () => this._positionOverlay());
        this._monitorSignal = Main.layoutManager.connect('monitors-changed', () => this._positionOverlay());
        this._setLevel(0);
    }

    _buildIndicator() {
        this._indicator = new PanelMenu.Button(0.0, _('Voice Typing'), false);
        this._indicatorIcon = new St.Icon({
            gicon: this._microphoneGicon,
            style_class: 'system-status-icon',
        });
        this._indicator.add_child(this._indicatorIcon);
        this._toggleItem = new PopupMenu.PopupMenuItem(_('Start voice typing'));
        this._toggleItem.connect('activate', () => this._toggleFromShortcut());
        this._indicator.menu.addMenuItem(this._toggleItem);
        this._indicator.menu.addAction(_('Voice Typing Settings'), () => this._openSettings());
        this._indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._indicator.menu.addAction(_('Stop and close'), () => this._dismissOverlay());
        Main.panel.addToStatusArea('anduinos-voice-typing', this._indicator);
    }

    _toggleFromShortcut() {
        this._targetWindow = global.display.focus_window ?? this._targetWindow;
        this._overlayHidden = false;
        this._root.show();
        this._positionOverlay();
        this._call('Toggle');
    }

    _dismissOverlay() {
        this._overlayHidden = true;
        this._root?.hide();
        this._preview?.hide();
        if (this._previewTimer)
            GLib.Source.remove(this._previewTimer);
        if (this._pasteTimer)
            GLib.Source.remove(this._pasteTimer);
        this._previewTimer = 0;
        this._pasteTimer = 0;
        // Do not auto-start an already stopped daemon merely to ask it to quit.
        if (this._proxy?.get_name_owner())
            this._call('Quit');
    }

    _call(method) {
        if (!this._proxy)
            return;
        const remote = this._proxy[`${method}Remote`];
        if (remote)
            remote.call(this._proxy, (_result, error) => {
                if (error)
                    this._setState('error', error.message);
            });
    }

    _setState(state, detail) {
        if (!this._enabled || !this._root)
            return;
        this._state = state;
        this._detail = detail;
        this._statusLabel.text = _(STATE_TEXT[state] ?? detail ?? state);
        this._bar.remove_style_class_name('listening');
        this._bar.remove_style_class_name('error');
        this._micButton.remove_style_class_name('listening');
        if (state === 'listening' || state === 'recognizing' || state === 'no-speech') {
            this._bar.add_style_class_name('listening');
            this._micButton.add_style_class_name('listening');
        } else if (state === 'error') {
            this._bar.add_style_class_name('error');
            this._statusLabel.text = detail || _(STATE_TEXT.error);
        }
        if (state === 'error') {
            this._indicatorIcon.gicon = null;
            this._indicatorIcon.icon_name = 'dialog-warning-symbolic';
        } else {
            this._indicatorIcon.icon_name = null;
            this._indicatorIcon.gicon = this._microphoneGicon;
        }
        this._toggleItem.label.text = ['listening', 'recognizing', 'no-speech'].includes(state)
            ? _('Stop voice typing')
            : _('Start voice typing');
        const language = this._settings.get_string('language') || 'auto';
        this._languageButton.label = `${_(LANGUAGE_TEXT[language] ?? language)} ▾`;
        if (!this._overlayHidden) {
            this._root.show();
            this._positionOverlay();
        } else
            this._root.hide();
        if (!['listening', 'recognizing', 'no-speech'].includes(state))
            this._setLevel(0);
        if (!['listening', 'recognizing', 'no-speech'].includes(state) &&
            !this._previewTimer)
            this._preview.hide();
    }

    _setLevel(level) {
        if (!this._enabled || !this._levelBars)
            return;
        const activeBars = Math.round(Math.max(0, Math.min(1, level)) * this._levelBars.length);
        this._levelBars.forEach((bar, index) => {
            const distance = Math.abs(index - 2);
            const height = index < activeBars ? 13 - distance * 2 : 4;
            bar.set_height(height);
            bar.opacity = index < activeBars ? 255 : 80;
        });
    }

    _previewAndInsert(text) {
        if (!this._enabled || this._overlayHidden || !text)
            return;
        if (this._previewTimer)
            GLib.Source.remove(this._previewTimer);
        const delay = this._settings.get_boolean('show-preview')
            ? this._settings.get_uint('preview-delay')
            : 0;
        if (delay > 0) {
            this._preview.text = text;
            this._preview.show();
            this._positionOverlay();
        }
        this._previewTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
            this._previewTimer = 0;
            this._preview.hide();
            this._insertText(text);
            return GLib.SOURCE_REMOVE;
        });
    }

    _showPartial(text) {
        if (!this._enabled || this._overlayHidden || !text ||
            !this._settings.get_boolean('live-transcription') ||
            this._previewTimer)
            return;
        this._preview.text = text;
        this._preview.show();
        this._positionOverlay();
    }

    _insertText(text) {
        const purpose = Main.inputMethod?.content_purpose;
        if (Clutter.InputContentPurpose &&
            [Clutter.InputContentPurpose.PASSWORD, Clutter.InputContentPurpose.PIN].includes(purpose)) {
            this._setState('error', _('Voice typing is disabled in password fields'));
            return;
        }
        const focused = global.display.focus_window ?? this._targetWindow;
        if (!focused) {
            this._setState('error', _('Select a text field before dictating'));
            return;
        }
        this._targetWindow = focused;
        const prepared = this._prepareSpacing(text);
        this._clipboard.set_text(St.ClipboardType.CLIPBOARD, prepared);
        if (this._pasteTimer)
            GLib.Source.remove(this._pasteTimer);
        this._pasteTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 35, () => {
            this._pasteTimer = 0;
            const windowClass = (focused.get_wm_class() ?? '').toLowerCase();
            const terminal = ['terminal', 'kgx', 'console', 'alacritty', 'kitty', 'konsole']
                .some(name => windowClass.includes(name));
            this._pressPaste(terminal);
            return GLib.SOURCE_REMOVE;
        });
    }

    _prepareSpacing(text) {
        if (/^[\n\t]+$/.test(text) || /^[,.;:!?，。；：！？]+$/.test(text))
            return text;
        const containsCjk = /[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/u.test(text);
        return containsCjk || /\s$/.test(text) ? text : `${text} `;
    }

    _pressPaste(terminal) {
        const time = Clutter.get_current_event_time() * 1000;
        const press = key => this._virtualKeyboard.notify_keyval(time, key, Clutter.KeyState.PRESSED);
        const release = key => this._virtualKeyboard.notify_keyval(time, key, Clutter.KeyState.RELEASED);
        press(Clutter.KEY_Control_L);
        if (terminal)
            press(Clutter.KEY_Shift_L);
        press(Clutter.KEY_v);
        release(Clutter.KEY_v);
        if (terminal)
            release(Clutter.KEY_Shift_L);
        release(Clutter.KEY_Control_L);
    }

    _positionOverlay() {
        if (!this._root || !this._root.visible)
            return;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const offset = this._settings.get_int('overlay-x');
        const x = Math.max(
            monitor.x + 8,
            Math.min(monitor.x + monitor.width - this._root.width - 8,
                monitor.x + Math.round((monitor.width - this._root.width) / 2) + offset),
        );
        const panelHeight = Main.panel?.height ?? 0;
        this._root.set_position(x, monitor.y + panelHeight + 12);
    }

    _startDrag(event) {
        if (event.get_button() !== Clutter.BUTTON_PRIMARY)
            return Clutter.EVENT_PROPAGATE;
        const [startX] = event.get_coords();
        const actorStartX = this._root.x;
        if (this._stageDragSignal)
            this._finishDrag();
        this._stageDragSignal = global.stage.connect('captured-event', (_stage, captured) => {
            if (captured.type() === Clutter.EventType.MOTION) {
                // A release outside the handle is not guaranteed to reach this
                // callback.  Never treat a later hover as part of the old drag.
                if (!(captured.get_state() & Clutter.ModifierType.BUTTON1_MASK)) {
                    this._finishDrag();
                    return Clutter.EVENT_PROPAGATE;
                }
                const [currentX] = captured.get_coords();
                const monitor = Main.layoutManager.primaryMonitor;
                const x = Math.max(
                    monitor.x + 8,
                    Math.min(monitor.x + monitor.width - this._root.width - 8,
                        actorStartX + currentX - startX),
                );
                this._root.set_x(x);
                return Clutter.EVENT_STOP;
            }
            if (captured.type() === Clutter.EventType.BUTTON_RELEASE &&
                captured.get_button() === Clutter.BUTTON_PRIMARY) {
                this._finishDrag();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        return Clutter.EVENT_STOP;
    }

    _finishDrag() {
        if (!this._stageDragSignal)
            return;
        global.stage.disconnect(this._stageDragSignal);
        this._stageDragSignal = 0;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._root || !this._settings)
            return;
        const centered = monitor.x + Math.round((monitor.width - this._root.width) / 2);
        this._settings.set_int('overlay-x', Math.round(this._root.x - centered));
    }

    _openSettings() {
        try {
            Gio.Subprocess.new(['anduinos-whisper-gtk'], Gio.SubprocessFlags.NONE);
        } catch (error) {
            this._setState('error', error.message);
        }
    }
}
