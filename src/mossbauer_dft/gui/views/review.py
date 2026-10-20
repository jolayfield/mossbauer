"""Plan review page (step 2): review, edit as a new revision, approve."""
from __future__ import annotations
import json
import os

import pandas as pd
import streamlit as st

from mossbauer_dft import plan as P
from mossbauer_dft import presets as PR
from mossbauer_dft import project as J
from mossbauer_dft.gui.state import Current, _cb_seal, _invalidate, ss
from mossbauer_dft.gui.widgets import _cli, _header, _need_entry, _preset_names


def _cb_clear_edit_preview():
    ss._edit_preview = None  # a preview built for the previously chosen preset must not be saved under the new choice


def _cb_version(key: str):
    ss.version = ss[key]


def page_review():
    st.header("Plan review")
    cur = _need_entry()
    if cur is None:
        return
    eid, root = cur.entry.entry_id, cur.root
    nums = [v.version for v in cur.entry.versions]
    key = f"vsel_{eid}"
    ss[key] = cur.vs.version
    st.selectbox("Version", nums, key=key, on_change=_cb_version, args=(key,),
                 format_func=lambda n: f"v{n}" + ("  (newest)" if n == nums[-1] else "  (older version, read-only)"))
    _header(cur)
    if ss.get("_flash_review"):
        st.success(ss.pop("_flash_review"))
    plan = cur.plan
    if plan is None:
        st.error(f"v{cur.vs.version} cannot be read: {cur.vs.error}")
        return
    if not cur.newest:
        st.caption(f"older version: read-only. Edits and approval act on the newest version, v{nums[-1]}.")
    _cli(f"mossbauer project edit --project {root} --entry {eid} ...   then   mossbauer project approve --project {root} --entry {eid} --by NAME")
    ev, m, c = plan["rule_evaluation"], plan["methods"], plan["calibration"]
    if cur.vs.approval_line:
        a = cur.vs.approval
        st.caption(f"{cur.vs.approval_line[0].upper()}{cur.vs.approval_line[1:]} at {a.get('at')} (hash `{str(a.get('plan_hash'))[:12]}`)")
    elif "modified" in cur.vs.flags:
        st.error("plan.yaml was modified after approval (hash mismatch): it is not approved.")
    k = st.columns(4)
    k[0].metric("Rule", ev["selected_rule"], help="; ".join(ev["match_reasons"]))
    k[1].metric("delta method", f"{m['delta']['functional']}/{m['delta']['basis']}",
                help=f"+ {m['delta']['fe_basis']} on Fe" if m["delta"]["fe_basis"] else None)
    k[2].metric("DeltaEQ method", m["deq"]["functional"],
                help=f"cross-check {m['deq']['crosscheck_functional']}" if m["deq"]["crosscheck_functional"] else None)
    k[3].metric("Estimated cost", f"{plan['cost']['total_core_hours']} core-h", help="UNVALIDATED order-of-magnitude model")
    for x in plan["open_items"]:
        st.error("OPEN: " + x)
    src_comment = (plan.get("_structure_xyz") or "").split("\n")[1:2]
    if src_comment and "built by RDKit" in src_comment[0] and plan["geometry"]["mode"] == "as_given":
        st.error("The structure was built by RDKit but the geometry is 'as_given': optimize it before property jobs.")
    from mossbauer_dft import calcontext
    cover = calcontext.coverage(plan)
    if cover:
        with st.expander(f"Calibration coverage ({sum(w['level'] == 'warning' for w in cover)} warning(s))",
                         expanded=any(w["level"] == "warning" for w in cover)):
            st.caption("Read before approving: does the calibration this plan will use cover this compound's chemistry?")
            for w in cover:
                (st.warning if w["level"] == "warning" else st.caption)(w["text"])

    t_m, t_c, t_j, t_e = st.tabs(["Methods", "Calibration", "Jobs and cost", "Adjust plan"])
    with t_m:
        st.write(f"Matched rule **{ev['selected_rule']}** (priority {ev['priority']}): {'; '.join(ev['match_reasons'])}")
        st.write(f"**delta**: {m['delta']['functional']}/{m['delta']['basis']}" + (f" + {m['delta']['fe_basis']} on Fe" if m['delta']['fe_basis'] else "")
                 + f"   |   **DeltaEQ**: {m['deq']['functional']}" + (f" (cross-check {m['deq']['crosscheck_functional']})" if m['deq']['crosscheck_functional'] else ""))
        st.write("**Justification:** " + str(ev["justification"]))
        st.write("**Citations (DOI):** " + ", ".join(f"[{d}](https://doi.org/{d})" for d in ev["citations"]))
        for x in ev["conflicts"] + ev["warnings"]:
            st.warning(x)
        with st.expander("Global constraints (R00)"):
            for x in ev["global_constraints"]:
                st.write("- " + x)
        with st.expander("All matched / rejected rules"):
            st.dataframe(pd.DataFrame([dict(id=r["id"], priority=r["priority"], matched=True, reasons="; ".join(r["reasons"])) for r in ev["matched"]]
                                      + [dict(id=r["id"], priority=None, matched=False, reasons="; ".join(r["reasons"])) for r in ev["rejected"]]),
                         hide_index=True, use_container_width=True)
    with t_c:
        st.write(f"Strategy: **{c['strategy']}** (requested: {c['requested']})")
        for x in c.get("caveats", []):
            st.error(x)
        if "refset" in c:
            st.info(f"No verified, protocol-matched calibration: compute reference set `{c['refset']['id']}` "
                    f"({c['refset']['n_ready']}/{c['refset']['n_total']} compounds have structures). {c['refset']['note']}")
        st.dataframe(pd.DataFrame([dict(id=x["id"], source=x["source"], verified=x["verified"], protocol_matched=x["protocol_matched"],
                                        hard="; ".join(x["hard"]), soft="; ".join(x["soft"])) for x in c["candidates"]]),
                     hide_index=True, use_container_width=True)
    with t_j:
        st.dataframe(pd.DataFrame([dict(stem=j["stem"], kind=j["kind"], method=j["method"], purpose=", ".join(j["purpose"]),
                                        depends_on=", ".join(j["depends_on"]), core_hours_est=j.get("core_hours_est")) for j in plan["jobs"]]),
                     hide_index=True, use_container_width=True)
        st.caption(f"Total ~{plan['cost']['total_core_hours']} core-h. Model: {plan['cost']['model']['description']} {plan['cost']['model']['validity']}")
    with t_e:
        if cur.newest:
            names = _preset_names(cur.root)
            if names:
                p1, p2 = st.columns([3, 1], vertical_alignment="bottom")
                pick = p1.selectbox("Apply a project preset", names, key="review_preset", on_change=_cb_clear_edit_preview,
                                    help="Makes this entry's method options equal the preset's; broken symmetry and references stay.")
                if p2.button("Preview preset", key="review_preset_go"):
                    try:
                        ss._edit_preview = dict(root=str(cur.root), proposal=PR.preview_apply(cur.root, cur.entry.entry_id, pick))
                    except (J.ProjectError, P.PlanError) as e:
                        st.error(f"Preset {pick!r} cannot be applied to this entry: {e}")
            _edit_form(cur)
        else:
            st.info(f"This is an older version and stays as it is. Select v{nums[-1]} (newest) to edit.")

    with st.container(border=True):
        st.markdown("#### Approve")
        st.caption("Approval records your name, the time, a content hash and `via: gui`; inputs can only be generated from an "
                   "approved, unmodified plan. To approve several entries at once, select them in the Project overview.")
        a1, a2, a3 = st.columns([2, 1, 1], vertical_alignment="bottom")
        if cur.newest:
            a1.text_input("Approver name", value=os.environ.get("USER", ""), key="approver")
            a2.button("Approve plan", type="primary", disabled=cur.vs.approved, use_container_width=True,
                      on_click=_cb_seal, args=([dict(entry=eid, _ref=J.VersionRef(eid, cur.vs.version, cur.vs.path),
                                                   _hash=P.compute_hash(plan))],))
        else:
            a1.caption("Older versions are read-only and cannot be approved here.")
        for result in ss.get("_approve_results") or []:
            if result["entry"] == eid and result["outcome"] == "skipped":
                st.error(f"Not approved: {result['detail']}")
        a3.download_button("Download plan.yaml", (cur.vs.path / "plan.yaml").read_bytes(), "plan.yaml", use_container_width=True)


def _edit_form(cur: Current):
    plan = cur.plan
    ov = plan["inputs"]["overrides"]
    st.caption(f"Edits never overwrite v{cur.vs.version}: they write the next version as a draft (its outputs and results stay "
               "with this version). Changing the broken-symmetry flip atoms changes the electronic state, so it creates a separate "
               "entry instead; the original entry is left unchanged.")
    with st.form(f"edit_{cur.entry.entry_id}_{cur.vs.version}"):
        st.markdown('**Scientific inputs and conditions**')
        inp, hints = plan['inputs'], plan['inputs']['hints']
        q1, q2, q3 = st.columns(3)
        charge = q1.number_input('Plan charge', value=int(inp['charge']), step=1)
        mult = q2.number_input('Plan multiplicity', value=int(inp['multiplicity']), min_value=1, step=1)
        ox = q3.selectbox('Plan Fe oxidation state', [None, 0, 1, 2, 3, 4, 5, 6],
                          index=[None, 0, 1, 2, 3, 4, 5, 6].index(hints.get('oxidation_state')))
        q1, q2, q3 = st.columns(3)
        solvent = q1.text_input('Plan solvent', value=hints.get('solvent') or '')
        temperature = q2.number_input('Plan temperature (K; 0 = unspecified)', value=float(hints.get('temperature_K') or 0), min_value=0.0)
        tri = [None, False, True]
        radical = q3.selectbox('Plan radical ligand', tri, index=tri.index(hints.get('radical_ligand')),
                              format_func=lambda x: 'not given' if x is None else 'yes' if x else 'no')
        nitrosyl = st.selectbox('Plan nitrosyl', tri, index=tri.index(hints.get('nitrosyl')),
                               format_func=lambda x: 'not given' if x is None else 'yes' if x else 'no')
        ex1, ex2 = st.columns(2)
        exp_delta = ex1.number_input('Plan experimental delta (mm/s)', value=(inp.get('experimental') or {}).get('delta'), format='%.3f')
        exp_deq = ex2.number_input('Plan experimental |DeltaEQ| (mm/s)', value=(inp.get('experimental') or {}).get('deq'), format='%.3f')
        st.markdown("**Methods**")
        c1, c2, c3 = st.columns(3)
        deq = c1.text_input("DeltaEQ functional", value=ov.get("deq_functional", ""), placeholder="from rule")
        dl = c2.text_input("delta functional", value=ov.get("delta_functional", ""), placeholder="from rule")
        cross = c3.text_input("Cross-check functional", value=ov.get("crosscheck_functional", ""), placeholder="from rule ('none' = off)")
        st.markdown("**Geometry**")
        g1, g2, g3 = st.columns(3)
        g = plan["geometry"]
        gm = g1.selectbox("Geometry", ["optimize", "as_given"], index=0 if g["mode"] == "optimize" else 1)
        gf = g2.text_input("Geometry functional/basis", value=f"{g['functional']}/{g['basis']}")
        freq = g3.checkbox("Frequency job", value=g.get("freq", False))
        st.markdown("**Calibration**")
        k1, k2, k3 = st.columns(3)
        strat = k1.selectbox("Calibration strategy", ["auto", "lookup", "compute_refset", "user_refs"],
                             index=["auto", "lookup", "compute_refset", "user_refs"].index(ov.get("calibration_strategy") or "auto"))
        unv = k2.checkbox("Allow UNVERIFIED calibration", value=bool(ov.get("allow_unverified_calibration")))
        mm = k3.checkbox("Allow protocol (geometry) mismatch", value=bool(ov.get("allow_protocol_mismatch")))
        user_refs = st.text_area('Reference compounds (JSON; for user_refs strategy)',
                                value=json.dumps(ov.get('user_refs') or [], indent=2),
                                help='Each reference needs name, xyz, charge, multiplicity and delta_exp. The Calibration workbench can supply project references.')
        st.markdown("**Broken symmetry and resources**")
        b1, b2, b3 = st.columns(3)
        bsf = b1.text_input("BS flip atoms (0-based, comma-separated)", value=",".join(map(str, (ov.get("broken_symmetry") or {}).get("flip_atoms", []))),
                            help="Turn on 'Atom indices' in the 3D view on page 1 to read these off the structure.")
        bsm = b2.number_input("BS FinalMs", value=float((ov.get("broken_symmetry") or {}).get("final_ms") or 0.0))
        nprocs = b3.number_input("nprocs", value=int(plan["resources"]["nprocs"]), min_value=1)
        hf = st.checkbox("Add highfield_sp (A, g, ZFS)", value=bool(ov.get("highfield")))
        sub = st.form_submit_button('Preview changes')
    if sub:
        try:
            o = dict(deq_functional=deq or None, delta_functional=dl or None, crosscheck_functional=cross or None, calibration_strategy=strat,
                     user_refs=json.loads(user_refs),
                     allow_unverified_calibration=unv, allow_protocol_mismatch=mm, nprocs=int(nprocs), highfield=hf,
                     geometry=dict(mode=gm, functional=gf.split("/")[0], basis=(gf.split("/") + ["def2-SVP"])[1], freq=freq,
                                   **({"as_given_label": gf} if gm == "as_given" else {})),
                     broken_symmetry=dict(kind="flipspin", flip_atoms=[int(x) for x in bsf.split(",") if x.strip()], final_ms=bsm) if bsf.strip() else None)
            # project.edit_entry merges overrides into the newest version's: a cleared field is sent as its "unset" value
            # ("" for a functional, None for broken symmetry), which make_plan reads exactly like an absent key
            if (ov.get("broken_symmetry") or {}).get("kind") not in (None, "flipspin") and not bsf.strip():
                o.pop("broken_symmetry")  # a spec this form cannot show (e.g. na/nb from the CLI) is kept, not cleared
            edit = {k: v for k, v in o.items() if v not in (None, "")}
            edit.update({k: ("" if k.endswith("functional") else None) for k, v in o.items() if v in (None, "") and ov.get(k) not in (None, "")})
            from mossbauer_dft import revision
            experimental = dict(inp.get('experimental') or {})
            for key, value in [('delta', exp_delta), ('deq', exp_deq)]:
                if value is None:
                    experimental.pop(key, None)
                else:
                    experimental[key] = value
            proposal = revision.preview(cur.root, cur.entry.entry_id, overrides=edit, charge=int(charge),
                                        multiplicity=int(mult), oxidation_state=ox, radical_ligand=radical,
                                        nitrosyl=nitrosyl, solvent=solvent or None, temperature_K=temperature or None,
                                        experimental=experimental)
            ss._edit_preview = dict(root=str(cur.root), proposal=proposal)
        except Exception as e:  # noqa: BLE001
            st.error(f"{type(e).__name__}: {e}")
    stored = ss.get('_edit_preview')
    if stored and stored['root'] == str(cur.root) and stored['proposal']['entry'] == cur.entry.entry_id:
        proposal = stored['proposal']
        st.markdown('**Review proposed changes**')
        st.caption(f"Creates a new {proposal['creates']}; jobs {proposal['jobs_before']} → {proposal['jobs_after']}; "
                   f"estimated core-hours {proposal['cost_before']} → {proposal['cost_after']} (unvalidated estimate).")
        st.dataframe(pd.DataFrame([dict(field=c['field'], before=str(c['before']), after=str(c['after']))
                                   for c in proposal['changes']]), hide_index=True, use_container_width=True)
        for blocker in proposal['blockers']:
            st.warning('Approval blocked until resolved: ' + blocker)
        st.button('Save reviewed changes', type='primary', on_click=_cb_save_revision,
                  args=(str(cur.root), proposal))


def _cb_save_revision(root, proposal):
    from mossbauer_dft import revision
    try:
        ref = revision.save(root, proposal)
        ss.entry, ss.version = ref.entry_id, ref.version
        ss._edit_preview = None
        ss._flash_review = f'Wrote {ref.entry_id} v{ref.version} as a draft. Prior versions are unchanged; review before approval.'
        _invalidate()
    except (J.ProjectError, P.PlanError) as ex:
        ss._flash_review = f'Changes were not saved: {ex}'

