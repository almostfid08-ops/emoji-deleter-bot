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

# حالات الانتظار الخاصة بلوحة تحكم المشرفين
WAITING_STATES = {}
TEMP_BROADCAST = {}

# تتبّع وقتي (غير محفوظ) لنداءات الاستغاثة داخل كل مجموعة
# {chat_id: {"count": int, "callers": [names]}}
RESCUE_TRACK = {}

# تتبّع وقتي (غير محفوظ) لعدد الرسائل العشوائية المرسلة من غير المشرفين في الخاص
# {user_id: int}
PRIVATE_MSG_TRACK = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# === نصوص ثابتة ===
DEVELOPER_USERNAME = "@Nabil1r"

NON_ADMIN_INFO_TEXT = (
    "🤖 **مرحباً بك!**\n\n"
    "هذا البوت مُصمَّم ليعمل كـ**مشرف داخل مجموعات الدراسة**، ومهامه:\n\n"
    "• 🚫 حذف الإيموجيات غير المرغوب بها.\n"
    "• 🚫 حذف الكلمات والروابط الممنوعة.\n"
    "• 🚫 حذف الملصقات (الستيكرز) والصور المتحركة GIF الممنوعة.\n"
    "• 🔇 تفعيل وضع صامت في أوقات محددة، أو تلقائياً عند نداء استغاثة من الأعضاء.\n"
    "• 🛡️ الحفاظ على الجو الدراسي الهادئ داخل المجموعة بشكل عام.\n\n"
    f"للحصول على هذه الميزات داخل مجموعتك، يرجى التواصل مع مطور البوت {DEVELOPER_USERNAME} "
    "كي يقوم بإضافتك كمشرف للبوت والسماح لك باستخدام ميزاته."
)

NON_ADMIN_SPAM_TEXT = (
    "⚠️ **تنبيه:**\n"
    "هذا البوت غير مبرمج لاستقبال الرسائل، ورسائلك هذه **لا تصل إلى المطور نهائياً**، "
    "لذلك لن تحصل هنا على أي رد رسمي.\n\n"
    f"في حال احتجت لأي خدمة أو أردت تفعيل البوت داخل مجموعتك، يرجى التواصل مباشرة مع المطور: {DEVELOPER_USERNAME}\n\n"
    "شكراً لتفهمك 🙏"
)

FORCED_SUB_MSG = (
    "⚠️ **تنبيه: الاشتراك الإجباري**\n\n"
    "يجب عليك الاشتراك في القناة المحددة لتتمكن من المشاركة في هذه المجموعة.\n\n"
    "🔔 **الرجاء الاشتراك أولاً ثم الضغط على زر التحقق.**"
)

# === 1. إدارة قاعدة البيانات ===
def default_data():
    return {
        "groups": {},
        "users": [],
        "admins": list(INITIAL_ADMINS),
        "emojis": ["😂", "🤣", "💩"],
        "words": [],
        "banned_links": [],
        "block_all_links": False,
        "block_stickers": False,
        "block_animated_stickers": False,
        "silent_mode": {
            "enabled": False,
            "start_time": "22:00",
            "end_time": "07:00",
            "until_timestamp": 0,
            "custom_message": "🔇 المجموعة الآن في الوضع الصامت. الكتابة مقتصرة على المشرفين فقط.",
            "target_group": None
        },
        "rescue_mode": {
            "enabled": False,
            "target_group": None,
            "keyword": "بوت مراقبة",
            "threshold": 3,
            "duration_minutes": 30,
            "message": "⚠️ نداء الاستغاثة يدل على وجود مخالفة أو نزاع داخل المجموعة، الرجاء الانتظار حتى وصول المشرفين."
        },
        "forced_subscription": {
            "groups": {}
        }
    }


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

                for admin in INITIAL_ADMINS:
                    if admin not in data.get("admins", []):
                        data.setdefault("admins", []).append(admin)

                data.setdefault("users", [])
                data.setdefault("groups", {})
                data.setdefault("emojis", ["😂", "🤣", "💩"])
                data.setdefault("words", [])
                data.setdefault("banned_links", [])
                data.setdefault("block_all_links", False)
                data.setdefault("block_stickers", False)
                data.setdefault("block_animated_stickers", False)

                data.setdefault("silent_mode", {})
                data["silent_mode"].setdefault("enabled", False)
                data["silent_mode"].setdefault("start_time", "22:00")
                data["silent_mode"].setdefault("end_time", "07:00")
                data["silent_mode"].setdefault("until_timestamp", 0)
                data["silent_mode"].setdefault(
                    "custom_message",
                    "🔇 المجموعة الآن في الوضع الصامت. الكتابة مقتصرة على المشرفين فقط."
                )
                data["silent_mode"].setdefault("target_group", None)

                data.setdefault("rescue_mode", {})
                data["rescue_mode"].setdefault("enabled", False)
                data["rescue_mode"].setdefault("target_group", None)
                data["rescue_mode"].setdefault("keyword", "بوت مراقبة")
                data["rescue_mode"].setdefault("threshold", 3)
                data["rescue_mode"].setdefault("duration_minutes", 30)
                data["rescue_mode"].setdefault(
                    "message",
                    "⚠️ نداء الاستغاثة يدل على وجود مخالفة أو نزاع داخل المجموعة، الرجاء الانتظار حتى وصول المشرفين."
                )
                
                data.setdefault("forced_subscription", {})
                data["forced_subscription"].setdefault("groups", {})
                
                return data
        except Exception:
            pass
    return default_data()


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

            btn_style = None
            style_match = re.search(r'(?:-\s*style:|\[)(green|blue|red)(?:\])?', part_str, re.IGNORECASE)
            if style_match:
                color_name = style_match.group(1).lower()
                btn_style = style_map.get(color_name)
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
        [InlineKeyboardButton("🆘 نداء الاستغاثة", callback_data="manage_rescue")],
        [InlineKeyboardButton("📖 دليل أوامر الإشراف", callback_data="show_cmd_help")],
        [InlineKeyboardButton("📢 الاشتراك الإجباري", callback_data="manage_forced_sub")],
        [InlineKeyboardButton("👤 إضافة مشرف جديد", callback_data="add_admin")],
        [InlineKeyboardButton("⛔ الكلمات المحظورة", callback_data="manage_words"),
         InlineKeyboardButton("😀 الإيموجيات المحظورة", callback_data="manage_emojis")],
        [InlineKeyboardButton("🔗 الروابط المحظورة", callback_data="manage_links"),
         InlineKeyboardButton("🎭 الملصقات المتحركة", callback_data="manage_stickers")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_silent_keyboard(data):
    silent_info = data.get("silent_mode", {})
    status = "🟢 مفعل" if silent_info.get("enabled") else "🔴 معطل"
    target = silent_info.get("target_group")
    target_name = data.get("groups", {}).get(str(target), "لم يتم التحديد ❌") if target else "لم يتم التحديد ❌"

    keyboard = [
        [InlineKeyboardButton(f"الحالة الحالية: {status}", callback_data="toggle_silent")],
        [InlineKeyboardButton(f"🎯 المجموعة المستهدفة: {target_name}", callback_data="silent_select_target")],
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


def get_groups_selection_keyboard(bot_data, callback_prefix, back_target="main_menu"):
    """لوحة اختيار مجموعة من بين المجموعات المسجلة، تُستخدم لتحديد مجموعة مستهدفة."""
    groups = bot_data.get("groups", {})
    keyboard = []
    for g_id, g_title in groups.items():
        keyboard.append([InlineKeyboardButton(f"👥 {g_title}", callback_data=f"{callback_prefix}{g_id}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data=back_target)])
    return InlineKeyboardMarkup(keyboard)


def get_rescue_keyboard(data):
    r = data.get("rescue_mode", {})
    status = "🟢 مفعل" if r.get("enabled") else "🔴 معطل"
    target = r.get("target_group")
    target_name = data.get("groups", {}).get(str(target), "لم يتم التحديد ❌") if target else "لم يتم التحديد ❌"

    keyboard = [
        [InlineKeyboardButton(f"الحالة الحالية: {status}", callback_data="rescue_toggle")],
        [InlineKeyboardButton(f"🎯 المجموعة المستهدفة: {target_name}", callback_data="rescue_select_target")],
        [InlineKeyboardButton(f"🗣️ كلمة النداء: {r.get('keyword')}", callback_data="rescue_set_keyword")],
        [InlineKeyboardButton(f"🔢 عدد النداءات: {r.get('threshold')}", callback_data="rescue_set_threshold")],
        [InlineKeyboardButton(f"⏱️ مدة الصمت: {r.get('duration_minutes')} دقيقة", callback_data="rescue_set_duration")],
        [InlineKeyboardButton("✏️ تعديل رسالة النداء", callback_data="rescue_set_message")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_links_keyboard(data):
    status = "🟢 مفعل" if data.get("block_all_links") else "🔴 معطل"
    keyboard = [
        [InlineKeyboardButton(f"حظر جميع الروابط: {status}", callback_data="toggle_block_all_links")],
        [InlineKeyboardButton("➕ إضافة رابط/نطاق محظور", callback_data="add_link"),
         InlineKeyboardButton("🗑️ مسح الكل", callback_data="clear_links")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_stickers_keyboard(data):
    status_all = "🟢 مفعل" if data.get("block_stickers") else "🔴 معطل"
    status_anim = "🟢 مفعل" if data.get("block_animated_stickers") else "🔴 معطل"
    keyboard = [
        [InlineKeyboardButton(f"حظر جميع الملصقات: {status_all}", callback_data="toggle_block_stickers")],
        [InlineKeyboardButton(f"حظر الملصقات المتحركة و GIF: {status_anim}", callback_data="toggle_block_animated")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_forced_sub_keyboard(channel_id=None, group_id=None):
    """إنشاء لوحة مفاتيح للاشتراك الإجباري"""
    keyboard = []
    
    if channel_id:
        try:
            channel_link = f"https://t.me/{channel_id}" if isinstance(channel_id, int) else f"https://t.me/{channel_id}"
            keyboard.append([InlineKeyboardButton("📢 الانضمام إلى القناة", url=channel_link)])
        except:
            pass
    
    keyboard.append([InlineKeyboardButton("✅ تحقق مرة أخرى", callback_data=f"check_sub_{group_id}")])
    return InlineKeyboardMarkup(keyboard)


def get_forced_sub_admin_keyboard():
    """لوحة إدارة الاشتراك الإجباري للمشرفين"""
    keyboard = [
        [InlineKeyboardButton("🔘 تفعيل/تعطيل الاشتراك", callback_data="fs_toggle")],
        [InlineKeyboardButton("🎯 اختيار المجموعة المستهدفة", callback_data="fs_select_group")],
        [InlineKeyboardButton("📢 اختيار القناة (إعادة توجيه)", callback_data="fs_select_channel")],
        [InlineKeyboardButton("✏️ تعديل رسالة الاشتراك", callback_data="fs_edit_message")],
        [InlineKeyboardButton("📊 عرض الإعدادات الحالية", callback_data="fs_view_settings")],
        [InlineKeyboardButton("🗑️ حذف إعدادات المجموعة", callback_data="fs_delete_settings")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def send_non_admin_response(update: Update):
    """رسالة تعريفية لغير المشرفين في أول رسالة، ثم رسالة توضح أن الرسائل لا تصل للمطور بعد ذلك."""
    user_id = update.effective_user.id
    count = PRIVATE_MSG_TRACK.get(user_id, 0) + 1
    PRIVATE_MSG_TRACK[user_id] = count

    if count <= 1:
        await update.message.reply_text(NON_ADMIN_INFO_TEXT, parse_mode='Markdown')
    else:
        await update.message.reply_text(NON_ADMIN_SPAM_TEXT, parse_mode='Markdown')


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
        await send_non_admin_response(update)


async def check_user_subscription(context: ContextTypes.DEFAULT_TYPE, user_id: int, channel_id: int) -> bool:
    """التحقق من اشتراك المستخدم في القناة"""
    try:
        member = await context.bot.get_chat_member(channel_id, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.error(f"خطأ في التحقق من الاشتراك: {e}")
        return False


def update_group_forced_sub_settings(group_id: str, channel_id: int = None, enabled: bool = None, custom_message: str = None):
    """تحديث إعدادات الاشتراك الإجباري لمجموعة معينة"""
    bot_data = load_data()
    
    if "forced_subscription" not in bot_data:
        bot_data["forced_subscription"] = {"groups": {}}
    
    if "groups" not in bot_data["forced_subscription"]:
        bot_data["forced_subscription"]["groups"] = {}
    
    group_id_str = str(group_id)
    
    if group_id_str not in bot_data["forced_subscription"]["groups"]:
        bot_data["forced_subscription"]["groups"][group_id_str] = {
            "channel_id": None,
            "enabled": False,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "custom_message": FORCED_SUB_MSG
        }
    
    settings = bot_data["forced_subscription"]["groups"][group_id_str]
    
    if channel_id is not None:
        settings["channel_id"] = channel_id
    if enabled is not None:
        settings["enabled"] = enabled
    if custom_message is not None:
        settings["custom_message"] = custom_message
    
    settings["updated_at"] = datetime.now().isoformat()
    
    save_data(bot_data)


# === 5. التحكم في الأزرار ===
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    action = query.data
    await query.answer()

    # زر إلغاء الوضع الصامت يظهر داخل المجموعة، ومتاح لمشرفي المجموعة (وليس فقط أدمن البوت)
    if action == "cancel_silent":
        chat_id = query.message.chat.id
        if not await is_group_admin(context, chat_id, user_id):
            await context.bot.answer_callback_query(query.id, "❌ هذا الزر مخصص لمشرفي المجموعة فقط.", show_alert=True)
            return
        bot_data = load_data()
        bot_data["silent_mode"]["until_timestamp"] = 0
        save_data(bot_data)
        try:
            await query.message.edit_text("✅ تم إلغاء الوضع الصامت، يمكن للأعضاء الكتابة الآن.")
        except Exception:
            await context.bot.send_message(chat_id, "✅ تم إلغاء الوضع الصامت، يمكن للأعضاء الكتابة الآن.")
        return

    if not is_bot_admin(user_id):
        await query.message.reply_text("❌ عذراً، هذه اللوحة مخصصة للمشرفين فقط.")
        return

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
            "• `/الغاء_الكتم` : السماح للمستخدم بالكتابة مجدداً بالرد عليه.\n\n"
            "🆘 **نداء الاستغاثة:** إذا كتب الأعضاء كلمة النداء المحددة عدداً من المرات المتتالية "
            "داخل المجموعة المستهدفة، يتم تفعيل الوضع الصامت تلقائياً."
        )
        await query.message.edit_text(help_text, parse_mode='Markdown', reply_markup=get_back_keyboard())

    # ---------- الوضع الصامت ----------
    elif action == "manage_silent":
        s = bot_data.get("silent_mode", {})
        target = s.get("target_group")
        target_name = bot_data.get("groups", {}).get(str(target), "غير محددة ❌") if target else "غير محددة ❌"
        msg = (
            "🔇 **إعدادات الوضع الصامت:**\n\n"
            f"• المجموعة المستهدفة: {target_name}\n"
            f"• التفعيل التلقائي: {'تفعيل' if s.get('enabled') else 'تعطيل'}\n"
            f"• الجدول اليومي: من `{s.get('start_time')}` إلى `{s.get('end_time')}`\n"
            f"• رسالة التنبيه:\n_{s.get('custom_message')}_"
        )
        await query.message.edit_text(msg, parse_mode='Markdown', reply_markup=get_silent_keyboard(bot_data))

    elif action == "silent_select_target":
        if not bot_data.get("groups"):
            await query.message.edit_text("❌ لا توجد مجموعات مسجلة بعد.", reply_markup=get_back_keyboard("manage_silent"))
            return
        await query.message.edit_text(
            "🎯 **اختر المجموعة التي سيُطبَّق عليها الوضع الصامت:**",
            parse_mode='Markdown',
            reply_markup=get_groups_selection_keyboard(bot_data, "silent_target_", "manage_silent")
        )

    elif action.startswith("silent_target_"):
        target_id = action.replace("silent_target_", "")
        bot_data["silent_mode"]["target_group"] = target_id
        save_data(bot_data)
        await query.message.edit_text("✅ تم تحديد المجموعة المستهدفة للوضع الصامت بنجاح.", reply_markup=get_silent_keyboard(bot_data))

    elif action == "toggle_silent":
        if not bot_data["silent_mode"].get("target_group"):
            await query.message.edit_text(
                "❌ يجب تحديد المجموعة المستهدفة أولاً قبل تفعيل الوضع الصامت.",
                reply_markup=get_silent_keyboard(bot_data)
            )
            return
        bot_data["silent_mode"]["enabled"] = not bot_data["silent_mode"].get("enabled", False)
        save_data(bot_data)
        await query.message.edit_text("تم تغيير حالة الوضع الصامت بنجاح!", reply_markup=get_silent_keyboard(bot_data))

    elif action == "silent_durations":
        if not bot_data["silent_mode"].get("target_group"):
            await query.message.edit_text(
                "❌ يجب تحديد المجموعة المستهدفة أولاً.",
                reply_markup=get_silent_keyboard(bot_data)
            )
            return
        await query.message.edit_text("⏱️ **اختر مدة الوضع الصامت المباشر:**", reply_markup=get_durations_keyboard())

    elif action.startswith("dur_"):
        target = bot_data["silent_mode"].get("target_group")
        if not target:
            await query.message.edit_text(
                "❌ يجب تحديد المجموعة المستهدفة أولاً.",
                reply_markup=get_silent_keyboard(bot_data)
            )
            return

        minutes = int(action.replace("dur_", ""))
        until_ts = int((datetime.now() + timedelta(minutes=minutes)).timestamp())
        bot_data["silent_mode"]["until_timestamp"] = until_ts
        bot_data["silent_mode"]["enabled"] = True
        save_data(bot_data)

        custom_msg = bot_data["silent_mode"].get("custom_message", "")
        try:
            await context.bot.send_message(
                chat_id=int(target),
                text=f"🔇 **تم تفعيل الوضع الصامت لمدة {minutes} دقيقة!**\n\n{custom_msg}",
                parse_mode='Markdown'
            )
        except Exception:
            pass

        await query.message.edit_text(f"✅ تم تفعيل الوضع الصامت لمدة {minutes} دقيقة بنجاح!", reply_markup=get_silent_keyboard(bot_data))

    elif action == "set_silent_schedule":
        if not bot_data["silent_mode"].get("target_group"):
            await query.message.edit_text(
                "❌ يجب تحديد المجموعة المستهدفة أولاً.",
                reply_markup=get_silent_keyboard(bot_data)
            )
            return
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

    # ---------- نداء الاستغاثة ----------
    elif action == "manage_rescue":
        r = bot_data.get("rescue_mode", {})
        target = r.get("target_group")
        target_name = bot_data.get("groups", {}).get(str(target), "غير محددة ❌") if target else "غير محددة ❌"
        msg = (
            "🆘 **إعدادات نداء الاستغاثة:**\n\n"
            f"• المجموعة المستهدفة: {target_name}\n"
            f"• الحالة: {'مفعل' if r.get('enabled') else 'معطل'}\n"
            f"• كلمة النداء: `{r.get('keyword')}`\n"
            f"• عدد النداءات المطلوبة: {r.get('threshold')}\n"
            f"• مدة الصمت التلقائي: {r.get('duration_minutes')} دقيقة\n"
            f"• رسالة النداء:\n_{r.get('message')}_\n\n"
            "عند تكرار كلمة النداء من الأعضاء بالعدد المحدد، يتم تفعيل الوضع الصامت تلقائياً في المجموعة المستهدفة."
        )
        await query.message.edit_text(msg, parse_mode='Markdown', reply_markup=get_rescue_keyboard(bot_data))

    elif action == "rescue_select_target":
        if not bot_data.get("groups"):
            await query.message.edit_text("❌ لا توجد مجموعات مسجلة بعد.", reply_markup=get_back_keyboard("manage_rescue"))
            return
        await query.message.edit_text(
            "🎯 **اختر المجموعة التي سيعمل بها نداء الاستغاثة:**",
            parse_mode='Markdown',
            reply_markup=get_groups_selection_keyboard(bot_data, "rescue_target_", "manage_rescue")
        )

    elif action.startswith("rescue_target_"):
        target_id = action.replace("rescue_target_", "")
        bot_data["rescue_mode"]["target_group"] = target_id
        RESCUE_TRACK.pop(int(target_id), None)
        save_data(bot_data)
        await query.message.edit_text("✅ تم تحديد المجموعة المستهدفة لنداء الاستغاثة بنجاح.", reply_markup=get_rescue_keyboard(bot_data))

    elif action == "rescue_toggle":
        if not bot_data["rescue_mode"].get("target_group"):
            await query.message.edit_text(
                "❌ يجب تحديد المجموعة المستهدفة أولاً قبل التفعيل.",
                reply_markup=get_rescue_keyboard(bot_data)
            )
            return
        bot_data["rescue_mode"]["enabled"] = not bot_data["rescue_mode"].get("enabled", False)
        save_data(bot_data)
        await query.message.edit_text("تم تغيير حالة نداء الاستغاثة بنجاح!", reply_markup=get_rescue_keyboard(bot_data))

    elif action == "rescue_set_keyword":
        WAITING_STATES[user_id] = "rescue_set_keyword"
        await query.message.edit_text("🗣️ **أرسل كلمة أو عبارة النداء الجديدة:**", reply_markup=get_back_keyboard("manage_rescue"))

    elif action == "rescue_set_threshold":
        WAITING_STATES[user_id] = "rescue_set_threshold"
        await query.message.edit_text("🔢 **أرسل عدد مرات النداء المطلوبة لتفعيل الوضع الصامت (رقم):**", reply_markup=get_back_keyboard("manage_rescue"))

    elif action == "rescue_set_duration":
        WAITING_STATES[user_id] = "rescue_set_duration"
        await query.message.edit_text("⏱️ **أرسل مدة الوضع الصامت بالدقائق (رقم):**", reply_markup=get_back_keyboard("manage_rescue"))

    elif action == "rescue_set_message":
        WAITING_STATES[user_id] = "rescue_set_message"
        await query.message.edit_text("✏️ **أرسل رسالة النداء الجديدة التي تظهر عند تفعيل الوضع الصامت تلقائياً:**", reply_markup=get_back_keyboard("manage_rescue"))

    # ---------- الإذاعات ----------
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
        await query.message.edit_text("🎯 **اختر المجموعة المستهدفة:**", reply_markup=
