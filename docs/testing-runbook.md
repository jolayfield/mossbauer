# Testing

Run the full suite from the repository root:

```bash
.venv-gui/bin/python -m pytest
```

The environment needs `.[test,gui]` for Streamlit AppTest and RDKit coverage. CI installs those extras and tests Python 3.10 and 3.12. Tests use temporary projects and an isolated application home; they do not submit cluster jobs.

## Approval after an external edit

Last verified: 2026-10-17 (local Streamlit AppTest).

```bash
.venv-gui/bin/python -m pytest tests/test_cli_gui.py tests/test_project_approve.py
```

The review-page regression renders a draft, changes its content or creates a newer version outside the app, then clicks the previously rendered approval button. Neither version may be sealed, and the page must show the rejection reason. Streamlit callbacks must carry the version and hash from the prior render: reading a plan in the click-triggered rerun can approve content the user has not seen. A rejected approval can leave the selected version read-only, so its rejection message must also render for older versions.

## AppTest dataframe selections

Streamlit 1.65 (installed by the Python 3.10 CI job) renders dataframes as non-widget test elements and omits their selection state from subsequent widget reruns. The `_app` test helper forwards selection state in the widget-state protobuf, matching the browser's retained selection. Keep the batch-approval assertions after changing the approver name: the preview must retain two eligible plans and one blocked plan, and the resulting seals must record that name.

## Study workbench N1–N6

Last verified: 2026-10-17. Focused library/CLI checks:

```bash
.venv-gui/bin/python -m pytest tests/test_study.py tests/test_revision.py tests/test_calibration_workspace.py tests/test_families.py tests/test_compact.py tests/test_report.py tests/test_site.py
```

Run `tests/test_cli_gui.py` for saved-version selection, preview/save callbacks, workspace reference previews, and candidate generation. Compact imports must reject the entire bundle before changing any output; the portable archive must also run under `python -S`. Do not weaken full/compact row parity or scheduler exit-status assertions.

Browser QA used installed `agent-browser`, headless Chrome, a temporary Streamlit server and synthetic ORCA excerpts. Inspect Study at 1440×900, 768×1024 and 390×844, including export controls and horizontal table scrolling. The fixed toolbar must leave the first heading visible. Review the multiplicity-change preview without saving, fit synthetic reference outputs, inspect an incomplete state-family cohort, upload/import a compact bundle, and download a report ZIP. Open its HTML with HTTP blocked; verify values, warnings and provenance, and compare CSV/JSON row counts. Browser console errors: none observed. Real cluster execution remains outside this QA.

Review coverage and acceptance trace: [study workbench review](reviews/2026-10-17-study-workbench.md). Representative screenshots: [desktop](testing-artifacts/study-workbench/study-desktop.png), [mobile](testing-artifacts/study-workbench/study-mobile.png), [tablet](testing-artifacts/study-workbench/study-tablet.png), and [offline report](testing-artifacts/study-workbench/report-desktop.png).

## Batch intake

Last verified: 2026-10-18 (browser at desktop and 375 px widths; the CSV re-import part by AppTest).

Library, CLI and AppTest checks:

```bash
.venv-gui/bin/python -m pytest tests/test_batch_sources.py tests/test_batch.py tests/test_cli_batch.py tests/test_cli_gui.py
```

AppTest has no `data_editor` element, so GUI tests set the canonical rows in session state; the editable grid itself is checked in a browser. Browser check: open a project, go to **Batch intake**, upload a multi-block xyz, edit charge and multiplicity cells, add a second file and confirm the earlier edits survive, preview, create, download the CSV, reload the page, import the CSV with the same structures and confirm every row previews as skipped. Repeat at a mobile width. The grid must keep earlier cell edits when a later edit triggers a rerun: the editor is fed the same base rows for its whole key generation, and row status is listed under the grid rather than in a column that changes as cells are edited.

## Project folder browser

Last verified: 2026-10-18. `tests/test_cli_gui.py -k folder_browser` covers navigating to an existing project, cancelling, and choosing an empty folder without implicitly creating a project. Browser QA at 1440×900 and 390×844 verified the Project page's Browse folders button, directory navigation and opening a selected project. Folder listings refer to the computer running Streamlit; they do not upload a browser client's directory. Existing typed-path and explicit create/import actions remain available.

## Guided teaching example

Last verified: 2026-10-18. Run `tests/test_tutorial.py tests/test_cli_gui.py` (52 checks passed locally). From Project, enter a new temporary folder, click Open, then **Create the guided example here**. Expect three draft entries and an expanded guide pointing to review and approval; no ORCA inputs or outputs exist. Existing projects and nonempty folders must be preserved. The same status-derived guide is available with `mossbauer project guide --project DIR`.

Browser QA used a temporary server on port 8503 and a temporary example folder. Verified creation, three draft rows, the next-step explanation and CLI snippet at 1440×900 and 390×844; no browser errors observed. The guide must keep incomplete output sets at Run and direct modified plans back to approval. When every entry is unreadable, show the repair warning without claiming any finished steps. No real calculations were run.

## SSH compact return

Last verified: 2026-10-18 (final full suite: 406 passed). Run `tests/test_remote.py tests/test_compact.py` for transport and scientific evidence boundaries and `tests/test_cli_gui.py -k fetches_remote` for GUI/backend parity. Transport tests substitute local Python processes for SSH and cover nonzero exit, empty data, oversize stdout/stderr, timeout cleanup, command quoting and host rejection. They never contact a real cluster. Compact tests preserve raw-output precedence and explicit replacement history.

For end-to-end browser QA, create a temporary approved fixture using the test helpers, harvest its synthetic property outputs into a JSON bundle, then remove those fixture outputs. Launch an isolated Streamlit server with a task-specific directory prepended to PATH containing a fake executable named `ssh` that prints that bundle. Never change the user's normal SSH configuration. On Results, expand **Fetch compact results over SSH**: the button starts disabled; supply a fixture alias and absolute filename, then fetch. Expect two imported property jobs, the missing optimization count, the compact provenance warning and complete property rows. Repeating the action is idempotent; an invalid host must show an error without invoking the fake transport. Existing file upload remains available.

Verified the success path at 1440×900 and 390×844 with no observed browser console errors. Allow the responsive sidebar transition to settle before capturing screenshots. This verifies app behavior, not real cluster authentication or network access. Shut down only the temporary QA servers and browser sessions after use; preserve the user's app on port 8502. Real connections require existing keys and trusted host records, and the remote bundle must already exist.

## Personal cluster setup

Last verified: 2026-10-18. Run `tests/test_cli_site.py tests/test_site.py` for profile validation, default precedence and preserved resubmission settings. The shared `_home` fixture explicitly configures the bundled generic example; tests for an unconfigured installation use their own empty home instead. The example is a template and its container path is not a working cluster installation.

GUI coverage: `tests/test_cli_gui.py -k 'cluster_setup or inputs_require_site'` and `tests/test_gui_pages.py tests/test_guide_navigation.py`. Verify Cluster setup before opening a project, profile preview/save, explicit overwrite, an outside edit after opening, default selection and the Inputs selector with no default. Preview must not write a file; missing SSH details must not block input generation.

Browser QA used an isolated app home under `/tmp/mossbauer-cluster-qa-home` on port 8503. At 1440x900, 768x1024 and 390x844, labels and controls remained readable without clipping. Saved a profile with blank SSH fields, selected its default in the browser and confirmed the CLI resolved the same profile. No browser errors observed. Streamlit scrolls its `[data-testid=stMain]` pane; reset that pane for top-of-page screenshots. Restart the QA server after editing imported view modules. Preserve the user's app on 8502; no real cluster connection was tested.

### Verified calculation exports

`tests/test_handoff.py` covers manual/agent ZIP parity, plan approval and byte-integrity guards, symlink rejection, unrelated-output exclusion and quoted instruction-like data. `test_inputs_manual_download_and_optional_agent_handoff` verifies the GUI and stale-file error. For browser QA, generate the `iron-basics` example in an isolated temporary project, approve its entries, generate inputs with `--site example`, and open Inputs for an exact entry. Toggle the agent checkbox and expand its preview. Desktop 1440×900 and mobile 390×844 were inspected without clipping or browser errors. No transport or scheduler was used. The temporary QA server was stopped; port 8502 was preserved.

The upload-helper tests run generated scripts through a fake SSH executable, including paths with spaces/quotes, existing destinations, missing inputs/tools, changed bytes and failed transport. They execute under macOS `/bin/bash` 3.2.57 locally. No real SSH endpoint is contacted. Browser QA inspected the optional helper destination and collapsed script preview at 1440×900 and 390×844; the download stays visible below the destination. Use the profile recorded with the generated inputs for transfer settings, or CLI `--transfer-site NAME`; changing the form selection alone does not change an existing bundle.

### Complete wheel installation and diagnostics

Build with `python -m build --wheel`. Install the wheel with default dependencies into an isolated uv tool directory using managed Python 3.12, outside the checkout; do not pass the gui extra. Run `mossbauer --version`, `mossbauer doctor --json`, import GUI views and bundled data, and perform an Fe SMILES conversion. This passed locally on macOS arm64 with Python 3.12.13. The installed app opened Cluster setup and the drawing editor; applying an actual drawn atom returned MOLFILE data to the app. Ketcher 0.0.1 emitted a nonblocking eventBus initialization/cleanup console error; controls and Apply remained functional. Keep that upstream browser limitation separate from Python installation health. Doctor failure tests cover broken imports, invalid defaults and an unwritable store. Windows and Intel macOS smoke evidence belongs to the release CI unit.

### Public installer assets

`scripts/release_assets.py` inspects actual wheel members, metadata/version, known private identifiers, bundled-site allowlist and required resources, then writes only five selected assets and their checksums. It must refuse an existing output directory. A local first scan caught a deleted private YAML still present under build/lib; removing that stale generated copy and rebuilding produced a passing asset set. Do not assume source deletion sanitizes an existing wheel.

`scripts/smoke_wheel.py --wheel-dir ASSETS` checks the wheel checksum, uses the documented default uv tool install with managed 3.12, and runs outside the checkout with a temporary user home. It validates metadata, bundled data/GUI views, a real Fe conversion, approved input export, LF scripts and an actual GUI health endpoint, then terminates its own process group (Windows uses taskkill /T for its owned launcher). Local macOS arm64 smoke passed; cross-platform results must come from the wheel-smoke CI jobs. The public repo checkout is prepared separately and contains only README.md, INSTALL.md and LICENSE. The release workflow never uploads a private source archive or local project/user-store data.

### Installation and delivery documentation checks

The student guide and README commands were checked against `site --help` and `project export-inputs --help`. The documented route uses the same wheel/default dependencies and managed Python 3.12 exercised by the installed-wheel smoke. Profile creation/default selection and all delivery routes were verified in their owning units; documentation changes do not rerun scientific computations. Public documentation scanning and release-selection tests passed after the guide updates. The existing license text is copied unchanged. Stable publication uses the exact final CI artifact only after all platform checks are green; first publish as a prerelease, verify anonymous access/install, then mark stable. Cross-repository automation needs a contents-write token scoped to the public destination; manual publication from the inspected/tested artifact avoids storing that token in the development repository.

Additional clean-install verification: the final local wheel installed with plain `python -m pip install WHEEL` in a new Python 3.12 virtual environment, using default requirements and no source checkout. Doctor, Fe conversion, approved file/agent export, LF scripts and the loopback GUI health check passed. Separately, all 18 bundled fe1_calib18 geometries loaded through the installed app’s reference/structure APIs outside the checkout. These checks supplement the recommended uv route; no real cluster was contacted.

Installer CI Windows encoding check: Windows' default cp1252 failed while loading the bundled UTF-8 method rules during approved input generation. Reference YAML, CSV and Markdown loaders now select UTF-8 explicitly. `tests/test_data_loader.py` simulates cp1252 and verifies Unicode content through all resource loaders; platform wheel smoke must also pass without a UTF-8 environment override.

The next Windows smoke progressed to plan persistence and exposed the same locale dependence while writing δ/Δ symbols. Plan/structure persistence, atomic project text and generated input README text now use UTF-8. The project regression simulates cp1252 through create/add/approve/generate/export, preserving Unicode project names, approvers and structure comments and checking plan integrity. This tests the complete failing workflow locally rather than relying only on an individual loader.

Structure-file portability: XYZ file loading (both entry points), CIF file loading and XYZ writing use UTF-8; generated XYZ files use LF. The structure-source tests simulate Windows cp1252 with Unicode filenames, scientific comments and CIF metadata. The XYZ write/read case preserves the structure and scientific plan hash at a fixed plan creation time. ASCII, multi-structure guards and bundled-reference geometry tests run alongside these regressions. The installed-wheel smoke also writes and reloads a Unicode-named XYZ with a scientific comment and checks LF bytes on all four native platforms.

Calculation completion: `tests/test_project_status.py` exercises real parsing, harvesting and compact import for failed or absent final SCF convergence, error termination and failed or absent optimization convergence. Every case checks incomplete counts, read-only scanning, guide routing before and after saving a partial analysis, exclusion of failed property rows and the generated resubmission selection. The positive case includes an earlier failed SCF followed by a converged final SCF. Synthetic optimization outputs in the shared test helper now explicitly report geometry convergence. Run the status, compact, return-desk, tutorial and guide-navigation tests together; the full suite also checks CLI and GUI consumers. No real cluster calculation or submission is used.

Report numeric validity: `tests/test_report_verify.py` replaces numeric values inside generated report ZIPs and updates the member hash, so a hash mismatch cannot mask a broken scientific check. Cases cover NaN, positive/negative infinity, an overflowing exponent and arithmetically consistent booleans in δ, ρ(0), α, C and β; finite-input float/integer arithmetic overflow; valid uncalibrated rows; and CLI JSON failure with exit code 1 and unchanged archive bytes. Run alongside `tests/test_report.py` to retain report-generation, privacy and presentation coverage. Tests use only temporary synthetic projects and in-memory archives.

CSV/JSON report agreement: The report-verification tests change CSV values, entry/job identities, columns, row multiplicities and row widths while updating member hashes. Missing/extra/duplicate columns, missing/extra cells, replacement with a duplicate row, malformed quoting and an unexpected header on an empty report must fail. Reordering rows/columns and quoting every field with LF record endings must pass, including Unicode, commas, quotes and multiline cell text. CLI verification exits 1 for a rehashed inconsistent table and leaves archive bytes unchanged. Scientific-fault tests keep their tables aligned; finite and boolean cases assert table agreement separately from numeric failure.
