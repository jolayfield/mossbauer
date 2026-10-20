# mossbauer-dft

Predict ⁵⁷Fe Mössbauer parameters (δ, ΔE_Q, η; later A-tensors and ZFS) from DFT with ORCA 6.x.
The default installation includes the command line, local browser app, chemical drawing and RDKit conversion.
Review and approve a scientific plan, generate its inputs, then run the calculations yourself or with your own agent.

| module | purpose |
|---|---|
| `orca_input` | ORCA 6.x input decks: `opt`, `freq`, `sp`, `mossbauer_sp`, `highfield_sp` (A, g, ZFS D/E); CPCM; RIJCOSX; broken symmetry (`FlipSpin`/`FinalMs` or `BrokenSym`) |
| `site` | personal YAML cluster profiles, a generic example and Slurm container scripts |
| `orca_parse` | ρ(0), EFG eigenvalues, sign(Vzz), η, ORCA's ΔE_Q, energy, ⟨S²⟩, Mulliken/Löwdin spin on Fe, SCF/termination, method fingerprint, A / g / D,E (stdlib only) |
| `calibration` | δ = α(ρ − C) + β: OLS, leave-one-out MAE, bootstrap prediction intervals, outlier flags, **fingerprint check** |
| `constants` | Q(⁵⁷Fe) = 0.16 b and the EFG→mm/s conversion (ORCA convention: 11.6 MHz per mm/s; ≈1.62 mm/s per a.u. of V_zz) |
| `data` | bundled `calibrations.yaml` (only McWilliams 2017 entries `verified: true`), `training_set.csv` (136 rows), `method_rules.yaml`/`.md`, `refsets/mcwilliams2017.yaml` |
| `plan` | rule matching -> `plan.yaml` (methods + rationale + citations, calibration strategy, jobs, cost model); approval gate |
| `references` | calibration lookup (fingerprint + geometry + ORCA-major protocol match), reference-set import/setup/fit |
| `workflow` | approved plan -> ORCA decks + Slurm + `submit.sh`; outputs -> delta (calibrated, PI), DeltaEQ (signed), eta, <S2>, Fe spin |
| `cli`, `gui/app.py` | `mossbauer plan|approve|setup|analyze|refset|gui`; Streamlit pages New prediction / Plan review / Inputs / Results / References |

## Install for users

Download the wheel from [public releases](https://github.com/jolayfield/mossbauer/releases). Development happens in this repository; installing the app needs only the release wheel. Follow the [Windows and macOS installation guide](docs/releases/INSTALL.md) for uv setup and the pip fallback.

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed, run from the download directory on either platform:

```text
uv tool install --python 3.12 ./mossbauer_dft-0.6.1-py3-none-any.whl
uv tool update-shell
```

Reopen your terminal if uv updated PATH, then run `mossbauer doctor` and `mossbauer gui`. GUI, RDKit and drawing support are included; no extra is needed. The 3D viewer needs network access to load 3Dmol.js. Python 3.10 remains supported; managed Python 3.12 is recommended.

For pip, create a Python 3.12 virtual environment and install the same downloaded wheel with `python -m pip install ./mossbauer_dft-0.6.1-py3-none-any.whl`. The [guide](docs/releases/INSTALL.md#pip-fallback) gives activation-free Windows commands as well as macOS/Linux steps.

## Develop from a checkout

```sh
uv venv --python 3.12
uv pip install -e ".[test]"
```

Run `.venv/bin/python -m pytest -o addopts='' -q` on macOS/Linux, or `.\.venv\Scripts\python -m pytest -o addopts='' -q` on Windows. Pip can install the same editable `.[test]` extra in an activated environment. The old `gui` extra remains accepted for compatibility, but adds nothing to the default install.

Build with `uv build --wheel`. Inspect release assets with `scripts/release_assets.py` and test them with `scripts/smoke_wheel.py --wheel-dir ASSET_DIRECTORY` before publication. CI retains Python 3.10/3.12 behavioral tests and tests the same built wheel on Linux, Windows, Intel Mac and Apple Silicon. See [the testing runbook](docs/testing-runbook.md).

## Configure a personal cluster

Open **Cluster setup** with or without a project. Enter the Slurm partition and absolute ORCA container path from your cluster administrator; account, additional scheduler directives and transfer settings are optional. Validate/preview, save the YAML and choose **Use as default**. Profiles are local under `~/.mossbauer_dft/sites/` (or `$MOSSBAUER_DFT_HOME/sites/`).

```sh
mossbauer site init personal --partition compute --sif /images/orca.sif
mossbauer site validate personal
mossbauer site show personal
mossbauer site list
mossbauer site default personal
```

Selection is explicit `--site NAME`, then `MOSSBAUER_DFT_SITE`, then the configured default. With no choice, the app asks for one. An invalid selected profile produces an error. The bundled `example` profile is a template whose paths must be adapted; no institution-specific default is installed. Slurm with Apptainer/Singularity is supported; module-only ORCA and other schedulers require separate integration.

## Choose how to deliver calculations

All routes start with an approved plan and generated inputs. On **ORCA inputs**, **Download calculation files** works by itself, with no SSH settings or agent required.

| Option | Download contains | You or your agent do next |
|---|---|---|
| Manual files | Decks, scheduler/submission scripts, recorded settings, manifest and harvesting tool | Transfer with your usual tools, inspect commands and run `bash submit.sh` on the cluster |
| Agent instructions | The same files plus optional AGENT.md | Authorize your external agent to submit, record job IDs, monitor and return results for that exact version |
| Bash upload helper | The same files plus optional upload.sh | Inspect the destination/script and execute `bash upload.sh`; then submit separately |

The two assisted options are independent checkboxes. The app never runs uploads or submissions. To use the helper, configure an SSH alias and an existing absolute remote base directory. It uses normal OpenSSH authentication and trusted host keys, verifies local file hashes and creates a new entry/version/hash directory. It refuses an existing destination, including a partial failed transfer; choose a new base for a retry. On Windows it needs Git Bash or WSL, OpenSSH, tar and shasum/sha256sum. Manual transfer remains available without these tools.

```sh
mossbauer project export-inputs --project PROJECT --entry ENTRY --version 1 --out inputs.zip
# Optional additions, separately or together:
mossbauer project export-inputs --project PROJECT --entry ENTRY --version 1 --out assisted.zip --agent --upload
```

Export checks approval, version/plan identity and generated bytes; changed files or old metadata require regeneration. Agent instructions preserve job dependencies, job-ID checkpoints and scientific/resource stop conditions. Return full `<stem>.out` and `.state` files into that version's outputs folder, or run `python3 harvest.pyz harvest-manifest.json . results.json` remotely and import results.json on Results. [Full walkthrough](docs/releases/INSTALL.md).

## Quick start

New to the workflow? Create the guided example: three draft entries (ferrocene, [FeCl4]2-, [FeCl4]-) with literature
structures and experimental values from the bundled `fe1_calib18` reference set. Then follow the guide, which shows for any
project which steps are done, what comes next and why:

```bash
mossbauer project example ~/iron-basics      # or "Create the guided example here" on the GUI's Project page
mossbauer project guide --project ~/iron-basics
```

You review, approve and run the example yourself. No calculations or outputs are included, and the app never submits jobs.

From Python:

```python
from mossbauer_dft.orca_input import mossbauer_method, build_input, BrokenSymmetry
from mossbauer_dft.site import SiteProfile
from mossbauer_dft.orca_parse import parse_orca_output
from mossbauer_dft.calibration import fit_calibration

spec = mossbauer_method("TPSSh", broken_symmetry=BrokenSymmetry("flipspin", (4, 5), 1.5))
deck = build_input("mossbauer_sp", spec, charge=0, multiplicity=6, xyz_file="cmpd4.xyz")   # %eprnmr goes after the geometry

site = SiteProfile.load("personal")
open("orca_job.slurm", "w").write(site.slurm_script())
print(site.sbatch_command("cmpd4", nprocs=16))

res = parse_orca_output("cmpd4.out")
fe = res.fe_sites[0]
print(fe.rho0, fe.vzz, fe.vzz_sign, fe.eta, fe.deq(q_barn=0.16))

cal = fit_calibration([...rho0...], [...delta_exp...], fingerprint=spec.fingerprint())
pred = cal.predict_result(res)        # refuses if functional/basis/Fe-basis differ from the calibration
```

## Workflow (CLI; the GUI calls the same functions)

```bash
mossbauer plan cmpd.xyz --charge -1 --multiplicity 4 --ox-state 1 --name cmpd --out plan/plan.yaml   # draft; prints rule, methods, calibration, jobs, cost
mossbauer plan --update plan/plan.yaml --deq-functional TPSSh --nprocs 8                              # re-derive (resets to draft)
mossbauer approve plan/plan.yaml --by "Your Name"                                                     # records approver, time, content hash
mossbauer setup plan/plan.yaml --out inputs --site personal                                               # refuses draft / edited-after-approval plans
mossbauer analyze plan/plan.yaml outputs/ --csv results.csv                                          # delta (PI), DeltaEQ (sign), eta, <S2>, Fe spin
mossbauer refset list | import | setup | fit                                                          # reference-set calibration (see below)
mossbauer gui [--workdir DIR]
```

Calibration strategies: `lookup` (verified + protocol-matched only: functional, basis, Fe basis, geometry level, ORCA major version;
`--allow-unverified-calibration` / `--allow-protocol-mismatch` add a loud caveat), `compute_refset` (default when nothing matches),
`user_refs` (`--user-refs refs.yaml`). Reference-set route: `refset import ID file.xyz --charge Q` for each compound -> `refset setup plan.yaml --out refset/`
-> run -> `refset fit refset/` (saves a calibration under `$MOSSBAUER_DFT_HOME` or `~/.mossbauer_dft/calibrations`, found automatically by later plans).
The McWilliams 2017 SI geometries could not be downloaded automatically (see `data/refsets/mcwilliams2017.yaml`); structures must be imported. Until then `refset list/setup` use the bundled `fe1_calib18` (starting geometries + caveat flags: ferrocene B3LYP outlier, [FeO4]2- leverage, RDKit-built nacnac geometries). The set has not been computed yet.

## Projects (many compounds, several sittings)

A project is a plain folder holding one entry per compound in one electronic state, with plan versions, generated inputs,
an `outputs/` folder per version and saved results. Status is computed from the folder each time it is opened, so outputs
placed there (by you or an agent) show up without an import step. The GUI opens on the project overview; the CLI offers
the same actions. Folder layout and the agent contract: [`docs/project-layout.md`](docs/project-layout.md).

```bash
mossbauer project init studies/fe1                                                   # make a folder a project
mossbauer project add --project studies/fe1 --structure cmpd.xyz --charge -1 --multiplicity 4 --ox-state 1 --name cmpd
mossbauer project batch template --project studies/fe1 --structures si.xyz --out sheet.csv   # one row per SI structure; fill charge, multiplicity, hints
mossbauer project batch preview --project studies/fe1 --structures si.xyz --csv sheet.csv    # ready / skipped / blocked per row, writes nothing
mossbauer project batch create --project studies/fe1 --structures si.xyz --csv sheet.csv     # draft entries for ready rows (never approved)
mossbauer project preset save --project studies/fe1 --name tpssh-hf --deq-functional TPSSh --highfield   # the group's protocol, by name
mossbauer project add --project studies/fe1 --structure cmpd2.xyz --charge -1 --multiplicity 4 --preset tpssh-hf
mossbauer project approve --project studies/fe1 --all-drafts --by "Your Name"       # each plan sealed individually
mossbauer project setup --project studies/fe1 --entry cmpd_qm1_m4                    # inputs into v1/inputs/
mossbauer project status --project studies/fe1 [--json]                              # draft / approved / inputs written / outputs found n of m / analyzed
mossbauer project analyze --project studies/fe1 --entry cmpd_qm1_m4                  # saves v1/results/analysis.json
mossbauer project analyze --project studies/fe1 --entry cmpd_qm1_m4 --trial --calibration ID   # try a calibration; prints JSON, saves nothing
mossbauer project import old_workdir/                                                # single-plan GUI work dir -> one-entry project (originals kept)
```

**Batch intake** (GUI page, or `mossbauer project batch`) brings a paper's SI into a project in one sitting: concatenated xyz blocks, multi-block CIFs, multi-record SDFs or several files become a grid with one row per compound-state. Fill or import the grid as CSV (it records the batch-wide method options), preview every row, then create draft entries for the rows that pass. Rows already in the project are skipped, rows sharing a structure are linked as a state family, and nothing is approved. Single-structure inputs now refuse a file that holds several structures instead of silently taking the first.

**Method presets** keep a study's protocol (functionals, geometry level, calibration route, highfield, resources) in the project as named presets. Choose one for a new entry or a batch, or apply it to an existing entry on Plan review (or `project edit --preset`), which always shows the revision preview before a new draft is saved. Presets never hold broken symmetry or reference structures, and plans made from them are ordinary plans.

**Calibration status.** Every saved analysis says whether its isomer shifts are a *calibrated* prediction (verified, protocol-matched literature calibration), a *project-fit* prediction, *exploratory* (unverified or protocol-mismatched constants used by override) or *uncalibrated* (ρ(0) only). Results, study comparisons and reports show it, and agreement statistics never pool different statuses. A **trial analysis** on Results (or `analyze --trial`) tries another calibration without writing anything.

## Study workbench

The GUI's **Study** page compares exact saved versions and downloads CSV, evidence JSON, and an offline report ZIP (HTML + CSV + JSON). It never recalculates results. Protocols and calibrations form separate agreement cohorts. Stale, partial, modified, training-set and ambiguous multi-Fe evidence stays visible with flags; unknown training membership cannot establish held-out validation.

On **Plan review**, preview edits to see scientific changes, jobs, cost and blockers before saving a new version or state entry. A stale preview cannot save. Approval remains a separate action.

**Calibration** creates project-local reference workspaces, copies custom or bundled reference structures, prepares inputs from an approved target, and fits returned outputs with written exclusion reasons. Inspect residuals and leave-one-out errors before explicitly selecting an immutable fit on Results. References freeze after preparation; create another workspace to change them. Fits remain unverified and are not installed globally.

**States** previews candidate multiplicities/charges and their added workload before creating independent draft entries. Families link states explicitly. Relative energies require matching composition, charge and full protocol. A cohort identifies its lowest computed state only when every family member is represented; the default near-degeneracy tolerance is 1 kJ/mol. This does not assign an experimental ground state.

```bash
mossbauer project compare --project studies/fe1 --select cmpd_qm1_m4:1 --json
mossbauer project report --project studies/fe1 --out report.zip --title "Fe study"
mossbauer project edit --project studies/fe1 --entry cmpd_qm1_m4 --multiplicity 6 --preview
mossbauer project calibration create --project studies/fe1 --entry cmpd_qm1_m4 --workspace refs
mossbauer project calibration add --project studies/fe1 --workspace refs --id ref1 --structure ref1.xyz --charge -1 --multiplicity 4 --delta-exp 0.7
mossbauer project calibration prepare --project studies/fe1 --workspace refs
mossbauer project calibration fit --project studies/fe1 --workspace refs --exclusions '{"ref1":"documented reason"}'
mossbauer project family preview --project studies/fe1 --entry cmpd_qm1_m4 --candidates '[{"multiplicity":4},{"multiplicity":6}]'
# Generate with the reviewed source hash; all generated plans remain drafts.
mossbauer project family generate --project studies/fe1 --entry cmpd_qm1_m4 --family cmpd --candidates '[{"multiplicity":4},{"multiplicity":6}]' --expect-hash HASH
mossbauer project family compare --project studies/fe1 --family cmpd
```

Every newly generated version's inputs include `harvest.pyz` and `harvest-manifest.json`. On the cluster, run the following with standard-library Python; no package installation is needed:

```bash
python3 harvest.pyz harvest-manifest.json . results.json
# Back on the workstation (or upload results.json on Results):
mossbauer project import-results --project studies/fe1 --entry cmpd_qm1_m4 --version 1 --bundle results.json
mossbauer project analyze --project studies/fe1 --entry cmpd_qm1_m4 --version 1
```

For SSH copy-back on macOS/Linux, first create the bundle on the cluster, then fetch it using an existing SSH host alias:

```bash
mossbauer project fetch-results --project studies/fe1 --entry ENTRY --version 1 --host my-cluster --remote-path /work/my-run/results.json
```

The same action is on **Results → Fetch compact results over SSH**. Each click/command fetches and validates one file, with a 5 MB limit and 30-second timeout. Set up keys and host trust outside the app using normal SSH tools; authentication never prompts here and unknown host keys are refused. Configure ports, proxy hosts and identities in your SSH configuration. No credentials or remote locations are saved in the project. Invalid data, failed connections and conflicts leave local evidence unchanged; `--replace` explicitly archives differing compact records, while raw-output conflicts remain blocked. Windows users can use the existing file import.

Imports validate all records before writing, preserve raw-output precedence and refuse conflicts. Explicit `--replace` archives differing compact evidence. Compact results retain the remote source hash and parser version, but cannot verify raw bytes that were not returned. Full `.out` analysis remains available. Slurm scripts propagate runtime, termination and optimization failures so `afterok` dependencies stop.

### Getting jobs back, and running the failures again

Each job writes `<stem>.state` in its submit folder when it starts and when it finishes (return code and reason, or the signal that stopped it). `harvest.pyz` carries those records in the bundle, and the status scan reads them from `outputs/` or `inputs/`, so Results can tell a failed job from one that never came back. **Results → Return desk** (or the CLI) handles the rest:

```bash
# Outputs the job script copied back into inputs/: copy them (and their state files) into outputs/, never overwriting.
mossbauer project adopt --project studies/fe1 --entry ENTRY
# Write inputs/resubmit_failed.sh for the failed jobs plus every job depending on them; per-job resources are optional.
mossbauer project resubmit --project studies/fe1 --entry ENTRY --time ENTRY_opt=96:00:00 --mem ENTRY_opt=128
```

Like `submit.sh`, the resubmission script is displayed and never run by the app. A job killed outright (for example out of memory) cannot record its end, so "started, no finish record" means still running or killed. It is never treated as success.

### Acting on a whole series

Select rows in the Project overview and use **Act on N selected** to write inputs, analyze, re-analyze saved results, or apply a method preset to all of them at once. From the CLI:

```bash
mossbauer project bulk setup --project studies/fe1 --all            # every approved entry
mossbauer project bulk analyze --project studies/fe1 --entry A --entry B
mossbauer project bulk reanalyze --project studies/fe1 --all        # refresh calibration status and trust flags
mossbauer project bulk preset-preview --project studies/fe1 --preset group-std --all   # prints a pin
mossbauer project bulk preset-apply --project studies/fe1 --preset group-std --all --expect-hash PIN
```

Each entry is handled on its own and reported as done, skipped (with the reason) or failed. Applying a preset makes new draft versions that each need approval.

Each report ZIP also carries supporting-information drafts (`si_table.csv`, booktabs `si_table.tex`, `methods.txt`, `references.bib`) built from the same snapshot, and a `MANIFEST.json` of member hashes. Anyone holding the ZIP can re-check it without the project:

```bash
mossbauer report verify study-report.zip
```

This checks the member hashes, that the CSV and JSON agree, that every δ follows from its recorded ρ(0) and calibration constants, and that no value is called calibrated while it carries a blocking alert. Raw outputs are not in a report, so their contents cannot be re-checked: the source hashes only identify them. The same check is on **Study → Check a report ZIP**.

Reports contain only allowed scientific metadata and filenames, with hashes identifying original evidence; raw outputs and arbitrary metadata are omitted. HTML is self-contained, escapes supplied text and loads no external assets. Review its scientific interpretation before publication.

### A recorded run for the teaching example

An instructor who has run the example once on a real cluster can hand learners that run, so students without cluster access still reach the analysis half of the exercise:

```bash
# Instructor: in the example project, after approving, running and returning every v1 plan
mossbauer project example-pack --project iron-basics --out iron-basics-recorded.zip --recorded-by "Dr. Name" --site personal --program "ORCA 6.1.0"
# Learner
mossbauer project example my-example --recorded-pack iron-basics-recorded.zip
mossbauer project approve --project my-example --entry ENTRY --by "Student Name"     # after reviewing the plan
mossbauer project import-recorded --project my-example --entry ENTRY
```

The learner's draft plans are exactly the plans that were run; the instructor's approval is not shipped, so approving is still the learner's step. The recorded evidence is imported only for a plan approved unchanged, goes through the normal compact validation, and is labelled "recorded real calculation by …". Edit a plan and it needs its own run. No recorded pack is bundled with the app: it has to be real data from a real cluster.

## Rules built in

* MN15 (or any functional with >40 % exact exchange) is refused for delta/DeltaEQ property calculations; it is allowed for geometry only.

* `%eprnmr` is always written **after** the coordinates (ORCA otherwise stops with "no coordinates have been read").
* MN15 has no ORCA 6.1 simple keyword; it is set through libxc in `%method`.
* A calibration is only valid for the method/basis/Fe-basis/grid it was fitted with. `Calibration.predict` raises
  `FingerprintMismatchError` on a functional/basis/Fe-basis mismatch (or warns loudly with `strict=False`), and warns on
  grid/RI/solvent/ORCA-version differences or when ρ(0) lies outside the calibrated range.
* Two-point calibrations are exactly determined: no LOO error or prediction interval exists, and a warning is attached.

## Status / caveats

* Input decks reproduce all 34 ORCA decks that ran successfully on the project cluster (token-equivalent tests).
* The A-tensor / g-tensor / D,E parsers follow the documented ORCA 6.1 layout and are tested on **synthetic** excerpts only.
* `highfield_sp` syntax follows the ORCA 6.1 manual; it has not been run on the cluster yet.
* Licence: MIT is a placeholder (see `LICENSE`); change it as appropriate.
