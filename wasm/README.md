# WebAssembly

Milestone **T4**: the transcriber built with Emscripten from the same `core/` and
`apps/surveil/` code, with a small JS wrapper for Song Deck's job worker. Not built yet.

M2 kept that path open: the core has no file-system or thread requirements of its own
(`decode_audio` works from memory, `Progress` reports and cancels per chunk), and ggml's CPU
backend builds for WebAssembly.
