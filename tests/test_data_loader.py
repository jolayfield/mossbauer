from mossbauer_dft import data


def test_reference_data_uses_utf8_with_windows_default_encoding(tmp_path, monkeypatch, windows_text_encoding):
    label = "Ł Mössbauer Δρ"
    for name in ("method_rules.yaml", "calibrations.yaml", "personal.yaml"):
        (tmp_path / name).write_text(f"label: {label}\n", encoding="utf-8")
    (tmp_path / "training_set.csv").write_text(f"label\n{label}\n", encoding="utf-8")
    (tmp_path / "method_rules.md").write_text(label, encoding="utf-8")
    monkeypatch.setattr(data, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data, "REFSETS_DIR", tmp_path)
    assert data.load_method_rules() == {"label": label}
    assert data.load_calibrations() == {"label": label}
    assert data.load_refset("personal") == {"label": label}
    assert data.load_training_set() == [{"label": label}]
    assert data.load_method_rules_md() == label


def test_loaders_tolerate_missing_files():
    a = data.available()
    assert set(a) == {"calibrations.yaml", "training_set.csv", "method_rules.yaml"}
    if not a["calibrations.yaml"]:
        assert data.load_calibrations() is None
    if not a["training_set.csv"]:
        assert data.load_training_set() is None
    if not a["method_rules.yaml"]:
        assert data.load_method_rules() is None
