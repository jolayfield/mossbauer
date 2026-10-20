"""Re-checkable reports and supporting-information drafts (second-pass idea 6; decisions D59-D60)."""
import hashlib
import io
import json
import csv
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from mossbauer_dft import report, study, trust
from mossbauer_dft.cli import main
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj  # noqa: F401
from test_study import saved


def _doc(proj, fe1):
    saved(proj, fe1)
    return study.compare(proj)


def _rewrite(data, name, mutate):
    src, out = ZipFile(io.BytesIO(data)), io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        for n in src.namelist():
            body = src.read(n)
            z.writestr(n, mutate(body) if n == name else body)
    return out.getvalue()


def _replace_member(data, name, body):
    bad = _rewrite(data, name, lambda _: body)
    with ZipFile(io.BytesIO(bad)) as z:
        manifest = json.loads(z.read("MANIFEST.json"))
    manifest["members"][name] = hashlib.sha256(body).hexdigest()
    return _rewrite(bad, "MANIFEST.json", lambda _: json.dumps(manifest).encode())


def _replace_study(data, body):
    """Keep CSV agreement intact when testing a scientific JSON fault."""
    changed = _replace_member(data, "study.json", body)
    return _replace_member(changed, "results.csv", study.comparison_csv(json.loads(body)).encode())


def test_fresh_report_verifies(proj, fe1, tmp_path):
    data, _ = report.bundle(_doc(proj, fe1))
    r = report.verify(io.BytesIO(data))
    assert r["ok"], r["checks"]
    assert "not re-checked" in r["note"]


def test_uncalibrated_report_without_delta_still_verifies():
    data, _ = report.bundle({"rows": [dict(entry="uncalibrated", version=1, rho0=11801.25, delta=None, delta_status="uncalibrated")]})
    outcome = report.verify(io.BytesIO(data))
    assert outcome["ok"], outcome["checks"]
    assert any("(0 values)" in c["check"] for c in outcome["checks"])


def test_tampered_member_fails_the_hash_check(proj, fe1):
    data, _ = report.bundle(_doc(proj, fe1))
    bad = _rewrite(data, "si_table.csv", lambda b: b + b"x")
    r = report.verify(io.BytesIO(bad))
    assert not r["ok"] and not next(c for c in r["checks"] if c["check"].startswith("member hashes"))["ok"]


def _calibrated_doc(proj, fe1):
    doc = _doc(proj, fe1)
    for row in doc["rows"]:
        snap = row.setdefault("calibration_snapshot", {})
        snap["model"] = dict(alpha=-0.4, C=11800.0, beta=0.5)
        row["rho0"] = 11801.25
        row["delta"] = -0.4 * (11801.25 - 11800.0) + 0.5
    return doc


def test_rederivation_passes_for_consistent_values_and_fails_for_edited_ones(proj, fe1):
    doc = _calibrated_doc(proj, fe1)
    data, _ = report.bundle(doc)
    good = report.verify(io.BytesIO(data))
    assert good["ok"] and any(c["check"].startswith("δ re-derived from ρ(0) and calibration constants (") and "(0 values)" not in c["check"]
                              for c in good["checks"])

    def edit(body):
        d = json.loads(body)
        for r in d["rows"]:
            r["delta"] += 0.5
        return json.dumps(d).encode()
    with ZipFile(io.BytesIO(data)) as z:
        forged = _replace_study(data, edit(z.read("study.json")))
    r = report.verify(io.BytesIO(forged))
    assert next(c for c in r["checks"] if c["check"].startswith("CSV and JSON"))["ok"]
    assert not r["ok"] and not next(c for c in r["checks"] if c["check"].startswith("δ re-derived"))["ok"]


@pytest.mark.parametrize("field", ["delta", "rho0", "alpha", "C", "beta"])
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999", "true"])
def test_invalid_numbers_fail_even_with_matching_member_hashes(proj, fe1, field, literal):
    data, _ = report.bundle(_calibrated_doc(proj, fe1))
    with ZipFile(io.BytesIO(data)) as z:
        public = json.loads(z.read("study.json"))
    row = public["rows"][0]
    target = row if field in ("delta", "rho0") else row["calibration_snapshot"]["model"]
    if literal == "true":
        # Keep the arithmetic consistent: a boolean must fail on type, not by chance on value.
        model = row["calibration_snapshot"]["model"]
        if field == "delta":
            model.update(alpha=0.0, beta=1.0)
        else:
            target[field] = True
            row["delta"] = model["alpha"] * (row["rho0"] - model["C"]) + model["beta"]
    target[field] = "INVALID_NUMBER"
    bad = _replace_study(data, json.dumps(public).replace('"INVALID_NUMBER"', literal).encode())
    outcome = report.verify(io.BytesIO(bad))
    assert not outcome["ok"]
    assert next(c for c in outcome["checks"] if c["check"].startswith("member hashes"))["ok"]
    if literal == "true":
        assert next(c for c in outcome["checks"] if c["check"].startswith("CSV and JSON"))["ok"]
    assert any(not c["ok"] for c in outcome["checks"] if not c["check"].startswith("member hashes"))


@pytest.mark.parametrize("rho,alpha", [(1e308, 1e308), (10 ** 400, 0.4)])
def test_rederivation_overflow_is_a_failed_check(proj, fe1, rho, alpha):
    data, _ = report.bundle(_calibrated_doc(proj, fe1))
    with ZipFile(io.BytesIO(data)) as z:
        public = json.loads(z.read("study.json"))
    public["rows"][0]["rho0"] = rho
    public["rows"][0]["calibration_snapshot"]["model"]["alpha"] = alpha
    bad = _replace_study(data, json.dumps(public).encode())
    outcome = report.verify(io.BytesIO(bad))
    assert outcome["ok"] is False
    assert next(c for c in outcome["checks"] if c["check"].startswith("CSV and JSON"))["ok"]


def test_cli_rejects_invalid_numeric_report_without_changing_it(proj, fe1, tmp_path, capsys):
    data, _ = report.bundle(_calibrated_doc(proj, fe1))
    with ZipFile(io.BytesIO(data)) as z:
        public = json.loads(z.read("study.json"))
    public["rows"][0]["delta"] = float("nan")
    bad = _replace_study(data, json.dumps(public).encode())
    path = tmp_path / "invalid.zip"
    path.write_bytes(bad)
    with pytest.raises(SystemExit) as error:
        main(["report", "verify", str(path), "--json"])
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert path.read_bytes() == bad


@pytest.mark.parametrize("fault", ["value", "entry", "job", "missing_column", "extra_column",
                                  "duplicate_column", "duplicate_row", "short_row", "long_row"])
def test_csv_content_must_match_json_even_after_rehashing(proj, fe1, fault):
    data, _ = report.bundle(_calibrated_doc(proj, fe1))
    with ZipFile(io.BytesIO(data)) as z:
        reader = csv.DictReader(io.StringIO(z.read("results.csv").decode()))
        fields, rows = reader.fieldnames, list(reader)
    if fault in ("value", "entry", "job"):
        rows[0][{"value": "delta", "entry": "entry", "job": "job"}[fault]] = "99.9" if fault == "value" else "different"
    elif fault == "missing_column":
        fields.remove("delta")
        for row in rows:
            row.pop("delta")
    elif fault == "extra_column":
        fields.append("unexpected")
    elif fault == "duplicate_column":
        fields.append("delta")
    elif fault == "duplicate_row":
        assert rows[0] != rows[1]
        rows[1] = dict(rows[0])
    table = [[row.get(key, "") for key in fields] for row in rows]
    if fault == "short_row": table[0].pop()
    if fault == "long_row": table[0].append("unexpected")
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(fields)
    writer.writerows(table)
    bad = _replace_member(data, "results.csv", text.getvalue().encode())
    result = report.verify(io.BytesIO(bad))
    assert not result["ok"]
    assert next(c for c in result["checks"] if c["check"].startswith("member hashes"))["ok"]
    assert not next(c for c in result["checks"] if c["check"].startswith("CSV and JSON"))["ok"]


def test_csv_row_column_order_and_quoting_do_not_change_agreement(proj, fe1):
    doc = _calibrated_doc(proj, fe1)
    for row in doc["rows"]:
        row["compound"] = 'Fe, "Mössbauer"\nsecond line'
    data, _ = report.bundle(doc)
    with ZipFile(io.BytesIO(data)) as z:
        reader = csv.DictReader(io.StringIO(z.read("results.csv").decode()))
        fields, rows = reader.fieldnames, list(reader)
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=list(reversed(fields)), quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writeheader()
    writer.writerows(reversed(rows))
    changed = _replace_member(data, "results.csv", text.getvalue().encode())
    assert report.verify(io.BytesIO(changed))["ok"]


def test_empty_report_requires_an_empty_csv():
    data, _ = report.bundle({"rows": []})
    assert report.verify(io.BytesIO(data))["ok"]
    changed = _replace_member(data, "results.csv", b"unexpected\n")
    assert report.verify(io.BytesIO(changed))["ok"] is False


def test_repeated_json_rows_keep_their_multiplicity():
    row = dict(entry="repeated", version=1, delta=None)
    data, _ = report.bundle({"rows": [row, row]})
    assert report.verify(io.BytesIO(data))["ok"]
    with ZipFile(io.BytesIO(data)) as z:
        table = list(csv.reader(io.StringIO(z.read("results.csv").decode())))
    text = io.StringIO(newline="")
    csv.writer(text).writerows(table[:2])  # header and just one of the two equal rows
    changed = _replace_member(data, "results.csv", text.getvalue().encode())
    assert report.verify(io.BytesIO(changed))["ok"] is False


def test_malformed_csv_quote_fails_cleanly(proj, fe1):
    data, _ = report.bundle(_calibrated_doc(proj, fe1))
    with ZipFile(io.BytesIO(data)) as z:
        rows = list(csv.reader(io.StringIO(z.read("results.csv").decode())))
    text = io.StringIO(newline="")
    csv.writer(text, quoting=csv.QUOTE_ALL).writerows(rows)
    body = text.getvalue()
    assert body.endswith('"\r\n')
    changed = _replace_member(data, "results.csv", (body[:-3] + "\r\n").encode())
    assert report.verify(io.BytesIO(changed))["ok"] is False


def test_si_outputs_escape_and_carry_alerts(proj, fe1):
    doc = _doc(proj, fe1)
    for row in doc["rows"]:
        row["compound"] = "Fe_1 & {x} 50%"
        row["trust_flags"] = [trust.classify("rho0=1 is outside the calibration range [2, 3] (extrapolation)")]
        row["delta_note"] = row["trust_flags"][0]["text"]
    public = report.public_snapshot(doc)
    tex = report.si_latex(public)
    assert r"Fe\_1 \& \{x\} 50\%" in tex and r"\toprule" in tex
    csv_text = report.si_csv(public)
    assert "caution: extrapolated" in csv_text and "δ calc (mm/s)" in csv_text
    assert "Review before publication" in report.methods_text(public)


def test_bibtex_from_rule_citations():
    public = dict(rows=[dict(citations=["10.1021/ct100398m", {"doi": "10.1021/acsomega.7b00595", "year": 2017}, "not a doi"])])
    bib = report.bibtex(public)
    assert bib.count("@article{") == 2 and "year = {2017}" in bib


def test_old_report_without_manifest_is_reported_not_crashed(tmp_path):
    p = tmp_path / "old.zip"
    with ZipFile(p, "w") as z:
        z.writestr("study.json", "{}")
    r = report.verify(p)
    assert not r["ok"] and "MANIFEST" in r["checks"][0]["detail"]


def test_cli_report_verify(proj, fe1, tmp_path, capsys):
    data, _ = report.bundle(_doc(proj, fe1))
    p = tmp_path / "r.zip"; p.write_bytes(data)
    main(["report", "verify", str(p)])
    assert "FAIL" not in capsys.readouterr().out
    bad = tmp_path / "bad.zip"; bad.write_bytes(_rewrite(data, "methods.txt", lambda b: b + b"edited"))
    with pytest.raises(SystemExit):
        main(["report", "verify", str(bad)])
    with ZipFile(io.BytesIO(data)) as z:
        csv_body = z.read("results.csv").replace(b"delta,", b"wrong_delta,", 1)
    bad.write_bytes(_replace_member(data, "results.csv", csv_body))
    capsys.readouterr()
    before = bad.read_bytes()
    with pytest.raises(SystemExit) as error:
        main(["report", "verify", str(bad), "--json"])
    assert error.value.code == 1 and not json.loads(capsys.readouterr().out)["ok"]
    assert bad.read_bytes() == before


@pytest.mark.parametrize("members", [{"MANIFEST.json": "not json"}, {"MANIFEST.json": '{"members": {}}'},
                                     {"MANIFEST.json": '{"members": {}}', "study.json": '{"rows": [{"delta": "x", "rho0": 1, '
                                      '"calibration_snapshot": {"model": {"alpha": 1, "C": 0, "beta": 0}}}]}', "results.csv": "a\n1\n"}])
def test_malformed_archives_fail_cleanly(tmp_path, members):
    p = tmp_path / "m.zip"
    with ZipFile(p, "w") as z:
        for n, body in members.items():
            z.writestr(n, body)
    r = report.verify(p)
    assert r["ok"] is False and r["checks"]


def test_latex_maps_unicode_so_pdflatex_can_compile():
    tex = report._tex("[FeCl₄]²⁻ − 0.5 ± 0.1 é")
    assert all(ord(ch) < 128 for ch in tex) and "$_{4}$" in tex and r"$\pm$" in tex
