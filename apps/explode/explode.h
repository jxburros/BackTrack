// BackTrack Explode: the stem separator's pipeline (docs/plans/explode.md).
//
//   decode -> 44.1 kHz stereo -> chunks with overlap -> per chunk and pass:
//   STFT -> model (a spectrum per stem) -> residual shared out -> grouped into the requested stems
//   -> inverse STFT -> crossfaded into the output.
//
// The stems always sum to the 44.1 kHz stereo mix (to float precision): the residual a model
// leaves is shared out bin by bin by the stems' power there, the STFT reconstructs exactly, and
// the chunks' crossfades sum to one.
#pragma once

#include "backtrack/audio.h"
#include "backtrack/common.h"
#include "backtrack/stft.h"

#include <memory>
#include <string>
#include <vector>

namespace explode {

inline constexpr int kDefaultPort = 8827;
inline constexpr const char * kAppName = "BackTrack Explode";
inline constexpr int kSampleRate = 44100;

struct ModelInfo {
    std::string id, name, version, license = "MIT";
    std::vector<std::string> stems;            // what it makes by default, in the design's order
    std::vector<std::string> on_request_stems; // made only when asked for
    bool placeholder = false;
    int n_fft = 2048, hop = 512;
};

// A separation model: from one chunk's stereo spectrogram, an estimate of each stem's. The
// estimates need not add up; the pipeline shares out what they miss.
class Model {
public:
    virtual ~Model() = default;
    virtual const ModelInfo & info() const = 0;
    // in_l/in_r: frames * bins (backtrack::Spectrum layout). Fill out_l/out_r with one spectrum
    // per info().stems entry, same layout. Call progress.check() between long steps.
    virtual void separate(const backtrack::Spectrum & in_l, const backtrack::Spectrum & in_r, int frames, int bins,
                          std::vector<backtrack::Spectrum> & out_l, std::vector<backtrack::Spectrum> & out_r,
                          const backtrack::Progress & progress) const = 0;
};

// The built-in placeholder (no training): soft masks from harmonic/percussive separation
// (median filters across time and frequency), the low band and how centred a bin is in the
// stereo field. Drums: percussive. Bass: harmonic, low. Vocals: harmonic, centred, 120 Hz-8 kHz.
// Other: the rest. Rough, but real stems that sum to the mix.
std::unique_ptr<Model> make_placeholder();

// The models Explode knows. S0 has only the built-in placeholder; trained GGUF separators
// (audio.task = separate) come with S2.
class Registry {
public:
    Registry();
    const std::vector<std::unique_ptr<Model>> & models() const { return models_; }
    // "" picks the default model; otherwise an id from models(). Throws NotFound.
    const Model & get(const std::string & id) const;

private:
    std::vector<std::unique_ptr<Model>> models_;
};

struct Options {
    std::vector<std::string> stems;            // empty: the model's default stems
    int passes = 1;                            // 1-4: plain, then flip + swap, flip, swap (averaged)
    double chunk_seconds = 20.0;
    double overlap_seconds = 1.0;
};

// Passes for Song Deck's `augment` flag.
inline constexpr int kAugmentPasses = 2;

// What each pass does to the input (and undoes on the output).
const char * pass_name(int pass);  // 0-based: "plain", "flip + swap", "flip", "swap"

// The output stems for a request: the requested ones (checked against the model), in the order
// asked, plus "other" when nothing requested can hold the rest. "other" and "instrumental" hold
// everything not in the other requested stems; asking for both is an error. Throws BadInput.
struct StemPlan {
    std::vector<std::string> outputs;
    std::vector<int> target;                   // per model stem: index into outputs
};
StemPlan plan_stems(const ModelInfo & model, const std::vector<std::string> & requested);

struct Result {
    std::vector<std::string> names;            // the output stems, in order
    std::vector<backtrack::Audio> stems;       // 44.1 kHz stereo each, same length as `mix`
    backtrack::Audio mix;                      // the input as separated: 44.1 kHz stereo
    std::string model;
    bool placeholder = false;
    int passes = 1, chunks = 0;
    double seconds = 0;
};

// The input at 44.1 kHz stereo (mono doubled; more channels folded, even ones left, odd right).
backtrack::Audio to_stereo_44k(const backtrack::Audio & audio, const backtrack::Progress & progress = {});

Result separate(const backtrack::Audio & audio, const Model & model, const Options & options,
                const backtrack::Progress & progress = {});

} // namespace explode
