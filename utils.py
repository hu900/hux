import json
import logging

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  Cookies that actually affect booking on webook.com
# ─────────────────────────────────────────────

# Must-have: without these the session fails
CRITICAL_COOKIES = {"token", "refresh_token", "token_expires_in"}

# Important: affect booking flow
BOOKING_COOKIES  = {"holdToken", "prism_254961849", "location", "lang", "currency"}

# Skip entirely: pure analytics, no server effect
SKIP_COOKIES = {
    "_ga", "_fbp", "_ttp", "_clsk", "_clck", "_scid", "_scid_r",
    "_twpid", "_hjSession_3800203", "_hjSessionUser_3800203",
    "__gads", "__gpi", "__eoi", "_gcl_au", "_dd_s",
    "AMP_c5b32821c1", "AMP_MKTG_c5b32821c1",
    "ttcsid", "ttcsid_CLM7RFJC77UEPNH41H2G", "ttcsid_D4OORBRC77U7MI8IRF00",
}

# These cookies are path-specific — must keep their real path
PATH_SPECIFIC_COOKIES = {"holdToken", "__cf_bm", "_cfuvid"}


def parse_cookie_input(raw: str) -> list[dict]:
    """
    Smart parser — accepts two formats:

    1. JSON array from Cookie-Editor Export:
       [{"name": "token", "value": "abc", "domain": "...", "path": "...", ...}]

    2. Classic semicolon string:
       token=abc; refresh_token=xyz

    Returns Playwright-compatible cookie dicts, preserving path for
    path-specific cookies like holdToken.
    """
    raw = raw.strip()

    if raw.startswith("["):
        return _parse_json_cookies(raw)

    return parse_cookie_string(raw)


def _parse_json_cookies(raw: str) -> list[dict]:
    """Parse Cookie-Editor JSON export, preserving real metadata."""
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON غير صالح: {e}") from e

    cookies = []
    skipped = []
    missing_critical = CRITICAL_COOKIES.copy()

    for item in items:
        name  = (item.get("name") or "").strip()
        value = (item.get("value") or "").strip()

        if not name:
            continue

        # Track which critical cookies we found
        missing_critical.discard(name)

        # Skip pure analytics cookies
        if name in SKIP_COOKIES:
            skipped.append(name)
            continue

        # Determine domain — prefer the one from the export
        raw_domain = item.get("domain", ".webook.com")
        if not raw_domain.startswith(".") and not item.get("hostOnly"):
            raw_domain = "." + raw_domain

        # Determine path — preserve real path for path-specific cookies
        if name in PATH_SPECIFIC_COOKIES:
            path = item.get("path", "/")
        else:
            path = "/"

        # sameSite — Playwright accepts: "Strict", "Lax", "None"
        same_site_raw = (item.get("sameSite") or "").lower()
        same_site_map = {
            "strict": "Strict",
            "lax": "Lax",
            "no_restriction": "None",
            "none": "None",
        }
        same_site = same_site_map.get(same_site_raw, "Lax")

        cookie = {
            "name":     name,
            "value":    value,
            "domain":   raw_domain,
            "path":     path,
            "secure":   bool(item.get("secure", False)),
            "httpOnly": bool(item.get("httpOnly", False)),
            "sameSite": same_site,
        }

        # Add expiry if present (Playwright uses "expires" as Unix timestamp)
        if item.get("expirationDate"):
            cookie["expires"] = int(item["expirationDate"])

        cookies.append(cookie)

    if skipped:
        logger.debug(f"Skipped analytics cookies: {skipped}")

    if missing_critical:
        logger.warning(f"Missing critical cookies: {missing_critical}")

    logger.info(
        f"Parsed {len(cookies)} cookies from JSON "
        f"(skipped {len(skipped)} analytics, "
        f"missing critical: {missing_critical or 'none'})"
    )
    return cookies


def parse_cookie_string(cookie_str: str) -> list[dict]:
    """Parse a semicolon-separated cookie string (manual paste)."""
    cookies = []
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        name  = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append(_make_cookie(name, value))
    logger.info(f"Parsed {len(cookies)} cookies from string")
    return cookies


def get_token(cookies: list[dict]) -> str:
    """Extract the JWT token from a parsed cookie list."""
    for c in cookies:
        if c["name"] == "token":
            return c["value"]
    return ""


def get_hold_token(cookies: list[dict]) -> str:
    """Extract the holdToken from a parsed cookie list."""
    for c in cookies:
        if c["name"] == "holdToken":
            return c["value"]
    return ""


def validate_cookies(cookies: list[dict]) -> tuple[bool, str]:
    """
    Check if the essential cookies are present.
    Returns (is_valid, message).
    """
    names = {c["name"] for c in cookies}

    if "token" not in names:
        return False, "❌ كوكي `token` مفقود — سجّل الدخول أولاً ثم أعد التصدير"

    if "refresh_token" not in names:
        return False, "⚠️ كوكي `refresh_token` مفقود — قد تنتهي الجلسة بسرعة"

    if "holdToken" not in names:
        return True, "⚠️ `holdToken` غير موجود — البوت سيحاول إنشاؤه تلقائياً عبر اختيار مقعد"

    return True, "✅ جميع الكوكيز الضرورية موجودة"


def _make_cookie(name: str, value: str) -> dict:
    """Build a minimal Playwright-compatible cookie (for string input)."""
    return {
        "name":     name,
        "value":    value,
        "domain":   ".webook.com",
        "path":     "/",
        "secure":   False,
        "sameSite": "Lax",
    }


def build_headers(extra: dict | None = None) -> dict:
    """Realistic Chrome headers for webook.com."""
    base = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://webook.com/",
    }
    if extra:
        base.update(extra)
    return base
