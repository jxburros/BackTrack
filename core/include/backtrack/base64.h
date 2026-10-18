#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace backtrack {

// Standard alphabet with padding. decode accepts missing padding and skips
// ASCII whitespace; anything else outside the alphabet throws BadInput. A
// leading "data:...;base64," prefix is tolerated, though the Song Deck
// contracts say not to send one.
std::string base64_encode(const uint8_t * data, size_t size);
inline std::string base64_encode(const std::vector<uint8_t> & v) { return base64_encode(v.data(), v.size()); }
std::vector<uint8_t> base64_decode(const std::string & text);

} // namespace backtrack
