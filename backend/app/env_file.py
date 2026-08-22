"""app/env_file.py -- surgical read/update of backend/.env.

Used by app/settings_routes.py so the Settings panel can persist
credentials across restarts without clobbering the hand-written comments
in .env: only the matched KEY=VALUE line is replaced (or appended if the
key isn't present yet); every other line is left byte-for-byte alone.
"""
import re

from app.config import BASE_DIR

ENV_PATH = BASE_DIR / ".env"


def update_env_file(updates: dict[str, str]) -> None:
    if not updates:
        return

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True) if ENV_PATH.exists() else []
    remaining = dict(updates)

    for i, line in enumerate(lines):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            newline = "\n" if line.endswith("\n") else ""
            lines[i] = f"{key}={remaining.pop(key)}{newline}"

    if remaining:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        for key, value in remaining.items():
            lines.append(f"{key}={value}\n")

    ENV_PATH.write_text("".join(lines), encoding="utf-8")
