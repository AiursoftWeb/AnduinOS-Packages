"""Deterministic endpoint, queue, cancellation and finishing regression tests."""
from array import array
from pathlib import Path
import subprocess
import os
import sys
import random
import threading
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from anduinos_whisper_framework.audio import AudioCapture, Gst
from anduinos_whisper_framework.daemon import VoiceTypingService
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "benchmarks"))
from benchmark_engine import WhisperEngine
from anduinos_whisper_framework.errors import RecognitionCancelled
from anduinos_whisper_framework.work_queue import RecognitionQueue


def pcm(level=1000):
    return array('h', [level, -level] * 160).tobytes()  # 20 ms


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.chunks, self.partials, self.errors = [], [], []
        self.capture = AudioCapture('', self.chunks.append, self.partials.append,
                                    lambda _: None, self.errors.append, lambda: None)

    def feed(self, frames, level, voiced):
        for _ in range(frames):
            self.capture._consume(pcm(level), voiced=voiced)

    def test_loud_non_speech_does_not_trigger(self):
        self.feed(1500, 8000, False)
        self.assertEqual(self.chunks, [])
        self.assertEqual(self.partials, [])
        self.assertFalse(self.capture._speaking)

    def test_quiet_speech_is_not_blocked_by_legacy_energy_threshold(self):
        self.feed(50, 100, True)
        self.feed(45, 2000, False)  # noisy non-speech after a quiet utterance
        self.assertEqual(len(self.chunks), 1)
        self.assertLess(len(self.chunks[0]) / 32000, 2)

    def test_continuous_voice_still_has_hard_duration_limit(self):
        self.feed(1250, 1000, True)
        self.assertEqual(len(self.chunks), 2)
        self.assertTrue(all(len(c) <= 12 * 32000 for c in self.chunks))

    def test_short_voice_spike_is_not_a_phrase(self):
        self.feed(2, 1000, True)
        self.feed(50, 2000, False)
        self.capture.stop(flush=True)
        self.assertEqual(self.chunks, [])

    def test_finish_flushes_short_phrase_but_cancel_does_not(self):
        self.feed(10, 100, True)
        self.capture.stop(flush=True)
        self.assertEqual(len(self.chunks), 1)
        self.assertGreaterEqual(len(self.chunks[0]), 16000)
        self.feed(10, 100, True)
        self.capture.stop(flush=False)
        self.assertEqual(len(self.chunks), 1)

    def test_watchdog_is_independent_of_audio_callbacks(self):
        self.capture._pipeline = Mock()
        self.capture._last_sample = 1
        with patch('anduinos_whisper_framework.audio.time.monotonic', return_value=7):
            self.assertFalse(self.capture._check_stall())
        self.assertIn('stopped providing audio', self.errors[0])
        self.capture._pipeline = None

    def test_native_frames_preserve_order_and_finish_keeps_partial_tail(self):
        self.capture._vad = Mock()
        detector = self.capture._vad
        detector.classify.side_effect = [0.1, 0.9]
        self.capture._consume = Mock()
        data = pcm() * 3 + pcm()[:320]  # 2240 bytes, two complete frames and a tail
        for offset in range(0, len(data), 320):
            self.capture._process_pcm(data[offset:offset+320])
        self.assertEqual([call.args[0] for call in detector.classify.call_args_list],
                         [data[:1024], data[1024:2048]])
        self.assertEqual([call.kwargs['voiced'] for call in self.capture._consume.call_args_list],
                         [False, True])
        self.capture.stop(flush=True)
        self.capture._consume.assert_called_with(data[2048:], voiced=True)
        self.assertEqual(detector.classify.call_count, 2)
        detector.close.assert_called_once()
        self.assertEqual(self.capture._pending_pcm, b'')

    def test_cancel_discards_unclassified_audio_and_closes_detector(self):
        detector = self.capture._vad = Mock()
        self.capture._consume = Mock()
        self.capture._process_pcm(b'\0' * 320)
        self.capture.stop(flush=False)
        self.capture._consume.assert_not_called()
        detector.classify.assert_not_called()
        detector.close.assert_called_once()
        self.assertIsNone(self.capture._vad)

    def test_microphone_test_meter_does_not_require_native_detector(self):
        self.capture._detect_speech = False
        self.capture._consume = Mock()
        self.capture._process_pcm(pcm())
        self.capture._consume.assert_called_once_with(pcm(), voiced=False)

    def test_dsp_uses_vad_and_moderate_processing_without_fake_aec(self):
        dsp = Gst.ElementFactory.make('webrtcdsp')
        if dsp is None:
            self.skipTest('Install gstreamer1.0-plugins-bad for DSP integration')
        AudioCapture.configure_processor(dsp, True)
        self.assertFalse(dsp.get_property('voice-detection'))
        self.assertTrue(dsp.get_property('noise-suppression'))
        self.assertFalse(dsp.get_property('echo-cancel'))
        AudioCapture.configure_processor(dsp, False)
        self.assertFalse(dsp.get_property('voice-detection'))
        self.assertFalse(dsp.get_property('noise-suppression'))
        self.assertFalse(dsp.get_property('gain-control'))

    def test_real_dsp_rejects_stationary_noise_without_opening_microphone(self):
        if not os.environ.get('ANDUINOS_VAD_MODEL') or not os.environ.get('ANDUINOS_VOICE_WORKER'):
            self.skipTest('Native VAD worker/model not supplied')
        from anduinos_whisper_framework.vad import VadEngine
        self.capture._vad = VadEngine(os.environ['ANDUINOS_VAD_MODEL'], os.environ['ANDUINOS_VOICE_WORKER'])
        self.capture._vad.start()
        self.addCleanup(self.capture.stop, False)
        if Gst.ElementFactory.find('webrtcdsp') is None:
            self.skipTest('Install gstreamer1.0-plugins-bad for DSP integration')
        pipeline = Gst.parse_launch('appsrc name=input format=time ! '
            'audio/x-raw,format=S16LE,rate=16000,channels=1,layout=interleaved ! '
            'webrtcdsp name=dsp ! appsink name=output emit-signals=true sync=false')
        AudioCapture.configure_processor(pipeline.get_by_name('dsp'), False)
        pipeline.get_by_name('output').connect('new-sample', self.capture._new_sample)
        bus = pipeline.get_bus()
        rng = random.Random(42)
        try:
            pipeline.set_state(Gst.State.PLAYING)
            source = pipeline.get_by_name('input')
            for index in range(1500):  # 15 s of -35 dBFS synthetic white noise
                samples = array('h', [int(rng.gauss(0, 580)) for _ in range(160)])
                buffer = Gst.Buffer.new_allocate(None, 320, None)
                buffer.fill(0, samples.tobytes())
                buffer.pts = index * Gst.SECOND // 100
                buffer.duration = Gst.SECOND // 100
                self.assertEqual(source.emit('push-buffer', buffer), Gst.FlowReturn.OK)
            source.emit('end-of-stream')
            message = bus.timed_pop_filtered(10 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            self.assertIsNotNone(message)
            self.assertEqual(message.type, Gst.MessageType.EOS)
            self.assertEqual(self.chunks, [])
            self.assertFalse(self.capture._speaking)
        finally:
            pipeline.set_state(Gst.State.NULL)
            bus.set_sync_handler(None)


class QueueTests(unittest.TestCase):
    def test_previews_are_coalesced_and_final_has_priority(self):
        queue = RecognitionQueue()
        for index in range(1000):
            queue.put((1, index, 'partial'))
        self.assertEqual(queue.get(), (1, 999, 'partial'))
        queue.put((1, 1000, 'partial'))
        queue.put((0, 1001, 'final'))
        self.assertFalse(queue.put((1, 1002, 'partial')))
        self.assertEqual(queue.get(), (0, 1001, 'final'))
        self.assertIsNone(queue._partial)

    def test_final_backlog_is_bounded_and_rejection_is_explicit(self):
        queue = RecognitionQueue(max_finals=2)
        self.assertTrue(queue.put((0, 1)))
        self.assertTrue(queue.put((0, 2)))
        self.assertFalse(queue.put((0, 3)))
        queue.clear()
        self.assertTrue(queue.put((0, 4)))
        queue.put((-1, 5))
        self.assertEqual(queue.get(), (-1, 5))


def service_stub():
    from anduinos_whisper_framework.diagnostics import PerformanceHistory
    service = VoiceTypingService.__new__(VoiceTypingService)
    service.active, service.testing, service.pending = True, False, 0
    service.session_id = 7
    service.partial_generation = service.partial_floor = service.work_sequence = 0
    service.work_lock = threading.Lock()
    service.current_cancel = service.current_kind = None
    service.audio_queue = RecognitionQueue()
    service.performance = PerformanceHistory()
    service.settings = Mock()
    service.settings.get_string.return_value = 'base'
    service.settings.get_boolean.return_value = True
    service._set_state = Mock()
    service._emit = Mock()
    service._play_cue = Mock()
    service.finish_message = ''
    service.capture = Mock()
    return service


class ServiceTests(unittest.TestCase):
    @patch('anduinos_whisper_framework.daemon.model_installed', return_value=True)
    def test_start_prepares_without_opening_microphone_and_finish_cancels(self, _installed):
        service = service_stub()
        service.active = False
        service.settings.get_string.side_effect = lambda key: {
            'model': 'base', 'language': 'zh', 'recognition-backend': 'auto'}[key]
        service.settings.get_uint.return_value = 0
        service._new_capture = Mock()
        service.start()
        session = service.session_id
        work = service.audio_queue.get(timeout=0)
        self.assertEqual(work[2], 'prepare')
        self.assertEqual(work[7]['model'], 'base')
        service._new_capture.assert_not_called()
        service._set_state.assert_called_with('preparing', 'Preparing recognition…')
        cancel = service.current_cancel = threading.Event()
        service.finish()
        self.assertTrue(cancel.is_set())
        service._prepared(session, {'model': 'base'})
        service._new_capture.assert_not_called()

    def test_prepared_opens_microphone_only_for_active_session(self):
        service = service_stub()
        service.capture = None
        service._new_capture = Mock()
        config = {'model': 'base', 'backend': 'cpu', 'threads': 2}
        service._prepared(6, config)
        service._new_capture.assert_not_called()
        service._prepared(7, config)
        service._new_capture.return_value.start.assert_called_once()
        self.assertEqual(service.session_config, config)
        service._set_state.assert_called_with('listening', 'Listening…')

    def test_cancel_releases_prepared_detector_before_idle_handoff(self):
        service = service_stub()
        detector = service.prepared_vad = Mock()
        service._new_capture = Mock()
        service.stop()
        detector.close.assert_called_once()
        self.assertIsNone(service.prepared_vad)
        service._prepared(7, {'model': 'base'}, detector)
        service._new_capture.assert_not_called()
        self.assertEqual(detector.close.call_count, 2)  # Idempotent stale handoff.

    def test_successful_handoff_transfers_detector_to_capture(self):
        service = service_stub()
        detector = service.prepared_vad = Mock()
        service._new_capture = Mock()
        service._prepared(7, {'model': 'base'}, detector)
        self.assertIsNone(service.prepared_vad)
        service._new_capture.assert_called_once_with(session_id=7, vad=detector)
        detector.close.assert_not_called()

    @patch('anduinos_whisper_framework.daemon.GLib.idle_add', return_value=1)
    def test_cancel_during_detector_start_never_opens_microphone(self, idle):
        service = service_stub()
        service.capture = None
        service._new_capture = Mock()
        config = dict(model='base', language='en', backend='cpu', threads=4, generation=0)
        service._put_work(0, 'prepare', 7, 0, b'', config)
        with patch('anduinos_whisper_framework.daemon.SessionEngine') as engine, \
                patch('anduinos_whisper_framework.daemon.AutomaticSelector') as selector, \
                patch('anduinos_whisper_framework.daemon.VadEngine') as vad:
            vad.return_value.ready_metrics = {}
            engine.return_value.last_metrics = {}
            selector.return_value.status = 'manual'
            selector.return_value.measurements = []
            selector.return_value.select.return_value = {'backend': 'cpu', 'threads': 4}
            selector.return_value.calibration.return_value = (b'fixture', {})
            def cancel_start(event):
                service.stop()
                self.assertTrue(event.is_set())
                service._put_work(-1, 'quit', 0, 0, b'')
            vad.return_value.start.side_effect = cancel_start
            thread = threading.Thread(target=service._recognition_worker, daemon=True)
            thread.start(); thread.join(2)
            self.assertFalse(thread.is_alive())
            vad.return_value.close.assert_called_once()
            self.assertIsNone(service.prepared_vad)
            self.assertFalse(any(call.args[0] == service._prepared for call in idle.call_args_list))
            service._new_capture.assert_not_called()

    def test_preparation_notice_cannot_revive_cancelled_or_recording_session(self):
        service = service_stub()
        service.capture = None
        service._preparation_state(7, 'calibrating')
        service._set_state.assert_called_once_with(
            'calibrating', 'Measuring performance — microphone off')
        service._set_state.reset_mock()
        service._preparation_state(6, 'calibrating')
        service.active = False
        service._preparation_state(7, 'preparing')
        service.active = True
        service.capture = Mock()
        service._preparation_state(7, 'calibrating')
        service._set_state.assert_not_called()

    @patch('anduinos_whisper_framework.daemon.GLib.idle_add', return_value=1)
    def test_prepare_worker_warms_selected_engine_without_emitting_fixture_text(self, idle):
        service = service_stub()
        config = dict(model='base', language='zh', backend='auto', threads=0, generation=2)
        service._put_work(0, 'prepare', 7, 0, b'', config)
        with patch('anduinos_whisper_framework.daemon.SessionEngine') as engine, \
                patch('anduinos_whisper_framework.daemon.AutomaticSelector') as selector, \
                patch('anduinos_whisper_framework.daemon.VadEngine') as vad:
            vad.return_value.ready_metrics = {'initialization_ms': 12}
            def select(*args, **kwargs):
                engine.return_value.close.assert_not_called()
                kwargs['on_measure']()
                engine.return_value.close.assert_called_once()
                return {'backend': 'cpu', 'threads': 2}
            selector.return_value.select.side_effect = select
            selector.return_value.measurements = [{"kind": "benchmark", "phase": "cold",
                "inference_ms": 123, "backend": "cpu", "text": "PRIVATE CALIBRATION"}]
            selector.return_value.calibration.return_value = (b'public fixture', {})
            engine.return_value.last_metrics = {}
            engine.return_value.prepare.return_value = True
            # End only after the preparation completion has been posted.
            idle.side_effect = lambda *args: service._put_work(-1, 'quit', 0, 0, b'')
            thread = threading.Thread(target=service._recognition_worker, daemon=True)
            thread.start()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(engine.return_value.prepare.call_args.args[2], b'public fixture')
            self.assertEqual(engine.return_value.prepare.call_args.kwargs['threads'], 2)
            self.assertEqual(idle.call_args.args[0], service._prepared)
            self.assertEqual(idle.call_args.args[2]['backend'], 'cpu')
            self.assertIs(idle.call_args.args[3], vad.return_value)
            vad.return_value.start.assert_called_once()
            self.assertEqual([call.args[2] for call in idle.call_args_list[:-1]],
                             ['calibrating', 'preparing'])
            service._emit.assert_not_called()
            report = service.performance.export_json()
            self.assertIn('"inference_ms": 123', report)
            self.assertIn('"phase": "cold"', report)
            self.assertNotIn('PRIVATE', report)
            self.assertIn('"engine": "vad"', report)

    @patch('anduinos_whisper_framework.daemon.GLib.idle_add', return_value=1)
    def test_preparation_invalidates_only_revalidated_or_manual_failed_gpu(self, idle):
        cases = [
            ('measured', 'gpu', True, True),
            ('measured', 'cpu', False, True),
            ('cached', 'gpu', True, False),
            ('cached', 'gpu', False, False),
            ('manual', 'gpu', True, True),
            ('manual', 'gpu', False, False),
            ('manual', 'cpu', True, False),
            ('fallback', 'cpu', True, False),
        ]
        for status, backend, failed, invalidate in cases:
            with self.subTest(status=status, backend=backend, failed=failed):
                service = service_stub()
                config = dict(model='base', language='en', backend='auto',
                              threads=4, generation=2)
                service._put_work(0, 'prepare', 7, 0, b'', config)
                with patch('anduinos_whisper_framework.daemon.SessionEngine') as factory, \
                        patch('anduinos_whisper_framework.daemon.AutomaticSelector') as selector, \
                        patch('anduinos_whisper_framework.daemon.VadEngine') as vad:
                    vad.return_value.ready_metrics = {}
                    engine = factory.return_value
                    engine.gpu_failed = failed
                    engine.last_metrics = {}
                    selected = selector.return_value
                    selected.status = status
                    selected.measurements = []
                    selected.select.return_value = {'backend': backend, 'threads': 4}
                    selected.calibration.return_value = (b'public fixture', {})
                    def warm(*args, **kwargs):
                        self.assertEqual(engine.invalidate.call_count, int(invalidate))
                        return 'discard fixture text'
                    engine.prepare.side_effect = warm
                    idle.side_effect = lambda *args: service._put_work(-1, 'quit', 0, 0, b'')
                    thread = threading.Thread(target=service._recognition_worker, daemon=True)
                    thread.start()
                    thread.join(2)
                    self.assertFalse(thread.is_alive())
                    engine.prepare.assert_called_once()
                    self.assertEqual(engine.invalidate.call_count, int(invalidate))

    def test_overload_stops_capture_without_cancelling_accepted_work(self):
        service = service_stub()
        service.pending = 1
        service.audio_queue.put((0, 0, 'final', 7, 0, pcm()))
        capture = service.capture
        service._overloaded(7)
        capture.stop.assert_called_once_with(flush=False)
        self.assertEqual(service.session_id, 7)
        self.assertEqual(service.audio_queue.get()[2], 'final')
        service._recognition_finished(7, 'accepted text', None)
        service._emit.assert_called_once()
        self.assertEqual(service._set_state.call_args.args[0], 'error')

    def test_backend_error_stops_recording_and_preserves_pending_finals(self):
        service = service_stub()
        service.pending = 2
        capture = service.capture
        service._recognition_failed(7, 'model error')
        self.assertFalse(service.active)
        self.assertEqual(service.pending, 1)
        capture.stop.assert_called_once_with(flush=False)
        self.assertEqual(service._set_state.call_args.args[0], 'finishing')

    def test_finish_is_shell_only_and_old_stop_still_cancels(self):
        service = service_stub()
        service.shell_owner = ':1.7'
        invocation = Mock()
        service._method_called(None, ':1.8', '', '', 'Finish', None, invocation)
        invocation.return_dbus_error.assert_called_once()
        service.stop = Mock()
        service._method_called(None, ':1.7', '', '', 'Stop', None, Mock())
        service.stop.assert_called_once()

    @patch('anduinos_whisper_framework.daemon.GLib.idle_add', return_value=1)
    def test_finish_retains_session_and_delivers_final(self, _idle):
        service = service_stub()
        capture = service.capture
        capture.stop.side_effect = lambda **_: service._queue_audio(7, pcm()*30)
        service.finish()
        capture.stop.assert_called_once_with(flush=True)
        self.assertEqual(service.session_id, 7)
        self.assertFalse(service.active)
        self.assertEqual(service.pending, 1)
        service._recognition_finished(7, 'hello', None)
        service._emit.assert_called_once()
        self.assertEqual(service.pending, 0)

    def test_cancel_invalidates_session_and_running_work(self):
        service = service_stub()
        event = service.current_cancel = threading.Event()
        service.current_kind = 'final'
        capture = service.capture
        service.stop()
        self.assertTrue(event.is_set())
        capture.stop.assert_called_once_with(flush=False)
        service._recognition_finished(7, 'late text', None)
        service._emit.assert_not_called()

    @patch('anduinos_whisper_framework.daemon.GLib.idle_add', return_value=1)
    def test_final_cancels_a_running_preview(self, _idle):
        service = service_stub()
        event = service.current_cancel = threading.Event()
        service.current_kind = 'partial'
        service._queue_audio(7, pcm()*30)
        self.assertTrue(event.is_set())
        self.assertEqual(service.audio_queue.get()[2], 'final')

    @patch('anduinos_whisper_framework.daemon.GLib.idle_add', return_value=1)
    def test_actual_worker_can_move_from_cancelled_partial_to_final(self, _idle):
        service = service_stub()
        entered, final_done = threading.Event(), threading.Event()

        def transcribe(model, language, data, cancel):
            if data == b'partial':
                entered.set()
                if not cancel.wait(2):
                    raise AssertionError('preview was not cancelled')
                raise RecognitionCancelled()
            final_done.set()
            return 'hello'

        with patch('anduinos_whisper_framework.daemon.SessionEngine') as engine:
            engine.return_value.transcribe.side_effect = transcribe
            engine.return_value.last_metrics = {}
            thread = threading.Thread(target=service._recognition_worker, daemon=True)
            service._queue_partial(7, b'partial')
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                service._queue_audio(7, b'final')
                self.assertTrue(final_done.wait(1))
            finally:
                service._put_work(-1, 'quit', 0, 0, b'')
                thread.join(2)
            self.assertFalse(thread.is_alive())


class CancellationTests(unittest.TestCase):
    def test_real_child_cancellation_is_bounded(self):
        event = threading.Event()
        timer = threading.Timer(0.15, event.set)
        timer.start()
        try:
            with self.assertRaises(RecognitionCancelled):
                WhisperEngine._run_cancellable(
                    [sys.executable, '-c', 'import time; time.sleep(30)'], event)
        finally:
            timer.cancel()

    def test_cancelled_job_never_starts_a_process(self):
        event = threading.Event()
        event.set()
        with patch('benchmark_engine.subprocess.Popen') as popen:
            with self.assertRaises(RecognitionCancelled):
                WhisperEngine._run_cancellable(['unused'], event)
            popen.assert_not_called()

    def test_running_process_is_terminated_and_reaped(self):
        event = threading.Event()
        process = Mock()
        process.poll.return_value = None

        def communicate(**kwargs):
            if kwargs['timeout'] == 0.1:
                event.set()
                raise subprocess.TimeoutExpired('whisper', 0.1)
            return '', ''

        process.communicate.side_effect = communicate
        with patch('benchmark_engine.subprocess.Popen', return_value=process):
            with self.assertRaises(RecognitionCancelled):
                WhisperEngine._run_cancellable(['whisper'], event)
        process.terminate.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)


if __name__ == '__main__':
    unittest.main()
