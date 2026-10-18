# Training

Python (PyTorch) training code for both apps' models, exporting GGUF. Milestone **T1**
(transcriber) and **S1** (separator data); nothing is here yet. See
[docs/plans/surveil.md](../docs/plans/surveil.md#training-code-training) and
[docs/plans/explode.md](../docs/plans/explode.md#training-code-training-shared-with-the-transcriber).

What M2 fixed for it to match:

- the front end, step by step: [docs/front-end.md](../docs/front-end.md);
- the model graph, tensor names and shapes, and metadata keys:
  [docs/gguf-metadata.md](../docs/gguf-metadata.md);
- a reference file to export against: `backtrack-surveil make-placeholder -o ref.gguf`.

The parity check (same clip, same likelihoods in PyTorch and C++) can call
`backtrack-surveil CLIP --model exported.gguf --json -` once the C++ side exposes raw
likelihoods; adding that output is part of T1.
