# Verifier contract

A **Verifier** checks a built increment against its acceptance criteria for one *deliverable type*.
It is the single genuinely new primitive in Nightshift. Verification is keyed by the kind of thing
produced (the output), **not** by source language — so one project can register several Verifiers
(e.g. an app deliverable and a documentation deliverable).

## Inputs and outputs

```
Verifier.verify(increment, config) -> VerificationResult
```

`Increment` (see `verifiers/base.py`):

- `id`, `summary`, `deliverable_type`
- `acceptance_criteria: [str]` — what "done and good" means; the oracle checks against these
- `requires: [str]` — what the increment can't be done/verified without (`"device"`, `"human"`, ...)
- `rubric_path` — optional rubric for the vision pass (taste-adjacent work)
- `target` — URL / scheme / file path, interpreted by the Verifier

`VerificationResult`:

- `verdict: pass | fail | needs-human`
- `built`, `tests_ran`, `tests_passed`, `tests_failed`
- `screenshots: [path]`, `vision_findings: [{rubric_item, verdict, notes}]`
- `evidence_paths: [path]`, `notes`

## The registry and the trust boundary

`registry.register(verifier)` maps `deliverable_type -> Verifier`.
`registry.can_verify_unattended(increment)` returns true **iff** a Verifier exists for the type
**and** the increment needs no device/human. This is the gate the shift loop uses to decide: run
unattended, or route to the human-gated queue. Nothing needing a physical device or human taste is
ever attempted blind.

## Initial deliverable types

| Type | How it verifies | Unattended? |
| --- | --- | --- |
| `web` | Playwright load → screenshot → vision-vs-rubric + unit/e2e tests | yes |
| `ios` | `xcodebuild` build/test → simulator → screenshot → vision | UI yes; device/accuracy → human-gated |
| `pdf` | render pages → vision-vs-rubric | floor yes; final taste sign-off → human-gated |
| `cli` | run tests + smoke-run + parse output | yes |

## Discrimination requirement

Before any Verifier is trusted to gate unattended work, it must demonstrably **discriminate**: a
deliberately-broken deliverable returns `fail`, a good one returns `pass`. A Verifier that
rubber-stamps is worse than none.

## Adding a deliverable type

Implement `Verifier` with a new `deliverable_type`, call `registry.register(...)`, and supply a
rubric if it uses a vision pass. No change to the orchestration brain is needed.
