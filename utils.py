def parse_cookie_string(cookie_str: str) -> list:
    cookies = []
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            cookies.append({
                "name": name,
                "value": value,
                "domain": ".webook.com",
                "path": "/"
            })
    return cookies
