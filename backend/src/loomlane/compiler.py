from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .models import GenerateResponse, SDL
from .settings import generated_templates_dir
from .storage import InvalidWorkflow
from .validator import validate_sdl


COMPILER_VERSION = "0.2.7"


def canonical_json(sdl: SDL) -> str:
    return json.dumps(
        sdl.canonical_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compiler_key(sdl: SDL) -> str:
    digest = hashlib.sha256(
        f"{COMPILER_VERSION}\n{canonical_json(sdl)}".encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


class WorkflowCompiler:
    def __init__(self, templates_dir: Path | None = None) -> None:
        self.templates_dir = templates_dir or generated_templates_dir()
        self.environment = Environment(
            loader=FileSystemLoader(self.templates_dir),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )

    def compile(self, sdl: SDL, project_dir: Path) -> GenerateResponse:
        validation = validate_sdl(sdl)
        if not validation.valid:
            raise InvalidWorkflow(
                [issue.model_dump(mode="json") for issue in validation.issues]
            )
        normalized = SDL.model_validate(validation.normalized_sdl)
        key = compiler_key(normalized)
        output_dir = (project_dir / "generated" / key).resolve()
        generated_root = (project_dir / "generated").resolve()
        if output_dir.parent != generated_root:
            raise ValueError("Invalid generated output path")

        manifest_path = output_dir / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            return GenerateResponse(
                compiler_key=key,
                generated_path=str(output_dir),
                cached=True,
                files=manifest.get("files", []),
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        context: dict[str, Any] = {
            "sdl_json_literal": json.dumps(
                json.dumps(
                    normalized.canonical_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
            "compiler_version": COMPILER_VERSION,
        }
        files: list[str] = []

        templates = {
            "workflow.py.j2": "workflow.py",
            "config.py.j2": "config.py",
            "runners/graph_runner.py.j2": "runners/graph_runner.py",
            "runners/__init__.py.j2": "runners/__init__.py",
            "agents/__init__.py.j2": "agents/__init__.py",
            "requirements.txt.j2": "requirements.txt",
        }
        for template_name, relative_path in templates.items():
            content = self.environment.get_template(template_name).render(**context)
            _write(output_dir / relative_path, content)
            files.append(relative_path)

        for agent in normalized.agents:
            agent_literal = json.dumps(
                agent.model_dump(mode="json", by_alias=True, exclude_none=True),
                ensure_ascii=False,
                indent=2,
            )
            relative_path = f"agents/{agent.id}.py"
            _write(
                output_dir / relative_path,
                "from __future__ import annotations\n\n"
                f"AGENT = {agent_literal}\n",
            )
            files.append(relative_path)

        manifest = {
            "compiler_version": COMPILER_VERSION,
            "compiler_key": key,
            "sdl_sha256": hashlib.sha256(
                canonical_json(normalized).encode("utf-8")
            ).hexdigest(),
            "files": sorted(files),
        }
        _write(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        files.append("manifest.json")

        return GenerateResponse(
            compiler_key=key,
            generated_path=str(output_dir),
            cached=False,
            files=sorted(files),
        )

