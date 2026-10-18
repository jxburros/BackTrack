#include "model.h"

#include "ggml-alloc.h"
#include "ggml-backend.h"
#include "ggml.h"
#include "gguf.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <random>

using backtrack::BadInput;
using backtrack::GgufFile;

namespace surveil {

namespace {

constexpr const char * kArch = "surveil-cnn";
constexpr int kChunk = 512;   // frames per model call
constexpr int kContext = 8;   // frames of context each side (receptive radius is 3)

// Element (kw, kh, ci, co) of a conv kernel with ne = [KW, KH, Cin, Cout].
float & at4(ggml_tensor * t, int kw, int kh, int ci, int co) {
    auto * d = static_cast<float *>(t->data);
    return d[kw + t->ne[0] * (kh + t->ne[1] * (ci + t->ne[2] * co))];
}

std::vector<std::string> drum_classes() {
    return {"kick", "snare", "toms", "hihat_closed", "hihat_open", "crash", "ride"};
}
std::vector<int> drum_notes() { return {36, 38, 45, 42, 46, 49, 51}; }

} // namespace

struct Model::Impl {
    ggml_backend_t backend = nullptr;
    ggml_context * ctx = nullptr;          // weight tensors (no data; data lives in buf)
    ggml_backend_buffer_t buf = nullptr;
    std::map<std::string, ggml_tensor *> w;

    ggml_tensor * get(const std::string & name) const {
        auto it = w.find(name);
        return it == w.end() ? nullptr : it->second;
    }
    ~Impl() {
        if (buf) ggml_backend_buffer_free(buf);
        if (ctx) ggml_free(ctx);
    }
};

Model::~Model() = default;

std::unique_ptr<Model> Model::load(std::unique_ptr<GgufFile> file, const std::string & id, ggml_backend_t backend) {
    const GgufFile & f = *file;
    std::unique_ptr<Model> m(new Model());
    ModelSpec & s = m->spec_;
    backtrack::AudioModelInfo info = backtrack::read_audio_model_info(f);
    if (info.arch != kArch) throw BadInput(f.path() + ": architecture '" + info.arch + "' is not " + kArch);
    if (info.task != "transcribe") throw BadInput(f.path() + ": audio.task is not 'transcribe'");
    s.id = id;
    s.arch = info.arch;
    s.name = info.name.empty() ? id : info.name;
    s.version = info.version;
    s.license = info.license;
    s.stems = info.stems;
    s.placeholder = info.placeholder;
    s.sources = f.strings("audio.sources");
    s.kind = f.str("audio.kind", "pitched");
    if (s.kind != "pitched" && s.kind != "drums") throw BadInput(f.path() + ": unknown audio.kind '" + s.kind + "'");

    backtrack::HcqtConfig & fc = s.front;
    fc.sample_rate = info.sample_rate > 0 ? info.sample_rate : fc.sample_rate;
    fc.hop = int(f.number("audio.hop", fc.hop));
    fc.fmin = f.number("audio.fmin", fc.fmin);
    fc.bins_per_semitone = int(f.number("audio.bins_per_semitone", fc.bins_per_semitone));
    fc.n_bins = int(f.number("audio.n_bins", fc.n_bins));
    fc.top_fraction = f.number("audio.top_fraction", fc.top_fraction);
    fc.filter_scale = f.number("audio.filter_scale", fc.filter_scale);
    std::vector<double> harmonics = f.numbers("audio.harmonics");
    if (!harmonics.empty()) fc.harmonics.assign(harmonics.begin(), harmonics.end());
    if (fc.hop <= 0 || fc.n_bins <= 0 || fc.bins_per_semitone <= 0 || fc.n_bins % fc.bins_per_semitone != 0)
        throw BadInput(f.path() + ": invalid front-end settings");

    // Copy every tensor to the backend.
    Impl & im = *(m->impl_ = std::make_unique<Impl>());
    im.backend = backend;
    const int64_t n_tensors = gguf_get_n_tensors(f.gguf());
    ggml_init_params p{size_t(n_tensors + 1) * ggml_tensor_overhead(), nullptr, true};
    im.ctx = ggml_init(p);
    std::vector<std::pair<ggml_tensor *, ggml_tensor *>> copies;
    for (int64_t i = 0; i < n_tensors; i++) {
        const char * name = gguf_get_tensor_name(f.gguf(), i);
        ggml_tensor * src = ggml_get_tensor(f.tensors(), name);
        if (!src) throw BadInput(f.path() + ": tensor " + name + " has no data");
        if (src->type != GGML_TYPE_F32) throw BadInput(f.path() + ": tensor " + name + " must be F32");
        ggml_tensor * dst = ggml_dup_tensor(im.ctx, src);
        ggml_set_name(dst, name);
        im.w[name] = dst;
        copies.emplace_back(src, dst);
    }
    im.buf = ggml_backend_alloc_ctx_tensors(im.ctx, backend);
    if (!im.buf) throw BadInput(f.path() + ": cannot allocate the weights on the device");
    for (auto & [src, dst] : copies) ggml_backend_tensor_set(dst, src->data, 0, ggml_nbytes(src));

    // Check the shapes the graph relies on.
    auto need = [&](const std::string & name) {
        ggml_tensor * t = im.get(name);
        if (!t) throw BadInput(f.path() + ": missing tensor " + name);
        return t;
    };
    auto check = [&](bool ok, const std::string & what) {
        if (!ok) throw BadInput(f.path() + ": " + what + " has the wrong shape");
    };
    const int H = int(fc.harmonics.size());
    ggml_tensor * c1 = need("conv1.weight"), * c2 = need("conv2.weight");
    check(c1->ne[2] == H && c1->ne[0] % 2 == 1 && c1->ne[1] % 2 == 1, "conv1.weight");
    check(c2->ne[2] == c1->ne[3] && c2->ne[0] % 2 == 1 && c2->ne[1] % 2 == 1, "conv2.weight");
    check(need("conv1.bias")->ne[0] == c1->ne[3], "conv1.bias");
    check(need("conv2.bias")->ne[0] == c2->ne[3], "conv2.bias");
    const int64_t trunk = c2->ne[3];
    if (s.kind == "pitched") {
        const int bps = fc.bins_per_semitone;
        ggml_tensor * pool = need("pool.weight");
        check(need("contour.weight")->ne[2] == trunk && im.get("contour.weight")->ne[3] == 1, "contour.weight");
        check(pool->ne[0] == bps && pool->ne[1] == 1 && pool->ne[2] == trunk, "pool.weight");
        for (const char * head : {"note", "onset", "offset"}) {
            ggml_tensor * t = need(std::string(head) + ".weight");
            check(t->ne[0] == 1 && t->ne[1] % 2 == 1 && t->ne[2] == pool->ne[3] && t->ne[3] == 1, std::string(head) + ".weight");
            need(std::string(head) + ".bias");
        }
        need("contour.bias");
        need("pool.bias");
        s.n_outputs = fc.n_bins / bps;
        s.pitch_min = int(f.number("audio.pitch_min", 21));
    } else {
        s.classes = f.strings("audio.classes");
        std::vector<double> notes = f.numbers("audio.class_notes");
        if (s.classes.empty() || notes.size() != s.classes.size())
            throw BadInput(f.path() + ": drum models need audio.classes and audio.class_notes of the same length");
        for (double n : notes) s.class_notes.push_back(int(n));
        s.n_outputs = int(s.classes.size());
        ggml_tensor * band = need("band.weight");
        check(band->ne[0] == fc.n_bins * trunk && band->ne[1] == s.n_outputs, "band.weight");
        check(need("band.bias")->ne[0] == s.n_outputs, "band.bias");
        ggml_tensor * onset = need("onset.weight");
        check(onset->ne[0] == 1 && onset->ne[1] % 2 == 1 && onset->ne[2] == 1 && onset->ne[3] == 1, "onset.weight");
        check(need("onset.bias")->ne[0] == s.n_outputs, "onset.bias");
    }
    if (s.stems.empty()) s.stems = s.kind == "drums" ? std::vector<std::string>{"drums"} : std::vector<std::string>{"mix", "other"};
    return m;
}

Activations Model::run(const backtrack::Hcqt & in, const backtrack::Progress & progress, double p0, double p1) const {
    const Impl & im = *impl_;
    const ModelSpec & s = spec_;
    if (in.n_bins != s.front.n_bins || in.n_harmonics != int(s.front.harmonics.size()))
        throw BadInput("front-end output does not match the model");
    const int T = in.n_frames, B = in.n_bins, H = in.n_harmonics, W = kChunk + 2 * kContext;
    const bool pitched = s.kind == "pitched";

    ggml_init_params params{ggml_tensor_overhead() * 96 + ggml_graph_overhead(), nullptr, true};
    ggml_context * ctx = ggml_init(params);
    ggml_tensor * x = ggml_new_tensor_4d(ctx, GGML_TYPE_F32, B, W, H, 1);
    ggml_set_name(x, "input");
    ggml_set_input(x);

    auto conv = [&](const char * name, ggml_tensor * input, int stride_freq) {
        ggml_tensor * k = im.get(std::string(name) + ".weight");
        ggml_tensor * b = im.get(std::string(name) + ".bias");
        int pad_freq = stride_freq == 1 ? int(k->ne[0] / 2) : 0;
        ggml_tensor * y = ggml_conv_2d(ctx, k, input, stride_freq, 1, pad_freq, int(k->ne[1] / 2), 1, 1);
        return ggml_add(ctx, y, ggml_reshape_4d(ctx, b, 1, 1, b->ne[0], 1));
    };

    ggml_tensor * h = ggml_relu(ctx, conv("conv1", x, 1));
    h = ggml_relu(ctx, conv("conv2", h, 1));

    std::vector<std::pair<ggml_tensor *, std::vector<float> *>> outs;
    Activations a;
    a.frames = T;
    a.outputs = s.n_outputs;
    a.bins = B;
    a.frame_rate = s.frame_rate();
    a.onset.assign(size_t(T) * a.outputs, 0.0f);
    if (pitched) {
        a.note.assign(size_t(T) * a.outputs, 0.0f);
        a.offset.assign(size_t(T) * a.outputs, 0.0f);
        a.contour.assign(size_t(T) * B, 0.0f);
        ggml_tensor * contour = ggml_sigmoid(ctx, conv("contour", h, 1));
        ggml_tensor * pooled = ggml_relu(ctx, conv("pool", h, s.front.bins_per_semitone));
        outs = {{ggml_sigmoid(ctx, conv("note", pooled, 1)), &a.note},
                {ggml_sigmoid(ctx, conv("onset", pooled, 1)), &a.onset},
                {ggml_sigmoid(ctx, conv("offset", pooled, 1)), &a.offset},
                {contour, &a.contour}};
    } else {
        ggml_tensor * flat = ggml_cont(ctx, ggml_permute(ctx, h, 0, 2, 1, 3));  // [B, C, W]
        flat = ggml_reshape_2d(ctx, flat, flat->ne[0] * flat->ne[1], flat->ne[2]);
        ggml_tensor * band = ggml_relu(ctx, ggml_add(ctx, ggml_mul_mat(ctx, im.get("band.weight"), flat), im.get("band.bias")));
        band = ggml_reshape_4d(ctx, band, s.n_outputs, W, 1, 1);
        ggml_tensor * k = im.get("onset.weight");
        ggml_tensor * on = ggml_conv_2d(ctx, k, band, 1, 1, 0, int(k->ne[1] / 2), 1, 1);
        on = ggml_add(ctx, on, ggml_reshape_4d(ctx, im.get("onset.bias"), s.n_outputs, 1, 1, 1));
        outs = {{ggml_sigmoid(ctx, on), &a.onset}};
    }

    ggml_cgraph * gf = ggml_new_graph(ctx);
    for (auto & [t, dst] : outs) {
        ggml_set_output(t);
        ggml_build_forward_expand(gf, t);
    }
    ggml_gallocr_t galloc = ggml_gallocr_new(ggml_backend_get_default_buffer_type(im.backend));
    if (!ggml_gallocr_reserve(galloc, gf) || !ggml_gallocr_alloc_graph(galloc, gf)) {
        ggml_gallocr_free(galloc);
        ggml_free(ctx);
        throw std::runtime_error("cannot allocate the model's compute buffer");
    }

    std::vector<float> host(size_t(B) * W * H);
    std::vector<float> out_host;
    const int n_chunks = (T + kChunk - 1) / kChunk;
    try {
        for (int c = 0; c < n_chunks; c++) {
            progress.step("model", p0 + (p1 - p0) * double(c) / n_chunks);
            const int start = c * kChunk;
            for (int hh = 0; hh < H; hh++) {
                for (int w = 0; w < W; w++) {
                    int t = start - kContext + w;
                    float * dst = &host[(size_t(hh) * W + w) * B];
                    if (t < 0 || t >= T) std::fill(dst, dst + B, 0.0f);
                    else std::copy_n(&in.data[(size_t(hh) * T + t) * B], B, dst);
                }
            }
            ggml_backend_tensor_set(x, host.data(), 0, host.size() * sizeof(float));
            if (ggml_backend_graph_compute(im.backend, gf) != GGML_STATUS_SUCCESS)
                throw std::runtime_error("model computation failed");
            for (auto & [t, dst] : outs) {
                const int width = int(t->ne[0]);  // outputs, or bins for the contour
                out_host.resize(size_t(width) * W);
                ggml_backend_tensor_get(t, out_host.data(), 0, out_host.size() * sizeof(float));
                for (int w = kContext; w < kContext + kChunk; w++) {
                    int frame = start - kContext + w;
                    if (frame >= T) break;
                    std::copy_n(&out_host[size_t(w) * width], width, &(*dst)[size_t(frame) * width]);
                }
            }
        }
    } catch (...) {
        ggml_gallocr_free(galloc);
        ggml_free(ctx);
        throw;
    }
    ggml_gallocr_free(galloc);
    ggml_free(ctx);
    progress.set("model", p1);
    return a;
}

// ---------------------------------------------------------------------------
// Placeholder models
// ---------------------------------------------------------------------------

std::unique_ptr<GgufFile> make_placeholder(const std::string & kind, const std::string & init, uint64_t seed) {
    if (kind != "pitched" && kind != "drums") throw BadInput("placeholder kind must be 'pitched' or 'drums'");
    if (init != "wired" && init != "random") throw BadInput("placeholder init must be 'wired' or 'random'");
    const bool pitched = kind == "pitched";
    backtrack::HcqtConfig fc;
    // Drums need time resolution more than pitch resolution: a kick at 55 Hz
    // would otherwise sit under a 0.9 s kernel.
    if (!pitched) fc.filter_scale = 0.25;
    const int H = int(fc.harmonics.size()), B = fc.n_bins, C1 = 16, C2 = 8, C3 = 8;
    const int n_cls = int(drum_classes().size());

    ggml_init_params p{size_t(16) << 20, nullptr, false};
    ggml_context * ctx = ggml_init(p);
    std::vector<ggml_tensor *> tensors;
    auto make = [&](const char * name, int64_t a, int64_t b = 1, int64_t c = 1, int64_t d = 1) {
        const int64_t ne[4] = {a, b, c, d};
        int dims = 4;
        while (dims > 1 && ne[dims - 1] == 1) dims--;  // biases 1-D, the band matrix 2-D
        ggml_tensor * t = ggml_new_tensor(ctx, GGML_TYPE_F32, dims, ne);
        ggml_set_name(t, name);
        std::fill_n(static_cast<float *>(t->data), ggml_nelements(t), 0.0f);
        tensors.push_back(t);
        return t;
    };

    ggml_tensor * conv1 = make("conv1.weight", 3, 3, H, C1);
    make("conv1.bias", C1);
    ggml_tensor * conv2 = make("conv2.weight", 3, 3, C1, C2);
    make("conv2.bias", C2);
    ggml_tensor *contour = nullptr, *contour_b = nullptr, *pool = nullptr, *note = nullptr, *note_b = nullptr,
                *onset = nullptr, *onset_b = nullptr, *offset = nullptr, *offset_b = nullptr, *band = nullptr;
    if (pitched) {
        contour = make("contour.weight", 1, 1, C2, 1);
        contour_b = make("contour.bias", 1);
        pool = make("pool.weight", fc.bins_per_semitone, 1, C2, C3);
        make("pool.bias", C3);
        note = make("note.weight", 1, 1, C3, 1);
        note_b = make("note.bias", 1);
        onset = make("onset.weight", 1, 3, C3, 1);
        onset_b = make("onset.bias", 1);
        offset = make("offset.weight", 1, 3, C3, 1);
        offset_b = make("offset.bias", 1);
    } else {
        band = make("band.weight", int64_t(B) * C2, n_cls);
        make("band.bias", n_cls);
        onset = make("onset.weight", 1, 3, 1, 1);
        onset_b = make("onset.bias", n_cls);
    }

    auto set1 = [](ggml_tensor * t, int i, float v) { static_cast<float *>(t->data)[i] = v; };
    if (init == "random") {
        std::mt19937_64 rng(seed);
        std::normal_distribution<float> dist(0.0f, 0.1f);
        for (ggml_tensor * t : tensors) {
            if (std::string(ggml_get_name(t)).find(".weight") == std::string::npos) continue;
            auto * d = static_cast<float *>(t->data);
            for (int64_t i = 0; i < ggml_nelements(t); i++) d[i] = dist(rng);
        }
    } else if (pitched) {
        // Harmonic salience: s = h1 + 0.5 h2 - h0.5 (the sub-octave term
        // suppresses octave errors), then likelihoods sigmoid(12 (s - 0.7)).
        at4(conv1, 1, 1, 1, 0) = 1;     // harmonic 1
        at4(conv1, 1, 1, 2, 1) = 1;     // harmonic 2
        at4(conv1, 1, 1, 0, 2) = 1;     // harmonic 0.5
        at4(conv2, 1, 1, 0, 0) = 1;
        at4(conv2, 1, 1, 1, 0) = 0.5f;
        at4(conv2, 1, 1, 2, 0) = -1;
        at4(contour, 0, 0, 0, 0) = 6;   // gentler than the note head, so the curve peaks instead of saturating
        set1(contour_b, 0, -4.2f);
        for (int k = 0; k < fc.bins_per_semitone; k++) at4(pool, k, 0, 0, 0) = 1.0f / float(fc.bins_per_semitone);
        at4(note, 0, 0, 0, 0) = 12;
        set1(note_b, 0, -8.4f);
        // Onsets where salience rises over three frames, offsets where it falls.
        at4(onset, 0, 2, 0, 0) = 20;
        at4(onset, 0, 0, 0, 0) = -20;
        set1(onset_b, 0, -3);
        at4(offset, 0, 0, 0, 0) = 20;
        at4(offset, 0, 2, 0, 0) = -20;
        set1(offset_b, 0, -3);
    } else {
        // Drums: band energy per class from harmonic channels 1, 2 and 4
        // (to reach above the 4.3 kHz top of channel 1), onsets where it rises.
        at4(conv1, 1, 1, 1, 0) = 1;
        at4(conv1, 1, 1, 2, 1) = 1;
        at4(conv1, 1, 1, 4, 2) = 1;
        for (int c = 0; c < 3; c++) at4(conv2, 1, 1, c, c) = 1;
        struct Band { int cls, channel; double lo, hi; };
        const double mult[3] = {1, 2, 4};
        const Band bands[] = {
            {0, 0, 40, 100}, {1, 0, 150, 250}, {1, 1, 1500, 4000}, {2, 0, 90, 180},
            {3, 2, 7000, 9900}, {4, 2, 5000, 7000}, {5, 1, 4000, 8500}, {6, 1, 2500, 4000},
        };
        std::vector<int> counts(size_t(n_cls), 0);
        for (const Band & b : bands)
            for (int k = 0; k < B; k++) {
                double f = mult[b.channel] * fc.bin_frequency(k);
                if (f >= b.lo && f < b.hi) counts[size_t(b.cls)]++;
            }
        auto * bw = static_cast<float *>(band->data);
        for (const Band & b : bands)
            for (int k = 0; k < B; k++) {
                double f = mult[b.channel] * fc.bin_frequency(k);
                if (f >= b.lo && f < b.hi) bw[size_t(b.cls) * B * C2 + size_t(b.channel) * B + k] = 1.0f / float(counts[size_t(b.cls)]);
            }
        at4(onset, 0, 2, 0, 0) = 20;
        at4(onset, 0, 0, 0, 0) = -20;
        for (int c = 0; c < n_cls; c++) set1(onset_b, c, -2);
    }

    gguf_context * g = gguf_init_empty();
    gguf_set_val_str(g, "general.architecture", kArch);
    gguf_set_val_str(g, "general.name", pitched ? "Surveil placeholder (pitched)" : "Surveil placeholder (drums)");
    gguf_set_val_str(g, "general.version", "0.0.1");
    gguf_set_val_str(g, "general.license", "MIT");
    gguf_set_val_str(g, "audio.task", "transcribe");
    gguf_set_val_str(g, "audio.arch", kArch);
    gguf_set_val_str(g, "audio.kind", kind.c_str());
    gguf_set_val_bool(g, "audio.placeholder", true);
    gguf_set_val_str(g, "audio.placeholder_init", init.c_str());
    std::vector<std::string> stems = pitched ? std::vector<std::string>{"mix", "vocals", "bass", "piano", "guitar", "melody", "other"}
                                             : std::vector<std::string>{"drums"};
    std::vector<const char *> cstr;
    for (const std::string & st : stems) cstr.push_back(st.c_str());
    gguf_set_arr_str(g, "audio.stems", cstr.data(), cstr.size());
    const char * no_sources[] = {"none: hand-set or random weights, no training data"};
    gguf_set_arr_str(g, "audio.sources", no_sources, 1);
    gguf_set_val_u32(g, "audio.sample_rate", uint32_t(fc.sample_rate));
    gguf_set_val_u32(g, "audio.hop", uint32_t(fc.hop));
    gguf_set_val_f32(g, "audio.fmin", float(fc.fmin));
    gguf_set_val_u32(g, "audio.bins_per_semitone", uint32_t(fc.bins_per_semitone));
    gguf_set_val_u32(g, "audio.n_bins", uint32_t(fc.n_bins));
    gguf_set_val_f32(g, "audio.top_fraction", float(fc.top_fraction));
    gguf_set_val_f32(g, "audio.filter_scale", float(fc.filter_scale));
    gguf_set_arr_data(g, "audio.harmonics", GGUF_TYPE_FLOAT32, fc.harmonics.data(), fc.harmonics.size());
    if (pitched) {
        gguf_set_val_u32(g, "audio.pitch_min", 21);
    } else {
        std::vector<std::string> classes = drum_classes();
        cstr.clear();
        for (const std::string & c : classes) cstr.push_back(c.c_str());
        gguf_set_arr_str(g, "audio.classes", cstr.data(), cstr.size());
        std::vector<int> dn = drum_notes();
        std::vector<int32_t> notes(dn.begin(), dn.end());
        gguf_set_arr_data(g, "audio.class_notes", GGUF_TYPE_INT32, notes.data(), notes.size());
    }
    for (ggml_tensor * t : tensors) gguf_add_tensor(g, t);
    return GgufFile::adopt(g, ctx, std::string("placeholder-") + kind);
}

} // namespace surveil
