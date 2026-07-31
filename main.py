import os
import logging
import asyncio
import json
import re
from datetime import datetime, timedelta
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
INITIAL_ADMINS = [1611988598, 7065061464]
DATA_FILE = "bot_data.json"
WAITING_STATES = {}
TEMP_BROADCAST = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# === 1. إدارة قاعدة البيانات ===
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for admin in INITIAL_ADMINS:
                    if admin not in data.get("admins", []):
                        data.setdefault("admins", []).append(admin)
                data.setdefault("users", [])
                return data
        except Exception:
            pass
    return {
        "groups": {},
        "users": [],
        "admins": INITIAL_ADMINS,
        "emojis": ["😂", "🤣", "💩"],
        "words": [],
        "silent_mode": {
            "enabled": False,
            "start_time": "22:00",
            "end_time": "07:00",
            "until_timestamp": 0,
            "custom_message": "🔇 المجموعة الآن في الوضع الصامت. الكتابة مقتصرة على المشرفين فقط."
        }
    }

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_bot_admin(user_id):
    data = load_data()
    return user_id in data.get("admins", [])

async def is_group_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    if is_bot_admin(user_id):
        return True
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ['creator', 'administrator']
    except Exception:
        return False

def register_group(chat_id, title):
    data = load_data()
    chat_id_str = str(chat_id)
    if chat_id_str not in data["groups"] or data["groups"][chat_id_str] != title:
        data["groups"][chat_id_str] = title
        save_data(data)

def register_user(user_id):
    data = load_data()
    if user_id not in data.get("users", []):
        data.setdefault("users", []).append(user_id)
        save_data(data)

# === 2. سيرفر الويب ===
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

# === 3. تحليل أزرار الروابط الملونة ===
def parse_button_markup(text):
    keyboard = []
    lines = text.strip().split("\n")
    
    style_map = {
        "green": "success",
        "blue": "primary",
        "red": "danger"
    }

    for line in lines:
        row = []
        btn_parts = line.split("|")
        for part in btn_parts:
            part_str = part.strip()
            if not part_str:
                continue

            # استخراج لون الستايل
            btn_style = None
            style_match = re.search(r'(?:-\s*style:|\[)(green|blue|red)(?:\])?', part_str, re.IGNORECASE)
            if style_match:
                color_name = style_match.group(1).lower()
                btn_style = style_map.get(color_name)
                # تنظيف النص من وسم اللون
                part_str = re.sub(r'\s*-\s*style:(green|blue|red)|\s*\[(green|blue|red)\]', '', part_str, flags=re.IGNORECASE).strip()

            if "-" in part_str:
                sub_parts = part_str.split("-", 1)
                title = sub_parts[0].strip()
                url = sub_parts[1].strip()

                if url.startswith("http://") or url.startswith("https://"):
                    btn_kwargs = {"text": title, "url": url}
                    if btn_style:
                        btn_kwargs["style"] = btn_style
                    row.append(InlineKeyboardButton(**btn_kwargs))

        if row:
            keyboard.append(row)
            
    return InlineKeyboardMarkup(keyboard) if keyboard else None

# === 4. القوائم واللوحات ===
def get_main_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📢 إذاعة عامة للمجموعات", callback_data="bc_all"),
         InlineKeyboardButton("🎯 إذاعة مخصصة لمجموعة", callback_data="bc_single_select")],
        [InlineKeyboardButton("👤 إذاعة للمستخدمين (خاص)", callback_data="bc_users")],
        [InlineKeyboardButton("🔇 إدارة الوضع الصامت", callback_data="manage_silent")],
        [InlineKeyboardButton("📖 دليل أوامر الإشراف", callback_data="show_cmd_help")],
        [InlineKeyboardButton("👤 إضافة مشرف جديد", callback_data="add_admin")],
        [InlineKeyboardButton("⛔ الكلمات المحظورة", callback_data="manage_words"),
         InlineKeyboardButton("😀 الإيموجيات المحظورة", callback_data="manage_emojis")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_silent_keyboard(data):
    silent_info = data.get("silent_mode", {})
    status = "🟢 مفعل" if silent_info.get("enabled") else "🔴 معطل"
    
    keyboard = [
        [InlineKeyboardButton(f"الحالة الحالية: {status}", callback_data="toggle_silent")],
        [InlineKeyboardButton("⏱️ وضع مدة مؤقتة جاهزة", callback_data="silent_durations")],
        [InlineKeyboardButton("⏰ ضبط توقيت يومي (من/إلى)", callback_data="set_silent_schedule")],
        [InlineKeyboardButton("✏️ تعديل الرسالة التوضيحية", callback_data="edit_silent_msg")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_durations_keyboard():
    keyboard = [
        [InlineKeyboardButton("5 دقائق", callback_data="dur_5"), InlineKeyboardButton("15 دقيقة", callback_data="dur_15"), InlineKeyboardButton("20 دقيقة", callback_data="dur_20")],
        [InlineKeyboardButton("25 دقيقة", callback_data="dur_25"), InlineKeyboardButton("30 دقيقة", callback_data="dur_30"), InlineKeyboardButton("35 دقيقة", callback_data="dur_35")],
        [InlineKeyboardButton("40 دقيقة", callback_data="dur_40"), InlineKeyboardButton("45 دقيقة", callback_data="dur_45"), InlineKeyboardButton("ساعة واحدة", callback_data="dur_60")],
        [InlineKeyboardButton("ساعة ونصف", callback_data="dur_90"), InlineKeyboardButton("ساعتان", callback_data="dur_120")],
        [InlineKeyboardButton("🔙 رجوع للوضع الصامت", callback_data="manage_silent")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_buttons_decision_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ إضافة أزرار روابط", callback_data="btn_add_yes")],
        [InlineKeyboardButton("🚀 إرسال بدون أزرار", callback_data="btn_add_no")],
        [InlineKeyboardButton("❌ إلغاء الإذاعة", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard(target="main_menu"):
    keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data=target)]]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)
    if is_bot_admin(user_id):
        WAITING_STATES[user_id] = None
        await update.message.reply_text(
            "أهلاً بك يا أدمن في لوحة التحكم الإدارية! 🛠️\nاختر من الأزرار أدناه للتحكم بالبوت:",
            reply_markup=get_main_admin_keyboard()
        )
    else:
        await update.message.reply_text("مرحباً بك! هذا البوت مخصص لإدارة وحماية المجموعات تلقائياً.")

# === 5. التحكم في الأزرار ===
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if not is_bot_admin(user_id):
        await query.message.reply_text("❌ عذراً، هذه اللوحة مخصصة للمشرفين فقط.")
        return

    action = query.data
    bot_data = load_data()

    if action == "main_menu":
        WAITING_STATES[user_id] = None
        TEMP_BROADCAST.pop(user_id, None)
        await query.message.edit_text("أهلاً بك في لوحة التحكم الإدارية! 🛠️", reply_markup=get_main_admin_keyboard())

    elif action == "show_cmd_help":
        help_text = (
            "📖 **دليل أوامر الإشراف السريعة للمجموعات:**\n\n"
            "يمكنك استخدام هذه الأوامر مباشرة داخل المجموعة (بالرد على الرسالة):\n\n"
            "• `/حظر` : حظر المستخدم نهائياً.\n"
            "• `/حظر 10m` أو `/حظر 2h` : حظر مؤقت.\n"
            "• `/كتم` أو `/كتم 30m` : كتم المستخدم.\n"
            "• `/الغاء_الحظر` : إزالة الحظر عن المستخدم بالرد عليه.\n"
            "• `/الغاء_الكتم` : السماح للمستخدم بالكتابة مجدداً بالرد عليه."
        )
        await query.message.edit_text(help_text, parse_mode='Markdown', reply_markup=get_back_keyboard())

    elif action == "manage_silent":
        s = bot_data.get("silent_mode", {})
        msg = (
            "🔇 **إعدادات الوضع الصامت:**\n\n"
            f"• التفعيل التلقائي: {'تفعيل' if s.get('enabled') else 'تعطيل'}\n"
            f"• الجدول اليومي: من `{s.get('start_time')}` إلى `{s.get('end_time')}`\n"
            f"• رسالة التنبيه:\n_{s.get('custom_message')}_"
        )
        await query.message.edit_text(msg, parse_mode='Markdown', reply_markup=get_silent_keyboard(bot_data))

    elif action == "toggle_silent":
        bot_data["silent_mode"]["enabled"] = not bot_data["silent_mode"].get("enabled", False)
        save_data(bot_data)
        await query.message.edit_text("تم تغيير حالة الوضع الصامت بنجاح!", reply_markup=get_silent_keyboard(bot_data))

    elif action == "silent_durations":
        await query.message.edit_text("⏱️ **اختر مدة الوضع الصامت المباشر:**", reply_markup=get_durations_keyboard())

    elif action.startswith("dur_"):
        minutes = int(action.replace("dur_", ""))
        until_ts = int((datetime.now() + timedelta(minutes=minutes)).timestamp())
        bot_data["silent_mode"]["until_timestamp"] = until_ts
        bot_data["silent_mode"]["enabled"] = True
        save_data(bot_data)
        
        custom_msg = bot_data["silent_mode"].get("custom_message", "")
        groups = bot_data.get("groups", {})
        for g_id in groups.keys():
            try:
                await context.bot.send_message(chat_id=int(g_id), text=f"🔇 **تم تفعيل الوضع الصامت لمدة {minutes} دقيقة!**\n\n{custom_msg}", parse_mode='Markdown')
            except Exception:
                pass

        await query.message.edit_text(f"✅ تم تفعيل الوضع الصامت لمدة {minutes} دقيقة بنجاح!", reply_markup=get_silent_keyboard(bot_data))

    elif action == "set_silent_schedule":
        WAITING_STATES[user_id] = "set_schedule"
        await query.message.edit_text(
            "⏰ **أرسل وقت البدء والانتهاء للوضع الصامت بالشكل التالي:**\n\n"
            "`22:00-07:00`\n"
            "(مع الأخذ بالاعتبار نظام 24 ساعة)",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("manage_silent")
        )

    elif action == "edit_silent_msg":
        WAITING_STATES[user_id] = "set_silent_msg"
        await query.message.edit_text("✏️ **أرسل الرسالة التوضيحية الجديدة للوضع الصامت:**", reply_markup=get_back_keyboard("manage_silent"))

    elif action == "bc_all":
        WAITING_STATES[user_id] = "bc_msg_all"
        await query.message.edit_text("📝 **أرسل الآن منشور الإذاعة العامة للمجموعات:**", reply_markup=get_back_keyboard())

    elif action == "bc_users":
        WAITING_STATES[user_id] = "bc_msg_users"
        await query.message.edit_text("📝 **أرسل الآن منشور الإذاعة الخاص للمستخدمين:**", reply_markup=get_back_keyboard())

    elif action == "bc_single_select":
        groups = bot_data.get("groups", {})
        if not groups:
            await query.message.edit_text("❌ لا توجد مجموعات مسجلة.", reply_markup=get_back_keyboard())
            return
        keyboard = [[InlineKeyboardButton(f"👥 {g_title}", callback_data=f"bc_to_{g_id}")] for g_id, g_title in groups.items()]
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
        await query.message.edit_text("🎯 **اختر المجموعة المستهدفة:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action.startswith("bc_to_"):
        target_g_id = action.replace("bc_to_", "")
        WAITING_STATES[user_id] = f"bc_msg_single_{target_g_id}"
        await query.message.edit_text("📝 **أرسل منشور الإذاعة للمجموعة المحددة:**", reply_markup=get_back_keyboard())

    elif action == "btn_add_yes":
        WAITING_STATES[user_id] = "wait_for_btn_format"
        guide = (
            "✏️ **أرسل قائمة أزرار الروابط بهذا التنسيق (مع تحديد الألوان اختيارياً):**\n\n"
            "الزر 1 - http://example1.com - style:green\n"
            "الزر 2 - http://example2.com - style:blue\n"
            "الزر 3 - http://example3.com - style:red\n\n"
            "🎨 **الألوان المتاحة:**\n"
            "• `style:green` : لون أخضر 🟢\n"
            "• `style:blue` : لون أزرق 🔵\n"
            "• `style:red` : لون أحمر 🔴\n\n"
            "💡 يمكنك أيضاً وضع أكثر من زر في نفس السطر بوضع الرمز `|` بينهما."
        )
        await query.message.edit_text(guide, parse_mode='Markdown', reply_markup=get_back_keyboard())

    elif action == "btn_add_no":
        await execute_broadcast(context, user_id, query.message, None)

    elif action == "add_admin":
        WAITING_STATES[user_id] = "add_admin"
        await query.message.edit_text("👤 **أرسل ID المشرف الجديد:**", reply_markup=get_back_keyboard())

    elif action == "manage_words":
        words = ", ".join(bot_data.get("words", [])) or "لا توجد"
        keyboard = [
            [InlineKeyboardButton("➕ إضافة كلمة", callback_data="add_word"), InlineKeyboardButton("🗑️ مسح الكل", callback_data="clear_words")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
        await query.message.edit_text(f"⛔ **الكلمات المحظورة:**\n`{words}`", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "add_word":
        WAITING_STATES[user_id] = "add_word"
        await query.message.edit_text("✏️ **أرسل الكلمة المحظورة:**", reply_markup=get_back_keyboard("manage_words"))

    elif action == "clear_words":
        bot_data["words"] = []
        save_data(bot_data)
        await query.message.edit_text("✅ تم تفريغ الكلمات.", reply_markup=get_back_keyboard("manage_words"))

    elif action == "manage_emojis":
        emojis = " ".join(bot_data.get("emojis", [])) or "لا توجد"
        keyboard = [
            [InlineKeyboardButton("➕ إضافة إيموجي", callback_data="add_emoji"), InlineKeyboardButton("🗑️ مسح الكل", callback_data="clear_emojis")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
        await query.message.edit_text(f"😀 **الإيموجيات المحظورة:**\n{emojis}", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "add_emoji":
        WAITING_STATES[user_id] = "add_emoji"
        await query.message.edit_text("✏️ **أرسل الإيموجي المحظور:**", reply_markup=get_back_keyboard("manage_emojis"))

    elif action == "clear_emojis":
        bot_data["emojis"] = []
        save_data(bot_data)
        await query.message.edit_text("✅ تم تفريغ الإيموجيات.", reply_markup=get_back_keyboard("manage_emojis"))

# === 6. تنفيذ عملية الإذاعة ===
async def execute_broadcast(context: ContextTypes.DEFAULT_TYPE, user_id: int, status_msg, reply_markup):
    data_bc = TEMP_BROADCAST.get(user_id)
    if not data_bc:
        await status_msg.edit_text("❌ حدث خطأ، أعد المحاولة.", reply_markup=get_back_keyboard())
        return

    bc_type = data_bc["type"]
    msg_to_copy = data_bc["msg"]
    bot_data = load_data()

    sent, failed = 0, 0
    await status_msg.edit_text("⏳ جاري الإذاعة...")

    targets = []
    if bc_type == "all":
        targets = list(bot_data.get("groups", {}).keys())
    elif bc_type == "users":
        targets = bot_data.get("users", [])
    elif bc_type.startswith("single_"):
        targets = [bc_type.replace("single_", "")]

    for target_id in targets:
        try:
            await msg_to_copy.copy(chat_id=int(target_id), reply_markup=reply_markup)
            sent += 1
            await asyncio.sleep(0.1)
        except Exception:
            failed += 1

    TEMP_BROADCAST.pop(user_id, None)
    WAITING_STATES[user_id] = None
    await status_msg.edit_text(f"✅ تمت الإذاعة بنجاح!\n- نجاح: {sent}\n- فشل: {failed}", reply_markup=get_back_keyboard())

# === 7. معالجة الرسائل الخاصة بأدمن البوت ===
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user_id = update.message.from_user.id
    register_user(user_id)

    if not is_bot_admin(user_id):
        return

    state = WAITING_STATES.get(user_id)
    if not state:
        await update.message.reply_text("يرجى استخدام الأوامر عبر القائمة من /start", reply_markup=get_main_admin_keyboard())
        return

    bot_data = load_data()

    if state.startswith("bc_msg_"):
        bc_type = state.replace("bc_msg_", "")
        TEMP_BROADCAST[user_id] = {
            "type": bc_type,
            "msg": update.message
        }
        WAITING_STATES[user_id] = "ask_buttons"
        await update.message.reply_text("🔗 **هل ترغب بإضافة أزرار روابط للمنشور؟**", reply_markup=get_buttons_decision_keyboard())

    elif state == "wait_for_btn_format":
        btn_markup = parse_button_markup(update.message.text)
        if not btn_markup:
            await update.message.reply_text("❌ التنسيق غير صحيح! التأكد من إرسال رابط صحيح (https://...)\nحاول مرة أخرى:")
            return
        status_msg = await update.message.reply_text("⏳ جاري تحضير الإذاعة...")
        await execute_broadcast(context, user_id, status_msg, btn_markup)

    elif state == "set_schedule":
        WAITING_STATES[user_id] = None
        txt = update.message.text.strip()
        if re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", txt):
            start, end = txt.split("-")
            bot_data["silent_mode"]["start_time"] = start
            bot_data["silent_mode"]["end_time"] = end
            bot_data["silent_mode"]["enabled"] = True
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم ضبط جدول الوضع الصامت من `{start}` إلى `{end}`!", parse_mode='Markdown', reply_markup=get_back_keyboard("manage_silent"))
        else:
            await update.message.reply_text("❌ صيغة غير صحيحة، اكتبها مثل: `22:00-07:00`", parse_mode='Markdown', reply_markup=get_back_keyboard("manage_silent"))

    elif state == "set_silent_msg":
        WAITING_STATES[user_id] = None
        bot_data["silent_mode"]["custom_message"] = update.message.text
        save_data(bot_data)
        await update.message.reply_text("✅ تم حفظ الوصف الجديد للوضع الصامت!", reply_markup=get_back_keyboard("manage_silent"))

    elif state == "add_admin":
        WAITING_STATES[user_id] = None
        try:
            new_id = int(update.message.text.strip())
            if new_id not in bot_data["admins"]:
                bot_data["admins"].append(new_id)
                save_data(bot_data)
                await update.message.reply_text(f"✅ تم إضافة المشرف `{new_id}`!", parse_mode='Markdown', reply_markup=get_back_keyboard())
            else:
                await update.message.reply_text("⚠️ هذا المستخدم مشرف بالفعل.", reply_markup=get_back_keyboard())
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال أرقام الـ ID فقط.", reply_markup=get_back_keyboard())

    elif state == "add_word":
        WAITING_STATES[user_id] = None
        word = update.message.text.strip().lower()
        if word not in bot_data["words"]:
            bot_data["words"].append(word)
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم إضافة الكلمة `{word}`!", parse_mode='Markdown', reply_markup=get_back_keyboard("manage_words"))

    elif state == "add_emoji":
        WAITING_STATES[user_id] = None
        emoji = update.message.text.strip()
        if emoji not in bot_data["emojis"]:
            bot_data["emojis"].append(emoji)
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم إضافة الإيموجي {emoji}!", reply_markup=get_back_keyboard("manage_emojis"))

# === 8. أوامر الإشراف السريعة للمجموعات ===
def parse_time(time_str):
    unit = time_str[-1].lower()
    value = int(time_str[:-1])
    if unit == 'm':
        return timedelta(minutes=value)
    elif unit == 'h':
        return timedelta(hours=value)
    elif unit == 'd':
        return timedelta(days=value)
    return None

async def admin_actions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type == 'private':
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not await is_group_admin(context, chat_id, user_id):
        return

    text = update.message.text.strip()
    cmd = text.split()[0].lower()

    target_user_id = None
    target_user_name = "المستخدم"

    if update.message.reply_to_message:
        target_user_id = update.message.reply_to_message.from_user.id
        target_user_name = update.message.reply_to_message.from_user.first_name

    if cmd in ["/حظر", "/ban"]:
        if not target_user_id:
            await update.message.reply_text("❌ يرجى الرد على رسالة المستخدم للقيام بالحظر.")
            return

        duration = None
        if len(context.args) > 0 and not context.args[0].startswith("@"):
            try:
                duration = parse_time(context.args[0])
            except Exception:
                pass

        try:
            until_date = datetime.now() + duration if duration else None
            await context.bot.ban_chat_member(chat_id, target_user_id, until_date=until_date)
            dur_text = f" لمدة {context.args[0]}" if duration else " نهائياً"
            await update.message.reply_text(f"🚫 تم حظر {target_user_name}{dur_text}.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل الحظر: {e}")

    elif cmd in ["/كتم", "/mute"]:
        if not target_user_id:
            await update.message.reply_text("❌ يرجى الرد على رسالة المستخدم للقيام بالكتم.")
            return

        duration = None
        if len(context.args) > 0 and not context.args[0].startswith("@"):
            try:
                duration = parse_time(context.args[0])
            except Exception:
                pass

        try:
            until_date = datetime.now() + duration if duration else None
            permissions = ChatPermissions(can_send_messages=False)
            await context.bot.restrict_chat_member(chat_id, target_user_id, permissions=permissions, until_date=until_date)
            dur_text = f" لمدة {context.args[0]}" if duration else " نهائياً"
            await update.message.reply_text(f"🔇 تم كتم {target_user_name}{dur_text}.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل الكتم: {e}")

    elif cmd in ["/الغاء_الحظر", "/unban"]:
        if not target_user_id:
            await update.message.reply_text("❌ يرجى الرد على رسالة المستخدم لإلغاء الحظر.")
            return

        try:
            await context.bot.unban_chat_member(chat_id, target_user_id)
            await update.message.reply_text(f"✅ تم إلغاء حظر {target_user_name}.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل إلغاء الحظر: {e}")

    elif cmd in ["/الغاء_الكتم", "/unmute"]:
        if not target_user_id:
            await update.message.reply_text("❌ يرجى الرد على رسالة المستخدم لإلغاء الكتم.")
            return

        try:
            permissions = ChatPermissions(can_send_messages=True, can_send_other_messages=True)
            await context.bot.restrict_chat_member(chat_id, target_user_id, permissions=permissions)
            await update.message.reply_text(f"🔊 تم إلغاء كتم {target_user_name}.")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل إلغاء الكتم: {e}")

# === 9. فلتر المجموعات ===
def is_silent_active(bot_data):
    s = bot_data.get("silent_mode", {})
    if not s.get("enabled"):
        return False

    now = datetime.now()

    until_ts = s.get("until_timestamp", 0)
    if until_ts > 0:
        if now.timestamp() < until_ts:
            return True
        else:
            bot_data["silent_mode"]["until_timestamp"] = 0
            save_data(bot_data)

    start_str = s.get("start_time", "22:00")
    end_str = s.get("end_time", "07:00")

    try:
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))

        start_time = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end_time = now.replace(hour=eh, minute=em, second=0, microsecond=0)

        if start_time < end_time:
            return start_time <= now <= end_time
        else:
            return now >= start_time or now <= end_time
    except Exception:
        return False

async def group_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return

    msg = update.message

    # استثناء منشورات القناة المربوطة
    if msg.is_automatic_forward or (msg.sender_chat and msg.sender_chat.type == "channel"):
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else None
    register_group(chat_id, update.effective_chat.title or "مجموعة")

    bot_data = load_data()

    # فحص الوضع الصامت
    if is_silent_active(bot_data):
        if user_id and not await is_group_admin(context, chat_id, user_id):
            try:
                await msg.delete()
                custom_msg = bot_data["silent_mode"].get("custom_message", "🔇 الوضع الصامت مفعل حالياً!")
                first_name = update.effective_user.first_name if update.effective_user else "العضو"
                warn = await context.bot.send_message(chat_id=chat_id, text=f"عذراً يا {first_name}:\n{custom_msg}")
                await asyncio.sleep(5)
                await warn.delete()
            except Exception:
                pass
            return

    # فحص الكلمات والإيموجي الممنوعة
    text = msg.text or msg.caption
    if not text:
        return

    text_lower = text.lower()
    first_name = update.effective_user.first_name if update.effective_user else "المستخدم"

    forbidden_emojis = bot_data.get("emojis", [])
    forbidden_words = bot_data.get("words", [])

    is_violating = False
    reason = ""

    for emoji in forbidden_emojis:
        if emoji in text:
            is_violating = True
            reason = "إيموجي ممنوع"
            break

    if not is_violating:
        for word in forbidden_words:
            if word in text_lower:
                is_violating = True
                reason = "كلمة محظورة"
                break

    if is_violating:
        try:
            await msg.delete()
            warning_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"عذراً يا {first_name}، يمنع استخدام ({reason}) في المجموعة!"
            )
            await asyncio.sleep(5)
            await warning_msg.delete()
        except Exception:
            pass

# === 10. تشغيل البوت ===
async def main():
    if not BOT_TOKEN:
        print("خطأ: لم يتم ضبط BOT_TOKEN!")
        return

    await start_web_server()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (~filters.COMMAND), handle_private_message))

    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.Regex(r"^/(حظر|كتم|الغاء_الحظر|الغاء_الكتم|ban|mute|unban|unmute)"), admin_actions_handler))

    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_filter))

    print("البوت يعمل بنجاح...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
