// Harmonic constant-Q transform: the transcriber's front end.
//
// A log-frequency magnitude spectrum, computed once over an extended range
// and then read at several harmonic offsets, so channel h at bin k holds the
// energy at h times bin k's frequency. The exact maths is in
// docs/front-end.md; the training code must reproduce it (the T1 parity
// check compares the two).
#pragma once

#include "backtrack/common.h"

#include <vector>

namespace backtrack {

struct HcqtConfig {
    int sample_rate = 22050;
    int hop = 256;                 // samples between frames (frame t is centred on sample t*hop)
    double fmin = 27.5;            // frequency of the centre bin of the lowest pitch (A0)
    int bins_per_semitone = 3;
    int n_bins = 264;              // 88 pitches x 3
    std::vector<float> harmonics = {0.5f, 1, 2, 3, 4, 5, 6, 7};
    double top_fraction = 0.9;     // bins above this fraction of Nyquist are left at 0
    double filter_scale = 1.0;     // kernel length multiplier; < 1 trades frequency for time resolution

    int bins_per_octave() const { return 12 * bins_per_semitone; }
    // Frequency of bin k (fractional k allowed; bins outside 0..n_bins-1 are the extended range).
    double bin_frequency(double k) const;
};

struct Hcqt {
    int n_frames = 0;
    int n_bins = 0;
    int n_harmonics = 0;
    // Normalised log magnitude in 0..1, laid out [harmonic][frame][bin]:
    // data[(h * n_frames + t) * n_bins + k]. That is a ggml tensor with
    // ne0 = bins, ne1 = frames, ne2 = harmonics.
    std::vector<float> data;

    float at(int h, int t, int k) const { return data[(size_t(h) * n_frames + t) * n_bins + k]; }
};

// mono must already be at cfg.sample_rate.
Hcqt compute_hcqt(const std::vector<float> & mono, const HcqtConfig & cfg, const Progress & progress = {},
                  double progress_from = 0.0, double progress_to = 1.0);

// Magnitude to the 0..1 scale the model sees: 1 + 20 log10(mag) / 80,
// clamped (a full-scale sine is about 0.92, -80 dB and below is 0).
float normalise_magnitude(float mag);

} // namespace backtrack
