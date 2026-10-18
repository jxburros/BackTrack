#pragma once

#include <complex>
#include <vector>

namespace backtrack {

// In-place iterative radix-2 complex FFT of a fixed power-of-two size.
class Fft {
public:
    explicit Fft(int n);
    int size() const { return n_; }
    // X[k] = sum_n x[n] e^{-2 pi i k n / N} (no scaling).
    void forward(std::complex<float> * data) const;

private:
    int n_;
    std::vector<int> bitrev_;
    std::vector<std::complex<float>> twiddle_;
};

inline int next_pow2(int n) {
    int p = 1;
    while (p < n) p <<= 1;
    return p;
}

} // namespace backtrack
