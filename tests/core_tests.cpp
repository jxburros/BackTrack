// Unit tests for the parts that are easy to get silently wrong: file
// formats, the front end's bin placement, music parsing, and the placeholder
// pipeline end to end. Run with ctest or ./build/backtrack-tests.
#include "surveil.h"

#include "backtrack/audio.h"
#include "backtrack/base64.h"
#include "backtrack/ggml_util.h"
#include "backtrack/hcqt.h"
#include "backtrack/midi.h"
#include "backtrack/music.h"

#include "ggml-backend.h"
#include "ggml.h"
#include "gguf.h"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <functional>
#include <random>
#include <string>
#include <vector>

namespace {

int g_failed = 0, g_passed = 0;

void check(bool ok, const std::string & what) {
    if (ok) g_passed++;
    else {
        g_failed++;
        std::fprintf(stderr, "FAIL: %s\n", what.c_str());
    }
}

template <class F> void expect_throw(F && f, const std::string & what) {
    try {
        f();
        check(false, what + " (no exception)");
    } catch (const std::exception &) {
        check(true, what);
    }
}

constexpr double kPi = 3.14159265358979323846;

std::vector<float> sine(double freq, double seconds, int rate, double amp = 0.5) {
    std::vector<float> x(size_t(seconds * rate));
    for (size_t i = 0; i < x.size(); i++) x[i] = float(amp * std::sin(2 * kPi * freq * double(i) / rate));
    return x;
}

// A harmonic tone (four partials) with a 10 ms attack.
void add_tone(std::vector<float> & x, int pitch, double start, double dur, int rate) {
    double f0 = 440.0 * std::pow(2.0, (pitch - 69) / 12.0);
    size_t i0 = size_t(start * rate), n = size_t(dur * rate);
    if (x.size() < i0 + n) x.resize(i0 + n);
    const double amps[] = {1.0, 0.5, 0.3, 0.2};
    for (size_t i = 0; i < n; i++) {
        double t = double(i) / rate, env = std::min(1.0, t / 0.01) * std::min(1.0, double(n - i) / (0.03 * rate)), s = 0;
        for (int k = 0; k < 4; k++) s += amps[k] * std::sin(2 * kPi * f0 * (k + 1) * t);
        x[i0 + i] += float(0.25 * env * s);
    }
}

void test_base64() {
    std::vector<uint8_t> data;
    for (int i = 0; i < 300; i++) data.push_back(uint8_t(i * 37));
    for (size_t n : {size_t(0), size_t(1), size_t(2), size_t(3), size_t(299)}) {
        std::vector<uint8_t> part(data.begin(), data.begin() + long(n));
        check(backtrack::base64_decode(backtrack::base64_encode(part)) == part, "base64 round trip " + std::to_string(n));
    }
    check(backtrack::base64_encode(std::vector<uint8_t>{'M', 'a'}) == "TWE=", "base64 padding");
    check(backtrack::base64_decode("data:audio/wav;base64,TWE=") == std::vector<uint8_t>{'M', 'a'}, "base64 data URI");
    expect_throw([] { backtrack::base64_decode("TW*E"); }, "base64 rejects bad characters");
}

void test_wav_and_resample() {
    backtrack::Audio a;
    a.sample_rate = 44100;
    a.channels = 2;
    std::vector<float> s = sine(440, 0.5, 44100);
    for (float v : s) { a.samples.push_back(v); a.samples.push_back(-v); }
    for (auto fmt : {backtrack::WavFormat::pcm16, backtrack::WavFormat::pcm24, backtrack::WavFormat::float32}) {
        std::vector<uint8_t> wav = backtrack::encode_wav(a, fmt);
        backtrack::Audio b = backtrack::decode_audio(wav.data(), wav.size());
        double err = 0;
        for (size_t i = 0; i < a.samples.size(); i++) err = std::max(err, double(std::fabs(a.samples[i] - b.samples[i])));
        check(b.sample_rate == 44100 && b.channels == 2 && b.frames() == a.frames() && err < 1e-4, "WAV round trip");
        std::vector<float> mono = backtrack::to_mono(b);
        check(std::fabs(mono[1000]) < 1e-4, "mono mix-down");
    }
    expect_throw([] { uint8_t junk[16] = {1, 2, 3}; backtrack::decode_audio(junk, sizeof junk); }, "unknown format rejected");

    // 1 kHz at 44.1 kHz -> 22.05 kHz keeps its frequency and level.
    std::vector<float> y = backtrack::resample(sine(1000, 1.0, 44100), 44100, 22050);
    check(y.size() == 22050, "resampled length");
    int crossings = 0;
    double peak = 0;
    for (size_t i = 2000; i + 1 < 20000; i++) {
        if ((y[i] < 0) != (y[i + 1] < 0)) crossings++;
        peak = std::max(peak, double(std::fabs(y[i])));
    }
    double freq = crossings / 2.0 / (18000.0 / 22050);
    check(std::fabs(freq - 1000) < 5, "resampled frequency " + std::to_string(freq));
    check(std::fabs(peak - 0.5) < 0.01, "resampled level " + std::to_string(peak));
    // Content above the new Nyquist is removed.
    std::vector<float> hi = backtrack::resample(sine(15000, 1.0, 44100), 44100, 22050);
    double rms = 0;
    for (size_t i = 2000; i < 20000; i++) rms += double(hi[i]) * hi[i];
    check(std::sqrt(rms / 18000) < 0.005, "anti-aliasing");
}

void test_hcqt() {
    backtrack::HcqtConfig cfg;
    check(std::fabs(cfg.bin_frequency(1) - 27.5) < 1e-9, "bin 1 is A0");
    check(std::fabs(cfg.bin_frequency(3 * 48 + 1) - 440) < 1e-6, "A4 bin");
    backtrack::Hcqt h = backtrack::compute_hcqt(sine(440, 2.0, 22050), cfg);
    check(h.n_frames == int(2.0 * 22050) / 256 + 1 && h.n_bins == 264 && h.n_harmonics == 8, "HCQT shape");
    const int t = h.n_frames / 2;
    auto argmax = [&](int ch) {
        int best = 0;
        for (int k = 0; k < h.n_bins; k++)
            if (h.at(ch, t, k) > h.at(ch, t, best)) best = k;
        return best;
    };
    const int a4 = 3 * (69 - 21) + 1;
    check(argmax(1) == a4, "harmonic 1 peaks at A4 (got bin " + std::to_string(argmax(1)) + ")");
    check(argmax(2) == a4 - 36, "harmonic 2 peaks an octave below (got " + std::to_string(argmax(2)) + ")");
    check(argmax(0) == a4 + 36, "harmonic 0.5 peaks an octave above (got " + std::to_string(argmax(0)) + ")");
    check(std::fabs(h.at(1, t, a4) - backtrack::normalise_magnitude(0.25f)) < 0.02, "full-level magnitude");
    check(h.at(1, t, a4 - 6) < h.at(1, t, a4) - 0.3f, "two semitones away is well down");
    // A low note, to exercise the decimated octaves.
    backtrack::Hcqt low = backtrack::compute_hcqt(sine(55, 3.0, 22050), cfg);
    int best = 0, tl = low.n_frames / 2;
    for (int k = 0; k < low.n_bins; k++)
        if (low.at(1, tl, k) > low.at(1, tl, best)) best = k;
    check(best == 3 * (33 - 21) + 1, "A1 lands in its bin (got " + std::to_string(best) + ")");
}

void test_music() {
    auto k = backtrack::parse_key("F# minor");
    check(k && k->tonic == 6 && k->minor() && k->sharps() == 3 && k->name() == "F# minor", "F# minor");
    auto bb = backtrack::parse_key("Bb");
    check(bb && bb->sharps() == -2 && bb->scale[3] && !bb->scale[4], "Bb major");
    check(backtrack::parse_key("Ebm") && backtrack::parse_key("Ebm")->name() == "Eb minor", "Ebm");
    check(backtrack::parse_key("D dorian") && backtrack::parse_key("D dorian")->sharps() == 0, "D dorian");
    check(!backtrack::parse_key("H major") && !backtrack::parse_key("C blues"), "bad keys rejected");
    auto c = backtrack::parse_chord("Am7/G");
    check(c && c->root == 9 && c->tones[9] && c->tones[0] && c->tones[4] && c->tones[7] && !c->tones[11], "Am7/G");
    check(!backtrack::parse_chord("N"), "N is no chord");
    std::vector<double> onsets;
    for (int i = 0; i < 16; i++) onsets.push_back(i * 0.5);
    auto tempo = backtrack::estimate_tempo(onsets);
    check(tempo && std::fabs(*tempo - 120) < 1, "tempo of a 120 BPM pulse");
    std::vector<backtrack::TimedNote> notes;
    for (int p : {57, 59, 60, 62, 64, 65, 67, 57, 60, 64}) notes.push_back({p, 0, 1});
    auto key = backtrack::estimate_key(notes);
    check(key && (*key == "A minor" || *key == "C major"), "key estimate " + key.value_or("none"));
}

void test_midi() {
    backtrack::MidiPart part;
    part.name = "bass";
    part.program = 33;
    part.notes = {{40, 0.0, 0.5, 100, {}}, {45, 0.5, 1.0, 90, {{0.0, 0.0}, {1.0, 100.0}}}};
    backtrack::MidiOptions mo;
    mo.tempo_bpm = 120;
    mo.key = "E minor";
    std::vector<uint8_t> m = backtrack::write_midi({part}, mo);
    check(m.size() > 14 && std::string(m.begin(), m.begin() + 4) == "MThd" && m[9] == 1 && m[11] == 2, "SMF header, 2 tracks");
    // Walk the second track's events.
    size_t pos = 14;
    auto be32 = [&](size_t p) { return uint32_t(m[p]) << 24 | uint32_t(m[p + 1]) << 16 | uint32_t(m[p + 2]) << 8 | m[p + 3]; };
    pos += 8 + be32(pos + 4);  // skip the conductor track
    check(std::string(m.begin() + long(pos), m.begin() + long(pos) + 4) == "MTrk", "note track");
    size_t end = pos + 8 + be32(pos + 4);
    pos += 8;
    long tick = 0;
    int ons = 0, offs = 0, program = -1, bends = 0;
    long first_off = -1;
    while (pos < end) {
        uint32_t delta = 0;
        while (m[pos] & 0x80) delta = (delta << 7) | (m[pos++] & 0x7f);
        delta = (delta << 7) | m[pos++];
        tick += long(delta);
        uint8_t st = m[pos];
        if (st == 0xff) { pos += 3 + m[pos + 2]; continue; }
        if ((st & 0xf0) == 0x90) { ons++; pos += 3; }
        else if ((st & 0xf0) == 0x80) { if (first_off < 0) first_off = tick; offs++; pos += 3; }
        else if ((st & 0xf0) == 0xc0) { program = m[pos + 1]; pos += 2; }
        else if ((st & 0xf0) == 0xe0) { bends++; pos += 3; }
        else pos += 3;
    }
    check(ons == 2 && offs == 2 && program == 33, "notes and program");
    check(first_off == 480, "0.5 s at 120 BPM is 480 ticks");
    check(bends == 3, "bend points plus reset");
}

void test_placeholder_pipeline() {
    std::string dev;
    ggml_backend_t backend = backtrack::init_backend("CPU", 2, &dev);
    auto model = surveil::Model::load(surveil::make_placeholder("pitched", "wired", 0), "placeholder", backend);
    check(model->spec().kind == "pitched" && model->spec().n_outputs == 88 && model->spec().placeholder, "placeholder spec");

    // C4 E4 G4 C5 at 44.1 kHz.
    backtrack::Audio audio;
    audio.sample_rate = 44100;
    audio.channels = 1;
    const int pitches[] = {60, 64, 67, 72};
    for (int i = 0; i < 4; i++) add_tone(audio.samples, pitches[i], 0.6 * i, 0.5, 44100);
    audio.samples.resize(audio.samples.size() + 44100 / 3);
    surveil::PickOptions opt;
    opt.source = "piano";
    surveil::Result r = surveil::transcribe(audio, *model, opt);
    bool ok = r.notes.size() == 4;
    for (size_t i = 0; ok && i < 4; i++)
        ok = r.notes[i].pitch == pitches[i] && std::fabs(r.notes[i].start - 0.6 * double(i)) < 0.05 &&
             std::fabs(r.notes[i].end - (0.6 * double(i) + 0.5)) < 0.08 && r.notes[i].confidence > 0.5;
    check(ok, "placeholder transcribes C4 E4 G4 C5 (" + std::to_string(r.notes.size()) + " notes)");
    nlohmann::json j = surveil::result_json(r, false);
    check(j["notes"].size() == r.notes.size() && j["notes"][0]["program"] == 0 && j["key"] == "C major", "response JSON");

    // Monophonic best path and the bass range.
    opt.source = "bass";
    backtrack::Audio bass;
    bass.sample_rate = 22050;
    bass.channels = 1;
    add_tone(bass.samples, 40, 0.0, 0.8, 22050);
    add_tone(bass.samples, 47, 0.8, 0.8, 22050);
    surveil::Result rb = surveil::transcribe(bass, *model, opt);
    check(rb.notes.size() == 2 && rb.notes[0].pitch == 40 && rb.notes[1].pitch == 47 && rb.notes[0].program == 33,
          "monophonic bass (" + std::to_string(rb.notes.size()) + " notes)");

    // The GGUF round trip gives the same model.
    std::string path = (std::filesystem::temp_directory_path() / "backtrack-test-placeholder.gguf").string();
    check(surveil::make_placeholder("pitched", "random", 7)->save(path), "save GGUF");
    auto saved = surveil::Model::load(backtrack::GgufFile::open(path), "file", backend);
    auto again = surveil::Model::load(surveil::make_placeholder("pitched", "random", 7), "mem", backend);
    backtrack::Hcqt h = backtrack::compute_hcqt(sine(220, 1.0, 22050), saved->spec().front);
    surveil::Activations a1 = saved->run(h, {}, 0, 1), a2 = again->run(h, {}, 0, 1);
    check(a1.note == a2.note && a1.contour == a2.contour, "GGUF round trip is exact");
    std::filesystem::remove(path);

    // Cancellation stops the run.
    backtrack::Progress cancel;
    cancel.cancelled = [] { return true; };
    expect_throw([&] { saved->run(h, cancel, 0, 1); }, "cancellation");

    auto drums = surveil::Model::load(surveil::make_placeholder("drums", "wired", 0), "placeholder-drums", backend);
    check(drums->spec().n_outputs == 7 && drums->spec().class_notes[0] == 36, "drum placeholder");
    model.reset();
    saved.reset();
    again.reset();
    drums.reset();
    ggml_backend_free(backend);
}

// A copy of a model with extra tensors (zeros), for the graph's shape checks.
std::unique_ptr<backtrack::GgufFile> with_tensors(const backtrack::GgufFile & src,
                                                   const std::vector<std::pair<std::string, std::vector<int64_t>>> & extra) {
    ggml_init_params p{size_t(32) << 20, nullptr, false};
    ggml_context * ctx = ggml_init(p);
    gguf_context * g = gguf_init_empty();
    gguf_set_kv(g, src.gguf());
    for (int64_t i = 0; i < gguf_get_n_tensors(src.gguf()); i++) {
        ggml_tensor * s = ggml_get_tensor(src.tensors(), gguf_get_tensor_name(src.gguf(), i));
        ggml_tensor * t = ggml_dup_tensor(ctx, s);
        ggml_set_name(t, ggml_get_name(s));
        std::memcpy(t->data, s->data, ggml_nbytes(s));
        gguf_add_tensor(g, t);
    }
    for (auto & [name, ne] : extra) {
        ggml_tensor * t = ggml_new_tensor(ctx, GGML_TYPE_F32, int(ne.size()), ne.data());
        ggml_set_name(t, name.c_str());
        std::memset(t->data, 0, ggml_nbytes(t));
        gguf_add_tensor(g, t);
    }
    return backtrack::GgufFile::adopt(g, ctx, "test");
}

void test_drum_layouts() {
    ggml_backend_t backend = backtrack::init_backend("CPU", 2, nullptr);
    auto shared = surveil::Model::load(surveil::make_placeholder("drums", "random", 1), "shared", backend);
    check(shared->spec().receptive_radius == 3, "T0 drum head: receptive radius 3");
    auto temporal_file = [] { return surveil::make_placeholder("drums", "random", 1, "temporal"); };
    auto temporal = surveil::Model::load(temporal_file(), "temporal", backend);
    check(temporal->spec().receptive_radius == 12 && temporal->spec().n_outputs == 7, "temporal drum head: radius 12");
    expect_throw([&] { surveil::make_placeholder("pitched", "random", 1, "temporal"); }, "temporal head is drums only");

    // Optional layers: a third temporal conv loads and widens the radius;
    // a gap in the trunk, a wrong channel count or a missing bias does not.
    auto deeper = surveil::Model::load(with_tensors(*temporal_file(), {{"tconv3.weight", {1, 9, 32, 32}}, {"tconv3.bias", {32}}}),
                                       "deeper", backend);
    check(deeper->spec().receptive_radius == 16, "tconv3 adds its radius");
    expect_throw([&] { surveil::Model::load(with_tensors(*temporal_file(), {{"conv4.weight", {3, 3, 8, 8}}, {"conv4.bias", {8}}}), "x", backend); },
                 "a trunk layer after a gap is rejected");
    expect_throw([&] { surveil::Model::load(with_tensors(*temporal_file(), {{"tconv3.weight", {1, 9, 16, 32}}, {"tconv3.bias", {32}}}), "x", backend); },
                 "tconv3 reading the wrong channel count is rejected");
    expect_throw([&] { surveil::Model::load(with_tensors(*temporal_file(), {{"tconv3.weight", {1, 9, 32, 32}}}), "x", backend); },
                 "tconv3 without a bias is rejected");
    expect_throw([&] { surveil::Model::load(with_tensors(*temporal_file(), {{"tconv3.weight", {3, 9, 32, 32}}, {"tconv3.bias", {32}}}), "x", backend); },
                 "a temporal conv wider than one bin is rejected");

    // Chunking: frames away from the clip edges do not depend on where the
    // 512-frame chunks fall, so the context covers the receptive field.
    backtrack::Hcqt x;
    x.n_frames = 700;
    x.n_bins = 264;
    x.n_harmonics = 8;
    x.data.resize(size_t(8) * 700 * 264);
    std::mt19937 rng(3);
    std::uniform_real_distribution<float> u(0.0f, 1.0f);
    for (float & v : x.data) v = u(rng);
    backtrack::Hcqt tail = x;
    const int shift = 100, T2 = 600;
    tail.n_frames = T2;
    tail.data.resize(size_t(8) * T2 * 264);
    for (int h = 0; h < 8; h++)
        std::copy_n(&x.data[(size_t(h) * 700 + shift) * 264], size_t(T2) * 264, &tail.data[size_t(h) * T2 * 264]);
    surveil::Activations full = deeper->run(x, {}, 0, 1), part = deeper->run(tail, {}, 0, 1);
    const int r = deeper->spec().receptive_radius;
    bool same = true;
    for (int t = r; t < T2 - r && same; t++)
        for (int c = 0; c < 7; c++) same = same && part.onset[size_t(t) * 7 + c] == full.onset[size_t(t + shift) * 7 + c];
    check(same, "likelihoods do not depend on the chunk boundaries");

    // The training code's outputs: activations JSON and the HCQT as .npy.
    nlohmann::json j = surveil::activations_json(full, deeper->spec());
    check(j["kind"] == "drums" && j["frames"] == 700 && j["onset"].size() == 700 && j["onset"][0].size() == 7 &&
              j["receptive_radius"] == 16 && j["class_notes"][0] == 36 && !j.contains("note"),
          "activations JSON (drums)");
    check(j["onset"][5][2].get<float>() == full.onset[5 * 7 + 2], "activations JSON keeps float values exactly");
    std::vector<uint8_t> npy = surveil::hcqt_npy(x);
    std::string head(npy.begin() + 10, npy.begin() + 10 + (npy[8] | npy[9] << 8));
    check(npy[0] == 0x93 && head.find("'shape': (8, 700, 264)") != std::string::npos && (10 + head.size()) % 64 == 0 &&
              npy.size() == 10 + head.size() + x.data.size() * 4,
          "HCQT .npy header and size");
    float v0;
    std::memcpy(&v0, &npy[10 + head.size()], 4);
    check(v0 == x.data[0], "HCQT .npy data");

    shared.reset();
    temporal.reset();
    deeper.reset();
    ggml_backend_free(backend);
}

void test_context() {
    nlohmann::json ctx = {{"key", "A minor"}, {"tempo", 96}, {"chords", {{{"symbol", "Am"}, {"start", 0}, {"end", 2}}}}, {"program", 5}};
    surveil::SongContext c = surveil::parse_context(ctx);
    check(c.key && c.key->name() == "A minor" && c.tempo && *c.tempo == 96 && c.chords.size() == 1 && c.program == 5, "context parsed");
    expect_throw([] { surveil::parse_context({{"key", "Q"}}); }, "bad key is a 400");
    expect_throw([] { surveil::parse_context({{"tempo", -1}}); }, "bad tempo is a 400");
    expect_throw([] { surveil::parse_context({{"program", 300}}); }, "bad program is a 400");
    surveil::SongContext r = surveil::parse_context({{"pitch_range", {40, 64}}, {"monophonic", false}});
    check(r.pitch_range && r.pitch_range->first == 40 && r.pitch_range->second == 64 && r.monophonic == false,
          "pitch range and monophonic parsed");
    expect_throw([] { surveil::parse_context({{"pitch_range", {64, 40}}}); }, "reversed pitch range is a 400");
    expect_throw([] { surveil::parse_context({{"monophonic", "yes"}}); }, "non-boolean monophonic is a 400");
}

} // namespace

int main() {
    test_base64();
    test_wav_and_resample();
    test_hcqt();
    test_music();
    test_midi();
    test_context();
    test_placeholder_pipeline();
    test_drum_layouts();
    std::printf("passed: %d, failed: %d\n", g_passed, g_failed);
    return g_failed ? 1 : 0;
}
