// Shared HTTP layer for the BackTrack apps: Song Deck's bridge conventions
// (packages/ai/src/contracts.ts in Song Deck) implemented once, in the same
// shape as Song Deck's own Python bridges (bridges/songdeck_bridge/server.py).
//
// - JSON in and out; errors are {"error": "..."} with 400/401/403/404/409/503.
// - Bound to 127.0.0.1 by default; Host headers that are not loopback names
//   are refused while bound to loopback (DNS-rebinding protection).
// - Optional bearer token, checked in constant time, on everything except
//   GET /health and the page at GET /.
// - CORS only for the Song Deck origins (or --allow-origin), plus the page's
//   own origin.
// - Long jobs run one at a time with a small queue (409 beyond it); they stop
//   when the client disconnects or on POST /cancel, and report progress in
//   GET /health.
#pragma once

#include "backtrack/common.h"

#include <nlohmann/json.hpp>

#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace backtrack::http {

using Json = nlohmann::json;

struct HttpError : std::runtime_error {
    int status;
    HttpError(int s, const std::string & message) : std::runtime_error(message), status(s) {}
};

// Song Deck's studio dev server (5173) and local server (7788).
std::vector<std::string> default_cors_origins();

struct ServerOptions {
    std::string host = "127.0.0.1";
    int port = 0;
    std::string token;                         // empty: no auth
    std::vector<std::string> cors_origins = default_cors_origins();  // "*" allows any
    std::vector<std::string> allow_hosts;      // extra Host names accepted on loopback
    size_t max_body_bytes = size_t(512) << 20;
    int max_queue = 4;                         // jobs waiting behind the running one
    std::string name;                          // "BackTrack Surveil"
    std::string app;                           // "surveil"
    std::string role;                          // Song Deck bridge role: "transcription"
    std::string version;
    std::string page_html;                     // served at GET /
    bool quiet = false;                        // no per-request log lines
};

struct JobContext {
    std::string id;
    Progress progress;                         // reports into /health, cancels on disconnect or /cancel
};

class BridgeServer {
public:
    explicit BridgeServer(ServerOptions options);
    ~BridgeServer();

    // A quick GET endpoint.
    void get(const std::string & path, std::function<Json()> handler);

    // A long-running POST endpoint. prepare() validates the request body and
    // throws BadInput/NotFound/HttpError before the job is queued; the work
    // function it returns runs in the job slot.
    using Work = std::function<Json(JobContext &)>;
    void job(const std::string & path, std::function<Work(const Json & body)> prepare);

    // While loading, /health says "loading" and jobs answer 503.
    void set_loading(bool loading);

    bool listen();                             // blocks until stop()
    void stop();
    int bound_port() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Helpers for prepare() functions: typed access to optional request fields,
// with 400s that name the field.
const Json * field(const Json & body, const char * name);       // nullptr when absent or null
std::string req_string(const Json & body, const char * name, bool required = false, const std::string & fallback = "");
bool req_bool(const Json & body, const char * name, bool fallback = false);
double req_number(const Json & body, const char * name, double fallback);

} // namespace backtrack::http
