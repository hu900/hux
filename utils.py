def parse_cookie_string(cookie_str):
    cookies = []
    for item in cookie_str.split(";"):
        name, value = item.strip().split("=", 1)
        cookies.append({
            "name": name,
            "value": value,
            "domain": ".webook.com",
            "path": "/"
        })
    return cookies
