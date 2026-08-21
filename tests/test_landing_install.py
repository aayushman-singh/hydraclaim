from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_landing_page_has_cli_mcp_install_and_agent_prompt():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="install"' in html
    assert "pip install hydraclaim" in html
    assert "pip install 'hydraclaim[mcp]'" in html
    assert "hydraclaim mcp" in html
    assert 'id="copy-agent-prompt"' in html
    assert 'id="agent-install-prompt"' in html
    assert "navigator.clipboard.writeText" in script
