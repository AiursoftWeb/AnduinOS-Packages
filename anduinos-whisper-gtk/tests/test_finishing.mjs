// Run production controller methods with a fake clock; no desktop input injection.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const timers = new Map();
let sequence = 0;
const context = {
    Extension: class {}, _: value => value,
    Gio: {DBusProxy: {makeProxyWrapper: () => class {}}},
    GLib: {
        PRIORITY_DEFAULT: 0, SOURCE_REMOVE: false,
        timeout_add: (_priority, _delay, callback) => {
            timers.set(++sequence, callback);
            return sequence;
        },
        Source: {remove: id => timers.delete(id)},
    },
};
let source = fs.readFileSync(new URL('../data/voice-typing@anduinos.com/extension.js', import.meta.url), 'utf8');
source = source.replace(/^import .*;\n/gm, '').replace('export default class ', 'class ');
const className = source.match(/class (\w+) extends Extension/)[1];
vm.runInNewContext(source + `\nglobalThis.Controller = ${className};`, context);
const controller = new context.Controller();
Object.assign(controller, {
    _enabled: true, _uiState: 'listening', _finishPending: false,
    _finalQueue: [], _previewTimer: 0, _pasteTimer: 0, _proxy: {},
    _settings: {get_boolean: () => false, get_uint: () => 0},
    _hidePreview: () => {}, _showPreview: () => {},
    _setUiState(state) { this._uiState = state; },
});
const calls = [], inserted = [];
controller._call = method => calls.push(method);
controller._insertText = text => {
    inserted.push(text);
    if (controller._finalQueue.length)
        controller._previewAndInsert(...controller._finalQueue.shift());
};
function drain() {
    while (timers.size) {
        const [id, callback] = timers.entries().next().value;
        timers.delete(id);
        callback();
    }
}
controller._stopListening();
assert.equal(controller._uiState, 'ready');
assert.equal(controller._finishPending, true);
assert.deepEqual(calls, ['Finish']);
controller._previewAndInsert('first');
controller._previewAndInsert('second');
drain();
assert.deepEqual(inserted, ['first', 'second']);
controller._previewAndInsert('cancel me');
controller._closeUi();
controller._previewAndInsert('late result');
drain();
assert.deepEqual(inserted, ['first', 'second']);
assert.equal(calls.at(-1), 'Quit');
assert.equal(controller._finishPending, false);
// Exercise the real clipboard/paste scheduling as well as the preview queue.
let clipboard = '';
const pasted = [];
context.Main = {inputMethod: {content_purpose: 0}};
context.Clutter = {InputContentPurpose: {PASSWORD: 1, PIN: 2}};
context.St = {ClipboardType: {CLIPBOARD: 0}};
context.global = {display: {focus_window: {get_wm_class: () => 'terminal'}}};
controller._clipboard = {set_text: (_kind, text) => { clipboard = text; }};
controller._pressPaste = () => pasted.push(clipboard);
controller._insertText = context.Controller.prototype._insertText;
controller._uiState = 'ready';
controller._finishPending = true;
const acknowledged = [];
controller._proxyReady = true;
controller._proxy.ReportDeliveryRemote = ticket => acknowledged.push(ticket);
controller._previewAndInsert('one', 41);
controller._previewAndInsert('two', 42);
drain();
assert.deepEqual(pasted, ['one ', 'two ']);
assert.deepEqual(acknowledged, [41, 42]);
controller._previewAndInsert('cancel before paste');
const [previewId, previewCallback] = timers.entries().next().value;
timers.delete(previewId);
previewCallback();
controller._closeUi();
drain();
assert.deepEqual(pasted, ['one ', 'two ']);
assert.deepEqual(acknowledged, [41, 42]);
// A password field gaining focus during the 35 ms paste delay is protected too.
controller._uiState = 'ready';
controller._finishPending = true;
controller._previewAndInsert('must not paste', 43);
const [raceId, raceCallback] = timers.entries().next().value;
timers.delete(raceId);
raceCallback();
context.Main.inputMethod.content_purpose = 1;
drain();
assert.deepEqual(pasted, ['one ', 'two ']);
assert.deepEqual(acknowledged, [41, 42]);
console.log('Finish retains final text; final bursts stay ordered; close cancels pending/late text.');

// Calibration is cancellable active preparation, not recording or an error.
const statuses = [], invoked = [];
Object.assign(controller, {
    _uiState: 'listening', _finishPending: false,
    _root: {show() {}, hide() {}},
    _bar: {add_style_class_name() {}, remove_style_class_name() {}},
    _micButton: {add_style_class_name() {}, remove_style_class_name() {}},
    _statusLabel: {}, _languageButton: {},
    _positionOverlay() {}, _emitUiState: detail => statuses.push(detail),
    _invoke: method => invoked.push(method),
});
controller._settings.get_string = () => 'auto';
context._ = text => `translated:${text}`;
controller._setState('calibrating', 'countdown:quick:10');
assert.equal(controller._uiState, 'listening');
assert.equal(controller._statusLabel.text,
    'translated:Optimizing recognition for first use: 10 seconds remaining (microphone off; click the microphone to cancel)');
assert.equal(statuses.at(-1), controller._statusLabel.text);
assert.deepEqual(invoked, []);
controller._setState('calibrating', 'countdown:full:60');
assert.equal(controller._statusLabel.text,
    'translated:Measuring recognition performance: 60 seconds remaining (microphone off; click the microphone to cancel)');
controller._setState('preparing', 'Loading speech model…');
assert.equal(controller._statusLabel.text, 'translated:Loading speech model…');
controller._stopListening();
assert.equal(calls.at(-1), 'Finish');
assert.equal(controller._uiState, 'ready');
controller._uiState = 'closed';
controller._finishPending = false;
controller._setState('calibrating', 'late calibration notice');
assert.equal(invoked.at(-1), 'Quit');
assert.equal(controller._uiState, 'closed');
console.log('Calibration shows a localized microphone-off notice and remains cancellable.');

// Shell shutdown can arrive after chrome actors have already been disposed.
controller._root = {hide() { throw new Error('actor already disposed'); }};
controller._proxy = {get_name_owner: () => ':1.123'};
controller._proxyReady = true;
controller._quitForShellShutdown();
assert.equal(controller._enabled, false);
assert.equal(controller._uiState, 'closed');
assert.equal(invoked.at(-1), 'Quit');
console.log('Shell shutdown stops the daemon without accessing disposed actors.');
