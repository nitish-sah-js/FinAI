"""python -m copilot_common.export_schema > schema.json  (for TypeScript generation, 01 §9)."""
import json

from pydantic import BaseModel

from . import models


def bundle() -> dict:
    out = {}
    for name in dir(models):
        obj = getattr(models, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            out[name] = obj.model_json_schema()
    return out


if __name__ == "__main__":
    print(json.dumps(bundle(), indent=2))
