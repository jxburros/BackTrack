#include "backtrack/hcqt.h"

#include "backtrack/fft.h"

#include <algorithm>
#include <cmath>
#include <complex>

namespace backtrack {

namespace {

constexpr double kPi = 3.14159265358979323846;

double bessel_i0(double x) {
    double sum = 1, term = 1, q = x * x / 4;
    for (int k = 1; k < 60; k++) {
        term *= q / (double(k) * k);
        sum += term;
        if (term < 1e-14 * sum) break;
    }
    return sum;
}

// Half-band low-pass for decimation by two: 101-tap Kaiser-windowed sinc,
// beta 8, cutoff at a quarter of the input rate. Passband to 0.45, stopband
// from 0.55 of the input Nyquist, so nothing that aliases lands below the
// next octave's top bin (0.9 of the new Nyquist).
const std::vector<double> & decimation_filter() {
    static const std::vector<double> h = [] {
        const int taps = 101, mid = taps / 2;
        const double beta = 8.0, i0b = bessel_i0(beta);
        std::vector<double> c(taps);
        double sum = 0;
        for (int i = 0; i < taps; i++) {
            int m = i - mid;
            double sinc = m == 0 ? 0.5 : std::sin(0.5 * kPi * m) / (kPi * m);
            double r = double(m) / mid;
            c[size_t(i)] = sinc * bessel_i0(beta * std::sqrt(std::max(0.0, 1 - r * r))) / i0b;
            sum += c[size_t(i)];
        }
        for (double & v : c) v /= sum;  // unity gain at DC
        return c;
    }();
    return h;
}

// y[i] = sum_j h[j] x[2i + mid - j]: zero-phase, so sample i of the output
// sits at time 2i of the input.
std::vector<float> decimate2(const std::vector<float> & x) {
    const std::vector<double> & h = decimation_filter();
    const long taps = long(h.size()), mid = taps / 2, n = long(x.size());
    std::vector<float> y(static_cast<size_t>((n + 1) / 2));
    for (long i = 0; i < long(y.size()); i++) {
        double acc = 0;
        long centre = 2 * i;
        long jlo = std::max(0L, centre + mid - (n - 1)), jhi = std::min(taps - 1, centre + mid);
        for (long j = jlo; j <= jhi; j++) acc += h[size_t(j)] * x[size_t(centre + mid - j)];
        y[size_t(i)] = float(acc);
    }
    return y;
}

struct SparseKernel {
    int ext_bin;                                 // index into the extended bin range
    std::vector<int> idx;                        // FFT bins
    std::vector<std::complex<float>> coef;       // conj(G[k]) / L
};

struct Group {
    int octave = 0;                              // signal decimated 2^octave times
    std::vector<SparseKernel> kernels;
};

} // namespace

double HcqtConfig::bin_frequency(double k) const {
    const double centre = bins_per_semitone / 2;  // bin of fmin (1 for three bins per semitone)
    return fmin * std::pow(2.0, (k - centre) / bins_per_octave());
}

float normalise_magnitude(float mag) {
    float db = 20.0f * std::log10(std::max(mag, 1e-5f));
    return std::min(1.0f, std::max(0.0f, 1.0f + db / 80.0f));
}

Hcqt compute_hcqt(const std::vector<float> & mono, const HcqtConfig & cfg, const Progress & progress,
                  double p0, double p1) {
    const int B = cfg.bins_per_octave();
    const int H = int(cfg.harmonics.size());
    if (cfg.hop <= 0 || cfg.n_bins <= 0 || H == 0 || cfg.sample_rate <= 0)
        throw BadInput("invalid front-end configuration");

    // Extended bin range: every bin any harmonic reads.
    std::vector<int> offset(static_cast<size_t>(H));
    int lo = 0, hi = cfg.n_bins - 1;
    for (int h = 0; h < H; h++) {
        offset[size_t(h)] = int(std::lround(B * std::log2(double(cfg.harmonics[size_t(h)]))));
        lo = std::min(lo, offset[size_t(h)]);
        hi = std::max(hi, cfg.n_bins - 1 + offset[size_t(h)]);
    }
    const int n_ext = hi - lo + 1;

    if (!(cfg.filter_scale > 0) || cfg.filter_scale > 4) throw BadInput("invalid front-end filter scale");
    const double q = cfg.filter_scale / (std::pow(2.0, 1.0 / B) - 1.0);
    const double f_cap = cfg.top_fraction * cfg.sample_rate / 2.0;

    // Assign each computable bin to an octave group; group o runs at
    // sample_rate / 2^o, so every group shares one FFT size. (A kernel would
    // be up to 4q/top_fraction samples at its group's lowest bin; the cap at
    // L - 1 shortens most of them. docs/front-end.md, "Kernels".)
    int n_groups = 0;
    std::vector<int> bin_group(size_t(n_ext), -1);
    for (int e = 0; e < n_ext; e++) {
        double f = cfg.bin_frequency(e + lo);
        if (f > f_cap) continue;
        int o = int(std::floor(std::log2(f_cap / f)));
        bin_group[size_t(e)] = o;
        n_groups = std::max(n_groups, o + 1);
    }
    const int L = next_pow2(int(std::ceil(2.0 * q / cfg.top_fraction)) + 2);
    Fft fft(L);

    std::vector<Group> groups(static_cast<size_t>(n_groups));
    std::vector<std::complex<float>> buf(static_cast<size_t>(L));
    for (int e = 0; e < n_ext; e++) {
        int o = bin_group[size_t(e)];
        if (o < 0) continue;
        groups[size_t(o)].octave = o;
        const double rate = cfg.sample_rate / std::pow(2.0, o);
        const double f = cfg.bin_frequency(e + lo);
        int n = int(std::ceil(q * rate / f));
        n |= 1;                                  // odd, so the kernel has a centre sample
        n = std::min(n, L - 1);
        const int half = n / 2;
        double wsum = 0;
        std::fill(buf.begin(), buf.end(), std::complex<float>(0, 0));
        std::vector<double> w(static_cast<size_t>(n));
        for (int m = -half; m <= half; m++) {
            w[size_t(m + half)] = 0.5 * (1 + std::cos(2 * kPi * m / (n + 1)));
            wsum += w[size_t(m + half)];
        }
        for (int m = -half; m <= half; m++) {
            double a = 2 * kPi * f * m / rate;
            double g = w[size_t(m + half)] / wsum;
            buf[size_t(L / 2 + m)] = {float(g * std::cos(a)), float(g * std::sin(a))};
        }
        fft.forward(buf.data());
        float peak = 0;
        for (auto & v : buf) peak = std::max(peak, std::abs(v));
        SparseKernel k;
        k.ext_bin = e;
        for (int i = 0; i < L; i++) {
            if (std::abs(buf[size_t(i)]) >= 1e-3f * peak) {
                k.idx.push_back(i);
                k.coef.push_back(std::conj(buf[size_t(i)]) / float(L));
            }
        }
        groups[size_t(o)].kernels.push_back(std::move(k));
    }

    Hcqt out;
    out.n_frames = int(mono.size() / size_t(cfg.hop)) + 1;
    out.n_bins = cfg.n_bins;
    out.n_harmonics = H;
    const int T = out.n_frames;
    std::vector<float> ext(size_t(T) * n_ext, 0.0f);

    std::vector<float> signal = mono;
    for (int o = 0; o < n_groups; o++) {
        if (o > 0) signal = decimate2(signal);
        const Group & g = groups[size_t(o)];
        if (g.kernels.empty()) continue;
        const double scale = std::pow(2.0, o);
        const long n = long(signal.size());
        for (int t = 0; t < T; t++) {
            if ((t & 255) == 0)
                progress.step("front end", p0 + (p1 - p0) * (o + double(t) / T) / n_groups);
            long c = std::lround(double(t) * cfg.hop / scale);
            long start = c - L / 2;
            for (int i = 0; i < L; i++) {
                long s = start + i;
                buf[size_t(i)] = {s >= 0 && s < n ? signal[size_t(s)] : 0.0f, 0.0f};
            }
            fft.forward(buf.data());
            float * row = &ext[size_t(t) * n_ext];
            for (const SparseKernel & k : g.kernels) {
                std::complex<float> acc(0, 0);
                for (size_t j = 0; j < k.idx.size(); j++) acc += buf[size_t(k.idx[j])] * k.coef[j];
                row[k.ext_bin] = normalise_magnitude(std::abs(acc));
            }
        }
    }

    out.data.assign(size_t(H) * T * cfg.n_bins, 0.0f);
    for (int h = 0; h < H; h++) {
        for (int t = 0; t < T; t++) {
            const float * row = &ext[size_t(t) * n_ext];
            float * dst = &out.data[(size_t(h) * T + t) * cfg.n_bins];
            for (int k = 0; k < cfg.n_bins; k++) dst[k] = row[k + offset[size_t(h)] - lo];
        }
    }
    progress.set("front end", p1);
    return out;
}

} // namespace backtrack
