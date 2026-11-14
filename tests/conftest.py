"""tests/test_run.py and tests/test_export.py build a synthetic `demo` place through scripts/ingest/make_fixture.py,
which exports it into the app's public data. Remove it again when the session ends so it never ships."""
import json
import shutil
from pathlib import Path

PUB = Path(__file__).resolve().parents[1] / "src/app/public/data"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(PUB / "demo", ignore_errors=True)
    idx = PUB / "index.json"
    if idx.exists():
        d = json.loads(idx.read_text())
        if "demo" in d.get("areas", []):
            d["areas"].remove("demo")
            idx.write_text(json.dumps(d, indent=2))
