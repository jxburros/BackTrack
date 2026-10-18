// The Surveil model: a small convolutional network over the harmonic CQT,
// in the spirit of Basic Pitch, run on ggml with weights from GGUF.
//
//   input  [bins 264, frames, harmonics 8]
//   trunk  conv 3x3 -> relu -> conv 3x3 -> relu
//   pitched heads (audio.kind = "pitched"):
//     contour  1x1 conv on the trunk          -> [264 bins, frames]  (pitch curve, 3 bins per semitone)
//     pool     3x1 conv, stride 3 in frequency -> [88 pitches, frames, channels]
//     note / onset / offset  1x1, 1x3, 1x3 convs on the pool -> [88, frames] each
//   drum head (audio.kind = "drums"):
//     band     dense over (bins x channels)   -> [classes, frames]
//     onset    1x3 conv in time               -> [classes, frames]
//
// The layout, tensor names and metadata are in docs/gguf-metadata.md.
#pragma once

#include "backtrack/common.h"
#include "backtrack/ggml_util.h"
#include "backtrack/hcqt.h"

#include <memory>
#include <string>
#include <vector>

namespace surveil {

struct ModelSpec {
    std::string id;                      // what requests and the CLI call it
    std::string arch;                    // general.architecture
    std::string kind;                    // pitched | drums
    std::string name, version, license;
    std::vector<std::string> stems;      // Song Deck sources it covers
    std::vector<std::string> sources;    // training-data record (audio.sources)
    bool placeholder = false;
    backtrack::HcqtConfig front;
    int pitch_min = 21;                  // pitched: MIDI note of output 0
    int n_outputs = 0;                   // pitches or drum classes
    std::vector<std::string> classes;    // drums
    std::vector<int> class_notes;        // drums: General MIDI note per class

    double frame_rate() const { return double(front.sample_rate) / front.hop; }
};

// Per-frame likelihoods, [frame][output] (contour: [frame][bin]).
struct Activations {
    int frames = 0, outputs = 0, bins = 0;
    double frame_rate = 0;
    std::vector<float> onset, note, offset, contour;
};

class Model {
public:
    // Reads the spec from the metadata and copies the tensors to the backend.
    static std::unique_ptr<Model> load(std::unique_ptr<backtrack::GgufFile> file, const std::string & id,
                                       ggml_backend_t backend);
    ~Model();

    const ModelSpec & spec() const { return spec_; }

    // Runs in chunks of frames (progress and cancellation between chunks).
    Activations run(const backtrack::Hcqt & input, const backtrack::Progress & progress, double p0, double p1) const;

private:
    Model() = default;
    struct Impl;
    std::unique_ptr<Impl> impl_;
    ModelSpec spec_;
};

// A placeholder model with the real graph: kind "pitched" or "drums";
// init "wired" (hand-set weights that turn harmonic energy into notes, so
// output follows the audio) or "random" (seeded N(0, 0.1)). Returns it as an
// in-memory GGUF, ready to save or load.
std::unique_ptr<backtrack::GgufFile> make_placeholder(const std::string & kind, const std::string & init, uint64_t seed);

} // namespace surveil
