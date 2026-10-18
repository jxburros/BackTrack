// Music-theory helpers shared by the apps: key and chord names, and the
// tempo and key estimates Song Deck's reference bridges use
// (bridges/songdeck_bridge/dsp.py in Song Deck), ported so BackTrack
// reports them the same way.
#pragma once

#include <array>
#include <optional>
#include <string>
#include <vector>

namespace backtrack {

struct Key {
    int tonic = 0;                    // pitch class, 0 = C
    std::string mode = "major";       // major, minor, dorian, phrygian, lydian, mixolydian, locrian
    std::array<bool, 12> scale{};     // pitch classes in the key

    std::string name() const;         // "Eb major", "F# minor"
    // Key-signature meta event values: sharps (+) or flats (-), and minor flag.
    int sharps() const;
    bool minor() const;
};

// "C", "Am", "F# minor", "Bb major", "D dorian", "Ebm". Returns nullopt for
// anything else.
std::optional<Key> parse_key(const std::string & text);

struct Chord {
    int root = 0;
    std::array<bool, 12> tones{};
};

// Root, optional accidental, then a quality: "", "m", "min", "maj7", "7",
// "m7", "dim", "aug", "sus2", "sus4", "6", "m6", "9", "add9", "5", with an
// optional "/bass" (the bass note is added as a chord tone). nullopt for
// anything unparseable, including "N" (no chord).
std::optional<Chord> parse_chord(const std::string & symbol);

// Pitch class from a note letter plus accidentals, or -1.
int parse_pitch_class(const std::string & text, size_t * consumed = nullptr);

struct TimedNote {
    int pitch;
    double start, end;
};

// Tempo from onset times by autocorrelating a smoothed onset train,
// 60-200 BPM with a gentle prior toward 120 (nullopt below 6 onsets).
std::optional<double> estimate_tempo(const std::vector<double> & onsets, double lo_bpm = 60, double hi_bpm = 200);

// Krumhansl-Kessler key estimate from duration-weighted pitch classes
// (nullopt with fewer than three pitch classes). Names match Song Deck's:
// "Db major", "C# minor".
std::optional<std::string> estimate_key(const std::vector<TimedNote> & notes);

} // namespace backtrack
