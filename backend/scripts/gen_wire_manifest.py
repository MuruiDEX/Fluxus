"""Regenerate docs/contracts/wire-manifest.json from live Pydantic schemas.

Source of truth: `app.api.v1.schemas` response models + the route table
from `app.openapi()`. Pure code inspection — no DB, no network, no server.

Run after any wire change:
    .\\.venv\\Scripts\\python.exe scripts/gen_wire_manifest.py

`tests/test_wire_manifest.py` fails if the checked-in file is stale.
Deterministic output: sorted keys, indent 2, trailing newline.
"""

import inspect
import json
import sys
import typing
from decimal import Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from pydantic import BaseModel  # noqa: E402

import app.api.v1.schemas as schemas  # noqa: E402
from app.main import create_app  # noqa: E402

MANIFEST_PATH = BACKEND_DIR.parent / "docs" / "contracts" / "wire-manifest.json"


def _type_name(annotation: object) -> tuple[str, bool]:
    """Map a Pydantic field annotation to (wire type, nullable)."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        nullable = len(args) != len(typing.get_args(annotation))
        if len(args) == 1:
            inner, _ = _type_name(args[0])
            return inner, True
        return "union", nullable
    if origin is list or annotation is list:
        return "array", False
    if origin is dict or annotation is dict:
        return "object", False
    if annotation is str:
        return "string", False
    if annotation is int:
        return "integer", False
    if annotation is float:
        return "number", False
    if annotation is bool:
        return "boolean", False
    if annotation is Decimal:
        # Pydantic JSON mode emits Decimal as a JSON string ("0.00") here.
        return "decimal-as-string", False
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__, False
    name = getattr(annotation, "__name__", None) or str(annotation)
    return name, False


def _schema_entry(model: type[BaseModel]) -> dict:
    properties: dict[str, dict] = {}
    required: list[str] = []
    for name, field in model.model_fields.items():
        wire_name = field.alias or name
        wire_type, nullable = _type_name(field.annotation)
        spec: dict[str, object] = {"type": wire_type}
        if nullable:
            spec["nullable"] = True
        if field.is_required():
            required.append(wire_name)
        else:
            spec["default"] = field.get_default(call_default_factory=True)
        properties[wire_name] = spec
    return {"properties": properties, "required": sorted(required)}


def build_manifest() -> dict:
    models = {
        name: obj
        for name, obj in vars(schemas).items()
        if inspect.isclass(obj)
        and issubclass(obj, BaseModel)
        and obj is not BaseModel
        and obj.__module__ == schemas.__name__
        and name not in ("ExecutionCreate", "Contracted")  # request body / base, not wire
    }
    app = create_app()
    endpoints: list[dict[str, str]] = []
    for path, methods in sorted(app.openapi()["paths"].items()):
        if not path.startswith("/api/v1"):
            continue
        for method, spec in sorted(methods.items()):
            responses = spec.get("responses", {})
            ok = responses.get("200", {})
            content = ok.get("content", {}).get("application/json", {})
            ref = content.get("schema", {}).get("$ref", "")
            response = ref.rsplit("/", 1)[-1] if ref else ""
            endpoints.append({"method": method.upper(), "path": path, "response": response})
    return {
        "contract": "v1",
        "generated_by": "backend/scripts/gen_wire_manifest.py (do not hand-edit)",
        "endpoints": endpoints,
        "schemas": {name: _schema_entry(model) for name, model in sorted(models.items())},
    }


def main() -> None:
    text = json.dumps(build_manifest(), indent=2, sort_keys=True, default=str) + "\n"
    MANIFEST_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
