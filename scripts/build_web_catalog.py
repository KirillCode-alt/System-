import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT = ROOT / "web" / "catalog.json"


def merge_catalog() -> dict:
    merged = {"drives": {}}
    for path in sorted(DATA_DIR.glob("*.json")):
        try:
            chunk = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Не удалось прочитать {path}: {exc}") from exc

        for drive_key, drive_payload in chunk.get("drives", {}).items():
            target = merged["drives"].setdefault(
                drive_key,
                {"name": drive_payload.get("name", drive_key), "categories": {}},
            )
            if drive_payload.get("name"):
                target["name"] = drive_payload["name"]

            for category_key, category_payload in drive_payload.get("categories", {}).items():
                target_category = target["categories"].setdefault(
                    category_key,
                    {"name": category_payload.get("name", category_key), "items": {}},
                )
                for key in ("name", "prefix", "example_full", "example_digits"):
                    if category_payload.get(key):
                        target_category[key] = category_payload[key]
                target_category.setdefault("items", {}).update(category_payload.get("items", {}))
    return merged


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(merge_catalog(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Собран каталог: {OUTPUT}")
