// BackTrack Surveil in WebAssembly: a C interface over the same pipeline the
// CLI and the server run (apps/surveil), for the JS wrapper in surveil.mjs.
//
// Single-threaded and file-free: models load from bytes, audio arrives as
// mono float samples, and results go back as JSON strings that stay valid
// until the next call. Progress and cancellation go through one JS hook
// (Module.surveilProgress), called between chunks.
#include "surveil.h"

#include "backtrack/common.h"
#include "backtrack/ggml_util.h"

#include "ggml-backend.h"

#include <emscripten/emscripten.h>

#include <algorithm>
#include <map>
#include <memory>
#include <string>

using nlohmann::json;

// Returns non-zero to cancel the job.
EM_JS(int, surveil_js_progress, (const char * stage, double fraction), {
    if (!Module.surveilProgress) return 0;
    return Module.surveilProgress(UTF8ToString(stage), fraction) ? 1 : 0;
});

namespace {

std::string g_error, g_out;
ggml_backend_t g_backend = nullptr;
std::map<int, std::unique_ptr<surveil::Model>> g_models;
int g_next = 1;

ggml_backend_t backend() {
    // One thread: this build has no pthreads (and needs no cross-origin isolation).
    if (!g_backend) g_backend = backtrack::init_backend("CPU", 1);
    return g_backend;
}

json spec_json(const surveil::ModelSpec & s) {
    json j = {{"id", s.id},
              {"arch", s.arch},
              {"kind", s.kind},
              {"name", s.name},
              {"version", s.version},
              {"license", s.license},
              {"stems", s.stems},
              {"sources", s.sources},
              {"placeholder", s.placeholder},
              {"sample_rate", s.front.sample_rate},
              {"frame_rate", s.frame_rate()},
              {"outputs", s.n_outputs}};
    if (s.kind == "drums") {
        j["classes"] = s.classes;
        j["class_notes"] = s.class_notes;
    } else {
        j["pitch_min"] = s.pitch_min;
    }
    return j;
}

int add(std::unique_ptr<backtrack::GgufFile> file, const char * id) {
    auto model = surveil::Model::load(std::move(file), id && *id ? id : "model", backend());
    int h = g_next++;
    g_models[h] = std::move(model);
    return h;
}

template <class F> auto guarded(F && f, decltype(f()) failed) -> decltype(f()) {
    try {
        g_error.clear();
        return f();
    } catch (const backtrack::Cancelled &) {
        g_error = "cancelled";
    } catch (const std::exception & e) {
        g_error = e.what();
        if (g_error.empty()) g_error = "error";
    }
    return failed;
}

} // namespace

extern "C" {

EMSCRIPTEN_KEEPALIVE const char * surveil_version() { return BACKTRACK_VERSION; }

// The message of the last failed call ("cancelled" after a cancel), or "".
EMSCRIPTEN_KEEPALIVE const char * surveil_last_error() { return g_error.c_str(); }

// Loads a Surveil GGUF from memory; the bytes are copied. Returns a handle,
// or 0 with the reason in surveil_last_error().
EMSCRIPTEN_KEEPALIVE int surveil_load(const uint8_t * data, size_t size, const char * id) {
    return guarded([&] { return add(backtrack::GgufFile::open_memory(data, size, id && *id ? id : "model.gguf"), id); }, 0);
}

// A built-in placeholder model ("pitched" or "drums", hand-set weights): the
// real graph with no trained weights, for tests.
EMSCRIPTEN_KEEPALIVE int surveil_load_placeholder(const char * kind) {
    return guarded(
        [&] {
            std::string k = kind ? kind : "pitched";
            return add(surveil::make_placeholder(k, "wired", 0), k == "drums" ? "placeholder-drums" : "placeholder");
        },
        0);
}

EMSCRIPTEN_KEEPALIVE void surveil_unload(int handle) { g_models.erase(handle); }

// The model's spec as JSON, or null for an unknown handle.
EMSCRIPTEN_KEEPALIVE const char * surveil_model_info(int handle) {
    auto it = g_models.find(handle);
    if (it == g_models.end()) {
        g_error = "unknown model handle";
        return nullptr;
    }
    g_out = spec_json(it->second->spec()).dump();
    return g_out.c_str();
}

// Transcribes mono samples at `sample_rate`. `request` is the JSON of Song
// Deck's transcription request, without the audio: {"source"?, "pitch_bends"?,
// "context"?, "onset_threshold"?, "frame_threshold"?}. Returns the response
// JSON ({"notes", "model", "tempo"?, "key"?} plus "kind" and "seconds"), or
// null with the reason in surveil_last_error().
EMSCRIPTEN_KEEPALIVE const char * surveil_transcribe(int handle, const float * samples, size_t count, int sample_rate,
                                                     const char * request) {
    return guarded(
        [&]() -> const char * {
            auto it = g_models.find(handle);
            if (it == g_models.end()) throw backtrack::BadInput("unknown model handle");
            if (sample_rate <= 0 || count == 0) throw backtrack::BadInput("no audio");
            json body = request && *request ? json::parse(request, nullptr, false) : json::object();
            if (!body.is_object()) throw backtrack::BadInput("the request must be a JSON object");

            surveil::PickOptions opt;
            if (body.contains("source") && body["source"].is_string()) opt.source = body["source"].get<std::string>();
            const auto & srcs = surveil::known_sources();
            if (std::find(srcs.begin(), srcs.end(), opt.source) == srcs.end())
                throw backtrack::BadInput("unknown source '" + opt.source + "'");
            if (body.contains("pitch_bends") && body["pitch_bends"].is_boolean()) opt.pitch_bends = body["pitch_bends"].get<bool>();
            if (body.contains("context")) opt.context = surveil::parse_context(body["context"]);
            if (body.contains("onset_threshold") && body["onset_threshold"].is_number())
                opt.onset_threshold = body["onset_threshold"].get<double>();
            if (body.contains("frame_threshold") && body["frame_threshold"].is_number())
                opt.frame_threshold = body["frame_threshold"].get<double>();

            backtrack::Audio audio;
            audio.sample_rate = sample_rate;
            audio.channels = 1;
            audio.samples.assign(samples, samples + count);

            backtrack::Progress progress;
            bool cancelled = false;
            progress.report = [&](const char * stage, double f) { cancelled = surveil_js_progress(stage, f) != 0 || cancelled; };
            progress.cancelled = [&] { return cancelled; };

            surveil::Result r = surveil::transcribe(audio, *it->second, opt, progress);
            json out = surveil::result_json(r, opt.pitch_bends);
            out["kind"] = r.kind;
            out["seconds"] = r.seconds;
            g_out = out.dump();
            return g_out.c_str();
        },
        nullptr);
}

} // extern "C"
