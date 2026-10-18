#include "backtrack/audio.h"

#include "backtrack/common.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iterator>

#define DR_WAV_IMPLEMENTATION
#define DR_WAV_NO_STDIO
#include "dr_wav.h"
#define DR_FLAC_IMPLEMENTATION
#define DR_FLAC_NO_STDIO
#define DR_FLAC_NO_OGG
#include "dr_flac.h"
#define DR_MP3_IMPLEMENTATION
#define DR_MP3_NO_STDIO
#include "dr_mp3.h"

namespace backtrack {

namespace {

bool starts_with(const uint8_t * d, size_t n, const char * magic) {
    size_t m = std::strlen(magic);
    return n >= m && std::memcmp(d, magic, m) == 0;
}

bool looks_like_mp3(const uint8_t * d, size_t n) {
    if (starts_with(d, n, "ID3")) return true;
    // MPEG audio frame sync: 11 set bits, a valid layer.
    return n >= 2 && d[0] == 0xff && (d[1] & 0xe0) == 0xe0 && (d[1] & 0x06) != 0;
}

void check_decoded(const Audio & a, double max_seconds) {
    if (a.channels <= 0 || a.sample_rate <= 0 || a.samples.empty())
        throw BadInput("the audio contains no samples");
    if (a.sample_rate < 4000 || a.sample_rate > 384000)
        throw BadInput("unsupported sample rate " + std::to_string(a.sample_rate));
    if (a.seconds() > max_seconds)
        throw BadInput("the audio is longer than the " + std::to_string(int(max_seconds)) + " s limit");
}

} // namespace

Audio decode_audio(const uint8_t * data, size_t size, double max_seconds) {
    if (!data || size < 4) throw BadInput("the audio is empty");
    Audio a;
    if (starts_with(data, size, "RIFF") || starts_with(data, size, "RF64") || starts_with(data, size, "riff")) {
        drwav wav;
        if (!drwav_init_memory(&wav, data, size, nullptr)) throw BadInput("unreadable WAV file");
        if (wav.channels == 0 || wav.channels > 64) {
            drwav_uninit(&wav);
            throw BadInput("unsupported WAV channel count");
        }
        if (wav.sampleRate && double(wav.totalPCMFrameCount) / wav.sampleRate > max_seconds) {
            drwav_uninit(&wav);
            throw BadInput("the audio is longer than the " + std::to_string(int(max_seconds)) + " s limit");
        }
        a.sample_rate = int(wav.sampleRate);
        a.channels = int(wav.channels);
        a.samples.resize(size_t(wav.totalPCMFrameCount) * wav.channels);
        drwav_uint64 got = drwav_read_pcm_frames_f32(&wav, wav.totalPCMFrameCount, a.samples.data());
        a.samples.resize(size_t(got) * wav.channels);
        drwav_uninit(&wav);
    } else if (starts_with(data, size, "fLaC")) {
        unsigned int channels = 0, rate = 0;
        drflac_uint64 frames = 0;
        float * pcm = drflac_open_memory_and_read_pcm_frames_f32(data, size, &channels, &rate, &frames, nullptr);
        if (!pcm) throw BadInput("unreadable FLAC file");
        a.sample_rate = int(rate);
        a.channels = int(channels);
        a.samples.assign(pcm, pcm + size_t(frames) * channels);
        drflac_free(pcm, nullptr);
    } else if (looks_like_mp3(data, size)) {
        drmp3_config cfg{};
        drmp3_uint64 frames = 0;
        float * pcm = drmp3_open_memory_and_read_pcm_frames_f32(data, size, &cfg, &frames, nullptr);
        if (!pcm) throw BadInput("unreadable MP3 file");
        a.sample_rate = int(cfg.sampleRate);
        a.channels = int(cfg.channels);
        a.samples.assign(pcm, pcm + size_t(frames) * cfg.channels);
        drmp3_free(pcm, nullptr);
    } else {
        throw BadInput("unrecognised audio format (expected WAV, FLAC or MP3)");
    }
    check_decoded(a, max_seconds);
    return a;
}

Audio load_audio_file(const std::string & path, double max_seconds) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw BadInput("cannot open " + path);
    std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    return decode_audio(bytes.data(), bytes.size(), max_seconds);
}

std::vector<float> to_mono(const Audio & a) {
    size_t n = a.frames();
    std::vector<float> out(n);
    if (a.channels == 1) {
        std::copy(a.samples.begin(), a.samples.begin() + n, out.begin());
        return out;
    }
    float scale = 1.0f / float(a.channels);
    for (size_t i = 0; i < n; i++) {
        float s = 0;
        for (int c = 0; c < a.channels; c++) s += a.samples[i * a.channels + c];
        out[i] = s * scale;
    }
    return out;
}

// ---------------------------------------------------------------------------
// Resampling: for each output sample, a Kaiser-windowed sinc centred on its
// position in the input. The kernel is tabulated at kTableRes points per zero
// crossing and linearly interpolated, which keeps the error well below
// 16-bit noise.
// ---------------------------------------------------------------------------

namespace {

double bessel_i0(double x) {
    double sum = 1, term = 1, q = x * x / 4;
    for (int k = 1; k < 50; k++) {
        term *= q / (double(k) * k);
        sum += term;
        if (term < 1e-12 * sum) break;
    }
    return sum;
}

constexpr double kPi = 3.14159265358979323846;
constexpr int kZeroCrossings = 32;
constexpr int kTableRes = 512;
constexpr double kBeta = 9.0;

const std::vector<float> & sinc_table() {
    static const std::vector<float> table = [] {
        std::vector<float> t(kZeroCrossings * kTableRes + 2);
        double i0b = bessel_i0(kBeta);
        for (size_t i = 0; i < t.size(); i++) {
            double x = double(i) / kTableRes;  // in zero crossings
            if (x >= kZeroCrossings) { t[i] = 0; continue; }
            double sinc = x == 0 ? 1.0 : std::sin(kPi * x) / (kPi * x);
            double r = x / kZeroCrossings;
            double w = bessel_i0(kBeta * std::sqrt(std::max(0.0, 1 - r * r))) / i0b;
            t[i] = float(sinc * w);
        }
        return t;
    }();
    return table;
}

} // namespace

std::vector<float> resample(const std::vector<float> & x, int rate_in, int rate_out, const Progress & progress) {
    if (rate_in == rate_out || x.empty()) return x;
    const std::vector<float> & table = sinc_table();
    const double ratio = double(rate_out) / rate_in;
    const double cutoff = std::min(1.0, ratio);         // fraction of the input Nyquist
    const double span = kZeroCrossings / cutoff;        // half-width in input samples
    const size_t n_out = size_t(std::floor(double(x.size()) * ratio));
    std::vector<float> y(n_out);
    const long n_in = long(x.size());
    for (size_t j = 0; j < n_out; j++) {
        if ((j & 0xffff) == 0) progress.check();
        double centre = double(j) / ratio;
        long lo = long(std::ceil(centre - span));
        long hi = long(std::floor(centre + span));
        double acc = 0;
        for (long i = std::max(lo, 0L); i <= std::min(hi, n_in - 1); i++) {
            double pos = std::fabs(double(i) - centre) * cutoff * kTableRes;
            size_t k = size_t(pos);
            double frac = pos - double(k);
            double w = table[k] + (table[k + 1] - table[k]) * frac;
            acc += w * x[size_t(i)];
        }
        y[j] = float(acc * cutoff);
    }
    return y;
}

// ---------------------------------------------------------------------------
// WAV writing
// ---------------------------------------------------------------------------

namespace {
void put_u16(std::vector<uint8_t> & b, uint32_t v) { b.push_back(uint8_t(v)); b.push_back(uint8_t(v >> 8)); }
void put_u32(std::vector<uint8_t> & b, uint32_t v) { put_u16(b, v & 0xffff); put_u16(b, v >> 16); }
} // namespace

std::vector<uint8_t> encode_wav(const Audio & a, WavFormat format) {
    const int bytes_per_sample = format == WavFormat::pcm16 ? 2 : format == WavFormat::pcm24 ? 3 : 4;
    const uint32_t data_bytes = uint32_t(a.samples.size() * bytes_per_sample);
    std::vector<uint8_t> b;
    b.reserve(44 + data_bytes);
    b.insert(b.end(), {'R', 'I', 'F', 'F'});
    put_u32(b, 36 + data_bytes);
    b.insert(b.end(), {'W', 'A', 'V', 'E', 'f', 'm', 't', ' '});
    put_u32(b, 16);
    put_u16(b, format == WavFormat::float32 ? 3 : 1);
    put_u16(b, uint32_t(a.channels));
    put_u32(b, uint32_t(a.sample_rate));
    put_u32(b, uint32_t(a.sample_rate * a.channels * bytes_per_sample));
    put_u16(b, uint32_t(a.channels * bytes_per_sample));
    put_u16(b, uint32_t(bytes_per_sample * 8));
    b.insert(b.end(), {'d', 'a', 't', 'a'});
    put_u32(b, data_bytes);
    for (float s : a.samples) {
        if (format == WavFormat::float32) {
            uint32_t v;
            std::memcpy(&v, &s, 4);
            put_u32(b, v);
            continue;
        }
        double c = std::max(-1.0, std::min(1.0, double(s)));
        if (format == WavFormat::pcm16) {
            put_u16(b, uint32_t(uint16_t(int16_t(std::lrint(c * 32767.0)))));
        } else {
            int32_t v = int32_t(std::lrint(c * 8388607.0));
            b.push_back(uint8_t(v)); b.push_back(uint8_t(v >> 8)); b.push_back(uint8_t(v >> 16));
        }
    }
    return b;
}

} // namespace backtrack
