import os
import logging
import asyncio
import json
import re
import html
from datetime import datetime, timedelta
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions, ChatMember
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ChatMemberHandler
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

# تتبّع وقتي (غير محفوظ) لعملية كتم مستخدم قيد الإعداد من طرف أدمن معيّن
# {admin_id: {"target_user_id":, "target_user_name":, "chat_id":}}
TEMP_MUTE = {}

# القائمة الافتراضية لكلمات/عبارات نظام "مراقبة الكلمات" (يمكن للأدمن إضافة/حذف كلمات لاحقاً)
DEFAULT_WATCH_WORDS = [
    "جروب", "قروب", "ڨروب", "غروب", "group", "Groupe", "گروب",
    "الجروب", "القروب", "مجموعة", "المجموعة", "جروبنا", "قروبنا",
    "المجموعة تاعنا", "الجروب تاعنا", "groupe ta3na", "group ta3na",
    "groupe privé", "groupe du bac", "rejoindre le groupe"
]

# المدد الافتراضية الجاهزة لنظام الكتم (بالثواني) — لا يمكن حذفها من لوحة الإدارة
DEFAULT_MUTE_DURATIONS = [
    ("15 دقيقة", 15 * 60),
    ("20 دقيقة", 20 * 60),
    ("30 دقيقة", 30 * 60),
    ("60 دقيقة", 60 * 60),
    ("ساعة ونصف", 90 * 60),
    ("ساعتان", 120 * 60),
    ("ساعتان ونصف", 150 * 60),
    ("3 ساعات", 180 * 60),
    ("3 ساعات ونصف", 210 * 60),
    ("4 ساعات ونصف", 270 * 60),
    ("يوم", 24 * 60 * 60),
    ("يومان", 2 * 24 * 60 * 60),
    ("3 أيام", 3 * 24 * 60 * 60),
    ("4 أيام", 4 * 24 * 60 * 60),
]

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# === نصوص ثابتة ===
DEVELOPER_USERNAME = "@Nabil1r"

# النص المحدّث والشامل لـ /start
UPDATED_START_TEXT = (
    "🤖 **مرحباً بك في بوت المشرف الذكي!**\n\n"
    "هذا البوت مُصمَّم ليعمل كـ**مشرف متكامل داخل مجموعات الدراسة والمجموعات التعليمية**، "
    "ويوفر لك مجموعة واسعة من الميزات للحفاظ على بيئة هادئة ومنظمة.\n\n"
    "✨ **الميزات الرئيسية:**\n\n"
    "**🛡️ الحماية والمراقبة:**\n"
    "• 🚫 حذف الإيموجيات غير المرغوب بها تلقائياً.\n"
    "• 🚫 حذف الكلمات والعبارات الممنوعة.\n"
    "• 🚫 حذف الروابط المحظورة أو حظر جميع الروابط.\n"
    "• 🚫 حظر الملصقات (الستيكرز) والصور المتحركة GIF.\n\n"
    "**🔇 إدارة الوضع الصامت:**\n"
    "• وضع صامت يومي في أوقات محددة (ليلاً مثلاً).\n"
    "• وضع صامت مؤقت لفترة زمنية محددة.\n"
    "• تفعيل الوضع الصامت تلقائياً عند نداء استغاثة من الأعضاء.\n\n"
    "**📢 أنظمة متقدمة:**\n"
    "• 📢 **الاشتراك الإجباري:** فرض الاشتراك في قناة محددة قبل السماح بالكتابة.\n"
    "• 🔎 **مراقبة الكلمات:** رصد الكلمات المحددة وتنبيه المشرفين.\n"
    "• 🔇 **كتم المستخدمين:** كتم أعضاء مزعجين لفترات محددة.\n"
    "• 📣 **الإذاعة:** إرسال رسائل جماعية للمجموعات أو المستخدمين.\n\n"
    "**👤 صلاحيات المشرفين:**\n"
    "• لوحة تحكم كاملة عبر الأزرار.\n"
    "• إضافة مشرفين جدد للبوت.\n"
    "• ضبط جميع الإعدادات بمرونة.\n\n"
    "📖 **للاستخدام:**\n"
    "• استخدم الأمر `/admin` لعرض لوحة التحكم (للمشرفين فقط).\n"
    "• استخدم الأمر `/start` لعرض هذه الرسالة.\n\n"
    f"📞 **للتواصل مع المطور:** {DEVELOPER_USERNAME}\n\n"
    "شكراً لاستخدامك البوت 🙏"
)

NON_ADMIN_SPAM_TEXT = (
    "⚠️ **تنبيه:**\n"
    "هذا البوت غير مبرمج لاستقبال الرسائل، ورسائلك هذه **لا تصل إلى المطور نهائياً**، "
    "لذلك لن تحصل هنا على أي رد رسمي.\n\n"
    f"في حال احتجت لأي خدمة أو أردت تفعيل البوت داخل مجموعتك، يرجى التواصل مباشرة مع المطور: {DEVELOPER_USERNAME}\n\n"
    "شكراً لتفهمك 🙏"
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
            "target_group": None,
            "is_temporary": False,
            "temp_until_timestamp": 0
        },
        "daily_silent_mode": {
            "enabled": False,
            "start_time": "23:00",
            "end_time": "07:00",
            "target_group": None,
            "custom_message": "🔇 المجموعة الآن في الوضع الصامت اليومي. الكتابة مقتصرة على المشرفين فقط."
        },
        "rescue_mode": {
            "enabled": False,
            "target_group": None,
            "keyword": "بوت مراقبة",
            "threshold": 3,
            "duration_minutes": 30,
            "message": "⚠️ نداء الاستغاثة يدل على وجود مخالفة أو نزاع داخل المجموعة، الرجاء الانتظار حتى وصول المشرفين."
        },
        "force_sub_groups": {},
        "word_watch": {
            "target_groups": {},
            "words": list(DEFAULT_WATCH_WORDS),
            "alert_admins": {}
        },
        "custom_mute_durations": [],
        "active_mutes": []
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
                data["silent_mode"].setdefault("is_temporary", False)
                data["silent_mode"].setdefault("temp_until_timestamp", 0)

                data.setdefault("daily_silent_mode", {})
                data["daily_silent_mode"].setdefault("enabled", False)
                data["daily_silent_mode"].setdefault("start_time", "23:00")
                data["daily_silent_mode"].setdefault("end_time", "07:00")
                data["daily_silent_mode"].setdefault("target_group", None)
                data["daily_silent_mode"].setdefault(
                    "custom_message",
                    "🔇 المجموعة الآن في الوضع الصامت اليومي. الكتابة مقتصرة على المشرفين فقط."
                )

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

                data.setdefault("force_sub_groups", {})

                data.setdefault("word_watch", {})
                data["word_watch"].setdefault("target_groups", {})
                data["word_watch"].setdefault("words", list(DEFAULT_WATCH_WORDS))
                data["word_watch"].setdefault("alert_admins", {})

                data.setdefault("custom_mute_durations", [])
                data.setdefault("active_mutes", [])

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


def escape_markdown(text):
    if not text:
        return text
    return re.sub(r'([_*`\[\]])', r'\\\1', str(text))


async def safe_edit_text(message, text, reply_markup=None, parse_mode='Markdown'):
    try:
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception:
        try:
            await message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            logging.exception("فشل تعديل الرسالة (safe_edit_text)")


async def safe_reply_text(message, text, reply_markup=None, parse_mode='Markdown'):
    try:
        await message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception:
        try:
            await message.reply_text(text, reply_markup=reply_markup)
        except Exception:
            logging.exception("فشل إرسال الرد (safe_reply_text)")


def extract_forwarded_channel(message):
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None)
        if chat is not None and getattr(chat, "type", None) == "channel":
            return chat

    legacy_chat = getattr(message, "forward_from_chat", None)
    if legacy_chat is not None and getattr(legacy_chat, "type", None) == "channel":
        return legacy_chat

    return None


# === 3.1 أدوات نظام مراقبة الكلمات ===
_WORD_PATTERN_CACHE = {}


def _compile_watch_pattern(word):
    cached = _WORD_PATTERN_CACHE.get(word)
    if cached is not None:
        return cached
    escaped = re.escape(word.strip())
    try:
        pattern = re.compile(r'(?<!\w)' + escaped + r'(?!\w)', re.IGNORECASE | re.UNICODE)
    except re.error:
        pattern = None
    _WORD_PATTERN_CACHE[word] = pattern
    return pattern


def detect_watched_word(text, watched_words):
    if not text:
        return None
    for word in watched_words:
        if not word or not word.strip():
            continue
        pattern = _compile_watch_pattern(word)
        if pattern and pattern.search(text):
            return word
    return None


def build_user_mention_html(user):
    """ينشئ Mention حقيقي وقابل للنقر لصاحب رسالة باستخدام tg://user?id=، يعمل حتى بدون معرّف عام (@username)."""
    name = html.escape(user.first_name or "عضو")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


# === 3.2 أدوات نظام كتم المستخدمين ===
def get_all_mute_durations(bot_data):
    durations = list(DEFAULT_MUTE_DURATIONS)
    for item in bot_data.get("custom_mute_durations", []):
        durations.append((item.get("label"), item.get("seconds")))
    return durations


def format_mute_duration_label(seconds):
    if seconds % (24 * 60 * 60) == 0 and seconds >= 24 * 60 * 60:
        days = seconds // (24 * 60 * 60)
        return f"{days} يوم" if days == 1 else f"{days} أيام"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} ساعة" if hours == 1 else f"{hours} ساعات"
    minutes = max(1, seconds // 60)
    return f"{minutes} دقيقة"


def register_active_mute(bot_data, chat_id, user_id, user_name, until_ts):
    active = bot_data.setdefault("active_mutes", [])
    active[:] = [m for m in active if not (str(m.get("chat_id")) == str(chat_id) and str(m.get("user_id")) == str(user_id))]
    active.append({
        "chat_id": chat_id,
        "user_id": user_id,
        "user_name": user_name,
        "until_ts": until_ts
    })
    save_data(bot_data)


async def mute_watcher_loop(app):
    while True:
        try:
            bot_data = load_data()
            active = bot_data.get("active_mutes", [])
            now_ts = datetime.now().timestamp()
            remaining = []
            changed = False
            for m in active:
                if m.get("until_ts", 0) <= now_ts:
                    changed = True
                    try:
                        permissions = ChatPermissions(can_send_messages=True, can_send_other_messages=True)
                        await app.bot.restrict_chat_member(int(m["chat_id"]), int(m["user_id"]), permissions=permissions)
                    except Exception:
                        logging.exception("فشل رفع الكتم التلقائي عن مستخدم")
                else:
                    remaining.append(m)
            if changed:
                bot_data["active_mutes"] = remaining
                save_data(bot_data)
        except Exception:
            logging.exception("خطأ في حلقة مراقبة الكتمات")
        await asyncio.sleep(30)


# === 4. القوائم واللوحات ===
def get_main_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📢 إذاعة عامة للمجموعات", callback_data="bc_all"),
         InlineKeyboardButton("🎯 إذاعة مخصصة لمجموعة", callback_data="bc_single_select")],
        [InlineKeyboardButton("👤 إذاعة للمستخدمين (خاص)", callback_data="bc_users")],
        [InlineKeyboardButton("🔇 إدارة الوضع الصامت", callback_data="manage_silent")],
        [InlineKeyboardButton("🆘 نداء الاستغاثة", callback_data="manage_rescue")],
        [InlineKeyboardButton("📢 الاشتراك الإجباري", callback_data="manage_force_sub")],
        [InlineKeyboardButton("🔎 مراقبة الكلمات", callback_data="manage_word_watch")],
        [InlineKeyboardButton("🔇 كتم مستخدم", callback_data="manage_mute")],
        [InlineKeyboardButton("📖 دليل أوامر الإشراف", callback_data="show_cmd_help")],
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
    
    mode_type = "مؤقت" if silent_info.get("is_temporary") else "يومي"

    keyboard = [
        [InlineKeyboardButton(f"الحالة الحالية: {status} ({mode_type})", callback_data="toggle_silent")],
        [InlineKeyboardButton(f"🎯 المجموعة المستهدفة: {target_name}", callback_data="silent_select_target")],
        [InlineKeyboardButton("⏱️ وضع مدة مؤقتة جاهزة", callback_data="silent_durations")],
        [InlineKeyboardButton("⏰ ضبط توقيت يومي (من/إلى)", callback_data="set_silent_schedule")],
        [InlineKeyboardButton("✏️ تعديل الرسالة التوضيحية", callback_data="edit_silent_msg")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_daily_silent_keyboard(data):
    daily = data.get("daily_silent_mode", {})
    status = "🟢 مفعل" if daily.get("enabled") else "🔴 معطل"
    target = daily.get("target_group")
    target_name = data.get("groups", {}).get(str(target), "لم يتم التحديد ❌") if target else "لم يتم التحديد ❌"
    
    keyboard = [
        [InlineKeyboardButton(f"الحالة الحالية: {status}", callback_data="toggle_daily_silent")],
        [InlineKeyboardButton(f"🎯 المجموعة المستهدفة: {target_name}", callback_data="daily_silent_select_target")],
        [InlineKeyboardButton(f"⏰ وقت البدء: {daily.get('start_time', 'غير محدد')}", callback_data="daily_silent_set_start")],
        [InlineKeyboardButton(f"⏰ وقت النهاية: {daily.get('end_time', 'غير محدد')}", callback_data="daily_silent_set_end")],
        [InlineKeyboardButton("✏️ تعديل الرسالة التوضيحية", callback_data="daily_silent_edit_msg")],
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


# ---------- لوحات مراقبة الكلمات ----------
def get_word_watch_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📌 المجموعات المستهدفة", callback_data="ww_targets")],
        [InlineKeyboardButton("📝 الكلمات والعبارات", callback_data="ww_words")],
        [InlineKeyboardButton("👥 مشرفو التنبيه", callback_data="ww_alert_admins")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_ww_targets_keyboard(bot_data):
    ww = bot_data.get("word_watch", {})
    targets = ww.get("target_groups", {})
    groups = bot_data.get("groups", {})
    keyboard = []
    for gid, enabled in targets.items():
        gname = groups.get(gid, f"مجموعة #{gid}")
        icon = "🟢" if enabled else "🔴"
        keyboard.append([
            InlineKeyboardButton(f"{icon} {gname}", callback_data=f"ww_targets_toggle_{gid}"),
            InlineKeyboardButton("🗑️", callback_data=f"ww_targets_remove_{gid}")
        ])
    keyboard.append([InlineKeyboardButton("➕ إضافة مجموعة مستهدفة", callback_data="ww_targets_add")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="manage_word_watch")])
    return InlineKeyboardMarkup(keyboard)


def get_ww_targets_add_keyboard(bot_data):
    ww = bot_data.get("word_watch", {})
    targets = ww.get("target_groups", {})
    groups = bot_data.get("groups", {})
    keyboard = []
    for gid, gname in groups.items():
        if gid in targets:
            continue
        keyboard.append([InlineKeyboardButton(f"👥 {gname}", callback_data=f"ww_targets_add_{gid}")])
    if not keyboard:
        keyboard.append([InlineKeyboardButton("لا توجد مجموعات أخرى متاحة", callback_data="ww_targets")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="ww_targets")])
    return InlineKeyboardMarkup(keyboard)


def get_ww_words_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ إضافة كلمة/عبارة", callback_data="ww_words_add")],
        [InlineKeyboardButton("🗑️ حذف كلمة/عبارة", callback_data="ww_words_remove")],
        [InlineKeyboardButton("♻️ استعادة القائمة الافتراضية", callback_data="ww_words_reset")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="manage_word_watch")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_ww_alert_admins_groups_keyboard(bot_data):
    ww = bot_data.get("word_watch", {})
    targets = ww.get("target_groups", {})
    groups = bot_data.get("groups", {})
    keyboard = []
    for gid in targets.keys():
        gname = groups.get(gid, f"مجموعة #{gid}")
        keyboard.append([InlineKeyboardButton(f"👥 {gname}", callback_data=f"ww_alert_admins_{gid}")])
    if not keyboard:
        keyboard.append([InlineKeyboardButton("لا توجد مجموعات مستهدفة", callback_data="manage_word_watch")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="manage_word_watch")])
    return InlineKeyboardMarkup(keyboard)


def get_ww_alert_admins_keyboard(bot_data, group_id):
    ww = bot_data.get("word_watch", {})
    admins_list = ww.get("alert_admins", {}).get(group_id, [])
    groups = bot_data.get("groups", {})
    gname = groups.get(group_id, f"مجموعة #{group_id}")
    
    keyboard = []
    if admins_list:
        for admin_username in admins_list:
            keyboard.append([InlineKeyboardButton(f"❌ {admin_username}", callback_data=f"ww_alert_remove_{group_id}_{admin_username}")])
    else:
        keyboard.append([InlineKeyboardButton("لا يوجد مشرفون للتنبيه", callback_data="ww_alert_admins")])
    
    keyboard.append([InlineKeyboardButton("➕ إضافة مشرف تنبيه", callback_data=f"ww_alert_add_{group_id}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="ww_alert_admins")])
    return InlineKeyboardMarkup(keyboard)


# ---------- لوحات الاشتراك الإجباري ----------
def get_force_sub_main_keyboard(bot_data):
    fs_groups = bot_data.get("force_sub_groups", {})
    groups = bot_data.get("groups", {})
    keyboard = []
    
    for gid, config in fs_groups.items():
        gname = groups.get(gid, f"مجموعة #{gid}")
        status = "🟢 مفعل" if config.get("enabled") else "🔴 معطل"
        keyboard.append([InlineKeyboardButton(f"{status} {gname}", callback_data=f"fs_manage_{gid}")])
    
    keyboard.append([InlineKeyboardButton("➕ إضافة مجموعة جديدة", callback_data="fs_add_group")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)


def get_force_sub_group_keyboard(bot_data, group_id):
    fs_groups = bot_data.get("force_sub_groups", {})
    config = fs_groups.get(group_id, {})
    groups = bot_data.get("groups", {})
    gname = groups.get(group_id, f"مجموعة #{group_id}")
    
    status = "🟢 مفعل" if config.get("enabled") else "🔴 معطل"
    channel_id = config.get("channel_id", "غير محدد")
    channel_title = config.get("channel_title", "غير محدد")
    channel_username = config.get("channel_username", "غير محدد")
    invite_link = config.get("invite_link", "غير محدد")
    
    keyboard = [
        [InlineKeyboardButton(f"الحالة: {status}", callback_data=f"fs_toggle_{group_id}")],
        [InlineKeyboardButton(f"📢 القناة: {channel_title}", callback_data=f"fs_set_channel_{group_id}")],
        [InlineKeyboardButton("📋 ضبط رابط الدعوة", callback_data=f"fs_set_link_{group_id}")],
        [InlineKeyboardButton("🗑️ حذف الإعداد", callback_data=f"fs_delete_{group_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="manage_force_sub")]
    ]
    return InlineKeyboardMarkup(keyboard)


# === 5. الأوامر والمعالجات ===
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    register_user(user_id)
    
    if is_bot_admin(user_id):
        await update.message.reply_text(
            "🤖 **مرحباً أيها المشرف!**\n\n"
            "يمكنك استخدام لوحة التحكم عبر الأمر `/admin`.\n"
            "أو اضغط على الزر أدناه.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 لوحة التحكم", callback_data="main_menu")]
            ])
        )
    else:
        await update.message.reply_text(
            UPDATED_START_TEXT,
            parse_mode='Markdown'
        )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_bot_admin(user_id):
        await update.message.reply_text("⛔ هذا الأمر مخصص للمشرفين فقط.")
        return
    
    await update.message.reply_text(
        "📋 **لوحة تحكم المشرفين**\n\nاختر الإجراء المناسب:",
        parse_mode='Markdown',
        reply_markup=get_main_admin_keyboard()
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    if not is_bot_admin(user_id):
        await query.answer("⛔ غير مصرح لك", show_alert=True)
        return
    
    await query.answer()
    data = query.data
    bot_data = load_data()
    
    # ===== القائمة الرئيسية =====
    if data == "main_menu":
        await safe_edit_text(
            query.message,
            "📋 **لوحة تحكم المشرفين**\n\nاختر الإجراء المناسب:",
            reply_markup=get_main_admin_keyboard()
        )
    
    # ===== الإذاعات =====
    elif data == "bc_all":
        WAITING_STATES[user_id] = "bc_all"
        await safe_edit_text(
            query.message,
            "📢 **إذاعة عامة للمجموعات**\n\n"
            "أرسل نص الإذاعة الذي تريد نشره في **جميع المجموعات** المسجلة.\n\n"
            "📌 يمكنك إضافة أزرار روابط بالشكل التالي:\n"
            "`العنوان - الرابط` (كل رابط في سطر منفصل)\n"
            "مثال:\n"
            "`قناتنا - https://t.me/yourchannel`\n\n"
            "❗ بعد إرسال النص، سيُطلب منك اختيار إضافة أزرار أم لا.",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("main_menu")
        )
    
    elif data == "bc_single_select":
        keyboard = get_groups_selection_keyboard(bot_data, "bc_single_", "main_menu")
        await safe_edit_text(
            query.message,
            "🎯 **اختر المجموعة المستهدفة للإذاعة:**",
            reply_markup=keyboard
        )
    
    elif data.startswith("bc_single_"):
        group_id = data.replace("bc_single_", "")
        WAITING_STATES[user_id] = f"bc_single_{group_id}"
        await safe_edit_text(
            query.message,
            f"🎯 **إذاعة مخصصة للمجموعة**\n\n"
            f"أرسل نص الإذاعة التي تريد نشرها في هذه المجموعة.\n\n"
            "📌 يمكنك إضافة أزرار روابط بالشكل التالي:\n"
            "`العنوان - الرابط` (كل رابط في سطر منفصل)\n"
            "مثال:\n"
            "`قناتنا - https://t.me/yourchannel`\n\n"
            "❗ بعد إرسال النص، سيُطلب منك اختيار إضافة أزرار أم لا.",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("main_menu")
        )
    
    elif data == "bc_users":
        WAITING_STATES[user_id] = "bc_users"
        await safe_edit_text(
            query.message,
            "👤 **إذاعة للمستخدمين (خاص)**\n\n"
            "أرسل نص الإذاعة التي تريد إرسالها إلى **جميع المستخدمين** المسجلين في الخاص.\n\n"
            "📌 يمكنك إضافة أزرار روابط بالشكل التالي:\n"
            "`العنوان - الرابط` (كل رابط في سطر منفصل)\n"
            "مثال:\n"
            "`قناتنا - https://t.me/yourchannel`\n\n"
            "❗ بعد إرسال النص، سيُطلب منك اختيار إضافة أزرار أم لا.",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("main_menu")
        )
    
    # ===== الوضع الصامت =====
    elif data == "manage_silent":
        await safe_edit_text(
            query.message,
            "🔇 **إدارة الوضع الصامت**\n\nاختر الإجراء المناسب:",
            reply_markup=get_silent_keyboard(bot_data)
        )
    
    elif data == "toggle_silent":
        silent = bot_data["silent_mode"]
        silent["enabled"] = not silent.get("enabled")
        if silent["enabled"]:
            silent["until_timestamp"] = datetime.now().timestamp()
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            "🔇 **إدارة الوضع الصامت**\n\nتم تحديث الحالة.",
            reply_markup=get_silent_keyboard(bot_data)
        )
    
    elif data == "silent_select_target":
        keyboard = get_groups_selection_keyboard(bot_data, "silent_target_", "manage_silent")
        await safe_edit_text(
            query.message,
            "🎯 **اختر المجموعة المستهدفة للوضع الصامت:**",
            reply_markup=keyboard
        )
    
    elif data.startswith("silent_target_"):
        group_id = data.replace("silent_target_", "")
        bot_data["silent_mode"]["target_group"] = int(group_id) if group_id.isdigit() else None
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            f"✅ تم تحديد المجموعة المستهدفة.",
            reply_markup=get_silent_keyboard(bot_data)
        )
    
    elif data == "silent_durations":
        await safe_edit_text(
            query.message,
            "⏱️ **اختر مدة الوضع الصامت المؤقت:**",
            reply_markup=get_durations_keyboard()
        )
    
    elif data.startswith("dur_"):
        minutes = int(data.replace("dur_", ""))
        seconds = minutes * 60
        silent = bot_data["silent_mode"]
        silent["enabled"] = True
        silent["is_temporary"] = True
        silent["temp_until_timestamp"] = datetime.now().timestamp() + seconds
        silent["until_timestamp"] = datetime.now().timestamp() + seconds
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            f"✅ تم تفعيل الوضع الصامت لمدة {minutes} دقيقة.",
            reply_markup=get_silent_keyboard(bot_data)
        )
    
    elif data == "set_silent_schedule":
        WAITING_STATES[user_id] = "silent_start_time"
        await safe_edit_text(
            query.message,
            "⏰ **ضبط التوقيت اليومي للوضع الصامت**\n\n"
            "أرسل وقت البدء بصيغة HH:MM (مثال: 22:00)",
            reply_markup=get_back_keyboard("manage_silent")
        )
    
    elif data == "edit_silent_msg":
        WAITING_STATES[user_id] = "edit_silent_msg"
        await safe_edit_text(
            query.message,
            "✏️ **تعديل رسالة الوضع الصامت**\n\n"
            "أرسل النص الجديد الذي سيظهر عند تفعيل الوضع الصامت.",
            reply_markup=get_back_keyboard("manage_silent")
        )
    
    # ===== الوضع الصامت اليومي =====
    elif data == "toggle_daily_silent":
        daily = bot_data["daily_silent_mode"]
        daily["enabled"] = not daily.get("enabled")
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            "🔇 **إدارة الوضع الصامت اليومي**\n\nتم تحديث الحالة.",
            reply_markup=get_daily_silent_keyboard(bot_data)
        )
    
    elif data == "daily_silent_select_target":
        keyboard = get_groups_selection_keyboard(bot_data, "daily_silent_target_", "manage_silent")
        await safe_edit_text(
            query.message,
            "🎯 **اختر المجموعة المستهدفة للوضع الصامت اليومي:**",
            reply_markup=keyboard
        )
    
    elif data.startswith("daily_silent_target_"):
        group_id = data.replace("daily_silent_target_", "")
        bot_data["daily_silent_mode"]["target_group"] = int(group_id) if group_id.isdigit() else None
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            f"✅ تم تحديد المجموعة المستهدفة.",
            reply_markup=get_daily_silent_keyboard(bot_data)
        )
    
    elif data == "daily_silent_set_start":
        WAITING_STATES[user_id] = "daily_silent_start"
        await safe_edit_text(
            query.message,
            "⏰ **ضبط وقت بدء الوضع الصامت اليومي**\n\n"
            "أرسل الوقت بصيغة HH:MM (مثال: 23:00)",
            reply_markup=get_back_keyboard("manage_silent")
        )
    
    elif data == "daily_silent_set_end":
        WAITING_STATES[user_id] = "daily_silent_end"
        await safe_edit_text(
            query.message,
            "⏰ **ضبط وقت نهاية الوضع الصامت اليومي**\n\n"
            "أرسل الوقت بصيغة HH:MM (مثال: 07:00)",
            reply_markup=get_back_keyboard("manage_silent")
        )
    
    elif data == "daily_silent_edit_msg":
        WAITING_STATES[user_id] = "daily_silent_msg"
        await safe_edit_text(
            query.message,
            "✏️ **تعديل رسالة الوضع الصامت اليومي**\n\n"
            "أرسل النص الجديد الذي سيظهر عند تفعيل الوضع الصامت اليومي.",
            reply_markup=get_back_keyboard("manage_silent")
        )
    
    # ===== نداء الاستغاثة =====
    elif data == "manage_rescue":
        await safe_edit_text(
            query.message,
            "🆘 **إدارة نداء الاستغاثة**\n\nاختر الإجراء المناسب:",
            reply_markup=get_rescue_keyboard(bot_data)
        )
    
    elif data == "rescue_toggle":
        rescue = bot_data["rescue_mode"]
        rescue["enabled"] = not rescue.get("enabled")
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            "🆘 **إدارة نداء الاستغاثة**\n\nتم تحديث الحالة.",
            reply_markup=get_rescue_keyboard(bot_data)
        )
    
    elif data == "rescue_select_target":
        keyboard = get_groups_selection_keyboard(bot_data, "rescue_target_", "manage_rescue")
        await safe_edit_text(
            query.message,
            "🎯 **اختر المجموعة المستهدفة لنظام نداء الاستغاثة:**",
            reply_markup=keyboard
        )
    
    elif data.startswith("rescue_target_"):
        group_id = data.replace("rescue_target_", "")
        bot_data["rescue_mode"]["target_group"] = int(group_id) if group_id.isdigit() else None
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            f"✅ تم تحديد المجموعة المستهدفة.",
            reply_markup=get_rescue_keyboard(bot_data)
        )
    
    elif data == "rescue_set_keyword":
        WAITING_STATES[user_id] = "rescue_keyword"
        await safe_edit_text(
            query.message,
            "🗣️ **تعديل كلمة نداء الاستغاثة**\n\n"
            "أرسل الكلمة أو العبارة التي ستُستخدم لتفعيل نظام الاستغاثة.",
            reply_markup=get_back_keyboard("manage_rescue")
        )
    
    elif data == "rescue_set_threshold":
        WAITING_STATES[user_id] = "rescue_threshold"
        await safe_edit_text(
            query.message,
            "🔢 **تعديل عدد النداءات**\n\n"
            "أرسل العدد المطلوب من النداءات (رقم) لتفعيل الوضع الصامت تلقائياً.",
            reply_markup=get_back_keyboard("manage_rescue")
        )
    
    elif data == "rescue_set_duration":
        WAITING_STATES[user_id] = "rescue_duration"
        await safe_edit_text(
            query.message,
            "⏱️ **تعديل مدة الصمت**\n\n"
            "أرسل المدة بالدقائق (رقم) التي سيبقى فيها الوضع الصامت بعد تفعيل الاستغاثة.",
            reply_markup=get_back_keyboard("manage_rescue")
        )
    
    elif data == "rescue_set_message":
        WAITING_STATES[user_id] = "rescue_message"
        await safe_edit_text(
            query.message,
            "✏️ **تعديل رسالة نداء الاستغاثة**\n\n"
            "أرسل النص الجديد الذي سيظهر عند تفعيل نظام الاستغاثة.",
            reply_markup=get_back_keyboard("manage_rescue")
        )
    
    # ===== إدارة الكلمات المحظورة =====
    elif data == "manage_words":
        words_list = bot_data.get("words", [])
        words_text = "\n".join([f"• {w}" for w in words_list]) if words_list else "لا توجد كلمات محظورة"
        await safe_edit_text(
            query.message,
            f"⛔ **الكلمات المحظورة**\n\n{words_text}\n\n"
            "لإضافة كلمة جديدة، استخدم الأمر:\n`/addword كلمة`\n"
            "لحذف كلمة، استخدم:\n`/delword كلمة`",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("main_menu")
        )
    
    elif data == "manage_emojis":
        emojis_list = bot_data.get("emojis", [])
        emojis_text = " ".join(emojis_list) if emojis_list else "لا توجد إيموجيات محظورة"
        await safe_edit_text(
            query.message,
            f"😀 **الإيموجيات المحظورة**\n\n{emojis_text}\n\n"
            "لإضافة إيموجي جديد، استخدم الأمر:\n`/addemoji 😂`\n"
            "لحذف إيموجي، استخدم:\n`/delemoji 😂`",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("main_menu")
        )
    
    # ===== الروابط =====
    elif data == "manage_links":
        await safe_edit_text(
            query.message,
            "🔗 **إدارة الروابط المحظورة**",
            reply_markup=get_links_keyboard(bot_data)
        )
    
    elif data == "toggle_block_all_links":
        bot_data["block_all_links"] = not bot_data.get("block_all_links", False)
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            "🔗 **إدارة الروابط المحظورة**\n\nتم تحديث الحالة.",
            reply_markup=get_links_keyboard(bot_data)
        )
    
    elif data == "add_link":
        WAITING_STATES[user_id] = "add_link"
        await safe_edit_text(
            query.message,
            "➕ **إضافة رابط/نطاق محظور**\n\n"
            "أرسل الرابط أو النطاق الذي تريد حظره.\n"
            "مثال: `t.me/spam` أو `spam.com`",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("manage_links")
        )
    
    elif data == "clear_links":
        bot_data["banned_links"] = []
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            "🗑️ **تم مسح جميع الروابط المحظورة**",
            reply_markup=get_links_keyboard(bot_data)
        )
    
    # ===== الملصقات =====
    elif data == "manage_stickers":
        await safe_edit_text(
            query.message,
            "🎭 **إدارة الملصقات والصور المتحركة**",
            reply_markup=get_stickers_keyboard(bot_data)
        )
    
    elif data == "toggle_block_stickers":
        bot_data["block_stickers"] = not bot_data.get("block_stickers", False)
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            "🎭 **إدارة الملصقات**\n\nتم تحديث الحالة.",
            reply_markup=get_stickers_keyboard(bot_data)
        )
    
    elif data == "toggle_block_animated":
        bot_data["block_animated_stickers"] = not bot_data.get("block_animated_stickers", False)
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            "🎭 **إدارة الملصقات المتحركة و GIF**\n\nتم تحديث الحالة.",
            reply_markup=get_stickers_keyboard(bot_data)
        )
    
    # ===== مراقبة الكلمات =====
    elif data == "manage_word_watch":
        await safe_edit_text(
            query.message,
            "🔎 **إدارة مراقبة الكلمات**\n\nاختر الإجراء المناسب:",
            reply_markup=get_word_watch_main_keyboard()
        )
    
    elif data == "ww_targets":
        await safe_edit_text(
            query.message,
            "📌 **المجموعات المستهدفة لمراقبة الكلمات**",
            reply_markup=get_ww_targets_keyboard(bot_data)
        )
    
    elif data.startswith("ww_targets_toggle_"):
        gid = data.replace("ww_targets_toggle_", "")
        ww = bot_data["word_watch"]
        if gid in ww["target_groups"]:
            ww["target_groups"][gid] = not ww["target_groups"][gid]
            save_data(bot_data)
        await safe_edit_text(
            query.message,
            "📌 **تم تحديث حالة المجموعة**",
            reply_markup=get_ww_targets_keyboard(bot_data)
        )
    
    elif data.startswith("ww_targets_remove_"):
        gid = data.replace("ww_targets_remove_", "")
        ww = bot_data["word_watch"]
        if gid in ww["target_groups"]:
            del ww["target_groups"][gid]
            save_data(bot_data)
        await safe_edit_text(
            query.message,
            "📌 **تم حذف المجموعة من القائمة**",
            reply_markup=get_ww_targets_keyboard(bot_data)
        )
    
    elif data == "ww_targets_add":
        await safe_edit_text(
            query.message,
            "➕ **اختر مجموعة لإضافتها للمراقبة**",
            reply_markup=get_ww_targets_add_keyboard(bot_data)
        )
    
    elif data.startswith("ww_targets_add_"):
        gid = data.replace("ww_targets_add_", "")
        ww = bot_data["word_watch"]
        ww["target_groups"][gid] = True
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            "✅ **تم إضافة المجموعة للمراقبة**",
            reply_markup=get_ww_targets_keyboard(bot_data)
        )
    
    elif data == "ww_words":
        ww = bot_data.get("word_watch", {})
        words = ww.get("words", [])
        words_text = "\n".join([f"• {w}" for w in words]) if words else "لا توجد كلمات"
        await safe_edit_text(
            query.message,
            f"📝 **الكلمات والعبارات المراقبة**\n\n{words_text}\n\n"
            "عدد الكلمات: {}",
            reply_markup=get_ww_words_keyboard()
        )
    
    elif data == "ww_words_add":
        WAITING_STATES[user_id] = "ww_words_add"
        await safe_edit_text(
            query.message,
            "➕ **إضافة كلمة/عبارة للمراقبة**\n\n"
            "أرسل الكلمة أو العبارة التي تريد إضافتها.",
            reply_markup=get_back_keyboard("ww_words")
        )
    
    elif data == "ww_words_remove":
        WAITING_STATES[user_id] = "ww_words_remove"
        await safe_edit_text(
            query.message,
            "🗑️ **حذف كلمة/عبارة من المراقبة**\n\n"
            "أرسل الكلمة أو العبارة التي تريد حذفها.",
            reply_markup=get_back_keyboard("ww_words")
        )
    
    elif data == "ww_words_reset":
        ww = bot_data["word_watch"]
        ww["words"] = list(DEFAULT_WATCH_WORDS)
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            "♻️ **تم استعادة القائمة الافتراضية للكلمات**",
            reply_markup=get_ww_words_keyboard()
        )
    
    elif data == "ww_alert_admins":
        await safe_edit_text(
            query.message,
            "👥 **اختر المجموعة لإدارة مشرفي التنبيه**",
            reply_markup=get_ww_alert_admins_groups_keyboard(bot_data)
        )
    
    elif data.startswith("ww_alert_admins_"):
        gid = data.replace("ww_alert_admins_", "")
        await safe_edit_text(
            query.message,
            f"👥 **مشرفو التنبيه للمجموعة**",
            reply_markup=get_ww_alert_admins_keyboard(bot_data, gid)
        )
    
    elif data.startswith("ww_alert_add_"):
        gid = data.replace("ww_alert_add_", "")
        WAITING_STATES[user_id] = f"ww_alert_add_{gid}"
        await safe_edit_text(
            query.message,
            "➕ **إضافة مشرف تنبيه**\n\n"
            "أرسل اسم المستخدم (@username) للمشرف الذي تريد تنبيهه عند رصد كلمة مراقبة.",
            reply_markup=get_back_keyboard(f"ww_alert_admins_{gid}")
        )
    
    elif data.startswith("ww_alert_remove_"):
        parts = data.replace("ww_alert_remove_", "").split("_")
        gid = parts[0]
        username = "_".join(parts[1:])
        ww = bot_data["word_watch"]
        if gid in ww.get("alert_admins", {}):
            admins_list = ww["alert_admins"][gid]
            if username in admins_list:
                admins_list.remove(username)
                if not admins_list:
                    del ww["alert_admins"][gid]
                save_data(bot_data)
        await safe_edit_text(
            query.message,
            f"❌ **تم حذف المشرف من قائمة التنبيه**",
            reply_markup=get_ww_alert_admins_keyboard(bot_data, gid)
        )
    
    # ===== الاشتراك الإجباري =====
    elif data == "manage_force_sub":
        await safe_edit_text(
            query.message,
            "📢 **إدارة الاشتراك الإجباري**\n\nاختر المجموعة التي تريد إدارة إعداداتها:",
            reply_markup=get_force_sub_main_keyboard(bot_data)
        )
    
    elif data == "fs_add_group":
        keyboard = get_groups_selection_keyboard(bot_data, "fs_select_", "manage_force_sub")
        await safe_edit_text(
            query.message,
            "➕ **اختر مجموعة لإضافة نظام الاشتراك الإجباري لها**",
            reply_markup=keyboard
        )
    
    elif data.startswith("fs_select_"):
        group_id = data.replace("fs_select_", "")
        fs_groups = bot_data.get("force_sub_groups", {})
        if group_id not in fs_groups:
            fs_groups[group_id] = {
                "channel_id": None,
                "channel_title": None,
                "channel_username": None,
                "invite_link": None,
                "enabled": False,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
            save_data(bot_data)
        
        WAITING_STATES[user_id] = f"fs_set_channel_{group_id}"
        await safe_edit_text(
            query.message,
            f"📢 **إعداد الاشتراك الإجباري للمجموعة**\n\n"
            "الرجاء **إعادة توجيه** رسالة من القناة التي تريد فرض الاشتراك بها.\n\n"
            "📌 تأكد من أن البوت مشرف في القناة المستهدفة.",
            reply_markup=get_back_keyboard("manage_force_sub")
        )
    
    elif data.startswith("fs_manage_"):
        group_id = data.replace("fs_manage_", "")
        await safe_edit_text(
            query.message,
            f"📢 **إدارة الاشتراك الإجباري**",
            reply_markup=get_force_sub_group_keyboard(bot_data, group_id)
        )
    
    elif data.startswith("fs_toggle_"):
        group_id = data.replace("fs_toggle_", "")
        fs_groups = bot_data.get("force_sub_groups", {})
        if group_id in fs_groups:
            fs_groups[group_id]["enabled"] = not fs_groups[group_id].get("enabled", False)
            fs_groups[group_id]["updated_at"] = datetime.now().isoformat()
            save_data(bot_data)
        await safe_edit_text(
            query.message,
            f"✅ **تم تحديث حالة الاشتراك الإجباري**",
            reply_markup=get_force_sub_group_keyboard(bot_data, group_id)
        )
    
    elif data.startswith("fs_set_channel_"):
        group_id = data.replace("fs_set_channel_", "")
        WAITING_STATES[user_id] = f"fs_set_channel_{group_id}"
        await safe_edit_text(
            query.message,
            f"📢 **تحديث القناة**\n\n"
            "الرجاء **إعادة توجيه** رسالة من القناة الجديدة التي تريد فرض الاشتراك بها.",
            reply_markup=get_back_keyboard(f"fs_manage_{group_id}")
        )
    
    elif data.startswith("fs_set_link_"):
        group_id = data.replace("fs_set_link_", "")
        WAITING_STATES[user_id] = f"fs_set_link_{group_id}"
        await safe_edit_text(
            query.message,
            f"📋 **ضبط رابط الدعوة**\n\n"
            "أرسل رابط الدعوة الخاص بالقناة.\n"
            "مثال: `https://t.me/yourchannel`",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard(f"fs_manage_{group_id}")
        )
    
    elif data.startswith("fs_delete_"):
        group_id = data.replace("fs_delete_", "")
        fs_groups = bot_data.get("force_sub_groups", {})
        if group_id in fs_groups:
            del fs_groups[group_id]
            save_data(bot_data)
        await safe_edit_text(
            query.message,
            f"🗑️ **تم حذف إعداد الاشتراك الإجباري لهذه المجموعة**",
            reply_markup=get_force_sub_main_keyboard(bot_data)
        )
    
    # ===== كتم المستخدمين =====
    elif data == "manage_mute":
        await safe_edit_text(
            query.message,
            "🔇 **كتم مستخدم**\n\n"
            "اختر مجموعة لكتم أحد أعضائها:",
            reply_markup=get_groups_selection_keyboard(bot_data, "mute_select_", "main_menu")
        )
    
    elif data.startswith("mute_select_"):
        group_id = data.replace("mute_select_", "")
        WAITING_STATES[user_id] = f"mute_target_{group_id}"
        await safe_edit_text(
            query.message,
            f"🔇 **كتم مستخدم**\n\n"
            "قم بالرد على رسالة المستخدم الذي تريد كتمه في المجموعة.\n"
            "أو أرسل معرف المستخدم (ID) إذا كنت تعرفه.\n\n"
            "📌 ملاحظة: يجب أن تكون في المجموعة المستهدفة للرد على الرسالة.",
            reply_markup=get_back_keyboard("manage_mute")
        )
    
    elif data == "show_cmd_help":
        help_text = (
            "📖 **دليل أوامر الإشراف**\n\n"
            "📌 **الأوامر الأساسية:**\n"
            "• `/start` - عرض رسالة الترحيب.\n"
            "• `/admin` - فتح لوحة تحكم المشرفين.\n\n"
            "📌 **إدارة الكلمات المحظورة:**\n"
            "• `/addword كلمة` - إضافة كلمة محظورة.\n"
            "• `/delword كلمة` - حذف كلمة محظورة.\n\n"
            "📌 **إدارة الإيموجيات المحظورة:**\n"
            "• `/addemoji 😂` - إضافة إيموجي محظور.\n"
            "• `/delemoji 😂` - حذف إيموجي محظور.\n\n"
            "📌 **الأزرار في لوحة التحكم:**\n"
            "تتيح لك التحكم الكامل في جميع إعدادات البوت بسهولة."
        )
        await safe_edit_text(
            query.message,
            help_text,
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("main_menu")
        )
    
    # ===== إضافة مشرف =====
    elif data == "add_admin":
        WAITING_STATES[user_id] = "add_admin"
        await safe_edit_text(
            query.message,
            "👤 **إضافة مشرف جديد**\n\n"
            "أرسل معرف المستخدم (ID) للشخص الذي تريد إضافته كمشرف للبوت.\n\n"
            "📌 يمكنك الحصول على ID المستخدم من خلال بوتات معرفة الأيدي.",
            reply_markup=get_back_keyboard("main_menu")
        )
    
    # ===== أزرار الإذاعة =====
    elif data == "btn_add_yes":
        WAITING_STATES[user_id] = "btn_text"
        await safe_edit_text(
            query.message,
            "➕ **إضافة أزرار**\n\n"
            "أرسل الأزرار بالشكل التالي (كل زر في سطر منفصل):\n"
            "`العنوان - الرابط`\n\n"
            "مثال:\n"
            "`قناتنا - https://t.me/yourchannel`\n"
            "`مجموعتنا - https://t.me/yourgroup`\n\n"
            "يمكنك تلوين الأزرار بإضافة `- style:green` أو `[green]` بعد الرابط.",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("main_menu")
        )
    
    elif data == "btn_add_no":
        # إرسال الإذاعة بدون أزرار
        await send_broadcast(update, context, None)
    
    else:
        await query.answer("⚠️ خيار غير معروف", show_alert=True)


async def send_broadcast(update, context, reply_markup):
    """يرسل الإذاعة المحفوظة في TEMP_BROADCAST"""
    user_id = update.effective_user.id
    broadcast_data = TEMP_BROADCAST.get(user_id)
    
    if not broadcast_data:
        await update.callback_query.answer("⚠️ لا توجد إذاعة معلقة", show_alert=True)
        return
    
    text = broadcast_data.get("text")
    broadcast_type = broadcast_data.get("type")
    target = broadcast_data.get("target")
    
    bot_data = load_data()
    success_count = 0
    fail_count = 0
    
    if broadcast_type == "bc_all":
        for group_id in bot_data.get("groups", {}).keys():
            try:
                await context.bot.send_message(int(group_id), text, reply_markup=reply_markup, parse_mode='Markdown')
                success_count += 1
            except Exception:
                fail_count += 1
    
    elif broadcast_type == "bc_single":
        try:
            await context.bot.send_message(int(target), text, reply_markup=reply_markup, parse_mode='Markdown')
            success_count = 1
        except Exception:
            fail_count = 1
    
    elif broadcast_type == "bc_users":
        for user_id in bot_data.get("users", []):
            try:
                await context.bot.send_message(user_id, text, reply_markup=reply_markup, parse_mode='Markdown')
                success_count += 1
            except Exception:
                fail_count += 1
    
    # تنظيف البيانات المؤقتة
    TEMP_BROADCAST.pop(user_id, None)
    WAITING_STATES.pop(user_id, None)
    
    await safe_edit_text(
        update.callback_query.message,
        f"✅ **تم إرسال الإذاعة**\n\n"
        f"📤 ناجحة: {success_count}\n"
        f"📥 فاشلة: {fail_count}",
        reply_markup=get_back_keyboard("main_menu")
    )


# === معالجات الرسائل النصية ===
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.effective_message
    text = message.text
    
    # تسجيل المستخدم
    register_user(user_id)
    
    # معالجة الرسائل في الخاص
    if update.effective_chat.type == "private":
        # تجاهل الرسائل من المشرفين أثناء انتظارهم لإدخال بيانات
        if user_id in WAITING_STATES:
            await handle_admin_input(update, context)
            return
        
        # رد على غير المشرفين برسالة تنبيه (مع حد لمنع الإزعاج)
        if not is_bot_admin(user_id):
            # التحقق من عدد الرسائل المرسلة
            track = PRIVATE_MSG_TRACK.get(user_id, 0)
            PRIVATE_MSG_TRACK[user_id] = track + 1
            
            # إرسال الرد في أول 3 رسائل فقط لمنع الإزعاج
            if track <= 2:
                await message.reply_text(NON_ADMIN_SPAM_TEXT, parse_mode='Markdown')
            return
    
    # معالجة الرسائل في المجموعات
    if update.effective_chat.type in ["group", "supergroup"]:
        chat_id = update.effective_chat.id
        register_group(chat_id, update.effective_chat.title)
        
        # تجاهل رسائل البوت نفسه
        if user_id == context.bot.id:
            return
        
        # التحقق من الصلاحيات
        is_admin = await is_group_admin(context, chat_id, user_id)
        
        # إذا كان المستخدم مشرفاً في المجموعة أو مشرفاً في البوت، لا تطبق عليه الفلاتر
        if is_admin:
            return
        
        # ===== نظام الاشتراك الإجباري (مع إضافة Mention) =====
        await handle_force_subscription(update, context)
        
        # ===== نظام مراقبة الكلمات =====
        await handle_word_watch(update, context)
        
        # ===== نظام الوضع الصامت =====
        await handle_silent_mode(update, context)
        
        # ===== نظام حظر الإيموجيات =====
        await handle_emoji_filter(update, context)
        
        # ===== نظام حظر الكلمات =====
        await handle_word_filter(update, context)
        
        # ===== نظام حظر الروابط =====
        await handle_link_filter(update, context)
        
        # ===== نظام حظر الملصقات =====
        await handle_sticker_filter(update, context)


async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة مدخلات المشرفين في حالة الانتظار"""
    user_id = update.effective_user.id
    message = update.effective_message
    text = message.text
    state = WAITING_STATES.get(user_id)
    
    if not state:
        return
    
    bot_data = load_data()
    
    # ===== إضافة مشرف =====
    if state == "add_admin":
        try:
            new_admin_id = int(text.strip())
            if new_admin_id not in bot_data["admins"]:
                bot_data["admins"].append(new_admin_id)
                save_data(bot_data)
                await message.reply_text(f"✅ تم إضافة المستخدم `{new_admin_id}` كمشرف.", parse_mode='Markdown')
            else:
                await message.reply_text("⚠️ هذا المستخدم مشرف بالفعل.")
        except ValueError:
            await message.reply_text("❌ يرجى إرسال معرف صحيح (أرقام فقط).")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== إضافة كلمة محظورة =====
    elif state == "add_word":
        word = text.strip()
        if word not in bot_data["words"]:
            bot_data["words"].append(word)
            save_data(bot_data)
            await message.reply_text(f"✅ تم إضافة الكلمة: `{word}`", parse_mode='Markdown')
        else:
            await message.reply_text("⚠️ هذه الكلمة موجودة بالفعل.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== حذف كلمة محظورة =====
    elif state == "del_word":
        word = text.strip()
        if word in bot_data["words"]:
            bot_data["words"].remove(word)
            save_data(bot_data)
            await message.reply_text(f"✅ تم حذف الكلمة: `{word}`", parse_mode='Markdown')
        else:
            await message.reply_text("⚠️ هذه الكلمة غير موجودة.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== إضافة إيموجي محظور =====
    elif state == "add_emoji":
        emoji = text.strip()
        if emoji not in bot_data["emojis"]:
            bot_data["emojis"].append(emoji)
            save_data(bot_data)
            await message.reply_text(f"✅ تم إضافة الإيموجي: {emoji}")
        else:
            await message.reply_text("⚠️ هذا الإيموجي موجود بالفعل.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== حذف إيموجي محظور =====
    elif state == "del_emoji":
        emoji = text.strip()
        if emoji in bot_data["emojis"]:
            bot_data["emojis"].remove(emoji)
            save_data(bot_data)
            await message.reply_text(f"✅ تم حذف الإيموجي: {emoji}")
        else:
            await message.reply_text("⚠️ هذا الإيموجي غير موجود.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== إضافة رابط محظور =====
    elif state == "add_link":
        link = text.strip().lower()
        if link not in bot_data["banned_links"]:
            bot_data["banned_links"].append(link)
            save_data(bot_data)
            await message.reply_text(f"✅ تم إضافة الرابط: `{link}`", parse_mode='Markdown')
        else:
            await message.reply_text("⚠️ هذا الرابط موجود بالفعل.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== الإذاعات =====
    elif state.startswith("bc_"):
        # حفظ نص الإذاعة في TEMP_BROADCAST
        broadcast_type = state
        target = None
        if state.startswith("bc_single_"):
            target = state.replace("bc_single_", "")
            broadcast_type = "bc_single"
        
        TEMP_BROADCAST[user_id] = {
            "text": text,
            "type": broadcast_type,
            "target": target
        }
        
        WAITING_STATES.pop(user_id, None)
        
        # سؤال عن إضافة أزرار
        await message.reply_text(
            "✅ **تم حفظ نص الإذاعة**\n\n"
            "هل تريد إضافة أزرار روابط؟",
            parse_mode='Markdown',
            reply_markup=get_buttons_decision_keyboard()
        )
        return
    
    # ===== أزرار الإذاعة =====
    elif state == "btn_text":
        # حفظ الأزرار
        markup = parse_button_markup(text)
        if markup:
            TEMP_BROADCAST[user_id]["markup"] = markup
            await message.reply_text("✅ تم حفظ الأزرار بنجاح.")
        else:
            await message.reply_text("⚠️ لم يتم التعرف على أزرار صالحة. سيتم الإرسال بدون أزرار.")
        
        WAITING_STATES.pop(user_id, None)
        # إرسال الإذاعة
        await send_broadcast_from_message(update, context, markup)
        return
    
    # ===== الوضع الصامت - ضبط وقت البدء =====
    elif state == "silent_start_time":
        match = re.match(r'^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$', text.strip())
        if match:
            bot_data["silent_mode"]["start_time"] = text.strip()
            save_data(bot_data)
            await message.reply_text("✅ تم ضبط وقت البدء.")
        else:
            await message.reply_text("❌ صيغة غير صحيحة. استخدم HH:MM (مثال: 22:00)")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== الوضع الصامت - تعديل الرسالة =====
    elif state == "edit_silent_msg":
        bot_data["silent_mode"]["custom_message"] = text.strip()
        save_data(bot_data)
        await message.reply_text("✅ تم تعديل الرسالة بنجاح.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== الوضع الصامت اليومي - وقت البدء =====
    elif state == "daily_silent_start":
        match = re.match(r'^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$', text.strip())
        if match:
            bot_data["daily_silent_mode"]["start_time"] = text.strip()
            save_data(bot_data)
            await message.reply_text("✅ تم ضبط وقت بدء الوضع الصامت اليومي.")
        else:
            await message.reply_text("❌ صيغة غير صحيحة. استخدم HH:MM (مثال: 23:00)")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== الوضع الصامت اليومي - وقت النهاية =====
    elif state == "daily_silent_end":
        match = re.match(r'^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$', text.strip())
        if match:
            bot_data["daily_silent_mode"]["end_time"] = text.strip()
            save_data(bot_data)
            await message.reply_text("✅ تم ضبط وقت نهاية الوضع الصامت اليومي.")
        else:
            await message.reply_text("❌ صيغة غير صحيحة. استخدم HH:MM (مثال: 07:00)")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== الوضع الصامت اليومي - تعديل الرسالة =====
    elif state == "daily_silent_msg":
        bot_data["daily_silent_mode"]["custom_message"] = text.strip()
        save_data(bot_data)
        await message.reply_text("✅ تم تعديل الرسالة بنجاح.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== نداء الاستغاثة - كلمة النداء =====
    elif state == "rescue_keyword":
        bot_data["rescue_mode"]["keyword"] = text.strip()
        save_data(bot_data)
        await message.reply_text("✅ تم تعديل كلمة النداء.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== نداء الاستغاثة - عدد النداءات =====
    elif state == "rescue_threshold":
        try:
            threshold = int(text.strip())
            if threshold > 0:
                bot_data["rescue_mode"]["threshold"] = threshold
                save_data(bot_data)
                await message.reply_text(f"✅ تم ضبط العدد على {threshold}")
            else:
                await message.reply_text("❌ يجب أن يكون العدد أكبر من صفر.")
        except ValueError:
            await message.reply_text("❌ يرجى إرسال رقم صحيح.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== نداء الاستغاثة - مدة الصمت =====
    elif state == "rescue_duration":
        try:
            duration = int(text.strip())
            if duration > 0:
                bot_data["rescue_mode"]["duration_minutes"] = duration
                save_data(bot_data)
                await message.reply_text(f"✅ تم ضبط المدة على {duration} دقيقة.")
            else:
                await message.reply_text("❌ يجب أن تكون المدة أكبر من صفر.")
        except ValueError:
            await message.reply_text("❌ يرجى إرسال رقم صحيح.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== نداء الاستغاثة - رسالة النداء =====
    elif state == "rescue_message":
        bot_data["rescue_mode"]["message"] = text.strip()
        save_data(bot_data)
        await message.reply_text("✅ تم تعديل رسالة النداء.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== مراقبة الكلمات - إضافة كلمة =====
    elif state == "ww_words_add":
        ww = bot_data["word_watch"]
        word = text.strip()
        if word not in ww["words"]:
            ww["words"].append(word)
            save_data(bot_data)
            await message.reply_text(f"✅ تم إضافة الكلمة: `{word}`", parse_mode='Markdown')
        else:
            await message.reply_text("⚠️ هذه الكلمة موجودة بالفعل.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== مراقبة الكلمات - حذف كلمة =====
    elif state == "ww_words_remove":
        ww = bot_data["word_watch"]
        word = text.strip()
        if word in ww["words"]:
            ww["words"].remove(word)
            save_data(bot_data)
            await message.reply_text(f"✅ تم حذف الكلمة: `{word}`", parse_mode='Markdown')
        else:
            await message.reply_text("⚠️ هذه الكلمة غير موجودة.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== مراقبة الكلمات - إضافة مشرف تنبيه =====
    elif state.startswith("ww_alert_add_"):
        group_id = state.replace("ww_alert_add_", "")
        username = text.strip()
        if username.startswith("@"):
            username = username[1:]
        if username:
            ww = bot_data["word_watch"]
            if group_id not in ww["alert_admins"]:
                ww["alert_admins"][group_id] = []
            if username not in ww["alert_admins"][group_id]:
                ww["alert_admins"][group_id].append(username)
                save_data(bot_data)
                await message.reply_text(f"✅ تم إضافة `@{username}` كمشرف تنبيه.", parse_mode='Markdown')
            else:
                await message.reply_text("⚠️ هذا المشرف موجود بالفعل.")
        else:
            await message.reply_text("❌ يرجى إرسال اسم مستخدم صحيح.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== الاشتراك الإجباري - تعيين القناة =====
    elif state.startswith("fs_set_channel_"):
        group_id = state.replace("fs_set_channel_", "")
        # ننتظر إعادة توجيه رسالة من القناة في معالج الرسائل
        WAITING_STATES[user_id] = f"fs_forward_{group_id}"
        await message.reply_text(
            "📢 **يرجى إعادة توجيه رسالة من القناة**\n\n"
            "قم بإعادة توجيه أي رسالة من القناة التي تريد فرض الاشتراك بها.\n\n"
            "📌 تأكد من أن البوت مشرف في تلك القناة.",
            parse_mode='Markdown'
        )
        return
    
    # ===== الاشتراك الإجباري - تعيين رابط الدعوة =====
    elif state.startswith("fs_set_link_"):
        group_id = state.replace("fs_set_link_", "")
        link = text.strip()
        fs_groups = bot_data.get("force_sub_groups", {})
        if group_id in fs_groups:
            fs_groups[group_id]["invite_link"] = link
            fs_groups[group_id]["updated_at"] = datetime.now().isoformat()
            save_data(bot_data)
            await message.reply_text("✅ تم ضبط رابط الدعوة بنجاح.")
        WAITING_STATES.pop(user_id, None)
        return
    
    # ===== كتم مستخدم - تحديد الهدف =====
    elif state.startswith("mute_target_"):
        group_id = state.replace("mute_target_", "")
        # إذا كان النص رقماً، نعتبره ID مستخدم
        try:
            target_user_id = int(text.strip())
            # تخزين مؤقت لبيانات الكتم
            TEMP_MUTE[user_id] = {
                "target_user_id": target_user_id,
                "target_user_name": f"مستخدم #{target_user_id}",
                "chat_id": int(group_id)
            }
            WAITING_STATES[user_id] = f"mute_duration_{group_id}"
            await message.reply_text(
                "🔇 **اختر مدة الكتم**\n\n"
                "سيتم عرض خيارات المدة المتاحة.",
                reply_markup=get_mute_durations_keyboard(bot_data, group_id)
            )
            return
        except ValueError:
            # إذا لم يكن رقماً، ربما رد على رسالة
            await message.reply_text(
                "❌ يرجى إرسال معرف المستخدم (ID) كرقم،\n"
                "أو قم بالرد على رسالة المستخدم في المجموعة.\n\n"
                "📌 استخدم الأمر `/mute` للرد على رسالة المستخدم مباشرة."
            )
            WAITING_STATES.pop(user_id, None)
            return


async def send_broadcast_from_message(update, context, reply_markup):
    """يرسل الإذاعة المحفوظة في TEMP_BROADCAST من رسالة عادية"""
    user_id = update.effective_user.id
    broadcast_data = TEMP_BROADCAST.get(user_id)
    
    if not broadcast_data:
        await update.message.reply_text("⚠️ لا توجد إذاعة معلقة.")
        return
    
    text = broadcast_data.get("text")
    broadcast_type = broadcast_data.get("type")
    target = broadcast_data.get("target")
    
    bot_data = load_data()
    success_count = 0
    fail_count = 0
    
    if broadcast_type == "bc_all":
        for group_id in bot_data.get("groups", {}).keys():
            try:
                await context.bot.send_message(int(group_id), text, reply_markup=reply_markup, parse_mode='Markdown')
                success_count += 1
            except Exception:
                fail_count += 1
    
    elif broadcast_type == "bc_single":
        try:
            await context.bot.send_message(int(target), text, reply_markup=reply_markup, parse_mode='Markdown')
            success_count = 1
        except Exception:
            fail_count = 1
    
    elif broadcast_type == "bc_users":
        for user_id in bot_data.get("users", []):
            try:
                await context.bot.send_message(user_id, text, reply_markup=reply_markup, parse_mode='Markdown')
                success_count += 1
            except Exception:
                fail_count += 1
    
    # تنظيف البيانات المؤقتة
    TEMP_BROADCAST.pop(user_id, None)
    
    await update.message.reply_text(
        f"✅ **تم إرسال الإذاعة**\n\n"
        f"📤 ناجحة: {success_count}\n"
        f"📥 فاشلة: {fail_count}",
        parse_mode='Markdown'
    )


def get_mute_durations_keyboard(bot_data, group_id):
    """لوحة اختيار مدة الكتم"""
    durations = get_all_mute_durations(bot_data)
    keyboard = []
    row = []
    for i, (label, seconds) in enumerate(durations):
        row.append(InlineKeyboardButton(label, callback_data=f"mute_dur_{group_id}_{seconds}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 إلغاء", callback_data="manage_mute")])
    return InlineKeyboardMarkup(keyboard)


# === معالجة إعادة توجيه القناة للاشتراك الإجباري ===
async def handle_forwarded_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إعادة توجيه رسالة القناة لتعيينها كنظام اشتراك إجباري"""
    user_id = update.effective_user.id
    message = update.effective_message
    
    if user_id not in WAITING_STATES:
        return
    
    state = WAITING_STATES.get(user_id)
    if not state.startswith("fs_forward_"):
        return
    
    group_id = state.replace("fs_forward_", "")
    channel_chat = extract_forwarded_channel(message)
    
    if not channel_chat:
        await message.reply_text(
            "❌ **لم يتم التعرف على القناة**\n\n"
            "الرجاء إعادة توجيه رسالة **من القناة** نفسها، وليس من مجموعة أو حساب شخصي.",
            parse_mode='Markdown'
        )
        return
    
    # استخراج معلومات القناة
    channel_id = channel_chat.id
    channel_title = channel_chat.title or "بدون عنوان"
    channel_username = channel_chat.username or ""
    
    # حفظ المعلومات في قاعدة البيانات
    bot_data = load_data()
    fs_groups = bot_data.get("force_sub_groups", {})
    
    if group_id not in fs_groups:
        fs_groups[group_id] = {}
    
    fs_groups[group_id]["channel_id"] = channel_id
    fs_groups[group_id]["channel_title"] = channel_title
    fs_groups[group_id]["channel_username"] = channel_username
    fs_groups[group_id]["enabled"] = True
    fs_groups[group_id]["updated_at"] = datetime.now().isoformat()
    
    save_data(bot_data)
    WAITING_STATES.pop(user_id, None)
    
    await message.reply_text(
        f"✅ **تم تعيين القناة بنجاح**\n\n"
        f"📢 **القناة:** {channel_title}\n"
        f"🆔 **المعرف:** {channel_id}\n"
        f"🔗 **الرابط:** {'@' + channel_username if channel_username else 'غير متوفر'}\n\n"
        f"📌 تم تفعيل نظام الاشتراك الإجباري لهذه المجموعة.",
        parse_mode='Markdown'
    )


# === نظام الاشتراك الإجباري (مع إضافة Mention حقيقي) ===
async def handle_force_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من الاشتراك الإجباري وإرسال رسالة مع Mention حقيقي للمستخدم غير المشترك"""
    chat_id = update.effective_chat.id
    user = update.effective_user
    message = update.effective_message
    
    bot_data = load_data()
    fs_groups = bot_data.get("force_sub_groups", {})
    group_id = str(chat_id)
    
    # التحقق من وجود إعداد للاشتراك الإجباري لهذه المجموعة
    if group_id not in fs_groups:
        return
    
    config = fs_groups[group_id]
    if not config.get("enabled", False):
        return
    
    channel_id = config.get("channel_id")
    if not channel_id:
        return
    
    # التحقق من اشتراك المستخدم في القناة
    try:
        chat_member = await context.bot.get_chat_member(channel_id, user.id)
        if chat_member.status in ["member", "administrator", "creator"]:
            return  # المستخدم مشترك، نسمح له بالكتابة
    except Exception:
        # إذا حدث خطأ (مثل عدم وجود البوت في القناة)، نسمح بالكتابة لتجنب التعطيل
        return
    
    # المستخدم غير مشترك → نمنعه من الكتابة ونرسل له رسالة مع Mention حقيقي
    try:
        # حذف الرسالة المخالفة
        await message.delete()
    except Exception:
        pass
    
    # بناء Mention حقيقي للمستخدم باستخدام HTML
    user_mention = build_user_mention_html(user)
    
    # الحصول على معلومات القناة
    channel_title = config.get("channel_title", "القناة")
    invite_link = config.get("invite_link", "")
    
    # بناء الرسالة بصيغة HTML مع Mention حقيقي
    if invite_link:
        sub_text = (
            f"{user_mention}، <b>يجب عليك الاشتراك في القناة أولاً</b> قبل المشاركة في هذه المجموعة.\n\n"
            f"📢 <b>القناة المطلوبة:</b> {html.escape(channel_title)}\n"
            f"🔗 <b>رابط الاشتراك:</b> {invite_link}\n\n"
            "✉️ بعد الاشتراك، أعد المحاولة."
        )
    else:
        sub_text = (
            f"{user_mention}، <b>يجب عليك الاشتراك في القناة أولاً</b> قبل المشاركة في هذه المجموعة.\n\n"
            f"📢 <b>القناة المطلوبة:</b> {html.escape(channel_title)}\n\n"
            "✉️ بعد الاشتراك، أعد المحاولة."
        )
    
    # إرسال الرسالة مع Mention حقيقي
    try:
        await context.bot.send_message(
            chat_id,
            sub_text,
            parse_mode='HTML'
        )
    except Exception as e:
        # إذا فشل HTML، نحاول إرسالها كنص عادي
        logging.error(f"فشل إرسال رسالة الاشتراك الإجباري بصيغة HTML: {e}")
        try:
            # محاولة إرسالها بدون HTML
            fallback_text = f"@{user.username or user.first_name}، يجب عليك الاشتراك في القناة أولاً قبل المشاركة."
            await context.bot.send_message(
                chat_id,
                fallback_text
            )
        except Exception as e2:
            logging.error(f"فشل إرسال رسالة الاشتراك الإجباري كنص عادي: {e2}")


# === نظام مراقبة الكلمات ===
async def handle_word_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مراقبة الكلمات المحددة وتنبيه المشرفين"""
    chat_id = update.effective_chat.id
    user = update.effective_user
    message = update.effective_message
    text = message.text or message.caption or ""
    
    bot_data = load_data()
    ww = bot_data.get("word_watch", {})
    group_id = str(chat_id)
    
    # التحقق من تفعيل المراقبة لهذه المجموعة
    if group_id not in ww.get("target_groups", {}):
        return
    
    if not ww["target_groups"].get(group_id, False):
        return
    
    # البحث عن كلمات مراقبة في النص
    watched_words = ww.get("words", [])
    if not watched_words:
        return
    
    detected_word = detect_watched_word(text, watched_words)
    if not detected_word:
        return
    
    # تم العثور على كلمة مراقبة → حذف الرسالة
    try:
        await message.delete()
    except Exception:
        pass
    
    # تنبيه المشرفين
    alert_admins = ww.get("alert_admins", {}).get(group_id, [])
    if alert_admins:
        mention = build_user_mention_html(user)
        alert_text = (
            f"🔔 <b>تنبيه: تم رصد كلمة مراقبة</b>\n\n"
            f"👤 <b>المستخدم:</b> {mention}\n"
            f"🔎 <b>الكلمة المكتشفة:</b> <code>{html.escape(detected_word)}</code>\n"
            f"📝 <b>النص:</b> {html.escape(text[:100])}\n\n"
            f"🆔 <b>المجموعة:</b> {html.escape(update.effective_chat.title)}"
        )
        
        for admin_username in alert_admins:
            try:
                # إرسال تنبيه للمشرف في الخاص
                await context.bot.send_message(
                    f"@{admin_username}",
                    alert_text,
                    parse_mode='HTML'
                )
            except Exception:
                pass


# === نظام الوضع الصامت ===
async def handle_silent_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من الوضع الصامت ومنع الكتابة إذا كان مفعلاً"""
    chat_id = update.effective_chat.id
    user = update.effective_user
    message = update.effective_message
    
    bot_data = load_data()
    group_id = str(chat_id)
    
    # التحقق من الوضع الصامت اليومي
    daily = bot_data.get("daily_silent_mode", {})
    if daily.get("enabled", False):
        target_group = daily.get("target_group")
        if target_group is None or int(target_group) == chat_id:
            # التحقق من الوقت
            start_time = daily.get("start_time", "23:00")
            end_time = daily.get("end_time", "07:00")
            now = datetime.now().time()
            start = datetime.strptime(start_time, "%H:%M").time()
            end = datetime.strptime(end_time, "%H:%M").time()
            
            is_silent = False
            if start < end:
                is_silent = start <= now <= end
            else:
                is_silent = now >= start or now <= end
            
            if is_silent:
                try:
                    await message.delete()
                    await context.bot.send_message(
                        chat_id,
                        daily.get("custom_message", "🔇 المجموعة في الوضع الصامت اليومي."),
                        parse_mode='Markdown'
                    )
                except Exception:
                    pass
                return
    
    # التحقق من الوضع الصامت المؤقت
    silent = bot_data.get("silent_mode", {})
    if silent.get("enabled", False):
        target_group = silent.get("target_group")
        if target_group is None or int(target_group) == chat_id:
            # التحقق من انتهاء المدة المؤقتة
            if silent.get("is_temporary", False):
                until_ts = silent.get("temp_until_timestamp", 0)
                if datetime.now().timestamp() > until_ts:
                    # انتهت المدة، تعطيل الوضع الصامت
                    silent["enabled"] = False
                    silent["is_temporary"] = False
                    save_data(bot_data)
                    return
            
            try:
                await message.delete()
                await context.bot.send_message(
                    chat_id,
                    silent.get("custom_message", "🔇 المجموعة في الوضع الصامت."),
                    parse_mode='Markdown'
                )
            except Exception:
                pass


# === نظام حظر الإيموجيات ===
async def handle_emoji_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف الإيموجيات المحظورة"""
    message = update.effective_message
    text = message.text or ""
    
    bot_data = load_data()
    banned_emojis = bot_data.get("emojis", [])
    
    if not banned_emojis:
        return
    
    # التحقق من وجود أي إيموجي محظور في النص
    for emoji in banned_emojis:
        if emoji in text:
            try:
                await message.delete()
            except Exception:
                pass
            return


# === نظام حظر الكلمات ===
async def handle_word_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف الكلمات المحظورة"""
    message = update.effective_message
    text = message.text or message.caption or ""
    
    bot_data = load_data()
    banned_words = bot_data.get("words", [])
    
    if not banned_words:
        return
    
    # التحقق من وجود أي كلمة محظورة في النص
    text_lower = text.lower()
    for word in banned_words:
        if word.lower() in text_lower:
            try:
                await message.delete()
            except Exception:
                pass
            return


# === نظام حظر الروابط ===
async def handle_link_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف الروابط المحظورة"""
    message = update.effective_message
    text = message.text or message.caption or ""
    
    if not text:
        return
    
    bot_data = load_data()
    
    # حظر جميع الروابط
    if bot_data.get("block_all_links", False):
        # البحث عن روابط في النص
        url_pattern = r'https?://[^\s]+|www\.[^\s]+|t\.me/[^\s]+|telegram\.me/[^\s]+'
        if re.search(url_pattern, text, re.IGNORECASE):
            try:
                await message.delete()
            except Exception:
                pass
            return
    
    # حظر روابط محددة
    banned_links = bot_data.get("banned_links", [])
    if banned_links:
        text_lower = text.lower()
        for link in banned_links:
            if link.lower() in text_lower:
                try:
                    await message.delete()
                except Exception:
                    pass
                return


# === نظام حظر الملصقات ===
async def handle_sticker_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف الملصقات والصور المتحركة المحظورة"""
    message = update.effective_message
    
    bot_data = load_data()
    
    # حظر جميع الملصقات
    if bot_data.get("block_stickers", False) and message.sticker:
        try:
            await message.delete()
        except Exception:
            pass
        return
    
    # حظر الملصقات المتحركة
    if bot_data.get("block_animated_stickers", False):
        if message.sticker and message.sticker.is_animated:
            try:
                await message.delete()
            except Exception:
                pass
            return
        
        # حظر GIF
        if message.animation:
            try:
                await message.delete()
            except Exception:
                pass
            return


# === معالج تغيير حالة المشرفين (إشعار عند إضافة البوت كمشرف) ===
async def handle_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    معالج تغيير حالة أعضاء المجموعة.
    يرسل إشعاراً للمشرفين عند إضافة البوت أو ترقيته كمشرف في مجموعة.
    """
    chat_member_update = update.chat_member
    
    # التأكد من أن التحديث خاص بالبوت نفسه
    if chat_member_update.new_chat_member.user.id != context.bot.id:
        return
    
    # التحقق من أن البوت أصبح مشرفاً
    new_status = chat_member_update.new_chat_member.status
    if new_status not in ["administrator", "creator"]:
        return
    
    chat = update.effective_chat
    chat_id = chat.id
    chat_title = chat.title or "بدون عنوان"
    chat_username = chat.username or "غير متوفر"
    
    # معلومات الشخص الذي قام بالإضافة/الترقية
    inviter = chat_member_update.from_user
    inviter_name = inviter.full_name or "غير معروف"
    inviter_username = f"@{inviter.username}" if inviter.username else "غير متوفر"
    inviter_id = inviter.id
    
    # بناء رسالة الإشعار
    notification_text = (
        f"🔔 **إشعار: تم إضافة البوت كمشرف**\n\n"
        f"📌 **المجموعة:** {chat_title}\n"
        f"🆔 **معرف المجموعة:** `{chat_id}`\n"
        f"🔗 **الرابط:** @{chat_username if chat_username != 'غير متوفر' else 'غير متوفر'}\n\n"
        f"👤 **تمت الإضافة بواسطة:**\n"
        f"• الاسم: {inviter_name}\n"
        f"• المعرف: {inviter_username}\n"
        f"• الرقم: `{inviter_id}`\n\n"
        f"📅 **التاريخ والوقت:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"✅ أصبح البوت مشرفاً في هذه المجموعة."
    )
    
    # إرسال الإشعار لجميع المشرفين المسجلين في البوت
    bot_data = load_data()
    admins = bot_data.get("admins", [])
    
    for admin_id in admins:
        try:
            await context.bot.send_message(
                admin_id,
                notification_text,
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"فشل إرسال إشعار للمشرف {admin_id}: {e}")


# === الأوامر الإضافية ===
async def add_word_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة كلمة محظورة"""
    user_id = update.effective_user.id
    if not is_bot_admin(user_id):
        await update.message.reply_text("⛔ هذا الأمر مخصص للمشرفين فقط.")
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ يرجى تحديد الكلمة.\nمثال: `/addword كلمة`")
        return
    
    word = " ".join(context.args)
    WAITING_STATES[user_id] = "add_word"
    await update.message.reply_text(f"لتأكيد إضافة الكلمة: `{word}`، أرسلها مرة أخرى.", parse_mode='Markdown')


async def del_word_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف كلمة محظورة"""
    user_id = update.effective_user.id
    if not is_bot_admin(user_id):
        await update.message.reply_text("⛔ هذا الأمر مخصص للمشرفين فقط.")
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ يرجى تحديد الكلمة.\nمثال: `/delword كلمة`")
        return
    
    word = " ".join(context.args)
    WAITING_STATES[user_id] = "del_word"
    await update.message.reply_text(f"لتأكيد حذف الكلمة: `{word}`، أرسلها مرة أخرى.", parse_mode='Markdown')


async def add_emoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة إيموجي محظور"""
    user_id = update.effective_user.id
    if not is_bot_admin(user_id):
        await update.message.reply_text("⛔ هذا الأمر مخصص للمشرفين فقط.")
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ يرجى تحديد الإيموجي.\nمثال: `/addemoji 😂`")
        return
    
    emoji = " ".join(context.args)
    WAITING_STATES[user_id] = "add_emoji"
    await update.message.reply_text(f"لتأكيد إضافة الإيموجي: {emoji}، أرسله مرة أخرى.")


async def del_emoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف إيموجي محظور"""
    user_id = update.effective_user.id
    if not is_bot_admin(user_id):
        await update.message.reply_text("⛔ هذا الأمر مخصص للمشرفين فقط.")
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ يرجى تحديد الإيموجي.\nمثال: `/delemoji 😂`")
        return
    
    emoji = " ".join(context.args)
    WAITING_STATES[user_id] = "del_emoji"
    await update.message.reply_text(f"لتأكيد حذف الإيموجي: {emoji}، أرسله مرة أخرى.")


async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """كتم مستخدم بالرد على رسالته"""
    user_id = update.effective_user.id
    
    if not is_bot_admin(user_id):
        await update.message.reply_text("⛔ هذا الأمر مخصص للمشرفين فقط.")
        return
    
    # التحقق من الرد على رسالة
    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text(
            "⚠️ يرجى الرد على رسالة المستخدم الذي تريد كتمه.\n"
            "مثال: قم بالرد على رسالته وأرسل `/mute`"
        )
        return
    
    target_user = reply.from_user
    chat_id = update.effective_chat.id
    group_id = str(chat_id)
    
    # تخزين بيانات الكتم مؤقتاً
    TEMP_MUTE[user_id] = {
        "target_user_id": target_user.id,
        "target_user_name": target_user.full_name or "مستخدم",
        "chat_id": chat_id
    }
    
    WAITING_STATES[user_id] = f"mute_duration_{group_id}"
    bot_data = load_data()
    await update.message.reply_text(
        "🔇 **اختر مدة الكتم**",
        reply_markup=get_mute_durations_keyboard(bot_data, group_id)
    )


async def mute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج اختيار مدة الكتم من الأزرار"""
    query = update.callback_query
    user_id = update.effective_user.id
    data = query.data
    
    if not data.startswith("mute_dur_"):
        await query.answer()
        return
    
    await query.answer()
    
    # استخراج البيانات
    parts = data.replace("mute_dur_", "").split("_")
    group_id = parts[0]
    seconds = int(parts[1])
    
    # استرجاع بيانات الكتم المؤقتة
    mute_data = TEMP_MUTE.get(user_id)
    if not mute_data:
        await query.edit_message_text("⚠️ انتهت صلاحية العملية. أعد المحاولة.")
        return
    
    target_user_id = mute_data["target_user_id"]
    target_user_name = mute_data["target_user_name"]
    chat_id = mute_data["chat_id"]
    
    try:
        # كتم المستخدم
        permissions = ChatPermissions(
            can_send_messages=False,
            can_send_media_messages=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False
        )
        until_date = datetime.now() + timedelta(seconds=seconds)
        
        await context.bot.restrict_chat_member(
            chat_id,
            target_user_id,
            permissions=permissions,
            until_date=until_date
        )
        
        # تسجيل الكتم في قاعدة البيانات
        bot_data = load_data()
        register_active_mute(
            bot_data,
            chat_id,
            target_user_id,
            target_user_name,
            until_date.timestamp()
        )
        
        duration_label = format_mute_duration_label(seconds)
        await query.edit_message_text(
            f"✅ **تم كتم المستخدم**\n\n"
            f"👤 الاسم: {target_user_name}\n"
            f"🆔 المعرف: `{target_user_id}`\n"
            f"⏱️ المدة: {duration_label}",
            parse_mode='Markdown'
        )
        
        # إرسال إشعار للمستخدم المكتم (إذا أمكن)
        try:
            await context.bot.send_message(
                target_user_id,
                f"🔇 **تم كتمك في المجموعة**\n\n"
                f"المدة: {duration_label}\n"
                f"المجموعة: {update.effective_chat.title}\n\n"
                "الرجاء الالتزام بقوانين المجموعة."
            )
        except Exception:
            pass
        
    except Exception as e:
        await query.edit_message_text(f"❌ فشل كتم المستخدم: {str(e)}")
    
    # تنظيف البيانات المؤقتة
    TEMP_MUTE.pop(user_id, None)
    WAITING_STATES.pop(user_id, None)


# === الوظيفة الرئيسية ===
async def main():
    # بدء سيرفر الويب
    await start_web_server()
    
    # إنشاء التطبيق
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # إضافة معالج الأوامر
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("addword", add_word_command))
    application.add_handler(CommandHandler("delword", del_word_command))
    application.add_handler(CommandHandler("addemoji", add_emoji_command))
    application.add_handler(CommandHandler("delemoji", del_emoji_command))
    application.add_handler(CommandHandler("mute", mute_command))
    
    # إضافة معالج الكولباك
    application.add_handler(CallbackQueryHandler(admin_callback))
    application.add_handler(CallbackQueryHandler(mute_callback, pattern=r"^mute_dur_"))
    
    # إضافة معالج الرسائل
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker_filter))
    application.add_handler(MessageHandler(filters.ANIMATION, handle_sticker_filter))
    application.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded_channel))
    
    # إضافة معالج تغيير حالة المشرفين (لإشعار إضافة البوت كمشرف)
    application.add_handler(ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    
    # بدء حلقة مراقبة الكتمات
    asyncio.create_task(mute_watcher_loop(application))
    
    # بدء البوت
    logging.info("Starting bot...")
    await application.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
