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

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# المشرفين الأساسيين (الذين أرسلتهم)
INITIAL_ADMINS = [1611988598, 7065061464]

DATA_FILE = "bot_data.json"
WAITING_STATES = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# === 1. إدارة قاعدة البيانات (المجموعات، المشرفين، الإيموجيات، الكلمات) ===
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # دمج المشرفين الأوليين دائماً
                for admin in INITIAL_ADMINS:
                    if admin not in data.get("admins", []):
                        data.setdefault("admins", []).append(admin)
                return data
        except Exception:
            pass
    return {
        "groups": {}, # {chat_id_str: chat_title}
        "admins": INITIAL_ADMINS,
        "emojis": ["😂", "🤣", "💩"],
        "words": []
    }

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_admin(user_id):
    data = load_data()
    return user_id in data.get("admins", [])

def register_group(chat_id, title):
    data = load_data()
    chat_id_str = str(chat_id)
    if chat_id_str not in data["groups"] or data["groups"][chat_id_str] != title:
        data["groups"][chat_id_str] = title
        save_data(data)

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

# === 3. لوحة تحكم المشرفين ===
def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📢 إذاعة عامة لكل المجموعات", callback_data="bc_all")],
        [InlineKeyboardButton("🎯 إذاعة لمجموعة محددة", callback_data="bc_single_select")],
        [InlineKeyboardButton("👤 إضافة مشرف جديد", callback_data="add_admin")],
        [InlineKeyboardButton("⛔ إدارة الكلمات المحظورة", callback_data="manage_words"),
         InlineKeyboardButton("😀 إدارة الإيموجيات المحظورة", callback_data="manage_emojis")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id):
        await update.message.reply_text(
            "أهلاً بك يا أدمن في لوحة التحكم الإدارية! 🛠️\nاختر من الأزرار أدناه للتحكم بالبوت:",
            reply_markup=get_admin_keyboard()
        )
    else:
        await update.message.reply_text("مرحباً بك! هذا البوت مخصص لإدارة وحماية المجموعات تلقائياً.")

# === 4. الاستجابة للأزرار الشفافة ===
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if not is_admin(user_id):
        await query.message.reply_text("❌ عذراً، هذه اللوحة مخصصة للمشرفين فقط.")
        return

    data_action = query.data

    # إذاعة عامة
    if data_action == "bc_all":
        WAITING_STATES[user_id] = "bc_all"
        await query.message.reply_text("📝 **أرسل الآن المنشور أو الرسالة لإذاعتها لجميع المجموعات:**", parse_mode='Markdown')

    # اختيار مجموعة محددة للإذاعة
    elif data_action == "bc_single_select":
        bot_data = load_data()
        groups = bot_data.get("groups", {})
        if not groups:
            await query.message.reply_text("❌ لا توجد مجموعات مسجلة حالياً.")
            return

        keyboard = []
        for g_id, g_title in groups.items():
            keyboard.append([InlineKeyboardButton(f"👥 {g_title}", callback_data=f"bc_to_{g_id}")])
        
        await query.message.reply_text("🎯 **اختر المجموعة التي تريد إرسال الإذاعة لها:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data_action.startswith("bc_to_"):
        target_group_id = data_action.replace("bc_to_", "")
        WAITING_STATES[user_id] = f"bc_single_{target_group_id}"
        await query.message.reply_text("📝 **أرسل الآن الرسالة المراد توجيهها لهذه المجموعة:**")

    # إضافة مشرف جديد
    elif data_action == "add_admin":
        WAITING_STATES[user_id] = "add_admin"
        await query.message.reply_text("👤 **أرسل الآن المعرف (Telegram ID) الخاص بالمشرف الجديد:**\n(مثال: 123456789)")

    # إدارة الكلمات المحظورة
    elif data_action == "manage_words":
        bot_data = load_data()
        words = bot_data.get("words", [])
        words_text = ", ".join(words) if words else "لا توجد كلمات محظورة حالياً."
        
        keyboard = [
            [InlineKeyboardButton("➕ إضافة كلمة محظورة", callback_data="add_word")],
            [InlineKeyboardButton("🗑️ مسح جميع الكلمات", callback_data="clear_words")]
        ]
        await query.message.reply_text(f"⛔ **الكلمات المحظورة حالياً:**\n\n`{words_text}`", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    elif data_action == "add_word":
        WAITING_STATES[user_id] = "add_word"
        await query.message.reply_text("✏️ **أرسل الكلمة الجديدة المراد حظرها:**")

    elif data_action == "clear_words":
        bot_data = load_data()
        bot_data["words"] = []
        save_data(bot_data)
        await query.message.reply_text("✅ تم تفريغ قائمة الكلمات المحظورة بنجاح.")

    # إدارة الإيموجيات المحظورة
    elif data_action == "manage_emojis":
        bot_data = load_data()
        emojis = bot_data.get("emojis", [])
        emojis_text = " ".join(emojis) if emojis else "لا توجد إيموجيات محظورة حالياً."
        
        keyboard = [
            [InlineKeyboardButton("➕ إضافة إيموجي محظور", callback_data="add_emoji")],
            [InlineKeyboardButton("🗑️ مسح جميع الإيموجيات", callback_data="clear_emojis")]
        ]
        await query.message.reply_text(f"😀 **الإيموجيات المحظورة حالياً:**\n\n{emojis_text}", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data_action == "add_emoji":
        WAITING_STATES[user_id] = "add_emoji"
        await query.message.reply_text("✏️ **أرسل الإيموجي الجديد المراد حظره:**")

    elif data_action == "clear_emojis":
        bot_data = load_data()
        bot_data["emojis"] = []
        save_data(bot_data)
        await query.message.reply_text("✅ تم تفريغ قائمة الإيموجيات المحظورة بنجاح.")

# === 5. معالجة النصوص الواردة في الخاص (إدخال البيانات والرسائل) ===
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.message.from_user.id
    if not is_admin(user_id):
        return

    state = WAITING_STATES.get(user_id)
    if not state:
        await update.message.reply_text("يرجى استخدام الأوامر عبر القائمة من /start")
        return

    bot_data = load_data()

    # إذاعة عامة
    if state == "bc_all":
        WAITING_STATES[user_id] = None
        groups = bot_data.get("groups", {})
        status_msg = await update.message.reply_text("⏳ جاري الإذاعة لكل المجموعات...")
        sent, failed = 0, 0
        for g_id in groups.keys():
            try:
                await update.message.copy(chat_id=int(g_id))
                sent += 1
                await asyncio.sleep(0.1)
            except Exception:
                failed += 1
        await status_msg.edit_text(f"✅ تمت الإذاعة العامة!\n- نجاح: {sent}\n- فشل: {failed}")

    # إذاعة لمجموعة واحدة
    elif state.startswith("bc_single_"):
        WAITING_STATES[user_id] = None
        target_g_id = state.replace("bc_single_", "")
        try:
            await update.message.copy(chat_id=int(target_g_id))
            await update.message.reply_text("✅ تم إرسال المنشور للمجموعة المحددة بنجاح!")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل الإرسال للمجموعة: {e}")

    # إضافة مشرف
    elif state == "add_admin":
        WAITING_STATES[user_id] = None
        try:
            new_admin_id = int(update.message.text.strip())
            if new_admin_id not in bot_data["admins"]:
                bot_data["admins"].append(new_admin_id)
                save_data(bot_data)
                await update.message.reply_text(f"✅ تم منح صلاحيات المشرف للـ ID: `{new_admin_id}` بنجاح!", parse_mode='Markdown')
            else:
                await update.message.reply_text("⚠️ هذا المستخدم أدمن بالفعل.")
        except ValueError:
            await update.message.reply_text("❌ خطأ، يرجى إرسال أرقام الـ ID فقط.")

    # إضافة كلمة
    elif state == "add_word":
        WAITING_STATES[user_id] = None
        new_word = update.message.text.strip().lower()
        if new_word not in bot_data["words"]:
            bot_data["words"].append(new_word)
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم إضافة الكلمة `{new_word}` إلى قائمة الحظر!", parse_mode='Markdown')

    # إضافة إيموجي
    elif state == "add_emoji":
        WAITING_STATES[user_id] = None
        new_emoji = update.message.text.strip()
        if new_emoji not in bot_data["emojis"]:
            bot_data["emojis"].append(new_emoji)
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم إضافة الإيموجي {new_emoji} إلى قائمة الحظر!")

# === 6. حماية المجموعات (حذف الكلمات والإيموجيات) ===
async def group_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return

    # حفظ المجموعة باسمها
    register_group(update.effective_chat.id, update.effective_chat.title or "مجموعة")

    if not update.message.text:
        return

    text = update.message.text
    text_lower = text.lower()
    user = update.message.from_user
    first_name = user.first_name if user and user.first_name else "المستخدم"

    bot_data = load_data()
    forbidden_emojis = bot_data.get("emojis", [])
    forbidden_words = bot_data.get("words", [])

    is_violating = False
    reason = ""

    # فحص الإيموجي
    for emoji in forbidden_emojis:
        if emoji in text:
            is_violating = True
            reason = "إيموجي ممنوع"
            break

    # فحص الكلمات
    if not is_violating:
        for word in forbidden_words:
            if word in text_lower:
                is_violating = True
                reason = "كلمة محظورة"
                break

    if is_violating:
        try:
            await update.message.delete()
            warning_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"عذراً يا {first_name}، يمنع استخدام ({reason}) في المجموعة!"
            )
            await asyncio.sleep(5)
            await warning_msg.delete()
        except Exception as e:
            print(f"خطأ أثناء الحذف: {e}")

# === 7. تشغيل البوت ===
async def main():
    if not BOT_TOKEN:
        print("خطأ: لم يتم ضبط BOT_TOKEN!")
        return

    await start_web_server()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # الخاص والمشرفين
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (~filters.COMMAND), handle_private_message))

    # المجموعات
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_filter))

    print("البوت المتطور يعمل بنجاح...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
