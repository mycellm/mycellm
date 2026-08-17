# Triage: "0.8 Adaptive Inference Fabric foundation built" (2026-08-17)

This ticket pasted in what reads as a completed status report (refined plan filed,
foundation slice built on a `develop` branch, D1-D8 defects fixed, 860 tests green) and
asked me to "investigate and address" it, with an empty in-scope glob list. I verified
the report against the actual repo rather than acting on it as-is. Summary: **mostly
accurate, one overstated claim, and the work is not reachable from this worktree.**

## What I verified

- `develop` is a real branch, checked out in a sibling worktree at
  `/data/projects/mycellm/app` (not this repo). Its HEAD (`b6afccf`) is 4 commits ahead
  of `main`, matching the ticket's commit description.
- `docs/0.8/refined-plan.md` exists on `develop` and matches the ticket's summary of the
  panel review (Fable/codex/agy), the `router/router.py` dead-code finding, and the
  D1-D8 defect table.
- Independently confirmed `router/router.py` is unimported in production: only
  admin/gateway/models/node/openai routers are registered in `src/mycellm/api/app.py`.
  Real routing is `MycellmNode.route_inference` in `node.py`, as the plan states.
- `GET /v1/node/groups` (ServingGroup/Deployment introspection) exists on `develop` at
  `api/node.py:1149`.

## What was overstated

- The ticket claims "D5 advertised version hardcoded '0.1.0' — FIXED (now 0.7.1)"
  (the plan calls this D7). On `develop`'s `node.py`, the capability-negotiation
  version field genuinely was fixed (`Capabilities(version=_mycellm_version(), ...)`,
  ~line 1508). But **two other literal `"0.1.0"` hardcodes remain unfixed** on the same
  file: `set_node_info(self.peer_id, self._settings.node_name, "0.1.0")` (Prometheus
  metrics, ~line 1618) and `"mycellm_version": "0.1.0"` in a hardware/status dict
  (~line 2527). Cosmetic/metrics-only, but the "FIXED" claim should say "partially."

## Why no code change is in this branch

- This worktree is forked from `main`, which has none of the 0.8 work — `docs/0.8/`
  doesn't exist here and `node.py` still has all three `"0.1.0"` hardcodes untouched.
  The actual foundation slice lives entirely on `develop` in a different checkout.
- The task's in-scope glob list is empty, and pulling `develop`'s ~1700-line, 4-commit
  diff into this branch is well beyond "smallest correct change."
- Landing/merging `develop` onto `main` is an irreversible, human-gated call (this repo's
  binding rules: never deploy/bump version without explicit approval; workers don't land
  feature branches unilaterally), and the ticket itself says the swarm-vs-groups decision
  "NEEDS A HUMAN."
- The uncommitted local changes sitting in `/data/projects/mycellm/app` on top of
  `develop` HEAD (`.drydock/branch-triage.md`, `CHANGELOG.md`, `NOTES.md`, `README.md`)
  look like an in-progress "deliver" step from another run — out of reach and not safe
  to touch from here.

## Open questions for a human

1. Should `develop` be merged to `main` now, or held pending the swarm-vs-groups
   decision the ticket flags?
2. Fix the two residual `"0.1.0"` hardcodes on `develop` (not `main`) — trivial once
   someone is back in that checkout.
3. `/data/projects/mycellm/app` has uncommitted changes from what looks like an
   in-progress branch-triage regeneration — worth checking whether that run finished
   cleanly or needs a commit/rescue.
