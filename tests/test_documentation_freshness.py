"""Keep the current V4 README aligned with the accepted SSC-retirement boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_and_current_runtime_contract_are_not_stale_ssc_authority_docs():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    contract = (ROOT / "docs" / "CURRENT-RUNTIME-CONTRACT-2026-08-13.md").read_text(
        encoding="utf-8"
    )
    retirement = (ROOT / "docs" / "SSC_RETIREMENT_2026-08-12.md").read_text(
        encoding="utf-8"
    )

    assert "do not load SSC" in readme
    assert "fossil.search" in readme
    assert "does not directly call embedding" in readme
    assert "closeout.md" in contract
    assert "does not export OpenTelemetry or Langfuse" in contract
    assert "retir" in retirement.lower()

    stale_authority_phrases = (
        "SSC corpus remains the source of knowledge and closeout authority",
        "SSC remains the working RAG corpus",
        "treating the old SSC corpus as a dependency",
    )
    assert not any(phrase in readme for phrase in stale_authority_phrases)
