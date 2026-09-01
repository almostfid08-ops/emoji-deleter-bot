import os
import logging
import asyncio
import json
import re
import html
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

NON_ADMIN_INFO_TEXT = (
    "🤖 **مرحباً بك!**\n\n"
    "هذا البوت مُصمَّم ليعمل كـ**مشرف داخل مجموعات الدراسة**، ومهامه:\n\n"
    "• 🚫 حذف الإيموجيات غير المرغوب بها.\n"
    "• 🚫 حذف الكلمات والروابط الممنوعة.\n"
    "• 🚫 حذف الملصقات (الستيكرز) والصور المتحركة GIF الممنوعة.\n"
    "• 🔇 تفعيل وضع صامت في أوقات محددة، أو تلقائياً عند نداء استغاثة من الأعضاء.\n"
    "• 📢 فرض الاشتراك الإجباري في قناة محددة قبل السماح بالكتابة.\n"
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
            # إضافة حقل جديد لتحديد ما إذا كان الوضع الصامت مؤقتاً أم يومياً
            "is_temporary": False,
            # حقل لتخزين وقت انتهاء الوضع الصامت المؤقت بشكل منفصل
            "temp_until_timestamp": 0
        },
        # نظام الوضع الصامت اليومي الجديد (مستقل تماماً)
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
        # إعدادات الاشتراك الإجباري: كل مجموعة (بالمعرّف كـ نص) لها إعداد مستقل خاص بها
        # المفتاح = group_id (str) → { channel_id, channel_title, channel_username, invite_link,
        #                              enabled, created_at, updated_at }
        "force_sub_groups": {},

        # نظام مراقبة الكلمات والعبارات (ميزة جديدة)
        "word_watch": {
            # group_id (str) → True/False (مفعّل أم لا)
            "target_groups": {},
            # قائمة الكلمات/العبارات المراقبة
            "words": list(DEFAULT_WATCH_WORDS),
            # group_id (str) → قائمة أسماء المستخدمين (@username) الذين يجب تنبيههم
            "alert_admins": {}
        },

        # نظام كتم المستخدمين (ميزة جديدة)
        # مدد مخصصة يضيفها الأدمن: [{"label": str, "seconds": int}]
        "custom_mute_durations": [],
        # كتمات مجدولة (لضمان عدم ضياعها عند إعادة تشغيل البوت):
        # [{"chat_id":, "user_id":, "user_name":, "until_ts":}]
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

                # إضافة نظام الوضع الصامت اليومي الجديد
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

                # الاشتراك الإجباري (ميزة جديدة) - لا يؤثر على أي إعداد قديم
                data.setdefault("force_sub_groups", {})

                # مراقبة الكلمات (ميزة جديدة) - لا يؤثر على أي إعداد قديم
                data.setdefault("word_watch", {})
                data["word_watch"].setdefault("target_groups", {})
                data["word_watch"].setdefault("words", list(DEFAULT_WATCH_WORDS))
                data["word_watch"].setdefault("alert_admins", {})

                # نظام الكتم (ميزة جديدة) - لا يؤثر على أي إعداد قديم
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
    """
    يهرّب الرموز الخاصة بصيغة Markdown القديمة (legacy) حتى لا تتسبب عناوين القنوات/المجموعات
    (التي لا نتحكم بمحتواها) في كسر تنسيق الرسالة وفشل الإرسال بصمت.
    الرموز الخاصة في هذا الوضع: _ * ` [ ]
    """
    if not text:
        return text
    return re.sub(r'([_*`\[\]])', r'\\\1', str(text))


async def safe_edit_text(message, text, reply_markup=None, parse_mode='Markdown'):
    """يحاول تعديل الرسالة بصيغة Markdown، وإذا فشل (لأي سبب) يعيد المحاولة كنص عادي بدل الفشل الصامت."""
    try:
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception:
        try:
            await message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            logging.exception("فشل تعديل الرسالة (safe_edit_text)")


async def safe_reply_text(message, text, reply_markup=None, parse_mode='Markdown'):
    """يحاول الرد بصيغة Markdown، وإذا فشل (لأي سبب) يعيد المحاولة كنص عادي بدل الفشل الصامت."""
    try:
        await message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception:
        try:
            await message.reply_text(text, reply_markup=reply_markup)
        except Exception:
            logging.exception("فشل إرسال الرد (safe_reply_text)")


def extract_forwarded_channel(message):
    """
    يحاول استخراج معلومات القناة (Chat) من رسالة مُعاد توجيهها (Forward).
    يدعم كلاً من:
    - الحقل الحديث forward_origin (Bot API 7.0+) عندما يكون النوع MessageOriginChannel.
    - الحقل القديم forward_from_chat (متوافقية رجعية مع نسخ أقدم).
    يعيد كائن Chat الخاص بالقناة إذا وُجد، أو None إذا لم تكن الرسالة توجيهاً صالحاً من قناة.
    """
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
    """
    يبني نمط Regex للكلمة/العبارة يعتمد على حدود كلمات حقيقية (word boundaries)
    لتفادي التطابقات الخاطئة (مثل اكتشاف 'group' داخل كلمة أخرى غير مرتبطة)،
    مع مراعاة عدم حساسية الأحرف الكبيرة/الصغيرة بالنسبة للفرنسية/الإنجليزية.
    """
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
    """
    يفحص النص بحثاً عن أول كلمة/عبارة مستهدفة موجودة فيه (سواء وحدها، داخل جملة،
    في البداية/الوسط/النهاية، أو بجانب علامات الترقيم). يعيد الكلمة المكتشفة أو None.
    """
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
    """يجمع المدد الجاهزة الافتراضية مع المدد المخصصة التي أضافها الأدمن."""
    durations = list(DEFAULT_MUTE_DURATIONS)
    for item in bot_data.get("custom_mute_durations", []):
        durations.append((item.get("label"), item.get("seconds")))
    return durations


def format_mute_duration_label(seconds):
    """يحوّل عدد الثواني إلى نص عربي مقروء لعرضه في رسالة الكتم التلقائية."""
    if seconds % (24 * 60 * 60) == 0 and seconds >= 24 * 60 * 60:
        days = seconds // (24 * 60 * 60)
        return f"{days} يوم" if days == 1 else f"{days} أيام"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} ساعة" if hours == 1 else f"{hours} ساعات"
    minutes = max(1, seconds // 60)
    return f"{minutes} دقيقة"


def register_active_mute(bot_data, chat_id, user_id, user_name, until_ts):
    """يحفظ سجل الكتم في قاعدة البيانات حتى لا يضيع النظام عند إعادة تشغيل البوت."""
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
    """
    حلقة خلفية دائمة تتحقق دورياً من الكتمات المجدولة المحفوظة، وترفع الكتم فعلياً
    عن أي مستخدم انتهت مدته (حتى إن أعيد تشغيل البوت أثناء فترة الكتم، فالبيانات محفوظة في الملف).
    """
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
    
    # تحديد نوع الوضع الصامت الحالي
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
    """لوحة إدارة الوضع الصامت اليومي"""
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


# ---------- لوحات مراقبة الكلمات (ميزة جديدة) ----------
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
        keyboard.append
