# Dashboard review — multi-model panel (2026-07-30)

Panel: **Sol** (OpenAI GPT-5.6 Sol — code), **Agy** (Gemini 3.1 Pro — UI/UX),
**Fable** (Claude Fable 5 — synthesis + release alignment). Scope: `web/`
(Vite + React 19 + TS + Tailwind, zustand + TanStack Query, i18next; 8.5k LoC).
Full transcripts: `/tmp/review-sol.md`, `/tmp/review-agy.md` (session host).

## P0 — fix before the next official release

1. **`api.remote()` leaks the local admin key to remote nodes** (`api/client.ts`).
   It attaches the local bearer token to arbitrary `nodeAddr` origins (plus
   HTTPS→HTTP mixed-content breakage). Fix: same-origin proxy
   (`/v1/remote/{peer_id}/…`) served by the local daemon; never forward the
   local token cross-origin. [Sol #1; Fable concurs independently]
2. **SSE puts the API key in the URL** (`api/client.ts stream()` —
   `?api_key=…`). Keys land in proxy/access logs and browser history. Fix:
   cookie-auth for same-origin SSE or a fetch-based SSE client with an
   Authorization header; redact stream URLs from telemetry. [Sol #2; Fable]
3. **ChatTab bypasses the authenticated client** — raw `fetch` without
   Authorization breaks chat on auth-enabled nodes; cancellation doesn't abort
   in-flight requests or the retry backoff timer. [Sol #3]
4. **ModelTable destructive-action bugs** — retry after failed load can send
   `model_path: undefined`; Cancel deletes the saved config without actually
   cancelling the backend load; retries hard-code `backend: 'llama.cpp'`.
   Needs a real cancel-load endpoint + preserved load params. [Sol #4]

## P1 — architecture

5. **Single owner for server state**: node/credits/models/fleet data currently
   lives in both TanStack Query and zustand mirrors with different lifecycles
   (stale data after logout/node switch). Query owns server state; zustand
   keeps auth/tab/chat-draft/theme only; `queryClient.clear()` on logout. [Sol #5]
6. **Replace ModelTable's `setInterval` polling** with `useQuery`
   (`refetchInterval`, device-scoped keys, abort signals, invalidation after
   mutations) — removes overlap races, stale-device writes, and the
   `JSON.stringify` diff workaround. [Sol #6]
7. **API client contract**: HeadersInit merging is unsound for `Headers`
   instances; unconditional JSON content-type; add typed error model. [Sol #7]

## P1 — UI/UX (Agy top items)

8. **Chat auto-scroll hijack**: streaming yanks the user to the bottom on every
   token; only auto-scroll when already near the bottom. (`ChatTab.tsx`)
9. **Native `window.confirm`** for delete/remove blocks the main thread and
   breaks the console aesthetic — custom ConfirmModal. (`ModelTable.tsx`)
10. **Mobile tables lose critical columns** (`hidden md:table-cell` drops
    Size/RAM/Quant) — stacked card layout on small screens. (`ModelTable`,
    `FleetGrid`)
11. **A11y**: sortable `<th onClick>` headers aren't keyboard-reachable (wrap
    in `<button>`); icon-only buttons need `aria-label` (title alone reads as
    "button, blank"); offline nodes at `opacity-50` on black fall below WCAG
    AA contrast.
12. **Chat polish**: retry drops the user's prompt on second failure (keep the
    message, remove only the error); hardcoded English strings in empty states
    bypass i18next; row re-sort jumps under 2s polling (auto-animate).

## Release alignment (Fable — new backend features the dashboard doesn't surface yet)

13. Show the **`verified` badge** on downloads (`/downloads` now reports
    sha256/git-sha1/unverified per file).
14. Show **effective `context_length`** (post-preflight-clamp) on model rows —
    the API now exposes it on `/v1/models`.
15. **Model edit/load panel**: new per-model options `max_kv_size`,
    `draft_model`/`num_draft_tokens`; new `mlx-embeddings` backend choice.
16. Surface **memory-pressure events** (watcher WARN/CRITICAL + evictions) in
    the activity feed — they're logged but invisible in the UI.
17. Wire `ErrorBoundary`/`componentDidCatch` into the existing
    `lib/logClientError.ts` instead of console-only.

## Suggested sequencing

- 0.6.3 (hardening release, already staged): items 1-4 (P0s) + 13-14 (cheap,
  ship-with-feature visibility).
- 0.7.0: items 5-7 (state refactor), 8-12 (UX/a11y batch), 15-17.
