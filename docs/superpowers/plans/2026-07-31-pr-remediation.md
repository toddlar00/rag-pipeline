# PR Stack and CI Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore billing-blocked GitHub Actions, merge the already-validated
R1 candidate (#44), add a two-lane CI matrix, converge #66/#68–#71/#73 (plus
the #67 docs reconciliation) into one R2 candidate, and redo dependabot #72's
updates through the hash-locked policy.

**Architecture:** Pure repository-management work — merge mechanics, one
workflow-file change, evidence regeneration, and documentation reconciliation.
No pipeline behavior changes. Spec:
`docs/superpowers/specs/2026-07-31-pr-remediation-design.md`.

**Tech Stack:** git, GitHub CLI (`gh`), GitHub Actions, Python 3.12 tooling in
`tools/` (`benchmark_phase_a0.py`, `check_architecture_inventory.py`,
`check_ci_security.py`, `refresh_locks.py`), uv-provisioned locked CPU
environments on native Windows and WSL.

## Global Constraints

- **Merges are history-preserving only** (`gh pr merge --merge`, never
  `--squash` or `--rebase`): `benchmarks/README.md` — "a squash or
  history-rewriting rebase invalidates this evidence."
- **Every `gh` and remote `git` command runs with `env -u GH_TOKEN`**: the
  session's `GH_TOKEN` has no scopes and cannot see the private repo; the
  keyring token (scopes `gist, read:org, repo`) works.
- **The keyring token lacks the `workflow` scope**, so pushing any branch that
  edits `.github/workflows/*` fails until the owner runs
  `gh auth refresh -s workflow` interactively (Task 3, Step 1).
- **Never hand-edit** `*.lock`, `benchmarks/phase-a0-*.json`, or
  `architecture-inventory.json`. Locks come only from
  `python tools/refresh_locks.py`; the inventory only from
  `python tools/check_architecture_inventory.py --refresh`; baselines only
  from `python tools/benchmark_phase_a0.py`.
- **Phase A0 gate-only delta:** after a pre-gate source checkpoint, the diff
  to the PR head may touch ONLY: `.gitattributes`,
  `.github/workflows/ci.yml`, `ROADMAP.md`, `benchmarks/README.md`,
  `benchmarks/phase-a0-linux-cpython312.json`,
  `benchmarks/phase-a0-windows-cpython312.json`, `docs/README.md`,
  `docs/architecture/decisions/phase-a0-benchmark-policy.md`. Everything else
  (source, specs, plans, inventory) must be committed BEFORE the checkpoint.
- **Any Python-source or dependency/model-lock change invalidates both Phase
  A0 baselines** and requires regeneration on both platforms from a new clean
  checkpoint (`benchmarks/README.md`).
- **CI-security invariants** (`tools/check_ci_security.py`): all actions
  pinned to full 40-char SHAs; exactly one top-level `permissions:` mapping of
  exactly `contents: read` per workflow, no job-level overrides; every
  `actions/checkout` sets `persist-credentials: false` exactly once;
  `security.yml` path filters cover every declared security owner.
- **Dropbox-synced working copy:** writes can silently not persist. Re-read
  files after writing before trusting a result (project memory + ROADMAP P0).
- **Candidate branch naming:** heavy-lane CI triggers on PR head branches
  matching `*-candidate` (introduced in Task 3). The R2 branch is
  `agent/r2-candidate`; the dependency branch is
  `agent/dependency-refresh-candidate`.
- Report observed test counts, not archived ones (repo CLAUDE.md).

## Preconditions

- **Owner action (blocking everything):** fix GitHub Actions billing at
  GitHub → Settings → Billing & plans (failed payment or spending limit).
  No task below can complete until hosted jobs start.
- Verified facts this plan relies on (checked 2026-07-31):
  - `origin/main` is an ancestor of `origin/agent/r1-cumulative-gate` (#44),
    and #44's head had an all-green hosted run on 2026-07-25
    (run id 30156965565).
  - Heads of #31–#43 are all ancestors of #44's head, EXCEPT #32
    (`agent/process-supervision-design`), which is not an ancestor.
  - #73 head (`c633363`) contains #44 and #66; #68 ⊂ #69 ⊂ #70 ⊂ #71 all
    build on #66; #67 is independent of both lines.
  - `git merge-tree` of #71 into #73 conflicts ONLY in: `rag.py`,
    `architecture-inventory.json`, `benchmarks/README.md`,
    `benchmarks/phase-a0-linux-cpython312.json`,
    `benchmarks/phase-a0-windows-cpython312.json`, `docs/README.md`,
    `docs/architecture/decisions/phase-a0-benchmark-policy.md`.
  - The remediation spec is committed on branch `agent/remediation-spec`
    (commit `0f0c0a6`), branched from #73's head.

---

### Task 1: Verify CI restoration after the owner billing fix

**Files:** none (verification only).

**Interfaces:**
- Consumes: owner has completed the billing fix.
- Produces: confirmation that hosted jobs start; a fresh full-matrix run at
  #73's frozen head `c633363` (free revalidation of the R2 base).

- [ ] **Step 1: Confirm the billing failure is gone by re-running #73's failed run**

```bash
cd "/c/Users/greyb/Dropbox/Encrypt all-in-one/Claude Co-Work Permission Folder/rag-pipeline"
env -u GH_TOKEN gh run rerun 30471949211 --failed
env -u GH_TOKEN gh run rerun 30471950117 --failed
```

Expected: both commands succeed (no error output).

- [ ] **Step 2: Watch until jobs actually execute**

```bash
env -u GH_TOKEN gh run watch 30471949211 --exit-status
```

Expected: jobs enter `in_progress` (not instant 1–9s failures). Any outcome
other than the billing annotation means CI is restored. If jobs still fail
instantly, STOP — billing is not fixed; report back to the owner.

- [ ] **Step 3: Record the outcome of the #73 head run**

```bash
env -u GH_TOKEN gh pr checks 73
```

Expected: checks complete with real durations (minutes, not seconds). Record
pass/fail per check in the task report. Failures here are NEW information
(first hosted run of this head) and must be reported, not fixed silently.

---

### Task 2: Bank PR #44 into main on its existing green evidence

**Files:** none locally (GitHub merge + verification).

**Interfaces:**
- Consumes: Task 1 confirmation that CI/billing works (merge itself needs no
  new run, but post-merge `push: main` CI will fire and must be able to run).
- Produces: `main` advanced to contain #44's 69 commits; #31–#43 (except
  possibly #32) auto-marked merged.

- [ ] **Step 1: Verify main has not moved and the green run is at #44's exact head**

```bash
cd "/c/Users/greyb/Dropbox/Encrypt all-in-one/Claude Co-Work Permission Folder/rag-pipeline"
env -u GH_TOKEN git fetch origin
git merge-base --is-ancestor origin/main origin/agent/r1-cumulative-gate && echo MAIN_IS_ANCESTOR
PR_HEAD=$(env -u GH_TOKEN gh pr view 44 --json headRefOid -q .headRefOid)
RUN_HEAD=$(env -u GH_TOKEN gh api repos/toddlar00/rag-pipeline/actions/runs/30156965565 --jq .head_sha)
echo "PR head:  $PR_HEAD"
echo "Run head: $RUN_HEAD"
[ "$PR_HEAD" = "$RUN_HEAD" ] && echo HEADS_MATCH
```

Expected: `MAIN_IS_ANCESTOR` and `HEADS_MATCH`. If either fails, STOP —
the no-new-CI merge premise is broken; the head must be revalidated hosted
before merging (re-run the full matrix via workflow_dispatch at that head).

- [ ] **Step 2: Confirm #44's checks are all green**

```bash
env -u GH_TOKEN gh pr checks 44
```

Expected: every row `pass`. (These are the 2026-07-25 results; they remain
attached to the unchanged head SHA.)

- [ ] **Step 3: Owner sign-off gate**

Confirm with the owner (Cade) that this merge is their review sign-off for
the R1 candidate. Do not proceed on silence.

- [ ] **Step 4: Mark ready and merge (history-preserving)**

```bash
env -u GH_TOKEN gh pr ready 44
env -u GH_TOKEN gh pr merge 44 --merge
```

Expected: merge succeeds. NEVER `--squash` or `--rebase` (Global
Constraints).

- [ ] **Step 5: Verify main's tree equals the validated tree**

```bash
env -u GH_TOKEN git fetch origin
git merge-base --is-ancestor origin/agent/r1-cumulative-gate origin/main && echo BANKED
[ "$(git rev-parse origin/agent/r1-cumulative-gate^{tree})" = "$(git rev-parse origin/main^{tree})" ] && echo TREE_IDENTICAL
```

Expected: `BANKED` and `TREE_IDENTICAL` (main had not diverged, so the merge
tree is exactly the validated tree).

- [ ] **Step 6: Verify auto-closure of the contained drafts**

```bash
env -u GH_TOKEN gh pr list --state open --limit 50 --json number -q '.[].number'
```

Expected: #31, #33–#43 no longer listed (GitHub marks PRs merged when their
head commits become reachable from the base). Remaining open: #32 (if not
auto-closed), #66–#73, #72.

- [ ] **Step 7: Close #32 manually if still open**

#32 (`agent/process-supervision-design`) is NOT an ancestor of #44. Compare
its content against main first:

```bash
git diff --stat origin/main...origin/agent/process-supervision-design
```

If the diff shows only the design document that the merged process-supervision
ADR superseded, close it:

```bash
env -u GH_TOKEN gh pr close 32 --comment "Superseded: the process-supervision extraction and its ADR merged to main via #44 (design content carried in docs/architecture/decisions/process-supervision-extraction.md)."
```

If the diff shows anything else, STOP and report the residue to the owner
instead of closing.

- [ ] **Step 8: Confirm the post-merge `push: main` full-matrix run passes**

```bash
env -u GH_TOKEN gh run list --branch main --limit 3
env -u GH_TOKEN gh run watch <newest-run-id> --exit-status
```

Expected: full matrix green on main. A failure here is new information —
report it; do not patch main directly.

---

### Task 3: Two-lane CI matrix

**Files:**
- Modify: `.github/workflows/ci.yml` (only this file — the branch must stay
  inside the Phase A0 allowed-delta set so the `phase-a0` job passes without
  evidence regeneration)

**Interfaces:**
- Consumes: merged main from Task 2.
- Produces: `lane` job with outputs `heavy` (`'true'`/`'false'` string) and
  `pythons` (JSON array string). Heavy jobs (`unit-windows`, `service-api`,
  `phase-a0`, `vector-store-smoke`, `full-integration`) run only when
  `needs.lane.outputs.heavy == 'true'`. Later tasks rely on branch names
  matching `*-candidate` to get the heavy lane on PRs.

- [ ] **Step 1: Owner adds the workflow scope to the gh token (interactive)**

Ask the owner to run in their terminal (or via `!` in the session):

```bash
gh auth refresh -s workflow
```

Verify afterward:

```bash
env -u GH_TOKEN gh auth status
```

Expected: keyring token scopes now include `workflow`.

- [ ] **Step 2: Create the branch off updated main**

```bash
cd "/c/Users/greyb/Dropbox/Encrypt all-in-one/Claude Co-Work Permission Folder/rag-pipeline"
env -u GH_TOKEN git fetch origin
git switch -c agent/ci-two-lane origin/main
```

- [ ] **Step 3: Add the `lane` job to `.github/workflows/ci.yml`**

Insert as the FIRST job under `jobs:` (before `quality`). It uses no actions,
so no SHA pins are needed; `github.head_ref` is passed through `env` to avoid
expression injection into the script:

```yaml
  lane:
    name: Select lane
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    outputs:
      heavy: ${{ steps.pick.outputs.heavy }}
      pythons: ${{ steps.pick.outputs.pythons }}
    steps:
      - name: Pick lane from event and head ref
        id: pick
        env:
          EVENT_NAME: ${{ github.event_name }}
          HEAD_REF: ${{ github.head_ref }}
        run: |
          heavy=false
          if [ "$EVENT_NAME" = "push" ] || [ "$EVENT_NAME" = "workflow_dispatch" ]; then
            heavy=true
          elif [ "$EVENT_NAME" = "pull_request" ]; then
            case "$HEAD_REF" in
              *-candidate) heavy=true ;;
            esac
          fi
          if [ "$heavy" = "true" ]; then
            pythons='["3.10", "3.11", "3.12", "3.13", "3.14"]'
          else
            pythons='["3.12"]'
          fi
          {
            echo "heavy=$heavy"
            echo "pythons=$pythons"
          } >> "$GITHUB_OUTPUT"
```

- [ ] **Step 4: Make `unit-linux` consume the lane matrix**

Change the `unit-linux` job header from:

```yaml
  unit-linux:
    name: Unit tests / Python ${{ matrix.python-version }} / Linux
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]
```

to:

```yaml
  unit-linux:
    name: Unit tests / Python ${{ matrix.python-version }} / Linux
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    needs: lane
    strategy:
      fail-fast: false
      matrix:
        python-version: ${{ fromJSON(needs.lane.outputs.pythons) }}
```

- [ ] **Step 5: Gate the five heavy jobs on the lane**

Add these two lines to each of `unit-windows`, `service-api`, `phase-a0`,
`vector-store-smoke`, and `full-integration`, immediately after their
`timeout-minutes:` line (indentation matches the job's other keys):

```yaml
    needs: lane
    if: needs.lane.outputs.heavy == 'true'
```

`quality` and `evaluation-smoke` are NOT changed — they run on every event.

- [ ] **Step 6: Validate locally**

```bash
python tools/check_ci_security.py
python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text(encoding='utf-8')); print('YAML OK')"
```

Expected: `GitHub Actions security policy is valid.` and `YAML OK`.
(If PyYAML is absent in the current env, install the test lock first:
`python -m pip install --require-hashes -r requirements-test.lock`.)

- [ ] **Step 7: Verify the branch delta is exactly one file**

```bash
git add .github/workflows/ci.yml
git diff --cached --name-only
```

Expected output, exactly:

```text
.github/workflows/ci.yml
```

- [ ] **Step 8: Commit and push**

```bash
git commit -m "Split CI into draft fast lane and candidate heavy lane

Draft PRs run quality gates, offline evaluation, and Linux 3.12 unit
tests. Push, workflow_dispatch, and *-candidate PR heads run the full
matrix including Windows, CPython 3.10-3.14, service API, real vector
stores, Phase A0, and full integration.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
env -u GH_TOKEN git push -u origin agent/ci-two-lane
```

Expected: push accepted (fails with a workflow-scope error if Step 1 was
skipped).

- [ ] **Step 9: Open the PR and verify the fast lane behaves**

```bash
env -u GH_TOKEN gh pr create --draft --title "Split CI into draft and candidate lanes" --body "Two-lane CI: draft PRs run quality gates, offline evaluation suites, and Linux 3.12 unit tests only; push to main, workflow_dispatch, and *-candidate PR branches run the full matrix. Workflow-only change; stays inside the Phase A0 gate-only delta set. Part of docs/superpowers/specs/2026-07-31-pr-remediation-design.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
env -u GH_TOKEN gh pr checks --watch
```

Expected: on this PR (branch does not match `*-candidate`), heavy jobs show
`skipped`; `Quality gates`, `Offline retrieval regression suites`, and
`Unit tests / Python 3.12 / Linux` pass.

- [ ] **Step 10: Exercise the heavy lane on this branch via dispatch**

```bash
env -u GH_TOKEN gh workflow run ci.yml --ref agent/ci-two-lane
env -u GH_TOKEN gh run list --workflow ci.yml --branch agent/ci-two-lane --limit 1
env -u GH_TOKEN gh run watch <run-id> --exit-status
```

Expected: full matrix runs and passes (the `phase-a0` job passes because
`ci.yml` is inside the allowed gate-only delta).

- [ ] **Step 11: Merge (history-preserving)**

```bash
env -u GH_TOKEN gh pr ready
env -u GH_TOKEN gh pr merge --merge
```

Expected: merged; subsequent `push: main` run is green.

---

### Task 4: Create the R2 candidate branch and resolve the #71 × #73 merge

**Files:**
- Create: branch `agent/r2-candidate` from #73's head `c633363`
- Modify (conflict resolution): `rag.py` (manual, behavior-preserving),
  `docs/README.md`, `docs/architecture/decisions/phase-a0-benchmark-policy.md`,
  `benchmarks/README.md` (provisional), `benchmarks/phase-a0-*.json`
  (provisional — regenerated in Task 6), `architecture-inventory.json`
  (regenerated by tool)
- Merge in: `origin/agent/strict-toc-verify-contracts` (#71),
  `origin/agent/remediation-spec` (spec + this plan), `origin/main`
  (two-lane ci.yml)

**Interfaces:**
- Consumes: merged main from Task 3; frozen heads of #71 and #73.
- Produces: a merged tree where the full test suite passes; all Python
  source, docs, specs, and the regenerated `architecture-inventory.json` are
  final. Task 5 adds ROADMAP text; Task 6 freezes the pre-gate checkpoint.

- [ ] **Step 1: Create the branch**

```bash
cd "/c/Users/greyb/Dropbox/Encrypt all-in-one/Claude Co-Work Permission Folder/rag-pipeline"
env -u GH_TOKEN git fetch origin
git switch -c agent/r2-candidate origin/agent/release-defect-remediation
```

- [ ] **Step 2: Merge #71 (expect the seven known conflicts)**

```bash
git merge --no-ff origin/agent/strict-toc-verify-contracts
git status --short
```

Expected: conflict markers in exactly the seven files listed in
Preconditions. If NEW conflicted files appear, examine them with the same
policy: source files get manual behavior-preserving resolution; generated
artifacts get regenerated.

- [ ] **Step 3: Resolve `rag.py` manually**

Read both sides of each conflicted hunk
(`git diff` shows base/ours/theirs with `merge.conflictStyle=diff3`:
`git config merge.conflictStyle diff3` first if not set, then re-merge).
Resolution policy:
- "Ours" (#73 line) carries publication-readiness/export hardening.
- "Theirs" (#71 line) carries strict LLM/TOC output contracts.
- The two features are disjoint concerns that touch neighboring code; keep
  BOTH sides' logic. Do not drop either side's validation. Where both sides
  edited the same function, compose the checks (contract validation AND
  publication hardening), preserving each side's error types and messages.

After editing:

```bash
python -m ruff check rag.py
python tools/check_python_sources.py
```

Expected: both pass.

- [ ] **Step 4: Resolve the documentation conflicts**

`docs/README.md` and
`docs/architecture/decisions/phase-a0-benchmark-policy.md`: combine both
sides' narratives (each line documents its own branch's status/evidence);
keep both feature descriptions, and note that Phase A0 evidence for the
merged head is regenerated in this candidate (Task 6 rewrites the
provenance/status sections anyway).

`benchmarks/README.md` and the two `benchmarks/phase-a0-*.json`: resolve
provisionally by taking the #73 side verbatim —

```bash
git checkout --ours benchmarks/README.md benchmarks/phase-a0-linux-cpython312.json benchmarks/phase-a0-windows-cpython312.json
```

These are invalid for the merged source by policy and are fully replaced in
Task 6. Do not hand-edit the JSON.

- [ ] **Step 5: Regenerate the architecture inventory**

```bash
git checkout --ours architecture-inventory.json
python tools/check_architecture_inventory.py --refresh
python tools/check_architecture_inventory.py
```

Expected: refresh succeeds; the plain check then reports success.

- [ ] **Step 6: Conclude the merge and run the fast gates**

```bash
git add -A
git commit --no-edit
python -m ruff check .
python tools/check_python_sources.py
python tools/check_dependency_policy.py
python tools/check_model_artifacts.py
python tools/check_ci_security.py
```

Expected: all pass.

- [ ] **Step 7: Run the full test suite**

```bash
python -m pytest -q
```

Expected: 0 failures. Both lines' suites now coexist; the passing count
should be at or above #73's observed 3,081 (report the observed number).
Skips for absent optional dependencies are acceptable. ANY failure means the
`rag.py` resolution broke one side — fix the resolution, not the test.

- [ ] **Step 8: Run the offline evaluation suites**

```bash
python eval.py --retriever bm25 --queries evaluation/suites/property/queries.jsonl --chunks evaluation/suites/property/chunks.jsonl --k 1 3 5 --depth 10
python eval.py --retriever bm25 --queries evaluation/suites/constitutional_law/queries.jsonl --chunks evaluation/suites/constitutional_law/chunks.jsonl --k 1 3 5 --depth 10
python eval.py --retriever bm25 --queries evaluation/suites/table_family/queries.jsonl --chunks evaluation/suites/table_family/chunks.jsonl --k 1 3 5 --depth 10
```

Expected: all three complete without threshold failures.

- [ ] **Step 9: Merge the spec/plan branch and updated main**

```bash
git merge --no-ff origin/agent/remediation-spec
git merge --no-ff origin/main
```

Expected: the spec merge is conflict-free (docs/superpowers/ only). The main
merge may conflict in `.github/workflows/ci.yml` if the #73 line touched it;
resolve by keeping the two-lane structure from main and folding in any
#73-side step changes. After any resolution:

```bash
python tools/check_ci_security.py
python -m pytest -q
```

Expected: policy valid; suite still passes (observed count recorded).

- [ ] **Step 10: Commit checkpoint**

```bash
git status --short
```

Expected: clean tree (all merges committed).

---

### Task 5: ROADMAP and documentation reconciliation (subsumes #67)

**Files:**
- Modify: `ROADMAP.md`, `docs/README.md` (status sections),
  `CLAUDE.md` (only if its branch-status note is now stale)

**Interfaces:**
- Consumes: merged tree from Task 4.
- Produces: documentation that matches post-Task-2 reality (R1 integrated)
  and describes this candidate; #67 has no unique content left worth keeping.

- [ ] **Step 1: Extract anything #67 has that is still accurate**

```bash
git diff origin/main...origin/agent/docs-roadmap-audit --stat
git diff origin/main...origin/agent/docs-roadmap-audit -- ROADMAP.md | head -200
```

Read the full diff. #67 reconciled docs as of 2026-07-25 — before #44 merged
and before this candidate existed — so treat it as a checklist of
reconciliation POINTS (which tables/sections drift), not as content to apply
verbatim.

- [ ] **Step 2: Update `ROADMAP.md` statuses**

Apply, preserving the file's existing voice and status vocabulary:
- Milestones listed as "Implemented in draft #44" / "Implemented (draft)"
  for PRs #31–#43 → "Integrated" with their merge recorded via #44.
- The #66, #68–#71, #73 milestone rows → combined into this R2 candidate
  (use the status term "Integration candidate (draft)" with the new PR
  number once Task 7 opens it; leave a placeholder reference to the branch
  name `agent/r2-candidate` until then and fix it in Task 7).
- Ethics retrieval calibration (#43): code merged with #44; the OWNER
  DECISION on thresholds remains open — keep it "Owner review pending" and
  note the PR itself is merged.
- Record dependabot #72's disposition: closed in favor of a
  `tools/refresh_locks.py --upgrade` pass under the dependency-domain
  process (Task 8).
- Add the CI two-lane change to the narrative (draft fast lane / candidate
  heavy lane, merged via its own PR).

- [ ] **Step 3: Verify docs coherence and commit**

```bash
python -m pytest -q tests/ -k "roadmap or docs" --collect-only -q | head -20
python -m pytest -q
git add ROADMAP.md docs/README.md CLAUDE.md
git commit -m "Reconcile roadmap and docs for the R2 candidate

Records R1 integration via #44, folds the #66-#71/#73 milestone rows
into this candidate, keeps the Ethics calibration owner decision open,
and records the dependabot #72 disposition. Subsumes and supersedes #67.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

Expected: if any collected tests assert on ROADMAP/docs content, they pass;
full suite still green (some suites hash-pin doc inventories — if one fails,
follow its failure message to regenerate the pinned value with the
repository's own tool rather than hand-editing).

---

### Task 6: Freeze the pre-gate checkpoint and regenerate Phase A0 evidence

**Files:**
- Modify (evidence-only commit): `benchmarks/phase-a0-windows-cpython312.json`,
  `benchmarks/phase-a0-linux-cpython312.json`, `benchmarks/README.md`,
  `docs/architecture/decisions/phase-a0-benchmark-policy.md`,
  `docs/README.md`, `ROADMAP.md` (evidence note only)

**Interfaces:**
- Consumes: final source tree from Task 5 (the pre-gate checkpoint S).
- Produces: evidence child commit E whose delta from S touches only the
  allowed gate-only paths; both platform baselines bind S; local 9×5 checks
  pass on both platforms.

**Environment note:** generation requires the exact locked CPU environment on
BOTH native Windows and WSL (Linux), CPython 3.12 x86-64, per
`benchmarks/README.md`. The `--require-clean` flag refuses a dirty worktree,
so reports are generated into a temp path and copied in afterward.

- [ ] **Step 1: Record the pre-gate checkpoint**

```bash
cd "/c/Users/greyb/Dropbox/Encrypt all-in-one/Claude Co-Work Permission Folder/rag-pipeline"
git status --short          # must be empty
git rev-parse HEAD          # this is S — record it
git rev-parse 'HEAD^{tree}'
```

Expected: clean tree. Record both hashes for the provenance table.

- [ ] **Step 2: Provision the exact environment (Windows)**

In a disposable/managed interpreter per `benchmarks/README.md` (never
`--system` against the durable user Python):

```bash
python -m pip install --require-hashes -r requirements-lock-tools.lock
uv pip sync --strict --torch-backend cpu --require-hashes requirements-full.lock requirements-test.lock requirements-lock-tools.lock
uv pip check
```

Expected: sync completes; `uv pip check` reports no issues.

- [ ] **Step 3: Generate the Windows baseline at clean S**

```bash
python tools/benchmark_phase_a0.py --require-clean --output "$TEMP/phase-a0-windows-cpython312.json"
```

Expected: completes all 9 scenarios × 5 repetitions; report written. Then
copy it in and re-read to confirm the bytes persisted (Dropbox hazard):

```bash
cp "$TEMP/phase-a0-windows-cpython312.json" benchmarks/phase-a0-windows-cpython312.json
python -c "import json; json.load(open('benchmarks/phase-a0-windows-cpython312.json', encoding='utf-8')); print('READ-BACK OK')"
```

- [ ] **Step 4: Generate the Linux baseline at clean S (WSL)**

From WSL, at a clean checkout of S (same repo via /mnt or a separate clone
fetched to S), with the same provisioning as Step 2:

```bash
python tools/benchmark_phase_a0.py --require-clean --output /tmp/phase-a0-linux-cpython312.json
```

Copy into the Windows working tree's `benchmarks/` and read back as in
Step 3. NOTE: `--require-clean` on the WSL side must run BEFORE the Windows
report is copied into a shared checkout — generate on each platform from a
clean S, then assemble both reports.

- [ ] **Step 5: Update the provenance documentation**

Rewrite `benchmarks/README.md` provenance/status sections for the new pair
(source commit S, tree hash, byte sizes, file SHA-256, embedded report
SHA-256 — compute with):

```bash
python - <<'PY'
import hashlib, json
from pathlib import Path
for name in ("phase-a0-windows-cpython312.json", "phase-a0-linux-cpython312.json"):
    p = Path("benchmarks") / name
    data = p.read_bytes()
    report = json.loads(data)
    print(name, len(data), hashlib.sha256(data).hexdigest(), report["report_sha256"])
PY
```

Update `docs/architecture/decisions/phase-a0-benchmark-policy.md`,
`docs/README.md`, and the ROADMAP evidence paragraph to describe the new
candidate pair. Do not claim any hosted result.

- [ ] **Step 6: Commit the evidence child and verify the delta set**

```bash
git add benchmarks/ docs/README.md docs/architecture/decisions/phase-a0-benchmark-policy.md ROADMAP.md
git commit -m "Freeze R2 candidate Phase A0 evidence

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git diff --name-only <S>..HEAD
```

Expected: the printed list is a subset of the eight allowed gate-only paths.
If anything else appears, the checkpoint is broken — move the offending
change into a commit BEFORE S (amend the structure, regenerate evidence).

- [ ] **Step 7: Run the same-platform 9×5 checks at clean E**

Windows:

```bash
python tools/benchmark_phase_a0.py --require-clean --check benchmarks/phase-a0-windows-cpython312.json --output "$TEMP/phase-a0-current-windows.json"
```

WSL (checkout at E):

```bash
python tools/benchmark_phase_a0.py --require-clean --check benchmarks/phase-a0-linux-cpython312.json --output /tmp/phase-a0-current-linux.json
```

Expected: both comparisons PASS. A failure means nondeterminism or an
environment mismatch — diagnose per the tool's stage/diagnostic codes; do
not regenerate blindly.

---

### Task 7: Publish, validate hosted, review, and merge the R2 candidate

**Files:** none locally (push + GitHub operations + one ROADMAP touch-up).

**Interfaces:**
- Consumes: evidence child E from Task 6 as the branch head.
- Produces: R2 candidate merged to main; #66–#71 and #73 closed; open PRs
  reduced to #32 (if kept), #72 (until Task 8).

- [ ] **Step 1: Push and open the candidate PR**

```bash
env -u GH_TOKEN git push -u origin agent/r2-candidate
env -u GH_TOKEN gh pr create --draft --title "Converge R2 candidate at one exact head" --body "$(cat <<'EOF'
## Summary
- combines the publication-readiness line (#73, containing #44 and #66) with the strict LLM/TOC output-contract stack (#68-#71)
- reconciles ROADMAP and docs, subsuming #67
- freezes fresh paired Windows/Linux Phase A0 evidence at the merged pre-gate checkpoint

## Validation
- full local suite, ruff, compile/policy gates, three offline evaluation suites: passed (counts in ROADMAP)
- local same-platform 9x5 Phase A0 comparisons: passed on Windows and Linux
- hosted exact-head checks and owner review remain required; this PR is a draft until then

Part of docs/superpowers/specs/2026-07-31-pr-remediation-design.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Fix the ROADMAP placeholder PR number**

Update the Task 5 placeholder (`agent/r2-candidate` branch reference) to the
real PR number. This edit touches only `ROADMAP.md` (inside the allowed
gate-only set):

```bash
git add ROADMAP.md
git commit -m "Record R2 candidate PR number

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
env -u GH_TOKEN git push
```

- [ ] **Step 3: Watch the heavy-lane hosted run**

```bash
env -u GH_TOKEN gh pr checks --watch
```

Expected: branch matches `*-candidate`, so the FULL matrix runs, including
both hosted Phase A0 cells at the exact head. All green. Any failure: STOP,
diagnose, report — hosted A0 failures in particular are gate evidence, not
noise (see `benchmarks/README.md` history).

- [ ] **Step 4: Owner review gate**

Ask the owner to review the candidate PR (review focus: the `rag.py` merge
resolution, ROADMAP reconciliation, and the evidence child). Do not merge on
silence.

- [ ] **Step 5: Merge (history-preserving) and verify closures**

```bash
env -u GH_TOKEN gh pr ready
env -u GH_TOKEN gh pr merge --merge
env -u GH_TOKEN git fetch origin
env -u GH_TOKEN gh pr list --state open --limit 50 --json number,title -q '.[] | "\(.number)\t\(.title)"'
```

Expected: #66, #68–#71, #73 auto-marked merged (their heads are ancestors of
the candidate). #67 will NOT auto-close (not an ancestor):

```bash
env -u GH_TOKEN gh pr close 67 --comment "Superseded: the documentation reconciliation was redone against the merged R1+R2 state inside the R2 candidate (see ROADMAP.md). This branch's 2026-07-25 snapshot is stale."
```

Remaining open PRs expected: #72 only (Task 8 closes it), plus #32 only if
Task 2 Step 7 found residue.

- [ ] **Step 6: Confirm the post-merge main run**

```bash
env -u GH_TOKEN gh run list --branch main --limit 1
env -u GH_TOKEN gh run watch <run-id> --exit-status
```

Expected: full matrix green on main at the merged head.

---

### Task 8: Dependency remediation under the domain process

**Files:**
- Modify (via tool only): `requirements*.lock` (regenerated),
  possibly `requirements*.txt` pins if a domain review decides to change a
  direct pin
- Modify (evidence-only commit): the same Phase A0 evidence set as Task 6
  (lock changes invalidate both baselines)

**Interfaces:**
- Consumes: merged main from Task 7; dependabot #72's 17-update list as an
  input checklist; `docs/architecture/decisions/dependency-compatibility-domains.md`
  as the review procedure.
- Produces: one policy-compliant dependency PR, validated and merged; #72
  closed.

- [ ] **Step 1: Close #72 with the policy pointer**

```bash
env -u GH_TOKEN gh pr view 72 --json body -q .body > "$TEMP/dependabot-72-list.md"
env -u GH_TOKEN gh pr close 72 --comment "Closing per repository dependency policy: hash-locked files are regenerated only by tools/refresh_locks.py and reviewed under docs/architecture/decisions/dependency-compatibility-domains.md. These 17 updates are being redone as one policy-compliant refresh PR (tracked in ROADMAP.md)."
```

Keep the saved body as the checklist of expected version movements.

- [ ] **Step 2: Branch and refresh the locks**

```bash
env -u GH_TOKEN git fetch origin
git switch -c agent/dependency-refresh-candidate origin/main
python tools/refresh_locks.py --upgrade
git diff --stat
```

Expected: only `*.lock` files change (plus any tool-managed metadata). If a
`requirements*.txt` direct pin needs to move to satisfy a resolution, that is
a domain decision — record it explicitly in the commit message.

- [ ] **Step 3: Domain-by-domain review**

Read `docs/architecture/decisions/dependency-compatibility-domains.md` and
follow its procedure for each domain the diff touches. Cross-check the diff
against the dependabot checklist from Step 1: every dependabot-listed bump
should be either present in the refreshed locks or explicitly explained
(e.g., held back by a domain constraint). Record the reconciliation in the
commit message body.

- [ ] **Step 4: Local validation with the refreshed locks**

```bash
python -m pip install --require-hashes -r requirements-test.lock
python -m pytest -q
python tools/check_dependency_policy.py
python -m ruff check .
```

Expected: suite passes with the refreshed test lock; policy gate green.

- [ ] **Step 5: Commit the lock refresh (pre-gate checkpoint S')**

```bash
git add -A
git commit -m "Refresh dependency locks across compatibility domains

Redoes dependabot #72's updates through tools/refresh_locks.py --upgrade
under the dependency-domain review procedure. <replace this line with the
domain-by-domain reconciliation recorded in Step 3>

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Regenerate Phase A0 evidence for the new locks**

Lock changes invalidate both baselines (Global Constraints). Repeat Task 6
Steps 1–7 exactly, on this branch: provision BOTH platforms with the NEW
locks, generate both baselines at clean S', commit the evidence child
(allowed paths only), and pass both local 9×5 checks.

- [ ] **Step 7: Publish, validate, review, merge**

```bash
env -u GH_TOKEN git push -u origin agent/dependency-refresh-candidate
env -u GH_TOKEN gh pr create --draft --title "Refresh dependency locks across compatibility domains" --body "One policy-compliant lock refresh replacing dependabot #72, reviewed per docs/architecture/decisions/dependency-compatibility-domains.md, with regenerated paired Phase A0 evidence. Part of docs/superpowers/specs/2026-07-31-pr-remediation-design.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
env -u GH_TOKEN gh pr checks --watch
```

Expected: heavy lane runs (branch matches `*-candidate`); all green,
including `dependency-compatibility.yml`'s resolve/install jobs and
`security.yml` if lock paths are covered. Then owner review, and:

```bash
env -u GH_TOKEN gh pr ready
env -u GH_TOKEN gh pr merge --merge
```

- [ ] **Step 8: Final state audit**

```bash
env -u GH_TOKEN gh pr list --state open --limit 50
env -u GH_TOKEN gh run list --branch main --limit 1
```

Expected: zero open PRs (or only #32 if the owner chose to keep it); latest
main run green. Report the end state against the spec's success criteria:
CI affordable two-lane matrix live, main holds all validated work, ROADMAP
matches reality.

---

## Deviations and escalation

- Any hosted check failure that is NOT the billing annotation is new
  information: diagnose and report before continuing. Do not push fixes into
  a frozen candidate head without regenerating its evidence.
- If `gh pr merge` reports the PR is not mergeable (unexpected base
  divergence), STOP — the no-new-CI premise of Task 2 or the ancestry chain
  of Task 7 is broken; re-verify ancestry and consult the owner.
- If the #44 evidence has aged past retention (~2026-08-24) before Task 2
  runs, trigger a fresh full-matrix `workflow_dispatch` run at
  `agent/r1-cumulative-gate`'s head and require it green before merging.
