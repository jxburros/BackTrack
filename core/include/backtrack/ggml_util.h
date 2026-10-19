// ggml and GGUF plumbing shared by the apps: opening a GGUF file, reading
// the audio-model metadata (docs/gguf-metadata.md), and picking a compute
// device.
#pragma once

#include <cstddef>
#include <memory>
#include <string>
#include <vector>

struct ggml_context;
struct gguf_context;
struct ggml_backend;
typedef struct ggml_backend * ggml_backend_t;

namespace backtrack {

// A GGUF file with its tensors loaded into a CPU ggml context.
class GgufFile {
public:
    // meta_only reads the metadata and tensor shapes without the data.
    static std::unique_ptr<GgufFile> open(const std::string & path, bool meta_only = false);  // throws BadInput
    // The same from bytes in memory (the WebAssembly build has no files);
    // the tensors are copied, so `data` can be freed afterwards.
    static std::unique_ptr<GgufFile> open_memory(const void * data, size_t size, const std::string & label);
    // Take ownership of an in-memory GGUF (metadata plus a CPU context
    // holding its tensors), e.g. a generated placeholder model.
    static std::unique_ptr<GgufFile> adopt(gguf_context * gguf, ggml_context * tensors, const std::string & label);
    bool save(const std::string & path) const;
    ~GgufFile();

    gguf_context * gguf() const { return gguf_; }
    ggml_context * tensors() const { return data_; }
    const std::string & path() const { return path_; }

    bool has(const char * key) const;
    std::string str(const char * key, const std::string & fallback = "") const;
    double number(const char * key, double fallback = 0) const;        // any integer or float type
    bool flag(const char * key, bool fallback = false) const;
    std::vector<std::string> strings(const char * key) const;          // empty when absent
    std::vector<double> numbers(const char * key) const;

private:
    GgufFile() = default;
    gguf_context * gguf_ = nullptr;
    ggml_context * data_ = nullptr;
    std::string path_;
};

// The keys every BackTrack audio model carries (docs/gguf-metadata.md).
struct AudioModelInfo {
    std::string arch;              // general.architecture, e.g. "surveil-cnn"
    std::string name;              // general.name
    std::string version;           // general.version
    std::string license;           // general.license
    std::string task;              // audio.task: transcribe | separate
    std::vector<std::string> stems;// audio.stems
    int sample_rate = 0;           // audio.sample_rate
    bool placeholder = false;      // audio.placeholder
};
AudioModelInfo read_audio_model_info(const GgufFile & file);

// Compute devices, as ggml names them ("CPU", "Vulkan0", "CUDA0", "Metal").
struct DeviceInfo {
    std::string name;
    std::string description;
    std::string type;              // cpu | gpu | igpu | accel
};
std::vector<DeviceInfo> list_devices();

// device "" or "auto" picks the best available (a GPU when one is built
// in, else the CPU). threads <= 0 leaves the CPU backend's default.
// Throws BadInput for an unknown device.
ggml_backend_t init_backend(const std::string & device, int threads, std::string * chosen_name = nullptr);

} // namespace backtrack
