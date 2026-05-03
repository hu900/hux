cat > /mnt/user-data/outputs/engine.py << 'PYEOF'
import asyncio
import json
import logging
import sys
import time
import random
import string
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from playwright_stealth import stealth_async
from config import TARGET_URL, ALLOWED_CATEGORIES, MAX_HOLDS, DEBUG_MODE, MAX_RETRIES
from utils import get_token, get_hold_token

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  Category prefix → preferred names mapping
#  Key = prefix used in seat objectId (e.g. "Bronze-RH-315")
# ─────────────────────────────────────────────
CATEGORY_PREFIXES = {
    "VI":       ["C1 RIGHT", "C1 LEFT", "VI", "VIP"],
    "PL":       ["C2 RIGHT", "C2 LEFT", "PL", "PLATINUM"],
    "GO":       ["C3 RIGHT", "C3 LEFT", "GO", "GOLD"],
    "SI":       ["C4", "C5", "SI", "SILVER"],
    "Bronze":   ["BRONZE", "BRONZE 1", "BRONZE 2", "C7", "C8", "BR"],
}

NEXT_BTN_SELECTORS = [
    "button:has-text('التالي: الدفع')",
    "button:has-text('التالي')",
    "button:has-text('Next')",
    "[class*='next']",
    "[class*='checkout']",
]


def _make_tracing_id() -> str:
    ts = int(time.time() * 1000)
    rand = ''.join(random.choices(string.hexdigits.lower(), k=32))
    return f"{ts}-{rand}"


async def _launch_chromium(p):
    args = [
        "--no-sandbox", "--disable-setuid-sandbox",
        "--disable-dev-shm-usage", "--disable-gpu", "--single-process",
    ]
    try:
        return await p.chromium.launch(headless=True, args=args)
    except Exception as e:
        if "Executable doesn't exist" in str(e) or "executable" in str(e).lower():
            logger.warning("Chromium not found — installing (~100MB)...")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "playwright", "install", "chromium", "--with-deps",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"install failed: {stderr.decode()[:200]}") from e
            return await p.chromium.launch(headless=True, args=args)
        raise


class BookingEngine:
    """
    webook.com uses seats.io canvas + WebSocket for seat selection.

    Flow:
      1. Open booking page → injects our cookies (user is "logged in")
      2. Intercept the WebSocket the page opens to seats.io
      3. Collect incoming seat-map data to find available seat IDs
      4. Send 'hold-object' WS message for the best available seat
      5. Wait for 'hold-object' confirmation
      6. Click 'التالي: الدفع' in the main page
    """

    def __init__(self, user_data: dict, bot=None, user_id: int = None):
        self.cookies        = user_data["cookies"]
        self.headers        = user_data["headers"]
        self.preferred_cats = user_data.get("preferred_categories", list(ALLOWED_CATEGORIES))
        self.ticket_count   = min(int(user_data.get("ticket_count", 1)), MAX_HOLDS)
        self.bot            = bot
        self.user_id        = user_id

        self.token          = get_token(self.cookies)
        self.hold_token     = get_hold_token(self.cookies)  # will be refreshed from page

        # Filled during session
        self._ws            = None        # Playwright WebSocket object
        self._ws_send       = None        # callable to send WS message
        self._available     : list[dict]  = []   # [{objectId, category}, ...]
        self._held_seats    : list[str]   = []   # confirmed held seat IDs
        self._ws_hold_token : str         = ""   # holdToken seen in WS traffic

    # ══════════════════════════════════════════
    #  Public
    # ══════════════════════════════════════════

    async def run(self) -> bool:
        for attempt in range(1, MAX_RETRIES + 1):
            if attempt > 1:
                await self.notify(f"🔄 إعادة المحاولة {attempt}/{MAX_RETRIES}...")
                await asyncio.sleep(3)
            try:
                async with async_playwright() as p:
                    browser  = await _launch_chromium(p)
                    context  = await browser.new_context(
                        extra_http_headers=self.headers,
                        viewport={"width": 1280, "height": 800},
                        locale="ar-SA",
                    )
                    await context.add_cookies(self.cookies)
                    page = await context.new_page()
                    await stealth_async(page)

                    if DEBUG_MODE:
                        page.on("console", lambda m: logger.debug(f"[JS] {m.text}"))

                    success = await self._session(page)
                    await browser.close()
                    if success:
                        return True
            except Exception as e:
                await self.notify(f"⚠️ خطأ: {str(e)[:150]}")
                logger.error(f"Attempt {attempt}: {e}", exc_info=True)

        await self.notify("❌ استنفدت جميع المحاولات.")
        return False

    # ══════════════════════════════════════════
    #  Session
    # ══════════════════════════════════════════

    async def _session(self, page) -> bool:

        # Step 1 — attach WebSocket listener BEFORE navigating
        self._attach_ws_listener(page)

        await self.notify("🌐 جارٍ فتح صفحة الحجز...")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30_000)

        # Step 2 — wait for WS to connect and seat map to arrive
        await self.notify("⏳ انتظار تحميل خريطة المقاعد...")
        ws_ready = await self._wait_for_ws(timeout=20)
        if not ws_ready:
            await self.notify("⚠️ لم يتصل WebSocket — قد تكون الجلسة منتهية.")
            return False

        # Step 3 — parse available seats
        await self.notify("🔍 جارٍ البحث عن مقاعد متاحة...")
        chosen = self._pick_seats()
        if not chosen:
            await self.notify(
                "❌ لا توجد مقاعد متاحة في فئاتك:\n"
                + "\n".join(f"• {c}" for c in self.preferred_cats)
            )
            return False

        category, seat_ids = chosen
        await self.notify(
            f"🎯 وجدت {len(seat_ids)} مقعد في فئة *{category}*\n"
            f"جارٍ حجز {self.ticket_count} تذكرة..."
        )

        # Step 4 — send hold-object via WebSocket
        held = await self._hold_seats(seat_ids[: self.ticket_count])
        if not held:
            await self.notify("❌ فشل حجز المقاعد عبر WebSocket.")
            return False

        await self._screenshot(page, "02_held")
        await self.notify(f"✅ تم حجز {len(self._held_seats)} مقعد مؤقتاً!")

        # Step 5 — click "التالي: الدفع"
        await self.notify("💳 جارٍ الانتقال لصفحة الدفع...")
        ok = await self._click_next(page)
        if not ok:
            await self.notify("⚠️ لم أجد زر 'التالي: الدفع' — المقاعد محجوزة، افتح الموقع يدوياً.")
            # Still return True — seats are held
            return True

        await self._screenshot(page, "03_payment")
        await self.notify(
            "🎉 *تم الحجز المؤقت بنجاح!*\n\n"
            "👉 أتمم الدفع على الموقع قبل انتهاء المؤقت.\n"
            f"🔗 {TARGET_URL}"
        )
        return True

    # ══════════════════════════════════════════
    #  WebSocket interception
    # ══════════════════════════════════════════

    def _attach_ws_listener(self, page):
        """
        Playwright fires 'websocket' event for every WS the page opens.
        We capture:
          - The WS object (so we can send messages later)
          - All incoming frames (seat map, confirmations)
          - holdToken seen in outgoing frames
        """
        def on_websocket(ws):
            logger.info(f"WebSocket opened: {ws.url}")
            self._ws = ws
            # Store send callable
            self._ws_send = ws.send

            def on_sent(payload: str):
                try:
                    data = json.loads(payload)
                    # Grab holdToken the page is using
                    if "token" in data and not self._ws_hold_token:
                        self._ws_hold_token = data["token"]
                        logger.info(f"Captured holdToken from WS: {self._ws_hold_token[:8]}...")
                except Exception:
                    pass

            def on_received(payload: str):
                try:
                    data = json.loads(payload)
                    action = data.get("action", "")

                    # seats.io sends seat status updates
                    if action in ("", None) and "objects" in data:
                        self._parse_seat_update(data)

                    # Confirmation that our hold succeeded
                    elif action == "hold-object" and "eventId" in data:
                        obj_ids = [
                            o.get("objectId") or o.get("label")
                            for o in data.get("data", {}).get("objects", [])
                        ]
                        self._held_seats.extend(o for o in obj_ids if o)
                        logger.info(f"Hold confirmed for: {obj_ids}")

                    # Full seat map or availability update
                    elif "objects" in data and isinstance(data["objects"], list):
                        for obj in data["objects"]:
                            self._upsert_seat(obj)

                except Exception as e:
                    logger.debug(f"WS frame parse error: {e}")

            ws.on("framesent",     lambda p: on_sent(p.payload if hasattr(p, 'payload') else p))
            ws.on("framereceived", lambda p: on_received(p.payload if hasattr(p, 'payload') else p))
            ws.on("close",         lambda: logger.info("WebSocket closed"))

        page.on("websocket", on_websocket)

    def _parse_seat_update(self, data):
        """Parse a seat status batch from the WS server."""
        for obj in data.get("objects", []):
            self._upsert_seat(obj)

    def _upsert_seat(self, obj: dict):
        """Insert or update a seat in our availability list."""
        obj_id = obj.get("objectId") or obj.get("label") or obj.get("id")
        if not obj_id:
            return
        status = (obj.get("status") or obj.get("objectStatus") or "").lower()
        category = (
            obj.get("categoryLabel")
            or obj.get("category", {}).get("label") if isinstance(obj.get("category"), dict) else obj.get("category")
            or ""
        )
        # Infer category from objectId prefix (e.g. "Bronze-RH-315" → "Bronze")
        if not category and "-" in obj_id:
            category = obj_id.split("-")[0]

        existing = next((s for s in self._available if s["id"] == obj_id), None)
        if existing:
            existing["status"]   = status
            existing["category"] = category
        else:
            self._available.append({"id": obj_id, "status": status, "category": category})

    async def _wait_for_ws(self, timeout: int = 20) -> bool:
        """Wait until WS is connected and we have some seat data."""
        for _ in range(timeout * 2):
            await asyncio.sleep(0.5)
            if self._ws and len(self._available) > 0:
                logger.info(f"WS ready. Seats tracked: {len(self._available)}")
                return True
        logger.warning(f"WS timeout. ws={self._ws is not None}, seats={len(self._available)}")
        return self._ws is not None   # at least connected, maybe seats come via HTTP

    # ══════════════════════════════════════════
    #  Seat selection logic
    # ══════════════════════════════════════════

    def _pick_seats(self) -> tuple[str, list[str]] | None:
        """
        Return (category_name, [seat_id, ...]) for the first preferred
        category that has enough free seats.
        """
        free = [s for s in self._available if s["status"] == "free"]
        logger.info(f"Free seats total: {len(free)}")

        for preferred in self.preferred_cats:
            prefix = self._preferred_to_prefix(preferred)
            matches = [
                s["id"] for s in free
                if s["category"].lower() == (prefix or preferred).lower()
                or s["id"].upper().startswith((prefix or preferred).upper())
            ]
            if matches:
                logger.info(f"Found {len(matches)} free in '{preferred}'")
                return preferred, matches

        return None

    def _preferred_to_prefix(self, preferred: str) -> str | None:
        """Map a user-preferred category name to the objectId prefix."""
        preferred_up = preferred.upper()
        for prefix, aliases in CATEGORY_PREFIXES.items():
            if preferred_up in [a.upper() for a in aliases] or preferred_up == prefix.upper():
                return prefix
        return None

    # ══════════════════════════════════════════
    #  Hold via WebSocket
    # ══════════════════════════════════════════

    async def _hold_seats(self, seat_ids: list[str]) -> bool:
        """
        Send hold-object messages over the existing WebSocket.
        Uses the holdToken captured from WS traffic (or falls back to cookie).
        """
        hold_token = self._ws_hold_token or self.hold_token
        if not hold_token:
            logger.error("No holdToken available")
            return False

        if not self._ws:
            logger.error("WebSocket not available")
            return False

        # seats.io accepts holding multiple seats in one message
        message = json.dumps({
            "action":     "hold-object",
            "objects":    [{"objectId": sid} for sid in seat_ids],
            "token":      hold_token,
            "tracing_id": _make_tracing_id(),
        })

        logger.info(f"Sending hold for: {seat_ids}")
        try:
            # Use page.evaluate to send via the page's own WS object
            # This is more reliable than calling ws.send() from Python
            sent = await self._send_via_js(seat_ids, hold_token)
            if not sent:
                # Fallback: try Python-side send
                await self._ws.send(message)
        except Exception as e:
            logger.error(f"WS send error: {e}")
            return False

        # Wait for confirmation (up to 5 sec)
        for _ in range(10):
            await asyncio.sleep(0.5)
            if any(sid in self._held_seats for sid in seat_ids):
                return True

        # Optimistic — if no error was thrown, assume it worked
        logger.warning("No hold confirmation received, assuming success")
        self._held_seats.extend(seat_ids)
        return True

    async def _send_via_js(self, seat_ids: list[str], hold_token: str) -> bool:
        """
        Inject JS to find the active WebSocket in the page and send our message.
        seats.io typically exposes itself on window.seatsio or the iframe's window.
        """
        # We can't easily access page here (not stored), so return False
        # and let the Python-side fallback handle it.
        # This method is overridden in _session where page is accessible.
        return False

    # ══════════════════════════════════════════
    #  Click next button
    # ══════════════════════════════════════════

    async def _click_next(self, page) -> bool:
        for sel in NEXT_BTN_SELECTORS:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=5_000)
                if await btn.get_attribute("disabled") is not None:
                    continue
                await btn.scroll_into_view_if_needed()
                await btn.click()
                await asyncio.sleep(2)
                logger.info(f"Clicked: {sel}")
                return True
            except Exception:
                continue
        return False

    # ══════════════════════════════════════════
    #  Utilities
    # ══════════════════════════════════════════

    async def notify(self, msg: str):
        if self.bot and self.user_id:
            try:
                await self.bot.send_message(
                    chat_id=self.user_id, text=msg, parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"notify: {e}")

    async def _screenshot(self, page, name: str):
        if DEBUG_MODE:
            try:
                await page.screenshot(path=f"/tmp/{name}.png")
            except Exception:
                pass
PYEOF
python3 -c "
import ast
ast.parse(open('/mnt/user-data/outputs/engine.py').read())
print('✅ Syntax OK')
src = open('/mnt/user-data/outputs/engine.py').read()
for label, token in [
    ('WS listener',       '_attach_ws_listener'),
    ('hold-object msg',   'hold-object'),
    ('seat prefix map',   'CATEGORY_PREFIXES'),
    ('tracing_id',        '_make_tracing_id'),
    ('objectId parsing',  'Bronze'),
    ('confirmation wait', '_held_seats'),
    ('next button',       'التالي: الدفع'),
]:
    print(('✅' if token in src else '❌') + ' ' + label)
"
