// Note picking: model likelihoods to notes. This is where song context
// counts (docs/plans/surveil.md, "How it works" step 4), and because it is
// code rather than model, behaviour changes without retraining.
#pragma once

#include "model.h"

#include "backtrack/music.h"

#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace surveil {

struct ChordSpan {
    std::string symbol;
    backtrack::Chord chord;
    double start = 0, end = 0;
};

// Optional song context (the transcription contract's "context" field).
struct SongContext {
    std::optional<backtrack::Key> key;
    std::optional<double> tempo;          // BPM; note starts are pulled toward its 16th-note grid
    std::vector<ChordSpan> chords;
    std::optional<int> program;           // General MIDI program reported on each note
    std::optional<std::pair<int, int>> pitch_range;  // lowest and highest MIDI note; else the source's range
    std::optional<bool> monophonic;       // one note at a time; else decided by the source
};

struct PickOptions {
    std::string source = "mix";           // mix | vocals | bass | drums | piano | guitar | melody | other
    bool pitch_bends = false;
    SongContext context;
    double onset_threshold = 0.5;
    double frame_threshold = 0.5;
    double min_note_seconds = 0.1;        // about 9 frames at 86 frames per second
};

struct Note {
    int pitch = 0;
    double start = 0, end = 0;
    int velocity = 80;
    double confidence = 0;                // mean model likelihood over the note
    std::vector<std::pair<double, double>> bend;  // (position 0..1, cents)
    int program = -1;
};

// The sources the contract names.
const std::vector<std::string> & known_sources();
bool is_monophonic(const std::string & source);
std::pair<int, int> pitch_range(const std::string & source);  // inclusive MIDI range
int default_program(const std::string & source);              // -1 when there is no sensible default

std::vector<Note> pick_notes(const Activations & act, const ModelSpec & spec, const PickOptions & options);

} // namespace surveil
