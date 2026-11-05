"""The release gate's lock on the packager and the build stamp (5.3.0).

Marcelo, 4 Nov 2026: versions were reaching the team on the strength of
"tested", while what had been tested was the unit suite. The packager now
refuses a FINAL build unless qa/gate.py said GO for this exact source; a
release CANDIDATE (--label rcN) is always allowed and says so on screen.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _packager():
    spec = importlib.util.spec_from_file_location("make_package_530",
                                                  ROOT / "tools" / "make_package.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MP = _packager()
FP = "ab" * 32


def test_candidate_labels_are_recognised():
    v = MP.VERSION
    assert MP.build_label("rc2") == (f"{v}-rc2", True)
    assert MP.build_label("public-rc1") == (f"{v}-rc1", True)
    assert MP.build_label("dev") == (f"{v}-dev", True)
    assert MP.build_label("") == (v, False)
    assert MP.build_label("public") == (v, False)
    assert MP.build_label("rcx") == (v, False)


def test_final_build_is_refused_without_a_report(tmp_path):
    with pytest.raises(SystemExit) as e:
        MP.require_gate(FP, gate_dir=tmp_path)
    assert "refusing to build a FINAL package" in str(e.value)


def _report(tmp_path, **kw):
    body = {"verdict": "GO", "version": MP.VERSION, "fingerprint": FP,
            "build": f"{MP.VERSION}-rc1", "when": "2026-11-04T15:00:00"}
    body.update(kw)
    (tmp_path / "gate-x.json").write_text(json.dumps(body), encoding="utf-8")


def test_a_go_report_for_the_same_source_opens_the_lock(tmp_path):
    _report(tmp_path)
    assert MP.require_gate(FP, gate_dir=tmp_path).startswith("release gate GO")


@pytest.mark.parametrize("change", [{"verdict": "NO-GO"}, {"fingerprint": "cd" * 32},
                                    {"version": "0.0.1"}])
def test_no_go_other_source_or_other_version_keeps_it_shut(tmp_path, change):
    _report(tmp_path, **change)
    with pytest.raises(SystemExit):
        MP.require_gate(FP, gate_dir=tmp_path)


def test_skipping_the_gate_is_possible_but_written_down(tmp_path):
    note = MP.require_gate(FP, skip_gate="Marcelo: hotfix on site", gate_dir=tmp_path)
    assert note == "built WITHOUT the release gate: Marcelo: hotfix on site"


def test_qa_harness_is_not_shipped():
    assert MP.wanted(ROOT / "qa" / "gate.py") is False


def test_qa_and_packager_compute_the_same_fingerprint():
    qa = ROOT / "qa" / "fingerprint.py"
    if not qa.exists():
        pytest.skip("qa/ is a development folder, not shipped")
    spec = importlib.util.spec_from_file_location("qa_fp", qa)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    lines = ["0123456789abcdef  backend/app/x.py", "fedcba9876543210  VERSION"]
    assert mod.fingerprint_of(lines) == MP.fingerprint_of(lines)


def test_setup_removes_the_build_stamp_from_the_unzipped_package():
    setup = ROOT / "setup.bat"
    if not setup.exists():
        # setup.bat is left out of an installation (robocopy /XF): this test
        # runs in the development tree and in the package, not in run-tests
        # of an installed copy. Found by the release gate on 5.3.0-rc2.
        pytest.skip("setup.bat is not copied into an installation")
    text = setup.read_text(encoding="utf-8")
    assert "PACKAGE.txt MANIFEST.txt BUILD setup.bat" in text


def test_build_stamp_is_read_and_garbage_is_ignored(tmp_path, monkeypatch):
    from app import version as V
    monkeypatch.setattr(V, "BUILD_FILE", tmp_path / "BUILD")
    assert V.read_build("5.3.0") == "5.3.0"                       # no file
    (tmp_path / "BUILD").write_text("5.3.0-rc2\n", encoding="utf-8")
    assert V.read_build("5.3.0") == "5.3.0-rc2"
    (tmp_path / "BUILD").write_text("9.9.9-rc1\n", encoding="utf-8")
    assert V.read_build("5.3.0") == "5.3.0"                       # another version
    (tmp_path / "BUILD").write_text("5.3.0; rm -rf\n", encoding="utf-8")
    assert V.read_build("5.3.0") == "5.3.0"


def test_healthz_reports_the_build():
    from fastapi.testclient import TestClient

    from app.main import app
    body = TestClient(app).get("/healthz").json()
    assert body["build"].startswith(body["version"])
