import json
from pathlib import Path
from app.config import settings

if settings().storage_backend != "sql":
    raise RuntimeError("CONTRACT_EXPORT_REQUIRES_COMPLETE_SQL_BASELINE_DURING_MIGRATION")

from app.main import app
from app.schemas import Config

root = Path(__file__).resolve().parents[1] / "contracts"
root.mkdir(exist_ok=True)
(root / "openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n")
(root / "config.schema.json").write_text(
    json.dumps(Config.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
)
print("Exported OpenAPI and configuration schema")
