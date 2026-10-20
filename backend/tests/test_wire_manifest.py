"""Wire manifest freshness: the checked-in contract mirror matches live code.

`docs/contracts/wire-manifest.json` is generated from the actual Pydantic
response models + route table (no DB, no network). If this test fails, the
wire changed without regenerating:
    .\\.venv\\Scripts\\python.exe scripts/gen_wire_manifest.py
"""

import json
import sys
from pathlib import Path


def test_wire_manifest_is_fresh() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

    from gen_wire_manifest import MANIFEST_PATH, build_manifest

    expected = json.dumps(build_manifest(), indent=2, sort_keys=True, default=str) + "\n"
    actual = MANIFEST_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "wire-manifest.json is stale vs app/api/v1/schemas.py + routes; "
        "regenerate with scripts/gen_wire_manifest.py"
    )


def test_manifest_covers_all_v1_routes() -> None:
    manifest = json.loads(
        (
            Path(__file__).resolve().parent.parent.parent
            / "docs"
            / "contracts"
            / "wire-manifest.json"
        ).read_text(encoding="utf-8")
    )
    paths = {(e["method"], e["path"]) for e in manifest["endpoints"]}
    for expected in [
        ("GET", "/api/v1/businesses/{business_id}"),
        ("GET", "/api/v1/businesses/{business_id}/projects"),
        ("GET", "/api/v1/businesses/{business_id}/projects/{project_id}"),
        ("GET", "/api/v1/businesses/{business_id}/projects/{project_id}/tasks"),
        ("GET", "/api/v1/businesses/{business_id}/projects/{project_id}/tasks/{task_id}"),
        ("GET", "/api/v1/businesses/{business_id}/agents"),
        ("GET", "/api/v1/businesses/{business_id}/agents/{agent_id}"),
        ("GET", "/api/v1/businesses/{business_id}/approvals"),
        ("GET", "/api/v1/businesses/{business_id}/approvals/{approval_id}"),
        ("GET", "/api/v1/businesses/{business_id}/activity"),
        ("GET", "/api/v1/businesses/{business_id}/brain"),
    ]:
        assert expected in paths, f"missing from manifest: {expected}"
