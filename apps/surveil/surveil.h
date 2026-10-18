// BackTrack Surveil: audio to MIDI. The pipeline (decode, resample, front
// end, model, note picking), the model registry, and the result formats
// shared by the CLI, the HTTP server and the page.
#pragma once

#include "model.h"
#include "notes.h"

#include "backtrack/audio.h"

#include <nlohmann/json.hpp>

#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

namespace surveil {

constexpr int kDefaultPort = 8826;
constexpr const char * kAppName = "BackTrack Surveil";

struct Result {
    std::vector<Note> notes;
    std::optional<double> tempo;
    std::optional<std::string> key;
    std::string model;
    std::string kind;
    std::string source;
    double seconds = 0;          // audio duration
};

Result transcribe(const backtrack::Audio & audio, const Model & model, const PickOptions & options,
                  const backtrack::Progress & progress = {});

// Song Deck's TranscriptionBridgeResponse, plus "model".
nlohmann::json result_json(const Result & result, bool with_bends);
std::vector<uint8_t> result_midi(const Result & result, const PickOptions & options);

// The context object of the transcription contract:
// {"key"?: str, "tempo"?: number, "chords"?: [{"symbol", "start", "end"}], "program"?: int}.
// Throws BadInput naming the bad field.
SongContext parse_context(const nlohmann::json & ctx);
std::vector<ChordSpan> parse_chords(const nlohmann::json & chords);

struct ModelEntry {
    std::string id;
    std::string path;            // empty for a built-in placeholder
    std::string name, kind, license, version;
    std::vector<std::string> stems;
    bool placeholder = false;
};

// Models known to this process: the built-in placeholders ("placeholder",
// "placeholder-drums") and every transcription GGUF in the model
// directories. Models load on first use and stay loaded.
class Registry {
public:
    Registry(std::string device, int threads, std::vector<std::string> model_dirs, std::vector<std::string> model_files);
    ~Registry();

    const std::vector<ModelEntry> & entries() const { return entries_; }
    const std::string & device_name() const { return device_name_; }

    // The model for a request: an id (or a .gguf path), or a source name
    // used as the model (multi-model bridges are called with
    // model "drums" and the like); otherwise the best model covering
    // `source`, trained before placeholder. Throws NotFound.
    std::string choose(const std::string & requested, const std::string & source) const;
    const Model & get(const std::string & id);

private:
    std::vector<ModelEntry> entries_;
    std::map<std::string, std::unique_ptr<Model>> loaded_;
    std::mutex mu_;
    ggml_backend_t backend_ = nullptr;
    std::string device_name_;
};

} // namespace surveil
