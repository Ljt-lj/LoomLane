from __future__ import annotations

import os
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PACKAGE_DIR.parents[1]
REPO_DIR = BACKEND_DIR.parent


def data_dir() -> Path:
    configured = os.getenv("LOOMLANE_DATA_DIR")
    return Path(configured).expanduser().resolve() if configured else REPO_DIR / "workspace"


def projects_dir() -> Path:
    return data_dir() / "projects"


def generated_templates_dir() -> Path:
    return BACKEND_DIR / "templates" / "generated"

