import asyncio
from engine import BookingEngine

queue = asyncio.Queue()

async def worker(bot):
    while True:
        user_id, data = await queue.get()

        engine = BookingEngine(data)
        result = await engine.run()

        if result:
            await bot.send_message(user_id, "✅ تم الحجز")
        else:
            await bot.send_message(user_id, "❌ فشل الحجز")

        queue.task_done()
