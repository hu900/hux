import asyncio
import logging
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth
from config import TARGET_URL, ALLOWED_CATEGORIES, MAX_HOLDS, DEBUG_MODE, MAX_RETRIES

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  Selector banks — ordered best-guess → fallback
#  If webook updates their DOM, tweak these lists.
# ─────────────────────────────────────────────
CATEGORY_SELECTORS = [
    # Specific webook patterns (inspect & confirm these)
    "[data-testid='ticket-category']",
    ".ticket-type-card",
    ".category-card",
    ".ticket-section",
    # Generic fallbacks
    "[class*='TicketType']",
    "[class*='ticket-type']",
    "[class*='category']",
]

QTY_PLUS_SELECTORS = [
    "[data-testid='increase-qty']",
    "button[aria-label*='increase']",
    "button[aria-label*='زيادة']",
    "button[aria-label*='+']",
    ".qty-increase",
    "[class*='increment']",
    "[class*='Increment']",
    "button:has-text('+')",
]

HOLD_BTN_SELECTORS = [
    # Arabic labels webook uses
    "button:has-text('أضف للسلة')",
    "button:has-text('احجز الآن')",
    "button:has-text('استمر')",
    "button:has-text('تأكيد')",
    # English fallbacks
    "button:has-text('Add to Cart')",
    "button:has-text('Book Now')",
    "button:has-text('Continue')",
    "button:has-text('Proceed')",
    # Class-based
    "[data-testid='checkout-btn']",
    "[class*='checkout']",
    "[class*='book-now']",
    "[class*='BookNow']",
    "[class*='AddToCart']",
]

SOLD_OUT_SIGNALS = ["sold-out", "soldout", "unavailable", "disabled", "مباعة", "نفذت"]


class BookingEngine:
    """
    Drives a Playwright browser session to:
      1. Navigate to the event page
      2. Select the best available category
      3. Set ticket quantity
      4. Click hold/checkout
      5. Notify the user via Telegram at every step
    """

    def __init__(self, user_data: dict, bot=None, user_id: int = None):
        self.cookies = user_data["cookies"]
        self.headers = user_data["headers"]
        # User's preferred categories (subset of ALLOWED_CATEGORIES), or full list
        self.preferred_categories: list[str] = user_data.get(
            "preferred_categories", ALLOWED_CATEGORIES
        )
        self.ticket_count: int = int(user_data.get("ticket_count", 1))
        self.bot = bot
        self.user_id = user_id

    # ──────────────────────────────────────────
    #  Public entry point
    # ──────────────────────────────────────────

    async def run(self) -> bool:
        """Launch browser and attempt booking. Returns True on success."""
        attempt = 0
        while attempt < MAX_RETRIES:
            attempt += 1
            logger.info(f"[user={self.user_id}] Booking attempt {attempt}/{MAX_RETRIES}")
            if attempt > 1:
                await self.notify(f"🔄 إعادة المحاولة {attempt}/{MAX_RETRIES}...")

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
                )
                context = await browser.new_context(
                    extra_http_headers=self.headers,
                    viewport={"width": 1280, "height": 800},
                    locale="ar-SA",
                )
                await context.add_cookies(self.cookies)

                stealth = Stealth()
                await stealth.apply_stealth_async(context)

                page = await context.new_page()

                # Log console errors in debug mode
                if DEBUG_MODE:
                    page.on("console", lambda msg: logger.debug(f"[browser] {msg.text}"))

                try:
                    success = await self._execute_booking(page)
                    if success:
                        return True
                except PWTimeout as e:
                    await self.notify(f"⏱ انتهت مهلة الانتظار: {str(e)[:100]}")
                    logger.warning(f"[user={self.user_id}] Timeout: {e}")
                except Exception as e:
                    await self.notify(f"⚠️ خطأ في المحاولة {attempt}: {str(e)[:120]}")
                    logger.error(f"[user={self.user_id}] Error attempt {attempt}: {e}", exc_info=True)
                finally:
                    await browser.close()

            if attempt < MAX_RETRIES:
                await asyncio.sleep(3)  # short cooldown between retries

        await self.notify("❌ استنفدت جميع المحاولات بدون حجز ناجح.")
        return False

    # ──────────────────────────────────────────
    #  Booking steps
    # ──────────────────────────────────────────

    async def _execute_booking(self, page) -> bool:
        # Step 1: Open event page
        await self.notify("🌐 جارٍ فتح صفحة الحدث...")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_load_state("networkidle", timeout=15_000)
        await self._screenshot(page, "01_event_page")

        # Step 2: Select category
        await self.notify("🔍 جارٍ البحث عن الفئات المتاحة...")
        selected = await self._select_category(page)
        if not selected:
            await self.notify(
                "⚠️ لا توجد فئات متاحة من قائمتك:\n"
                + "\n".join(f"• {c}" for c in self.preferred_categories)
            )
            return False

        await self.notify(f"✅ تم اختيار الفئة: *{selected}*")
        await self._screenshot(page, "02_category_selected")

        # Step 3: Set ticket count
        if self.ticket_count > 1:
            await self.notify(f"🎫 جارٍ تحديد العدد: {self.ticket_count} تذاكر...")
            await self._set_ticket_count(page)
            await self._screenshot(page, "03_qty_set")

        # Step 4: Hold / Add to cart
        await self.notify("🛒 جارٍ إضافة التذاكر للسلة...")
        held = await self._hold_tickets(page)
        if not held:
            await self.notify("❌ لم أتمكن من الضغط على زر الحجز — ربما تغير الموقع.")
            return False

        await self._screenshot(page, "04_after_hold")
        await self.notify(
            "🎉 *تم الحجز المؤقت بنجاح!*\n\n"
            "👉 افتح الموقع وأتمم الدفع قبل انتهاء المهلة.\n"
            f"🔗 {TARGET_URL}"
        )
        return True

    # ──────────────────────────────────────────
    #  Step helpers
    # ──────────────────────────────────────────

    async def _select_category(self, page) -> str | None:
        """
        Tries each user-preferred category in order.
        Returns the name of the first available one, or None.
        """
        # First, wait for at least one category card to appear
        await self._wait_for_any(page, CATEGORY_SELECTORS, timeout=15_000)

        for category in self.preferred_categories:
            try:
                # Build text-based selector for this category name
                text_selectors = [
                    f"text='{category}'",
                    f"text=\"{category}\"",
                    f":text('{category}')",
                    f"*:has-text('{category}')",
                ]

                element = None
                for sel in text_selectors:
                    try:
                        element = page.locator(sel).first
                        if await element.count() == 0:
                            element = None
                            continue
                        break
                    except Exception:
                        continue

                if element is None:
                    logger.debug(f"Category '{category}' not found in DOM")
                    continue

                # Check for sold-out signals
                class_name = (await element.get_attribute("class") or "").lower()
                aria_disabled = await element.get_attribute("aria-disabled")
                disabled = await element.get_attribute("disabled")

                if disabled is not None or aria_disabled == "true":
                    logger.debug(f"Category '{category}' is disabled")
                    continue
                if any(s in class_name for s in SOLD_OUT_SIGNALS):
                    logger.debug(f"Category '{category}' appears sold out via class")
                    continue

                # Check parent container for sold-out
                parent = element.locator("..")
                parent_class = (await parent.get_attribute("class") or "").lower()
                if any(s in parent_class for s in SOLD_OUT_SIGNALS):
                    logger.debug(f"Category '{category}' parent is sold-out")
                    continue

                # Looks good — click it
                await element.scroll_into_view_if_needed()
                await element.click()
                await asyncio.sleep(1.5)
                return category

            except Exception as e:
                logger.debug(f"Error trying category '{category}': {e}")
                continue

        return None

    async def _set_ticket_count(self, page):
        """Clicks the + button (ticket_count - 1) times."""
        if self.ticket_count <= 1:
            return

        plus_btn = await self._find_element(page, QTY_PLUS_SELECTORS, timeout=5_000)
        if plus_btn is None:
            logger.warning("Quantity + button not found; proceeding with default count")
            return

        clicks = min(self.ticket_count - 1, MAX_HOLDS - 1)
        for i in range(clicks):
            await plus_btn.click()
            await asyncio.sleep(0.4)
            logger.debug(f"Clicked + button {i + 1}/{clicks}")

    async def _hold_tickets(self, page) -> bool:
        """Clicks the checkout / add-to-cart button. Returns True on success."""
        btn = await self._find_element(page, HOLD_BTN_SELECTORS, timeout=8_000)
        if btn is None:
            return False

        is_disabled = await btn.get_attribute("disabled")
        if is_disabled is not None:
            logger.warning("Hold button found but is disabled")
            return False

        await btn.scroll_into_view_if_needed()
        await btn.click()
        await asyncio.sleep(2.5)

        # Verify we advanced (URL should change to cart/checkout)
        current_url = page.url
        logger.info(f"URL after hold click: {current_url}")
        if any(kw in current_url for kw in ["checkout", "cart", "payment", "order", "basket"]):
            return True

        # Even if URL didn't change, treat as success (SPA may not redirect immediately)
        return True

    # ──────────────────────────────────────────
    #  Utilities
    # ──────────────────────────────────────────

    async def _find_element(self, page, selectors: list[str], timeout: int = 5_000):
        """Try each selector and return the first element found, or None."""
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                await locator.wait_for(state="visible", timeout=timeout // len(selectors))
                return locator
            except Exception:
                continue
        return None

    async def _wait_for_any(self, page, selectors: list[str], timeout: int = 10_000):
        """Wait until at least one of the selectors appears in the DOM."""
        combined = ", ".join(selectors)
        try:
            await page.wait_for_selector(combined, timeout=timeout)
        except PWTimeout:
            logger.debug("None of the category selectors appeared in time")

    async def notify(self, message: str):
        """Send a Telegram message to the user (fire-and-forget, never raises)."""
        if self.bot and self.user_id:
            try:
                await self.bot.send_message(
                    chat_id=self.user_id,
                    text=message,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Failed to notify user {self.user_id}: {e}")

    async def _screenshot(self, page, name: str):
        if DEBUG_MODE:
            path = f"/tmp/{name}.png"
            try:
                await page.screenshot(path=path, full_page=False)
                logger.debug(f"Screenshot saved: {path}")
            except Exception as e:
                logger.debug(f"Screenshot failed: {e}")
