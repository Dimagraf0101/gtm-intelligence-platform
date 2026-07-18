"""Product-shell tests (Branding, Theme Control & Favicon sprint).

Covers the persistent application shell: shared page configuration, brand assets, the favicon, and the
theme-control configuration. These are deterministic file/config assertions — no pixel comparisons and
no browser automation. Offline, no pytest:

    ./.venv/bin/python tests/test_product_shell.py
"""
import sys
import struct
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

APP = ROOT / "app.py"
CONFIG = ROOT / ".streamlit" / "config.toml"
ASSETS = ROOT / "assets"
PAGES = sorted((ROOT / "pages").glob("*.py"))


def _config() -> dict:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


# --- shared page configuration ----------------------------------------------

def test_router_owns_page_configuration():
    """The shell configures the page once; branding is not a per-page concern."""
    src = APP.read_text(encoding="utf-8")
    assert "st.set_page_config(" in src
    assert "page_icon=" in src and "layout=" in src


def test_no_page_configures_branding_itself():
    """Any page calling set_page_config would override the shared favicon/layout for that page."""
    offenders = [p.name for p in PAGES + [ROOT / "legacy_qualification.py"]
                 if "st.set_page_config(" in p.read_text(encoding="utf-8")]
    assert offenders == [], f"pages must not configure branding: {offenders}"


def test_logo_registered_once_in_the_shell():
    src = APP.read_text(encoding="utf-8")
    assert src.count("st.logo(") == 1, "the wordmark must be registered exactly once, in the shell"
    assert "assets/wordmark.svg" in src
    pages_with_logo = [p.name for p in PAGES if "st.logo(" in p.read_text(encoding="utf-8")]
    assert pages_with_logo == [], f"pages must not register a logo: {pages_with_logo}"


# --- brand assets ------------------------------------------------------------

def test_brand_assets_exist():
    for name in ("wordmark.svg", "mark.svg", "favicon.png"):
        assert (ASSETS / name).is_file(), f"missing brand asset: {name}"


def test_wordmark_and_mark_are_valid_svg():
    for name in ("wordmark.svg", "mark.svg"):
        svg = (ASSETS / name).read_text(encoding="utf-8")
        assert svg.lstrip().startswith("<svg") and svg.rstrip().endswith("</svg>")
        assert "viewBox=" in svg, f"{name} needs a viewBox to scale correctly"


def test_wordmark_glyph_is_large_enough_to_read():
    """st.logo scales to a fixed height, so legibility is the glyph height as a fraction of the
    viewBox height. Guards against a future edit silently shrinking the brand again."""
    svg = (ASSETS / "wordmark.svg").read_text(encoding="utf-8")
    import re
    vb = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    assert vb, "wordmark needs a 0-origin viewBox"
    height = float(vb.group(2))
    sizes = [float(m) for m in re.findall(r'font-size="([\d.]+)"', svg)]
    assert sizes, "wordmark has no text"
    assert max(sizes) / height >= 0.40, (
        f"product name is only {max(sizes) / height:.0%} of the lockup height — too small to read")


# --- favicon -----------------------------------------------------------------

def _png_size(path: Path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "favicon is not a valid PNG"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def test_favicon_is_a_valid_square_png():
    w, h = _png_size(ASSETS / "favicon.png")
    assert w == h, f"favicon must be square, got {w}x{h}"
    assert w >= 256, f"favicon should be at least 256px for high-density displays, got {w}"


def test_favicon_survives_dark_and_light_browser_chrome():
    """The mark must not be a dark-only silhouette: it needs an opaque, non-black field so it stays
    visible against both dark and light browser tabs."""
    try:
        from PIL import Image
    except ImportError:                                   # pragma: no cover - Pillow ships with Streamlit
        return
    img = Image.open(ASSETS / "favicon.png").convert("RGBA")
    px = list(img.resize((32, 32)).getdata())
    opaque = [p for p in px if p[3] > 200]
    assert len(opaque) > len(px) * 0.5, "favicon is mostly transparent — it will vanish on some chrome"
    brightness = [(r + g + b) / 3 for r, g, b, _ in opaque]
    assert max(brightness) > 180, "favicon has no light pixels — invisible on dark chrome"
    assert min(brightness) < 200, "favicon has no dark/coloured pixels — invisible on light chrome"


def test_shell_uses_the_favicon_asset():
    assert "assets/favicon.png" in APP.read_text(encoding="utf-8")


# --- theme control -----------------------------------------------------------

def test_theme_control_is_reachable_by_users():
    """toolbarMode must keep the VIEWER menu (which holds the System/Light/Dark control). "minimal"
    hides the whole menu and removes the only user-facing way to choose a theme."""
    mode = _config().get("client", {}).get("toolbarMode")
    assert mode == "viewer", (
        f"toolbarMode is {mode!r}; 'viewer' is required so the theme control stays available while "
        "developer options (Deploy, rerun, clear cache) remain hidden")


def test_config_defines_both_theme_variants():
    cfg = _config().get("theme", {})
    for variant in ("light", "dark"):
        assert variant in cfg, f"[theme.{variant}] is missing"
        for token in ("primaryColor", "backgroundColor", "textColor", "borderColor"):
            assert token in cfg[variant], f"[theme.{variant}] is missing {token}"
        assert "sidebar" in cfg[variant], f"[theme.{variant}.sidebar] is missing"


def test_palette_is_defined_only_in_config():
    """config.toml is the single source of truth; no page may hard-code a colour."""
    import re
    hex_colour = re.compile(r"#[0-9A-Fa-f]{6}\b")
    offenders = []
    for p in PAGES + [APP, ROOT / "legacy_qualification.py"]:
        if hex_colour.search(p.read_text(encoding="utf-8")):
            offenders.append(p.name)
    assert offenders == [], f"colours must live in config.toml, not in: {offenders}"


def test_one_accent_colour_across_both_themes():
    cfg = _config()["theme"]
    accents = {cfg["light"]["primaryColor"].lower(), cfg["dark"]["primaryColor"].lower()}
    assert len(accents) == 2, "expected one accent per theme variant"
    assert cfg["light"]["blueColor"].lower() == cfg["light"]["primaryColor"].lower(), \
        "info colour must reuse the accent so no second blue exists"


# --- navigation compatibility ------------------------------------------------

def test_navigation_urls_unchanged():
    """Shell polish must never move a page. These paths are the app's public URLs."""
    import re
    src = APP.read_text(encoding="utf-8")
    found = set(re.findall(r'url_path="([^"]+)"', src))
    expected = {"Business_Knowledge_Review", "Knowledge_Interview", "Strategy_Review", "Approval",
                "General_ICP", "Market_Hypotheses", "Search_Strategy", "Search_Execution",
                "Lead_Import", "Qualification", "Human_Review", "legacy_qualification"}
    assert found == expected, f"URL set changed: {found ^ expected}"


def test_every_page_is_registered_and_home_is_default():
    import re
    src = APP.read_text(encoding="utf-8")
    # Count the page FILES actually registered, which is immune to the word appearing in comments.
    registered = set(re.findall(r'st\.Page\(\s*"([^"]+\.py)"', src))
    assert len(registered) == 13, f"expected 13 registered pages, found {len(registered)}: {registered}"
    assert "pages/0_Home.py" in registered
    assert "legacy_qualification.py" in registered
    assert "default=True" in src, "Home must remain the default page"


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
