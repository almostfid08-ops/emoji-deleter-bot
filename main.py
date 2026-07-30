import os
import logging
import asyncio
import json
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters
)

FORBIDDEN_EMOJIS = ["😂", "🤣", "💩"]
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE = "groups_data.json"

# حالة مؤقتة لمعرفة هل ينتظر البوت نص الإذاعة من المشرف أم لا
WAITING_FOR_BROADCAST = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# === 1. حفظ المجموعات ===
def load_groups():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_group(chat_id):
    groups = load_groups()
    if chat_id not in groups:
        groups.append(chat_id)
        with open(DATA_FILE, "w") as f:
            json.dump(groups, f)

# === 2. سيرفر الويب لإبقاء Render يقظاً ===
async def handle_ping(request):
    return web.Response(text="Bot is awake and running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# === 3. أمر /start والأزرار ===
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # زر شفاف للإذاعة
    keyboard = [
        [InlineKeyboardButton("📢 إذاعة للمجموعات", callback_data="broadcast_groups")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "أهلاً بك في لوحة تحكم البوت! 🤖\n\n"
        "يمكنك استخدام الزر أدناه لإرسال إعلان لجميع المجموعات التي يتواجد بها البوت:",
        reply_markup=reply_markup
    )

# === 4. الاستجابة للضغط على الزر ===
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "broadcast_groups":
        user_id = query.from_user.id
        WAITING_FOR_BROADCAST[user_id] = True
        
        await query.message.reply_text(
            "📝 **أرسل الآن الرسالة أو المنشور المراد إذاعته للمجموعات:**\n"
            "(يمكنك إرسال نص، صورة، أو نص مع صورة)",
            parse_mode='Markdown'
        )

# === 5. استقبال رسالة الإذاعة وإرسالها ===
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.message.from_user.id

    # التأكد أن المستخدم ضغط على زر الإذاعة أولاً
    if WAITING_FOR_BROADCAST.get(user_id):
        WAITING_FOR_BROADCAST[user_id] = False  # إعادة ضبط الحالة
        
        groups = load_groups()
        if not groups:
            await update.message.reply_text("❌ لا توجد مجموعات مسجلة حالياً! تأكد من إرسال رسالة في المجموعة أولاً ليتعرف البوت عليها.")
            return

        status_msg = await update.message.reply_text("⏳ جاري إرسال الإذاعة لجميع المجموعات...")

        sent_count = 0
        failed_count = 0

        for group_id in groups:
            try:
                await update.message.copy(chat_id=group_id)
                sent_count += 1
                await asyncio.sleep(0.1)
            except Exception as e:
                failed_count += 1
                print(f"فشل الإرسال: {e}")

        await status_msg.edit_text(
            f"✅ **تمت الإذاعة بنجاح!**\n\n"
            f"• تم الإرسال إلى: `{sent_count}` مجموعة\n"
            f"• فشل الإرسال إلى: `{failed_count}` مجموعة",
            parse_mode='Markdown'
        )
    else:
        # إذا أرسل رسالة عادية دون الضغط على الزر
        await update.message.reply_text("يرجى الضغط على زر **📢 إذاعة للمجموعات** أولاً من أمر /start لبدء الإذاعة.")

# === 6. حذف الإيموجي في المجموعات ===
async def group_emoji_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return

    # حفظ ID المجموعة بمجرد وصول أي رسالة
    save_group(update.effective_chat.id)

    if not update.message.text:
        return

    text = update.message.text
    user = update.message.from_user
    first_name = user.first_name if user and user.first_name else "المستخدم"

    for emoji in FORBIDDEN_EMOJIS:
        if emoji in text:
            try:
                await update.message.delete()
                warning_msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"عذراً يا {first_name}، يمنع استخدام هذا الإيموجي!"
                )
                await asyncio.sleep(5)
                await warning_msg.delete()
                break
            except Exception as e:
                print(f"خطأ أثناء الحذف: {e}")

# === 7. تشغيل البوت ===
async def main():
    if not BOT_TOKEN:
        print("خطأ: لم يتم ضبط BOT_TOKEN!")
        return

    await start_web_server()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # الأوامر والأزرار في الخاص
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (~filters.COMMAND), handle_private_message))

    # المجموعات
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_emoji_filter))

    print("البوت الشامل مع الأزرار يعمل بنجاح...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
