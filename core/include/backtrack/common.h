// Shared error types, progress reporting and cancellation for the BackTrack
// apps. Everything long-running in core/ and the apps takes a Progress and
// calls check() between chunks, so the CLI, the HTTP server and (later) the
// WebAssembly wrapper can all report progress and stop work the same way.
#pragma once

#include <functional>
#include <stdexcept>
#include <string>

namespace backtrack {

// Bad input from the caller (unreadable audio, an invalid option). The HTTP
// layer answers 400.
struct BadInput : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// Something asked for does not exist (an unknown model id). HTTP 404.
struct NotFound : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// The job was cancelled (POST /cancel, client disconnect, Ctrl-C).
struct Cancelled : std::runtime_error {
    Cancelled() : std::runtime_error("cancelled") {}
};

struct Progress {
    // stage is a short label ("decode", "front end", "model", "notes");
    // fraction is 0..1 within the whole job.
    std::function<void(const char * stage, double fraction)> report;
    std::function<bool()> cancelled;

    void set(const char * stage, double fraction) const {
        if (report) report(stage, fraction);
    }
    void check() const {
        if (cancelled && cancelled()) throw Cancelled();
    }
    // Report and check in one call, the usual thing to do per chunk.
    void step(const char * stage, double fraction) const {
        check();
        set(stage, fraction);
    }
};

} // namespace backtrack
