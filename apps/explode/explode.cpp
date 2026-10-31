#include "explode.h"

#include "backtrack/version.h"

#include <algorithm>
#include <cmath>
#include <cstdio>

namespace explode {

using backtrack::Audio;
using backtrack::BadInput;
using backtrack::Progress;
using backtrack::Spectrum;
using cf = std::complex<float>;

// ---------------------------------------------------------------------------
// The placeholder model
// ---------------------------------------------------------------------------

namespace {

constexpr int kMedianFrames = 17;  // harmonic: median across time (about 0.2 s at hop 512)
constexpr int kMedianBins = 17;    // percussive: median across frequency (about 370 Hz)

float median_of(std::vector<float> & v) {
    auto mid = v.begin() + std::ptrdiff_t(v.size() / 2);
    std::nth_element(v.begin(), mid, v.end());
    return *mid;
}

class Placeholder final : public Model {
public:
    Placeholder() {
        info_.id = "placeholder";
        info_.name = "Explode placeholder (masks, no training)";
        info_.version = backtrack::version();
        info_.stems = {"vocals", "drums", "bass", "other"};
        info_.on_request_stems = {"instrumental"};
        info_.placeholder = true;
    }
    const ModelInfo & info() const override { return info_; }

    void separate(const Spectrum & in_l, const Spectrum & in_r, int frames, int bins, std::vector<Spectrum> & out_l,
                  std::vector<Spectrum> & out_r, const Progress & progress) const override {
        const size_t cells = size_t(frames) * size_t(bins);
        std::vector<float> mag(cells), centre(cells);
        for (size_t i = 0; i < cells; i++) {
            const float l = std::abs(in_l[i]), r = std::abs(in_r[i]);
            mag[i] = 0.5f * (l + r);
            const float e = l * l + r * r;
            // 1 when both channels agree (centre), 0.5 for unrelated channels, 0 in anti-phase.
            centre[i] = e > 1e-20f ? std::norm(in_l[i] + in_r[i]) / (2.f * e) : 0.f;
        }
        progress.check();
        // Harmonic: median across time per bin; percussive: median across frequency per frame.
        std::vector<float> harm(cells), perc(cells), win;
        win.reserve(std::max(kMedianFrames, kMedianBins));
        for (int k = 0; k < bins; k++) {
            for (int t = 0; t < frames; t++) {
                win.clear();
                for (int d = -kMedianFrames / 2; d <= kMedianFrames / 2; d++) {
                    const int u = std::min(frames - 1, std::max(0, t + d));
                    win.push_back(mag[size_t(u) * size_t(bins) + size_t(k)]);
                }
                harm[size_t(t) * size_t(bins) + size_t(k)] = median_of(win);
            }
        }
        progress.check();
        for (int t = 0; t < frames; t++) {
            const float * row = mag.data() + size_t(t) * size_t(bins);
            for (int k = 0; k < bins; k++) {
                win.clear();
                for (int d = -kMedianBins / 2; d <= kMedianBins / 2; d++) win.push_back(row[std::min(bins - 1, std::max(0, k + d))]);
                perc[size_t(t) * size_t(bins) + size_t(k)] = median_of(win);
            }
        }
        progress.check();

        // Per-bin weights of frequency.
        std::vector<float> low(static_cast<size_t>(bins)), band(static_cast<size_t>(bins));
        for (int k = 0; k < bins; k++) {
            const double f = double(k) * kSampleRate / info_.n_fft;
            low[size_t(k)] = float(1.0 / (1.0 + std::pow(f / 150.0, 4)));
            const double hp = 1.0 / (1.0 + std::pow(120.0 / std::max(f, 1.0), 4)), lp = 1.0 / (1.0 + std::pow(f / 8000.0, 4));
            band[size_t(k)] = float(hp * lp);
        }

        out_l.assign(4, Spectrum(cells));
        out_r.assign(4, Spectrum(cells));
        for (int t = 0; t < frames; t++) {
            for (int k = 0; k < bins; k++) {
                const size_t i = size_t(t) * size_t(bins) + size_t(k);
                const float h2 = harm[i] * harm[i], p2 = perc[i] * perc[i];
                const float drums = h2 + p2 > 1e-20f ? p2 / (h2 + p2) : 0.f;
                const float harmonic = 1.f - drums;
                const float bass = harmonic * low[size_t(k)];
                const float c = std::min(1.f, std::max(0.f, 2.f * centre[i] - 1.f));
                const float vocals = harmonic * (1.f - low[size_t(k)]) * band[size_t(k)] * c * c;
                const float other = std::max(0.f, 1.f - drums - bass - vocals);
                const float masks[4] = {vocals, drums, bass, other};
                for (int s = 0; s < 4; s++) {
                    out_l[size_t(s)][i] = masks[s] * in_l[i];
                    out_r[size_t(s)][i] = masks[s] * in_r[i];
                }
            }
        }
    }

private:
    ModelInfo info_;
};

} // namespace

std::unique_ptr<Model> make_placeholder() { return std::make_unique<Placeholder>(); }

Registry::Registry() { models_.push_back(make_placeholder()); }

const Model & Registry::get(const std::string & id) const {
    if (id.empty()) return *models_.front();
    for (const auto & m : models_)
        if (m->info().id == id) return *m;
    std::string ids;
    for (const auto & m : models_) ids += (ids.empty() ? "" : ", ") + m->info().id;
    throw backtrack::NotFound("unknown model '" + id + "' (models: " + ids + ")");
}

// ---------------------------------------------------------------------------
// Stems asked for
// ---------------------------------------------------------------------------

const char * pass_name(int pass) {
    static const char * kNames[] = {"plain", "flip + swap", "flip", "swap"};
    return pass >= 0 && pass < 4 ? kNames[pass] : "?";
}

StemPlan plan_stems(const ModelInfo & model, const std::vector<std::string> & requested) {
    StemPlan plan;
    std::vector<std::string> want;
    for (const std::string & s : requested.empty() ? model.stems : requested) {
        const bool known = std::find(model.stems.begin(), model.stems.end(), s) != model.stems.end() ||
                           std::find(model.on_request_stems.begin(), model.on_request_stems.end(), s) != model.on_request_stems.end();
        if (!known) {
            std::string list;
            for (const auto & v : model.stems) list += (list.empty() ? "" : ", ") + v;
            for (const auto & v : model.on_request_stems) list += ", " + v;
            throw BadInput("model " + model.id + " cannot make the stem '" + s + "' (it makes " + list + ")");
        }
        if (std::find(want.begin(), want.end(), s) == want.end()) want.push_back(s);
    }
    const bool other = std::find(want.begin(), want.end(), "other") != want.end();
    const bool instrumental = std::find(want.begin(), want.end(), "instrumental") != want.end();
    if (other && instrumental) throw BadInput("ask for 'other' or 'instrumental', not both: either one holds the rest");
    plan.outputs = want;
    int rest = -1;
    for (size_t i = 0; i < want.size(); i++)
        if (want[i] == "other" || want[i] == "instrumental") rest = int(i);
    for (const std::string & s : model.stems) {
        auto it = std::find(want.begin(), want.end(), s);
        if (it != want.end() && s != "other") {
            plan.target.push_back(int(it - want.begin()));
            continue;
        }
        if (rest < 0) {  // nothing asked for can hold this stem: keep it as "other", so the stems still sum to the mix
            plan.outputs.push_back("other");
            rest = int(plan.outputs.size() - 1);
        }
        plan.target.push_back(rest);
    }
    return plan;
}

// ---------------------------------------------------------------------------
// The pipeline
// ---------------------------------------------------------------------------

Audio to_stereo_44k(const Audio & a, const Progress & progress) {
    const size_t n = a.frames();
    if (a.channels < 1 || !n) throw BadInput("the audio is empty");
    std::vector<float> l(n), r(n);
    if (a.channels == 1) {
        for (size_t i = 0; i < n; i++) l[i] = r[i] = a.samples[i];
    } else {
        const int c = a.channels, nl = (c + 1) / 2, nr = c / 2;
        for (size_t i = 0; i < n; i++) {
            float sl = 0, sr = 0;
            for (int ch = 0; ch < c; ch++) (ch % 2 ? sr : sl) += a.samples[i * size_t(c) + size_t(ch)];
            l[i] = sl / float(nl);
            r[i] = sr / float(nr);
        }
    }
    if (a.sample_rate != kSampleRate) {
        progress.step("resample", 0.02);
        l = backtrack::resample(l, a.sample_rate, kSampleRate, progress);
        r = backtrack::resample(r, a.sample_rate, kSampleRate, progress);
    }
    Audio out;
    out.sample_rate = kSampleRate;
    out.channels = 2;
    out.samples.resize(l.size() * 2);
    for (size_t i = 0; i < l.size(); i++) {
        out.samples[2 * i] = l[i];
        out.samples[2 * i + 1] = r[i];
    }
    return out;
}

namespace {

struct Chunk {
    size_t start, end;
};

std::vector<Chunk> make_chunks(size_t n, size_t chunk, size_t overlap) {
    std::vector<Chunk> out;
    size_t s = 0;
    while (true) {
        const size_t e = std::min(n, s + chunk);
        out.push_back({s, e});
        if (e == n) break;
        s = e - overlap;
    }
    return out;
}

// One pass over one chunk: the output stems' samples (interleaved stereo), which sum to the chunk.
void run_chunk(const Model & model, const backtrack::Stft & stft, const StemPlan & plan, const std::vector<float> & l,
               const std::vector<float> & r, std::vector<std::vector<float>> & out_l, std::vector<std::vector<float>> & out_r,
               const Progress & progress) {
    const size_t n = l.size();
    const int frames = stft.frames(n), bins = stft.bins();
    const size_t cells = size_t(frames) * size_t(bins);
    Spectrum xl, xr;
    stft.forward(l.data(), r.data(), n, xl, xr);
    std::vector<Spectrum> sl, sr;
    model.separate(xl, xr, frames, bins, sl, sr, progress);
    const size_t ns = model.info().stems.size();
    if (sl.size() != ns || sr.size() != ns) throw std::runtime_error("the model returned the wrong number of stems");
    for (size_t s = 0; s < ns; s++)
        if (sl[s].size() != cells || sr[s].size() != cells) throw std::runtime_error("the model returned a spectrum of the wrong size");

    // Share out the residual (mix - sum of the stems) by each stem's power in the bin, so the stems
    // sum to the mix exactly; then add each model stem into its output stem.
    const size_t no = plan.outputs.size();
    std::vector<Spectrum> ol(no, Spectrum(cells)), orr(no, Spectrum(cells));
    std::vector<float> pow_l(ns), pow_r(ns);
    for (size_t i = 0; i < cells; i++) {
        cf res_l = xl[i], res_r = xr[i];
        float tl = 0, tr = 0;
        for (size_t s = 0; s < ns; s++) {
            res_l -= sl[s][i];
            res_r -= sr[s][i];
            pow_l[s] = std::norm(sl[s][i]);
            pow_r[s] = std::norm(sr[s][i]);
            tl += pow_l[s];
            tr += pow_r[s];
        }
        for (size_t s = 0; s < ns; s++) {
            const float wl = tl > 1e-30f ? pow_l[s] / tl : 1.f / float(ns), wr = tr > 1e-30f ? pow_r[s] / tr : 1.f / float(ns);
            const size_t o = size_t(plan.target[s]);
            ol[o][i] += sl[s][i] + wl * res_l;
            orr[o][i] += sr[s][i] + wr * res_r;
        }
    }
    out_l.assign(no, std::vector<float>(n));
    out_r.assign(no, std::vector<float>(n));
    for (size_t o = 0; o < no; o++) stft.inverse(ol[o], orr[o], n, out_l[o].data(), out_r[o].data());
}

} // namespace

Result separate(const Audio & audio, const Model & model, const Options & opt, const Progress & progress) {
    if (opt.passes < 1 || opt.passes > 4) throw BadInput("passes must be 1-4");
    if (!(opt.chunk_seconds >= 2.0) || !(opt.overlap_seconds >= 0.1) || opt.overlap_seconds * 2 > opt.chunk_seconds)
        throw BadInput("chunks must be at least 2 s and at least twice the overlap (at least 0.1 s)");
    const ModelInfo & info = model.info();
    const StemPlan plan = plan_stems(info, opt.stems);

    Result res;
    res.mix = to_stereo_44k(audio, progress);
    res.model = info.id;
    res.placeholder = info.placeholder;
    res.passes = opt.passes;
    res.names = plan.outputs;
    const size_t n = res.mix.frames();
    res.seconds = double(n) / kSampleRate;

    const backtrack::Stft stft(info.n_fft, info.hop);
    const size_t overlap = size_t(opt.overlap_seconds * kSampleRate);
    const auto chunks = make_chunks(n, size_t(opt.chunk_seconds * kSampleRate), overlap);
    res.chunks = int(chunks.size());
    const size_t no = plan.outputs.size();
    std::vector<std::vector<float>> acc_l(no, std::vector<float>(n, 0.f)), acc_r(no, std::vector<float>(n, 0.f));

    const int total = res.chunks * opt.passes;
    char stage[64];
    for (size_t c = 0; c < chunks.size(); c++) {
        const Chunk ch = chunks[c];
        const size_t len = ch.end - ch.start;
        // Linear crossfades: chunk c fades in where chunk c-1 fades out, and the two sum to one.
        std::vector<float> fade(len, 1.f);
        if (c > 0)
            for (size_t i = 0; i < overlap && i < len; i++) fade[i] = float(i + 0.5) / float(overlap);
        if (c + 1 < chunks.size())
            for (size_t i = 0; i < overlap && i < len; i++) fade[len - 1 - i] *= float(i + 0.5) / float(overlap);

        for (int p = 0; p < opt.passes; p++) {
            const int done = int(c) * opt.passes + p;
            std::snprintf(stage, sizeof stage, "chunk %zu/%zu, pass %d/%d", c + 1, chunks.size(), p + 1, opt.passes);
            progress.step(stage, 0.05 + 0.9 * done / total);
            // flip: polarity inverted; swap: left and right exchanged. Both are undone on the way out.
            const bool flip = p == 1 || p == 2, swap = p == 1 || p == 3;
            const float sign = flip ? -1.f : 1.f;
            std::vector<float> l(len), r(len);
            for (size_t i = 0; i < len; i++) {
                const float a = res.mix.samples[2 * (ch.start + i)], b = res.mix.samples[2 * (ch.start + i) + 1];
                l[i] = sign * (swap ? b : a);
                r[i] = sign * (swap ? a : b);
            }
            std::vector<std::vector<float>> yl, yr;
            run_chunk(model, stft, plan, l, r, yl, yr, progress);
            const float w = sign / float(opt.passes);
            for (size_t o = 0; o < no; o++) {
                const std::vector<float> & a = swap ? yr[o] : yl[o];
                const std::vector<float> & b = swap ? yl[o] : yr[o];
                for (size_t i = 0; i < len; i++) {
                    acc_l[o][ch.start + i] += w * fade[i] * a[i];
                    acc_r[o][ch.start + i] += w * fade[i] * b[i];
                }
            }
        }
    }
    progress.step("stems", 0.96);
    res.stems.resize(no);
    for (size_t o = 0; o < no; o++) {
        Audio & s = res.stems[o];
        s.sample_rate = kSampleRate;
        s.channels = 2;
        s.samples.resize(2 * n);
        for (size_t i = 0; i < n; i++) {
            s.samples[2 * i] = acc_l[o][i];
            s.samples[2 * i + 1] = acc_r[o][i];
        }
    }
    return res;
}

} // namespace explode
