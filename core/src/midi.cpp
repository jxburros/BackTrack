#include "backtrack/midi.h"

#include "backtrack/music.h"

#include <algorithm>
#include <cmath>
#include <tuple>

namespace backtrack {

namespace {

struct Event {
    long tick;
    int order;                 // ties: note-offs, then controllers/bends, then note-ons
    std::vector<uint8_t> bytes;
};

void put_varlen(std::vector<uint8_t> & out, uint32_t v) {
    uint8_t buf[5];
    int n = 0;
    buf[n++] = v & 0x7f;
    while (v >>= 7) buf[n++] = uint8_t(0x80 | (v & 0x7f));
    while (n) out.push_back(buf[--n]);
}

void put_be(std::vector<uint8_t> & out, uint32_t v, int bytes) {
    for (int i = bytes - 1; i >= 0; i--) out.push_back(uint8_t(v >> (8 * i)));
}

std::vector<uint8_t> track_chunk(std::vector<Event> events) {
    std::stable_sort(events.begin(), events.end(),
                     [](const Event & a, const Event & b) { return std::tie(a.tick, a.order) < std::tie(b.tick, b.order); });
    std::vector<uint8_t> body;
    long last = 0;
    for (const Event & e : events) {
        put_varlen(body, uint32_t(std::max(0L, e.tick - last)));
        last = std::max(last, e.tick);
        body.insert(body.end(), e.bytes.begin(), e.bytes.end());
    }
    put_varlen(body, 0);
    body.insert(body.end(), {0xff, 0x2f, 0x00});  // end of track
    std::vector<uint8_t> chunk = {'M', 'T', 'r', 'k'};
    put_be(chunk, uint32_t(body.size()), 4);
    chunk.insert(chunk.end(), body.begin(), body.end());
    return chunk;
}

std::vector<uint8_t> meta(uint8_t type, const std::vector<uint8_t> & data) {
    std::vector<uint8_t> b = {0xff, type};
    put_varlen(b, uint32_t(data.size()));
    b.insert(b.end(), data.begin(), data.end());
    return b;
}

int clamp7(int v) { return std::max(0, std::min(127, v)); }

} // namespace

std::vector<uint8_t> write_midi(const std::vector<MidiPart> & parts, const MidiOptions & opt) {
    const double tempo = opt.tempo_bpm > 0 ? opt.tempo_bpm : 120.0;
    const int tpq = opt.ticks_per_quarter;
    auto to_tick = [&](double seconds) { return long(std::lround(std::max(0.0, seconds) * tempo / 60.0 * tpq)); };

    std::vector<uint8_t> file = {'M', 'T', 'h', 'd', 0, 0, 0, 6, 0, 1};
    put_be(file, uint32_t(parts.size() + 1), 2);
    put_be(file, uint32_t(tpq), 2);

    // Conductor track.
    std::vector<Event> conductor;
    uint32_t usec = uint32_t(std::lround(60e6 / tempo));
    conductor.push_back({0, 0, meta(0x51, {uint8_t(usec >> 16), uint8_t(usec >> 8), uint8_t(usec)})});
    conductor.push_back({0, 0, meta(0x58, {4, 2, 24, 8})});
    if (auto key = parse_key(opt.key)) {
        conductor.push_back({0, 0, meta(0x59, {uint8_t(int8_t(key->sharps())), uint8_t(key->minor() ? 1 : 0)})});
    }
    std::vector<uint8_t> out = file;
    std::vector<uint8_t> chunk = track_chunk(conductor);
    out.insert(out.end(), chunk.begin(), chunk.end());

    for (const MidiPart & part : parts) {
        const uint8_t ch = uint8_t(part.channel & 15);
        std::vector<Event> ev;
        if (!part.name.empty()) ev.push_back({0, 0, meta(0x03, std::vector<uint8_t>(part.name.begin(), part.name.end()))});
        if (part.program >= 0 && ch != 9) ev.push_back({0, 1, {uint8_t(0xc0 | ch), uint8_t(clamp7(part.program))}});

        // Which notes may carry bends: those not overlapping any other note.
        std::vector<MidiNote> notes = part.notes;
        std::sort(notes.begin(), notes.end(), [](const MidiNote & a, const MidiNote & b) { return a.start < b.start; });
        bool any_bend = false;
        std::vector<bool> bend_ok(notes.size(), false);
        double earlier_end = -1;  // latest end among the notes before i
        for (size_t i = 0; i < notes.size(); i++) {
            bool overlap = earlier_end > notes[i].start + 1e-6 ||
                           (i + 1 < notes.size() && notes[i + 1].start < notes[i].end - 1e-6);
            earlier_end = std::max(earlier_end, notes[i].end);
            if (notes[i].bend.empty() || ch == 9 || overlap) continue;
            bend_ok[i] = true;
            any_bend = true;
        }
        const int range = std::max(1, std::min(24, opt.bend_range_semitones));
        if (any_bend) {
            // RPN 0 (pitch-bend sensitivity) = range semitones, then null the RPN.
            const uint8_t cc = uint8_t(0xb0 | ch);
            for (auto pair : std::vector<std::pair<int, int>>{{101, 0}, {100, 0}, {6, range}, {38, 0}, {101, 127}, {100, 127}})
                ev.push_back({0, 1, {cc, uint8_t(pair.first), uint8_t(pair.second)}});
        }

        for (size_t i = 0; i < notes.size(); i++) {
            const MidiNote & n = notes[i];
            long on = to_tick(n.start), off = std::max(on + 1, to_tick(n.end));
            uint8_t pitch = uint8_t(clamp7(n.pitch));
            ev.push_back({on, 2, {uint8_t(0x90 | ch), pitch, uint8_t(std::max(1, clamp7(n.velocity)))}});
            ev.push_back({off, 0, {uint8_t(0x80 | ch), pitch, 0}});
            if (!bend_ok[i]) continue;
            for (const auto & [pos, cents] : n.bend) {
                long t = on + long(std::lround(std::max(0.0, std::min(1.0, pos)) * double(off - on)));
                if (t >= off) t = off - 1;
                double v = 8192.0 + cents / (100.0 * range) * 8192.0;
                int w = std::max(0, std::min(16383, int(std::lround(v))));
                ev.push_back({t, 1, {uint8_t(0xe0 | ch), uint8_t(w & 0x7f), uint8_t(w >> 7)}});
            }
            ev.push_back({off, 1, {uint8_t(0xe0 | ch), 0x00, 0x40}});  // back to centre
        }
        chunk = track_chunk(std::move(ev));
        out.insert(out.end(), chunk.begin(), chunk.end());
    }
    return out;
}

} // namespace backtrack
