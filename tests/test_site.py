import re
from pathlib import Path
from mossbauer_dft.site import SiteProfile, available_sites

GOLD = Path(__file__).parent / "data" / "golden" / "fe1_orca.slurm"


def norm(text, drop_comments=True):
    text = text.replace("\\\n", " ")
    out = []
    for l in text.split("\n"):
        l = re.sub(r"\s+", " ", l.strip())
        if not l or (drop_comments and l.startswith("#") and not l.startswith("#!") and not l.startswith("#SBATCH")):
            continue
        out.append(l)
    return out


def test_generic_example_is_listed():
    assert "example" in available_sites()


def runner_profile():
    return SiteProfile(name='runner', partition='compute', sif='/images/orca.sif', nice=10000000,
                       scratch_root='/scratch', workdir_prefix='fe1_orca', comment='Fe(I) project only.')


def test_script_equivalent_to_verified_runner_plus_nice():
    gen = norm(runner_profile().slurm_script())
    orig = norm(GOLD.read_text())
    assert "#SBATCH --nice=10000000" in gen
    # job-state lines (D55) are additions for the return desk; everything else must match the hand-written runner
    state = ('STATE=', 'finish()', 'echo "started ', "trap 'finish ")
    gen_wo_nice = [l.replace('"$RT"', '$RT').replace('|| { finish 2 "scratch directory unavailable"; exit 2; }', '|| exit 2') for l in gen
                   if l != "#SBATCH --nice=10000000" and not l.startswith(state)]
    assert gen_wo_nice[:gen_wo_nice.index('st=$?')+1] == orig[:orig.index('st=$?')+1]
    assert 'exit \"$st\"' in runner_profile().slurm_script()


def test_script_key_features():
    s = runner_profile().slurm_script()
    assert "/images/orca.sif" in s
    assert "/scratch/$USER/fe1_orca_${STEM}_${SLURM_JOB_ID}" in s
    assert "trap copyback EXIT" in s and "\\$ORCADIR/orca" in s


def test_sbatch_command_has_nice_and_n_c():
    cmd = runner_profile().sbatch_command("job1", nprocs=16, mem_gb=48, time="01:00:00")
    assert "--nice=10000000" in cmd and "-n 16 -c 1" in cmd and cmd.endswith("job1")


def test_failed_runtime_and_unconverged_optimization_exit_nonzero(tmp_path):
    import os
    import subprocess
    submit = tmp_path / 'submit'; submit.mkdir()
    runtime = tmp_path / 'runtime'
    runtime.write_text('#!/bin/bash\nprintf "%s\\n" "$TEST_OUTPUT" > job.out\nexit "$TEST_RC"\n')
    runtime.chmod(0o700)
    profile = SiteProfile(name='test', partition='test', sif='/unused.sif', runtimes=[str(runtime)], scratch_root=str(tmp_path / 'scratch'))
    script = submit / 'job.slurm'; script.write_text(profile.slurm_script())
    for rc, output, deck, expected in [(7, 'ORCA TERMINATED NORMALLY', '! SP', 7),
            (0, 'failed', '! SP', 1), (0, 'ORCA TERMINATED NORMALLY', '! Opt B3LYP', 1),
            (0, 'THE OPTIMIZATION HAS CONVERGED\nORCA TERMINATED NORMALLY', '! Opt B3LYP', 0)]:
        (submit / 'job.inp').write_text(deck)
        env = dict(os.environ, SLURM_SUBMIT_DIR=str(submit), SLURM_JOB_ID='123', USER='test', TEST_RC=str(rc), TEST_OUTPUT=output)
        run = subprocess.run(['bash', str(script), 'job'], env=env, capture_output=True, text=True)
        assert run.returncode == expected, run.stderr + run.stdout
        assert (submit / 'job.out').read_text().strip() == output
        from mossbauer_dft.harvest import parse_state
        state = parse_state((submit / 'job.state').read_text())
        assert state['started']['job'] == '123' and state['finished']['rc'] == str(expected)
        assert (state['finished']['reason'] == 'normal') == (expected == 0)


def test_walltime_or_scancel_term_is_recorded_in_job_state(tmp_path):  # D55
    import os
    import signal
    import subprocess
    import time
    from mossbauer_dft.harvest import parse_state
    submit = tmp_path / 'submit'; submit.mkdir()
    runtime = tmp_path / 'runtime'
    runtime.write_text('#!/bin/bash\nsleep 30\n')
    runtime.chmod(0o700)
    profile = SiteProfile(name='test', partition='test', sif='/unused.sif', runtimes=[str(runtime)], scratch_root=str(tmp_path / 'scratch'))
    script = submit / 'job.slurm'; script.write_text(profile.slurm_script())
    (submit / 'job.inp').write_text('! SP')
    env = dict(os.environ, SLURM_SUBMIT_DIR=str(submit), SLURM_JOB_ID='9', USER='test')
    proc = subprocess.Popen(['bash', str(script), 'job'], env=env, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):  # wait until the job has started
        if (submit / 'job.state').is_file():
            break
        time.sleep(0.05)
    time.sleep(0.3)
    os.killpg(proc.pid, signal.SIGTERM)  # Slurm signals the whole job's process group
    assert proc.wait(timeout=20) == 143
    state = parse_state((submit / 'job.state').read_text())
    assert state['finished']['rc'] == '143' and 'signal' in state['finished']['reason']


# Personal profiles use the same validation from files, CLI and GUI.
def test_personal_profile_roundtrip_and_listing(tmp_path, monkeypatch):
    from mossbauer_dft.site import save_profile, site_profiles
    monkeypatch.setenv('MOSSBAUER_DFT_HOME', str(tmp_path))
    profile = SiteProfile(name='personal', partition='compute', sif='/images/orca.sif', account='chem',
                          extra_sbatch=['#SBATCH --constraint=cpu'], ssh_host='my-cluster', remote_dir='/work/me')
    path = save_profile(profile)
    assert path == tmp_path / 'sites' / 'personal.yaml'
    assert SiteProfile.load('personal') == profile
    assert next(r for r in site_profiles() if r['name'] == 'personal')['origin'] == 'user'
    import pytest
    with pytest.raises(FileExistsError):
        save_profile(profile)
    (path.parent / 'broken.yaml').write_text('partition: [')
    assert next(r for r in site_profiles() if r['name'] == 'broken')['error']
    assert 'personal' in available_sites() and 'broken' not in available_sites()


def test_profile_rejects_injection_on_load_and_render(tmp_path):
    import pytest
    for fields in [dict(name='../escape'), dict(partition='compute\n#SBATCH --exclusive'),
                   dict(sif='/images/$(id).sif'), dict(workdir_prefix='x;id'),
                   dict(account='chem\nwhoami'), dict(extra_sbatch=['#SBATCH --partition=other']),
                   dict(extra_sbatch=['#SBATCH --constraint=cpu; touch /tmp/x']),
                   dict(extra_sbatch=['#SBATCH --output=/tmp/x']), dict(nodes=True),
                   dict(defaults={'nprocs': 0}), dict(ssh_host='-oProxyCommand=x')]:
        data = dict(name='test', partition='compute', sif='/images/orca.sif')
        data.update(fields)
        with pytest.raises(ValueError):
            SiteProfile.from_dict(data)
    profile = SiteProfile(name='test', partition='compute', sif='/images/orca.sif')
    profile.partition = 'compute\necho hacked'
    with pytest.raises(ValueError):
        profile.slurm_script()
    with pytest.raises(ValueError):
        SiteProfile.load('../escape')


def test_profile_spaces_account_and_directives(tmp_path):
    import subprocess
    profile = SiteProfile(name='personal', partition='compute', sif='/images/my orca.sif',
                          scratch_root=str(tmp_path / 'my scratch'), account='chem',
                          extra_sbatch=['#SBATCH --constraint=cpu'])
    script = profile.slurm_script()
    assert "SIF='/images/my orca.sif'" in script
    assert script.count('#SBATCH --account=chem') == 1
    assert script.count('#SBATCH --constraint=cpu') == 1
    assert '--account=chem' in profile.sbatch_command('job')
    assert subprocess.run(['bash', '-n'], input=script, text=True, capture_output=True).returncode == 0


def test_user_profile_shadows_and_home_changes(tmp_path, monkeypatch):
    from mossbauer_dft.site import save_profile, site_profiles
    monkeypatch.setenv('MOSSBAUER_DFT_HOME', str(tmp_path / 'first'))
    profile = SiteProfile(name='example', partition='personal', sif='/images/orca.sif')
    save_profile(profile)
    assert SiteProfile.load('example').partition == 'personal'
    assert next(r for r in site_profiles() if r['name'] == 'example')['shadowed']
    monkeypatch.setenv('MOSSBAUER_DFT_HOME', str(tmp_path / 'second'))
    assert SiteProfile.load('example').partition != 'personal'


def test_profile_listing_uses_same_extension_precedence_as_load(tmp_path, monkeypatch):
    from mossbauer_dft.site import save_profile, site_profiles
    monkeypatch.setenv('MOSSBAUER_DFT_HOME', str(tmp_path))
    p = save_profile(SiteProfile(name='personal', partition='compute', sif='/images/orca.sif'))
    p.with_suffix('.yml').write_text('not a valid profile')
    row = next(r for r in site_profiles() if r['name'] == 'personal')
    assert row['error'] is None
    assert row['path'] == str(p)
    assert SiteProfile.load('personal').partition == 'compute'


def test_profile_save_detects_external_edit(tmp_path, monkeypatch):
    import pytest
    from mossbauer_dft.site import profile_hash, save_profile
    monkeypatch.setenv('MOSSBAUER_DFT_HOME', str(tmp_path))
    profile = SiteProfile(name='personal', partition='compute', sif='/images/orca.sif')
    p = save_profile(profile)
    original = profile_hash(p)
    p.write_text(p.read_text() + '# outside edit\n')
    with pytest.raises(ValueError, match='changed outside'):
        save_profile(profile, replace=True, expected_hash=original)
    assert '# outside edit' in p.read_text()
    save_profile(profile, replace=True, expected_hash=profile_hash(p))
    assert '# outside edit' not in p.read_text()
