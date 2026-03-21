---
title: "Privacy Policy"
---

*Last updated: March 21, 2026*

## What mycellm is

mycellm is open-source, peer-to-peer software for distributed LLM inference. When you use mycellm, your prompts are processed by GPU nodes operated by independent volunteers or your own hardware — not by a centralized service under our control.

## What we collect

### On mycellm.ai and mycellm.dev

- **Analytics**: We use Umami (self-hosted, privacy-focused) to collect anonymous page views. No cookies, no personal data, no tracking across sites.
- **Chat**: Prompts submitted through the public chat are processed by distributed GPU nodes. We do not store prompt or response content on our servers. Metadata (timestamp, model, token count, latency) may be retained for service monitoring.

### On your own node

When you run mycellm on your own hardware:

- **Local data**: All data (keys, certificates, credits, model configs) is stored locally on your machine. We have no access to it.
- **Telemetry** (opt-in): If you enable telemetry, anonymous usage counters (request totals, token counts, TPS, model names, uptime) are sent to the bootstrap node. No prompts, responses, IP addresses, or personal data are included. You can disable telemetry at any time via the dashboard Settings tab or `MYCELLM_TELEMETRY=false`.
- **Announce**: Your node announces its capabilities (hardware, models, node name) to the bootstrap for network coordination. This is required for network participation.

### On the public network

- **Prompts**: When you use the public chat or API gateway, your prompts are routed to volunteer-operated GPU nodes. These nodes process your prompt in memory and return a response. We instruct nodes not to log prompt content, but **we cannot enforce this on nodes we don't operate**. Do not send sensitive data (passwords, API keys, personal information) through the public network.
- **Rate limiting**: The public gateway tracks token usage per IP address for rate limiting. IP addresses are not stored permanently.

## What we don't collect

- We do not collect personal information, email addresses, or account credentials (there are no accounts).
- We do not sell, share, or monetize any data.
- We do not use cookies or third-party trackers.
- We do not store prompt or response content from the public chat.

## Your rights

- **Opt out of telemetry**: Disable via settings at any time.
- **Delete your data**: All node data is stored locally. Delete `~/.local/share/mycellm/` to remove everything.
- **Inspect the code**: mycellm is open source under Apache 2.0. Verify any claim by reading the source.

## Private and org networks

If you run a private mycellm network, you control all data handling policies. The privacy characteristics of private networks depend entirely on your configuration and the trust level of your members.

## Contact

For privacy questions: [michael@mycellm.ai](mailto:michael@mycellm.ai)
