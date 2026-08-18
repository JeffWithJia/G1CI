---
name: sync-personal-terminal-robot-record
description: Selectively sync robot-record functionality from Personal Terminal into this G1CI operator console while preserving and documenting downstream-only constraints. Use whenever reviewing, porting, copying, or updating robot-ledger UI, schemas, reports, API integration, or related assets from Personal Terminal, or when a new local divergence or constraint is discovered.
---

# Selectively Sync The Robot Record

Treat `/home/jeff/coscene/code/codex/Personal terminal/src/robot-record/` as the upstream implementation and this repository as an adapted downstream consumer. Port useful changes without replacing this repository's runtime, product scope, or integration behavior.

Before comparing or editing files, read [downstream-differences.md](references/downstream-differences.md) completely. Keep that reference current whenever an intentional local difference is added, changed, or removed.

## Workflow

1. Read this repository's `AGENTS.md`, inspect `git status --short`, and preserve unrelated user changes.
2. Compare matching files individually with `git diff --no-index -- <upstream-file> <downstream-file>`. Do not infer intent from timestamps or copy an entire directory.
3. Classify each upstream change as:
   - **Direct port**: runtime-neutral calculation, normalization, schema, or report logic.
   - **Adapt**: useful behavior that touches host messaging, persistence, API direction, project controls, layout, or assets.
   - **Skip**: Electron-only lifecycle, token-provider controls, multi-project product behavior, generated output, or runtime data that does not belong here.
4. Before code edits, update `references/downstream-differences.md` for every newly discovered intentional divergence. Edit the existing rule instead of keeping a chronological changelog.
5. Apply only the required hunks. Never replace downstream `app.js`, `index.html`, `styles.css`, or `project-data.js` wholesale.
6. If the upstream data shape changed, trace the change through browser normalization, Python validation, persistence, remote API parsing, migrations, and inventory status projection before editing.
7. Recompare the affected files after editing and confirm every remaining difference is either documented or an explicitly deferred upstream change.

## Porting Rules

- Prefer direct ports in `record-data.js`, `problem-data.js`, and `weekly-data.js`, but review every diff and keep their APIs compatible with downstream callers.
- Adapt changes in `app.js`, `index.html`, `styles.css`, and `project-data.js` around the downstream rules. Do not reintroduce upstream project creation, switching, deletion, API-token generation, or Electron-only host actions.
- Preserve `operator_console/operator_console.py`, `operator_console/templates/index.html`, and `operator_console/static/style.css` integration unless a compatible change is deliberately required.
- Preserve `operator_console/static/robot-record/lucide.js`; it is a downstream-local static dependency even though it is absent upstream.
- Do not copy `data/robot-record.json`, build output, caches, logs, credentials, or API tokens as part of a code sync. Refresh live ledger data through the supported API/UI flow.
- Do not edit Personal Terminal while performing a downstream sync unless the user explicitly requests an upstream change too.
- Do not overwrite `inventory.ini` from ledger data. Inventory remains the Ansible host source; ledger availability is only merged into its presentation.

## Updating Constraints

Record stable product or integration differences, not incidental line diffs. For each rule, state:

- what downstream behavior must remain;
- why it differs from upstream;
- which files enforce it;
- how to verify it after a sync.

If intent is uncertain, leave the code unchanged and report the exact upstream hunk and downstream behavior that need a user decision.

## Verification

Run checks proportional to the touched files:

```bash
node --check operator_console/static/robot-record/app.js
node --check operator_console/static/robot-record/project-data.js
node --check operator_console/static/robot-record/record-data.js
node --check operator_console/static/robot-record/problem-data.js
node --check operator_console/static/robot-record/weekly-data.js
python3 -m py_compile operator_console/operator_console.py
git diff --check
```

Also smoke-test the embedded **机器人台帐** module when UI, host messaging, persistence, remote API, or inventory integration changed. Confirm load, edit/save, page reload, API refresh, and robot-list status refresh as applicable. Run `ansible-playbook --syntax-check <playbook>` only when an Ansible file was changed.
