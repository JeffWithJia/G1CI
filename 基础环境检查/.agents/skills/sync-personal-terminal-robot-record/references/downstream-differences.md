# Downstream Differences

Use this file as the current contract for the G1CI copy of the Personal Terminal robot-record module. Update the relevant rule as soon as an intentional difference changes; do not turn this into a chronological sync log.

## Ownership Map

| Area | Upstream behavior | Required downstream behavior | Main files |
| --- | --- | --- | --- |
| Runtime host | Electron/Personal Terminal host bridge | Browser iframe hosted by the Python operator console | `operator_console/templates/index.html`, `operator_console/static/robot-record/app.js` |
| Persistence | Personal Terminal project store and Electron handlers | `data/robot-record.json` through `/api/robot-record`, with Python validation, locking, and atomic replace | `operator_console/operator_console.py` |
| Project scope | Create, switch, rename, and delete up to 50 projects | Present one active operational board; allow renaming only while retaining portfolio v7 compatibility | `index.html`, `app.js`, `project-data.js` |
| Project API | Publish per-project read-only API and manage/rotate tokens | Consume the Personal Terminal API and replace the active local board data after validation | `app.js`, `operator_console.py`, host bridge in `templates/index.html` |
| Robot inventory | Ledger owns its own robot list | Keep `inventory.ini` as the Ansible host source and overlay the latest ledger availability by robot code for display | `operator_console.py` |
| Icons | Resolve Lucide from Personal Terminal dependencies | Serve a repository-local `lucide.js`; do not require `node_modules` at runtime | `index.html`, `lucide.js` |

## Runtime And Messaging

- Keep the ledger inside `#robotRecordFrame` at `/static/robot-record/index.html`.
- Keep the `postMessage` channel named `robot-record` and restrict downstream actions to `load`, `save`, and `remote-load` unless both iframe and host are deliberately extended together.
- After a downstream save, keep the host's robot list refresh so ledger availability changes appear in the main console.
- Do not port Electron-only actions such as `api-info`, `api-rotate`, `api-delete`, or `copy-text` without designing an explicit Python-host equivalent.
- Keep standalone-browser fallback behavior only where it does not weaken the embedded host flow.

Verification: load the module in the operator console, edit a record, reload the page, and confirm the saved data and the main robot status both refresh.

## Single-Board Product Scope

- Keep the visible board title default as `湾谷机器人状态看板`.
- Keep title renaming, but do not expose upstream project selection, creation, or deletion controls.
- Continue accepting portfolio version 7 and legacy flat data so the downstream can consume upstream data safely. The single-board UI is a product constraint, not permission to collapse or destructively rewrite compatible stored data.
- On remote refresh, replace only the active downstream project's `data`; keep the downstream project identity and local board name.

Verification: confirm there is no project switch/create/delete UI, renaming persists, and an API refresh does not rename the local board.

## API Direction And Security

- Treat Personal Terminal as the API provider and this project as the API client.
- Keep API URL and key entry, local browser configuration, explicit refresh, remote project name, and data timestamp in the downstream dialog.
- Whenever the operator opens the robot-record module, automatically refresh from the configured project API before presenting the data; if credentials are absent or refresh fails, preserve the last local data and expose a readable status instead of clearing the board.
- Preserve server-side validation: only HTTP/HTTPS without embedded credentials, no redirects, no query/fragment, exact `/api/v1/robot-record/projects/<project-id>` path, 32-128 character key format, bounded timeout, disabled proxy inheritance, `apiVersion: v1`, and a 20 MB response limit.
- Keep secrets out of source files, logs, Markdown, and committed data. Never copy a live token from Personal Terminal into this repository.
- Preserve locked reads and temporary-file plus `os.replace` writes for local ledger persistence.

Verification: open the robot-record module repeatedly and confirm each opening issues a fresh API request; also test a valid manual refresh and representative missing credentials, invalid URL, invalid key, redirect, authentication failure, and oversized/invalid payload cases when this path changes.

## Inventory Boundary

- Do not generate or overwrite `inventory.ini` from the ledger robot list.
- Match ledger records to inventory using the robot code/ID only for presentation metadata such as `ledger_availability`.
- Keep Ansible connection fields and deployment targeting owned by `inventory.ini`.
- Preserve the latest non-future daily ledger availability lookup used by the main console.

Verification: confirm inventory hosts and connection fields remain unchanged while the displayed ledger status follows the latest applicable daily record.

## UI And Assets

- Keep the ledger as a full-width module only when its main console tab is active.
- Preserve the downstream compact header: board title plus rename control, project API refresh, date, taxonomy, and robot-list actions.
- Keep downstream dialog sizing and responsive adjustments unless an upstream improvement is intentionally adapted and visually checked in the iframe.
- Preserve the local `lucide.js` asset and its relative script path. Upstream's `../../node_modules/...` path is invalid in the Python static server.

Verification: inspect desktop and narrow viewport layouts inside the operator console; confirm icons load with no dependency on Personal Terminal's `node_modules`.

## Shared Logic

Treat `record-data.js`, `problem-data.js`, and `weekly-data.js` as shared-domain candidates. Port upstream fixes when they remain runtime-neutral, including classification, normalization, aggregation, and report calculations. Reconcile function signatures and pass the active `classificationCatalog` wherever upstream logic requires it.

Do not label every temporary mismatch as a downstream constraint. A mismatch belongs here only when this project intentionally must behave differently.
