# Install Mössbauer DFT

Download `mossbauer_dft-0.6.1-py3-none-any.whl` and `SHA256SUMS` from [the v0.6.1 release](https://github.com/jolayfield/mossbauer/releases/tag/v0.6.1). No GitHub login or source checkout is required. Dependencies download from PyPI during installation.

Compare the downloaded wheel's SHA-256 digest with its line in SHA256SUMS. On macOS:

```sh
shasum -a 256 ./mossbauer_dft-0.6.1-py3-none-any.whl
```

On Windows PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 .\mossbauer_dft-0.6.1-py3-none-any.whl
```

## Recommended: uv and managed Python 3.12

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), reopen your terminal, then run this from the download directory on Windows PowerShell, macOS or Linux:

```text
uv tool install --python 3.12 ./mossbauer_dft-0.6.1-py3-none-any.whl
uv tool update-shell
```

Reopen the terminal if uv updated your PATH, then:

```text
mossbauer --version
mossbauer doctor
mossbauer gui
```

The GUI, molecular drawing and RDKit are included. The 3D viewer loads 3Dmol.js from a CDN and needs network access. Input generation and analysis can operate locally offline once installed.

## pip fallback

Use Python 3.12, create a virtual environment and install the same wheel. On macOS/Linux:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install ./mossbauer_dft-0.6.1-py3-none-any.whl
mossbauer doctor
mossbauer gui
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install ./mossbauer_dft-0.6.1-py3-none-any.whl
.\.venv\Scripts\mossbauer doctor
.\.venv\Scripts\mossbauer gui
```

Python 3.10 remains supported; managed 3.12 is the recommended setup. Intel macOS uses a compatible RDKit version automatically. Supported platform smoke checks run against the exact release wheel.

## Configure your cluster

Open **Cluster setup** without opening a project. Enter a profile name, Slurm partition and absolute ORCA container path supplied by your cluster administrator. Account and additional scheduler directives are optional. Preview, save and choose **Use as default**.

Personal YAML profiles live under `~/.mossbauer_dft/sites/`, or `$MOSSBAUER_DFT_HOME/sites/` when set. CLI equivalents:

```sh
mossbauer site init personal --partition compute --sif /images/orca.sif
mossbauer site default personal
mossbauer site validate personal
```

The bundled `example` profile is a template; its paths must be adapted to your cluster. No institution-specific profile or default is installed. You may instead choose a profile explicitly with `--site NAME` when generating inputs. Previously generated runs retain their original scheduler settings.

## Generate files and manage calculations yourself

Create/open a project, add a structure, review and approve the scientific plan, then generate inputs on **ORCA inputs**. **Download calculation files** is a complete route: transfer the extracted files using your normal tools, inspect the generated commands and run `bash submit.sh` from the remote inputs directory. No SSH settings in the app are required. Keep job dependencies intact.

CLI export for an approved, generated version:

```sh
mossbauer project export-inputs --project PROJECT --entry ENTRY --version 1 --out inputs.zip
```

The export checks the selected approval, plan hash and generated file bytes. If files changed or older metadata is present, regenerate inputs. Unrelated outputs and personal configuration are excluded.

## Optional upload helper and agent instructions

To include **a Bash upload helper**, set an SSH alias and an existing absolute remote base directory on Cluster setup. Authentication comes from your normal OpenSSH configuration; do not put passwords or private keys in YAML. Confirm the shown destination and inspect the script before downloading. Extract the ZIP, then run `bash upload.sh` from Bash on macOS/Linux, or Git Bash/WSL on Windows. Bash, OpenSSH, tar and shasum/sha256sum are required.

Connect once with your normal SSH client and verify its host key through your institution. The helper verifies local files, requires an already trusted SSH host and creates a new directory below your remote base. It refuses existing destinations and never submits jobs or deletes prior runs. Failed transfers may leave a partial directory; choose a new remote base before retrying.

**Include instructions for an external agent** adds AGENT.md for the same approved version. Give it to your agent and authorize remote operations separately. It describes dependencies, submission, job-ID checkpoints, monitoring, failure diagnosis, scientific stop conditions and result return. Changing science or exceeding recorded resources requires user direction and, for science, a new approval.

CLI options may be combined or used separately:

```sh
mossbauer project export-inputs --project PROJECT --entry ENTRY --version 1 --out assisted.zip --agent --upload
```

Use `--transfer-site NAME` with `--upload` to take SSH settings from another personal profile. Exporting never opens an SSH connection.

## Return results

After successful calculations, run in the remote run directory:

```sh
python3 harvest.pyz harvest-manifest.json . results.json
```

Import results.json on Results, or return full `<stem>.out` and `.state` files into the selected version's outputs folder. Keep raw outputs and logs on the cluster for diagnosis. Review trust flags and calibration suitability before interpreting predictions.
