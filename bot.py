import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from utils import parse_cookie_string
from queue_worker import queue, worker

TOKEN = os.getenv("BOT_TOKEN")
users = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *أهلاً في بوت حجز التذاكر*\n\n"
        "📋 *الأوامر:*\n"
        "/setcookies — حفظ الكوكيز\n"
        "/book — بدء الحجز\n"
        "/status — حالتك الحالية\n\n"
        "🔑 للبدء أرسل الأمر /setcookies ثم الصق الكوكيز",
        parse_mode="Markdown"
    )


async def set_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        cookie_text = " ".join(context.args)
    elif update.message.reply_to_message:
        cookie_text = update.message.reply_to_message.text
    else:
        await update.message.reply_text(
            "📋 أرسل الكوكيز بعد الأمر مباشرة:\n"
            "`/setcookies name1=val1; name2=val2`\n\n"
            "أو أرسل الكوكيز كرسالة عادية وأنا أحفظها تلقائياً",
            parse_mode="Markdown"
        )
        context.user_data["waiting_cookies"] = True
        return

    try:
        cookies = parse_cookie_string(cookie_text)
        if not cookies:
            await update.message.reply_text("❌ لم أجد كوكيز صالحة")
            return
        user_id = update.effective_user.id
        users[user_id] = {
            "cookies": cookies,
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            }
        }
        context.user_data["waiting_cookies"] = False
        await update.message.reply_text(
            f"✅ تم حفظ {len(cookies)} cookie بنجاح\n"
            f"الآن أرسل /book لبدء الحجز"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في تحليل الكوكيز: {str(e)[:80]}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_cookies"):
        return
    cookie_text = update.message.text
    try:
        cookies = parse_cookie_string(cookie_text)
        if not cookies:
            await update.message.reply_text("❌ لم أجد كوكيز صالحة")
            return
        user_id = update.effective_user.id
        users[user_id] = {
            "cookies": cookies,
            "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        }
        context.user_data["waiting_cookies"] = False
        await update.message.reply_text(
            f"✅ تم حفظ {len(cookies)} cookie\n"
            f"أرسل /book لبدء الحجز 🎯"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)[:80]}")


async def book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in users:
        await update.message.reply_text("❌ أرسل الكوكيز أولاً عبر /setcookies")
        return
    await queue.put((user_id, users[user_id]))
    await update.message.reply_text(
        "⏳ *تم إضافة طلبك للقائمة*\n\n"
        "🔍 البوت سيراقب المقاعد المتاحة في الفئات المسموحة\n"
        "🔔 ستصلك إشعارات فورية عند كل إجراء",
        parse_mode="Markdown"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in users:
        n = len(users[user_id].get("cookies", []))
        await update.message.reply_text(
            f"✅ حسابك محفوظ\n"
            f"🍪 عدد الكوكيز: {n}\n"
            f"📦 قائمة الانتظار: {queue.qsize()}"
        )
    else:
        await update.message.reply_text("❌ لا يوجد حساب محفوظ، أرسل /setcookies")


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
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
