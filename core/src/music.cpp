#include "backtrack/music.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <numeric>

namespace backtrack {

namespace {

const char * kMajorNames[12] = {"C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"};
const char * kMinorNames[12] = {"C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"};

struct Mode {
    const char * name;
    int intervals[7];
    int major_offset;  // semitones from this mode's tonic to its relative major
};
const Mode kModes[] = {
    {"major", {0, 2, 4, 5, 7, 9, 11}, 0},
    {"minor", {0, 2, 3, 5, 7, 8, 10}, 3},
    {"dorian", {0, 2, 3, 5, 7, 9, 10}, 10},
    {"phrygian", {0, 1, 3, 5, 7, 8, 10}, 8},
    {"lydian", {0, 2, 4, 6, 7, 9, 11}, 7},
    {"mixolydian", {0, 2, 4, 5, 7, 9, 10}, 5},
    {"locrian", {0, 1, 3, 5, 6, 8, 10}, 1},
};

const Mode * find_mode(const std::string & m) {
    for (const Mode & mode : kModes)
        if (m == mode.name) return &mode;
    return nullptr;
}

std::string lower(std::string s) {
    for (char & c : s) c = char(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

std::string trim(const std::string & s) {
    size_t a = s.find_first_not_of(" \t"), b = s.find_last_not_of(" \t");
    return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
}

} // namespace

int parse_pitch_class(const std::string & t, size_t * consumed) {
    if (t.empty()) return -1;
    static const int base[7] = {9, 11, 0, 2, 4, 5, 7};  // A B C D E F G
    char c = char(std::toupper(static_cast<unsigned char>(t[0])));
    if (c < 'A' || c > 'G') return -1;
    int pc = base[c - 'A'];
    size_t i = 1;
    for (; i < t.size(); i++) {
        if (t[i] == '#') pc++;
        else if (t[i] == 'b') pc--;
        else if (t.compare(i, 3, "\xe2\x99\xaf") == 0) { pc++; i += 2; }  // U+266F sharp
        else if (t.compare(i, 3, "\xe2\x99\xad") == 0) { pc--; i += 2; }  // U+266D flat
        else break;
    }
    if (consumed) *consumed = i;
    return ((pc % 12) + 12) % 12;
}

std::string Key::name() const {
    const char * root = minor() ? kMinorNames[tonic] : kMajorNames[tonic];
    return std::string(root) + " " + mode;
}

bool Key::minor() const { return mode == "minor"; }

int Key::sharps() const {
    const Mode * m = find_mode(mode);
    int major_tonic = (tonic + (m ? m->major_offset : 0)) % 12;
    // Circle of fifths, preferring the spelling with fewer accidentals (F# over Gb).
    static const int sig[12] = {0, -5, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5};
    return sig[major_tonic];
}

std::optional<Key> parse_key(const std::string & text) {
    std::string t = trim(text);
    size_t used = 0;
    int pc = parse_pitch_class(t, &used);
    if (pc < 0) return std::nullopt;
    std::string rest = lower(trim(t.substr(used)));
    std::string mode;
    if (rest.empty() || rest == "maj" || rest == "major" || rest == "ionian") mode = "major";
    else if (rest == "m" || rest == "min" || rest == "minor" || rest == "aeolian") mode = "minor";
    else if (find_mode(rest)) mode = rest;
    else return std::nullopt;
    Key k;
    k.tonic = pc;
    k.mode = mode;
    for (int iv : find_mode(mode)->intervals) k.scale[size_t((pc + iv) % 12)] = true;
    return k;
}

std::optional<Chord> parse_chord(const std::string & symbol) {
    std::string s = trim(symbol);
    size_t used = 0;
    int root = parse_pitch_class(s, &used);
    if (root < 0) return std::nullopt;
    std::string q = s.substr(used);
    int bass = -1;
    size_t slash = q.find('/');
    if (slash != std::string::npos) {
        bass = parse_pitch_class(q.substr(slash + 1));
        if (bass < 0) return std::nullopt;
        q = q.substr(0, slash);
    }
    struct Quality { const char * name; std::vector<int> iv; };
    static const Quality kQualities[] = {
        {"", {0, 4, 7}}, {"maj", {0, 4, 7}}, {"M", {0, 4, 7}},
        {"m", {0, 3, 7}}, {"min", {0, 3, 7}}, {"-", {0, 3, 7}},
        {"7", {0, 4, 7, 10}}, {"maj7", {0, 4, 7, 11}}, {"M7", {0, 4, 7, 11}},
        {"m7", {0, 3, 7, 10}}, {"min7", {0, 3, 7, 10}}, {"mmaj7", {0, 3, 7, 11}},
        {"dim", {0, 3, 6}}, {"dim7", {0, 3, 6, 9}}, {"m7b5", {0, 3, 6, 10}},
        {"aug", {0, 4, 8}}, {"+", {0, 4, 8}},
        {"sus2", {0, 2, 7}}, {"sus4", {0, 5, 7}}, {"sus", {0, 5, 7}}, {"7sus4", {0, 5, 7, 10}},
        {"6", {0, 4, 7, 9}}, {"m6", {0, 3, 7, 9}},
        {"9", {0, 4, 7, 10, 2}}, {"maj9", {0, 4, 7, 11, 2}}, {"m9", {0, 3, 7, 10, 2}},
        {"add9", {0, 4, 7, 2}}, {"madd9", {0, 3, 7, 2}}, {"5", {0, 7}},
    };
    for (const Quality & qual : kQualities) {
        if (q != qual.name) continue;
        Chord c;
        c.root = root;
        for (int iv : qual.iv) c.tones[size_t((root + iv) % 12)] = true;
        if (bass >= 0) c.tones[size_t(bass)] = true;
        return c;
    }
    return std::nullopt;
}

std::optional<double> estimate_tempo(const std::vector<double> & onsets, double lo_bpm, double hi_bpm) {
    std::vector<double> pts;
    for (double t : onsets)
        if (t >= 0) pts.push_back(t);
    if (pts.size() < 6) return std::nullopt;
    std::sort(pts.begin(), pts.end());
    const double res = 0.01;
    const size_t length = size_t(pts.back() / res) + 8;
    std::vector<double> train(length, 0.0);
    static const std::pair<int, double> kSmooth[] = {{-2, 0.25}, {-1, 0.6}, {0, 1.0}, {1, 0.6}, {2, 0.25}};
    for (double t : pts) {
        long i = std::lround(t / res);
        for (auto [d, w] : kSmooth)
            if (i + d >= 0 && size_t(i + d) < length) train[size_t(i + d)] += w;
    }
    double best_bpm = 0, best_score = 0;
    for (double bpm = lo_bpm; bpm <= hi_bpm + 1e-9; bpm += 0.5) {
        double lag = 60.0 / bpm / res, score = 0;
        static const std::pair<int, double> kMults[] = {{1, 1.0}, {2, 0.5}, {4, 0.25}};
        for (auto [mult, w] : kMults) {
            long lg = std::lround(lag * mult);
            if (lg > 0 && size_t(lg) < length) {
                double dot = 0;
                for (size_t i = 0; i + size_t(lg) < length; i++) dot += train[i] * train[i + size_t(lg)];
                score += w * dot;
            }
        }
        double r = std::log2(bpm / 120.0) / 0.9;
        score *= std::exp(-0.5 * r * r);
        if (score > best_score) { best_bpm = bpm; best_score = score; }
    }
    if (best_bpm <= 0 || best_score <= 0) return std::nullopt;
    return std::round(best_bpm * 10) / 10;
}

std::optional<std::string> estimate_key(const std::vector<TimedNote> & notes) {
    static const double kMajor[12] = {6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88};
    static const double kMinor[12] = {6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17};
    double hist[12] = {0};
    for (const TimedNote & n : notes) hist[((n.pitch % 12) + 12) % 12] += std::max(0.0, n.end - n.start);
    if (std::count_if(std::begin(hist), std::end(hist), [](double v) { return v > 0; }) < 3) return std::nullopt;
    auto corr = [](const double * a, const double * b) {
        double ma = 0, mb = 0;
        for (int i = 0; i < 12; i++) { ma += a[i]; mb += b[i]; }
        ma /= 12; mb /= 12;
        double num = 0, da = 0, db = 0;
        for (int i = 0; i < 12; i++) {
            num += (a[i] - ma) * (b[i] - mb);
            da += (a[i] - ma) * (a[i] - ma);
            db += (b[i] - mb) * (b[i] - mb);
        }
        return da * db > 0 ? num / std::sqrt(da * db) : 0.0;
    };
    double best = -2;
    std::string name;
    for (int tonic = 0; tonic < 12; tonic++) {
        double rot[12];
        for (int i = 0; i < 12; i++) rot[i] = hist[(tonic + i) % 12];
        double maj = corr(rot, kMajor), min = corr(rot, kMinor);
        if (maj > best) { best = maj; name = std::string(kMajorNames[tonic]) + " major"; }
        if (min > best) { best = min; name = std::string(kMinorNames[tonic]) + " minor"; }
    }
    return name;
}

} // namespace backtrack
