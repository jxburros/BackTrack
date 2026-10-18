#include "backtrack/base64.h"

#include "backtrack/common.h"

namespace backtrack {

static const char kAlphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

std::string base64_encode(const uint8_t * data, size_t size) {
    std::string out;
    out.reserve((size + 2) / 3 * 4);
    size_t i = 0;
    for (; i + 2 < size; i += 3) {
        uint32_t v = (uint32_t(data[i]) << 16) | (uint32_t(data[i + 1]) << 8) | data[i + 2];
        out += kAlphabet[(v >> 18) & 63];
        out += kAlphabet[(v >> 12) & 63];
        out += kAlphabet[(v >> 6) & 63];
        out += kAlphabet[v & 63];
    }
    if (i < size) {
        uint32_t v = uint32_t(data[i]) << 16;
        if (i + 1 < size) v |= uint32_t(data[i + 1]) << 8;
        out += kAlphabet[(v >> 18) & 63];
        out += kAlphabet[(v >> 12) & 63];
        out += i + 1 < size ? kAlphabet[(v >> 6) & 63] : '=';
        out += '=';
    }
    return out;
}

std::vector<uint8_t> base64_decode(const std::string & text) {
    int8_t table[256];
    for (int k = 0; k < 256; k++) table[k] = -1;
    for (int k = 0; k < 64; k++) table[uint8_t(kAlphabet[k])] = int8_t(k);
    table[uint8_t('-')] = 62;  // URL-safe alphabet too
    table[uint8_t('_')] = 63;

    size_t start = 0;
    if (text.compare(0, 5, "data:") == 0) {
        size_t comma = text.find(',');
        if (comma == std::string::npos) throw BadInput("invalid base64: data: URI without a comma");
        start = comma + 1;
    }

    std::vector<uint8_t> out;
    out.reserve((text.size() - start) / 4 * 3);
    uint32_t acc = 0;
    int bits = 0;
    bool padding = false;
    for (size_t i = start; i < text.size(); i++) {
        unsigned char c = uint8_t(text[i]);
        if (c == ' ' || c == '\n' || c == '\r' || c == '\t') continue;
        if (c == '=') { padding = true; continue; }
        if (padding) throw BadInput("invalid base64: data after padding");
        int8_t v = table[c];
        if (v < 0) throw BadInput("invalid base64 character");
        acc = (acc << 6) | uint32_t(v);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out.push_back(uint8_t((acc >> bits) & 0xff));
        }
    }
    return out;
}

} // namespace backtrack
