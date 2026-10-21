import hashlib
import json
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from cv_skills.build import build

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def tailored(tmp_path_factory) -> Path:
    """The example master + example selection, built into a tailored RenderCV YAML (one page)."""
    out = tmp_path_factory.mktemp("tailored") / "tailored.yaml"
    result = build(REPO / "examples/master.yaml", REPO / "examples/selection.json", out)
    assert result["ok"]
    return out


@pytest.fixture
def make_cv(tailored, tmp_path):
    """`make_cv(extra_bullets)`: the tailored CV with that many extra highlights on the first entry."""

    def make(extra_bullets: int, name: str = "cv.yaml") -> Path:
        yaml = YAML()
        data = yaml.load(tailored.read_text(encoding="utf-8"))
        entry = next(e for e in data["cv"]["sections"]["experience"] if "highlights" in e)
        for i in range(extra_bullets):
            entry["highlights"].append(f"Filler bullet number {i} about pipelines and quality")
        path = tmp_path / name
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f)
        return path

    return make


DEFAULT_TITLE = "Senior Data Engineer"
VARIANTS = "Senior Analytics Engineer; Senior ML Engineer"


@pytest.fixture
def title_app(tmp_path):
    """`title_app(position=None)`: (master, selection) in tmp_path. The master is the example with
    `title_variants` on experience.0; the selection is the example's, re-hashed, with experience.0's
    `"position"` set when given. `examples/master.yaml` itself is never touched."""

    def make(position: str | None = None, variants: str = VARIANTS, replace: tuple = ()) -> tuple[Path, Path]:
        text = (REPO / "examples/master.yaml").read_text(encoding="utf-8")
        old = f"        position: {DEFAULT_TITLE}\n"
        assert text.count(old) == 1
        text = text.replace(old, old + f"        title_variants: {variants}\n")
        for a, b in replace:
            assert a in text
            text = text.replace(a, b)
        master = tmp_path / "master.yaml"
        master.write_text(text, encoding="utf-8")
        sel = json.loads((REPO / "examples/selection.json").read_text(encoding="utf-8"))
        sel["master_hash"] = "sha256:" + hashlib.sha256(master.read_bytes()).hexdigest()
        if position is not None:
            sel["sections"][1]["entries"][0]["position"] = position
        selection = tmp_path / "selection.json"
        selection.write_text(json.dumps(sel), encoding="utf-8")
        return master, selection

    return make
