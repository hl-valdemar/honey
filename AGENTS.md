style: before writing or reviewing `.hon` code, read [docs/style.md](docs/style.md).

## bootstrap compatibility (required)

- Each release MUST pin an already-published bootstrap compiler version and retain a known-good bootstrap binary for every supported platform.
- New syntax or semantics MUST follow a two-release rollout:
  1. Implement and release compiler support while keeping the compiler and its production dependencies buildable by the existing pinned bootstrap.
  2. Pin that published supporting release as the bootstrap before adopting the feature in compiler or production-dependency source. Implementation and self-adoption MUST NOT occur in the same release.
- Before publishing a release, MUST verify the pinned bootstrap builds stage 1, stage 1 builds stage 2, and stage 2 passes self-hosting acceptance checks. The bootstrap MUST be the pinned published compiler, rather than a fresh build of the release candidate.
