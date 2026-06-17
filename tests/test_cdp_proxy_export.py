from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from im_archive_cli.cdp_proxy_export import CdpProxyClient


def test_new_tab_percent_encodes_nested_query(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"targetId": "target-1"}).encode("utf-8")

    def fake_urlopen(url, timeout=30):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr("im_archive_cli.cdp_proxy_export.urllib.request.urlopen", fake_urlopen)

    target_id = CdpProxyClient("http://localhost:3456").new_tab(
        "https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=200001150281214"
    )

    assert target_id == "target-1"
    query = parse_qs(urlparse(captured["url"]).query)
    assert query["url"] == [
        "https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=200001150281214"
    ]
