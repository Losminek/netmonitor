"""Regrese L30: doplnění release musí stavět z commitu tagu vydané verze."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent


def test_build_checks_out_release_tag():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    build = re.search(r"^  build:\n.*?(?=^  [\w-]+:|\Z)", workflow, re.M | re.S)
    assert build is not None, "Chybí job build"

    checkout = re.search(
        r"^      - uses: actions/checkout@v5\n(?P<options>(?:        .*\n)*)",
        build.group(),
        re.M,
    )
    assert checkout is not None, "V jobu build chybí checkout"
    assert "        with:\n          ref: refs/tags/${{ needs.prepare.outputs.tag }}\n" in checkout.group("options"), (
        "Checkout v jobu build musí použít tag z jobu prepare"
    )
