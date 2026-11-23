"""5.9.0-rc8 - the upload limit is 1024 MB (it was 200).

A whole-venue book of 222 MB was refused on 22 Nov 2026 ("file is 222 MB, over
the 200 MB limit"). The end-to-end check with a book over the old limit is
``qa/big_book_rc8.py``; here, the rule and the sentence.
"""
from __future__ import annotations

import io
import os

import pytest

from tests.test_ner_register import TMP  # noqa: F401  (sets the env)

from app import services as S
from app.config import Settings, settings


def test_the_default_is_a_gigabyte():
    # Not by reloading app.config: a reload makes a second ``settings`` and
    # the tests that point the first one at their own folder lose it.
    import re
    from pathlib import Path
    import app.config as config
    src = Path(config.__file__).read_text(encoding="utf-8")
    assert re.search(r'NER_MAX_UPLOAD_MB", "1024"\)', src)
    if not os.environ.get("NER_MAX_UPLOAD_MB"):
        assert Settings.model_fields["max_upload_mb"].default == 1024


def test_a_222_mb_book_is_under_the_limit():
    assert 222 < Settings().max_upload_mb or os.environ.get("NER_MAX_UPLOAD_MB")


def test_over_the_limit_is_refused_with_the_way_out(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    data = io.BytesIO(b"%PDF-1.7\n" + os.urandom(3 << 20))
    with pytest.raises(S.ValidationError) as e:
        S.store_upload(999999, "big.pdf", data, allowed_suffixes=(".pdf",))
    msg = str(e.value)
    assert "over the 1 MB limit" in msg and "NER_MAX_UPLOAD_MB" in msg
    assert "split the book" in msg.lower()
    folder = settings.uploads_dir / "project_999999"
    assert not any(folder.glob("big*.pdf"))          # nothing left behind


def test_under_the_limit_is_kept(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 5)
    data = io.BytesIO(b"%PDF-1.7\n" + os.urandom(2 << 20))
    p = S.store_upload(999998, "ok.pdf", data, allowed_suffixes=(".pdf",))
    assert p.exists() and p.stat().st_size > 2 << 20
    p.unlink()
