#include "backtrack/fft.h"

#include <cmath>
#include <stdexcept>

namespace backtrack {

Fft::Fft(int n) : n_(n) {
    if (n < 2 || (n & (n - 1)) != 0) throw std::invalid_argument("FFT size must be a power of two");
    int bits = 0;
    while ((1 << bits) < n) bits++;
    bitrev_.resize(size_t(n));
    for (int i = 0; i < n; i++) {
        int r = 0;
        for (int b = 0; b < bits; b++) r |= ((i >> b) & 1) << (bits - 1 - b);
        bitrev_[size_t(i)] = r;
    }
    twiddle_.resize(size_t(n / 2));
    for (int k = 0; k < n / 2; k++) {
        double a = -2.0 * 3.14159265358979323846 * k / n;
        twiddle_[size_t(k)] = {float(std::cos(a)), float(std::sin(a))};
    }
}

void Fft::forward(std::complex<float> * x) const {
    const int n = n_;
    for (int i = 0; i < n; i++) {
        int j = bitrev_[size_t(i)];
        if (j > i) std::swap(x[i], x[j]);
    }
    for (int len = 2; len <= n; len <<= 1) {
        const int half = len / 2, step = n / len;
        for (int start = 0; start < n; start += len) {
            for (int k = 0; k < half; k++) {
                std::complex<float> t = twiddle_[size_t(k * step)] * x[start + k + half];
                x[start + k + half] = x[start + k] - t;
                x[start + k] += t;
            }
        }
    }
}

} // namespace backtrack
