import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from config import TARGET_URL

class BookingEngine:
    def __init__(self, user_data):
        self.cookies = user_data["cookies"]
        self.headers = user_data["headers"]

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            context = await browser.new_context(
                extra_http_headers=self.headers
            )

            await context.add_cookies(self.cookies)

            page = await context.new_page()

            stealth = Stealth()
            await stealth.apply_stealth_async(context)

            await page.goto(TARGET_URL)

            await asyncio.sleep(5)  # مكان تنفيذ منطقك

            await browser.close()
            return True
