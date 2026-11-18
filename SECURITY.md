# Security policy

## Reporting a vulnerability

Do not publish exploit details, API keys, private prompts, or reference images in a public issue or pull request.

If this repository's **Security** tab offers **Report a vulnerability**, use that private reporting flow. If the option is unavailable, open an issue that only asks the maintainers for a private reporting channel; withhold vulnerability details until a private channel has been established. This project does not publish a separate security email address or a guaranteed response deadline.

Once a private channel is available, include:

- The affected version or commit, Windows version, and whether you used source or a packaged build.
- A minimal reproduction using synthetic data, expected behavior, and observed impact.
- Relevant file paths or routes, and redacted logs if needed.
- Any suggested mitigation. Do not include active credentials or other people's data.

This is a development-stage project. Report issues against the latest available source or release where possible; no long-term support schedule for older versions is promised.

## Security boundaries

PixelAgent's desktop UI uses a loopback server. Do not expose that server through a public bind address, proxy, or port-forwarding service. It is not designed as an authenticated, multi-user hosted service.

OpenAI mode sends prompts and selected images to the configured API service. A local UI does not mean that generation is offline. Use Test run for workflows that must avoid model requests.

The app reads API credentials from the Windows user environment or process environment. Keep credentials out of repository files, project folders, screenshots, and reports. If a credential was exposed, revoke or rotate it through the provider; deleting a file or commit does not revoke it.

The drawing agent and engine run with the application's local permissions. They are not a security sandbox for untrusted code. Use only engine code you trust, keep the engine lock intact when building, and review imported files and model/tool integrations accordingly.

Local cost estimates and spending checks are not hard provider-side spending limits. Check actual provider usage separately. Missing usage or pricing information should never be interpreted as proof that a request was free.

