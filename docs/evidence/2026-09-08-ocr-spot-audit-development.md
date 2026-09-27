# Random spot-audit development — 2026-09-08

This is local generated-fixture development evidence, not full copied-source
qualification, representative OCR accuracy, independent human adjudication,
canonical adoption, or completion of the 67-outcome OCR/AI program.

## Delivered scope

The opt-in [random spot-audit lane](../ocr-spot-audits.md) adds a separate strict
v1 snapshot, complete source-page eligibility frame, reproducible seeded sampling,
source-first original-page preview, unfinished versus reviewed transcription,
explicit unresolved/unavailable outcomes, explicit-only scoring, and private
create-only Save/restart. Existing draft schemas, default review UI, baseline
CER/WER semantics, OCR execution, canonical extraction and indexes are unchanged.

The eligible frame is only the recovery report's retained nonempty candidates
with at most 20,000 characters and mean confidence at least the chosen threshold.
The current recovery contract retains at most 20 candidates and 5,000 source
pages. Unselected, deferred, failed, empty, overlong and low-confidence pages
are excluded explicitly, not counted clean. The SHA-256 counter/rejection/
Fisher–Yates algorithm retains nominal n/N inclusion fractions, not calibrated
accuracy, independently selected seeds or representative population intervals.

The Gradio skill and official Blocks/event/layout guidance informed separate
state and image-delivery tokens, private serialized callbacks, dictionary
outputs, field-change invalidation and no-op protection. The Playwright skill
guided actual browser verification. The listed PDF skill file could not be
read; generated fixtures used the project's installed PyMuPDF renderer instead.

## Source identity and independent review

Original HEAD remains `e34103f70b676eacc8d55badb2468b8a10004ef4`, tree
`af0a69ccde82b6b679580f07813495721978efe0`. These identify the original history,
not a commit containing this dirty-worktree implementation. No index/history,
architecture baseline, model asset or corpus change was made.

| Changed production file | SHA-256 |
| --- | --- |
| `ocr_spot_audit.py` | `ac80adf141511d05f492114deee9fc44aca38e2e1153c4826b112320d170c215` |
| `ocr_spot_audit_ui.py` | `26cadee77aa8c79e6b70fe3721ff4eecd52c87888629100d200fad67dcc0c5e4` |
| `ocr_review_runtime.py` | `3f40873a0dc87a879a7f34125ddea46e879d8f69c0dcfd41367d39b59c8e3a09` |
| `ocr_review_ui.py` | `8c0ba704d479e61ac56b3b1d0d3db53f418b573c9ce63bb39f259ce52a8edb49` |
| `tools/review_ocr.py` | `218e6f697f17515ac86a7a3a6baceb4d812ccff040b2501948f2d77325c42bdc` |

Read-only `hash-object --no-filters` Git blob identities, in the same file order:
`8cc2bfe70def79413c225634df28d481226c80e5`,
`dde97997f7fb51898dec3a91c25e472e4511656a`,
`f881dab12d7bec82d96b310b12125d1ff4198343`,
`d17c41dd03037bd321f98cc41673f84973e2335d`,
`f14dccbfe691e132de9483f18535c031a0ec3f13`. No objects were written or staged.

Peirce implemented/tested the pure policy. Anscombe implemented/tested the UI.
The primary agent integrated fixed-input loading, original rendering, private
publication and launcher opt-in. Socrates independently reviewed the policy,
runtime, launcher, UI and retained evidence. Review found and closed these
specific issues before final verification:

- A late valid workspace substitution must not relabel already rendered pixels.
  Original rendering now captures/rechecks its complete host binding.
- Secondary image-close exceptions must not replace the primary failure.
- Field invalidation needs actual `.change` observations and a no-op guard;
  confirmation additionally binds the exact submitted field digest.
- Image ownership cannot transfer before the session lock exits successfully.
- Image-delivery authority cannot be recovered through ordinary edits following
  a failed Gradio image postprocess. Explicit Reset clears it instead.

The original renderer's existing behavior is retained. The new original-page
renderer caps allocation before decoding to a 1,400-side RGB display, checks
actual geometry/stride/bytes and closes untransferred images. It is in-process,
not a total decoder-memory cap or an isolated-worker deadline guarantee.

## Focused development verification

Qualified Python 3.12.10 was invoked with `-I -B`; generated-only tests had
plugin autoload/cache disabled and a curated child environment. The finite
helper [ocr_spot_audit_focused_v1.py](../../tmp/ocr_spot_audit_focused_v1.py)
completed terminal exit 0. Its retained directory is
[tmp/ocr-spot-audit-focused-v1](../../tmp/ocr-spot-audit-focused-v1).

| Test file group | Passing cases |
| --- | ---: |
| Spot-audit policy / runtime / UI | 91 / 43 / 45 |
| Existing review policy / runtime / UI | 52 / 34 / 16 |
| Existing scan review / drafts / transcription evaluator | 37 / 45 / 56 |
| Total | 419 |

No failures, errors, skips or warnings were reported. Pytest printed 65.62 s;
JUnit recorded 65.595 s; the outer command measured 68.828 s. These are separate
test-run clocks, not OCR throughput. Ruff checked the five changed production
files and three new test files and exited 0.

Socrates verified 419 unique retained JUnit identities across the requested nine
files and all 429 enumerated immediate root/tools/tests Python files against
the identical before/after manifests and current bytes. No separate collection
artifact was produced: this is not an independent collection-bijection or
complete loaded-byte qualification claim.

- Receipt SHA-256: `9ccbdf3466d9cd33e31f1495cff2b9b2fd46f1c0ca6728be954a6af2acc83437`.
- JUnit: `79abde30680a1b95eb42853c9278a2922aeef76309c70304daf0c4d9fa7ea2c4`.
- Identical source manifests: `217356a0ec676fc38cbf240d86d1ec78a3ae035f941f18da1d3703bf6c5b36a3`.

Earlier diagnostic generations remain separate: the core's first fixtures
violated the existing mean-versus-line confidence contract; corrected fixtures
passed 91 tests without a policy change. UI v1 had 41 passes/four fixture failures
because direct setup did not install dynamic Dropdown choices in real SessionState;
v2 had 44 passes/one assertion failure because Gradio wraps the injected exception;
v3 passed 45 tests using actual process_api setup and exact cause identity.
Their XML files remain under `tmp/ocr_spot_audit_core_v*.xml` and
`tmp/ocr_spot_audit_ui_v*.xml`. A separate early 126-case compatibility check
predates the final rendering safeguards; it does not qualify those repairs.

The queue controls use actual `Queue.push` capture followed by actual
`process_api` dispatch of that retained body after a page change. They are not
claims about autonomous Queue worker scheduling or all browser race timings.

## Generated browser / real storage round trip

The reviewed [browser helper](../../tmp/ocr_spot_audit_browser_v1.py), SHA-256
`ed5e7f8b4e34922ca4e9744e8d5d455c27a8a6607d89872312ecd2223d2c71ab`,
ran the real complete review app and ReviewWorkspace on authenticated literal
loopback. It authored a five-page PDF and synthetic Reader candidates; no native
OCR engine was configured or called by the helper. Save and summary wrappers
only observed actual production results. Events/control reads were bounded.

Evidence is retained under
[output/playwright/ocr-spot-audit-v1](../../output/playwright/ocr-spot-audit-v1).
The full frame had five pages: three eligible candidates, one failed retry and
one unselected page. All three eligible candidates were sampled; this browser
case tests retention/navigation, not a statistically informative subsample.
Separate policy tests exercise partial random samples and a fixed draw vector.

Observed journey:

1. Create the frozen sample, open page 1's original pixels and transcribe.
   Typing one character after confirmation clears it; unconfirmed Store refuses.
   Fresh confirmation and Store reveal the deliberately wrong high-confidence
   candidate. Only explicit Score invokes the summary.
2. Store page 2 as a deliberately unresolved workflow control, leave page 3's
   transcription unfinished, then Save. The first snapshot has one reviewed,
   one unresolved and one pending item; the unfinished draft is not promoted.
3. Close the first app and create a fresh app/workspace in the same helper
   process with the exact saved snapshot. Reauthenticate and restore the same
   sample. The draft survives; image, confirmation and metrics are absent.
   Confirmation before a fresh original Open is refused. No summary call occurs
   during restore, page selection, refused confirmation or Open.
4. Inspect page 3's original, finish its transcription, explicitly confirm/Store,
   Score and Save a child. It retains two reviewed and one unresolved item;
   the first snapshot's bytes remain unchanged. Browser and both app lifecycles
   close, and the owned helper terminates with exit 0.

The deliberately incorrect candidate declares mean confidence 0.99. Its one
wrong character gives 1/15 CER and 1/3 WER for the first reviewed subset. Adding
a second exact 17-character reference gives 1/32 CER and 1/6 WER. This denominator
change is not an OCR improvement; the erroneous candidate is unchanged. The
unresolved page is never scored. No critical-token references were supplied.

The primary agent inspected original-page screenshots and retained actual typing,
refusal, Save and restored-view snapshots. The final browser console observation
reported zero errors/warnings. This is not screen-reader, IME, clipboard, general
platform, independent human review or representative-corpus qualification.

| Retained artifact | SHA-256 |
| --- | --- |
| Browser completion | `0ea3d63386cffac20c8325a6e79cdda4b3e82fe5810c38e53decbe32684e38b1` |
| Four-event log: two explicit Scores, two Saves | `ce0eed71a07ed68c650dde7a2e38970969c9af68c32cc759f8e4933092ae2a61` |
| Identical 11-file browser source manifests | `6a16ec1405007de053bef3764e7e6b32ba8ae8c6d0716dc32fd1160b3535db76` |
| First audit snapshot | `b2c30f0051505569c58d3242d788cd5c527b09498816d91fc927167476bb45e2` |
| Child audit snapshot | `f856f2c4383e16aaada2b17fe8943be3cdb34d41ba0b6ef0577d236aa0decd71` |

Socrates independently revalidated both snapshots without scoring/native calls,
matched parent/child differences and all 11 source pins to the focused manifest,
and checked retained browser refusal/restoration observations. Peirce separately
checked the documentation's hashes, arithmetic, 111 relative links and all 67
unchanged requirement/acceptance pairs. These are agent reviews, not independent
human transcription or corpus qualification.

The completion record explicitly has `private_caches_absent: false`. Ordinary
file inspection initially found no cached files. Forced metadata inspection then
found one remaining cache-named directory with a reparse attribute and a hidden
`vibe_edit_history` directory. The nonrecursive empty-directory removal guard
refused; no directory/history was removed and its contents were not inspected.
The original completion and separate `posthoc-empty-cache-cleanup.json` preserve
that limitation. Cause is not established. Two returned `app.close()` calls and
helper termination do not qualify automatic framework/cache cleanup.

## Remaining work

Fresh architecture admission/review and full copied-source gates remain pending
for both the previous Save-feedback changes and this later feature. The original
architecture size cap, baseline, Git index and history are unchanged. The prior
v4 full qualification does not cover this generation.

Independent reviewed error labels, representative held-out sources and reference
policy, population inference, reviewer effort, broader accessibility and every
other pending OCR/AI acceptance outcome remain outstanding. Existing eight-page
OCR benchmarks are unchanged; this generated audit control is not a new accuracy
or speed benchmark. No new corpus/disclosure/AI destination authority is implied.
