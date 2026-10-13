"""Small AST-based import boundary check for the documented DDD direction."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "pin"
SPEECH_DEPENDENCIES = {"faster_whisper", "ctranslate2"}
FRAMEWORK_DEPENDENCIES = {
    "aiogram",
    "aiohttp",
    "alembic",
    "httpx",
    "pydantic",
    "psycopg",
    "sqlalchemy",
}


def _module_name(path: Path, source: Path) -> str:
    parts = path.relative_to(source).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path: Path, source: Path) -> list[tuple[int, str]]:
    module = _module_name(path, source)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                package_parts = package.split(".") if package else []
                package_parts = package_parts[: len(package_parts) - node.level + 1]
                base = ".".join(part for part in (*package_parts, node.module or "") if part)
            if node.names == [ast.alias(name="*", asname=None)]:
                found.append((node.lineno, base))
            else:
                found.extend(
                    (node.lineno, ".".join(part for part in (base, alias.name) if part))
                    for alias in node.names
                )
    return found


def _uses_environment(tree: ast.Module) -> bool:
    os_names = {"os"}
    environment_names = {"environ"}
    getenv_names = {"getenv"}
    for top_node in ast.walk(tree):
        if isinstance(top_node, ast.Import):
            for alias in top_node.names:
                if alias.name == "os":
                    os_names.add(alias.asname or "os")
        elif isinstance(top_node, ast.ImportFrom) and top_node.module == "os":
            for alias in top_node.names:
                if alias.name == "environ":
                    environment_names.add(alias.asname or alias.name)
                elif alias.name == "getenv":
                    getenv_names.add(alias.asname or alias.name)
    for ast_node in ast.walk(tree):
        if isinstance(ast_node, ast.Attribute) and isinstance(ast_node.value, ast.Name):
            if (ast_node.value.id in os_names and ast_node.attr in {"environ", "getenv"}) or (
                ast_node.value.id in environment_names and ast_node.attr == "get"
            ):
                return True
        if isinstance(ast_node, ast.Subscript) and isinstance(ast_node.value, ast.Name):
            if ast_node.value.id in environment_names:
                return True
        if isinstance(ast_node, ast.Call) and isinstance(ast_node.func, ast.Name):
            if ast_node.func.id in environment_names | getenv_names:
                return True
    return False


def violations(source: Path = SOURCE) -> list[str]:
    issues: list[str] = []
    contexts = source / "contexts"
    context_roots = (
        {path.name for path in contexts.iterdir() if path.is_dir()} if contexts.exists() else set()
    )
    for path in source.rglob("*.py"):
        module = _module_name(path, source)
        context = module.removeprefix("contexts.").split(".", 1)[0]
        is_domain = module.startswith("contexts.") and ".domain" in module
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if module != "bootstrap.config" and _uses_environment(tree):
            issues.append(f"{path}: environment access must stay in bootstrap.config")
        for line, imported in _imports(path, source):
            target = imported.removeprefix("pin.")
            top_level = imported.split(".", 1)[0]
            if top_level in SPEECH_DEPENDENCIES and path != SOURCE / "bootstrap" / "speech.py":
                issues.append(f"{path}:{line}: speech dependency must stay in bootstrap.speech")
            if is_domain and (
                top_level in FRAMEWORK_DEPENDENCIES | SPEECH_DEPENDENCIES
                or target == "bootstrap"
                or target.startswith(("adapters.", "bootstrap.", "platform.", "entrypoints."))
                or target == "contexts"
                or target.startswith(f"contexts.{context}.")
                and not target.startswith(f"contexts.{context}.domain.")
                or target.startswith("contexts.")
                and not target.startswith(f"contexts.{context}.")
            ):
                issues.append(f"{path}:{line}: domain imports forbidden dependency {imported}")
            if module.startswith("contexts.") and target.startswith("adapters."):
                issues.append(f"{path}:{line}: context imports outer adapter {imported}")
            if module.startswith("contexts.") and target.startswith(("bootstrap.", "entrypoints.")):
                issues.append(f"{path}:{line}: context imports composition root {imported}")
            module_parts = module.split(".")
            if (
                len(module_parts) > 2
                and module_parts[:1] == ["contexts"]
                and module_parts[2] == "application"
            ):
                own_context = f"contexts.{context}."
                target_layer = target.removeprefix(own_context).split(".", 1)[0]
                if target.startswith(own_context) and target_layer in {
                    "infrastructure",
                    "presentation",
                }:
                    issues.append(
                        f"{path}:{line}: application imports outward layer {imported}"
                    )
            if module.startswith("contexts.") and target.startswith("contexts."):
                other_context = target.removeprefix("contexts.").split(".", 1)[0]
                if other_context in context_roots and other_context != context:
                    published_api = f"contexts.{other_context}.api"
                    if target != published_api and not target.startswith(published_api + "."):
                        issues.append(
                            f"{path}:{line}: cross-context import must use the published .api: "
                            f"{imported}"
                        )
            if module.startswith("adapters.") and target.startswith("entrypoints."):
                issues.append(f"{path}:{line}: adapter imports entrypoint {imported}")
    return issues


def main() -> int:
    issues = violations()
    if issues:
        print("Architecture violations:\n" + "\n".join(issues))
        return 1
    print("Architecture import boundaries: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

