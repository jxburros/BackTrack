#include "backtrack/ggml_util.h"

#include "backtrack/common.h"

#include "ggml-backend.h"
#include "ggml-cpu.h"
#include "ggml.h"
#include "gguf.h"

namespace backtrack {

std::unique_ptr<GgufFile> GgufFile::open(const std::string & path, bool meta_only) {
    std::unique_ptr<GgufFile> f(new GgufFile());
    f->path_ = path;
    gguf_init_params params{};
    params.no_alloc = meta_only;
    params.ctx = &f->data_;
    f->gguf_ = gguf_init_from_file(path.c_str(), params);
    if (!f->gguf_) throw BadInput("cannot read GGUF file " + path);
    return f;
}

std::unique_ptr<GgufFile> GgufFile::adopt(gguf_context * gguf, ggml_context * tensors, const std::string & label) {
    std::unique_ptr<GgufFile> f(new GgufFile());
    f->gguf_ = gguf;
    f->data_ = tensors;
    f->path_ = label;
    return f;
}

bool GgufFile::save(const std::string & path) const { return gguf_write_to_file(gguf_, path.c_str(), false); }

GgufFile::~GgufFile() {
    if (gguf_) gguf_free(gguf_);
    if (data_) ggml_free(data_);
}

bool GgufFile::has(const char * key) const { return gguf_find_key(gguf_, key) >= 0; }

std::string GgufFile::str(const char * key, const std::string & fallback) const {
    int64_t id = gguf_find_key(gguf_, key);
    if (id < 0 || gguf_get_kv_type(gguf_, id) != GGUF_TYPE_STRING) return fallback;
    return gguf_get_val_str(gguf_, id);
}

static double scalar(const gguf_context * g, int64_t id, gguf_type t, double fallback) {
    switch (t) {
        case GGUF_TYPE_UINT8: return gguf_get_val_u8(g, id);
        case GGUF_TYPE_INT8: return gguf_get_val_i8(g, id);
        case GGUF_TYPE_UINT16: return gguf_get_val_u16(g, id);
        case GGUF_TYPE_INT16: return gguf_get_val_i16(g, id);
        case GGUF_TYPE_UINT32: return gguf_get_val_u32(g, id);
        case GGUF_TYPE_INT32: return gguf_get_val_i32(g, id);
        case GGUF_TYPE_UINT64: return double(gguf_get_val_u64(g, id));
        case GGUF_TYPE_INT64: return double(gguf_get_val_i64(g, id));
        case GGUF_TYPE_FLOAT32: return gguf_get_val_f32(g, id);
        case GGUF_TYPE_FLOAT64: return gguf_get_val_f64(g, id);
        case GGUF_TYPE_BOOL: return gguf_get_val_bool(g, id) ? 1 : 0;
        default: return fallback;
    }
}

double GgufFile::number(const char * key, double fallback) const {
    int64_t id = gguf_find_key(gguf_, key);
    return id < 0 ? fallback : scalar(gguf_, id, gguf_get_kv_type(gguf_, id), fallback);
}

bool GgufFile::flag(const char * key, bool fallback) const {
    return number(key, fallback ? 1 : 0) != 0;
}

std::vector<std::string> GgufFile::strings(const char * key) const {
    std::vector<std::string> out;
    int64_t id = gguf_find_key(gguf_, key);
    if (id < 0 || gguf_get_kv_type(gguf_, id) != GGUF_TYPE_ARRAY || gguf_get_arr_type(gguf_, id) != GGUF_TYPE_STRING)
        return out;
    for (size_t i = 0; i < gguf_get_arr_n(gguf_, id); i++) out.emplace_back(gguf_get_arr_str(gguf_, id, i));
    return out;
}

std::vector<double> GgufFile::numbers(const char * key) const {
    std::vector<double> out;
    int64_t id = gguf_find_key(gguf_, key);
    if (id < 0 || gguf_get_kv_type(gguf_, id) != GGUF_TYPE_ARRAY) return out;
    const gguf_type t = gguf_get_arr_type(gguf_, id);
    const void * data = gguf_get_arr_data(gguf_, id);
    const size_t n = gguf_get_arr_n(gguf_, id);
    for (size_t i = 0; i < n; i++) {
        switch (t) {
            case GGUF_TYPE_FLOAT32: out.push_back(static_cast<const float *>(data)[i]); break;
            case GGUF_TYPE_FLOAT64: out.push_back(static_cast<const double *>(data)[i]); break;
            case GGUF_TYPE_INT32: out.push_back(static_cast<const int32_t *>(data)[i]); break;
            case GGUF_TYPE_UINT32: out.push_back(static_cast<const uint32_t *>(data)[i]); break;
            case GGUF_TYPE_INT64: out.push_back(double(static_cast<const int64_t *>(data)[i])); break;
            case GGUF_TYPE_UINT8: out.push_back(static_cast<const uint8_t *>(data)[i]); break;
            case GGUF_TYPE_INT8: out.push_back(static_cast<const int8_t *>(data)[i]); break;
            case GGUF_TYPE_UINT16: out.push_back(static_cast<const uint16_t *>(data)[i]); break;
            case GGUF_TYPE_INT16: out.push_back(static_cast<const int16_t *>(data)[i]); break;
            default: return {};
        }
    }
    return out;
}

AudioModelInfo read_audio_model_info(const GgufFile & f) {
    AudioModelInfo info;
    info.arch = f.str("general.architecture");
    info.name = f.str("general.name");
    info.version = f.str("general.version");
    info.license = f.str("general.license");
    info.task = f.str("audio.task");
    info.stems = f.strings("audio.stems");
    info.sample_rate = int(f.number("audio.sample_rate"));
    info.placeholder = f.flag("audio.placeholder");
    return info;
}

static const char * type_name(enum ggml_backend_dev_type t) {
    switch (t) {
        case GGML_BACKEND_DEVICE_TYPE_CPU: return "cpu";
        case GGML_BACKEND_DEVICE_TYPE_GPU: return "gpu";
        case GGML_BACKEND_DEVICE_TYPE_IGPU: return "igpu";
        case GGML_BACKEND_DEVICE_TYPE_ACCEL: return "accel";
        default: return "other";
    }
}

std::vector<DeviceInfo> list_devices() {
    std::vector<DeviceInfo> out;
    for (size_t i = 0; i < ggml_backend_dev_count(); i++) {
        ggml_backend_dev_t dev = ggml_backend_dev_get(i);
        enum ggml_backend_dev_type t = ggml_backend_dev_type(dev);
        if (t == GGML_BACKEND_DEVICE_TYPE_ACCEL || t == GGML_BACKEND_DEVICE_TYPE_META) continue;
        out.push_back({ggml_backend_dev_name(dev), ggml_backend_dev_description(dev), type_name(t)});
    }
    return out;
}

ggml_backend_t init_backend(const std::string & device, int threads, std::string * chosen) {
    ggml_backend_t backend = nullptr;
    if (device.empty() || device == "auto") {
        backend = ggml_backend_init_best();
    } else {
        ggml_backend_dev_t dev = ggml_backend_dev_by_name(device.c_str());
        if (!dev) {
            std::string names;
            for (const DeviceInfo & d : list_devices()) names += (names.empty() ? "" : ", ") + d.name;
            throw BadInput("unknown device '" + device + "' (available: " + names + ")");
        }
        backend = ggml_backend_dev_init(dev, nullptr);
    }
    if (!backend) throw BadInput("cannot initialise device '" + device + "'");
    if (threads > 0) {
        ggml_backend_dev_t dev = ggml_backend_get_device(backend);
        ggml_backend_reg_t reg = dev ? ggml_backend_dev_backend_reg(dev) : nullptr;
        auto set_threads = reg ? reinterpret_cast<ggml_backend_set_n_threads_t>(
                                     ggml_backend_reg_get_proc_address(reg, "ggml_backend_set_n_threads"))
                               : nullptr;
        if (set_threads) set_threads(backend, threads);
    }
    if (chosen) *chosen = ggml_backend_name(backend);
    return backend;
}

} // namespace backtrack
