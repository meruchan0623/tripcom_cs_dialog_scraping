from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = [
    ROOT / "README.md",
    ROOT / "docs" / "HERMES_AGENT_RUNBOOK.md",
    ROOT / "skills" / "hermes-ctrip-im-archive" / "SKILL.md",
]

FORBIDDEN_PATTERNS = [
    re.compile(r"run export\s+--kind structured[^\n`]*--via cdp"),
    re.compile(r"structured\s+--via cdp"),
    re.compile(r"结构化和 SingleFile 导出同样复用 web-access CDP proxy"),
    re.compile(r"detail-page\.js"),
]


def test_structured_export_docs_do_not_recommend_cdp() -> None:
    failures: list[str] = []
    for path in DOC_PATHS:
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATTERNS:
            match = pattern.search(text)
            if match:
                failures.append(f"{path.relative_to(ROOT)}: forbidden {pattern.pattern!r} at offset {match.start()}")

    assert failures == []


def test_hermes_skill_contains_current_production_flow() -> None:
    text = (ROOT / "skills" / "hermes-ctrip-im-archive" / "SKILL.md").read_text(encoding="utf-8")

    assert "--via cdp" in text
    assert "python3 -m im_archive_cli.imx_cli run collect" in text
    assert "python3 -m im_archive_cli.imx_cli run export --kind links" in text
    assert "python3 -m im_archive_cli.imx_cli run export --kind structured --formats \"${STRUCTURED_FORMATS:-json,markdown}\"" in text
    assert "structured export 已剥离 CDP/Selenium DOM 抓取路径，只支持 --via http" in text
