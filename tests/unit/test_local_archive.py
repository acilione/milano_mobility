import json
from pathlib import Path

import pytest

from milano_mobility.storage import LocalObjectStore


def test_local_archive_preserves_source_and_supports_quarantine(tmp_path: Path) -> None:
    store = LocalObjectStore(str(tmp_path / "archive"))
    store.ensure_buckets(["raw", "curated", "quarantine"])
    source = tmp_path / "feed.zip"
    source.write_bytes(b"original")
    uri = store.put_file("raw", "version/feed.zip", source, {})
    source.write_bytes(b"replacement")
    assert store.put_file("raw", "version/feed.zip", source, {}) == uri
    assert (store.root / "raw/version/feed.zip").read_bytes() == b"original"
    assert store.exists("raw", "version/feed.zip")
    store.copy("raw", "version/feed.zip", "quarantine", "version/feed.zip")
    assert (store.root / "quarantine/version/feed.zip").read_bytes() == b"original"
    store.put_json("curated", "report.json", {"status": "passed"})
    assert json.loads((store.root / "curated/report.json").read_text()) == {"status": "passed"}
    assert not list(store.root.rglob("tmp*"))


def test_local_archive_rejects_path_escape(tmp_path: Path) -> None:
    store = LocalObjectStore(str(tmp_path))
    for bucket, key in [("..", "outside"), ("raw", "../../outside"), ("", "")]:
        with pytest.raises(ValueError):
            store.exists(bucket, key)
