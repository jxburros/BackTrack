#include "surveil.h"

#include "backtrack/midi.h"
#include "backtrack/music.h"

#include "ggml-backend.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <filesystem>

namespace fs = std::filesystem;
using backtrack::BadInput;
using backtrack::NotFound;
using nlohmann::json;

namespace surveil {

Result transcribe(const backtrack::Audio & audio, const Model & model, const PickOptions & opt,
                  const backtrack::Progress & progress) {
    const ModelSpec & spec = model.spec();
    progress.step("resample", 0.02);
    std::vector<float> mono = backtrack::resample(backtrack::to_mono(audio), audio.sample_rate, spec.front.sample_rate, progress);
    backtrack::Hcqt hcqt = backtrack::compute_hcqt(mono, spec.front, progress, 0.05, 0.45);
    Activations act = model.run(hcqt, progress, 0.45, 0.9);
    progress.step("notes", 0.92);

    Result r;
    r.notes = pick_notes(act, spec, opt);
    r.model = spec.id;
    r.kind = spec.kind;
    r.source = opt.source;
    r.seconds = audio.seconds();
    for (Note & n : r.notes) n.end = std::min(n.end, std::max(n.start + 1e-3, r.seconds));

    if (opt.context.tempo) r.tempo = *opt.context.tempo;
    else {
        std::vector<double> onsets;
        for (const Note & n : r.notes) onsets.push_back(n.start);
        r.tempo = backtrack::estimate_tempo(onsets);
    }
    if (opt.context.key) r.key = opt.context.key->name();
    else if (spec.kind != "drums") {
        std::vector<backtrack::TimedNote> tn;
        for (const Note & n : r.notes) tn.push_back({n.pitch, n.start, n.end});
        r.key = backtrack::estimate_key(tn);
    }
    progress.set("done", 1.0);
    return r;
}

// Rounded to a number of decimals (dividing keeps the shortest printed form).
static double round_to(double v, double per_unit) { return std::round(v * per_unit) / per_unit; }

json result_json(const Result & r, bool with_bends) {
    json notes = json::array();
    for (const Note & n : r.notes) {
        json j = {{"pitch", n.pitch},
                  {"start", round_to(n.start, 1e4)},
                  {"end", round_to(n.end, 1e4)},
                  {"velocity", n.velocity},
                  {"confidence", n.confidence}};
        if (with_bends && !n.bend.empty()) {
            json b = json::array();
            for (auto & [pos, cents] : n.bend) b.push_back({round_to(pos, 1e3), cents});
            j["bend"] = b;
        }
        if (n.program >= 0) j["program"] = n.program;
        notes.push_back(j);
    }
    json out = {{"notes", notes}, {"model", r.model}};
    if (r.tempo) out["tempo"] = *r.tempo;
    if (r.key) out["key"] = *r.key;
    return out;
}

std::vector<uint8_t> result_midi(const Result & r, const PickOptions & opt) {
    backtrack::MidiPart part;
    part.name = r.source.empty() ? "transcription" : r.source;
    part.channel = r.kind == "drums" ? 9 : 0;
    if (r.kind != "drums" && !r.notes.empty()) part.program = r.notes.front().program;
    for (const Note & n : r.notes) {
        backtrack::MidiNote m;
        m.pitch = n.pitch;
        m.start = n.start;
        m.end = n.end;
        m.velocity = n.velocity;
        if (opt.pitch_bends) m.bend = n.bend;
        part.notes.push_back(std::move(m));
    }
    backtrack::MidiOptions mo;
    mo.tempo_bpm = r.tempo.value_or(120.0);
    mo.key = r.key.value_or("");
    return backtrack::write_midi({part}, mo);
}

std::vector<ChordSpan> parse_chords(const json & chords) {
    if (!chords.is_array()) throw BadInput("'context.chords' must be an array");
    std::vector<ChordSpan> out;
    for (const json & c : chords) {
        if (!c.is_object() || !c.contains("symbol") || !c["symbol"].is_string() || !c.contains("start") ||
            !c["start"].is_number() || !c.contains("end") || !c["end"].is_number())
            throw BadInput("each chord needs a string 'symbol' and numeric 'start' and 'end'");
        ChordSpan span;
        span.symbol = c["symbol"].get<std::string>();
        span.start = c["start"].get<double>();
        span.end = c["end"].get<double>();
        if (!(span.end > span.start)) throw BadInput("chord '" + span.symbol + "' ends before it starts");
        auto parsed = backtrack::parse_chord(span.symbol);
        if (!parsed) continue;  // "N" (no chord) and unknown qualities: no prior for that span
        span.chord = *parsed;
        out.push_back(span);
    }
    return out;
}

SongContext parse_context(const json & c) {
    SongContext ctx;
    if (c.is_null()) return ctx;
    if (!c.is_object()) throw BadInput("'context' must be an object");
    if (c.contains("key") && !c["key"].is_null()) {
        if (!c["key"].is_string()) throw BadInput("'context.key' must be a string");
        ctx.key = backtrack::parse_key(c["key"].get<std::string>());
        if (!ctx.key) throw BadInput("'context.key' is not a key name (e.g. \"A minor\", \"Eb\")");
    }
    if (c.contains("tempo") && !c["tempo"].is_null()) {
        if (!c["tempo"].is_number() || !(c["tempo"].get<double>() > 0) || c["tempo"].get<double>() > 1000)
            throw BadInput("'context.tempo' must be a positive number of beats per minute");
        ctx.tempo = c["tempo"].get<double>();
    }
    if (c.contains("chords") && !c["chords"].is_null()) ctx.chords = parse_chords(c["chords"]);
    if (c.contains("program") && !c["program"].is_null()) {
        if (!c["program"].is_number_integer() || c["program"].get<int>() < 0 || c["program"].get<int>() > 127)
            throw BadInput("'context.program' must be an integer 0-127");
        ctx.program = c["program"].get<int>();
    }
    return ctx;
}

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

namespace {

ModelEntry describe(const backtrack::GgufFile & f, const std::string & id, const std::string & path) {
    backtrack::AudioModelInfo info = backtrack::read_audio_model_info(f);
    ModelEntry e;
    e.id = id;
    e.path = path;
    e.name = info.name.empty() ? id : info.name;
    e.kind = f.str("audio.kind", "pitched");
    e.license = info.license;
    e.version = info.version;
    e.stems = info.stems;
    e.placeholder = info.placeholder;
    return e;
}

} // namespace

Registry::Registry(std::string device, int threads, std::vector<std::string> dirs, std::vector<std::string> files) {
    backend_ = backtrack::init_backend(device, threads, &device_name_);

    for (const char * kind : {"pitched", "drums"}) {
        auto f = make_placeholder(kind, "wired", 0);
        entries_.push_back(describe(*f, std::string(kind) == "drums" ? "placeholder-drums" : "placeholder", ""));
    }
    auto add_file = [&](const fs::path & p, bool explicit_file) {
        std::string id = p.stem().string();
        for (const ModelEntry & e : entries_)
            if (e.id == id) return;
        try {
            auto f = backtrack::GgufFile::open(p.string(), true);
            if (f->str("audio.task") != "transcribe") {
                if (explicit_file) throw BadInput(p.string() + " is not a transcription model");
                return;
            }
            entries_.push_back(describe(*f, id, p.string()));
        } catch (const BadInput & e) {
            if (explicit_file) throw;
            std::fprintf(stderr, "warning: skipping %s: %s\n", p.string().c_str(), e.what());
        }
    };
    for (const std::string & file : files) add_file(fs::path(file), true);
    for (const std::string & dir : dirs) {
        std::error_code ec;
        if (dir.empty() || !fs::is_directory(dir, ec)) continue;
        std::vector<fs::path> found;
        for (const auto & de : fs::directory_iterator(dir, ec))
            if (de.is_regular_file() && de.path().extension() == ".gguf") found.push_back(de.path());
        std::sort(found.begin(), found.end());
        for (const fs::path & p : found) add_file(p, false);
    }
}

Registry::~Registry() {
    loaded_.clear();
    if (backend_) ggml_backend_free(backend_);
}

std::string Registry::choose(const std::string & requested, const std::string & source) const {
    auto covers = [](const ModelEntry & e, const std::string & s) {
        return std::find(e.stems.begin(), e.stems.end(), s) != e.stems.end();
    };
    if (!requested.empty()) {
        for (const ModelEntry & e : entries_)
            if (e.id == requested || (!e.path.empty() && e.path == requested)) return e.id;
        // Song Deck calls multi-model bridges with a stem as the model.
        const std::vector<std::string> & srcs = known_sources();
        if (std::find(srcs.begin(), srcs.end(), requested) != srcs.end()) return choose("", requested);
        throw NotFound("unknown model '" + requested + "'");
    }
    for (bool placeholder : {false, true})
        for (const ModelEntry & e : entries_)
            if (e.placeholder == placeholder && covers(e, source)) return e.id;
    throw NotFound("no installed model transcribes '" + source + "'");
}

const Model & Registry::get(const std::string & id) {
    std::lock_guard<std::mutex> lock(mu_);
    auto it = loaded_.find(id);
    if (it != loaded_.end()) return *it->second;
    const ModelEntry * entry = nullptr;
    for (const ModelEntry & e : entries_)
        if (e.id == id) entry = &e;
    if (!entry) throw NotFound("unknown model '" + id + "'");
    std::unique_ptr<backtrack::GgufFile> f =
        entry->path.empty() ? make_placeholder(entry->kind, "wired", 0) : backtrack::GgufFile::open(entry->path);
    auto model = Model::load(std::move(f), id, backend_);
    const Model & ref = *model;
    loaded_[id] = std::move(model);
    return ref;
}

} // namespace surveil
