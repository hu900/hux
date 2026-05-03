import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from utils import parse_cookie_input, build_headers, validate_cookies
from queue_worker import queue, worker, is_user_active, queue_size
from config import ALLOWED_CATEGORIES, MAX_HOLDS, WORKER_COUNT

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")

# ─────────────────────────────────────────────
#  In-memory user store
#  Structure per user_id:
#    {
#      "cookies": [...],
#      "headers": {...},
#      "preferred_categories": [...],   # subset of ALLOWED_CATEGORIES
#      "ticket_count": int,
#    }
# ─────────────────────────────────────────────
users: dict[int, dict] = {}


# ══════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *أهلاً في بوت حجز التذاكر*\n\n"
        "📋 *الأوامر المتاحة:*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🍪 /setcookies — حفظ كوكيز الجلسة\n"
        "🏷 /setcategories — اختيار الفئات المفضلة\n"
        "🎫 /setcount `N` — عدد التذاكر (افتراضي: 1)\n"
        "🚀 /book — بدء الحجز الآن\n"
        "❌ /cancel — إلغاء طلبك من القائمة\n"
        "📊 /status — حالة القائمة\n"
        "👤 /myinfo — بياناتك المحفوظة\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔑 *للبدء:*\n"
        "1️⃣ أرسل /setcookies\n"
        "2️⃣ اختر فئاتك بـ /setcategories\n"
        "3️⃣ أرسل /book عند فتح الحجز",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════
#  /setcookies
# ══════════════════════════════════════════════
async def set_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if context.args:
        cookie_text = " ".join(context.args)
        await _save_cookies(update, context, user_id, cookie_text)
    else:
        context.user_data["waiting_cookies"] = True
        await update.message.reply_text(
            "🍪 *كيف ترسل الكوكيز؟*\n\n"
            "*الطريقة الأسهل — Cookie-Editor:*\n"
            "1. افتح webook.com وسجّل دخولك\n"
            "2. افتح إضافة Cookie-Editor من المتصفح\n"
            "3. اضغط زر التصدير ⬆️ (Export) في الأسفل\n"
            "4. الصق النص هنا مباشرةً\n\n"
            "_البوت يقبل JSON من Cookie-Editor أو صيغة name=value_",
            parse_mode="Markdown",
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catches plain text when bot is waiting for cookies."""
    if not context.user_data.get("waiting_cookies"):
        return
    user_id = update.effective_user.id
    await _save_cookies(update, context, user_id, update.message.text)


async def _save_cookies(update, context, user_id: int, cookie_text: str):
    try:
        cookies = parse_cookie_input(cookie_text)
        if not cookies:
            await update.message.reply_text(
                "❌ لم أجد كوكيز صالحة\n\nتأكد أنك ضغطت Export في Cookie-Editor وليس نسخة كوكي واحدة."
            )
            return

        if user_id not in users:
            users[user_id] = {}

        users[user_id]["cookies"] = cookies
        users[user_id]["headers"] = build_headers()
        users[user_id].setdefault("preferred_categories", list(ALLOWED_CATEGORIES))
        users[user_id].setdefault("ticket_count", 1)

        context.user_data["waiting_cookies"] = False

        is_valid, validation_msg = validate_cookies(cookies)
        if not is_valid:
            await update.message.reply_text(validation_msg)
            return

        await update.message.reply_text(
            f"✅ تم حفظ *{len(cookies)}* cookie بنجاح!\n"
            f"{validation_msg}\n\n"
            f"الخطوة التالية:\n"
            f"• اختر فئاتك المفضلة بـ /setcategories\n"
            f"• أو ابدأ الحجز مباشرة بـ /book",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في تحليل الكوكيز: {str(e)[:100]}")


# ══════════════════════════════════════════════
#  /setcategories — inline multi-select keyboard
# ══════════════════════════════════════════════
async def set_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    selected = users.get(user_id, {}).get("preferred_categories", list(ALLOWED_CATEGORIES))
    await update.message.reply_text(
        "🏷 *اختر الفئات التي تريد حجزها:*\n"
        "اضغط للتحديد/الإلغاء ثم اضغط ✅ *حفظ*",
        parse_mode="Markdown",
        reply_markup=_build_category_keyboard(selected),
    )


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    data = query.data  # e.g.  "cat_toggle:C5"  or  "cat_save"

    if data == "cat_save":
        selected = users.get(user_id, {}).get("preferred_categories", [])
        if not selected:
            await query.edit_message_text("❌ يجب اختيار فئة واحدة على الأقل.")
            return
        await query.edit_message_text(
            f"✅ تم حفظ {len(selected)} فئة:\n"
            + "\n".join(f"• {c}" for c in selected)
        )
        return

    if data.startswith("cat_toggle:"):
        category = data.split(":", 1)[1]
        if user_id not in users:
            users[user_id] = {}
        selected = users[user_id].setdefault("preferred_categories", list(ALLOWED_CATEGORIES))

        if category in selected:
            selected.remove(category)
        else:
            selected.append(category)

        await query.edit_message_reply_markup(
            reply_markup=_build_category_keyboard(selected)
        )


def _build_category_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    """Renders ALLOWED_CATEGORIES as 2-column inline buttons with ✅/🔲 indicators."""
    rows = []
    cats = list(ALLOWED_CATEGORIES)

    for i in range(0, len(cats), 2):
        row = []
        for cat in cats[i : i + 2]:
            tick = "✅" if cat in selected else "🔲"
            row.append(
                InlineKeyboardButton(
                    f"{tick} {cat}",
                    callback_data=f"cat_toggle:{cat}",
                )
            )
        rows.append(row)

    rows.append([InlineKeyboardButton("💾 حفظ الاختيارات", callback_data="cat_save")])
    return InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════
#  /setcount
# ══════════════════════════════════════════════
async def set_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args or not context.args[0].isdigit():
        count = users.get(user_id, {}).get("ticket_count", 1)
        await update.message.reply_text(
            f"🎫 عدد التذاكر الحالي: *{count}*\n\n"
            f"لتغييره: `/setcount 2`\n"
            f"الحد الأقصى: *{MAX_HOLDS}*",
            parse_mode="Markdown",
        )
        return

    count = int(context.args[0])
    if count < 1 or count > MAX_HOLDS:
        await update.message.reply_text(
            f"❌ العدد يجب أن يكون بين 1 و {MAX_HOLDS}"
        )
        return

    if user_id not in users:
        users[user_id] = {}
    users[user_id]["ticket_count"] = count
    await update.message.reply_text(f"✅ تم تحديد عدد التذاكر: *{count}*", parse_mode="Markdown")


# ══════════════════════════════════════════════
#  /book
# ══════════════════════════════════════════════
async def book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in users or not users[user_id].get("cookies"):
        await update.message.reply_text("❌ أرسل الكوكيز أولاً عبر /setcookies")
        return

    if is_user_active(user_id):
        await update.message.reply_text(
            "⏳ طلبك السابق لا يزال قيد المعالجة.\n"
            "انتظر حتى ينتهي أو استخدم /cancel لإلغائه."
        )
        return

    selected_cats = users[user_id].get("preferred_categories", [])
    ticket_count = users[user_id].get("ticket_count", 1)

    await queue.put((user_id, users[user_id]))

    await update.message.reply_text(
        "⏳ *تم إضافة طلبك للقائمة!*\n\n"
        f"🏷 الفئات: {', '.join(selected_cats) if selected_cats else 'الكل'}\n"
        f"🎫 عدد التذاكر: {ticket_count}\n"
        f"📦 موقعك في القائمة: {queue_size()}\n\n"
        "🔔 ستصلك إشعارات عند كل خطوة.",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════
#  /cancel
# ══════════════════════════════════════════════
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if is_user_active(user_id):
        await update.message.reply_text(
            "⚠️ طلبك قيد التنفيذ الآن ولا يمكن إلغاؤه.\n"
            "انتظر حتى ينتهي."
        )
        return

    # Drain matching entries from the queue (rebuild it without this user)
    removed = 0
    temp = []
    while not queue.empty():
        try:
            item = queue.get_nowait()
            if item[0] == user_id:
                removed += 1
                queue.task_done()
            else:
                temp.append(item)
        except Exception:
            break

    for item in temp:
        await queue.put(item)

    if removed:
        await update.message.reply_text(f"✅ تم إلغاء {removed} طلب من القائمة.")
    else:
        await update.message.reply_text("ℹ️ لا يوجد لك طلبات في القائمة حالياً.")


# ══════════════════════════════════════════════
#  /status
# ══════════════════════════════════════════════
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    in_queue = is_user_active(user_id)
    qs = queue_size()

    if user_id in users:
        n_cookies = len(users[user_id].get("cookies", []))
        cats = users[user_id].get("preferred_categories", [])
        count = users[user_id].get("ticket_count", 1)
        state = "🔄 قيد التنفيذ" if in_queue else "⏸ في الانتظار"
        await update.message.reply_text(
            f"👤 *حالتك:*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🍪 Cookies: {n_cookies}\n"
            f"🏷 الفئات: {', '.join(cats) if cats else '—'}\n"
            f"🎫 عدد التذاكر: {count}\n"
            f"📌 الحالة: {state}\n"
            f"📦 حجم القائمة الكلي: {qs}\n",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "❌ لا يوجد حساب محفوظ.\n"
            "ابدأ بـ /setcookies"
        )


# ══════════════════════════════════════════════
#  /myinfo
# ══════════════════════════════════════════════
async def myinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in users:
        await update.message.reply_text("❌ لا توجد بيانات محفوظة. ابدأ بـ /setcookies")
        return

    data = users[user_id]
    cookies = data.get("cookies", [])
    cats = data.get("preferred_categories", [])
    count = data.get("ticket_count", 1)

    # Show first 2 cookie names only (security)
    preview = ", ".join(c["name"] for c in cookies[:2])
    if len(cookies) > 2:
        preview += f", ... (+{len(cookies) - 2})"

    await update.message.reply_text(
        f"👤 *معلوماتك المحفوظة:*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🍪 Cookies: {len(cookies)} ({preview})\n"
        f"🏷 الفئات: {', '.join(cats) if cats else '—'}\n"
        f"🎫 عدد التذاكر: {count}\n\n"
        f"لتغيير الفئات: /setcategories\n"
        f"لتغيير العدد: /setcount\n"
        f"لتحديث الكوكيز: /setcookies",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════
#  App setup
# ══════════════════════════════════════════════
async def post_init(app):
    """Start background worker(s) after the bot is initialised."""
    for i in range(1, WORKER_COUNT + 1):
        asyncio.create_task(worker(app.bot, worker_id=i))
    logger.info(f"Started {WORKER_COUNT} queue worker(s)")


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set!")

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setcookies", set_cookies))
    app.add_handler(CommandHandler("setcategories", set_categories))
    app.add_handler(CommandHandler("setcount", set_count))
    app.add_handler(CommandHandler("book", book))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("myinfo", myinfo))
    app.add_handler(CallbackQueryHandler(category_callback, pattern=r"^cat_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 Bot started. Listening for updates...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
