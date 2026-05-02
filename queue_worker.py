import asyncio
from engine import BookingEngine

queue: asyncio.Queue = asyncio.Queue()

async def worker(bot):
    while True:
        user_id, data = await queue.get()
        try:
            engine = BookingEngine(user_data=data, bot=bot, user_id=user_id)
            result = await engine.run()
            if result:
                await bot.send_message(user_id, "✅ اكتمل الحجز! اذهب للموقع لإتمام الدفع 🎟️")
            else:
                await bot.send_message(user_id, "❌ انتهت الجلسة بدون حجز ناجح")
        except Exception as e:
            try:
                await bot.send_message(user_id, f"❌ خطأ: {str(e)[:100]}")
            except:
                pass
        finally:
            queue.task_done()
