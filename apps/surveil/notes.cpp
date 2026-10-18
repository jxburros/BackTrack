#include "notes.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>

namespace surveil {

const std::vector<std::string> & known_sources() {
    static const std::vector<std::string> s = {"mix", "vocals", "bass", "drums", "piano", "guitar", "melody", "other"};
    return s;
}

bool is_monophonic(const std::string & source) {
    return source == "bass" || source == "vocals" || source == "melody";
}

std::pair<int, int> pitch_range(const std::string & source) {
    if (source == "bass") return {28, 67};     // E1-G4
    if (source == "vocals") return {36, 84};   // C2-C6
    if (source == "melody") return {40, 96};
    if (source == "guitar") return {40, 88};   // E2-E6
    return {21, 108};
}

int default_program(const std::string & source) {
    if (source == "bass") return 33;    // Electric Bass (finger)
    if (source == "piano") return 0;    // Acoustic Grand Piano
    if (source == "guitar") return 25;  // Acoustic Guitar (steel)
    if (source == "vocals") return 53;  // Voice Oohs
    return -1;
}

namespace {

bool onset_peak(const std::vector<float> & on, int outputs, int frames, int t, int i, double thr) {
    float v = on[size_t(t) * outputs + i];
    if (v < thr) return false;
    float prev = t > 0 ? on[size_t(t - 1) * outputs + i] : 0.0f;
    float next = t + 1 < frames ? on[size_t(t + 1) * outputs + i] : 0.0f;
    return v >= prev && v > next;
}

// Context priors as a per-frame, per-pitch multiplier.
std::vector<float> priors(const Activations & a, const ModelSpec & spec, const PickOptions & opt) {
    std::vector<float> w(size_t(a.frames) * a.outputs, 1.0f);
    auto [lo, hi] = pitch_range(opt.source);
    const SongContext & ctx = opt.context;
    for (int i = 0; i < a.outputs; i++) {
        int pitch = spec.pitch_min + i;
        int pc = pitch % 12;
        float base = (pitch < lo || pitch > hi) ? 0.0f : 1.0f;
        if (ctx.key && !ctx.key->scale[size_t(pc)]) base *= 0.7f;
        for (int t = 0; t < a.frames; t++) w[size_t(t) * a.outputs + i] = base;
    }
    for (const ChordSpan & c : ctx.chords) {
        int t0 = std::max(0, int(std::floor(c.start * a.frame_rate)));
        int t1 = std::min(a.frames, int(std::ceil(c.end * a.frame_rate)));
        for (int t = t0; t < t1; t++)
            for (int i = 0; i < a.outputs; i++)
                if (!c.chord.tones[size_t((spec.pitch_min + i) % 12)]) w[size_t(t) * a.outputs + i] *= 0.85f;
    }
    return w;
}

void add_bend(Note & n, const Activations & a, const ModelSpec & spec, int i, int t0, int t1) {
    const int bps = spec.front.bins_per_semitone;
    const int centre = i * bps + bps / 2;
    std::vector<std::pair<double, double>> pts;
    double max_abs = 0;
    for (int t = t0; t < t1; t++) {
        const float * row = &a.contour[size_t(t) * a.bins];
        // Highest bin within a semitone; on a (near) tie, the one nearer the centre.
        int best = -1;
        float peak = 0.3f;
        for (int k = std::max(1, centre - bps); k <= std::min(a.bins - 2, centre + bps); k++) {
            bool higher = row[k] > peak + 1e-3f;
            bool tie_nearer = best >= 0 && std::fabs(row[k] - peak) <= 1e-3f && std::abs(k - centre) < std::abs(best - centre);
            if (higher || tie_nearer) { peak = std::max(peak, row[k]); best = k; }
        }
        if (best < 0) continue;
        double y0 = row[best - 1], y1 = row[best], y2 = row[best + 1];
        double den = y0 - 2 * y1 + y2;
        double delta = std::fabs(den) > 1e-9 ? std::max(-0.5, std::min(0.5, 0.5 * (y0 - y2) / den)) : 0.0;
        double cents = (best + delta - centre) * 100.0 / bps;
        double pos = t1 - t0 > 1 ? double(t - t0) / (t1 - t0 - 1) : 0.0;
        pts.emplace_back(pos, std::round(cents * 10) / 10);
        max_abs = std::max(max_abs, std::fabs(cents));
    }
    if (pts.empty() || max_abs < 15) return;  // flat notes carry no curve
    const size_t max_points = 24;
    if (pts.size() > max_points) {
        std::vector<std::pair<double, double>> thin;
        for (size_t j = 0; j < max_points; j++) thin.push_back(pts[j * (pts.size() - 1) / (max_points - 1)]);
        pts.swap(thin);
    }
    n.bend = std::move(pts);
}

Note make_note(const Activations & a, const ModelSpec & spec, const PickOptions & opt, int i, int t0, int t1) {
    Note n;
    n.pitch = spec.pitch_min + i;
    n.start = t0 / a.frame_rate;
    n.end = t1 / a.frame_rate;
    double sum = 0;
    for (int t = t0; t < t1; t++) sum += a.note[size_t(t) * a.outputs + i];
    double mean = sum / std::max(1, t1 - t0);
    n.confidence = std::round(mean * 1000) / 1000;
    n.velocity = std::max(1, std::min(127, int(std::lround(127 * mean))));
    if (opt.pitch_bends && !a.contour.empty()) add_bend(n, a, spec, i, t0, t1);
    return n;
}

// Split [t0, t1) of output i at onset peaks, keeping pieces of at least min frames.
void emit_split(std::vector<Note> & out, const Activations & a, const std::vector<float> & on, const ModelSpec & spec,
                const PickOptions & opt, int i, int t0, int t1, int min) {
    int start = t0;
    for (int t = t0 + min; t <= t1 - min; t++) {
        if (t - start >= min && onset_peak(on, a.outputs, a.frames, t, i, opt.onset_threshold)) {
            out.push_back(make_note(a, spec, opt, i, start, t));
            start = t;
        }
    }
    if (t1 - start >= min) out.push_back(make_note(a, spec, opt, i, start, t1));
}

void polyphonic(std::vector<Note> & out, const Activations & a, const std::vector<float> & frame,
                const std::vector<float> & on, const ModelSpec & spec, const PickOptions & opt, int min) {
    const int T = a.frames, N = a.outputs;
    std::vector<char> active(static_cast<size_t>(T));
    for (int i = 0; i < N; i++) {
        for (int t = 0; t < T; t++) active[size_t(t)] = frame[size_t(t) * N + i] >= opt.frame_threshold;
        // Bridge gaps of up to two frames.
        for (int t = 1; t + 1 < T; t++) {
            if (active[size_t(t)] || !active[size_t(t - 1)]) continue;
            int g = t;
            while (g < T && !active[size_t(g)] && g - t < 3) g++;
            if (g < T && active[size_t(g)] && g - t <= 2 && !onset_peak(on, N, T, g, i, opt.onset_threshold))
                for (int k = t; k < g; k++) active[size_t(k)] = 1;
        }
        for (int t = 0; t < T;) {
            if (!active[size_t(t)]) { t++; continue; }
            int t0 = t;
            while (t < T && active[size_t(t)]) t++;
            // Pull the start back to an onset peak just before the frames turn on.
            for (int k = std::max(0, t0 - 3); k < t0; k++)
                if (onset_peak(on, N, T, k, i, opt.onset_threshold)) { t0 = k; break; }
            emit_split(out, a, on, spec, opt, i, t0, t, min);
        }
    }
}

// One note at a time: best path through {silence, pitch 0..N-1} with a
// fixed cost for changing state.
void monophonic(std::vector<Note> & out, const Activations & a, const std::vector<float> & frame,
                const std::vector<float> & on, const ModelSpec & spec, const PickOptions & opt, int min) {
    const int T = a.frames, N = a.outputs, S = N + 1;
    if (T == 0) return;
    const double switch_cost = 3.0, eps = 1e-4;
    std::vector<double> score(static_cast<size_t>(S)), next(static_cast<size_t>(S));
    std::vector<int> back(size_t(T) * S);
    auto emission = [&](int t, int s) {
        const float * row = &frame[size_t(t) * N];
        if (s == 0) {
            float mx = *std::max_element(row, row + N);
            return std::log(std::max(eps, 1.0 - double(mx)));
        }
        return std::log(std::max(eps, double(row[s - 1])));
    };
    for (int s = 0; s < S; s++) score[size_t(s)] = emission(0, s) - (s ? switch_cost : 0.0);
    for (int t = 1; t < T; t++) {
        int best = int(std::max_element(score.begin(), score.end()) - score.begin());
        for (int s = 0; s < S; s++) {
            double stay = score[size_t(s)], jump = score[size_t(best)] - switch_cost;
            bool from_stay = stay >= jump || best == s;
            next[size_t(s)] = (from_stay ? stay : jump) + emission(t, s);
            back[size_t(t) * S + s] = from_stay ? s : best;
        }
        score.swap(next);
    }
    std::vector<int> path(static_cast<size_t>(T));
    path[size_t(T - 1)] = int(std::max_element(score.begin(), score.end()) - score.begin());
    for (int t = T - 1; t > 0; t--) path[size_t(t - 1)] = back[size_t(t) * S + path[size_t(t)]];
    for (int t = 0; t < T;) {
        int s = path[size_t(t)], t0 = t;
        while (t < T && path[size_t(t)] == s) t++;
        if (s == 0) continue;
        int i = s - 1;
        double mean = 0;
        for (int k = t0; k < t; k++) mean += frame[size_t(k) * N + i];
        if (mean / (t - t0) < opt.frame_threshold * 0.8) continue;  // a weak path segment, not a note
        emit_split(out, a, on, spec, opt, i, t0, t, min);
    }
}

void drums(std::vector<Note> & out, const Activations & a, const ModelSpec & spec, const PickOptions & opt) {
    const int T = a.frames, N = a.outputs;
    const int min_gap = std::max(1, int(std::lround(0.035 * a.frame_rate)));
    for (int i = 0; i < N; i++) {
        int last = -min_gap - 1;
        for (int t = 0; t < T; t++) {
            if (!onset_peak(a.onset, N, T, t, i, opt.onset_threshold)) continue;
            float v = a.onset[size_t(t) * N + i];
            if (t - last < min_gap) {
                if (!out.empty() && v > out.back().confidence) out.pop_back();  // keep the stronger of the two
                else continue;
            }
            Note n;
            n.pitch = spec.class_notes[size_t(i)];
            n.start = t / a.frame_rate;
            n.end = n.start + 0.1;
            n.confidence = std::round(double(v) * 1000) / 1000;
            n.velocity = std::max(1, std::min(127, int(std::lround(40 + 87 * v))));
            out.push_back(n);
            last = t;
        }
    }
}

void snap_to_grid(std::vector<Note> & notes, double tempo, double min_len) {
    if (tempo <= 0) return;
    const double grid = 60.0 / tempo / 4;
    const double window = std::min(0.04, 0.3 * grid);
    for (Note & n : notes) {
        double g = std::round(n.start / grid) * grid;
        if (std::fabs(n.start - g) <= window && n.end - g >= min_len) n.start = g;
    }
}

} // namespace

std::vector<Note> pick_notes(const Activations & a, const ModelSpec & spec, const PickOptions & opt) {
    std::vector<Note> notes;
    const int min = std::max(1, int(std::lround(opt.min_note_seconds * a.frame_rate)));
    if (spec.kind == "drums") {
        drums(notes, a, spec, opt);
    } else {
        std::vector<float> w = priors(a, spec, opt);
        std::vector<float> frame(a.note.size()), on(a.onset.size());
        for (size_t k = 0; k < w.size(); k++) {
            frame[k] = a.note[k] * w[k];
            on[k] = a.onset[k] * w[k];
        }
        if (is_monophonic(opt.source)) monophonic(notes, a, frame, on, spec, opt, min);
        else polyphonic(notes, a, frame, on, spec, opt, min);
        int program = opt.context.program ? *opt.context.program : default_program(opt.source);
        for (Note & n : notes) n.program = program;
    }
    if (opt.context.tempo) snap_to_grid(notes, *opt.context.tempo, opt.min_note_seconds);
    std::sort(notes.begin(), notes.end(), [](const Note & x, const Note & y) {
        return x.start != y.start ? x.start < y.start : x.pitch < y.pitch;
    });
    return notes;
}

} // namespace surveil
