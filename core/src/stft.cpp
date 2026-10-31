#include "backtrack/stft.h"

#include <cmath>
#include <stdexcept>

namespace backtrack {

Stft::Stft(int n_fft, int hop) : n_(n_fft), hop_(hop), fft_(n_fft) {
    if (hop < 1 || hop > n_fft / 2) throw std::invalid_argument("STFT hop must be between 1 and n_fft / 2");
    window_.resize(size_t(n_fft));
    for (int i = 0; i < n_fft; i++) window_[size_t(i)] = float(0.5 - 0.5 * std::cos(2.0 * 3.14159265358979323846 * i / n_fft));
}

void Stft::forward(const float * left, const float * right, size_t samples, Spectrum & out_left, Spectrum & out_right) const {
    const int n = n_, nb = bins(), nf = frames(samples);
    out_left.assign(size_t(nf) * size_t(nb), {});
    out_right.assign(size_t(nf) * size_t(nb), {});
    std::vector<std::complex<float>> z(static_cast<size_t>(n));
    for (int t = 0; t < nf; t++) {
        const long long start = (long long)t * hop_ - n / 2;
        for (int i = 0; i < n; i++) {
            const long long j = start + i;
            const bool in = j >= 0 && j < (long long)samples;
            const float w = window_[size_t(i)];
            z[size_t(i)] = in ? std::complex<float>(w * left[j], w * right[j]) : std::complex<float>(0.f, 0.f);
        }
        fft_.forward(z.data());
        // Z = FFT(l + i r): L[k] = (Z[k] + conj(Z[n-k])) / 2, R[k] = (Z[k] - conj(Z[n-k])) / 2i.
        std::complex<float> * L = out_left.data() + size_t(t) * size_t(nb);
        std::complex<float> * R = out_right.data() + size_t(t) * size_t(nb);
        for (int k = 0; k < nb; k++) {
            const std::complex<float> a = z[size_t(k)], b = std::conj(z[size_t((n - k) % n)]);
            L[k] = 0.5f * (a + b);
            const std::complex<float> d = 0.5f * (a - b);
            R[k] = {d.imag(), -d.real()};  // d / i
        }
    }
}

void Stft::inverse(const Spectrum & left, const Spectrum & right, size_t samples, float * out_left, float * out_right) const {
    const int n = n_, nb = bins(), nf = frames(samples);
    if (left.size() != size_t(nf) * size_t(nb) || right.size() != left.size())
        throw std::invalid_argument("spectrum size does not match the signal length");
    std::vector<double> acc_l(samples, 0.0), acc_r(samples, 0.0), wsum(samples, 0.0);
    std::vector<std::complex<float>> z(static_cast<size_t>(n));
    for (int t = 0; t < nf; t++) {
        const std::complex<float> * L = left.data() + size_t(t) * size_t(nb);
        const std::complex<float> * R = right.data() + size_t(t) * size_t(nb);
        // The spectrum of l + i r from the two half spectra (Hermitian halves), conjugated so the
        // forward FFT computes the inverse: ifft(X) = conj(fft(conj(X))) / n.
        for (int k = 0; k < n; k++) {
            std::complex<float> lk, rk;
            if (k < nb) {
                lk = L[k];
                rk = R[k];
            } else {
                lk = std::conj(L[n - k]);
                rk = std::conj(R[n - k]);
            }
            if (k == 0 || k == n / 2) {  // DC and Nyquist of a real signal are real
                lk = {lk.real(), 0.f};
                rk = {rk.real(), 0.f};
            }
            const std::complex<float> x = lk + std::complex<float>(-rk.imag(), rk.real());  // L + i R
            z[size_t(k)] = std::conj(x);
        }
        fft_.forward(z.data());
        const long long start = (long long)t * hop_ - n / 2;
        const float scale = 1.0f / float(n);
        for (int i = 0; i < n; i++) {
            const long long j = start + i;
            if (j < 0 || j >= (long long)samples) continue;
            const std::complex<float> y = std::conj(z[size_t(i)]) * scale;
            const float w = window_[size_t(i)];
            acc_l[size_t(j)] += double(w) * y.real();
            acc_r[size_t(j)] += double(w) * y.imag();
            wsum[size_t(j)] += double(w) * w;
        }
    }
    for (size_t j = 0; j < samples; j++) {
        const double d = wsum[j] > 1e-10 ? wsum[j] : 1.0;
        out_left[j] = float(acc_l[j] / d);
        out_right[j] = float(acc_r[j] / d);
    }
}

} // namespace backtrack
