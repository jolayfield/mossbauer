"""Site profiles (YAML) and Slurm script generation."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from pathlib import Path
import shlex
import hashlib
import math
import os
import re
import tempfile

from .data import SITES_DIR, user_home


@dataclass
class SiteProfile:
    name: str
    partition: str
    sif: str
    scheduler: str = "slurm"
    nodes: int = 1
    nice: int | None = None
    orca_exe: str = "$ORCADIR/orca"
    container_verb: str = "run"                      # run | exec
    runtimes: list[str] = field(default_factory=lambda: ["apptainer", "/usr/local/bin/singularity"])
    scratch_root: str = "/scratch"
    workdir_prefix: str = "orca"
    copy_xyz_from_submit_dir: bool = True
    copyback_suffixes: list[str] = field(default_factory=lambda: ["out", "xyz", "_trj.xyz", "property.txt", "engrad", "hess", "gbw", "err"])
    comment: str = ""
    defaults: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    account: str | None = None
    extra_sbatch: list[str] = field(default_factory=list)
    ssh_host: str | None = None
    remote_dir: str | None = None

    def __post_init__(self):
        self.validate()

    def validate(self):
        validate_name(self.name)
        _token(self.partition, 'partition')
        if self.account is not None:
            _token(self.account, 'account')
        if self.scheduler != 'slurm' or self.container_verb not in ('run', 'exec'):
            raise ValueError('Use Slurm and container_verb run or exec')
        if type(self.nodes) is not int or self.nodes < 1:
            raise ValueError('nodes must be a positive integer')
        if self.nice is not None and (type(self.nice) is not int or not 0 <= self.nice <= 2147483645):
            raise ValueError('nice must be a nonnegative Slurm priority adjustment')
        for key in ('sif', 'scratch_root'):
            _path(getattr(self, key), key)
        _token(self.workdir_prefix, 'workdir_prefix')
        if not isinstance(self.orca_exe, str) or not re.fullmatch(r'(?:\$ORCADIR/|/)[A-Za-z0-9_./+-]+', self.orca_exe):
            raise ValueError('orca_exe must be an absolute path or $ORCADIR/executable')
        if not isinstance(self.runtimes, list) or not self.runtimes:
            raise ValueError('runtimes must be a nonempty list')
        for runtime in self.runtimes:
            _path(runtime, 'runtime', absolute=False)
        if not isinstance(self.copy_xyz_from_submit_dir, bool):
            raise ValueError('copy_xyz_from_submit_dir must be true or false')
        if not isinstance(self.copyback_suffixes, list) or not self.copyback_suffixes:
            raise ValueError('copyback_suffixes must be a nonempty list')
        for suffix in self.copyback_suffixes:
            if not isinstance(suffix, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', suffix):
                raise ValueError('copyback suffixes must be filename suffixes')
        if not isinstance(self.comment, str) or any(ord(c) < 32 or ord(c) == 127 for c in self.comment):
            raise ValueError('comment must be one line')
        if not isinstance(self.notes, list) or not all(isinstance(n, str) for n in self.notes):
            raise ValueError('notes must be a list of strings')
        if not isinstance(self.defaults, dict) or set(self.defaults) - {'nprocs', 'mem_gb_per_core', 'time'}:
            raise ValueError('defaults supports nprocs, mem_gb_per_core and time')
        n = self.defaults.get('nprocs', 16)
        mem = self.defaults.get('mem_gb_per_core', 3)
        if type(n) is not int or n < 1 or isinstance(mem, bool) or not isinstance(mem, (int, float)) or not math.isfinite(mem) or mem <= 0:
            raise ValueError('default processors and memory must be positive')
        _walltime(self.defaults.get('time', '48:00:00'))
        if not isinstance(self.extra_sbatch, list):
            raise ValueError('extra_sbatch must be a list of directive lines')
        managed = {'partition', 'nodes', 'nice', 'account', 'ntasks', 'cpus-per-task', 'mem', 'mem-per-cpu', 'mem-per-gpu', 'time', 'dependency', 'job-name', 'output', 'error', 'chdir', 'wrap', 'export', 'export-file', 'get-user-env', 'input', 'array'}
        for line in self.extra_sbatch:
            match = re.fullmatch(r'#SBATCH --([a-z][a-z0-9-]*)(?:=([A-Za-z0-9_.,:/@%+!=-]+))?', line) if isinstance(line, str) else None
            if not match or match[1] in managed or match[1] not in {'constraint', 'qos', 'reservation', 'exclusive', 'hint', 'distribution', 'licenses', 'mail-type', 'mail-user', 'requeue', 'no-requeue'}:
                raise ValueError('extra_sbatch requires non-conflicting #SBATCH --flag[=value] lines')
        if self.ssh_host is not None and (not isinstance(self.ssh_host, str) or not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]*', self.ssh_host)):
            raise ValueError('ssh_host must be an SSH alias or user@hostname')
        if self.remote_dir is not None:
            _path(self.remote_dir, 'remote_dir')
        return self

    def to_yaml(self):
        import yaml
        return yaml.safe_dump(asdict(self.validate()), sort_keys=False)

    @classmethod
    def from_dict(cls, d: dict) -> "SiteProfile":
        if not isinstance(d, dict):
            raise ValueError('site profile must be a YAML mapping')
        known = {f for f in cls.__dataclass_fields__}
        extra = set(d) - known
        if extra:
            raise ValueError(f"unknown site-profile keys: {sorted(extra)}")
        return cls(**d)

    @classmethod
    def from_yaml(cls, path) -> "SiteProfile":
        import yaml
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f))

    @classmethod
    def load(cls, name_or_path=None) -> "SiteProfile":
        """Load a personal or bundled profile by name, or an explicit YAML file."""
        if isinstance(name_or_path, cls):
            return name_or_path.validate()
        if name_or_path is None:
            name_or_path = resolve_site()
        p = Path(str(name_or_path)).expanduser()
        if p.suffix in ('.yaml', '.yml'):
            return cls.from_yaml(p)
        validate_name(name_or_path)
        for directory in (user_sites_dir(), SITES_DIR):
            for suffix in ('.yaml', '.yml'):
                candidate = directory / (name_or_path + suffix)
                if candidate.is_file():
                    if not candidate.resolve().is_relative_to(directory.resolve()):
                        raise ValueError('site profile escapes its sites directory')
                    profile = cls.from_yaml(candidate)
                    if profile.name != name_or_path:
                        raise ValueError('profile name must match its filename')
                    return profile
        raise FileNotFoundError(f'No site profile {name_or_path!r}; configure one with mossbauer site init')

    # ------------------------------------------------------------------
    def slurm_script(self) -> str:
        """Return a Slurm batch script taking one argument, the job stem.

        Runs <stem>.inp (+ any *.xyz in the submit dir) in node-local scratch inside
        the ORCA container and copies results back on exit (also on failure).
        """
        if self.scheduler != "slurm":
            raise NotImplementedError("only slurm profiles are supported")
        if self.container_verb not in ("run", "exec"):
            raise ValueError("container_verb must be run or exec")
        self.validate()
        d = self.defaults
        rt_candidates = self.runtimes
        first, rest = shlex.quote(rt_candidates[0]), [shlex.quote(r) for r in rt_candidates[1:]]
        rt_sel = f"if command -v {first} >/dev/null; then RT={first}; else RT={rest[0]}; fi" if rest else f"RT={first}"
        suffix_files = " ".join(
            f'"$STEM".{s}' if not s.startswith("_") else f'"$STEM"{s}' for s in self.copyback_suffixes)
        suffix_files = suffix_files.replace('"$STEM".' + "_trj.xyz", '"$STEM"_trj.xyz')
        L = ["#!/bin/bash",
             f"#SBATCH --partition={self.partition}",
             f"#SBATCH --nodes={self.nodes}"]
        if self.nice is not None:
            L.append(f"#SBATCH --nice={self.nice}")
        if self.account is not None:
            L.append(f"#SBATCH --account={self.account}")
        L.extend(self.extra_sbatch)
        L += [
            "# usage: sbatch -J <stem> -n <nprocs> -c 1 --mem=<GB>G -t <HH:MM:SS> <this script> <stem>",
            "# Runs <stem>.inp (and any <stem>*.xyz it reads) from the submit dir in node",
            "# scratch with ORCA and copies results back on exit (also on failure).",
            "# %pal nprocs in the .inp must equal -n (OpenMPI needs one Slurm task per rank)."
            + (f" {self.comment}" if self.comment else ""),
            "set -u",
            "export USER=${USER:-$(id -un)}",
            "STEM=$1",
            f"SIF={shlex.quote(self.sif)}",
            "SUBMIT=$SLURM_SUBMIT_DIR",
            "# job state for the app's return desk: written to the submit dir, so it survives the scratch cleanup",
            'STATE="$SUBMIT/$STEM.state"',
            'finish() { echo "finished rc=$1 at=$(date -u +%Y-%m-%dT%H:%M:%SZ) reason=$2" >> "$STATE"; }',
            'echo "started job=$SLURM_JOB_ID node=$(hostname) at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE"',
            "trap 'finish 143 \"terminated by signal (walltime limit or scancel)\"; exit 143' TERM",
            f"WORK={shlex.quote(self.scratch_root)}/$USER/{self.workdir_prefix}_${{STEM}}_${{SLURM_JOB_ID}}",
            'mkdir -p "$WORK" && cd "$WORK" || { finish 2 "scratch directory unavailable"; exit 2; }',
            'cp "$SUBMIT/$STEM.inp" .',
        ]
        if self.copy_xyz_from_submit_dir:
            L.append('for f in "$SUBMIT"/*.xyz; do [ -f "$f" ] && cp "$f" .; done')
        L += [
            "copyback() {",
            f"  for f in {suffix_files}; do",
            '    [ -f "$f" ] && cp -f "$f" "$SUBMIT/"',
            "  done",
            '  cd / && rm -rf "$WORK"',
            "}",
            "trap copyback EXIT",
            f'echo "{self.workdir_prefix}: job=$SLURM_JOB_ID stem=$STEM node=$(hostname) sif=$SIF"',
            "cat > run.sh <<EOF",
            "#!/bin/bash",
            f"\\{self.orca_exe} $STEM.inp > $STEM.out 2> $STEM.err" if self.orca_exe.startswith("$") else f"{self.orca_exe} $STEM.inp > $STEM.out 2> $STEM.err",
            "EOF",
            "chmod +x run.sh",
            rt_sel,
            f'"$RT" {self.container_verb} -B "$WORK" --pwd "$WORK" "$SIF" ./run.sh',
            "st=$?",
            'if [ "$st" -ne 0 ]; then echo "ORCA runtime failed (rc=$st)"; finish "$st" "ORCA runtime failed"; exit "$st"; fi',
            'if ! grep -q "ORCA TERMINATED NORMALLY" "$STEM.out"; then echo "ORCA did not terminate normally"; finish 1 "ORCA did not terminate normally"; exit 1; fi',
            'if grep -Eiq "^[[:space:]]*![^#]*[[:space:]](opt|tightopt|looseopt|verytightopt)([[:space:]]|$)" "$STEM.inp"; then',
            '  if ! grep -q "THE OPTIMIZATION HAS CONVERGED" "$STEM.out"; then echo "Geometry optimization did not converge"; finish 1 "geometry optimization did not converge"; exit 1; fi',
            'fi',
            'finish 0 normal',
            f'echo "{self.workdir_prefix}: normal"',
        ]
        return "\n".join(L) + "\n"

    def sbatch_command(self, stem: str, nprocs: int | None = None, mem_gb: float | None = None,
                       time: str | None = None, script: str = "orca_job.slurm") -> str:
        """sbatch command line for one job (``-n N -c 1``; --nice is also in the script header)."""
        self.validate()
        _token(stem, "job stem")
        d = self.defaults
        n = nprocs or d.get("nprocs", 16)
        mem = mem_gb or (n * d.get("mem_gb_per_core", 3))
        t = time or d.get("time", "48:00:00")
        parts = ["sbatch", "-J", stem, "-n", str(n), "-c", "1", f"--mem={int(mem)}G", "-t", t]
        if self.nice is not None:
            parts.append(f"--nice={self.nice}")
        if self.account is not None:
            parts.append(f"--account={self.account}")
        parts += [script, stem]
        return " ".join(shlex.quote(p) for p in parts)


def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
        raise ValueError('Site names use 1-64 letters, digits, underscores or hyphens')
    return name


def _token(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+-]*', value):
        raise ValueError(f'{label} must be a single safe name')


def _path(value, label, absolute=True):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value) or any(c in value for c in '$`;|&<>\\'):
        raise ValueError(f'{label} must be a literal path without shell expressions')
    if absolute and not value.startswith('/'):
        raise ValueError(f'{label} must be an absolute cluster path')


def _walltime(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9]+-)?[0-9]{1,3}:[0-5][0-9](?::[0-5][0-9])?', value):
        raise ValueError('time must be HH:MM:SS or D-HH:MM:SS')


def user_sites_dir():
    return user_home() / 'sites'


def site_profiles():
    rows = {}
    for directory, origin in ((SITES_DIR, 'bundled'), (user_sites_dir(), 'user')):
        for path in sorted(directory.glob('*.yml')) + sorted(directory.glob('*.yaml')):
            shadowed = path.stem in rows and rows[path.stem]['origin'] != origin
            try:
                validate_name(path.stem)
                if not path.resolve().is_relative_to(directory.resolve()):
                    raise ValueError('profile escapes its sites directory')
                profile = SiteProfile.from_yaml(path)
                if profile.name != path.stem:
                    raise ValueError('profile name must match its filename')
                error = None
            except (OSError, ValueError, TypeError) as exc:
                error = str(exc)
            except Exception as exc:
                # A malformed YAML must not hide the remaining profiles.
                import yaml
                if not isinstance(exc, yaml.YAMLError):
                    raise
                error = str(exc)
            rows[path.stem] = dict(name=path.stem, path=str(path), origin=origin, shadowed=shadowed, error=error)
    return [rows[n] for n in sorted(rows)]


def available_sites() -> list[str]:
    return [r['name'] for r in site_profiles() if not r['error']]


def profile_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_profile(profile, *, replace=False, expected_hash=None):
    text = profile.to_yaml()
    directory = user_sites_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError('User sites directory must not be a symlink')
    target = directory / (profile.name + '.yaml')
    alternate = target.with_suffix('.yml')
    if not target.exists() and not target.is_symlink() and alternate.exists():
        target = alternate
    if target.is_symlink():
        raise ValueError('Refusing to overwrite a profile symlink')
    if expected_hash is not None and (not target.is_file() or profile_hash(target) != expected_hash):
        raise ValueError('Profile changed outside the app; reload before saving')
    if target.exists() and not replace:
        raise FileExistsError(f'{target} already exists; confirm replacement')
    fd, temp = tempfile.mkstemp(dir=directory, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', newline='\n') as stream:
            stream.write(text)
        if replace:
            os.replace(temp, target)
        else:
            os.link(temp, target)  # atomic no-clobber creation
    finally:
        Path(temp).unlink(missing_ok=True)
    return target


class NoDefaultSiteError(ValueError):
    """No explicit, environment or configured cluster selection exists."""


def resolve_site(explicit=None):
    import yaml
    selected = explicit if explicit is not None else os.environ.get('MOSSBAUER_DFT_SITE')
    if selected is None:
        path = user_home() / 'config.yaml'
        if path.exists():
            with path.open() as stream:
                config = yaml.safe_load(stream)
            if not isinstance(config, dict):
                raise ValueError('User config.yaml must be a mapping')
            selected = config.get('default_site')
    if selected is None:
        raise NoDefaultSiteError('No default cluster: use mossbauer site init NAME, then mossbauer site default NAME, or pass --site NAME')
    SiteProfile.load(selected)
    return selected


def set_default_site(name):
    import yaml
    SiteProfile.load(name)
    validate_name(name)
    root = user_home()
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'config.yaml'
    config = yaml.safe_load(path.read_text()) if path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError('User config.yaml must be a mapping')
    config['default_site'] = name
    fd, temp = tempfile.mkstemp(dir=root, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', newline='\n') as stream:
            yaml.safe_dump(config, stream, sort_keys=False)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)
    return name
