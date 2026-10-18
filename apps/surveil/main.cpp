// backtrack-surveil: the BackTrack transcriber's command line and server.
#include "surveil.h"

#include "backtrack/base64.h"
#include "backtrack/ggml_util.h"
#include "backtrack/server.h"
#include "backtrack/version.h"
#include "backtrack_page.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <sstream>

#ifdef _WIN32
#include <io.h>
#else
#include <unistd.h>
#endif

namespace fs = std::filesystem;
using backtrack::BadInput;
using nlohmann::json;

namespace {

const char * kUsage = R"(BackTrack Surveil - audio to MIDI

usage:
  backtrack-surveil INPUT [options]            transcribe a file (WAV, FLAC or MP3)
  backtrack-surveil serve [options]            run the HTTP server and page (port 8826)
  backtrack-surveil models [--json]            list models
  backtrack-surveil devices                    list compute devices
  backtrack-surveil make-placeholder -o FILE   write a placeholder model as GGUF
  backtrack-surveil --version | --help

transcribe:
  --stem S              mix | vocals | bass | drums | piano | guitar | melody | other (default mix)
  --key K               song key, e.g. "A minor", "Eb" (lowers out-of-key pitches)
  --tempo BPM           pulls note starts toward the 16th-note grid; also the MIDI tempo
  --chords FILE         JSON [{"symbol": "Am", "start": 0, "end": 2}, ...]
  --program N           General MIDI program (0-127) to report
  --pitch-bends         include pitch curves (JSON "bend", MIDI pitch wheel)
  -o, --output FILE     MIDI file (default: INPUT with .mid; "-" for none)
  --json FILE           also write the bridge response JSON ("-" for stdout)
  --onset-threshold X   0..1 (default 0.5)
  --frame-threshold X   0..1 (default 0.5)

models and devices (transcribe and serve):
  --model ID|FILE       model id from `models`, a stem name, or a .gguf file
  --models-dir DIR      look for .gguf models here (repeatable; also $BACKTRACK_MODELS,
                        ./models and models/ next to the binary)
  --device NAME         compute device from `devices` (default: best available)
  --threads N           CPU threads

serve:
  --host H              bind address (default 127.0.0.1)
  --port N              port (default 8826)
  --api-key KEY         require "Authorization: Bearer KEY" (default: $BACKTRACK_API_KEY)
  --allow-origin URL    CORS origin, repeatable, replaces the Song Deck defaults; "*" = any
  --allow-host NAME     extra Host header name accepted while bound to loopback
  --max-body-mb N       largest request body (default 512)
  --max-queue N         jobs that may wait behind the running one (default 4)
  --quiet               no per-request log lines

make-placeholder:
  --kind pitched|drums  (default pitched)
  --init wired|random   wired: hand-set weights that follow the audio (default);
                        random: seeded random weights
  --seed N              for --init random
)";

bool stderr_is_terminal() {
#ifdef _WIN32
    return _isatty(_fileno(stderr));
#else
    return isatty(fileno(stderr));
#endif
}

std::atomic<bool> g_interrupted{false};
backtrack::http::BridgeServer * g_server = nullptr;

void on_signal(int) {
    g_interrupted = true;
    if (g_server) g_server->stop();
}

struct Args {
    std::vector<std::string> positional;
    std::map<std::string, std::vector<std::string>> opts;
    std::vector<std::string> flags;

    bool flag(const std::string & f) const { return std::find(flags.begin(), flags.end(), f) != flags.end(); }
    std::string get(const std::string & k, const std::string & fallback = "") const {
        auto it = opts.find(k);
        return it == opts.end() ? fallback : it->second.back();
    }
    std::vector<std::string> all(const std::string & k) const {
        auto it = opts.find(k);
        return it == opts.end() ? std::vector<std::string>{} : it->second;
    }
};

Args parse_args(int argc, char ** argv, int first) {
    static const std::vector<std::string> kFlags = {"--pitch-bends", "--quiet", "--help", "-h", "--version"};
    static const std::map<std::string, std::string> kAlias = {{"-o", "--output"}};
    Args a;
    for (int i = first; i < argc; i++) {
        std::string s = argv[i];
        if (kAlias.count(s)) s = kAlias.at(s);
        if (s.rfind("--", 0) == 0 && s.size() > 2) {
            size_t eq = s.find('=');
            if (eq != std::string::npos) {
                a.opts[s.substr(0, eq)].push_back(s.substr(eq + 1));
            } else if (std::find(kFlags.begin(), kFlags.end(), s) != kFlags.end()) {
                a.flags.push_back(s);
            } else if (i + 1 >= argc || std::string(argv[i + 1]).rfind("--", 0) == 0) {
                a.flags.push_back(s);  // no value: a flag (`models --json`), or an error the command reports
            } else {
                a.opts[s].push_back(argv[++i]);
            }
        } else if (s == "-h") {
            a.flags.push_back("--help");
        } else {
            a.positional.push_back(s);
        }
    }
    return a;
}

double to_number(const std::string & s, const std::string & what) {
    char * end = nullptr;
    double v = std::strtod(s.c_str(), &end);
    if (s.empty() || *end || !std::isfinite(v)) throw BadInput(what + " must be a number");
    return v;
}

std::vector<std::string> model_dirs(const Args & a, const char * argv0) {
    std::vector<std::string> dirs = a.all("--models-dir");
    if (const char * env = std::getenv("BACKTRACK_MODELS")) {
        std::stringstream ss(env);
        std::string part;
#ifdef _WIN32
        const char sep = ';';
#else
        const char sep = ':';
#endif
        while (std::getline(ss, part, sep)) dirs.push_back(part);
    }
    dirs.push_back("models");
    std::error_code ec;
    fs::path exe = fs::weakly_canonical(fs::path(argv0), ec);
    if (!ec && exe.has_parent_path()) dirs.push_back((exe.parent_path() / "models").string());
    return dirs;
}

// --model may name a file; register it and use its id.
std::unique_ptr<surveil::Registry> make_registry(const Args & a, const char * argv0, std::string & requested) {
    std::vector<std::string> files;
    requested = a.get("--model");
    if (!requested.empty() && fs::path(requested).extension() == ".gguf") {
        if (!fs::exists(requested)) throw BadInput("model file not found: " + requested);
        files.push_back(requested);
        requested = fs::path(requested).stem().string();
    }
    int threads = a.opts.count("--threads") ? int(to_number(a.get("--threads"), "--threads")) : 0;
    return std::make_unique<surveil::Registry>(a.get("--device"), threads, model_dirs(a, argv0), files);
}

void write_file(const std::string & path, const std::vector<uint8_t> & bytes) {
    std::ofstream f(path, std::ios::binary);
    if (!f) throw BadInput("cannot write " + path);
    f.write(reinterpret_cast<const char *>(bytes.data()), std::streamsize(bytes.size()));
    if (!f) throw BadInput("cannot write " + path);
}

surveil::PickOptions pick_options_from_cli(const Args & a) {
    surveil::PickOptions opt;
    opt.source = a.get("--stem", "mix");
    const auto & srcs = surveil::known_sources();
    if (std::find(srcs.begin(), srcs.end(), opt.source) == srcs.end()) throw BadInput("unknown --stem '" + opt.source + "'");
    opt.pitch_bends = a.flag("--pitch-bends");
    json ctx = json::object();
    if (a.opts.count("--key")) ctx["key"] = a.get("--key");
    if (a.opts.count("--tempo")) ctx["tempo"] = to_number(a.get("--tempo"), "--tempo");
    if (a.opts.count("--program")) ctx["program"] = int(to_number(a.get("--program"), "--program"));
    if (a.opts.count("--chords")) {
        std::ifstream f(a.get("--chords"));
        if (!f) throw BadInput("cannot open " + a.get("--chords"));
        json chords = json::parse(f, nullptr, false);
        if (chords.is_object() && chords.contains("chords")) chords = chords["chords"];
        if (chords.is_discarded()) throw BadInput(a.get("--chords") + " is not valid JSON");
        ctx["chords"] = chords;
    }
    opt.context = surveil::parse_context(ctx);
    if (a.opts.count("--onset-threshold")) opt.onset_threshold = to_number(a.get("--onset-threshold"), "--onset-threshold");
    if (a.opts.count("--frame-threshold")) opt.frame_threshold = to_number(a.get("--frame-threshold"), "--frame-threshold");
    return opt;
}

int cmd_transcribe(const Args & a, const char * argv0) {
    if (a.positional.size() != 1) throw BadInput("give exactly one input file (see --help)");
    const std::string input = a.positional[0];
    surveil::PickOptions opt = pick_options_from_cli(a);
    std::string requested;
    auto registry = make_registry(a, argv0, requested);
    std::string id = registry->choose(requested, opt.source);
    const surveil::Model & model = registry->get(id);

    backtrack::Progress progress;
    progress.cancelled = [] { return g_interrupted.load(); };
    int last = -1;
    if (stderr_is_terminal()) progress.report = [&last](const char * stage, double f) {
        int pct = int(f * 100);
        if (pct / 5 == last / 5) return;
        last = pct;
        std::fprintf(stderr, "\r%-10s %3d%%", stage, pct);
        std::fflush(stderr);
    };
    backtrack::Audio audio = backtrack::load_audio_file(input);
    surveil::Result r = surveil::transcribe(audio, model, opt, progress);
    if (stderr_is_terminal()) std::fprintf(stderr, "\r%-16s\r", "");

    std::string out = a.get("--output", fs::path(input).replace_extension(".mid").string());
    if (out != "-") write_file(out, surveil::result_midi(r, opt));
    std::string json_out = a.get("--json");
    std::string body = surveil::result_json(r, opt.pitch_bends).dump(2);
    if (json_out == "-") std::cout << body << "\n";
    else if (!json_out.empty()) write_file(json_out, std::vector<uint8_t>(body.begin(), body.end()));

    std::fprintf(stderr, "%zu notes from %.1f s of %s (model %s%s, device %s)", r.notes.size(), r.seconds, opt.source.c_str(),
                 r.model.c_str(), model.spec().placeholder ? ", placeholder" : "", registry->device_name().c_str());
    if (r.tempo) std::fprintf(stderr, ", tempo %.1f", *r.tempo);
    if (r.key) std::fprintf(stderr, ", key %s", r.key->c_str());
    std::fprintf(stderr, "\n");
    if (out != "-") std::fprintf(stderr, "wrote %s\n", out.c_str());
    return 0;
}

json model_json(const surveil::ModelEntry & e) {
    return {{"id", e.id}, {"name", e.name}, {"kind", e.kind}, {"stems", e.stems}, {"placeholder", e.placeholder},
            {"license", e.license}, {"version", e.version}};
}

int cmd_models(const Args & a, const char * argv0) {
    std::string requested;
    auto registry = make_registry(a, argv0, requested);
    if (a.flag("--json")) {
        json list = json::array();
        for (const auto & e : registry->entries()) list.push_back(model_json(e));
        std::cout << list.dump(2) << "\n";
        return 0;
    }
    for (const auto & e : registry->entries()) {
        std::string stems;
        for (const auto & s : e.stems) stems += (stems.empty() ? "" : ",") + s;
        std::printf("%-20s %-7s %-40s %s%s\n", e.id.c_str(), e.kind.c_str(), stems.c_str(),
                    e.path.empty() ? "built in" : e.path.c_str(), e.placeholder ? " (placeholder)" : "");
    }
    return 0;
}

int cmd_devices() {
    for (const auto & d : backtrack::list_devices()) std::printf("%-12s %-5s %s\n", d.name.c_str(), d.type.c_str(), d.description.c_str());
    return 0;
}

int cmd_make_placeholder(const Args & a) {
    std::string out = a.get("--output");
    if (out.empty()) throw BadInput("make-placeholder needs -o FILE.gguf");
    uint64_t seed = a.opts.count("--seed") ? uint64_t(to_number(a.get("--seed"), "--seed")) : 0;
    auto f = surveil::make_placeholder(a.get("--kind", "pitched"), a.get("--init", "wired"), seed);
    if (!f->save(out)) throw BadInput("cannot write " + out);
    std::fprintf(stderr, "wrote %s\n", out.c_str());
    return 0;
}

int cmd_serve(const Args & a, const char * argv0) {
    std::string requested;
    auto registry = make_registry(a, argv0, requested);
    const std::string default_model = requested;
    if (!default_model.empty()) registry->get(registry->choose(default_model, "mix"));  // fail fast on a bad --model

    backtrack::http::ServerOptions so;
    so.host = a.get("--host", "127.0.0.1");
    so.port = int(to_number(a.get("--port", std::to_string(surveil::kDefaultPort)), "--port"));
    so.token = a.get("--api-key");
    if (so.token.empty())
        if (const char * env = std::getenv("BACKTRACK_API_KEY")) so.token = env;
    if (a.opts.count("--allow-origin")) so.cors_origins = a.all("--allow-origin");
    so.allow_hosts = a.all("--allow-host");
    if (a.opts.count("--max-body-mb")) so.max_body_bytes = size_t(to_number(a.get("--max-body-mb"), "--max-body-mb")) << 20;
    if (a.opts.count("--max-queue")) so.max_queue = int(to_number(a.get("--max-queue"), "--max-queue"));
    so.name = surveil::kAppName;
    so.app = "surveil";
    so.role = "transcription";
    so.version = backtrack::version();
    so.page_html = backtrack::kPageHtml;
    so.quiet = a.flag("--quiet");
    bool loopback = so.host == "127.0.0.1" || so.host == "localhost" || so.host == "::1";
    if (!loopback && so.token.empty())
        std::fprintf(stderr, "warning: listening on %s without --api-key; anyone who can reach it can use it\n", so.host.c_str());

    backtrack::http::BridgeServer server(so);
    surveil::Registry & reg = *registry;

    server.get("/info", [&reg]() {
        json models = json::array();
        for (const auto & e : reg.entries()) models.push_back(model_json(e));
        return json{{"name", surveil::kAppName},
                    {"app", "surveil"},
                    {"version", backtrack::version()},
                    {"role", "transcription"},
                    {"models", models},
                    {"sources", surveil::known_sources()},
                    {"device", reg.device_name()},
                    {"pitch_bends", true},
                    {"context", {"key", "tempo", "chords", "program"}},
                    {"capabilities", {"AUDIO_TRANSCRIPTION", "AUDIO_TO_MIDI"}}};
    });

    server.job("/transcribe", [&reg, default_model](const json & body) -> backtrack::http::BridgeServer::Work {
        using namespace backtrack::http;
        std::string audio_b64 = req_string(body, "audio_base64", true);
        surveil::PickOptions opt;
        opt.source = req_string(body, "source", false, "mix");
        const auto & srcs = surveil::known_sources();
        if (std::find(srcs.begin(), srcs.end(), opt.source) == srcs.end())
            throw BadInput("unknown source '" + opt.source + "' (expected one of mix, vocals, bass, drums, piano, guitar, melody, other)");
        opt.pitch_bends = req_bool(body, "pitch_bends", false);
        if (const json * ctx = field(body, "context")) opt.context = surveil::parse_context(*ctx);
        bool include_midi = req_bool(body, "include_midi", false);
        std::string id = reg.choose(req_string(body, "model", false, default_model), opt.source);
        std::vector<uint8_t> bytes = backtrack::base64_decode(audio_b64);
        if (bytes.empty()) throw BadInput("'audio_base64' is empty");
        return [&reg, id, opt, include_midi, bytes = std::move(bytes)](JobContext & job) {
            job.progress.step("decode", 0.0);
            backtrack::Audio audio = backtrack::decode_audio(bytes.data(), bytes.size());
            const surveil::Model & model = reg.get(id);
            surveil::Result r = surveil::transcribe(audio, model, opt, job.progress);
            json out = surveil::result_json(r, opt.pitch_bends);
            if (include_midi) out["midi_base64"] = backtrack::base64_encode(surveil::result_midi(r, opt));
            return out;
        };
    });

    g_server = &server;
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);
    std::fprintf(stderr, "%s %s on http://%s:%d (device %s, %zu models)%s\n", surveil::kAppName, backtrack::version(),
                 so.host.find(':') != std::string::npos ? ("[" + so.host + "]").c_str() : so.host.c_str(), so.port,
                 reg.device_name().c_str(), reg.entries().size(), so.token.empty() ? "" : ", API key required");
    bool ok = server.listen();
    g_server = nullptr;
    if (!ok && !g_interrupted) {
        std::fprintf(stderr, "error: cannot listen on %s:%d\n", so.host.c_str(), so.port);
        return 1;
    }
    return 0;
}

} // namespace

int main(int argc, char ** argv) {
    try {
        std::string cmd = argc > 1 ? argv[1] : "";
        if (cmd.empty() || cmd == "--help" || cmd == "-h" || cmd == "help") {
            std::fputs(kUsage, stdout);
            return cmd.empty() ? 1 : 0;
        }
        if (cmd == "--version") {
            std::printf("backtrack-surveil %s\n", backtrack::version());
            return 0;
        }
        const bool sub = cmd == "serve" || cmd == "models" || cmd == "devices" || cmd == "make-placeholder" || cmd == "transcribe";
        Args a = parse_args(argc, argv, sub ? 2 : 1);
        if (a.flag("--help")) {
            std::fputs(kUsage, stdout);
            return 0;
        }
        for (const std::string & f : a.flags) {
            static const std::vector<std::string> kKnown = {"--pitch-bends", "--quiet", "--help", "--version"};
            bool known = std::find(kKnown.begin(), kKnown.end(), f) != kKnown.end() || (cmd == "models" && f == "--json");
            if (!known) throw BadInput(f + " needs a value");
        }
        if (cmd == "serve") return cmd_serve(a, argv[0]);
        if (cmd == "models") return cmd_models(a, argv[0]);
        if (cmd == "devices") return cmd_devices();
        if (cmd == "make-placeholder") return cmd_make_placeholder(a);
        std::signal(SIGINT, on_signal);
        return cmd_transcribe(a, argv[0]);
    } catch (const backtrack::Cancelled &) {
        std::fprintf(stderr, "\ncancelled\n");
        return 130;
    } catch (const std::exception & e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 1;
    }
}
