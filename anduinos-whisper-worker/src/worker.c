/* Private stdio worker for the packaged, pinned whisper.cpp implementation.
 * No sockets, audio files, transcript logging, or model downloads. Its matching
 * source extension supplies independent-state timing counters.
 *
 * Input: little-endian uint32 PCM byte count, followed by S16LE mono 16 kHz PCM.
 * Output: one bounded JSON line for startup and for each request.
 * SIGUSR1 requests cooperative inference cancellation; EOF releases the model.
 */
#define _POSIX_C_SOURCE 200809L
#include <whisper.h>
#include <dlfcn.h>
#include <limits.h>
#include <malloc.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

#define API_LIST(X) \
    X(whisper_version) X(whisper_context_default_params) \
    X(whisper_full_default_params) \
    X(whisper_init_from_file_with_params_no_state) \
    X(whisper_init_state) X(whisper_free_state) X(whisper_full_with_state) \
    X(whisper_full_n_segments_from_state) X(whisper_full_get_segment_text_from_state) \
    X(whisper_free) X(whisper_log_set) \
    X(ggml_backend_load_all) X(ggml_log_set)
#define DECLARE(name) static __typeof__(name) * api_##name;
API_LIST(DECLARE)

static volatile sig_atomic_t cancelled;
static int actual_gpu;
static bool (*state_timings_v1)(const struct whisper_state *, double *, size_t);

static double now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000.0 + t.tv_nsec / 1000000.0;
}

static void on_cancel(int signum) { (void)signum; cancelled = 1; }
static bool should_abort(void * data) { (void)data; return cancelled != 0; }

static void numeric_log(enum ggml_log_level level, const char * text, void * data) {
    (void)level; (void)data;
    /* A detected device is not an active backend. Only accept the actual
     * backend initialization message, never export the driver-supplied name. */
    if (strstr(text, "whisper_backend_init_gpu: using ") == text)
        actual_gpu = 1;
}

static int read_exact(void * buffer, size_t size) {
    unsigned char * bytes = buffer;
    while (size) {
        size_t n = fread(bytes, 1, size, stdin);
        if (!n) return 0;
        bytes += n; size -= n;
    }
    return 1;
}

static void json_string(const char * text) {
    for (const unsigned char * p = (const unsigned char *)text; *p; ++p) {
        if (*p == '"' || *p == '\\') { putchar('\\'); putchar(*p); }
        else if (*p < 32) printf("\\u%04x", *p);
        else putchar(*p);
    }
}

static int unavailable(void) {
    /* Tell the parent this is a packaging/ABI problem, not a GPU failure.
     * Do not export dlerror(): it may contain private filesystem paths. */
    puts("{\"status\":\"unavailable\"}");
    fflush(stdout);
    return 3;
}

static int run_vad(void * library, const char * model, double started) {
    void * (*open_stream)(const char *);
    bool (*process_frame)(void *, const float *, size_t, float *);
    void (*close_stream)(void *);
    *(void **)(&open_stream) = dlsym(library, "anduinos_vad_stream_open_v1");
    *(void **)(&process_frame) = dlsym(library, "anduinos_vad_stream_process_v1");
    *(void **)(&close_stream) = dlsym(library, "anduinos_vad_stream_close_v1");
    if (!open_stream || !process_frame || !close_stream) return unavailable();
    void * stream = open_stream(model);
    if (!stream) return unavailable();
    printf("{\"status\":\"ready\",\"mode\":\"vad\",\"metrics\":{\"initialization_ms\":%.3f}}\n",
           now_ms() - started);
    fflush(stdout);
    unsigned char header[4], pcm[1024];
    float samples[512];
    int result = 0;
    while (read_exact(header, sizeof(header))) {
        uint32_t size = (uint32_t)header[0] | (uint32_t)header[1] << 8 |
                        (uint32_t)header[2] << 16 | (uint32_t)header[3] << 24;
        if (size != sizeof(pcm)) { result = 2; break; }
        if (!read_exact(pcm, sizeof(pcm))) break;
        if (cancelled) {
            puts("{\"status\":\"cancelled\"}");
            fflush(stdout);
            break; /* Never reuse recurrent state after cancellation. */
        }
        for (size_t i = 0; i < 512; ++i) {
            int16_t value = (int16_t)((uint16_t)pcm[i*2] | (uint16_t)pcm[i*2+1] << 8);
            samples[i] = value / 32768.0f;
        }
        float probability;
        if (!process_frame(stream, samples, 512, &probability)) { result = 4; break; }
        printf("{\"status\":\"success\",\"probability\":%.9g}\n", (double)probability);
        fflush(stdout);
    }
    close_stream(stream);
    return result;
}

int main(int argc, char ** argv) {
    bool vad_mode = argc == 3 && !strcmp(argv[1], "--vad");
    if (!vad_mode && (argc < 6 || argc > 8)) return 2;
    bool no_fallback = false, no_flash_attn = false;
    for (int i = 6; i < argc; ++i) {
        if (!strcmp(argv[i], "--no-fallback")) no_fallback = true;
        else if (!strcmp(argv[i], "--no-flash-attn")) no_flash_attn = true;
        else return 2;
    }
    long threads = 1, beam = 5;
    if (!vad_mode) {
        char * end;
        threads = strtol(argv[3], &end, 10);
        if (*end || threads < 1 || threads > 256) return 2;
        beam = strtol(argv[5], &end, 10);
        if (*end || beam < 1 || beam > 5) return 2;
        if (strcmp(argv[4], "cpu") && strcmp(argv[4], "gpu")) return 2;
    }
    pid_t parent = getppid();
    if (parent == 1 || prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != parent) return 2;
    struct rlimit no_core = {0, 0};
    setrlimit(RLIMIT_CORE, &no_core); /* Do not dump model input/transcripts. */
    struct sigaction action = {0};
    action.sa_handler = on_cancel;
    action.sa_flags = SA_RESTART;
    sigemptyset(&action.sa_mask);
    sigaction(SIGUSR1, &action, NULL);
    /* Fixed sibling path works in both installed packages and extracted tests.
     * Never search cwd/LD_LIBRARY_PATH for the private whisper implementation. */
    char library_path[PATH_MAX];
    ssize_t path_size = readlink("/proc/self/exe", library_path, sizeof(library_path) - 1);
    if (path_size < 0 || path_size >= (ssize_t)sizeof(library_path) - 1) return unavailable();
    library_path[path_size] = '\0';
    char * slash = strrchr(library_path, '/');
    if (!slash) return unavailable();
    *slash = '\0';
    size_t directory_size = strlen(library_path);
    const char suffix[] = "/anduinos-whisper/libanduinos-whisper.so.1";
    if (directory_size + sizeof(suffix) > sizeof(library_path)) return unavailable();
    memcpy(library_path + directory_size, suffix, sizeof(suffix));
    void * library = dlopen(library_path, RTLD_NOW | RTLD_LOCAL);
    if (!library) return unavailable();
#define LOAD(name) do { *(void **)(&api_##name) = dlsym(library, #name); if (!api_##name) return unavailable(); } while (0);
    API_LIST(LOAD)
    if (strcmp(api_whisper_version(), "1.8.3")) return unavailable();
    api_whisper_log_set(numeric_log, NULL);
    *(void **)(&state_timings_v1) = dlsym(library, "anduinos_whisper_state_timings_v1");
    if (!state_timings_v1) return unavailable();
    api_ggml_log_set(numeric_log, NULL);
    double started = now_ms();
    api_ggml_backend_load_all();
    if (vad_mode) return run_vad(library, argv[2], started);
    struct whisper_context_params context_params = api_whisper_context_default_params();
    context_params.use_gpu = !strcmp(argv[4], "gpu");
    context_params.flash_attn = !no_flash_attn;
    struct whisper_context * context = api_whisper_init_from_file_with_params_no_state(argv[1], context_params);
    if (!context) return 4;
    printf("{\"status\":\"ready\",\"metrics\":{\"initialization_ms\":%.3f,\"backend\":\"%s\"}}\n",
           now_ms() - started, "unknown");
    fflush(stdout);
    struct whisper_full_params params = api_whisper_full_default_params(
        beam > 1 ? WHISPER_SAMPLING_BEAM_SEARCH : WHISPER_SAMPLING_GREEDY);
    params.n_threads = (int)threads;
    params.language = argv[2];
    params.no_context = true;
    params.no_timestamps = true;
    params.print_special = params.print_progress = params.print_realtime = params.print_timestamps = false;
    params.suppress_nst = true;
    params.greedy.best_of = 5;
    params.beam_search.beam_size = (int)beam;
    if (no_fallback) params.temperature_inc = 0.0f;
    params.abort_callback = should_abort;
    for (;;) {
        cancelled = 0;
        unsigned char header[4];
        if (!read_exact(header, sizeof(header))) break;
        uint32_t size = (uint32_t)header[0] | ((uint32_t)header[1] << 8) |
                        ((uint32_t)header[2] << 16) | ((uint32_t)header[3] << 24);
        if (size < 16000 || size > 60 * 32000 || size % 2) break;
        unsigned char * raw = malloc(size);
        float * pcm = malloc(size / 2 * sizeof(float));
        if (!raw || !pcm) { free(raw); free(pcm); break; }
        if (!read_exact(raw, size)) { free(raw); free(pcm); break; }
        for (uint32_t i = 0; i < size / 2; ++i) {
            int16_t value = (int16_t)((uint16_t)raw[2*i] | (uint16_t)raw[2*i+1] << 8);
            pcm[i] = value / 32768.0f;
        }
        free(raw);
        started = now_ms();
        /* Model weights stay resident; mutable request state never leaks into
         * the next phrase. The matching source extension supplies its timings. */
        actual_gpu = 0;
        struct whisper_state * state = api_whisper_init_state(context);
        if (!state) { free(pcm); break; }
        double state_initialization_ms = now_ms() - started;
        started = now_ms();
        int result = api_whisper_full_with_state(context, state, params, pcm, (int)(size / 2));
        double inference_ms = now_ms() - started;
        free(pcm);
        double state_ms[6];
        if (!state_timings_v1(state, state_ms, 6)) { api_whisper_free_state(state); break; }
        struct rusage usage;
        getrusage(RUSAGE_SELF, &usage);
        printf("{\"status\":\"%s\",\"text\":\"", cancelled ? "cancelled" : result ? "error" : "success");
        if (!cancelled && !result) {
            int segments = api_whisper_full_n_segments_from_state(state);
            for (int i = 0; i < segments; ++i)
                json_string(api_whisper_full_get_segment_text_from_state(state, i));
        }
        started = now_ms();
        api_whisper_free_state(state);
        /* A resident worker must return freed per-request scratch pages, not
         * retain growing allocator arenas between phrases. This releases only
         * unused glibc heap pages; live model weights remain resident. Include
         * its cost in state_release_ms and the parent's end-to-end wall time.
         */
        malloc_trim(0);
        double state_release_ms = now_ms() - started;
        printf("\",\"metrics\":{\"engine\":\"resident\",\"backend\":\"%s\",\"threads\":%ld,"
               "\"audio_ms\":%.3f,\"inference_ms\":%.3f,\"peak_rss_mib\":%.3f",
               actual_gpu ? "gpu" : "cpu", threads, size / 32.0, inference_ms, usage.ru_maxrss / 1024.0);
        printf(",\"encode_ms\":%.3f,\"decode_ms\":%.3f,\"batch_decode_ms\":%.3f,"
                   "\"sample_ms\":%.3f,\"mel_ms\":%.3f,\"prompt_decode_ms\":%.3f",
                   state_ms[1], state_ms[2], state_ms[3], state_ms[4], state_ms[0], state_ms[5]);
        printf(",\"state_initialization_ms\":%.3f,\"state_release_ms\":%.3f",
                   state_initialization_ms, state_release_ms);
        puts("}}");
        fflush(stdout);
    }
    api_whisper_free(context);
    dlclose(library);
    return 0;
}
