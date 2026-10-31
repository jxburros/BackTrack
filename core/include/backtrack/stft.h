// Short-time Fourier transform and its inverse, for spectrogram masking
// (Explode). Frames are centred (frame t is centred on sample t * hop, the
// signal is zero outside), the window is a periodic Hann, and the inverse
// overlap-adds with the squared-window sum, so inverse(forward(x)) gives x
// back to float precision for any hop up to n_fft / 2.
//
// Stereo is the usual case, so both channels share one complex FFT per frame
// (left in the real part, right in the imaginary part), forward and inverse.
#pragma once

#include "backtrack/fft.h"

#include <complex>
#include <cstddef>
#include <vector>

namespace backtrack {

// One channel's spectrogram: frames * bins values, frame-major (bin k of
// frame t at t * bins + k).
using Spectrum = std::vector<std::complex<float>>;

class Stft {
public:
    Stft(int n_fft, int hop);

    int n_fft() const { return n_; }
    int hop() const { return hop_; }
    int bins() const { return n_ / 2 + 1; }
    // Frames for a signal of this length (the last one reaches past the end).
    int frames(size_t samples) const { return int(samples / size_t(hop_)) + 1; }

    // Both channels of a stereo signal of `samples` samples each.
    void forward(const float * left, const float * right, size_t samples, Spectrum & out_left, Spectrum & out_right) const;
    // Back to `samples` samples per channel (written, not added).
    void inverse(const Spectrum & left, const Spectrum & right, size_t samples, float * out_left, float * out_right) const;

private:
    int n_, hop_;
    Fft fft_;
    std::vector<float> window_;
};

} // namespace backtrack
