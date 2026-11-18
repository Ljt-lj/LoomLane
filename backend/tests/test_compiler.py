from __future__ import annotations

import compileall
import hashlib
from pathlib import Path

from loomlane.compiler import WorkflowCompiler, compiler_key
from loomlane.storage import default_sdl


def _generated_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_generated_project_is_deterministic_and_standalone(tmp_path: Path) -> None:
    compiler = WorkflowCompiler()
    sdl = default_sdl()
    first = compiler.compile(sdl, tmp_path / "first")
    second = compiler.compile(sdl, tmp_path / "second")

    assert first.compiler_key == second.compiler_key == compiler_key(sdl)
    assert _generated_digest(Path(first.generated_path)) == _generated_digest(
        Path(second.generated_path)
    )
    assert compileall.compile_dir(first.generated_path, quiet=1)

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path(first.generated_path).rglob("*.py")
    )
    assert "import loomlane" not in source
    assert "sk-" not in source.lower()


def test_compile_cache_reuses_manifest(tmp_path: Path) -> None:
    compiler = WorkflowCompiler()
    sdl = default_sdl()
    first = compiler.compile(sdl, tmp_path)
    second = compiler.compile(sdl, tmp_path)

    assert not first.cached
    assert second.cached
    assert first.compiler_key == second.compiler_key
