// Standard MIDI File writer (format 1: a conductor track with tempo, time
// and key signature, then one track per part).
#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace backtrack {

struct MidiNote {
    int pitch = 60;
    double start = 0, end = 0;     // seconds
    int velocity = 80;
    // Pitch curve: (position 0..1 along the note, cents), as in Song Deck's
    // transcription contract. Written as pitch-wheel events.
    std::vector<std::pair<double, double>> bend;
};

struct MidiPart {
    std::string name;
    int channel = 0;               // 0-15; 9 is General MIDI drums
    int program = -1;              // 0-127, or -1 for no program change
    std::vector<MidiNote> notes;
};

struct MidiOptions {
    double tempo_bpm = 120;
    int ticks_per_quarter = 480;
    std::string key;               // optional, anything parse_key() accepts
    int bend_range_semitones = 12; // set with RPN 0 when a part has bends
};

// Bends are written only for notes that do not overlap another note in the
// same part, since one channel has one pitch wheel.
std::vector<uint8_t> write_midi(const std::vector<MidiPart> & parts, const MidiOptions & options = {});

} // namespace backtrack
