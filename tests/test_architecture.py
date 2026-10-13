from __future__ import annotations

from pathlib import Path

from pin.architecture import violations


def test_current_source_obeys_import_boundaries() -> None:
    assert violations() == []


def test_forbidden_domain_and_cross_context_imports_fail(tmp_path: Path) -> None:
    source = tmp_path / "src" / "pin"
    (source / "contexts" / "identity" / "domain").mkdir(parents=True, exist_ok=True)
    (source / "contexts" / "identity" / "domain" / "model.py").write_text(
        "import faster_whisper\n"
        "from pin.bootstrap.config import Settings\n"
        "from pin.contexts.identity.infrastructure.repo import Repository\n"
        "from pin import bootstrap\n"
        "from .. import infrastructure\n"
        "from pin.contexts.planning.api import Plan\n",
        encoding="utf-8",
    )
    (source / "contexts" / "planning" / "api").mkdir(parents=True, exist_ok=True)
    (source / "contexts" / "planning" / "api" / "__init__.py").write_text("", encoding="utf-8")
    (source / "contexts" / "planning" / "infrastructure").mkdir(parents=True, exist_ok=True)
    (source / "contexts" / "planning" / "infrastructure" / "repo.py").write_text(
        "", encoding="utf-8"
    )
    (source / "contexts" / "identity" / "application").mkdir(parents=True, exist_ok=True)
    (source / "contexts" / "identity" / "application" / "__init__.py").write_text(
        "from .. import infrastructure\n",
        encoding="utf-8",
    )
    (source / "contexts" / "identity" / "application" / "handler.py").write_text(
        "import os\n"
        "os.environ.get('PIN_DATABASE_URL')\n"
        "from pin.contexts import planning\n"
        "import pin.contexts.identity.infrastructure\n"
        "from .. import infrastructure\n"
        "from pin.contexts.identity.infrastructure.repo import OwnRepository\n"
        "from pin.contexts.planning.infrastructure.repo import Repository\n"
        "from pin.contexts.planning.infrastructure.api import Repository as BadApi\n"
        "from pin.contexts.planning.infrastructure.api.models import Model as BadModel\n",
        encoding="utf-8",
    )

    found = violations(source)
    assert any("domain imports forbidden dependency faster_whisper" in issue for issue in found)
    assert any("bootstrap.config" in issue for issue in found)
    assert any("environment access" in issue for issue in found)
    assert any("published .api" in issue for issue in found)
    assert any("infrastructure.api" in issue for issue in found)
    assert any("infrastructure.api.models" in issue for issue in found)
    assert sum("domain imports forbidden dependency" in issue for issue in found) >= 3
    assert any(
        "domain imports forbidden dependency pin.contexts.identity.infrastructure" in issue
        for issue in found
    )
    assert any("application imports outward layer" in issue for issue in found)
    assert any("contexts.identity.infrastructure" in issue for issue in found)

