import os
import logging
import asyncio
import json
import re
import html
from datetime import datetime, timedelta
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions, MessageEntity
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
    "🤖 **مرحباً بك في بوت الإشراف للمجموعات الدراسية!**\n\n"
    "البوت مخصص للعمل داخل مجموعات الدراسة للمساعدة في تنظيم المجموعة والمحافظة على بيئة هادئة ومنظمة.\n\n"
    "🛡️ **أهم الوظائف المتاحة داخل المجموعة:**\n"
    "• 🚫 حذف الإيموجيات المحظورة التي يتم تحديدها من لوحة الإدارة.\n"
    "• ⛔ حذف الكلمات والعبارات المحظورة.\n"
    "• 🔗 حظر الروابط المحظورة أو حظر جميع الروابط بحسب إعدادات المجموعة.\n"
    "• 🎭 حظر الملصقات، مع إمكانية حظر الملصقات المتحركة وGIF بشكل مستقل.\n"
    "• 🔇 تطبيق الوضع الصامت على المجموعة المستهدفة، سواء وفق جدول زمني أو لمدة مؤقتة.\n"
    "• 🆘 تفعيل الوضع الصامت تلقائياً عند تكرار كلمة/عبارة نداء الاستغاثة بالعدد المحدد.\n"
    "• 📢 تطبيق الاشتراك الإجباري في قناة محددة للمجموعة قبل السماح للعضو بإرسال الرسائل.\n"
    "• 🔎 مراقبة كلمات وعبارات محددة داخل المجموعات المستهدفة وتنبيه المشرفين المحددين عند رصدها.\n"
    "• 🔇 إمكانية كتم المستخدمين من طرف المشرفين بمدد جاهزة أو مدد مخصصة، مع دعم الكتمات المجدولة.\n\n"
    "📌 **للمشرفين:** توجد لوحة تحكم لإدارة الإذاعات، المجموعات، الوضع الصامت، نداء الاستغاثة، الاشتراك الإجباري، مراقبة الكلمات، الكتم، الكلمات والروابط والإيموجيات والملصقات، بالإضافة إلى إدارة مشرفي البوت.\n"
    "كما يدعم البوت أوامر الإشراف الخاصة بالحظر والكتم وإلغاء الحظر وإلغاء الكتم بحسب الصلاحيات المتاحة.\n\n"
    f"للحصول على هذه الميزات داخل مجموعتك، يرجى التواصل مع مطور البوت {DEVELOPER_USERNAME} "
    "كي يقوم بإضافة البوت كمشرف في مجموعتك وتهيئته لاستخدام الميزات المطلوبة."
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
        keyboard.append([InlineKeyboardButton(f"👥 {gname}", callback_data=f"ww_alert_admins_group_{gid}")])
    if not keyboard:
        keyboard.append([InlineKeyboardButton("لا توجد مجموعات مستهدفة بعد", callback_data="ww_targets")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="manage_word_watch")])
    return InlineKeyboardMarkup(keyboard)
def get_ww_alert_admins_manage_keyboard(gid, admins_list):
    keyboard = []
    for idx, username in enumerate(admins_list):
        keyboard.append([InlineKeyboardButton(f"🗑️ {username}", callback_data=f"ww_alert_admins_remove_{gid}_{idx}")])
    keyboard.append([InlineKeyboardButton("➕ إضافة مشرف تنبيه", callback_data=f"ww_alert_admins_add_{gid}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="ww_alert_admins")])
    return InlineKeyboardMarkup(keyboard)
# ---------- لوحات نظام كتم المستخدمين (ميزة جديدة) ----------
def get_mute_durations_keyboard(bot_data):
    durations = get_all_mute_durations(bot_data)
    keyboard = []
    row = []
    for label, seconds in durations:
        row.append(InlineKeyboardButton(label, callback_data=f"mute_dur_{seconds}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("➕ إضافة مدة مخصصة", callback_data="mute_add_duration")])
    if bot_data.get("custom_mute_durations"):
        keyboard.append([InlineKeyboardButton("🗑️ حذف مدة مخصصة", callback_data="mute_remove_duration")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)
def get_mute_remove_duration_keyboard(bot_data):
    keyboard = []
    for idx, item in enumerate(bot_data.get("custom_mute_durations", [])):
        keyboard.append([InlineKeyboardButton(f"🗑️ {item.get('label')}", callback_data=f"mute_remove_duration_{idx}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="manage_mute")])
    return InlineKeyboardMarkup(keyboard)
# ---------- لوحات الاشتراك الإجباري (ميزة جديدة) ----------
def get_force_sub_list_keyboard(data):
    fs_groups = data.get("force_sub_groups", {})
    groups = data.get("groups", {})
    keyboard = []
    for gid, cfg in fs_groups.items():
        gname = groups.get(gid, f"مجموعة #{gid}")
        status_icon = "🟢" if cfg.get("enabled") else "🔴"
        keyboard.append([InlineKeyboardButton(f"{status_icon} {gname}", callback_data=f"fs_manage_{gid}")])
    keyboard.append([InlineKeyboardButton("➕ إضافة مجموعة جديدة", callback_data="fs_add_new")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)
def get_force_sub_new_group_keyboard(data):
    groups = data.get("groups", {})
    keyboard = []
    for gid, title in groups.items():
        keyboard.append([InlineKeyboardButton(f"👥 {title}", callback_data=f"fs_new_group_{gid}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="manage_force_sub")])
    return InlineKeyboardMarkup(keyboard)
def get_force_sub_manage_keyboard(gid, cfg):
    status = "🟢 مفعل" if cfg.get("enabled") else "🔴 معطل"
    keyboard = [
        [InlineKeyboardButton(f"الحالة: {status} (اضغط للتبديل)", callback_data=f"fs_toggle_{gid}")],
    ]
    # زر واضح ومباشر لإلغاء (تعطيل) الاشتراك الإجباري لهذه المجموعة تحديداً، بدون حذف الإعدادات المحفوظة
    if cfg.get("enabled"):
        keyboard.append([InlineKeyboardButton("❌ إلغاء الاشتراك الإجباري لهذه المجموعة", callback_data=f"fs_disable_{gid}")])
    keyboard.append([InlineKeyboardButton("✏️ تعديل/تغيير القناة", callback_data=f"fs_edit_channel_{gid}")])
    keyboard.append([InlineKeyboardButton("🗑️ حذف الإعداد نهائياً", callback_data=f"fs_delete_{gid}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع لقائمة الاشتراك الإجباري", callback_data="manage_force_sub")])
    return InlineKeyboardMarkup(keyboard)
def get_force_sub_delete_confirm_keyboard(gid):
    keyboard = [
        [InlineKeyboardButton("✅ نعم، احذف الإعداد", callback_data=f"fs_delete_confirm_{gid}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"fs_manage_{gid}")]
    ]
    return InlineKeyboardMarkup(keyboard)
def format_force_sub_info(gid, cfg, groups):
    # تهريب أسماء المجموعة والقناة لأنها نصوص خارجة عن تحكمنا وقد تحتوي رموز Markdown خاصة
    # (كانت هذه هي السبب في فشل الرد بصمت عندما يحتوي اسم القناة على رمز مثل _ أو * أو `)
    gname = escape_markdown(groups.get(gid, f"مجموعة #{gid}"))
    channel_display = escape_markdown(cfg.get("channel_title") or "غير معروف")
    username = cfg.get("channel_username")
    if username:
        channel_display += f" (@{escape_markdown(username)})"
    return (
        "📢 **إعدادات الاشتراك الإجباري**\n\n"
        f"• المجموعة: {gname}\n"
        f"• القناة: {channel_display}\n"
        f"• الحالة: {'🟢 مفعل' if cfg.get('enabled') else '🔴 معطل'}\n"
        f"• تاريخ الإنشاء: {cfg.get('created_at', '-')}\n"
        f"• آخر تعديل: {cfg.get('updated_at', '-')}"
    )
def build_force_sub_join_keyboard(cfg):
    """يبني لوحة (زر الانضمام للقناة + زر إعادة التحقق) لعرضها للعضو غير المشترك."""
    url = None
    if cfg.get("channel_username"):
        url = f"https://t.me/{cfg['channel_username']}"
    elif cfg.get("invite_link"):
        url = cfg["invite_link"]
    buttons = []
    if url:
        buttons.append([InlineKeyboardButton("📢 الانضمام إلى القناة", url=url)])
    buttons.append([InlineKeyboardButton("✅ تحقق مرة أخرى", callback_data="fs_recheck")])
    return InlineKeyboardMarkup(buttons)
async def send_non_admin_response(update: Update):
    """رسالة تعريفية لغير المشرفين في أول رسالة، ثم رسالة توضح أن الرسائل لا تصل للمطور بعد ذلك."""
    user_id = update.effective_user.id
    count = PRIVATE_MSG_TRACK.get(user_id, 0) + 1
    PRIVATE_MSG_TRACK[user_id] = count
    if count <= 1:
        await update.message.reply_text(NON_ADMIN_INFO_TEXT, parse_mode='Markdown')
    else:
        await update.message.reply_text(NON_ADMIN_SPAM_TEXT, parse_mode='Markdown')
async def bot_my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إشعار المشرفين عند إضافة البوت أو ترقيته إلى مشرف في مجموعة."""
    cmu = update.my_chat_member
    if not cmu or not cmu.chat or cmu.chat.type not in ("group", "supergroup"):
        return

    old_status = getattr(cmu.old_chat_member, "status", None)
    new_status = getattr(cmu.new_chat_member, "status", None)
    admin_statuses = {"administrator", "creator"}

    if new_status not in admin_statuses or old_status in admin_statuses:
        return

    chat = cmu.chat
    actor = cmu.from_user
    chat_username = getattr(chat, "username", None)
    actor_username = getattr(actor, "username", None) if actor else None

    chat_title = html.escape(str(chat.title or "مجموعة غير معروفة"))
    username_line = f"@{html.escape(chat_username)}" if chat_username else "غير متوفر"
    actor_name = html.escape(str(actor.full_name if actor else "غير متوفر"))
    actor_username_line = f"@{html.escape(actor_username)}" if actor_username else "غير متوفر"
    actor_id = actor.id if actor else "غير متوفر"
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    notification = (
        "🤖 <b>تمت إضافة البوت كمشرف في مجموعة جديدة</b>\n\n"
        f"👥 <b>اسم المجموعة:</b> {chat_title}\n"
        f"🔗 <b>Username المجموعة:</b> {username_line}\n"
        f"🆔 <b>ID المجموعة:</b> <code>{chat.id}</code>\n\n"
        "👤 <b>من قام بالإضافة/الترقية:</b>\n"
        f"• الاسم: {actor_name}\n"
        f"• Username: {actor_username_line}\n"
        f"• ID: <code>{actor_id}</code>\n\n"
        f"🕐 <b>التاريخ والوقت:</b> <code>{html.escape(now_text)}</code>\n"
        "🛡️ <b>الحالة:</b> البوت أصبح مشرفاً في هذه المجموعة."
    )

    bot_data = load_data()
    admin_ids = []
    for admin_id in bot_data.get("admins", INITIAL_ADMINS):
        try:
            admin_id = int(admin_id)
        except (TypeError, ValueError):
            continue
        if admin_id not in admin_ids:
            admin_ids.append(admin_id)

    for admin_id in admin_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=notification, parse_mode="HTML")
        except Exception:
            logging.exception("فشل إرسال إشعار إضافة البوت كمشرف إلى الأدمن %s", admin_id)


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
# === 5. التحكم في الأزرار ===
async def handle_force_sub_recheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة زر '✅ تحقق مرة أخرى' الذي يظهر لأي عضو داخل المجموعة (وليس فقط للمشرفين)."""
    query = update.callback_query
    chat_id = query.message.chat.id
    bot_data = load_data()
    fs_config = bot_data.get("force_sub_groups", {}).get(str(chat_id))
    if not fs_config or not fs_config.get("enabled"):
        await query.answer("✅ لا يوجد اشتراك إجباري مفعل حالياً في هذه المجموعة.", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
        return
    channel_id = fs_config.get("channel_id")
    try:
        member = await context.bot.get_chat_member(channel_id, query.from_user.id)
        subscribed = member.status not in ['left', 'kicked']
    except Exception:
        await query.answer("⚠️ تعذر التحقق حالياً، يرجى المحاولة مرة أخرى بعد قليل.", show_alert=True)
        return
    if subscribed:
        await query.answer("✅ تم التحقق بنجاح! يمكنك الآن إرسال الرسائل في المجموعة.", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
    else:
        await query.answer("❌ لم يتم رصد اشتراكك بعد. يرجى الاشتراك في القناة أولاً ثم إعادة المحاولة.", show_alert=True)
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    action = query.data
    # زر التحقق من الاشتراك الإجباري متاح لأي عضو داخل المجموعة (وليس فقط أدمن البوت)
    if action == "fs_recheck":
        await handle_force_sub_recheck(update, context)
        return
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
    # ---------- الاشتراك الإجباري (ميزة جديدة) ----------
    elif action == "manage_force_sub":
        await query.message.edit_text(
            "📢 **إدارة الاشتراك الإجباري**\n\n"
            "من هنا يمكنك تفعيل الاشتراك الإجباري بشكل مستقل لكل مجموعة على حدة، بحيث يُطلب من "
            "الأعضاء الاشتراك في قناة محددة (خاصة بتلك المجموعة) قبل السماح لهم بالكتابة.",
            parse_mode='Markdown',
            reply_markup=get_force_sub_list_keyboard(bot_data)
        )
    elif action == "fs_add_new":
        if not bot_data.get("groups"):
            await query.message.edit_text(
                "❌ لا توجد مجموعات مسجلة بعد. أضف البوت إلى مجموعة أولاً ثم أعد المحاولة.",
                reply_markup=get_back_keyboard("manage_force_sub")
            )
            return
        await query.message.edit_text(
            "🎯 **اختر المجموعة التي تريد تفعيل الاشتراك الإجباري لها:**",
            parse_mode='Markdown',
            reply_markup=get_force_sub_new_group_keyboard(bot_data)
        )
    elif action.startswith("fs_new_group_"):
        gid = action.replace("fs_new_group_", "")
        try:
            bot_member = await context.bot.get_chat_member(int(gid), context.bot.id)
            if bot_member.status not in ['administrator', 'creator']:
                await query.message.edit_text(
                    "❌ البوت ليس مشرفاً داخل هذه المجموعة.\n"
                    "يرجى ترقية البوت إلى مشرف داخل المجموعة أولاً، ثم إعادة المحاولة.",
                    reply_markup=get_back_keyboard("manage_force_sub")
                )
                return
        except Exception:
            await query.message.edit_text(
                "❌ تعذّر التحقق من صلاحيات البوت داخل المجموعة. تأكد أن البوت لا يزال داخل المجموعة.",
                reply_markup=get_back_keyboard("manage_force_sub")
            )
            return
        WAITING_STATES[user_id] = f"fs_forward_channel_{gid}"
        await query.message.edit_text(
            "📩 **قم الآن بإعادة توجيه (Forward) أي رسالة من القناة المطلوب الاشتراك الإجباري بها إلى هنا.**\n\n"
            "⚠️ تأكد أن البوت مشرف داخل القناة قبل المتابعة، وإلا لن يتمكن من التحقق من اشتراك الأعضاء.",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("manage_force_sub")
        )
    elif action.startswith("fs_manage_"):
        gid = action.replace("fs_manage_", "")
        cfg = bot_data.get("force_sub_groups", {}).get(gid)
        if not cfg:
            await query.message.edit_text("❌ لا يوجد إعداد لهذه المجموعة.", reply_markup=get_force_sub_list_keyboard(bot_data))
            return
        await safe_edit_text(
            query.message,
            format_force_sub_info(gid, cfg, bot_data.get("groups", {})),
            reply_markup=get_force_sub_manage_keyboard(gid, cfg)
        )
    elif action.startswith("fs_toggle_"):
        gid = action.replace("fs_toggle_", "")
        cfg = bot_data.get("force_sub_groups", {}).get(gid)
        if not cfg:
            await query.message.edit_text("❌ لا يوجد إعداد لهذه المجموعة.", reply_markup=get_force_sub_list_keyboard(bot_data))
            return
        cfg["enabled"] = not cfg.get("enabled", False)
        cfg["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            format_force_sub_info(gid, cfg, bot_data.get("groups", {})),
            reply_markup=get_force_sub_manage_keyboard(gid, cfg)
        )
    elif action.startswith("fs_disable_"):
        # زر إلغاء صريح: يعطّل الاشتراك الإجباري لهذه المجموعة تحديداً فوراً (بدون حذف الإعداد المحفوظ)
        gid = action.replace("fs_disable_", "")
        cfg = bot_data.get("force_sub_groups", {}).get(gid)
        if not cfg:
            await query.message.edit_text("❌ لا يوجد إعداد لهذه المجموعة.", reply_markup=get_force_sub_list_keyboard(bot_data))
            return
        cfg["enabled"] = False
        cfg["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            "✅ تم إلغاء الاشتراك الإجباري لهذه المجموعة.\n\n" + format_force_sub_info(gid, cfg, bot_data.get("groups", {})),
            reply_markup=get_force_sub_manage_keyboard(gid, cfg)
        )
    elif action.startswith("fs_edit_channel_"):
        gid = action.replace("fs_edit_channel_", "")
        if gid not in bot_data.get("force_sub_groups", {}):
            await query.message.edit_text("❌ لا يوجد إعداد لهذه المجموعة.", reply_markup=get_force_sub_list_keyboard(bot_data))
            return
        WAITING_STATES[user_id] = f"fs_forward_channel_{gid}"
        await query.message.edit_text(
            "📩 **قم بإعادة توجيه رسالة من القناة الجديدة المراد ربطها بهذه المجموعة.**",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard(f"fs_manage_{gid}")
        )
    elif action.startswith("fs_delete_confirm_"):
        gid = action.replace("fs_delete_confirm_", "")
        bot_data.get("force_sub_groups", {}).pop(gid, None)
        save_data(bot_data)
        await query.message.edit_text("✅ تم حذف إعداد الاشتراك الإجباري لهذه المجموعة.", reply_markup=get_force_sub_list_keyboard(bot_data))
    elif action.startswith("fs_delete_"):
        gid = action.replace("fs_delete_", "")
        if gid not in bot_data.get("force_sub_groups", {}):
            await query.message.edit_text("❌ لا يوجد إعداد لهذه المجموعة.", reply_markup=get_force_sub_list_keyboard(bot_data))
            return
        await query.message.edit_text(
            "⚠️ هل أنت متأكد من حذف إعداد الاشتراك الإجباري لهذه المجموعة؟",
            reply_markup=get_force_sub_delete_confirm_keyboard(gid)
        )
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
    # ---------- الكلمات ----------
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
    # ---------- الإيموجيات ----------
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
    # ---------- الروابط ----------
    elif action == "manage_links":
        links = ", ".join(bot_data.get("banned_links", [])) or "لا توجد"
        await query.message.edit_text(
            f"🔗 **إعدادات الروابط المحظورة:**\n\nالقائمة الحالية:\n`{links}`",
            parse_mode='Markdown',
            reply_markup=get_links_keyboard(bot_data)
        )
    elif action == "toggle_block_all_links":
        bot_data["block_all_links"] = not bot_data.get("block_all_links", False)
        save_data(bot_data)
        await query.message.edit_text("✅ تم تحديث إعداد حظر جميع الروابط.", reply_markup=get_links_keyboard(bot_data))
    elif action == "add_link":
        WAITING_STATES[user_id] = "add_link"
        await query.message.edit_text("✏️ **أرسل الرابط أو النطاق المراد حظره (مثال: example.com):**", reply_markup=get_back_keyboard("manage_links"))
    elif action == "clear_links":
        bot_data["banned_links"] = []
        save_data(bot_data)
        await query.message.edit_text("✅ تم تفريغ قائمة الروابط المحظورة.", reply_markup=get_back_keyboard("manage_links"))
    # ---------- الملصقات ----------
    elif action == "manage_stickers":
        await query.message.edit_text(
            "🎭 **إعدادات حظر الملصقات والصور المتحركة:**",
            parse_mode='Markdown',
            reply_markup=get_stickers_keyboard(bot_data)
        )
    elif action == "toggle_block_stickers":
        bot_data["block_stickers"] = not bot_data.get("block_stickers", False)
        save_data(bot_data)
        await query.message.edit_text("✅ تم تحديث إعداد حظر الملصقات.", reply_markup=get_stickers_keyboard(bot_data))
    elif action == "toggle_block_animated":
        bot_data["block_animated_stickers"] = not bot_data.get("block_animated_stickers", False)
        save_data(bot_data)
        await query.message.edit_text("✅ تم تحديث إعداد حظر الملصقات المتحركة و GIF.", reply_markup=get_stickers_keyboard(bot_data))
    # ---------- مراقبة الكلمات والعبارات (ميزة جديدة) ----------
    elif action == "manage_word_watch":
        ww = bot_data.get("word_watch", {})
        targets_count = len(ww.get("target_groups", {}))
        words_count = len(ww.get("words", []))
        msg = (
            "🔎 **نظام مراقبة الكلمات والعبارات**\n\n"
            f"• المجموعات المستهدفة: {targets_count}\n"
            f"• عدد الكلمات/العبارات المراقبة: {words_count}\n\n"
            "من هنا تستطيع تحديد المجموعات التي يعمل بها النظام، وإدارة قائمة الكلمات، "
            "وتحديد المشرفين الذين يتم تنبيههم عند اكتشاف مخالفة."
        )
        await query.message.edit_text(msg, parse_mode='Markdown', reply_markup=get_word_watch_main_keyboard())
    elif action == "ww_targets":
        await query.message.edit_text(
            "📌 **المجموعات المستهدفة بمراقبة الكلمات:**\n\n"
            "اضغط على اسم المجموعة لتفعيل/تعطيل المراقبة فيها، أو 🗑️ لإزالتها من القائمة.",
            parse_mode='Markdown',
            reply_markup=get_ww_targets_keyboard(bot_data)
        )
    elif action == "ww_targets_add":
        if not bot_data.get("groups"):
            await query.message.edit_text("❌ لا توجد مجموعات مسجلة بعد.", reply_markup=get_back_keyboard("ww_targets"))
            return
        await query.message.edit_text(
            "🎯 **اختر المجموعة لإضافتها إلى المراقبة:**",
            parse_mode='Markdown',
            reply_markup=get_ww_targets_add_keyboard(bot_data)
        )
    elif action.startswith("ww_targets_add_"):
        gid = action.replace("ww_targets_add_", "")
        bot_data["word_watch"]["target_groups"][gid] = True
        save_data(bot_data)
        await query.message.edit_text("✅ تم إضافة المجموعة إلى المراقبة بنجاح.", reply_markup=get_ww_targets_keyboard(bot_data))
    elif action.startswith("ww_targets_toggle_"):
        gid = action.replace("ww_targets_toggle_", "")
        targets = bot_data["word_watch"]["target_groups"]
        if gid in targets:
            targets[gid] = not targets[gid]
            save_data(bot_data)
        await query.message.edit_text("تم تحديث حالة المراقبة لهذه المجموعة.", reply_markup=get_ww_targets_keyboard(bot_data))
    elif action.startswith("ww_targets_remove_"):
        gid = action.replace("ww_targets_remove_", "")
        bot_data["word_watch"]["target_groups"].pop(gid, None)
        bot_data["word_watch"]["alert_admins"].pop(gid, None)
        save_data(bot_data)
        await query.message.edit_text("✅ تم إزالة المجموعة من المراقبة.", reply_markup=get_ww_targets_keyboard(bot_data))
    elif action == "ww_words":
        words = bot_data.get("word_watch", {}).get("words", [])
        words_text = "، ".join(words) if words else "لا توجد كلمات"
        await query.message.edit_text(
            f"📝 **الكلمات والعبارات المراقبة:**\n\n{words_text}",
            parse_mode='Markdown',
            reply_markup=get_ww_words_keyboard()
        )
    elif action == "ww_words_add":
        WAITING_STATES[user_id] = "ww_words_add"
        await query.message.edit_text(
            "✏️ **أرسل الكلمة أو العبارة المراد إضافتها.**\n"
            "يمكنك إرسال أكثر من كلمة دفعة واحدة، كل واحدة في سطر منفصل.",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("ww_words")
        )
    elif action == "ww_words_remove":
        WAITING_STATES[user_id] = "ww_words_remove"
        await query.message.edit_text(
            "🗑️ **أرسل الكلمة أو العبارة المراد حذفها بالضبط كما تظهر في القائمة:**",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("ww_words")
        )
    elif action == "ww_words_reset":
        bot_data["word_watch"]["words"] = list(DEFAULT_WATCH_WORDS)
        save_data(bot_data)
        await query.message.edit_text("✅ تم استعادة القائمة الافتراضية للكلمات المراقبة.", reply_markup=get_ww_words_keyboard())
    elif action == "ww_alert_admins":
        await query.message.edit_text(
            "👥 **مشرفو التنبيه**\n\nاختر المجموعة لإدارة قائمة المشرفين الذين يتم تنبيههم فيها عند اكتشاف كلمة مستهدفة:",
            parse_mode='Markdown',
            reply_markup=get_ww_alert_admins_groups_keyboard(bot_data)
        )
    elif action.startswith("ww_alert_admins_group_"):
        gid = action.replace("ww_alert_admins_group_", "")
        admins_list = bot_data.get("word_watch", {}).get("alert_admins", {}).get(gid, [])
        gname = bot_data.get("groups", {}).get(gid, f"مجموعة #{gid}")
        admins_text = "\n".join(admins_list) if admins_list else "لا يوجد مشرفون بعد"
        await query.message.edit_text(
            f"👥 **مشرفو التنبيه لمجموعة {gname}:**\n\n{admins_text}",
            parse_mode='Markdown',
            reply_markup=get_ww_alert_admins_manage_keyboard(gid, admins_list)
        )
    elif action.startswith("ww_alert_admins_add_"):
        gid = action.replace("ww_alert_admins_add_", "")
        WAITING_STATES[user_id] = f"ww_alert_admins_add_{gid}"
        await query.message.edit_text(
            "✏️ **أرسل معرّف المشرف بالشكل التالي:**\n\n`@username`",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard(f"ww_alert_admins_group_{gid}")
        )
    elif action.startswith("ww_alert_admins_remove_"):
        remainder = action.replace("ww_alert_admins_remove_", "")
        gid, idx_str = remainder.rsplit("_", 1)
        try:
            idx = int(idx_str)
            admins_list = bot_data["word_watch"]["alert_admins"].get(gid, [])
            if 0 <= idx < len(admins_list):
                admins_list.pop(idx)
                save_data(bot_data)
        except Exception:
            pass
        admins_list = bot_data.get("word_watch", {}).get("alert_admins", {}).get(gid, [])
        gname = bot_data.get("groups", {}).get(gid, f"مجموعة #{gid}")
        admins_text = "\n".join(admins_list) if admins_list else "لا يوجد مشرفون بعد"
        await query.message.edit_text(
            f"👥 **مشرفو التنبيه لمجموعة {gname}:**\n\n{admins_text}",
            parse_mode='Markdown',
            reply_markup=get_ww_alert_admins_manage_keyboard(gid, admins_list)
        )
    # ---------- نظام كتم المستخدمين (ميزة جديدة) ----------
    elif action == "manage_mute":
        TEMP_MUTE[user_id] = {}
        WAITING_STATES[user_id] = "mute_forward_wait"
        await query.message.edit_text(
            "🔇 **كتم مستخدم**\n\n"
            "قم بإعادة توجيه (Forward) رسالة المستخدم المخالف إلى هذا البوت هنا، "
            "وسأقوم بتحديد هويته ثم أطلب منك اختيار المجموعة ومدة الكتم.",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("main_menu")
        )
    elif action.startswith("mute_select_group_"):
        gid = action.replace("mute_select_group_", "")
        temp = TEMP_MUTE.get(user_id)
        if not temp or not temp.get("target_user_id"):
            await query.message.edit_text("❌ انتهت صلاحية العملية، يرجى البدء من جديد.", reply_markup=get_back_keyboard("main_menu"))
            return
        temp["chat_id"] = gid
        await query.message.edit_text(
            f"⏱ **اختر مدة كتم {temp.get('target_user_name', 'المستخدم')}:**",
            parse_mode='Markdown',
            reply_markup=get_mute_durations_keyboard(bot_data)
        )
    elif action.startswith("mute_dur_"):
        seconds_str = action.replace("mute_dur_", "")
        temp = TEMP_MUTE.get(user_id)
        if not temp or not temp.get("target_user_id") or not temp.get("chat_id"):
            await query.message.edit_text("❌ انتهت صلاحية العملية، يرجى البدء من جديد.", reply_markup=get_back_keyboard("main_menu"))
            return
        try:
            seconds = int(seconds_str)
        except ValueError:
            await query.message.edit_text("❌ مدة غير صحيحة.", reply_markup=get_back_keyboard("main_menu"))
            return
        chat_id = int(temp["chat_id"])
        target_user_id = temp["target_user_id"]
        target_user_name = temp.get("target_user_name", "المستخدم")
        try:
            member_check = await context.bot.get_chat_member(chat_id, target_user_id)
            if member_check.status in ['left', 'kicked']:
                await query.message.edit_text("❌ فشل الكتم: هذا المستخدم غير موجود داخل المجموعة.", reply_markup=get_back_keyboard("main_menu"))
                return
        except Exception as e:
            await query.message.edit_text(f"❌ فشل الكتم: تعذر التحقق من عضوية المستخدم ({e}).", reply_markup=get_back_keyboard("main_menu"))
            return
        try:
            bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
            if bot_member.status != 'administrator' or not getattr(bot_member, "can_restrict_members", True):
                await query.message.edit_text("❌ فشل الكتم: البوت لا يملك صلاحية تقييد الأعضاء داخل هذه المجموعة.", reply_markup=get_back_keyboard("main_menu"))
                return
        except Exception as e:
            await query.message.edit_text(f"❌ فشل الكتم: تعذر التحقق من صلاحيات البوت ({e}).", reply_markup=get_back_keyboard("main_menu"))
            return
        until_date = datetime.now() + timedelta(seconds=seconds)
        try:
            permissions = ChatPermissions(can_send_messages=False)
            await context.bot.restrict_chat_member(chat_id, target_user_id, permissions=permissions, until_date=until_date)
        except Exception as e:
            await query.message.edit_text(f"❌ فشل تنفيذ الكتم فعلياً: {e}", reply_markup=get_back_keyboard("main_menu"))
            return
        register_active_mute(bot_data, chat_id, target_user_id, target_user_name, until_date.timestamp())
        duration_label = None
        for lbl, secs in get_all_mute_durations(bot_data):
            if secs == seconds:
                duration_label = lbl
                break
        if not duration_label:
            duration_label = format_mute_duration_label(seconds)
        try:
            member_obj = await context.bot.get_chat_member(chat_id, target_user_id)
            mention = build_user_mention_html(member_obj.user)
        except Exception:
            mention = html.escape(target_user_name)
        group_text = (
            f"🔇 تم كتم {mention} بسبب تجاوزات/مخالفات.\n\n"
            f"⏱ مدة الكتم: {duration_label}\n"
            f"🚫 لن يتمكن من إرسال الرسائل خلال هذه المدة."
        )
        try:
            await context.bot.send_message(chat_id=chat_id, text=group_text, parse_mode='HTML')
        except Exception:
            pass
        TEMP_MUTE.pop(user_id, None)
        WAITING_STATES[user_id] = None
        await query.message.edit_text(f"✅ تم كتم {target_user_name} لمدة {duration_label} بنجاح.", reply_markup=get_back_keyboard("main_menu"))
    elif action == "mute_add_duration":
        WAITING_STATES[user_id] = "mute_add_duration"
        await query.message.edit_text(
            "➕ **أرسل المدة الجديدة، مثال:**\n\n`5 ساعات` أو `12 ساعة` أو `5 أيام` أو `30 دقيقة`",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("manage_mute")
        )
    elif action == "mute_remove_duration":
        await query.message.edit_text(
            "🗑️ **اختر المدة المخصصة المراد حذفها:**",
            parse_mode='Markdown',
            reply_markup=get_mute_remove_duration_keyboard(bot_data)
        )
    elif action.startswith("mute_remove_duration_"):
        idx_str = action.replace("mute_remove_duration_", "")
        try:
            idx = int(idx_str)
            custom = bot_data.get("custom_mute_durations", [])
            if 0 <= idx < len(custom):
                custom.pop(idx)
                save_data(bot_data)
        except Exception:
            pass
        await query.message.edit_text("✅ تم حذف المدة المخصصة.", reply_markup=get_mute_remove_duration_keyboard(bot_data))
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
        await send_non_admin_response(update)
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
    elif state == "add_link":
        WAITING_STATES[user_id] = None
        link = update.message.text.strip().lower()
        if link not in bot_data["banned_links"]:
            bot_data["banned_links"].append(link)
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم إضافة الرابط `{link}` للقائمة المحظورة!", parse_mode='Markdown', reply_markup=get_back_keyboard("manage_links"))
    elif state == "rescue_set_keyword":
        WAITING_STATES[user_id] = None
        keyword = update.message.text.strip()
        bot_data["rescue_mode"]["keyword"] = keyword
        save_data(bot_data)
        await update.message.reply_text(f"✅ تم تحديث كلمة النداء إلى: `{keyword}`", parse_mode='Markdown', reply_markup=get_back_keyboard("manage_rescue"))
    elif state == "rescue_set_threshold":
        WAITING_STATES[user_id] = None
        try:
            threshold = int(update.message.text.strip())
            if threshold < 1:
                raise ValueError
            bot_data["rescue_mode"]["threshold"] = threshold
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم تحديث عدد النداءات إلى: {threshold}", reply_markup=get_back_keyboard("manage_rescue"))
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال رقم صحيح أكبر من صفر.", reply_markup=get_back_keyboard("manage_rescue"))
    elif state == "rescue_set_duration":
        WAITING_STATES[user_id] = None
        try:
            duration = int(update.message.text.strip())
            if duration < 1:
                raise ValueError
            bot_data["rescue_mode"]["duration_minutes"] = duration
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم تحديث مدة الصمت التلقائي إلى: {duration} دقيقة", reply_markup=get_back_keyboard("manage_rescue"))
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال رقم صحيح أكبر من صفر.", reply_markup=get_back_keyboard("manage_rescue"))
    elif state == "rescue_set_message":
        WAITING_STATES[user_id] = None
        bot_data["rescue_mode"]["message"] = update.message.text
        save_data(bot_data)
        await update.message.reply_text("✅ تم تحديث رسالة النداء!", reply_markup=get_back_keyboard("manage_rescue"))
    # ---------- استقبال رسالة القناة المُعاد توجيهها (الاشتراك الإجباري) ----------
    elif state.startswith("fs_forward_channel_"):
        gid = state.replace("fs_forward_channel_", "")
        # نغلّف الخطوة بالكامل بمعالجة أخطاء شاملة حتى لا يحدث "عدم رد" صامت من البوت
        # مهما كان سبب أي عطل غير متوقع (مشاكل شبكة، صلاحيات، تنسيق النص...الخ).
        try:
            channel_chat = extract_forwarded_channel(update.message)
            if not channel_chat:
                await update.message.reply_text(
                    "❌ لم يتم العثور على معلومات قناة في هذه الرسالة.\n\n"
                    "تأكد من أنك تقوم بإعادة توجيه (Forward) حقيقية لرسالة من داخل القناة نفسها "
                    "(وليس نسخ/لصق النص، وليست رسالة من مجموعة أو من مستخدم عادي).\n\n"
                    "💡 إن استمرت المشكلة: افتح القناة مباشرة، اختر أي منشور بها، ثم اضغط Forward → اختر هذا البوت.",
                    reply_markup=get_back_keyboard("manage_force_sub")
                )
                return
            try:
                bot_member = await context.bot.get_chat_member(channel_chat.id, context.bot.id)
                if bot_member.status not in ['administrator', 'creator']:
                    await update.message.reply_text(
                        "❌ البوت ليس مشرفاً داخل هذه القناة.\n"
                        "يرجى ترقية البوت إلى مشرف داخل القناة أولاً (ويفضّل منحه صلاحية دعوة الأعضاء)، ثم أعد إرسال التوجيه.",
                        reply_markup=get_back_keyboard("manage_force_sub")
                    )
                    return
            except Exception as e:
                logging.exception("فشل التحقق من صلاحيات البوت داخل القناة")
                await update.message.reply_text(
                    "❌ تعذّر التحقق من صلاحيات البوت داخل القناة.\n"
                    "تأكد من أن البوت عضو ومشرف في القناة، ثم أعد المحاولة.\n"
                    f"(تفاصيل تقنية: {e})",
                    reply_markup=get_back_keyboard("manage_force_sub")
                )
                return
            # إذا كانت القناة خاصة (بدون يوزر عام)، نحاول إنشاء رابط دعوة يستخدمه الأعضاء للانضمام
            invite_link = None
            if not channel_chat.username:
                try:
                    invite_obj = await context.bot.create_chat_invite_link(channel_chat.id)
                    invite_link = invite_obj.invite_link
                except Exception:
                    invite_link = None
            WAITING_STATES[user_id] = None
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            fs_groups = bot_data.setdefault("force_sub_groups", {})
            existing = fs_groups.get(gid)
            fs_groups[gid] = {
                "channel_id": channel_chat.id,
                "channel_title": channel_chat.title or "قناة بدون اسم",
                "channel_username": channel_chat.username,
                "invite_link": invite_link,
                "enabled": existing.get("enabled", True) if existing else True,
                "created_at": existing.get("created_at", now_str) if existing else now_str,
                "updated_at": now_str
            }
            save_data(bot_data)
            await safe_reply_text(
                update.message,
                "✅ تم ربط القناة بنجاح!\n\n" + format_force_sub_info(gid, fs_groups[gid], bot_data.get("groups", {})),
                reply_markup=get_force_sub_manage_keyboard(gid, fs_groups[gid])
            )
        except Exception as e:
            logging.exception("خطأ غير متوقع أثناء ربط القناة بالاشتراك الإجباري")
            WAITING_STATES[user_id] = None
            try:
                await update.message.reply_text(
                    f"⚠️ حدث خطأ غير متوقع أثناء معالجة القناة: {e}\nيرجى المحاولة مرة أخرى.",
                    reply_markup=get_back_keyboard("manage_force_sub")
                )
            except Exception:
                pass
    # ---------- إضافة/حذف كلمات مراقبة الكلمات ----------
    elif state == "ww_words_add":
        WAITING_STATES[user_id] = None
        raw_lines = [w.strip() for w in update.message.text.split("\n") if w.strip()]
        words_list = bot_data["word_watch"].setdefault("words", [])
        added = []
        for w in raw_lines:
            if w not in words_list:
                words_list.append(w)
                added.append(w)
        save_data(bot_data)
        if added:
            await update.message.reply_text(f"✅ تم إضافة {len(added)} كلمة/عبارة بنجاح.", reply_markup=get_back_keyboard("ww_words"))
        else:
            await update.message.reply_text("⚠️ الكلمات المُرسلة موجودة بالفعل في القائمة.", reply_markup=get_back_keyboard("ww_words"))
    elif state == "ww_words_remove":
        WAITING_STATES[user_id] = None
        word = update.message.text.strip()
        words_list = bot_data["word_watch"].setdefault("words", [])
        if word in words_list:
            words_list.remove(word)
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم حذف `{word}` من القائمة.", parse_mode='Markdown', reply_markup=get_back_keyboard("ww_words"))
        else:
            await update.message.reply_text("❌ لم يتم العثور على هذه الكلمة/العبارة في القائمة.", reply_markup=get_back_keyboard("ww_words"))
    # ---------- إضافة مشرف تنبيه لمجموعة معينة ----------
    elif state.startswith("ww_alert_admins_add_"):
        gid = state.replace("ww_alert_admins_add_", "")
        WAITING_STATES[user_id] = None
        username = update.message.text.strip()
        if not username.startswith("@"):
            username = "@" + username
        admins_map = bot_data["word_watch"].setdefault("alert_admins", {})
        admins_list = admins_map.setdefault(gid, [])
        if username not in admins_list:
            admins_list.append(username)
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم إضافة {username} كمشرف تنبيه لهذه المجموعة.", reply_markup=get_ww_alert_admins_manage_keyboard(gid, admins_list))
        else:
            await update.message.reply_text("⚠️ هذا المشرف مضاف بالفعل.", reply_markup=get_ww_alert_admins_manage_keyboard(gid, admins_list))
    # ---------- إضافة مدة كتم مخصصة ----------
    elif state == "mute_add_duration":
        WAITING_STATES[user_id] = None
        txt = update.message.text.strip()
        match = re.match(r"^(\d+)\s*(دقيقة|دقائق|ساعة|ساعات|يوم|أيام)$", txt)
        if not match:
            await update.message.reply_text(
                "❌ صيغة غير صحيحة. أرسلها مثل: `5 ساعات` أو `12 ساعة` أو `5 أيام` أو `30 دقيقة`",
                parse_mode='Markdown',
                reply_markup=get_back_keyboard("manage_mute")
            )
            return
        value = int(match.group(1))
        unit = match.group(2)
        if unit in ("دقيقة", "دقائق"):
            seconds = value * 60
        elif unit in ("ساعة", "ساعات"):
            seconds = value * 3600
        else:
            seconds = value * 86400
        custom = bot_data.setdefault("custom_mute_durations", [])
        custom.append({"label": txt, "seconds": seconds})
        save_data(bot_data)
        await update.message.reply_text(f"✅ تم إضافة المدة `{txt}` إلى قائمة مدد الكتم.", parse_mode='Markdown', reply_markup=get_back_keyboard("manage_mute"))
    # ---------- استقبال الرسالة المُعاد توجيهها لتحديد المستخدم المراد كتمه ----------
    elif state == "mute_forward_wait":
        origin = getattr(update.message, "forward_origin", None)
        legacy_user = getattr(update.message, "forward_from", None)
        target_user = None
        if legacy_user is not None:
            target_user = legacy_user
        elif origin is not None and getattr(origin, "type", None) == "user":
            target_user = getattr(origin, "sender_user", None)
        if not target_user:
            await update.message.reply_text(
                "❌ لم أتمكن من تحديد صاحب هذه الرسالة.\n\n"
                "تأكد من أنها إعادة توجيه (Forward) حقيقية لرسالة المستخدم المخالف من داخل المجموعة "
                "(وليست رسالة من قناة أو مستخدم مخفي الهوية).",
                reply_markup=get_back_keyboard("main_menu")
            )
            return
        TEMP_MUTE[user_id] = {
            "target_user_id": target_user.id,
            "target_user_name": target_user.first_name or "المستخدم",
            "chat_id": None
        }
        WAITING_STATES[user_id] = None
        if not bot_data.get("groups"):
            await update.message.reply_text("❌ لا توجد مجموعات مسجلة بعد.", reply_markup=get_back_keyboard("main_menu"))
            return
        await update.message.reply_text(
            f"👤 **تم تحديد المستخدم:** {target_user.first_name or 'المستخدم'}\n\n"
            "🎯 **اختر المجموعة التي سيتم الكتم فيها:**",
            parse_mode='Markdown',
            reply_markup=get_groups_selection_keyboard(bot_data, "mute_select_group_", "main_menu")
        )
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
def is_silent_active(bot_data, chat_id):
    """يتحقق مما إذا كان الوضع الصامت مفعلاً، ويطبَّق فقط على المجموعة المستهدفة المحددة."""
    s = bot_data.get("silent_mode", {})
    if not s.get("enabled"):
        return False
    target = s.get("target_group")
    if not target or str(target) != str(chat_id):
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
async def delete_and_warn(msg, context, chat_id, first_name, reason):
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
async def trigger_rescue_silent(context, chat_id, bot_data, rescue):
    """تفعيل الوضع الصامت تلقائياً بعد اكتمال نداء الاستغاثة."""
    duration = rescue.get("duration_minutes", 30)
    until_ts = int((datetime.now() + timedelta(minutes=duration)).timestamp())
    bot_data["silent_mode"]["until_timestamp"] = until_ts
    bot_data["silent_mode"]["enabled"] = True
    bot_data["silent_mode"]["target_group"] = str(chat_id)
    save_data(bot_data)
    RESCUE_TRACK[chat_id] = {"count": 0, "callers": []}
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء الوضع الصامت", callback_data="cancel_silent")]])
    threshold = rescue.get("threshold", 3)
    text = (
        f"🚨 **تم تفعيل الوضع الصامت تلقائياً** بسبب تكرار نداء الاستغاثة ({threshold}/{threshold})!\n\n"
        f"{rescue.get('message', '')}\n\n"
        "يمكن لمشرفي المجموعة إلغاء الوضع الصامت بالضغط على الزر أدناه."
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=kb)
    except Exception:
        pass
async def handle_rescue_keyword(update, context, chat_id, bot_data, text):
    rescue = bot_data.get("rescue_mode", {})
    target = rescue.get("target_group")
    if not rescue.get("enabled") or not target or str(target) != str(chat_id):
        return False
    keyword = (rescue.get("keyword") or "").strip()
    if not keyword or keyword not in text:
        return False
    threshold = rescue.get("threshold", 3)
    track = RESCUE_TRACK.setdefault(chat_id, {"count": 0, "callers": []})
    track["count"] += 1
    caller_name = update.effective_user.first_name if update.effective_user else "عضو"
    track["callers"].append(caller_name)
    count = track["count"]
    if count >= threshold:
        await trigger_rescue_silent(context, chat_id, bot_data, rescue)
    elif count == 1:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🆘 تم رصد نداء استغاثة ({count}/{threshold})\nفي حال تكرر النداء سيتم تفعيل الوضع الصامت تلقائياً."
        )
    else:
        callers_text = "، ".join(track["callers"])
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ تحذير: نداء استغاثة ({count}/{threshold})\n"
                f"في حال كان النداء بدون سبب حقيقي، سيتم حظر من قام بالنداء عند تفعيل الوضع الصامت: {callers_text}"
            )
        )
    return True
async def delete_after_delay(message, delay_seconds):
    """يحذف رسالة معينة بعد مهلة زمنية محددة، مستخدَمة لرسالة الاشتراك الإجباري (25 ثانية)."""
    try:
        await asyncio.sleep(delay_seconds)
        await message.delete()
    except Exception:
        pass
async def check_force_subscription(update, context, chat_id, bot_data, user_id, first_name):
    """
    يتحقق مما إذا كان العضو مشتركاً في القناة المطلوبة الخاصة بهذه المجموعة تحديداً
    (كل مجموعة لها قناتها المستقلة الخاصة بها).
    يعيد True للسماح بمرور الرسالة، أو False في حال تم حذفها بسبب عدم الاشتراك.
    """
    fs_config = bot_data.get("force_sub_groups", {}).get(str(chat_id))
    if not fs_config or not fs_config.get("enabled"):
        return True  # لا يوجد اشتراك إجباري مفعل لهذه المجموعة تحديداً
    channel_id = fs_config.get("channel_id")
    try:
        member = await context.bot.get_chat_member(channel_id, user_id)
        subscribed = member.status not in ['left', 'kicked']
    except Exception:
        # تعذر التحقق (غالباً بسبب فقدان صلاحية الإشراف داخل القناة)
        # نسمح بمرور الرسالة تفادياً لتعطيل المجموعة بالكامل بسبب خلل مؤقت في الصلاحيات
        return True
    if subscribed:
        return True
    try:
        await update.message.delete()
    except Exception:
        pass
    channel_name = fs_config.get("channel_title") or "القناة"
    # Mention حقيقي للمستخدم نفسه باستخدام Telegram TEXT_MENTION entity.
    # هذه الطريقة لا تعتمد على Username (@username) إطلاقاً، بل تربط النص مباشرةً
    # بمعرّف الحساب user_id، ولذلك تكون الإشارة للشخص المحدد نفسه حتى لو لم يكن لديه Username.
    user = update.effective_user
    display_name = (user.first_name or user.full_name or "العضو") if user else "العضو"
    safe_channel_name = str(channel_name)
    # نضع الرمز @ فعلياً داخل النص، ثم نجعل كامل @ + الاسم TEXT_MENTION
    # مرتبطاً بـ user.id. بهذه الطريقة سيظهر للمستخدم كـ @اسم، ويكون قابلاً
    # للضغط ويشير إلى الحساب المحدد نفسه، حتى لو لم يكن لديه Username عام.
    mention_text = f"@{display_name}"
    text = (
        f"🔒 عذراً يا {mention_text}،\n"
        f"يرجى الاشتراك أولاً في قناة «{safe_channel_name}» حتى تتمكن من المشاركة وإرسال الرسائل داخل هذه المجموعة."
    )

    # Telegram يحسب offset/length بوحدات UTF-16، لذلك نحسبها بدقة بدلاً من
    # الاعتماد على len() وحدها، خصوصاً مع العربية والإيموجي.
    mention_prefix = "🔒 عذراً يا "
    mention_start = len(mention_prefix.encode("utf-16-le")) // 2
    mention_length = len(mention_text.encode("utf-16-le")) // 2
    entities = [
        MessageEntity(
            type=MessageEntity.TEXT_MENTION,
            offset=mention_start,
            length=mention_length,
            user=user
        )
    ] if user else None

    try:
        warn = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            entities=entities,
            reply_markup=build_force_sub_join_keyboard(fs_config)
        )
        asyncio.create_task(delete_after_delay(warn, 25))
    except Exception:
        pass
    return False
async def check_word_watch(update, context, chat_id, bot_data, text):
    """
    يفحص الرسالة بحثاً عن كلمات/عبارات مستهدفة إن كانت المجموعة ضمن المجموعات المستهدفة والمراقبة مفعّلة،
    وعند الاكتشاف يرسل تنبيهاً للمشرفين المحددين لهذه المجموعة مع Mention حقيقي لصاحب الرسالة وللمشرفين.
    """
    ww = bot_data.get("word_watch", {})
    targets = ww.get("target_groups", {})
    if not targets.get(str(chat_id)):
        return False
    watched_words = ww.get("words", [])
    found_word = detect_watched_word(text, watched_words)
    if not found_word:
        return False
    user = update.effective_user
    mention = build_user_mention_html(user) if user else "عضو"
    group_name = bot_data.get("groups", {}).get(str(chat_id), "المجموعة")
    alert_admins = ww.get("alert_admins", {}).get(str(chat_id), [])
    admins_line = " ".join(alert_admins) if alert_admins else "لا يوجد مشرفون محددون لهذه المجموعة"
    alert_text = (
        "🚨 <b>تم اكتشاف كلمة مستهدفة</b>\n\n"
        f"👤 العضو: {mention}\n"
        f"🔎 الكلمة/العبارة المكتشفة: {html.escape(found_word)}\n"
        f"📍 المجموعة: {html.escape(group_name)}\n\n"
        f"{admins_line}"
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=alert_text, parse_mode='HTML')
    except Exception:
        logging.exception("فشل إرسال تنبيه مراقبة الكلمات")
    return True
async def group_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return
    msg = update.message
    chat_id = update.effective_chat.id
    # استثناء منشورات القناة المربوطة والرسائل المرسلة باسم الأدمن المتخفي (GroupAnonymousBot / اسم المجموعة)
    if msg.is_automatic_forward or (msg.sender_chat and msg.sender_chat.id == chat_id):
        return
    user_id = update.effective_user.id if update.effective_user else None
    register_group(chat_id, update.effective_chat.title or "مجموعة")
    bot_data = load_data()
    user_is_admin = False
    if user_id:
        user_is_admin = await is_group_admin(context, chat_id, user_id)
    # فحص الوضع الصامت (يُطبَّق فقط على المجموعة المستهدفة المحددة)
    if is_silent_active(bot_data, chat_id):
        if user_id and not user_is_admin:
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
    # المشرفون معفيون من فلاتر المحتوى (كلمات/إيموجي/روابط/ملصقات/نداء الاستغاثة/الاشتراك الإجباري)
    if user_is_admin:
        return
    first_name = update.effective_user.first_name if update.effective_user else "المستخدم"
    # فحص الاشتراك الإجباري الخاص بهذه المجموعة تحديداً (إن وُجد ومفعّل)
    if user_id:
        allowed = await check_force_subscription(update, context, chat_id, bot_data, user_id, first_name)
        if not allowed:
            return
    # فحص الملصقات (ستيكرز) والصور المتحركة GIF
    if msg.sticker:
        is_animated_or_video = bool(msg.sticker.is_animated or msg.sticker.is_video)
        if bot_data.get("block_animated_stickers") and is_animated_or_video:
            await delete_and_warn(msg, context, chat_id, first_name, "ملصق متحرك")
            return
        if bot_data.get("block_stickers"):
            await delete_and_warn(msg, context, chat_id, first_name, "ملصقات (ستيكرز)")
            return
    if msg.animation and bot_data.get("block_animated_stickers"):
        await delete_and_warn(msg, context, chat_id, first_name, "صورة متحركة GIF")
        return
    # فحص الكلمات والإيموجي والروابط الممنوعة + نداء الاستغاثة
    text = msg.text or msg.caption
    if not text:
        return
    # نداء الاستغاثة (يُفحص بشكل مستقل عن باقي المخالفات)
    handled = await handle_rescue_keyword(update, context, chat_id, bot_data, text)
    if handled:
        return
    # مراقبة الكلمات والعبارات (نظام مستقل جديد، لا يحذف الرسالة، فقط ينبّه المشرفين المحددين)
    await check_word_watch(update, context, chat_id, bot_data, text)
    text_lower = text.lower()
    forbidden_emojis = bot_data.get("emojis", [])
    forbidden_words = bot_data.get("words", [])
    forbidden_links = bot_data.get("banned_links", [])
    block_all_links = bot_data.get("block_all_links", False)
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
    url_regex = re.compile(r'(https?://\S+|t\.me/\S+|www\.\S+)', re.IGNORECASE)
    has_link = bool(url_regex.search(text))
    if not is_violating and block_all_links and has_link:
        is_violating = True
        reason = "رابط ممنوع"
    if not is_violating:
        for link in forbidden_links:
            if link and link in text_lower:
                is_violating = True
                reason = "رابط محظور"
                break
    if is_violating:
        await delete_and_warn(msg, context, chat_id, first_name, reason)
# === 10. معالج أخطاء عام (شبكة أمان) ===
async def global_error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """
    يلتقط أي استثناء غير متوقع يحدث داخل أي معالج (Handler) في البوت ويسجّله،
    ويحاول إعلام المستخدم بدل ترك الطلب بدون أي رد (وهو ما كان يسبب انطباع 'البوت لا يرد').
    """
    logging.error("حدث استثناء غير متوقع أثناء معالجة تحديث:", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ حدث خطأ غير متوقع أثناء تنفيذ هذا الإجراء. يرجى المحاولة مرة أخرى، "
                "وفي حال تكرر الخطأ يرجى التواصل مع المطور."
            )
    except Exception:
        pass
# === 11. تشغيل البوت ===
async def main():
    if not BOT_TOKEN:
        print("خطأ: لم يتم ضبط BOT_TOKEN!")
        return
    await start_web_server()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    # إشعار الإدارة عند إضافة البوت أو ترقيته إلى مشرف في مجموعة.
    app.add_handler(MessageHandler(filters.StatusUpdate.MY_CHAT_MEMBER, bot_my_chat_member_handler))

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (~filters.COMMAND), handle_private_message))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.Regex(r"^/(حظر|كتم|الغاء_الحظر|الغاء_الكتم|ban|mute|unban|unmute)"), admin_actions_handler))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_filter))
    app.add_error_handler(global_error_handler)
    print("البوت يعمل بنجاح...")
    await app.initialize()
    await app.start()
    asyncio.create_task(mute_watcher_loop(app))
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()
if __name__ == '__main__':
    asyncio.run(main())
