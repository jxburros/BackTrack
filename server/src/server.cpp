#include "backtrack/server.h"

#include "httplib.h"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <map>
#include <mutex>
#include <random>

namespace backtrack::http {

std::vector<std::string> default_cors_origins() {
    return {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:7788", "http://127.0.0.1:7788"};
}

namespace {

using Clock = std::chrono::steady_clock;

void replace_first(std::string & s, const std::string & from, const std::string & to) {
    size_t at = s.find(from);
    if (at != std::string::npos) s.replace(at, from.size(), to);
}

std::string lower(std::string s) {
    for (char & c : s) c = char(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

std::string strip_slash(std::string s) {
    while (!s.empty() && s.back() == '/') s.pop_back();
    return lower(s);
}

// Host header to bare host name: "127.0.0.1:8826" -> "127.0.0.1", "[::1]:8826" -> "::1".
std::string host_name(const std::string & header) {
    if (!header.empty() && header[0] == '[') {
        size_t end = header.find(']');
        return lower(header.substr(1, end == std::string::npos ? std::string::npos : end - 1));
    }
    size_t colon = header.find(':');
    return lower(header.substr(0, colon));
}

bool is_loopback(const std::string & host) {
    std::string h = lower(host);
    return h == "127.0.0.1" || h == "localhost" || h == "::1" || h.rfind("127.", 0) == 0;
}

bool constant_time_equal(const std::string & a, const std::string & b) {
    unsigned char diff = a.size() == b.size() ? 0 : 1;
    for (size_t i = 0; i < std::max(a.size(), b.size()); i++) {
        unsigned char x = i < a.size() ? uint8_t(a[i]) : 0, y = i < b.size() ? uint8_t(b[i]) : 0;
        diff |= x ^ y;
    }
    return diff == 0;
}

std::string random_id() {
    static std::mt19937_64 rng{std::random_device{}()};
    static std::mutex m;
    std::lock_guard<std::mutex> lock(m);
    char buf[17];
    std::snprintf(buf, sizeof buf, "%012llx", static_cast<unsigned long long>(rng() & 0xffffffffffffULL));
    return buf;
}

bool valid_job_id(const std::string & id) {
    if (id.empty() || id.size() > 64) return false;
    return std::all_of(id.begin(), id.end(), [](char c) { return std::isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_' || c == '.'; });
}

void send_json(httplib::Response & res, int status, const Json & body) {
    res.status = status;
    res.set_content(body.dump(), "application/json");
}

void send_error(httplib::Response & res, int status, const std::string & message) {
    send_json(res, status, Json{{"error", message}});
}

} // namespace

struct Job {
    std::string id, path;
    std::string state = "queued";              // queued | running
    std::string stage;
    double fraction = 0;
    Clock::time_point created = Clock::now();
    std::atomic<bool> cancelled{false};
    std::string reason;
};

struct BridgeServer::Impl {
    ServerOptions opt;
    httplib::Server svr;
    Clock::time_point started = Clock::now();
    std::mutex mu;
    std::condition_variable cv;
    std::map<std::string, std::shared_ptr<Job>> jobs;
    int running = 0, queued = 0;
    long completed = 0, failed = 0, cancelled = 0, rejected = 0;
    std::atomic<bool> loading{false};
    std::atomic<bool> stopping{false};
    std::vector<std::string> origins;
    std::map<std::string, std::string> page_types;   // "/path" -> Content-Type
    int port = 0;

    bool origin_allowed(const std::string & origin, const httplib::Request & req) const {
        std::string o = strip_slash(origin);
        for (const std::string & a : origins)
            if (a == "*" || a == o) return true;
        // The page this binary serves calls its own API from its own origin.
        std::string host = req.get_header_value("Host");
        return !host.empty() && (o == "http://" + lower(host) || o == "https://" + lower(host));
    }

    bool host_allowed(const std::string & header) const {
        if (!is_loopback(opt.host)) return true;   // bound to a public interface: the token protects it
        std::string name = host_name(header);
        if (name == "localhost" || name == "127.0.0.1" || name == "::1" || name == "localhost.localdomain") return true;
        for (const std::string & h : opt.allow_hosts)
            if (lower(h) == name) return true;
        return false;
    }

    bool authorized(const httplib::Request & req) const {
        if (opt.token.empty()) return true;
        std::string auth = req.get_header_value("Authorization");
        const std::string prefix = "Bearer ";
        if (auth.size() <= prefix.size() || lower(auth.substr(0, prefix.size())) != "bearer ") return false;
        return constant_time_equal(auth.substr(prefix.size()), opt.token);
    }

    Json health() {
        std::lock_guard<std::mutex> lock(mu);
        Json active = Json::array();
        auto now = Clock::now();
        for (auto & [id, j] : jobs) {
            active.push_back({{"id", j->id}, {"path", j->path}, {"state", j->state}, {"stage", j->stage},
                              {"progress", std::round(j->fraction * 1000) / 1000},
                              {"seconds", std::round(std::chrono::duration<double>(now - j->created).count() * 100) / 100}});
        }
        std::string status = stopping ? "shutting-down" : loading ? "loading" : "ok";
        return {{"status", status},
                {"name", opt.name},
                {"app", opt.app},
                {"version", opt.version},
                {"role", opt.role},
                {"uptime_seconds", std::round(std::chrono::duration<double>(now - started).count() * 10) / 10},
                {"auth_required", !opt.token.empty()},
                {"jobs", {{"running", running}, {"queued", queued}, {"max_jobs", 1}, {"max_queue", opt.max_queue},
                          {"completed", completed}, {"failed", failed}, {"cancelled", cancelled}, {"rejected", rejected}}},
                {"active_jobs", active}};
    }

    int cancel_jobs(const std::string & id, const std::string & reason) {
        std::lock_guard<std::mutex> lock(mu);
        int n = 0;
        for (auto & [jid, j] : jobs) {
            if (!id.empty() && jid != id) continue;
            if (!j->cancelled.exchange(true)) {
                j->reason = reason;
                n++;
            }
        }
        cv.notify_all();
        return n;
    }
};

BridgeServer::BridgeServer(ServerOptions options) : impl_(new Impl()) {
    Impl & s = *impl_;
    s.opt = std::move(options);
    for (const std::string & o : s.opt.cors_origins)
        if (!o.empty()) s.origins.push_back(strip_slash(o));

    s.svr.set_payload_max_length(s.opt.max_body_bytes);

    s.svr.set_pre_routing_handler([this](const httplib::Request & req, httplib::Response & res) {
        Impl & s = *impl_;
        if (!s.host_allowed(req.get_header_value("Host"))) {
            send_error(res, 403, "Host header not allowed (DNS-rebinding protection); start with --allow-host <name> if you use another host name");
            return httplib::Server::HandlerResponse::Handled;
        }
        std::string origin = req.get_header_value("Origin");
        if (!origin.empty()) {
            if (!s.origin_allowed(origin, req)) {
                send_error(res, 403, "origin " + origin + " is not allowed; start with --allow-origin " + origin + " to permit it");
                return httplib::Server::HandlerResponse::Handled;
            }
            res.set_header("Access-Control-Allow-Origin", origin);
            res.set_header("Vary", "Origin");
            res.set_header("Access-Control-Expose-Headers", "X-Job-Id, X-Model");
        }
        if (req.method == "OPTIONS") {
            res.status = 204;
            res.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
            res.set_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept, X-Job-Id");
            res.set_header("Access-Control-Max-Age", "600");
            if (lower(req.get_header_value("Access-Control-Request-Private-Network")) == "true")
                res.set_header("Access-Control-Allow-Private-Network", "true");
            return httplib::Server::HandlerResponse::Handled;
        }
        bool open = (req.method == "GET" || req.method == "HEAD") &&
                    (req.path == "/health" || req.path == "/" || req.path == "/favicon.ico" || s.page_types.count(req.path));
        if (!open && !s.authorized(req)) {
            res.set_header("WWW-Authenticate", "Bearer realm=\"backtrack\"");
            send_error(res, 401, "missing or invalid bearer token (Authorization: Bearer <token>)");
            return httplib::Server::HandlerResponse::Handled;
        }
        res.set_header("Cache-Control", "no-store");
        res.set_header("X-Content-Type-Options", "nosniff");
        return httplib::Server::HandlerResponse::Unhandled;
    });

    s.svr.set_error_handler([](const httplib::Request & req, httplib::Response & res) {
        if (!res.body.empty()) return;
        if (res.status == 404) send_error(res, 404, "unknown endpoint " + req.method + " " + req.path);
        else if (res.status == 413) send_error(res, 413, "the request body is too large (start with a larger --max-body-mb)");
        else send_error(res, res.status, httplib::status_message(res.status));
    });

    s.svr.set_exception_handler([](const httplib::Request &, httplib::Response & res, std::exception_ptr ep) {
        try {
            std::rethrow_exception(ep);
        } catch (const std::exception & e) {
            std::fprintf(stderr, "error: unhandled exception: %s\n", e.what());
        } catch (...) {
        }
        send_error(res, 500, "internal error; see the server log for details");
    });

    s.svr.set_logger([this](const httplib::Request & req, const httplib::Response & res) {
        if (impl_->opt.quiet || (req.path == "/health" && res.status == 200)) return;
        std::fprintf(stderr, "%s \"%s %s\" %d %zu B\n", req.remote_addr.c_str(), req.method.c_str(), req.path.c_str(),
                     res.status, res.body.size());
    });

    s.svr.Get("/health", [this](const httplib::Request &, httplib::Response & res) { send_json(res, 200, impl_->health()); });

    s.svr.Post("/cancel", [this](const httplib::Request & req, httplib::Response & res) {
        std::string id;
        if (!req.body.empty()) {
            Json body = Json::parse(req.body, nullptr, false);
            if (body.is_discarded() || !body.is_object()) return send_error(res, 400, "the request body is not a JSON object");
            if (body.contains("job_id") && body["job_id"].is_string()) id = body["job_id"].get<std::string>();
        }
        int n = impl_->cancel_jobs(id, "cancelled via POST /cancel");
        res.status = 204;
        res.set_header("X-Cancelled-Jobs", std::to_string(n));
    });

    for (PageFile & f : s.opt.page) {
        if (f.path == "index.html") {
            replace_first(f.body, "data-theme=\"xerox-riot\"", "data-theme=\"" + (s.opt.theme.empty() ? "xerox-riot" : s.opt.theme) + "\"");
            replace_first(f.body, "data-app=\"\"", "data-app=\"" + s.opt.app + "\"");
        }
        s.page_types["/" + f.path] = f.type;
    }
    for (const PageFile & f : s.opt.page) {
        auto serve = [&f](const httplib::Request &, httplib::Response & res) {
            if (f.path == "index.html")
                res.set_header("Content-Security-Policy",
                               "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'");
            res.set_content(f.body, f.type);
        };
        s.svr.Get("/" + f.path, serve);
        if (f.path == "index.html") s.svr.Get("/", serve);
    }
    s.svr.Get("/favicon.ico", [](const httplib::Request &, httplib::Response & res) { res.status = 204; });
}

BridgeServer::~BridgeServer() { stop(); }

void BridgeServer::get(const std::string & path, std::function<Json()> handler) {
    impl_->svr.Get(path, [handler](const httplib::Request &, httplib::Response & res) {
        try {
            send_json(res, 200, handler());
        } catch (const HttpError & e) {
            send_error(res, e.status, e.what());
        }
    });
}

void BridgeServer::job(const std::string & path, std::function<Work(const Json &)> prepare) {
    impl_->svr.Post(path, [this, path, prepare](const httplib::Request & req, httplib::Response & res) {
        Impl & s = *impl_;
        if (s.stopping) {
            res.set_header("Retry-After", "5");
            return send_error(res, 503, "the server is shutting down");
        }
        if (s.loading) {
            res.set_header("Retry-After", "5");
            return send_error(res, 503, "the model is still loading");
        }
        Json body = Json::parse(req.body, nullptr, false);
        if (body.is_discarded() || !body.is_object()) {
            std::lock_guard<std::mutex> lock(s.mu);
            s.rejected++;
            return send_error(res, 400, "the request body is not a JSON object");
        }

        Work work;
        try {
            work = prepare(body);
        } catch (const HttpError & e) {
            std::lock_guard<std::mutex> lock(s.mu);
            s.rejected++;
            return send_error(res, e.status, e.what());
        } catch (const BadInput & e) {
            std::lock_guard<std::mutex> lock(s.mu);
            s.rejected++;
            return send_error(res, 400, e.what());
        } catch (const NotFound & e) {
            std::lock_guard<std::mutex> lock(s.mu);
            s.rejected++;
            return send_error(res, 404, e.what());
        }

        auto job = std::make_shared<Job>();
        std::string requested = req.get_header_value("X-Job-Id");
        job->id = valid_job_id(requested) ? requested : random_id();
        job->path = path;
        {
            std::lock_guard<std::mutex> lock(s.mu);
            if (s.running >= 1 && s.queued >= s.opt.max_queue) {
                s.rejected++;
                return send_error(res, 409, "busy: " + std::to_string(s.running) + " job running, " + std::to_string(s.queued) +
                                                " queued (queue limit " + std::to_string(s.opt.max_queue) + "); retry later");
            }
            if (s.jobs.count(job->id)) job->id += "-" + random_id().substr(0, 4);
            s.jobs[job->id] = job;
            s.queued++;
        }
        res.set_header("X-Job-Id", job->id);

        auto gone = [&req, job]() { return job->cancelled.load() || req.is_connection_closed(); };
        auto finish = [&](long Impl::*counter) {
            std::lock_guard<std::mutex> lock(s.mu);
            (s.*counter)++;
            s.jobs.erase(job->id);
            s.cv.notify_all();
        };

        // Wait for the slot, still noticing cancellation and disconnects.
        {
            std::unique_lock<std::mutex> lock(s.mu);
            while (s.running >= 1 && !gone()) s.cv.wait_for(lock, std::chrono::milliseconds(100));
            s.queued--;
            if (gone()) {
                lock.unlock();
                finish(&Impl::cancelled);
                return send_error(res, 409, "job cancelled (" + (job->reason.empty() ? std::string("client disconnected") : job->reason) + ")");
            }
            s.running++;
            job->state = "running";
        }

        JobContext ctx;
        ctx.id = job->id;
        ctx.progress.cancelled = gone;
        ctx.progress.report = [&s, job](const char * stage, double fraction) {
            std::lock_guard<std::mutex> lock(s.mu);
            job->stage = stage;
            job->fraction = std::max(0.0, std::min(1.0, fraction));
        };

        auto release = [&]() {
            std::lock_guard<std::mutex> lock(s.mu);
            s.running--;
            s.cv.notify_all();
        };
        try {
            Json out = work(ctx);
            release();
            finish(&Impl::completed);
            send_json(res, 200, out);
        } catch (const Cancelled &) {
            release();
            finish(&Impl::cancelled);
            send_error(res, 409, "job cancelled (" + (job->reason.empty() ? std::string("client disconnected") : job->reason) + ")");
        } catch (const BadInput & e) {
            release();
            finish(&Impl::rejected);
            send_error(res, 400, e.what());
        } catch (const NotFound & e) {
            release();
            finish(&Impl::rejected);
            send_error(res, 404, e.what());
        } catch (const HttpError & e) {
            release();
            finish(e.status >= 500 ? &Impl::failed : &Impl::rejected);
            send_error(res, e.status, e.what());
        } catch (const std::exception & e) {
            release();
            finish(&Impl::failed);
            std::fprintf(stderr, "error: job %s failed: %s\n", job->id.c_str(), e.what());
            send_error(res, 500, std::string("job failed: ") + e.what());
        }
    });
}

void BridgeServer::set_loading(bool loading) { impl_->loading = loading; }

bool BridgeServer::listen() {
    Impl & s = *impl_;
    if (s.opt.port == 0) {
        s.port = s.svr.bind_to_any_port(s.opt.host);
        if (s.port <= 0) return false;
    } else {
        if (!s.svr.bind_to_port(s.opt.host, s.opt.port)) return false;
        s.port = s.opt.port;
    }
    return s.svr.listen_after_bind();
}

void BridgeServer::stop() {
    if (!impl_) return;
    impl_->stopping = true;
    impl_->cancel_jobs("", "the server is shutting down");
    impl_->svr.stop();
}

int BridgeServer::bound_port() const { return impl_->port; }

const Json * field(const Json & body, const char * name) {
    auto it = body.find(name);
    if (it == body.end() || it->is_null()) return nullptr;
    return &*it;
}

std::string req_string(const Json & body, const char * name, bool required, const std::string & fallback) {
    const Json * v = field(body, name);
    if (!v) {
        if (required) throw BadInput(std::string("'") + name + "' is required");
        return fallback;
    }
    if (!v->is_string()) throw BadInput(std::string("'") + name + "' must be a string");
    return v->get<std::string>();
}

bool req_bool(const Json & body, const char * name, bool fallback) {
    const Json * v = field(body, name);
    if (!v) return fallback;
    if (!v->is_boolean()) throw BadInput(std::string("'") + name + "' must be true or false");
    return v->get<bool>();
}

double req_number(const Json & body, const char * name, double fallback) {
    const Json * v = field(body, name);
    if (!v) return fallback;
    if (!v->is_number()) throw BadInput(std::string("'") + name + "' must be a number");
    double d = v->get<double>();
    if (!std::isfinite(d)) throw BadInput(std::string("'") + name + "' must be finite");
    return d;
}

} // namespace backtrack::http
