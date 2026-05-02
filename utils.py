def parse_cookie_string(cookie_str: str) -> list[dict]:
    """
    Parse a semicolon-separated cookie string into a list of Playwright cookie dicts.

    Example input:
        "session=abc123; token=xyz; user_id=42"
    """
    cookies = []
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": ".webook.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }
        )
    return cookies


def build_headers(extra: dict | None = None) -> dict:
    """
    Build a realistic browser header set for webook.com requests.
    Optionally merge in extra headers.
    """
    base = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
    }
    if extra:
        base.update(extra)
    return base
