from __future__ import annotations

from pathlib import Path


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _strip_wrapping_quotes(value.strip())
    return values


def update_dotenv(path: Path, updates: dict[str, str | None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    rewritten: list[str] = []
    handled: set[str] = set()

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rewritten.append(line)
            continue

        key, _ = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key not in updates:
            rewritten.append(line)
            continue

        handled.add(normalized_key)
        value = updates[normalized_key]
        if value is None:
            continue
        rewritten.append(f"{normalized_key}={value}")

    for key, value in updates.items():
        if key in handled or value is None:
            continue
        rewritten.append(f"{key}={value}")

    final_text = "\n".join(rewritten).rstrip()
    path.write_text(final_text + ("\n" if final_text else ""), encoding="utf-8")


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
