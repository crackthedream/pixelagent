# Changelog

## v0.1.0-alpha.1 · 2026-11-18

First packaged Windows x64 preview of the character and expression workflows.

- Create a character from a prompt or reference in Soft or Classic mode. Review the plan, choose a concept, request a local revision, and export the sprite.
- Generate expressions for a Soft character and export a shared atlas, JSON frame data and Godot SpriteFrames resource.
- GPT-6.2 Sol handles planning and Classic drawing. GPT-6.2 Astra is available for pairwise comparisons. Paid calls require a configured pricing profile.
- The Windows bundle includes Python and the drawing engine. Fresh installs start in the free Test run mode.
- The application passed 245 offline regression tests. Earlier live tests covered both drawing workflows and an expression edit from the packaged app. The final ZIP passed three packaging tests and a fresh-profile free workflow through game pack export. See the [Windows verification report](docs/WINDOWS_VERIFICATION_2026-11-18.md) for the tested builds and remaining checks.
