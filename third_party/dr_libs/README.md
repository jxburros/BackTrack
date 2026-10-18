# dr_libs (vendored)

`dr_wav.h`, `dr_flac.h` and `dr_mp3.h` from [mackron/dr_libs], commit
`dfe8377631000664666519fdb83da193fd8037f4`, copied verbatim. Each is
available as public domain (Unlicense) or MIT No Attribution, at our choice;
`LICENSE` is the MIT-0 text from the headers.

They decode WAV (PCM 8–32 bit, IEEE float, A-law, µ-law, ADPCM), FLAC and
MP3 for `core/src/audio.cpp`. The implementations are compiled once, in that
file.

[mackron/dr_libs]: https://github.com/mackron/dr_libs
