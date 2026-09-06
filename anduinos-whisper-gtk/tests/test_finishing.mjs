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
        controller._previewAndInsert(controller._finalQueue.shift());
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
controller._previewAndInsert('one');
controller._previewAndInsert('two');
drain();
assert.deepEqual(pasted, ['one ', 'two ']);
controller._previewAndInsert('cancel before paste');
const [previewId, previewCallback] = timers.entries().next().value;
timers.delete(previewId);
previewCallback();
controller._closeUi();
drain();
assert.deepEqual(pasted, ['one ', 'two ']);
console.log('Finish retains final text; final bursts stay ordered; close cancels pending/late text.');
