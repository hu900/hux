import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from utils import parse_cookie_string
from queue_worker import queue, worker

TOKEN = os.getenv("BOT_TOKEN")
users = {}

async def start(update: Update, context):
    await update.message.reply_text(
        "👋 مرحباً!\n\n"
        "الأوامر المتاحة:\n"
        "/setcookies <cookies> — حفظ الكوكيز\n"
        "/book — بدء الحجز\n"
        "/status — حالة طلبك"
    )

async def set_cookies(update: Update, context):
    if not context.args:
        await update.message.reply_text("❌ الاستخدام: /setcookies <cookie_string>")
        return
    cookie_text = " ".join(context.args)
    try:
        cookies = parse_cookie_string(cookie_text)
        user_id = update.effective_user.id
        users[user_id] = {"cookies": cookies, "headers": {"User-Agent": "Mozilla/5.0"}}
        await update.message.reply_text("✅ تم حفظ حسابك بنجاح")
    except Exception:
        await update.message.reply_text("❌ صيغة الكوكيز غلط، تأكد من النسخ الصحيح")

async def book(update: Update, context):
    user_id = update.effective_user.id
    if user_id not in users:
        await update.message.reply_text("❌ أرسل الكوكيز أولاً عبر /setcookies")
        return
    await queue.put((user_id, users[user_id]))
    await update.message.reply_text("⏳ تم إضافة طلبك للقائمة")

async def post_init(app):
    asyncio.create_task(worker(app.bot))

def main():
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setcookies", set_cookies))
    app.add_handler(CommandHandler("book", book))
    app.run_polling()

if __name__ == "__main__":
    main()
