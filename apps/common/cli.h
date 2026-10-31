// Command-line helpers shared by the BackTrack apps' main.cpp: argument parsing, numbers,
// writing files, and the Windows double-click behaviour (open the page, keep the window).
#pragma once

#include "backtrack/common.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#ifdef _WIN32
#include <io.h>
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shellapi.h>
#else
#include <unistd.h>
#endif

namespace backtrack::cli {

inline bool stderr_is_terminal() {
#ifdef _WIN32
    return _isatty(_fileno(stderr));
#else
    return isatty(fileno(stderr));
#endif
}

// True when Windows made a console just for us, as it does when the .exe is double-clicked:
// that window closes the moment we exit, so printing usage would only flash it.
inline bool started_by_double_click() {
#ifdef _WIN32
    DWORD ids[2];
    return GetConsoleProcessList(ids, 2) == 1;
#else
    return false;
#endif
}

inline void open_in_browser(const std::string & url) {
#ifdef _WIN32
    ShellExecuteA(nullptr, "open", url.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
#else
    (void)url;
#endif
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

// `flags` take no value; any other --option takes the next argument unless that is another
// option (then it is recorded as a flag, which the command reports as missing its value).
inline Args parse_args(int argc, char ** argv, int first, const std::vector<std::string> & flags) {
    static const std::map<std::string, std::string> kAlias = {{"-o", "--output"}};
    Args a;
    for (int i = first; i < argc; i++) {
        std::string s = argv[i];
        if (kAlias.count(s)) s = kAlias.at(s);
        if (s.rfind("--", 0) == 0 && s.size() > 2) {
            size_t eq = s.find('=');
            if (eq != std::string::npos) {
                a.opts[s.substr(0, eq)].push_back(s.substr(eq + 1));
            } else if (std::find(flags.begin(), flags.end(), s) != flags.end()) {
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

inline double to_number(const std::string & s, const std::string & what) {
    char * end = nullptr;
    double v = std::strtod(s.c_str(), &end);
    if (s.empty() || *end || !std::isfinite(v)) throw BadInput(what + " must be a number");
    return v;
}

inline void write_file(const std::string & path, const std::vector<uint8_t> & bytes) {
    std::ofstream f(path, std::ios::binary);
    if (!f) throw BadInput("cannot write " + path);
    f.write(reinterpret_cast<const char *>(bytes.data()), std::streamsize(bytes.size()));
    if (!f) throw BadInput("cannot write " + path);
}

} // namespace backtrack::cli
