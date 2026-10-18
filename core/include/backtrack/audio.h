// Audio in and out: decode WAV, FLAC and MP3 from memory or a file, mix
// down, resample, and write WAV.
#pragma once

#include "backtrack/common.h"

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace backtrack {

struct Audio {
    int sample_rate = 0;
    int channels = 0;
    std::vector<float> samples;  // interleaved, nominally -1..1

    size_t frames() const { return channels ? samples.size() / size_t(channels) : 0; }
    double seconds() const { return sample_rate ? double(frames()) / sample_rate : 0.0; }
};

// Format detected from the bytes (RIFF/RF64/W64 WAV, fLaC, or MP3 with or
// without an ID3 tag), not from a file name. Throws BadInput on anything it
// cannot decode, on empty audio and on files longer than max_seconds.
Audio decode_audio(const uint8_t * data, size_t size, double max_seconds = 3600.0);
Audio load_audio_file(const std::string & path, double max_seconds = 3600.0);

// Average of all channels.
std::vector<float> to_mono(const Audio & audio);

// Band-limited resampling (Kaiser-windowed sinc, 32 zero crossings at the
// lower of the two rates). Returns the input unchanged when the rates match.
// Checks progress for cancellation every 64k output samples.
std::vector<float> resample(const std::vector<float> & x, int rate_in, int rate_out, const Progress & progress = {});

enum class WavFormat { pcm16, pcm24, float32 };
std::vector<uint8_t> encode_wav(const Audio & audio, WavFormat format = WavFormat::pcm16);

} // namespace backtrack
