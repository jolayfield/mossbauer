"""``mossbauer`` command line: plan | approve | setup | analyze | refset | project | gui."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__


def _hint_args(p):
    p.add_argument("--ox-state", type=int, dest="oxidation_state", help="Fe oxidation state (e.g. 1 for Fe(I))")
    p.add_argument("--radical-ligand", choices=["yes", "no"], help="suspected redox-active/radical ligand")
    p.add_argument("--nitrosyl", choices=["yes", "no"], help="Fe-NO present (default: detected from the geometry)")
    p.add_argument("--solvent", help="CPCM solvent name (default: gas phase)")
    p.add_argument("--temperature", type=float, dest="temperature_K", help="experimental temperature (K)")
    p.add_argument("--exp-delta", type=float)
    p.add_argument("--exp-deq", type=float, help="experimental |DeltaEQ| (mm/s)")


def _override_args(p, broken_symmetry: bool = True):
    """The method/geometry/calibration override flags shared by ``plan``, ``project add/edit`` and ``project batch``
    (batch options leave out broken symmetry, which is per structure)."""
    p.add_argument("--geometry", help="geometry level FUNCTIONAL/BASIS (default MN15/def2-SVP)")
    p.add_argument("--geometry-as-given", metavar="LABEL", help="skip optimization; label of the supplied geometry protocol, e.g. BP86-D3BJ/def2-TZVP")
    p.add_argument("--freq", action="store_true")
    p.add_argument("--delta-functional")
    p.add_argument("--deq-functional")
    p.add_argument("--crosscheck-functional")
    p.add_argument("--calibration-strategy", choices=["auto", "lookup", "compute_refset", "user_refs"])
    p.add_argument("--allow-unverified-calibration", action="store_true")
    p.add_argument("--allow-protocol-mismatch", action="store_true")
    p.add_argument("--user-refs", metavar="YAML", help="YAML list of {name, xyz, charge, multiplicity, delta_exp[, deq_exp]}")
    if broken_symmetry:
        p.add_argument("--bs-flip", help="comma-separated 0-based atom indices to flip (broken symmetry)")
        p.add_argument("--bs-final-ms", type=float)
        p.add_argument("--bs-na", type=int)
        p.add_argument("--bs-nb", type=int)
    p.add_argument("--highfield", action="store_true")
    p.add_argument("--nprocs", type=int)


def _yesno(a, attr: str):
    return {"yes": True, "no": False, None: None}[getattr(a, attr, None)]


def _radical(a):
    return _yesno(a, "radical_ligand")


def _nitrosyl(a):
    return _yesno(a, "nitrosyl")


def _experimental(a) -> dict:
    return {k: v for k, v in (("delta", a.exp_delta), ("deq", a.exp_deq), ("T_K", a.temperature_K)) if v is not None}


def _overrides(a) -> dict:
    ov = {}
    if getattr(a, "geometry", None):
        f, _, b = a.geometry.partition("/")
        ov["geometry"] = dict(mode="optimize", functional=f, basis=b or "def2-SVP")
    if getattr(a, "geometry_as_given", None):
        ov["geometry"] = dict(mode="as_given", as_given_label=a.geometry_as_given)
    if getattr(a, "freq", False):
        ov.setdefault("geometry", dict(mode="optimize", functional="MN15", basis="def2-SVP"))["freq"] = True
    for k in ("delta_functional", "deq_functional", "crosscheck_functional", "calibration_strategy", "nprocs", "refset_subset"):
        if getattr(a, k, None) is not None:
            ov[k] = getattr(a, k)
    if getattr(a, "highfield", False):
        ov["highfield"] = True
    if getattr(a, "allow_unverified_calibration", False):
        ov["allow_unverified_calibration"] = True
    if getattr(a, "allow_protocol_mismatch", False):
        ov["allow_protocol_mismatch"] = True
    if getattr(a, "bs_flip", None):
        ov["broken_symmetry"] = dict(kind="flipspin", flip_atoms=[int(x) for x in a.bs_flip.split(",")], final_ms=a.bs_final_ms)
    if getattr(a, "bs_na", None) is not None:
        ov["broken_symmetry"] = dict(kind="brokensym", na=a.bs_na, nb=a.bs_nb)
    if getattr(a, "user_refs", None):
        import yaml
        ov["user_refs"] = yaml.safe_load(Path(a.user_refs).read_text())
    return ov


def cmd_plan(a):
    from . import plan as P
    rl = _radical(a)
    exp = _experimental(a)
    if a.update:
        plan = P.load_plan(a.update)
        hints = {k: getattr(a, k) for k in ("oxidation_state", "solvent", "temperature_K") if getattr(a, k) is not None}
        if rl is not None:
            hints["radical_ligand"] = rl
        if _nitrosyl(a) is not None:
            hints["nitrosyl"] = _nitrosyl(a)
        plan = P.update_plan(plan, overrides=_overrides(a), **hints)
        out = a.update
    else:
        if not a.structure or a.charge is None or a.multiplicity is None:
            sys.exit("plan: structure, --charge and --multiplicity are required")
        plan = P.make_plan(a.structure, a.charge, a.multiplicity, name=a.name, oxidation_state=a.oxidation_state, radical_ligand=rl,
                           solvent=a.solvent, temperature_K=a.temperature_K, nitrosyl=_nitrosyl(a), experimental=exp, overrides=_overrides(a))
        out = a.out
    P.save_plan(plan, out)
    print(P.summarize(plan))
    print(f"\nwritten: {out}  (status draft; review, then `mossbauer approve {out}`)")


def _approval_channel(a, cmd: str) -> str:
    """--via agent needs --on-behalf-of; without --via the channel is cli, or cli (non-interactive) off a terminal."""
    if (a.via == "agent") != bool(a.on_behalf_of):
        sys.exit(f"{cmd}: --via agent and --on-behalf-of NAME go together (name the person the agent acts for)")
    return a.via or ("cli" if sys.stdin is not None and sys.stdin.isatty() else "cli (non-interactive)")


def _approval_flags(q):
    q.add_argument("--by")
    q.add_argument("--via", choices=["cli", "agent"], help="approval channel (default: cli, or 'cli (non-interactive)' "
                   "when stdin is not a terminal); agents pass --via agent --on-behalf-of NAME")
    q.add_argument("--on-behalf-of", metavar="NAME", help="the person an agent approves for (required with --via agent)")


def cmd_approve(a):
    from . import plan as P
    from .project import approval_line
    via = _approval_channel(a, "approve")
    plan = P.approve_file(a.plan, a.by, via, a.on_behalf_of)
    print(f"{approval_line(plan['approval'])} at {plan['approval']['at']}  hash {plan['plan_hash'][:12]}")


def cmd_setup(a):
    from . import workflow as W
    r = W.write_inputs(a.plan, a.out, a.site)
    print(f"Site profile: {r['site']}")
    print("wrote:", *r["files"], sep="\n  ")
    print("\nsubmission commands (displayed, not run):")
    print("\n".join("  " + c for c in r["commands"]))


def cmd_analyze(a):
    from . import workflow as W
    r = W.analyze(a.plan, a.outputs, a.calibration, a.level, include_unmatched=getattr(a, "all", False))
    _print_analysis(r, a.csv)


def _f3(v) -> str:
    return "n/a" if v is None else f"{v:.3f}"  # None: a NaN saved as null in a project's analysis.json


def _print_analysis(r: dict, csv=None):
    from . import workflow as W
    print("calibration:", r["calibration"])
    for n in r["notes"]:
        print("NOTE:", n)
    for x in r["rows"]:
        d = "n/a" if x["delta"] is None else f"{x['delta']:.3f} [{_f3(x['delta_lo'])}, {_f3(x['delta_hi'])}]"
        print(f"{x['job']:<34} Fe{x['atom']} rho0={x['rho0']} delta={d}  DeltaEQ={_f3(x['deq_signed'])} (|{_f3(x['abs_deq'])}|) eta={_f3(x['eta'])} "
              f"<S2>={x['s2']} Fe-spin={x['fe_spin_mulliken']}")
        for f in x.get("trust_flags") or []:
            print(f"    {f['severity']} ({f['property']}): {f['text']}")
    if csv:
        Path(csv).write_text(W.results_csv(r))
        print("written", csv)


def cmd_refset(a):
    from . import references as R
    if a.action == "list":
        d = R.refset_definition(a.refset)
        ready, skipped = R.resolve_refset(a.refset, a.store)
        print(f"{d['id']}: {len(d['compounds'])} compounds, {len(ready)} ready; structure status: {d['structure_status']}")
        for c in d["compounds"]:
            print(f"  {c['id']:<6} {c['set']:<8} ox {c['formal_ox_state']:<4} S={c['spin_S_as_printed']:<12} delta={c['delta_exp_mm_s']} |dEQ|={c['deq_abs_exp_mm_s']}  structure={c['structure']}")
    elif a.action == "import":
        p = R.import_structure(a.refset, a.compound, a.file, a.charge, a.multiplicity, a.store or Path("refset_user"))
        print("imported ->", p)
    elif a.action == "setup":
        m = R.refset_setup(a.plan, a.out, a.refset, a.store, a.subset, a.ids, a.site)
        print(f"wrote {len(m['compounds'])} compounds to {a.out}; skipped {len(m['skipped'])}:")
        for s in m["skipped"]:
            print(f"  {s['id']}: {s['reason']}")
    elif a.action == "fit":
        e = R.refset_fit(a.dir, a.outputs, install=not a.no_install, exclude=a.exclude)
        R.refset_fit_csv(a.dir, e)
        st = e["stats"]
        print(f"calibration {e['id']}: alpha={e['alpha']:.4f} C={e['C']} beta={e['beta']:.4f}  n={st['n']} R2={st['r2']:.3f} "
              f"LOO-MAE={st['loo_mae']:.3f} mm/s; excluded: {e['excluded']}")


def cmd_report(a):
    from . import report
    r = report.verify(a.zip)
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        for c in r["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + c["check"] + (f": {c['detail']}" if c["detail"] and not c["ok"] else ""))
        if r.get("note"):
            print("note:", r["note"])
    if not r["ok"]:
        sys.exit(1)


def cmd_site(a):
    import yaml
    from .site import SiteProfile, resolve_site, save_profile, set_default_site, site_profiles
    if a.site_action == 'init':
        if a.file:
            profile = SiteProfile.from_yaml(a.file)
            profile.name = a.name
            profile.validate()
        else:
            if not a.partition or not a.sif:
                raise ValueError('Provide --partition and --sif, or --file PROFILE.yaml')
            profile = SiteProfile(name=a.name, partition=a.partition, sif=a.sif, account=a.account,
                                  extra_sbatch=a.extra_sbatch, ssh_host=a.ssh_host, remote_dir=a.remote_dir)
        print(save_profile(profile, replace=a.replace))
    elif a.site_action == 'default':
        print(set_default_site(a.name) if a.name is not None else resolve_site())
    elif a.site_action == 'list':
        rows = site_profiles()
        try:
            default, warning = resolve_site(), None
        except (ValueError, OSError, yaml.YAMLError) as exc:
            default, warning = None, str(exc)
        for row in rows:
            row['default'] = row['name'] == default
        if a.json:
            print(json.dumps(dict(profiles=rows, default=default, warning=warning), indent=2))
        else:
            for row in rows:
                print(f"{row['name']} ({row['origin']})" + (' [default]' if row['default'] else '')
                      + (' [shadows bundled profile]' if row['shadowed'] else '')
                      + (f" INVALID: {row['error']}" if row['error'] else ''))
            if warning:
                print(warning)
    else:
        profile = SiteProfile.load(a.name)
        print(f'{profile.name}: valid' if a.site_action == 'validate' else profile.to_yaml())


def cmd_doctor(a):
    from .doctor import report
    result = report()
    if a.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"mossbauer {result['version']} · Python {result['python']}")
        for name, check in result['dependencies'].items():
            print(f"{name}: " + (check['version'] if check['ok'] else check['error']))
        for name in ('rdkit', 'user_store', 'site'):
            check = result[name]
            detail = check.get('detail') or check.get('error') or check.get('name') or check.get('path') or 'OK'
            print(f"{name}: {detail}")
        print(result['viewer']['detail'])
        if not result['ok']:
            print(result['repair'])
    if not result['ok']:
        raise SystemExit(1)


def cmd_gui(a):
    from .doctor import dependencies, REPAIR
    broken = [name for name, check in dependencies().items() if not check['ok']]
    if broken:
        raise SystemExit('GUI dependencies missing or broken: ' + ', '.join(broken) + '. ' + REPAIR)
    app = Path(__file__).parent / "gui" / "app.py"
    from mossbauer_dft.gui.theme import theme_flags
    sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), "--server.address", "127.0.0.1", *theme_flags(), "--",
                              *(["--workdir", a.workdir] if a.workdir else [])]))


# ----------------------------------------------------------------------------- mossbauer project ...
def _rel(root, path) -> str:
    from .project import rel_path
    return rel_path(root, path)


def _plan_kwargs(a) -> dict:
    kw = dict(oxidation_state=a.oxidation_state, radical_ligand=_radical(a), solvent=a.solvent, temperature_K=a.temperature_K,
              nitrosyl=_nitrosyl(a))
    return dict(kw, experimental=_experimental(a), overrides=_overrides(a))


def _edits(a, root) -> dict:
    from . import project as J
    ed = {k: getattr(a, k) for k in ("oxidation_state", "solvent", "temperature_K", "charge", "multiplicity") if getattr(a, k) is not None}
    if _radical(a) is not None:
        ed["radical_ligand"] = _radical(a)
    if _nitrosyl(a) is not None:
        ed["nitrosyl"] = _nitrosyl(a)
    if _experimental(a):
        ed["experimental"] = {**(J.load_version(root, a.entry)["inputs"].get("experimental") or {}), **_experimental(a)}
    ov = _overrides(a)
    for k in getattr(a, "unset", None) or []:  # the same "cleared" values the GUI edit form sends
        ov[k] = "" if k.endswith("functional") else None
    if ov:
        ed["overrides"] = ov
    return ed


def _where(root, ref) -> str:
    return f"{ref.entry_id} v{ref.version}  ({_rel(root, ref.path)})"


def cmd_project(a):
    from . import plan as P
    from . import project as J
    root = Path(getattr(a, "project", None) or ".")
    act = a.pcmd
    if act == "init":
        root = Path(a.path)
        meta = J.create_project(root, a.name)
        print(f"created project {meta['name']!r} at {root}")
        print(f"next: mossbauer project add --project {root} --structure FILE --charge Q --multiplicity M --name NAME")
    elif act == "example":
        from . import tutorial as T
        a.path = a.path or a.project
        if a.list or not a.path:
            print(json.dumps(T.list_examples(), indent=2))
            return
        refs = T.create_example(a.path, a.example, recorded_pack=a.recorded_pack)
        print(json.dumps(dict(project=str(a.path), example=a.example, entries=[r.entry_id for r in refs],
                              next=f"mossbauer project guide --project {a.path}"), indent=2))
    elif act == "guide":
        from . import tutorial as T
        print(json.dumps(T.guide(Path(a.path) if a.path else root), indent=2))
    elif act == "import":
        ref = J.import_legacy(a.path)
        print(f"imported {a.path} as a project (originals kept): entry {_where(a.path, ref)}")
    elif act == "add":
        kw = _plan_kwargs(a)
        if a.preset:
            from . import presets as PR
            kw["overrides"] = _preset_overrides(a, root, kw["overrides"])
        ref = J.add_entry(root, a.structure, a.charge, a.multiplicity, name=a.name or Path(a.structure).stem, **kw)
        print(P.summarize(J.load_version(root, ref.entry_id, ref.version)))
        print(f"\nadded entry {_where(root, ref)}  (status draft; review, then "
              f"`mossbauer project approve --project {root} --entry {ref.entry_id}`)")
    elif act == "edit":
        if a.preset:
            _edit_with_preset(a, root)
            return
        if a.preview:
            if a.structure:
                raise J.ProjectError('--preview currently applies to scientific input edits; use a new entry for replacement geometry review')
            from . import revision
            print(json.dumps(revision.preview(root, a.entry, **_edits(a, root)), indent=2))
            return
        if a.expect_hash and not P.compute_hash(J.load_version(root, a.entry)).startswith(a.expect_hash):
            raise J.ProjectError('plan changed since preview; preview again')
        if a.structure:
            ref = J.replace_structure(root, a.entry, a.structure, **_edits(a, root))
        else:
            ref = J.edit_entry(root, a.entry, **_edits(a, root))
        kind = "new version" if ref.entry_id == a.entry else "new entry (charge, multiplicity or broken-symmetry variant changed)"
        print(f"{kind}: {_where(root, ref)}  (status draft); earlier versions are unchanged")
    elif act == "list":
        for e in J.list_entries(root):
            vers = ", ".join(f"v{v.version}" for v in e.versions) or "no versions"
            print(f"{e.entry_id:<34} {e.display_name or '?':<20} {e.state_label or '?':<14} {vers}" + (f"  [{e.error}]" if e.error else ""))
    elif act == "status":
        if a.json:
            print(json.dumps(J.status_document(root), indent=2))
            return
        for e in J.scan_project(root):
            v = e.newest
            if v is None:
                print(f"{e.entry_id:<34} {e.display_name or '?':<20} {e.state_label or '?':<14} no versions")
                continue
            extra = [x for x in (v.approval_line, ", ".join(v.flags) if v.flags else None,
                                 f"{len(v.unmatched)} unmatched output(s)" if v.unmatched else None,
                                 "older version has results" if e.older_results else None) if x]
            print(f"{e.entry_id:<34} {e.display_name or '?':<20} {e.state_label or '?':<14} v{v.version:<3} {v.label}"
                  + (f"  [{'; '.join(extra)}]" if extra else ""))
    elif act == "approve":
        via = _approval_channel(a, "project approve")
        items = list(a.entry or [])
        if (a.version or a.expect_hash) and (len(items) != 1 or a.all_drafts):
            sys.exit("project approve: --version and --expect-hash apply to exactly one --entry")
        if a.version:
            items = [J.VersionRef(items[0], a.version, J.version_dir(root, items[0], a.version))]
        if a.all_drafts:
            items += [e.entry_id for e in J.scan_project(root) if e.newest and e.newest.status == "draft" and e.entry_id not in items]
        if not items:
            sys.exit("project approve: name entries with --entry ID, or pass --all-drafts" if not a.all_drafts else
                     "project approve: no entry's newest version is a draft; nothing to approve")
        expect = {a.entry[0]: a.expect_hash} if a.expect_hash else None
        for o in J.approve_versions(root, items, a.by, via, a.on_behalf_of, expect):
            tag = f"{o.entry_id} v{o.version}" if o.version else o.entry_id
            if o.outcome == "skipped":
                print(f"{tag}: skipped - {o.reason}")
            else:
                print(f"{tag}: {o.outcome} - {J.approval_line(o.approval)} at {o.approval['at']}  hash {o.approval['plan_hash'][:12]}")
    elif act == "setup":
        r = J.write_version_inputs(root, a.entry, a.version, a.site)
        vd = J.version_dir(root, a.entry, a.version)
        print(f"Site profile: {r['site']}")
        print(f"wrote inputs for {a.entry} {vd.name} to {_rel(root, vd / 'inputs')}:", *r["files"], sep="\n  ")
        print("\nsubmission commands (displayed, not run; run from the inputs folder on the cluster):")
        print("\n".join("  " + c for c in r["commands"]))
        print(f"\nput finished outputs in: {_rel(root, vd / 'outputs')}/<stem>.out")
    elif act == 'export-inputs':
        from .handoff import export_inputs
        content = export_inputs(root, a.entry, a.version, agent=a.agent, upload=(a.transfer_site or True) if a.upload else False)
        with Path(a.out).open('xb') as stream:
            stream.write(content)
        print(f'Exported approved inputs to {a.out}; upload and submission are external.')
    elif act == "analyze":
        if a.trial:
            trial = J.trial_analysis(root, a.entry, a.version, a.calibration, a.level)
            if a.csv:
                from . import workflow as W
                Path(a.csv).write_text(W.results_csv(trial))
            print(json.dumps(trial, indent=2, default=str))
            return
        r = J.analyze_version(root, a.entry, a.version, a.calibration, a.level)
        _print_analysis(r, a.csv)
        print(f"saved: {_rel(root, J.version_dir(root, a.entry, r['version']) / 'results' / J.RESULTS_FILE)}")
    elif act == "compare":
        from . import study
        selection = [] if a.select else None
        for item in a.select or []:
            entry, sep, version = item.rpartition(':')
            if not sep or not version.isdigit():
                raise J.ProjectError('--select must be ENTRY:VERSION')
            selection.append((entry, int(version)))
        doc = study.compare(root, selection)
        if a.csv:
            Path(a.csv).write_text(study.comparison_csv(doc))
        if a.json:
            print(json.dumps(doc, indent=2))
        else:
            for row in doc['rows']:
                print(f"{row['compound']} {row['entry']} v{row['version']} {row.get('functional')} "
                      f"delta={row.get('delta')} mm/s |DeltaEQ|={row.get('abs_deq')} mm/s "
                      f"[{'; '.join(row['flags']) or 'current'}]")
            for issue in doc['issues']:
                print(f"{issue['entry']} v{issue['version']}: {issue['reason']}")
            print('\n'.join(doc['notes']))
    elif act == 'report':
        from . import report, study
        selection = [] if a.select else None
        for item in a.select or []:
            entry, sep, version = item.rpartition(':')
            if not sep or not version.isdigit():
                raise J.ProjectError('--select must be ENTRY:VERSION')
            selection.append((entry, int(version)))
        data, _ = report.bundle(study.compare(root, selection), a.title)
        with Path(a.out).open('xb') as output:
            output.write(data)
        print(f'saved report: {a.out}')
    elif act == 'calibration':
        from . import calibration_workspace as C
        action = a.cal_action
        if action == 'create':
            result = C.create(root, a.workspace, a.entry, a.version)
        elif action == 'add':
            if not all(v is not None for v in (a.ref_id, a.structure, a.charge, a.multiplicity, a.delta_exp)):
                raise J.ProjectError('calibration add requires --id --structure --charge --multiplicity --delta-exp')
            result = C.add_reference(root, a.workspace, a.ref_id, a.structure, a.charge, a.multiplicity,
                                     a.delta_exp, a.deq_exp, a.training_entry)
        elif action == 'import':
            result = C.import_bundled(root, a.workspace, a.refset)
        elif action == 'prepare':
            result = C.prepare(root, a.workspace, a.entry, a.version, a.site)
        elif action == 'fit':
            result = C.fit(root, a.workspace, json.loads(a.exclusions), n_boot=a.n_boot)
        else:
            result = C.status(root, a.workspace)
        print(json.dumps(result, indent=2))
    elif act == "batch":
        _cmd_batch(a, root)
    elif act == "preset":
        _cmd_preset(a, root)
    elif act == "example-pack":
        from . import recorded
        r = recorded.export_pack(root, a.out, a.recorded_by, site=a.site, program=a.program, note=a.note)
        print(json.dumps(r, indent=2))
    elif act == "import-recorded":
        from . import recorded
        entries = list(a.entry or []) + ([e.entry_id for e in J.list_entries(root)] if a.all else [])
        if not entries:
            sys.exit("project import-recorded: name entries with --entry ID, or pass --all")
        failed = False
        for e in dict.fromkeys(entries):
            try:
                out = recorded.import_recorded(root, e)
                print(f"{e}: imported {len(out['imported'])} job(s). {out['recorded']}")
            except J.ProjectError as ex:
                failed = True
                print(f"{e}: not imported - {ex}")
        if failed:
            sys.exit(1)
    elif act == "coverage":
        from . import calcontext
        plan = J.load_version(root, a.entry, a.version)
        dom, label = calcontext.plan_domain(plan)
        print(json.dumps(dict(entry=a.entry, calibration=label, domain=dom, warnings=calcontext.coverage(plan)), indent=2))
    elif act == "adopt":
        r = J.adopt_outputs(root, a.entry, a.version)
        for name in r["adopted"]:
            print(f"adopted inputs/{name} -> outputs/{name}")
        for sk in r["skipped"]:
            print(f"skipped {sk['file']}: {sk['reason']}")
        print(f"{a.entry} v{r['version']}: {len(r['adopted'])} adopted, {len(r['skipped'])} skipped")
    elif act == "resubmit":
        resources = {}
        for flag, key, conv in ((a.time or [], "time", str), (a.mem or [], "mem_gb", float)):
            for item in flag:
                stem, sep, value = item.partition("=")
                if not sep:
                    sys.exit(f"project resubmit: expected STEM=VALUE, got {item!r}")
                resources.setdefault(stem, {})[key] = conv(value)
        r = J.write_resubmit(root, a.entry, a.version, stems=a.stem or None, include_unreturned=a.include_unreturned,
                             resources=resources, site=a.site)
        print(f"Site profile: {r['site']}")
        print(f"wrote {_rel(root, Path(r['path']))} for: {', '.join(r['stems'])}")
        print("commands (displayed, not run; run from the inputs folder on the cluster):")
        print("\n".join("  " + c for c in r["commands"]))
    elif act == "bulk":
        from . import bulk as B
        entries = list(a.entry or [])
        verb = a.bulk_action
        if verb in B.VERBS:
            if a.all:
                entries += [e for e in B.eligible(root, verb) if e not in entries]
            if not entries:
                sys.exit(f"project bulk {verb}: name entries with --entry ID, or pass --all")
            outs = B.run(root, verb, entries, site=a.site, calibration=a.calibration, level=a.level)
        else:
            if not a.preset:
                sys.exit(f"project bulk {verb}: --preset NAME is required")
            if a.all:
                entries += [e.entry_id for e in J.list_entries(root) if e.entry_id not in entries]
            if not entries:
                sys.exit(f"project bulk {verb}: name entries with --entry ID, or pass --all")
            pv = B.preview_preset(root, entries, a.preset)
            if verb == "preset-preview":
                print(json.dumps(dict(preset=pv["preset"], pin=pv["pin"], skipped=pv["skipped"],
                                      proposals=[{k: p[k] for k in ("entry", "base_version", "creates", "changes", "jobs_before",
                                                                    "jobs_after", "cost_before", "cost_after", "blockers")}
                                                 for p in pv["proposals"]]), indent=2, default=str))
                print(f"apply with: mossbauer project bulk preset-apply --project {root} --preset {a.preset} "
                      + " ".join(f"--entry {p['entry']}" for p in pv["proposals"]) + f" --expect-hash {pv['pin']}", file=sys.stderr)
                return
            if not a.expect_hash:
                sys.exit("project bulk preset-apply: pass --expect-hash PIN from `bulk preset-preview`")
            outs = B.apply_preset(root, pv, expect_pin=a.expect_hash) + pv["skipped"]
        for o in outs:
            print(f"{o['entry']}" + (f" v{o['version']}" if o['version'] else "") + f": {o['outcome']}" + (f" - {o['detail']}" if o['detail'] else ""))
        print(B.summary(outs))
        if any(o["outcome"] == "failed" for o in outs):
            sys.exit(1)
    elif act == 'family':
        from . import families as F
        if a.family_action == 'create':
            result = F.create(root, a.family, a.member or [])
        elif a.family_action in ('preview', 'generate'):
            proposal = F.preview(root, a.entry, json.loads(a.candidates))
            if a.family_action == 'generate':
                if a.expect_hash != proposal['base_hash']:
                    raise J.ProjectError('generate needs --expect-hash from a current family preview')
                result = F.generate(root, a.family, proposal)
            else:
                result = proposal
        elif a.family_action == 'compare':
            result = F.energies(root, a.family, a.tolerance)
        else:
            result = F.read(root)
        print(json.dumps(result, indent=2))
    elif act == 'harvest':
        from . import harvest
        vd = J.version_dir(root, a.entry, a.version)
        result = harvest.harvest(vd / 'inputs/harvest-manifest.json', a.outputs or vd / 'outputs')
        if Path(a.out).exists():
            raise J.ProjectError('bundle destination exists; choose a new filename')
        Path(a.out).write_text(json.dumps(result, indent=2, allow_nan=False))
        print(a.out)
    elif act == 'fetch-results':
        from . import remote
        print(json.dumps(remote.fetch_results(root, a.entry, a.version, a.host, a.remote_path, replace=a.replace), indent=2))
    elif act == 'import-results':
        from . import compact
        print(json.dumps(compact.import_bundle(root, a.entry, a.version, a.bundle, a.replace), indent=2))


UNSETTABLE = ["geometry", "delta_functional", "deq_functional", "crosscheck_functional", "calibration_strategy", "nprocs",
              "highfield", "allow_unverified_calibration", "allow_protocol_mismatch", "broken_symmetry", "user_refs"]


def _batch_sources(a):
    from . import batch as B
    if not a.structures:
        raise ValueError("give at least one --structures FILE")
    return B.load_sources([(Path(f).name, Path(f).read_text()) for f in a.structures])


def _cmd_batch(a, root):
    """template | preview | create.  Always prints one JSON document; exit 0 when the run completed (blocked rows included)."""
    from . import batch as B
    from . import project as J
    src = _batch_sources(a)
    opts = _overrides(a)
    if a.preset:
        opts = _preset_overrides(a, root, opts)
    if a.batch_action == "template":
        rows = B.rows_from_sources(src)
        out = Path(a.out or "batch.csv")
        out.write_text(B.write_csv(rows, opts))
        print(json.dumps(dict(schema_version=B.SCHEMA, csv=str(out), notes=src.notes,
                              structures=[dict(structure=r["structure"], compound=r["compound"], placeholder=f.placeholder,
                                               formula=f.structure.formula, n_atoms=len(f.structure), warnings=f.warnings)
                                          for r, f in zip(rows, src.structures.values())]), indent=2))
        return
    if not a.csv:
        raise ValueError("give --csv FILE (write one with `mossbauer project batch template`)")
    rows, recorded = B.read_csv(Path(a.csv).read_text())
    if a.batch_action == "preview":
        doc = B.preview(root, rows, src, opts, recorded)
    else:
        try:
            doc = B.create(root, rows, src, opts, expected_hash=a.expect_hash, recorded_options=recorded)
        except B.BatchError as e:
            print(json.dumps(dict(schema_version=B.SCHEMA, error=str(e), created=e.created, rows=e.rows), indent=2))
            raise
        for row in doc["rows"]:
            row["paths"] = [_rel(root, J.version_dir(root, eid)) for eid in row["entries"]] if row["outcome"] == "created" else []
    print(json.dumps(doc, indent=2))


def _preset_overrides(a, root, explicit: dict) -> dict:
    """Preset options with explicit flags winning; a bare --freq adds a frequency job to the preset's geometry level
    instead of replacing it with the default level."""
    from . import presets as PR
    out = PR.resolve(root, a.preset, explicit)
    preset_geom = PR.get(root, a.preset)["options"].get("geometry") or {}
    if getattr(a, "freq", False) and not getattr(a, "geometry", None) and not getattr(a, "geometry_as_given", None):
        out["geometry"] = dict(preset_geom or dict(mode="optimize", functional="MN15", basis="def2-SVP"), freq=True)
    elif getattr(a, "geometry", None) and preset_geom.get("freq"):
        out["geometry"] = dict(out["geometry"], freq=True)  # the frequency job is its own option: a new level keeps it
    return out


def _edit_with_preset(a, root):
    """Applying a preset always shows the revision preview; it writes the draft only with the hash that preview reported."""
    from . import presets as PR
    from . import project as J
    from . import revision
    if a.structure or a.preview or _edits(a, root):
        raise J.ProjectError("--preset replaces the method options on its own; give it without other edit flags")
    pv = PR.preview_apply(root, a.entry, a.preset)
    pin = PR.apply_pin(pv)
    if not a.expect_hash:
        print(json.dumps(dict({k: v for k, v in pv.items() if k != "proposed"}, preset_hash=pin), indent=2, default=str))
        print(f"\nnothing written; to save, re-run with --expect-hash {pin[:12]}", file=sys.stderr)
        return
    if len(a.expect_hash) < 8 or not pin.startswith(a.expect_hash):
        raise J.ProjectError("plan or preset changed since preview (or the hash is shorter than 8 characters); preview again")
    ref = revision.save(root, pv)
    print(f"new version: {_where(root, ref)}  (status draft, preset {a.preset!r}); earlier versions are unchanged")


def _cmd_preset(a, root):
    from . import presets as PR
    if a.preset_action == "list":
        items = PR.list_presets(root)
        if a.json:
            print(json.dumps(dict(schema_version=PR.SCHEMA, presets=items), indent=2))
        else:
            for p in items:
                opts = ", ".join(f"{k}={v}" for k, v in sorted(p["options"].items())) or "(all from the rules)"
                print(f"{p['name']:<24} {opts}" + (f"  -- {p['description']}" if p["description"] else ""))
            if not items:
                print("no presets in this project")
        return
    if not a.name:
        from . import project as J
        raise J.ProjectError(f"preset {a.preset_action}: give --name")
    if a.preset_action == "save":
        out = PR.save(root, a.name, _overrides(a), description=a.description or "", replace=a.replace)
    elif a.preset_action == "show":
        out = PR.get(root, a.name)
    else:
        PR.delete(root, a.name)
        out = dict(deleted=a.name)
    print(json.dumps(out, indent=2))


def _project_parser(sub):
    q = sub.add_parser("project", help="multi-entry project folder: init, example, guide, add, list, status, edit, approve, setup, analyze, bulk, import")
    ps = q.add_subparsers(dest="pcmd", required=True)

    def cmd(name, help, *, project=True, entry=False, version=False):
        r = ps.add_parser(name, help=help)
        r.set_defaults(func=cmd_project)
        if project:
            r.add_argument("--project", default=".", metavar="PATH", help="project folder (default: current directory)")
        if entry:
            r.add_argument("--entry", required=True, metavar="ID", help="entry id (see `mossbauer project list`)")
        if version:
            r.add_argument("--version", type=int, metavar="N", help="plan version (default: newest)")
        return r

    r = cmd("init", "make a folder a project", project=False)
    r.add_argument("path", nargs="?", default=".")
    r.add_argument("--name", help="project name (default: folder name)")
    r = cmd("example", "create the bundled teaching example (draft entries with literature values) in a new or empty folder",
            project=False)
    r.add_argument("path", nargs="?", help="new or empty folder for the example project")
    r.add_argument("--project", metavar="PATH", help="same as PATH, for consistency with the other project commands")
    r.add_argument("--example", default="iron-basics", help="example id (see --list)")
    r.add_argument("--recorded-pack", metavar="PACK.zip", help="an instructor's recorded real run of this example (see example-pack)")
    r.add_argument("--list", action="store_true", help="list the bundled examples")
    r = cmd("guide", "the workflow steps for this project: which are done, what is next, and why (JSON; writes nothing)")
    r.add_argument("path", nargs="?", help="project folder (same as --project)")
    r = cmd("import", "turn a single-plan work directory (plan/plan.yaml) into a one-entry project", project=False)
    r.add_argument("path")
    r = cmd("add", "new entry (one compound in one electronic state) with a draft v1 plan")
    r.add_argument("--structure", required=True, metavar="FILE", help="xyz or cif file")
    r.add_argument("--charge", type=int, required=True)
    r.add_argument("--multiplicity", type=int, required=True)
    r.add_argument("--name", help="compound name (default: structure file stem)")
    r.add_argument("--preset", metavar="NAME", help="project method preset; flags given here override its values")
    _hint_args(r)
    _override_args(r)
    r = cmd("edit", "edit an entry's newest plan: a new draft version, or a new entry when the electronic state changes", entry=True)
    r.add_argument("--charge", type=int)
    r.add_argument("--multiplicity", type=int)
    r.add_argument('--preview', action='store_true', help='show scientific changes and workload without writing')
    r.add_argument('--expect-hash', help='require the source hash reported by --preview (with --preset: its preset_hash)')
    r.add_argument("--structure", metavar="FILE", help="replacement xyz or cif (e.g. a refined geometry): the next version uses it")
    r.add_argument("--unset", action="append", metavar="KEY", choices=UNSETTABLE,
                   help="clear an override back to the rule default; repeat for several")
    r.add_argument("--preset", metavar="NAME", help="make the method options equal this project preset (prints the preview; "
                   "re-run with --expect-hash to save)")
    _hint_args(r)
    _override_args(r)
    cmd("list", "entries and their versions")
    r = cmd("status", "computed status of every entry (newest version), or --json for every version")
    r.add_argument("--json", action="store_true", help="schema-versioned document with project-relative paths (see docs/project-layout.md)")
    r = cmd("approve", "seal the newest version of one or more entries (each plan sealed individually)")
    r.add_argument("--entry", action="append", metavar="ID", help="entry id; repeat for several")
    r.add_argument("--version", type=int, metavar="N", help="the version you reviewed (one --entry only); skipped if no longer newest")
    r.add_argument("--expect-hash", metavar="HASH", help="plan_hash (or prefix) from `status --json` you reviewed; skipped if the plan changed")
    r.add_argument("--all-drafts", action="store_true", help="every entry whose newest version is a draft")
    _approval_flags(r)
    r = cmd("setup", "write ORCA inputs + Slurm scripts into a version's inputs/ (approved versions only)", entry=True, version=True)
    r.add_argument("--site", default=None)
    r = cmd('export-inputs', 'download verified files for manual transfer; optional agent instructions', entry=True, version=True)
    r.add_argument('--out', required=True, metavar='FILE.zip')
    r.add_argument('--agent', action='store_true', help='include external-agent instructions (no submission)')
    r.add_argument('--upload', action='store_true', help='include a Bash upload helper; requires SSH settings')
    r.add_argument('--transfer-site', help='profile supplying SSH settings for --upload; default: the generated site')
    r = cmd("analyze", "analyze a version's complete outputs and save results/analysis.json", entry=True, version=True)
    r.add_argument("--calibration", help="calibration YAML path or entry id (default: from the plan)")
    r.add_argument("--level", type=float, default=0.95)
    r.add_argument("--csv")
    r.add_argument("--trial", action="store_true", help="try the calibration without saving: prints the result as JSON, writes nothing")
    r = cmd('compare', 'compare selected saved project analyses without recomputing')
    r.add_argument('--select', action='append', metavar='ENTRY:VERSION', help='exact saved version; repeat for several')
    r.add_argument('--json', action='store_true')
    r.add_argument('--csv', metavar='FILE')
    r = cmd('report', 'export selected saved analyses as an offline HTML/CSV/JSON ZIP')
    r.add_argument('--select', action='append', metavar='ENTRY:VERSION')
    r.add_argument('--out', required=True, metavar='FILE.zip')
    r.add_argument('--title', default='Mössbauer study report')
    r = cmd('calibration', 'project-local reference preparation and calibration fitting')
    r.add_argument('cal_action', choices=['create', 'add', 'import', 'prepare', 'fit', 'status'])
    r.add_argument('--workspace', default='references')
    r.add_argument('--entry')
    r.add_argument('--version', type=int)
    r.add_argument('--id', dest='ref_id')
    r.add_argument('--structure')
    r.add_argument('--charge', type=int)
    r.add_argument('--multiplicity', type=int)
    r.add_argument('--delta-exp', type=float)
    r.add_argument('--deq-exp', type=float)
    r.add_argument('--training-entry')
    r.add_argument('--refset')
    r.add_argument('--site', default=None)
    r.add_argument('--exclusions', default='{}', help='JSON object mapping excluded reference ids to written reasons')
    r.add_argument('--n-boot', type=int, default=5000)
    r = cmd("batch", "many entries at once from structure files (multi-block xyz/cif, sdf) and a CSV: template, preview, create")
    r.add_argument("batch_action", choices=["template", "preview", "create"])
    r.add_argument("--structures", action="append", metavar="FILE", help="structure source; repeat for several")
    r.add_argument("--csv", metavar="FILE", help="batch sheet (preview/create)")
    r.add_argument("--out", metavar="FILE", help="template: CSV to write (default batch.csv)")
    r.add_argument("--expect-hash", help="create: the preview_hash you reviewed; refused if the preview changed")
    r.add_argument("--preset", metavar="NAME", help="project method preset; flags given here override its values")
    _override_args(r, broken_symmetry=False)
    r = cmd("example-pack", "export an example project's approved, returned v1 runs as a recorded pack for learners")
    r.add_argument("--out", required=True, metavar="PACK.zip")
    r.add_argument("--recorded-by", required=True, metavar="NAME", help="who ran the calculations")
    r.add_argument("--site", default="", help="where they ran (e.g. cluster name)")
    r.add_argument("--program", default="", help="program and build (e.g. ORCA 6.1.0)")
    r.add_argument("--note", default="")
    r = cmd("import-recorded", "import the recorded run for example entries whose plans you approved unchanged")
    r.add_argument("--entry", action="append", metavar="ID")
    r.add_argument("--all", action="store_true")
    cmd("coverage", "does the calibration an entry's plan will use cover its chemistry? (JSON; writes nothing)",
        entry=True, version=True)
    cmd("adopt", "copy outputs (and job-state files) the cluster left in inputs/ into outputs/; never overwrites",
        entry=True, version=True)
    r = cmd("resubmit", "write inputs/resubmit_failed.sh for failed jobs and their dependents (displayed, not run)",
            entry=True, version=True)
    r.add_argument("--stem", action="append", help="resubmit exactly these jobs (default: every failed job)")
    r.add_argument("--include-unreturned", action="store_true", help="also jobs with no output at all")
    r.add_argument("--time", action="append", metavar="STEM=HH:MM:SS", help="walltime override for one job; repeat")
    r.add_argument("--mem", action="append", metavar="STEM=GB", help="memory override for one job; repeat")
    r.add_argument("--site", default=None)
    r = cmd("bulk", "one action for many entries' newest versions: setup, analyze, reanalyze, preset-preview, preset-apply")
    r.add_argument("bulk_action", choices=["setup", "analyze", "reanalyze", "preset-preview", "preset-apply"])
    r.add_argument("--entry", action="append", metavar="ID", help="entry id; repeat for several")
    r.add_argument("--all", action="store_true", help="every entry that can take the action now (preset: every entry)")
    r.add_argument("--site", default=None, help="setup: site profile")
    r.add_argument("--calibration", help="analyze: calibration YAML path or entry id (default: from each plan)")
    r.add_argument("--level", type=float, default=0.95)
    r.add_argument("--preset", metavar="NAME", help="preset-preview/preset-apply: the project preset")
    r.add_argument("--expect-hash", help="preset-apply: the pin printed by preset-preview")
    r = cmd("preset", "named method presets kept in the project: save, list, show, delete")
    r.add_argument("preset_action", choices=["save", "list", "show", "delete"])
    r.add_argument("--name", metavar="NAME")
    r.add_argument("--description", default="")
    r.add_argument("--replace", action="store_true", help="save: overwrite an existing preset of this name")
    r.add_argument("--json", action="store_true", help="list: print the presets document")
    _override_args(r, broken_symmetry=False)
    r = cmd('family', 'explicit electronic-state families and compatible energy comparisons')
    r.add_argument('family_action', choices=['create', 'preview', 'generate', 'compare', 'status'])
    r.add_argument('--family', default='states')
    r.add_argument('--member', action='append')
    r.add_argument('--entry')
    r.add_argument('--candidates', default='[]', help='JSON list of candidate charge/multiplicity/broken_symmetry specifications')
    r.add_argument('--expect-hash')
    r.add_argument('--tolerance', type=float, default=1.0, help='near-degeneracy threshold in kJ/mol')
    r = cmd('harvest', 'extract compact evidence from ORCA outputs', entry=True, version=True)
    r.add_argument('--outputs', type=Path)
    r.add_argument('--out', required=True)
    r = cmd('fetch-results', 'fetch and validate a compact bundle using existing SSH configuration (macOS/Linux)', entry=True, version=True)
    r.add_argument('--host', required=True, help='SSH host alias or user@hostname; configure access outside the app')
    r.add_argument('--remote-path', required=True, help='absolute remote JSON bundle filename')
    r.add_argument('--replace', action='store_true', help='archive and replace differing compact evidence; raw conflicts still fail')
    r = cmd('import-results', 'validate a compact cluster-return bundle before importing', entry=True, version=True)
    r.add_argument('--bundle', required=True)
    r.add_argument('--replace', action='store_true', help='archive older compact evidence before replacing conflicts')


def main(argv=None):
    p = argparse.ArgumentParser(prog="mossbauer", description="57Fe Mossbauer parameters from DFT (ORCA): plan-then-approve workflow")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("plan", help="create (or --update) a draft plan.yaml")
    q.add_argument("structure", nargs="?")
    q.add_argument("--charge", type=int)
    q.add_argument("--multiplicity", type=int)
    q.add_argument("--name")
    q.add_argument("--out", default="plan.yaml")
    q.add_argument("--update", metavar="PLAN", help="re-derive an existing plan with new hints/overrides")
    _hint_args(q)
    _override_args(q)
    q.set_defaults(func=cmd_plan)
    q = sub.add_parser("approve", help="mark a plan approved (records approver, time, hash)")
    q.add_argument("plan")
    _approval_flags(q)
    q.set_defaults(func=cmd_approve)
    q = sub.add_parser('doctor', help='check installation, RDKit, local storage and cluster configuration')
    q.add_argument('--json', action='store_true')
    q.set_defaults(func=cmd_doctor)
    q = sub.add_parser("setup", help="write ORCA inputs + Slurm scripts (approved plans only)")
    q.add_argument("plan")
    q.add_argument("--out", default="inputs")
    q.add_argument("--site", default=None)
    q.set_defaults(func=cmd_setup)
    q = sub.add_parser("analyze", help="parse ORCA outputs and report delta/DeltaEQ/eta/<S2>/Fe spin")
    q.add_argument("plan", nargs="?")
    q.add_argument("outputs", nargs="+")
    q.add_argument("--calibration", help="calibration YAML path or entry id (default: from the plan)")
    q.add_argument("--level", type=float, default=0.95)
    q.add_argument("--all", action="store_true", help="also analyze outputs whose file stem is not a job stem of the plan")
    q.add_argument("--csv")
    q.set_defaults(func=lambda a: (setattr(a, "outputs", a.outputs), cmd_analyze(a))[1])
    q = sub.add_parser("refset", help="reference-set calibration workflow")
    rs = q.add_subparsers(dest="action", required=True)
    for act in ("list", "import", "setup", "fit"):
        r = rs.add_parser(act)
        r.set_defaults(func=cmd_refset)
        r.add_argument("--refset", default=None, help="bundled refset id (default: mcwilliams2017 if its structures are present, else fe1_calib18)")
        r.add_argument("--store", type=Path, help="user refset directory holding imported structures")
        if act == "import":
            r.add_argument("compound")
            r.add_argument("file")
            r.add_argument("--charge", type=int, required=True)
            r.add_argument("--multiplicity", type=int)
        if act == "setup":
            r.add_argument("plan")
            r.add_argument("--out", default="refset")
            r.add_argument("--subset", default="all", choices=["all", "training", "test"])
            r.add_argument("--ids", nargs="*")
            r.add_argument("--site", default=None)
        if act == "fit":
            r.add_argument("dir")
            r.add_argument("--outputs")
            r.add_argument("--exclude", nargs="*")
            r.add_argument("--no-install", action="store_true")
    q = sub.add_parser("report", help="check an exported study report ZIP: hashes, re-derived delta values, status rules")
    q.add_argument("report_action", choices=["verify"])
    q.add_argument("zip", help="study-report.zip from `project report` or the Study page")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_report)
    q = sub.add_parser('site', help='configure personal Slurm cluster profiles')
    site_sub = q.add_subparsers(dest='site_action', required=True)
    for action in ('init', 'validate', 'list', 'show', 'default'):
        r = site_sub.add_parser(action)
        r.set_defaults(func=cmd_site)
        if action in ('init', 'validate', 'show'):
            r.add_argument('name')
        elif action == 'default':
            r.add_argument('name', nargs='?')
        if action == 'list':
            r.add_argument('--json', action='store_true')
        if action == 'init':
            r.add_argument('--file', type=Path, help='copy and validate an existing YAML profile')
            r.add_argument('--partition')
            r.add_argument('--sif', help='absolute cluster container path')
            r.add_argument('--account')
            r.add_argument('--extra-sbatch', action='append', default=[])
            r.add_argument('--ssh-host')
            r.add_argument('--remote-dir')
            r.add_argument('--replace', action='store_true')
    _project_parser(sub)
    q = sub.add_parser("gui", help="launch the Streamlit GUI")
    q.add_argument("--workdir")
    q.set_defaults(func=cmd_gui)
    a = p.parse_args(argv)
    # analyze: allow `analyze OUTPUTS...` without a plan (first positional is a plan only if it is YAML)
    if a.cmd == "analyze":
        if a.plan and not a.plan.endswith((".yaml", ".yml")):
            a.outputs = [a.plan] + a.outputs
            a.plan = None
    try:
        a.func(a)
    except Exception as e:  # concise CLI errors
        if "--debug" in (argv or sys.argv):
            raise
        cmd = a.cmd + (f" {a.pcmd}" if getattr(a, "pcmd", None) else "")
        sys.exit(f"mossbauer {cmd}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
