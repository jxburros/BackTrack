#pragma once

#ifndef BACKTRACK_VERSION
#define BACKTRACK_VERSION "0.0.0-dev"
#endif

namespace backtrack {
inline const char * version() { return BACKTRACK_VERSION; }
} // namespace backtrack
