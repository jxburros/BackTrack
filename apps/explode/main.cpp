// backtrack-explode: the BackTrack stem separator's command line and server.
#include "explode.h"

#include "cli.h"

#include "backtrack/base64.h"
#include "backtrack/server.h"
#include "backtrack/version.h"
#include "backtrack_page.h"

#include <algorithm>
#include <atomic>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <thread>

namespace fs = std::filesystem;
using backtrack::BadInput;
using backtrack::cli::Args;
using backtrack::cli::to_number;
using nlohmann::json;

namespace {

const char * kUsage = R"(BackTrack Explode - split a song into stems

usage:
  backtrack-explode INPUT [options]            separate a file (WAV, FLAC or MP3)
  backtrack-explode serve [options]            run the HTTP server and page (port 8827)
  backtrack-explode models [--json]            list models
  backtrack-explode --version | --help

separate:
  --stems A,B,...       stems to write (default: the model's, vocals,drums,bass,other);
                        "instrumental" (on request) or "other" holds the rest
  --augment             average two passes (plain, then polarity-flipped and channel-swapped)
  --passes N            1-4 passes, averaged: plain, flip + swap, flip, swap (default 1)
  --format F            pcm16 | pcm24 | float32 (default pcm24)
  -o, --output DIR      folder for the stems (default: INPUT's name with -stems)
  --json FILE           also write the bridge response, without the audio ("-" for stdout)
  --chunk SECONDS       chunk length (default 20); chunks overlap by 1 s

models (separate and serve):
  --model ID            model id from `models` (default: the first)

serve:
  --host H              bind address (default 127.0.0.1)
  --port N              port (default 8827)
  --api-key KEY         require "Authorization: Bearer KEY" (default: $BACKTRACK_API_KEY)
  --allow-origin URL    CORS origin, repeatable, replaces the Song Deck defaults; "*" = any
  --allow-host NAME     extra Host header name accepted while bound to loopback
  --max-body-mb N       largest request body (default 512)
  --max-queue N         jobs that may wait behind the running one (default 4)
  --quiet               no per-request log lines
  --watch-stdin         stop when standard input closes (for an app that starts the server)

Stems are 44.1 kHz stereo and sum to the input (as 44.1 kHz stereo).
)";

std::atomic<bool> g_interrupted{false};
backtrack::http::BridgeServer * g_server = nullptr;

void on_signal(int) {
    g_interrupted = true;
    if (g_server) g_server->stop();
}

const std::vector<std::string> kFlags = {"--augment", "--quiet", "--help", "--version", "--watch-stdin"};

backtrack::WavFormat parse_format(const std::string & f) {
    if (f == "pcm16" || f == "16") return backtrack::WavFormat::pcm16;
    if (f == "pcm24" || f == "24") return backtrack::WavFormat::pcm24;
    if (f == "float32" || f == "32f" || f == "float") return backtrack::WavFormat::float32;
    throw BadInput("unknown format '" + f + "' (pcm16, pcm24 or float32)");
}

int parse_passes(double v) {
    if (v != std::floor(v) || v < 1 || v > 4) throw BadInput("passes must be a whole number from 1 to 4");
    return int(v);
}

std::vector<std::string> split_list(const std::string & s) {
    std::vector<std::string> out;
    std::stringstream ss(s);
    std::string part;
    while (std::getline(ss, part, ','))
        if (!part.empty()) out.push_back(part);
    return out;
}

json model_json(const explode::ModelInfo & m) {
    return {{"id", m.id},           {"name", m.name},       {"stems", m.stems}, {"on_request_stems", m.on_request_stems},
            {"placeholder", m.placeholder}, {"license", m.license}, {"version", m.version}};
}

// The bridge response's fields other than the audio.
json result_meta(const explode::Result & r) {
    return {{"model", r.model},           {"passes", r.passes},         {"placeholder", r.placeholder},
            {"sample_rate", explode::kSampleRate}, {"seconds", r.seconds}, {"chunks", r.chunks}};
}

backtrack::Progress terminal_progress(int & last) {
    backtrack::Progress progress;
    progress.cancelled = [] { return g_interrupted.load(); };
    if (backtrack::cli::stderr_is_terminal()) progress.report = [&last](const char * stage, double f) {
        int pct = int(f * 100);
        if (pct == last) return;
        last = pct;
        std::fprintf(stderr, "\r%-24s %3d%%", stage, pct);
        std::fflush(stderr);
    };
    return progress;
}

int cmd_separate(const Args & a) {
    if (a.positional.size() != 1) throw BadInput("give exactly one input file (see --help)");
    const std::string input = a.positional[0];
    explode::Registry registry;
    const explode::Model & model = registry.get(a.get("--model"));
    explode::Options opt;
    opt.stems = split_list(a.get("--stems"));
    opt.passes = a.flag("--augment") ? explode::kAugmentPasses : 1;
    if (a.opts.count("--passes")) opt.passes = parse_passes(to_number(a.get("--passes"), "--passes"));
    if (a.opts.count("--chunk")) opt.chunk_seconds = to_number(a.get("--chunk"), "--chunk");
    const backtrack::WavFormat format = parse_format(a.get("--format", "pcm24"));
    explode::plan_stems(model.info(), opt.stems);  // a bad --stems fails before the audio is read

    int last = -1;
    backtrack::Progress progress = terminal_progress(last);
    backtrack::Audio audio = backtrack::load_audio_file(input);
    explode::Result r = explode::separate(audio, model, opt, progress);
    if (backtrack::cli::stderr_is_terminal()) std::fprintf(stderr, "\r%-32s\r", "");

    fs::path dir = a.get("--output");
    if (dir.empty()) dir = fs::path(input).parent_path() / (fs::path(input).stem().string() + "-stems");
    std::error_code ec;
    fs::create_directories(dir, ec);
    if (ec) throw BadInput("cannot create " + dir.string() + ": " + ec.message());
    for (size_t i = 0; i < r.names.size(); i++)
        backtrack::cli::write_file((dir / (r.names[i] + ".wav")).string(), backtrack::encode_wav(r.stems[i], format));

    const std::string json_out = a.get("--json");
    if (!json_out.empty()) {
        json meta = result_meta(r);
        meta["stems"] = r.names;
        const std::string body = meta.dump(2);
        if (json_out == "-") std::cout << body << "\n";
        else backtrack::cli::write_file(json_out, std::vector<uint8_t>(body.begin(), body.end()));
    }
    std::string names;
    for (const auto & n : r.names) names += (names.empty() ? "" : ", ") + n;
    std::fprintf(stderr, "%zu stems (%s) from %.1f s in %d chunks, %d pass%s (model %s%s)\nwrote %s\n", r.names.size(), names.c_str(),
                 r.seconds, r.chunks, r.passes, r.passes == 1 ? "" : "es", r.model.c_str(), r.placeholder ? ", placeholder" : "",
                 dir.string().c_str());
    return 0;
}

int cmd_models(const Args & a) {
    explode::Registry registry;
    if (a.flag("--json")) {
        json list = json::array();
        for (const auto & m : registry.models()) list.push_back(model_json(m->info()));
        std::cout << list.dump(2) << "\n";
        return 0;
    }
    for (const auto & m : registry.models()) {
        const auto & i = m->info();
        std::string stems;
        for (const auto & s : i.stems) stems += (stems.empty() ? "" : ",") + s;
        for (const auto & s : i.on_request_stems) stems += ",(" + s + ")";
        std::printf("%-14s %-40s built in%s\n", i.id.c_str(), stems.c_str(), i.placeholder ? " (placeholder)" : "");
    }
    return 0;
}

int cmd_serve(const Args & a, bool open_page = false) {
    auto registry = std::make_shared<explode::Registry>();
    const std::string default_model = a.get("--model");
    registry->get(default_model);  // fail fast on a bad --model

    backtrack::http::ServerOptions so;
    so.host = a.get("--host", "127.0.0.1");
    so.port = int(to_number(a.get("--port", std::to_string(explode::kDefaultPort)), "--port"));
    so.token = a.get("--api-key");
    if (so.token.empty())
        if (const char * env = std::getenv("BACKTRACK_API_KEY")) so.token = env;
    if (a.opts.count("--allow-origin")) so.cors_origins = a.all("--allow-origin");
    so.allow_hosts = a.all("--allow-host");
    if (a.opts.count("--max-body-mb")) so.max_body_bytes = size_t(to_number(a.get("--max-body-mb"), "--max-body-mb")) << 20;
    if (a.opts.count("--max-queue")) so.max_queue = int(to_number(a.get("--max-queue"), "--max-queue"));
    so.name = explode::kAppName;
    so.app = "explode";
    so.role = "separation";
    so.version = backtrack::version();
    for (const backtrack::EmbeddedFile & f : backtrack::kPageFiles)
        so.page.push_back({f.path, f.type, std::string(reinterpret_cast<const char *>(f.data), f.size)});
    so.theme = "blast-zone";
    so.quiet = a.flag("--quiet");
    const bool watch_stdin = a.flag("--watch-stdin");
    so.on_listening = [open_page, watch_stdin](int port) {
        if (open_page) backtrack::cli::open_in_browser("http://127.0.0.1:" + std::to_string(port) + "/");
        if (watch_stdin)
            std::thread([] {
                while (std::fgetc(stdin) != EOF) {}
                on_signal(0);
            }).detach();
    };
    bool loopback = so.host == "127.0.0.1" || so.host == "localhost" || so.host == "::1";
    if (!loopback && so.token.empty())
        std::fprintf(stderr, "warning: listening on %s without --api-key; anyone who can reach it can use it\n", so.host.c_str());

    backtrack::http::BridgeServer server(so);

    server.get("/info", [registry, default_model]() {
        json models = json::array();
        for (const auto & m : registry->models()) models.push_back(model_json(m->info()));
        const explode::ModelInfo & def = registry->get(default_model).info();
        return json{{"name", explode::kAppName},
                    {"app", "explode"},
                    {"version", backtrack::version()},
                    {"role", "separation"},
                    {"models", models},
                    {"model", def.id},
                    {"stems", def.stems},
                    {"on_request_stems", def.on_request_stems},
                    {"sample_rate", explode::kSampleRate},
                    {"device", "CPU"},
                    {"augment", true},
                    {"max_passes", 4},
                    {"formats", {"pcm16", "pcm24", "float32"}},
                    {"capabilities", {"SOURCE_SEPARATION", "VOCAL_ISOLATION", "STEM_OUTPUT"}}};
    });

    server.job("/separate", [registry, default_model](const json & body) -> backtrack::http::BridgeServer::Work {
        using namespace backtrack::http;
        std::string audio_b64 = req_string(body, "audio_base64", true);
        const explode::Model & model = registry->get(req_string(body, "model", false, default_model));
        explode::Options opt;
        if (const json * s = field(body, "stems")) {
            if (!s->is_array()) throw BadInput("'stems' must be an array of stem names");
            for (const json & v : *s) {
                if (!v.is_string()) throw BadInput("'stems' must be an array of stem names");
                opt.stems.push_back(v.get<std::string>());
            }
        }
        opt.passes = req_bool(body, "augment", false) ? explode::kAugmentPasses : 1;
        if (field(body, "passes")) opt.passes = parse_passes(req_number(body, "passes", 1));
        const backtrack::WavFormat format = parse_format(req_string(body, "format", false, "pcm16"));
        explode::plan_stems(model.info(), opt.stems);  // 400 before queueing
        std::vector<uint8_t> bytes = backtrack::base64_decode(audio_b64);
        if (bytes.empty()) throw BadInput("'audio_base64' is empty");
        return [&model, opt, format, bytes = std::move(bytes)](JobContext & job) {
            job.progress.step("decode", 0.0);
            backtrack::Audio audio = backtrack::decode_audio(bytes.data(), bytes.size());
            explode::Result r = explode::separate(audio, model, opt, job.progress);
            json stems = json::object();
            for (size_t i = 0; i < r.names.size(); i++) {
                job.progress.step("encode", 0.97 + 0.03 * double(i) / double(r.names.size()));
                stems[r.names[i]] = backtrack::base64_encode(backtrack::encode_wav(r.stems[i], format));
            }
            json out = result_meta(r);
            out["stems"] = std::move(stems);
            return out;
        };
    });

    g_server = &server;
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);
    std::fprintf(stderr, "%s %s on http://%s:%d (%zu models)%s\n", explode::kAppName, backtrack::version(),
                 so.host.find(':') != std::string::npos ? ("[" + so.host + "]").c_str() : so.host.c_str(), so.port,
                 registry->models().size(), so.token.empty() ? "" : ", API key required");
    bool ok = server.listen();
    g_server = nullptr;
    if (!ok && !g_interrupted) {
        std::fprintf(stderr, "error: cannot listen on %s:%d\n", so.host.c_str(), so.port);
        return 1;
    }
    return 0;
}

} // namespace

int run(int argc, char ** argv, bool double_click) {
    try {
        if (double_click) {
            std::fprintf(stderr, "Opening the Explode page in your browser. Keep this window open while you use it;\n"
                                 "close it to stop. For the command line, run backtrack-explode --help in a terminal.\n\n");
            return cmd_serve(Args{}, true);
        }
        std::string cmd = argc > 1 ? argv[1] : "";
        if (cmd.empty() || cmd == "--help" || cmd == "-h" || cmd == "help") {
            std::fputs(kUsage, stdout);
            return cmd.empty() ? 1 : 0;
        }
        if (cmd == "--version") {
            std::printf("backtrack-explode %s\n", backtrack::version());
            return 0;
        }
        const bool sub = cmd == "serve" || cmd == "models" || cmd == "separate";
        Args a = backtrack::cli::parse_args(argc, argv, sub ? 2 : 1, kFlags);
        if (a.flag("--help")) {
            std::fputs(kUsage, stdout);
            return 0;
        }
        for (const std::string & f : a.flags) {
            bool known = std::find(kFlags.begin(), kFlags.end(), f) != kFlags.end() || (cmd == "models" && f == "--json");
            if (!known) throw BadInput(f + " needs a value");
        }
        if (cmd == "serve") return cmd_serve(a);
        if (cmd == "models") return cmd_models(a);
        std::signal(SIGINT, on_signal);
        return cmd_separate(a);
    } catch (const backtrack::Cancelled &) {
        std::fprintf(stderr, "\ncancelled\n");
        return 130;
    } catch (const std::exception & e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 1;
    }
}

int main(int argc, char ** argv) {
    const bool double_click = argc == 1 && backtrack::cli::started_by_double_click();
    int rc = run(argc, argv, double_click);
    if (double_click && rc != 0) {
        std::fputs("\npress Enter to close this window\n", stderr);
        std::getchar();
    }
    return rc;
}
