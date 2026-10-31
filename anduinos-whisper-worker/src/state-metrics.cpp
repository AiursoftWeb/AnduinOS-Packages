/* Compile beside the pinned upstream source, never against a guessed binary
 * layout. The timing extension changes no model/decoder behavior and keeps
 * all ownership in whisper.cpp. Values are totals, not per-token averages.
 * Preserve upstream source provenance and its MIT notice in distributions.
 */
#include "whisper.cpp"

extern "C" bool anduinos_whisper_state_timings_v1(
        const struct whisper_state * state, double * milliseconds, size_t count) {
    if (!state || !milliseconds || count != 6) return false;
    milliseconds[0] = state->t_mel_us / 1000.0;
    milliseconds[1] = state->t_encode_us / 1000.0;
    milliseconds[2] = state->t_decode_us / 1000.0;
    milliseconds[3] = state->t_batchd_us / 1000.0;
    milliseconds[4] = state->t_sample_us / 1000.0;
    milliseconds[5] = state->t_prompt_us / 1000.0;
    return true;
}

// Experimental streaming VAD API: unlike upstream's whole-file entry point,
// retain recurrent state and the allocated graph across consecutive 32 ms
// frames. No speech-recognition model/decoder or GPU policy is changed.
struct anduinos_vad_stream {
    whisper_vad_context * context;
    ggml_cgraph * graph;
    ggml_tensor * frame;
    ggml_tensor * probability;
};

extern "C" void anduinos_vad_stream_close_v1(anduinos_vad_stream * stream) {
    if (!stream) return;
    ggml_backend_sched_reset(stream->context->sched.sched);
    whisper_vad_free(stream->context);
    delete stream;
}

extern "C" anduinos_vad_stream * anduinos_vad_stream_open_v1(const char * model) {
    if (!model) return nullptr;
    auto params = whisper_vad_default_context_params();
    params.n_threads = 1;
    params.use_gpu = false;
    auto context = whisper_vad_init_from_file_with_params(model, params);
    if (!context) return nullptr;
    // This interface deliberately has exactly one pinned frame/model format.
    if (context->n_window != 512 || context->model.version != "6.2.0") {
        whisper_vad_free(context);
        return nullptr;
    }
    auto stream = new anduinos_vad_stream{context, nullptr, nullptr, nullptr};
    ggml_backend_buffer_clear(context->buffer, 0);
    stream->graph = whisper_vad_build_graph(*context);
    if (!ggml_backend_sched_alloc_graph(context->sched.sched, stream->graph)) {
        anduinos_vad_stream_close_v1(stream);
        return nullptr;
    }
    stream->frame = ggml_graph_get_tensor(stream->graph, "frame");
    stream->probability = ggml_graph_get_tensor(stream->graph, "prob");
    if (!stream->frame || !stream->probability) {
        anduinos_vad_stream_close_v1(stream);
        return nullptr;
    }
    return stream;
}

extern "C" bool anduinos_vad_stream_process_v1(anduinos_vad_stream * stream,
        const float * samples, size_t count, float * probability) {
    if (!stream || !samples || count != 512 || !probability) return false;
    ggml_backend_tensor_set(stream->frame, samples, 0, count * sizeof(float));
    if (!ggml_graph_compute_helper(stream->context->sched.sched, stream->graph,
                                  1, false)) return false;
    ggml_backend_tensor_get(stream->probability, probability, 0, sizeof(float));
    return std::isfinite(*probability) && *probability >= 0.0f && *probability <= 1.0f;
}
