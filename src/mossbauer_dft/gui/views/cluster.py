"""Personal Slurm configuration, available before a project is opened."""
from __future__ import annotations

from dataclasses import asdict
import os
import shlex

import streamlit as st
import yaml

from mossbauer_dft.site import SiteProfile, available_sites, profile_hash, resolve_site, save_profile, set_default_site, site_profiles, user_sites_dir
from mossbauer_dft.gui.state import ss
from mossbauer_dft.gui.widgets import _cli


def _load_profile():
    chosen = ss.get('cluster_pick')
    profile = SiteProfile(name='personal', partition='compute', sif='/path/to/orca.sif')
    ss.cluster_loaded_hash = None
    ss.cluster_loaded_name = chosen
    if chosen:
        row = next((r for r in site_profiles() if r['name'] == chosen), None)
        if row:
            try:
                if row['origin'] == 'user':
                    ss.cluster_loaded_hash = profile_hash(row['path'])
                profile = SiteProfile.load(chosen)
            except (ValueError, OSError, TypeError, yaml.YAMLError) as exc:
                ss.cluster_notice = f'Could not load this profile: {exc}. Correct the fields below before saving.'
                profile.name = chosen
    ss.cluster_base = asdict(profile)
    fields = dict(name=profile.name if chosen else '', partition=profile.partition if chosen else '',
                  sif=profile.sif if chosen else '', account=profile.account or '',
                  nprocs=profile.defaults.get('nprocs', 16), memory=float(profile.defaults.get('mem_gb_per_core', 3)),
                  time=profile.defaults.get('time', '48:00:00'), scratch=profile.scratch_root,
                  runtimes=', '.join(profile.runtimes), orca=profile.orca_exe, verb=profile.container_verb,
                  prefix=profile.workdir_prefix, extra='\n'.join(profile.extra_sbatch),
                  nice_enabled=profile.nice is not None, nice=profile.nice or 0,
                  ssh=profile.ssh_host or '', remote=profile.remote_dir or '', replace=False)
    for key, value in fields.items():
        ss['cluster_' + key] = value
    ss.pop('cluster_preview', None)


def _form_profile():
    data = dict(ss.cluster_base)
    data.update(name=ss.cluster_name.strip(), partition=ss.cluster_partition.strip(), sif=ss.cluster_sif.strip(),
                account=ss.cluster_account.strip() or None,
                defaults=dict(nprocs=int(ss.cluster_nprocs), mem_gb_per_core=float(ss.cluster_memory), time=ss.cluster_time.strip()),
                scratch_root=ss.cluster_scratch.strip(), runtimes=[r.strip() for r in ss.cluster_runtimes.split(',') if r.strip()],
                orca_exe=ss.cluster_orca.strip(), container_verb=ss.cluster_verb, workdir_prefix=ss.cluster_prefix.strip(),
                extra_sbatch=[line.strip() for line in ss.cluster_extra.splitlines() if line.strip()],
                nice=int(ss.cluster_nice) if ss.cluster_nice_enabled else None,
                ssh_host=ss.cluster_ssh.strip() or None, remote_dir=ss.cluster_remote.strip() or None)
    return SiteProfile.from_dict(data)


def page_cluster():
    st.header('Cluster setup')
    st.caption('Configure your own Slurm cluster. Settings stay on this computer; no connection or job submission happens here.')
    _cli('mossbauer site init NAME --partition PARTITION --sif /path/to/orca.sif')
    st.caption(f'Local profiles: {user_sites_dir()}')
    rows = site_profiles()
    names = [r['name'] for r in rows]
    if ss.get('cluster_pick') not in [None] + names:
        ss.cluster_pick = None
    st.selectbox('Profile to edit', [None] + names, key='cluster_pick', on_change=_load_profile,
                 format_func=lambda n: 'Create a profile' if n is None else n)
    if 'cluster_base' not in ss:
        _load_profile()
    if ss.get('cluster_notice'):
        st.info(ss.pop('cluster_notice'))
    for row in rows:
        if row['error']:
            st.warning(f"{row['name']}: {row['error']}")
        elif row['shadowed']:
            st.caption(f"{row['name']} uses your local profile instead of the bundled profile.")
    with st.form('cluster_configuration'):
        left, right = st.columns(2)
        left.text_input('Profile name', key='cluster_name', help='Letters, digits, underscores and hyphens; this is the name used by --site.')
        right.text_input('Slurm partition', key='cluster_partition')
        st.text_input('ORCA container path on the cluster', key='cluster_sif', placeholder='/path/to/orca.sif')
        st.text_input('Slurm account (optional)', key='cluster_account')
        a, b, c = st.columns(3)
        a.number_input('Default processors', min_value=1, step=1, key='cluster_nprocs', help='Approved plan processor counts take precedence over this site default.')
        b.number_input('Memory per core (GB)', min_value=0.1, step=0.5, key='cluster_memory')
        c.text_input('Default walltime', key='cluster_time')
        with st.expander('Container and Slurm options'):
            st.text_input('Node scratch directory', key='cluster_scratch')
            st.text_input('Container runtimes (comma separated)', key='cluster_runtimes')
            st.selectbox('Container command', ['run', 'exec'], key='cluster_verb')
            st.text_input('ORCA executable inside the container', key='cluster_orca')
            st.text_input('Scratch job prefix', key='cluster_prefix')
            st.checkbox('Set a Slurm nice adjustment', key='cluster_nice_enabled')
            st.number_input('Nice adjustment', min_value=0, max_value=2147483645, step=1, key='cluster_nice')
            st.text_area('Additional #SBATCH directives', key='cluster_extra', help='One --flag[=value] directive per line, including #SBATCH. Supported extras include constraint, qos, reservation, exclusive, hint, distribution, licenses and mail settings.')
        with st.expander('Optional upload settings'):
            st.caption('Leave these blank to download files and handle uploads yourself. Authentication stays in your normal SSH configuration.')
            st.text_input('SSH host alias or user@hostname', key='cluster_ssh')
            st.text_input('Remote working directory', key='cluster_remote', placeholder='/work/your-account')
        st.checkbox('Replace an existing local profile', key='cluster_replace')
        preview_column, save_column = st.columns(2)
        preview = preview_column.form_submit_button('Validate / preview YAML', use_container_width=True)
        save = save_column.form_submit_button('Save profile', type='primary', use_container_width=True)
    if preview or save:
        ss.pop('cluster_preview', None)
        try:
            profile = _form_profile()
            ss.cluster_preview = profile.to_yaml()
            if save:
                expected = ss.cluster_loaded_hash if ss.cluster_loaded_name == profile.name else None
                path = save_profile(profile, replace=ss.cluster_replace, expected_hash=expected)
                ss.cluster_loaded_name = profile.name
                ss.cluster_loaded_hash = profile_hash(path)
                ss.cluster_notice = f'Saved {profile.name}. Choose it below to make it the default cluster.'
                st.rerun()
            st.success('Profile is valid. Previewing does not write a file.')
        except (ValueError, OSError, TypeError, yaml.YAMLError) as exc:
            st.error(str(exc))
    if ss.get('cluster_preview'):
        with st.expander('YAML preview', expanded=preview):
            st.code(ss.cluster_preview, language='yaml')
            st.download_button('Download profile YAML', ss.cluster_preview, f"{ss.cluster_name or 'cluster'}.yaml", mime='text/yaml')
    st.subheader('Default cluster')
    if os.environ.get('MOSSBAUER_DFT_SITE') is not None:
        st.info(f"MOSSBAUER_DFT_SITE currently overrides the saved default: {os.environ['MOSSBAUER_DFT_SITE']}")
    try:
        default = resolve_site()
        st.caption(f'Effective default: {default}')
    except (ValueError, OSError, yaml.YAMLError) as exc:
        default = None
        st.info(str(exc))
    valid = available_sites()
    if ss.get('cluster_default') not in [None] + valid:
        ss.cluster_default = None
    if 'cluster_default' not in ss:
        ss.cluster_default = default if default in valid else None
    selected = st.selectbox('Default cluster profile', [None] + valid, key='cluster_default',
                            format_func=lambda n: 'Choose a profile' if n is None else n)
    if st.button('Use as default', disabled=selected is None):
        try:
            set_default_site(selected)
            st.success(f'Saved default cluster: {selected}')
        except (ValueError, OSError, yaml.YAMLError) as exc:
            st.error(str(exc))
    if selected:
        _cli(f'mossbauer site default {shlex.quote(selected)}')
