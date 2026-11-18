# PixelAgent Windows x64 preview verification · 18 November 2026

The application source passed 245 offline regression tests across four isolated test processes. Three additional packaging tests passed: license texts and distribution terms are included, stale notices are omitted, and mismatched executable/notice content is rejected. The publication build excludes unused optional audio libraries and includes a first-run guide, the application license, distribution terms, and 60 original third-party license/notice files covering 32 installed distributions plus Python and runtime notices. The bundled engine advertised 17 tools and exported a transparent PNG with the requested pixels.

The publication ZIP-extracted executable ran on the development PC under a fresh profile, with only Windows System32 on PATH and an empty working directory. The free workflow passed the three approval gates, PNG export, a local edit that preserved every pixel outside the selected area, two expressions, and game pack export (three frames, 108×68 atlas). Edge rendered the character library and Settings through the application's own local HTTP server, without mocked responses. All 16 model calls were free stand-ins with recorded cost $0. Test processes were stopped and the local endpoint no longer accepted connections. The executable is unsigned. An independent clean Windows machine, SmartScreen handling and a real Godot import remain unverified.

The earlier live run used an original text prompt. The Soft workflow generated the character, a smile and a game pack. GPT-6.2 Sol also drew the same character in Classic mode in 27 MCP tool rounds. GPT-6.2 Astra assessed the neutral and smiling faces in both orders. The earlier ZIP-extracted executable (`794dd339bb27560d9dbe8959875d640f2592ed6475bdb41f1126de5622074c21`) used the actual Images API to add a blink and re-exported the game pack. The exported clips are stand, face_happy, face_blink.

The publication rebuild changes packaging only: it removes unused sounddevice/PortAudio audio components. No application or engine runtime source was changed for this rebuild, and no paid request was repeated. Its free packaged workflow and bundled MCP engine were rechecked. Separately, installed OpenAI SDK imports and image/Responses client construction passed with audio imports and all network connections blocked; this is an offline compatibility check, not a live API test of the rebuilt binary.

## Estimated API cost

| Model | Recorded successful calls | Estimated USD |
| --- | ---: | ---: |
| gpt-6.1-terra | 5 | 0.01397 |
| gpt-6.2-astra | 2 | 0.01531 |
| gpt-6.2-sol | 29 | 0.21513 |
| gpt-image-2.6-sunburst | 4 | 0.33463 |
| **Total** | **40** | **0.57905** |

Figures use returned usage and the configured Standard rates. The OpenAI billing dashboard is the source for the final charge. The project log counts each event once. The free workflow used stand-ins.

Publication executable SHA-256: `c3f8204022c7b5f35a18b01a6271573d8febde5d08de439d84b4447fffb07c48`

Publication ZIP SHA-256: `d4c569771ad1896c15f6fcb6283a4f6b832a4e89f39475cd3a6a46e66eb0494e`

The ZIP is 39,519,824 bytes and contains 66 files. Its third-party manifest is tied to the executable hash. See [the build recipe](../packaging/BUILDING.md) for dependencies and notice collection.
