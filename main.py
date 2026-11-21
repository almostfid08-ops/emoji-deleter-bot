import os
import time
import math
import logging
import asyncio
import json
import re
import html as html_lib
from datetime import datetime, timedelta, timezone
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.error import RetryAfter, Forbidden, BadRequest
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    filters
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
INITIAL_ADMINS = [1611988598, 7065061464]
DATA_FILE = "bot_data.json"
ACTIVITY_FILE = "member_activity.json"

# توقيت الجزائر (UTC+1 بدون توقيت صيفي) يُستخدم في الإشعارات فقط
LOCAL_TZ = timezone(timedelta(hours=1))

# حالات الانتظار الخاصة بلوحة تحكم المشرفين
WAITING_STATES = {}
TEMP_BROADCAST = {}

# تتبّع وقتي (غير محفوظ) لنداءات الاستغاثة داخل كل مجموعة
# {chat_id: {"count": int, "callers": [names]}}
RESCUE_TRACK = {}

# تتبّع وقتي (غير محفوظ) لعدد الرسائل العشوائية المرسلة من غير المشرفين في الخاص
# {user_id: int}
PRIVATE_MSG_TRACK = {}

# تتبّع وقتي (غير محفوظ) لمخالفات الأعضاء داخل كل مجموعة (نظام الكتم التلقائي)
# {(chat_id, user_id): {"count": int, "ts": float}}
STRIKE_TRACK = {}

# تتبّع وقتي (غير محفوظ) لآخر رسالة اشتراك إجباري أُرسلت لكل عضو (لمنع تكرار الإزعاج)
# {(chat_id, user_id): timestamp}
FS_WARN_TRACK = {}

# مراجع لمهام المسح الجارية في الخلفية (حتى لا يجمعها جامع المهملات قبل انتهائها)
BACKGROUND_TASKS = set()

# ---------- حالة التاغ الجماعي (وقتية، غير محفوظة) ----------
# عمليات التاغ الجارية: {chat_id: {"id": int, "cancelled": bool, ...}}
TAG_JOBS = {}
# قوائم الاختيار المعلّقة: {(chat_id, menu_message_id): {...}}
TAG_PENDING = {}
# عدّاد معرّفات عمليات التاغ (لمنع تأثير أزرار قديمة على عملية جديدة)
TAG_JOB_SEQ = {"n": 0}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


# === سجل نشاط الأعضاء (يُستخدم في التاغ الجماعي) ===
# تيليجرام لا تتيح للبوتات جلب قائمة أعضاء المجموعة، لذلك يسجّل البوت آخر رسالة كتبها كل عضو
# (وانضمام الأعضاء ومغادرتهم). يُحفظ في ملف مستقل عن bot_data.json حتى لا يؤثر على بياناتك الحالية،
# ويُكتب على القرص بشكل دوري (كل 20 ثانية عند وجود تغيير) بدل كتابته مع كل رسالة.
# الصيغة: {group_id(str): {user_id(str): {"n": الاسم, "t": وقت آخر رسالة (0 = لم يكتب بعد)}}}
ACTIVITY_STATE = {"dirty": False}


def load_activity():
    if os.path.exists(ACTIVITY_FILE):
        try:
            with open(ACTIVITY_FILE, "r", encoding="utf-8") as f:
                content = json.load(f)
                if isinstance(content, dict):
                    return content
        except Exception:
            logging.exception("تعذّر قراءة ملف نشاط الأعضاء، سيبدأ سجل جديد")
    return {}


MEMBER_ACTIVITY = load_activity()


def flush_activity():
    """يحفظ سجل النشاط على القرص (كتابة آمنة عبر ملف مؤقت) إن وُجدت تغييرات."""
    if not ACTIVITY_STATE["dirty"]:
        return
    ACTIVITY_STATE["dirty"] = False
    try:
        tmp_path = ACTIVITY_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(MEMBER_ACTIVITY, f, ensure_ascii=False)
        os.replace(tmp_path, ACTIVITY_FILE)
    except Exception:
        ACTIVITY_STATE["dirty"] = True
        logging.exception("فشل حفظ ملف نشاط الأعضاء")


async def activity_flush_loop():
    while True:
        await asyncio.sleep(20)
        try:
            flush_activity()
        except Exception:
            logging.exception("خطأ في حلقة حفظ النشاط")


def record_activity(chat_id, user, is_message=True):
    """
    يسجّل العضو في سجل المجموعة.
    is_message=True  → يحدّث وقت آخر رسالة (نشاط كتابي).
    is_message=False → يسجّل العضو فقط (مثل الانضمام) دون اعتباره نشاطاً كتابياً.
    """
    if not user or getattr(user, "is_bot", False):
        return
    grp = MEMBER_ACTIVITY.setdefault(str(chat_id), {})
    uid = str(user.id)
    rec = grp.get(uid) or {"n": "", "t": 0}
    name = (getattr(user, "full_name", None) or getattr(user, "first_name", None) or "عضو").strip()[:40] or "عضو"
    rec["n"] = name
    if is_message:
        rec["t"] = int(time.time())
    grp[uid] = rec
    ACTIVITY_STATE["dirty"] = True


def forget_member(chat_id, user_id):
    grp = MEMBER_ACTIVITY.get(str(chat_id))
    if grp and grp.pop(str(user_id), None) is not None:
        ACTIVITY_STATE["dirty"] = True


# الإعدادات الافتراضية للميزات الجديدة (تُدمج تلقائياً مع أي بيانات قديمة دون التأثير عليها)
DEFAULT_SETTINGS = {
    "notify_join_leave": True,    # إشعار المطور عند إضافة/إخراج البوت من أي مجموعة
    "group_welcome": True,        # رسالة ترحيب وتعليمات داخل المجموعة عند إضافة البوت
    "clean_service": False,       # حذف رسائل انضمام/مغادرة الأعضاء
    "strikes_enabled": False,     # نظام المخالفات والكتم التلقائي
    "strikes_limit": 3,           # عدد المخالفات قبل الكتم
    "strikes_minutes": 10,        # مدة الكتم التلقائي بالدقائق
    "block_whispers": True,       # منع الهمسات (الرسائل المرسلة عبر بوتات إنلاين خارجية) حتى من المشرفين
    "block_premium_stickers": True  # حذف الملصقات المتحركة/الفيديو الشبيه بالملصق من أصحاب الحسابات المميزة
}

# أوامر مسح الرسائل الافتراضية (يمكن للمشرف إضافة أوامر أخرى من لوحة التحكم)
# الصيغة: <الأمر> <العدد>  مثال: مسح 100  أو  بوت امسح 100
DEFAULT_PURGE_COMMANDS = ["مسح", "امسح", "بوت مسح", "بوت امسح"]

# أقصى عدد رسائل يمكن مسحه في أمر واحد
PURGE_MAX_COUNT = 5000

# === إعدادات التاغ الجماعي ===
TAG_BATCH_SIZE = 10          # عدد الأعضاء في كل رسالة
TAG_BATCH_DELAY = 2.5        # الفاصل بين كل رسالتين (ثوانٍ) لتفادي حدود تيليجرام
DEFAULT_TAG_MESSAGE = "📢 نداء لأعضاء المجموعة"
TAG_MESSAGE_MAX_LEN = 500
TAG_PENDING_TTL = 900        # مدة صلاحية قائمة الاختيار (ثوانٍ)

# (المفتاح، عدد الأيام، نص الزر)
TAG_PERIODS = [
    ("7", 7, "🕐 آخر 7 أيام"),
    ("20", 20, "📅 آخر 20 يوماً"),
    ("30", 30, "🗓️ آخر 30 يوماً"),
    ("60", 60, "📆 آخر 60 يوماً"),
]
TAG_PERIOD_DAYS = {"7": 7, "20": 20, "30": 30, "60": 60, "all": None}
TAG_PERIOD_LABELS = {
    "7": "آخر 7 أيام (أسبوع)",
    "20": "آخر 20 يوماً",
    "30": "آخر 30 يوماً (شهر)",
    "60": "آخر 60 يوماً (شهران)",
    "all": "الجميع بدون استثناء",
}

# أوامر التاغ داخل المجموعة: /تاغ  /tag  /tagall  /منشن  أو كلمة «تاغ» / «منشن» وحدها
TAG_CMD_REGEX = r"^(?:/(?:tagall|tag|تاغ|منشن)(?:@\w+)?(?:\s|$)|(?:تاغ|منشن)\s*$)"

# === نصوص ثابتة ===
DEVELOPER_USERNAME = "@Nabil1r"

NON_ADMIN_INFO_TEXT = (
    "🤖 **أهلاً بك في بوت مراقبة المجموعات الدراسية!**\n\n"
    "بوت حماية وإدارة احترافي، مُصمَّم ليكون **المشرف الذكي داخل مجموعات الدراسة**؛ "
    "يحافظ على جو المذاكرة الهادئ ويتصدى لكل ما يشتّت الأعضاء أو يسيء للمجموعة، ويعمل على مدار الساعة.\n\n"
    "🛡️ **الحماية التلقائية:**\n"
    "• حذف الإيموجيات غير المرغوب بها.\n"
    "• حذف الكلمات الممنوعة.\n"
    "• حظر الروابط (جميعها أو نطاقات محددة).\n"
    "• حذف الملصقات (الستيكرز) والصور المتحركة GIF.\n"
    "• منع الهمسات القادمة من البوتات الخارجية بشكل صارم.\n"
    "• حذف الملصقات المتحركة المرسلة من أصحاب الحسابات المميزة.\n"
    "• نظام مخالفات يكتم المكرِّر تلقائياً لمدة محددة.\n"
    "• تنظيف رسائل انضمام ومغادرة الأعضاء.\n\n"
    "🧹 **مسح الرسائل:** يستطيع مشرفو البوت (الذين هم مشرفون في المجموعة أيضاً) مسح آخر عدد محدد من الرسائل "
    "بكتابة أمر مثل: مسح 100 أو بوت امسح 100.\n\n"
    "🏷️ **التاغ الجماعي:** منشن أعضاء المجموعة حسب نشاطهم (آخر 7 / 20 / 30 / 60 يوماً أو الجميع) "
    "على دفعات من 10 أعضاء في كل رسالة، مع زر إلغاء فوري للمشرفين.\n\n"
    "🔇 **الوضع الصامت:** تفعيل لمدة مؤقتة جاهزة، أو بجدول يومي (من/إلى) مع رسالة توضيحية للأعضاء.\n\n"
    "🆘 **نداء الاستغاثة:** عند تكرار الأعضاء لكلمة النداء يُفعَّل الوضع الصامت تلقائياً "
    "حتى يصل المشرفون، ويمكن للمشرف إلغاؤه بضغطة زر.\n\n"
    "📢 **الاشتراك الإجباري:** لكل مجموعة قناتها المستقلة؛ يُحذف كلام غير المشترك ويُوجَّه له تنبيه "
    "**بمنشن باسمه** يعلمه أن الرسالة موجهة له، مع زر انضمام وزر تحقق فوري.\n\n"
    "⚖️ **أوامر الإشراف السريعة (بالرد على الرسالة):** حظر، كتم، إلغاء الحظر، إلغاء الكتم — دائماً أو لمدة محددة.\n\n"
    "📣 **الإذاعة:** إرسال إعلان واحد لكل المجموعات دفعة واحدة، أو لمجموعة محددة، أو للقنوات، أو لمستخدمي البوت، "
    "مع أزرار روابط ملونة، وخيار **تثبيت** الإعلان بعد إرساله.\n\n"
    "🔔 **تنبيه المطور:** يصل المطور إشعار فوري عند إضافة البوت إلى أي مجموعة أو إخراجه منها، "
    "ويتضمن حساب الشخص والمجموعة والرابط والوقت.\n\n"
    f"للحصول على هذه الميزات داخل مجموعتك، يرجى التواصل مع مطور البوت {DEVELOPER_USERNAME} "
    "كي يقوم بتفعيل البوت لك وإضافتك كمشرف."
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
        # القنوات المسجّلة للإذاعة (المفتاح = معرّف القناة كنص)
        "channels": {},
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
        # تفاصيل المجموعات (من أضاف البوت ومتى + الرابط): المفتاح = group_id (str)
        "groups_info": {},
        # سجل المجموعات التي أُخرج منها البوت (آخر 50)
        "removed_groups": {},
        # إعدادات الميزات الجديدة
        "settings": dict(DEFAULT_SETTINGS),
        # أوامر مسح الرسائل (قابلة للتعديل من لوحة المشرفين)
        "purge_commands": list(DEFAULT_PURGE_COMMANDS),
        # بوتات الإنلاين المسموح لها بالإرسال رغم منع الهمسات (يوزرات بدون @ وبأحرف صغيرة)
        "allowed_inline_bots": []
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
                data.setdefault("channels", {})
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

                # الاشتراك الإجباري - لا يؤثر على أي إعداد قديم
                data.setdefault("force_sub_groups", {})

                # الميزات الجديدة (تتبع المجموعات + الإعدادات)
                data.setdefault("groups_info", {})
                data.setdefault("removed_groups", {})
                data.setdefault("settings", {})
                for key, value in DEFAULT_SETTINGS.items():
                    data["settings"].setdefault(key, value)

                # أوامر المسح + بوتات الإنلاين المسموحة (تُضاف تلقائياً للبيانات القديمة)
                data.setdefault("purge_commands", list(DEFAULT_PURGE_COMMANDS))
                data.setdefault("allowed_inline_bots", [])

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


async def is_real_group_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    """
    يتحقق أن العضو مشرف فعلي (مالك/مشرف) داخل المجموعة نفسها.
    على عكس is_group_admin، لا يكتفي بكون العضو مشرفاً للبوت؛ يُستخدم في أوامر المسح
    التي تشترط أن يكون المنفّذ مشرفاً في البوت ومشرفاً في المجموعة معاً.
    """
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


def h(text):
    """تهريب نص ليُستخدم داخل رسائل HTML بأمان."""
    return html_lib.escape(str(text if text is not None else ""))


def local_now_str():
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M") + " (توقيت الجزائر)"


def user_mention_html(user_id, name):
    """منشن (تاغ) لعضو عبر معرّفه، يصله بإشعار حتى لو لم يكن لديه يوزر."""
    return f'<a href="tg://user?id={user_id}">{h(name)}</a>'


def user_details_html(user):
    """تفاصيل كاملة عن مستخدم (للإشعارات): الاسم بمنشن + اليوزر + المعرّف."""
    username = f"@{h(user.username)}" if getattr(user, "username", None) else "لا يوجد"
    return (
        f"   • الاسم: {user_mention_html(user.id, user.full_name)}\n"
        f"   • اليوزر: {username}\n"
        f"   • المعرّف: <code>{user.id}</code>"
    )


def user_contact_url(user):
    if getattr(user, "username", None):
        return f"https://t.me/{user.username}"
    return f"tg://user?id={user.id}"


def retry_wait_seconds(err):
    """يحوّل قيمة retry_after (int أو timedelta حسب إصدار المكتبة) إلى ثوانٍ."""
    wait = err.retry_after
    return wait.total_seconds() if hasattr(wait, "total_seconds") else float(wait)


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


def member_is_subscribed(member):
    """يحدد هل العضو مشترك فعلاً في القناة (يعالج حالة restricted التي قد يكون صاحبها غادر)."""
    if member.status in ['left', 'kicked']:
        return False
    if member.status == 'restricted' and getattr(member, "is_member", True) is False:
        return False
    return True


# === 3.5 أدوات مسح الرسائل (ميزة جديدة) ===
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_text_spaces(text):
    """يوحّد المسافات (مسافة واحدة بين الكلمات) ويحذف الفراغات الزائدة."""
    return " ".join(str(text or "").strip().split())


def get_purge_commands(data):
    """يعيد قائمة أوامر المسح الحالية (أو الافتراضية إن كانت فارغة/تالفة)."""
    cmds = data.get("purge_commands")
    if not isinstance(cmds, list) or not cmds:
        return list(DEFAULT_PURGE_COMMANDS)
    return [c for c in cmds if isinstance(c, str) and c.strip()]


def match_purge_command(text, commands):
    """
    يفحص هل النص بصيغة «<أمر مسح> <عدد>» مثل: مسح 100 أو بوت امسح 1000.
    يدعم الأرقام العربية الهندية (١٠٠). يعيد العدد (int) أو None إن لم يتطابق.
    الأوامر الأطول تُفحص أولاً حتى لا يتداخل «مسح» مع «بوت مسح».
    """
    if not text:
        return None
    clean = normalize_text_spaces(text).translate(_ARABIC_DIGITS)
    if not clean:
        return None
    for cmd in sorted(commands, key=len, reverse=True):
        norm_cmd = normalize_text_spaces(cmd)
        if not norm_cmd:
            continue
        m = re.match(r"^" + re.escape(norm_cmd) + r"\s+(\d{1,7})$", clean, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


async def _delete_single_message(bot, chat_id, message_id, sem):
    """حذف رسالة واحدة (يُستخدم فقط كخطة بديلة إذا كانت نسخة المكتبة لا تدعم الحذف الجماعي)."""
    async with sem:
        for _ in range(2):
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
                return True, None
            except RetryAfter as e:
                wait = e.retry_after
                wait = wait.total_seconds() if hasattr(wait, "total_seconds") else float(wait)
                await asyncio.sleep(wait + 1)
            except Exception as e:
                return False, e
        return False, None


async def delete_message_ids(context: ContextTypes.DEFAULT_TYPE, chat_id: int, ids):
    """
    يحذف قائمة معرّفات رسائل على دفعات (100 رسالة لكل طلب).
    الرسائل غير الموجودة أو التي لا يمكن حذفها تُتخطى بصمت (سلوك تيليجرام).
    يعيد (نجاح، أول_خطأ) حيث النجاح = False فقط عند وجود مشكلة صلاحيات حقيقية.
    """
    bot = context.bot
    use_bulk = hasattr(bot, "delete_messages")
    deleted_any = False
    first_error = None

    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]

        if use_bulk:
            for _ in range(3):
                try:
                    await bot.delete_messages(chat_id=chat_id, message_ids=chunk)
                    deleted_any = True
                    break
                except RetryAfter as e:
                    wait = e.retry_after
                    wait = wait.total_seconds() if hasattr(wait, "total_seconds") else float(wait)
                    await asyncio.sleep(wait + 1)
                except Exception as e:
                    if first_error is None:
                        first_error = e
                    break
        else:
            sem = asyncio.Semaphore(10)
            results = await asyncio.gather(*[_delete_single_message(bot, chat_id, mid, sem) for mid in chunk])
            for ok, err in results:
                if ok:
                    deleted_any = True
                elif err is not None and first_error is None:
                    first_error = err

        await asyncio.sleep(0.3)

    perm_error = False
    if first_error is not None:
        err_text = str(first_error).lower()
        perm_error = any(k in err_text for k in ("rights", "can't be deleted", "forbidden", "not enough", "admin"))
    return (deleted_any or not perm_error), first_error


# === 4. القوائم واللوحات ===
def get_main_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📢 إذاعة عامة للمجموعات", callback_data="bc_all"),
         InlineKeyboardButton("🎯 إذاعة مخصصة لمجموعة", callback_data="bc_single_select")],
        [InlineKeyboardButton("👤 إذاعة للمستخدمين (خاص)", callback_data="bc_users"),
         InlineKeyboardButton("📡 إذاعة للقنوات", callback_data="bc_channels")],
        [InlineKeyboardButton("📊 الإحصائيات وسجل المجموعات", callback_data="show_stats")],
        [InlineKeyboardButton("🔇 إدارة الوضع الصامت", callback_data="manage_silent")],
        [InlineKeyboardButton("🆘 نداء الاستغاثة", callback_data="manage_rescue")],
        [InlineKeyboardButton("📢 الاشتراك الإجباري", callback_data="manage_force_sub")],
        [InlineKeyboardButton("🧹 أوامر مسح الرسائل", callback_data="manage_purge")],
        [InlineKeyboardButton("⚙️ الإشعارات والحماية المتقدمة", callback_data="manage_settings")],
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


# ---------- الخطوة الأخيرة قبل الإذاعة: إرسال عادي أم إرسال مع تثبيت (ميزة جديدة) ----------
def get_send_choice_keyboard():
    keyboard = [
        [InlineKeyboardButton("📨 إرسال عادي", callback_data="bc_send_normal")],
        [InlineKeyboardButton("📌 إرسال مع تثبيت الرسالة", callback_data="bc_send_pin")],
        [InlineKeyboardButton("❌ إلغاء الإذاعة", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_send_choice_text(bc_type):
    if bc_type == "users":
        pin_note = (
            "📌 **مع التثبيت:** تُثبَّت الرسالة داخل محادثة كل مستخدم مع البوت "
            "(قد يظهر للمستخدم إشعار «تم تثبيت رسالة»)."
        )
    elif bc_type == "channels":
        pin_note = (
            "📌 **مع التثبيت:** تُثبَّت الرسالة في كل قناة. يتطلب ذلك أن يملك البوت صلاحية "
            "تعديل الرسائل في القناة، وإلا تُرسل الرسالة عادية ويظهر ذلك في التقرير."
        )
    else:
        pin_note = (
            "📌 **مع التثبيت:** تُثبَّت الرسالة في كل مجموعة. يتطلب ذلك أن يملك البوت صلاحية "
            "«تثبيت الرسائل»، وإلا تُرسل الرسالة عادية ويظهر ذلك في التقرير."
        )
    return (
        "📮 **الخطوة الأخيرة: كيف تريد إرسال الإذاعة؟**\n\n"
        "📨 **إرسال عادي:** تصل الرسالة دون تثبيت.\n\n"
        f"{pin_note}"
    )


async def show_send_choice(message, user_id, reply_markup, as_new=False):
    """
    يعرض سؤال «إرسال عادي / إرسال مع تثبيت» ويحفظ لوحة الأزرار (إن وُجدت) إلى حين الضغط.
    as_new=True → يرسل السؤال كرسالة جديدة (بعد استلام تنسيق الأزرار)، وإلا يعدّل الرسالة الحالية.
    """
    data_bc = TEMP_BROADCAST.get(user_id)
    if not data_bc:
        text = "❌ حدث خطأ، أعد المحاولة من القائمة الرئيسية."
        if as_new:
            await message.reply_text(text, reply_markup=get_back_keyboard())
        else:
            await message.edit_text(text, reply_markup=get_back_keyboard())
        return

    data_bc["markup"] = reply_markup
    WAITING_STATES[user_id] = "ask_send_choice"
    text = get_send_choice_text(data_bc.get("type", ""))
    if as_new:
        await safe_reply_text(message, text, reply_markup=get_send_choice_keyboard())
    else:
        await safe_edit_text(message, text, reply_markup=get_send_choice_keyboard())


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


# ---------- لوحة الإشعارات والحماية المتقدمة (ميزة جديدة) ----------
def get_settings_keyboard(data):
    s = data.get("settings", {})

    def st(key):
        return "🟢" if s.get(key) else "🔴"

    allowed_count = len(data.get("allowed_inline_bots", []))

    keyboard = [
        [InlineKeyboardButton(f"{st('notify_join_leave')} إشعاري عند إضافة/إخراج البوت", callback_data="cfg_toggle_notify_join_leave")],
        [InlineKeyboardButton(f"{st('group_welcome')} رسالة ترحيب وتعليمات عند الإضافة", callback_data="cfg_toggle_group_welcome")],
        [InlineKeyboardButton(f"{st('clean_service')} حذف رسائل الانضمام والمغادرة", callback_data="cfg_toggle_clean_service")],
        [InlineKeyboardButton(f"{st('strikes_enabled')} نظام المخالفات والكتم التلقائي", callback_data="cfg_toggle_strikes_enabled")],
        [InlineKeyboardButton(f"🔢 عدد المخالفات قبل الكتم: {s.get('strikes_limit')}", callback_data="cfg_strikes_limit")],
        [InlineKeyboardButton(f"⏱️ مدة الكتم التلقائي: {s.get('strikes_minutes')} دقيقة", callback_data="cfg_strikes_minutes")],
        [InlineKeyboardButton(f"{st('block_whispers')} 🤫 منع الهمسات (حتى من المشرفين)", callback_data="cfg_toggle_block_whispers")],
        [InlineKeyboardButton(f"🤖 بوتات الإنلاين المسموحة: {allowed_count}", callback_data="manage_inline_bots")],
        [InlineKeyboardButton(f"{st('block_premium_stickers')} 💎 منع الملصقات المتحركة من الحسابات المميزة", callback_data="cfg_toggle_block_premium_stickers")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_settings_text():
    return (
        "⚙️ **الإشعارات والحماية المتقدمة**\n\n"
        "• **إشعاري عند إضافة/إخراج البوت:** يصلك تنبيه فوري بحساب الشخص والمجموعة والرابط والوقت.\n"
        "• **رسالة الترحيب:** تظهر مؤقتاً داخل المجموعة وتشرح الصلاحيات المطلوبة للبوت.\n"
        "• **حذف رسائل الانضمام والمغادرة:** يبقي المجموعة نظيفة.\n"
        "• **نظام المخالفات:** من يكرر مخالفة الفلاتر خلال ساعة يُكتم تلقائياً للمدة المحددة.\n"
        "• **منع الهمسات:** تُحذف فوراً أي رسالة مرسلة عبر بوت إنلاين خارجي (الهمسات) حتى لو كان مرسلها مشرفاً، "
        "مع تنبيه صارم مختصر.\n"
        "• **بوتات الإنلاين المسموحة:** استثناء بوتات معينة من المنع (مثل بوتات الصور أو GIF).\n"
        "• **منع الملصقات المتحركة من الحسابات المميزة:** يحذف الملصقات المتحركة وفيديو الستيكر "
        "وملصقات بريميوم المرسلة من أصحاب حسابات تيليجرام المميز (لا يشمل الفيديو العادي MP4)."
    )


# ---------- لوحة أوامر مسح الرسائل (ميزة جديدة) ----------
def get_purge_text(data):
    cmds = get_purge_commands(data)
    cmds_text = "\n".join(f"• `{c} 100`" for c in cmds)
    return (
        "🧹 **أوامر مسح الرسائل**\n\n"
        "يكتب المشرف الأمر داخل المجموعة بصيغة: **الأمر + العدد** فيُحذف آخر عدد من الرسائل "
        "(صور، فيديو، رسائل صوتية، نصوص... أي نوع) مع رسالة الأمر نفسها.\n\n"
        "🔐 **شرط التنفيذ:** أن يكون المُنفِّذ مشرفاً في البوت **ومشرفاً في المجموعة** معاً.\n\n"
        f"📌 **الأوامر الحالية (مثال بالعدد 100):**\n{cmds_text}\n\n"
        f"الحد الأقصى في الأمر الواحد: {PURGE_MAX_COUNT} رسالة.\n"
        "يمكنك إضافة أوامر جديدة أو حذف أي أمر بالأزرار أدناه."
    )


def get_purge_keyboard(data):
    cmds = get_purge_commands(data)
    keyboard = [[InlineKeyboardButton("➕ إضافة أمر مسح جديد", callback_data="purge_add")]]
    for i, c in enumerate(cmds):
        keyboard.append([InlineKeyboardButton(f"🗑️ حذف الأمر: {c}", callback_data=f"purge_del_{i}")])
    keyboard.append([InlineKeyboardButton("♻️ استعادة الأوامر الافتراضية", callback_data="purge_reset")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)


# ---------- لوحة بوتات الإنلاين المسموحة (ميزة جديدة) ----------
def get_inline_bots_text(data):
    bots = data.get("allowed_inline_bots", [])
    bots_text = "، ".join(f"@{b}" for b in bots) if bots else "لا توجد"
    return (
        "🤖 **بوتات الإنلاين المسموحة**\n\n"
        "عند تفعيل «منع الهمسات» تُحذف أي رسالة مرسلة عبر بوت إنلاين (تظهر بعبارة via @bot) "
        "حتى لو كان مرسلها مشرفاً، **إلا** البوتات المضافة في هذه القائمة.\n\n"
        f"القائمة الحالية:\n{bots_text}"
    )


def get_inline_bots_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ إضافة بوت مسموح", callback_data="inline_bot_add"),
         InlineKeyboardButton("🗑️ مسح الكل", callback_data="inline_bot_clear")],
        [InlineKeyboardButton("🔙 رجوع للإعدادات", callback_data="manage_settings")]
    ]
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


def build_force_sub_join_keyboard(cfg, target_user_id=None):
    """
    يبني لوحة (زر الانضمام للقناة + زر إعادة التحقق) لعرضها للعضو غير المشترك.
    زر التحقق مرتبط بمعرّف العضو المستهدف حتى لا يستطيع غيره الضغط عليه وحذف الرسالة.
    """
    url = None
    if cfg.get("channel_username"):
        url = f"https://t.me/{cfg['channel_username']}"
    elif cfg.get("invite_link"):
        url = cfg["invite_link"]

    buttons = []
    if url:
        buttons.append([InlineKeyboardButton("📢 الانضمام إلى القناة", url=url)])
    recheck_data = f"fs_recheck_{target_user_id}" if target_user_id else "fs_recheck"
    buttons.append([InlineKeyboardButton("✅ تحقق مرة أخرى", callback_data=recheck_data)])
    return InlineKeyboardMarkup(buttons)


# ---------- الإحصائيات وسجل المجموعات (ميزة جديدة) ----------
def build_stats_html(data):
    groups = data.get("groups", {})
    info = data.get("groups_info", {})
    removed = data.get("removed_groups", {})
    fs_active = sum(1 for c in data.get("force_sub_groups", {}).values() if c.get("enabled"))

    lines = [
        "📊 <b>الإحصائيات وسجل المجموعات</b>",
        "",
        f"• المجموعات النشطة: <b>{len(groups)}</b>",
        f"• القنوات المسجّلة للإذاعة: <b>{len(data.get('channels', {}))}</b>",
        f"• مستخدمو البوت (الخاص): <b>{len(data.get('users', []))}</b>",
        f"• مشرفو البوت: <b>{len(data.get('admins', []))}</b>",
        f"• مجموعات الاشتراك الإجباري المفعّل: <b>{fs_active}</b>",
        "",
        "👥 <b>المجموعات:</b>",
    ]

    total_len = sum(len(x) for x in lines)
    shown = 0
    for i, (gid, title) in enumerate(groups.items(), 1):
        inf = info.get(gid)
        entry = f"{i}. {h(title)}"
        if inf:
            adder = user_mention_html(inf.get("added_by_id"), inf.get("added_by_name", "غير معروف"))
            if inf.get("added_by_username"):
                adder += f" (@{h(inf['added_by_username'])})"
            entry += f"\n   ↳ أضافه: {adder} • {h(inf.get('added_at', '-'))}"
            if inf.get("link"):
                entry += f' • <a href="{h(inf["link"])}">رابط المجموعة</a>'
        else:
            entry += "\n   ↳ <i>مصدر الإضافة غير مسجّل (أُضيف قبل تفعيل التتبع)</i>"
        if total_len + len(entry) > 3300:
            break
        lines.append(entry)
        total_len += len(entry)
        shown += 1

    if shown < len(groups):
        lines.append(f"… و {len(groups) - shown} مجموعة أخرى.")

    if removed:
        lines.append("")
        lines.append("🚪 <b>آخر المجموعات التي أُخرج منها البوت:</b>")
        for gid, r in list(removed.items())[-5:]:
            entry = f"• {h(r.get('title', gid))} — {h(r.get('removed_at', '-'))}"
            if r.get("removed_by_name"):
                entry += f" — بواسطة {h(r['removed_by_name'])}"
            if total_len + len(entry) > 3900:
                break
            lines.append(entry)
            total_len += len(entry)

    return "\n".join(lines)


async def send_non_admin_response(update: Update):
    """رسالة تعريفية لغير المشرفين في أول رسالة، ثم رسالة توضح أن الرسائل لا تصل للمطور بعد ذلك."""
    user_id = update.effective_user.id
    count = PRIVATE_MSG_TRACK.get(user_id, 0) + 1
    PRIVATE_MSG_TRACK[user_id] = count

    if count <= 1:
        await safe_reply_text(update.message, NON_ADMIN_INFO_TEXT)
    else:
        await safe_reply_text(update.message, NON_ADMIN_SPAM_TEXT)


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


# === 4.5 تتبع إضافة/إخراج البوت من المجموعات (ميزة جديدة) ===
def _in_chat(chat_member):
    """هل العضو (البوت هنا) موجود فعلاً داخل الدردشة؟"""
    if chat_member.status in ("member", "administrator", "creator"):
        return True
    if chat_member.status == "restricted":
        return bool(getattr(chat_member, "is_member", True))
    return False


async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text, keyboard=None):
    """يرسل إشعاراً لكل مشرفي البوت (بصيغة HTML)."""
    data = load_data()
    for admin_id in data.get("admins", []):
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode='HTML',
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
        except Exception:
            # قد يكون المشرف لم يبدأ البوت بعد أو حظره؛ لا نوقف الإشعار لبقية المشرفين
            pass


async def get_group_link(context: ContextTypes.DEFAULT_TYPE, chat, stored_link=None):
    """
    يحاول جلب رابط المجموعة دون إتلاف أي رابط قائم:
    1) اليوزر العام  2) رابط محفوظ سابقاً  3) رابط الدعوة الأساسي إن كان البوت مشرفاً
    4) إنشاء رابط دعوة إضافي (لا يُلغي الروابط الحالية) إن كانت لديه الصلاحية.
    """
    if getattr(chat, "username", None):
        return f"https://t.me/{chat.username}"
    if stored_link:
        return stored_link
    try:
        full = await context.bot.get_chat(chat.id)
        if getattr(full, "username", None):
            return f"https://t.me/{full.username}"
        if getattr(full, "invite_link", None):
            return full.invite_link
    except Exception:
        pass
    try:
        invite = await context.bot.create_chat_invite_link(chat.id, name="BotOwner")
        return invite.invite_link
    except Exception:
        return None


async def send_group_welcome(context: ContextTypes.DEFAULT_TYPE, chat, is_admin):
    if is_admin:
        text = (
            "🛡️ شكراً لإضافتي! تم تفعيل البوت كمشرف ✅\n"
            f"لتفعيل ميزات الحماية الكاملة داخل المجموعة تواصل مع المطور {DEVELOPER_USERNAME}."
        )
    else:
        text = (
            "🛡️ شكراً لإضافتي إلى المجموعة!\n\n"
            "لأتمكن من حماية المجموعة يرجى رفعي إلى **مشرف** مع تفعيل الصلاحيات التالية:\n"
            "• حذف الرسائل\n"
            "• حظر المستخدمين\n"
            "• دعوة المستخدمين عبر رابط\n\n"
            f"ولتفعيل ميزات الحماية تواصل مع المطور {DEVELOPER_USERNAME}."
        )
    try:
        msg = await context.bot.send_message(chat_id=chat.id, text=text)
        asyncio.create_task(delete_after_delay(msg, 90))
    except Exception:
        pass


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يُستدعى عند أي تغيير في حالة البوت داخل مجموعة/قناة: إضافة، إخراج، ترقية، تنزيل."""
    cmu = update.my_chat_member
    if not cmu:
        return

    chat = cmu.chat
    if chat.type == "private":
        return

    try:
        actor = cmu.from_user
        old_cm = cmu.old_chat_member
        new_cm = cmu.new_chat_member
        was_in = _in_chat(old_cm)
        now_in = _in_chat(new_cm)

        data = load_data()
        settings = data.get("settings", {})
        notify = settings.get("notify_join_leave", True)
        gid = str(chat.id)
        is_group = chat.type in ("group", "supergroup")
        kind = "مجموعة" if is_group else "قناة"
        title = chat.title or "بدون اسم"

        # ---------- 1) تمت إضافة البوت ----------
        if now_in and not was_in:
            stored = data["groups_info"].get(gid, {})
            link = await get_group_link(context, chat, stored.get("link"))
            try:
                members_count = await context.bot.get_chat_member_count(chat.id)
            except Exception:
                members_count = "غير متاح"

            now_time = local_now_str()
            if is_group:
                data["groups"][gid] = title
                data["groups_info"][gid] = {
                    "title": title,
                    "chat_type": chat.type,
                    "added_by_id": actor.id,
                    "added_by_name": actor.full_name,
                    "added_by_username": actor.username,
                    "added_at": now_time,
                    "link": link
                }
                data["removed_groups"].pop(gid, None)
                save_data(data)
            elif chat.type == "channel":
                # تسجيل القناة ضمن قائمة وجهات «إذاعة للقنوات»
                data.setdefault("channels", {})[gid] = title
                save_data(data)

            bot_is_admin = new_cm.status == "administrator"

            if notify:
                if link:
                    link_html = '<a href="' + h(link) + '">فتح ' + kind + '</a>'
                else:
                    link_html = 'غير متاح (خاصة والبوت ليس مشرفاً)'
                text = (
                    f"✅ <b>تمت إضافة البوت إلى {kind} جديدة!</b>\n\n"
                    f"📛 <b>الاسم:</b> {h(title)}\n"
                    f"🆔 <b>المعرّف:</b> <code>{chat.id}</code>\n"
                    f"🔗 <b>الرابط:</b> {link_html}\n"
                    f"👥 <b>عدد الأعضاء:</b> {members_count}\n"
                    f"🤖 <b>حالة البوت:</b> {'مشرف ✅' if bot_is_admin else 'عضو عادي ⚠️ (لن يعمل الحذف قبل ترقيته)'}\n\n"
                    f"👤 <b>الشخص الذي أضافه:</b>\n{user_details_html(actor)}\n\n"
                    f"🕒 <b>الوقت:</b> {h(now_time)}"
                )
                rows = [[InlineKeyboardButton("👤 التواصل مع الشخص", url=user_contact_url(actor))]]
                if link:
                    rows.append([InlineKeyboardButton(f"🔗 فتح {kind}", url=link)])
                rows.append([InlineKeyboardButton(f"🚪 إخراج البوت من هذه {kind}", callback_data=f"leave_ask_{chat.id}")])
                await notify_admins(context, text, InlineKeyboardMarkup(rows))

            if is_group and settings.get("group_welcome", True):
                await send_group_welcome(context, chat, bot_is_admin)
            return

        # ---------- 2) تم إخراج البوت ----------
        if was_in and not now_in:
            info = data["groups_info"].pop(gid, {})
            data["groups"].pop(gid, None)
            data.setdefault("channels", {}).pop(gid, None)
            RESCUE_TRACK.pop(chat.id, None)
            # سجل نشاط الأعضاء الخاص بهذه المجموعة لم يعد مطلوباً
            if MEMBER_ACTIVITY.pop(gid, None) is not None:
                ACTIVITY_STATE["dirty"] = True

            bot_left_by_self = actor.id == context.bot.id
            now_time = local_now_str()

            if is_group:
                data["removed_groups"][gid] = {
                    "title": title,
                    "removed_at": now_time,
                    "removed_by_id": actor.id,
                    "removed_by_name": actor.full_name,
                    "added_by_name": info.get("added_by_name"),
                    "link": info.get("link")
                }
                # نحتفظ بآخر 50 سجلاً فقط
                if len(data["removed_groups"]) > 50:
                    for old_key in list(data["removed_groups"].keys())[:-50]:
                        data["removed_groups"].pop(old_key, None)
            save_data(data)

            if notify:
                if bot_left_by_self:
                    who = "غادر البوت بأمر منك (من لوحة الإشعارات)"
                    who_block = ""
                else:
                    reason = "حظر البوت وطرده" if new_cm.status == "kicked" else "إخراج البوت (أو مغادرته)"
                    who = reason
                    who_block = f"\n\n👤 <b>الشخص الذي قام بذلك:</b>\n{user_details_html(actor)}"

                adder_block = ""
                if info.get("added_by_id"):
                    adder_name = user_mention_html(info["added_by_id"], info.get("added_by_name", "غير معروف"))
                    adder_block = f"\n\n➕ <b>كان قد أضافه:</b> {adder_name} بتاريخ {h(info.get('added_at', '-'))}"

                link = info.get("link") or (f"https://t.me/{chat.username}" if getattr(chat, "username", None) else None)
                if link:
                    link_html = '<a href="' + h(link) + '">فتح ' + kind + '</a>'
                else:
                    link_html = 'غير متاح'
                text = (
                    f"🚪 <b>تم إخراج البوت من {kind}!</b>\n\n"
                    f"📛 <b>الاسم:</b> {h(title)}\n"
                    f"🆔 <b>المعرّف:</b> <code>{chat.id}</code>\n"
                    f"🔗 <b>الرابط:</b> {link_html}\n"
                    f"📌 <b>السبب:</b> {who}"
                    f"{who_block}{adder_block}\n\n"
                    f"🕒 <b>الوقت:</b> {h(now_time)}"
                )
                rows = []
                if not bot_left_by_self:
                    rows.append([InlineKeyboardButton("👤 التواصل مع الشخص", url=user_contact_url(actor))])
                if link:
                    rows.append([InlineKeyboardButton(f"🔗 فتح {kind}", url=link)])
                await notify_admins(context, text, InlineKeyboardMarkup(rows) if rows else None)
            return

        # ---------- 3) تغيّرت صلاحيات البوت (ترقية/تنزيل) ----------
        if was_in and now_in and old_cm.status != new_cm.status and notify:
            if new_cm.status == "administrator":
                missing = []
                if not getattr(new_cm, "can_delete_messages", False):
                    missing.append("حذف الرسائل")
                if not getattr(new_cm, "can_restrict_members", False):
                    missing.append("حظر/كتم المستخدمين")
                if not getattr(new_cm, "can_invite_users", False):
                    missing.append("دعوة المستخدمين")
                warn = ("\n⚠️ <b>صلاحيات ناقصة:</b> " + "، ".join(missing)) if missing else "\n✅ جميع الصلاحيات الأساسية مفعّلة."
                text = (
                    f"⬆️ <b>تمت ترقية البوت إلى مشرف في {kind}</b>\n\n"
                    f"📛 {h(title)}\n🆔 <code>{chat.id}</code>\n"
                    f"👤 بواسطة: {user_mention_html(actor.id, actor.full_name)}"
                    f"{warn}\n\n🕒 {h(local_now_str())}"
                )
                await notify_admins(context, text)
            elif old_cm.status == "administrator":
                text = (
                    f"⬇️ <b>تم تنزيل البوت من الإشراف في {kind}</b>\n\n"
                    f"📛 {h(title)}\n🆔 <code>{chat.id}</code>\n"
                    f"👤 بواسطة: {user_mention_html(actor.id, actor.full_name)}\n"
                    "⚠️ لن يتمكن البوت من الحذف والإشراف حتى تتم ترقيته من جديد.\n\n"
                    f"🕒 {h(local_now_str())}"
                )
                await notify_admins(context, text)
    except Exception:
        logging.exception("خطأ أثناء معالجة تغيّر حالة البوت داخل مجموعة/قناة")


async def handle_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يرصد انضمام الأعضاء ومغادرتهم لتحديث سجل التاغ الجماعي (يصل هذا التحديث فقط إن كان البوت مشرفاً).
    الانضمام يسجّل العضو دون اعتباره نشاطاً كتابياً، والمغادرة تحذفه من السجل.
    """
    cm = update.chat_member
    if not cm:
        return
    try:
        chat = cm.chat
        if chat.type not in ("group", "supergroup"):
            return
        was_in = _in_chat(cm.old_chat_member)
        now_in = _in_chat(cm.new_chat_member)
        member_user = cm.new_chat_member.user
        if now_in and not was_in:
            record_activity(chat.id, member_user, is_message=False)
        elif was_in and not now_in:
            forget_member(chat.id, member_user.id)
    except Exception:
        logging.exception("خطأ أثناء تحديث سجل الأعضاء")


# === 4.6 التاغ الجماعي على دفعات (ميزة جديدة) ===
async def fetch_group_admins(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """يجلب مشرفي المجموعة (غير البوتات) لتضمينهم في خيار «الجميع بدون استثناء»."""
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        result = {}
        for a in admins:
            if getattr(a.user, "is_bot", False):
                continue
            result[str(a.user.id)] = (a.user.full_name or a.user.first_name or "عضو")[:40]
        return result
    except Exception:
        return {}


def select_tag_members(chat_id, days, admins_map):
    """
    يرجع قائمة (user_id, name) للأعضاء المراد منشنتهم، مرتبة من الأحدث نشاطاً إلى الأقدم.
    days=None → الجميع (كل من رصدهم البوت + مشرفو المجموعة)، وإلا من كتب خلال آخر days يوماً.
    """
    grp = MEMBER_ACTIVITY.get(str(chat_id), {})
    collected = {}
    if days is None:
        for uid, rec in grp.items():
            collected[uid] = (rec.get("t", 0), rec.get("n") or "عضو")
        for uid, name in (admins_map or {}).items():
            collected.setdefault(uid, (0, name))
    else:
        cutoff = time.time() - days * 86400
        for uid, rec in grp.items():
            if rec.get("t", 0) >= cutoff:
                collected[uid] = (rec.get("t", 0), rec.get("n") or "عضو")

    ordered = sorted(collected.items(), key=lambda kv: kv[1][0], reverse=True)
    result = []
    for uid, (_, name) in ordered:
        try:
            result.append((int(uid), name))
        except ValueError:
            continue
    return result


def format_tag_duration(batches):
    seconds = int(max(0, batches - 1) * TAG_BATCH_DELAY)
    if seconds < 60:
        return f"حوالي {max(seconds, 1)} ثانية"
    return f"حوالي {math.ceil(seconds / 60)} دقيقة"


def build_tag_menu_text(custom_text):
    return (
        "🏷️ <b>التاغ الجماعي</b>\n\n"
        f"📝 <b>الرسالة المرفقة مع كل دفعة:</b>\n{h(custom_text)}\n\n"
        "اختر فئة الأعضاء المراد منشنتهم حسب <b>آخر رسالة كتبوها</b> في المجموعة "
        f"(الرقم بين القوسين = عدد الأعضاء). يُمنشَن <b>{TAG_BATCH_SIZE}</b> أعضاء في كل رسالة.\n\n"
        "ℹ️ <i>تيليجرام لا تتيح للبوتات جلب قائمة الأعضاء كاملة، لذلك يشمل التاغ الأعضاء "
        "الذين رصدهم البوت داخل المجموعة.</i>"
    )


def get_tag_menu_keyboard(chat_id, admins_map):
    rows = []
    pair = []
    for key, days, label in TAG_PERIODS:
        count = len(select_tag_members(chat_id, days, admins_map))
        pair.append(InlineKeyboardButton(f"{label} ({count})", callback_data=f"tag_pick_{key}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    all_count = len(select_tag_members(chat_id, None, admins_map))
    rows.append([InlineKeyboardButton(f"👥 الجميع بدون استثناء ({all_count})", callback_data="tag_pick_all")])
    rows.append([InlineKeyboardButton("❌ إغلاق", callback_data="tag_close")])
    return InlineKeyboardMarkup(rows)


def build_tag_confirm_text(period_key, members_count, custom_text):
    batches = math.ceil(members_count / TAG_BATCH_SIZE)
    return (
        "🏷️ <b>تأكيد التاغ الجماعي</b>\n\n"
        f"📌 <b>الفئة:</b> {h(TAG_PERIOD_LABELS[period_key])}\n"
        f"👥 <b>عدد الأعضاء:</b> {members_count}\n"
        f"📦 <b>عدد الرسائل:</b> {batches} (كل رسالة {TAG_BATCH_SIZE} أعضاء)\n"
        f"⏱️ <b>المدة التقريبية:</b> {format_tag_duration(batches)}\n\n"
        f"📝 <b>الرسالة:</b>\n{h(custom_text)}\n\n"
        "يمكن إيقاف العملية في أي لحظة بزر «🛑 إلغاء التاغ» الظاهر أسفل آخر دفعة."
    )


def get_tag_confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 ابدأ التاغ", callback_data="tag_go")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="tag_back"),
         InlineKeyboardButton("❌ إغلاق", callback_data="tag_close")]
    ])


async def _edit_html(message, text, reply_markup=None):
    try:
        await message.edit_text(text, parse_mode='HTML', reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception:
        # غالباً «message is not modified»؛ لا حاجة لأي إجراء
        pass


async def tag_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    أمر التاغ داخل المجموعة: /تاغ [نص الرسالة]  أو  /tag [نص]  أو كلمة «تاغ» وحدها.
    إن أُرسل بالرد على رسالة بدون نص، يُستخدم نص الرسالة المردود عليها كرسالة مرفقة بكل دفعة.
    ينفّذه فقط مشرفو البوت ومشرفو المجموعة (يتجاهل غيرهم بصمت).
    """
    msg = update.message
    if not msg or not msg.text or not update.effective_chat:
        return

    chat = update.effective_chat
    chat_id = chat.id
    if chat.type not in ("group", "supergroup"):
        return

    parts = msg.text.strip().split(None, 1)
    first_token = parts[0]
    # إن كان الأمر موجهاً لبوت آخر (/tag@OtherBot) نتجاهله
    if "@" in first_token:
        addressed = first_token.split("@", 1)[1].lower()
        if addressed != (context.bot.username or "").lower():
            return

    user = update.effective_user
    is_anonymous_admin = bool(msg.sender_chat and msg.sender_chat.id == chat_id)
    allowed = is_anonymous_admin or bool(user and await is_group_admin(context, chat_id, user.id))
    if not allowed:
        return

    if chat_id in TAG_JOBS:
        try:
            warn = await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ هناك عملية تاغ جارية حالياً في هذه المجموعة. أوقفها بزر «🛑 إلغاء التاغ» أو انتظر اكتمالها."
            )
            asyncio.create_task(delete_after_delay(warn, 8))
        except Exception:
            pass
        return

    custom = parts[1].strip() if len(parts) > 1 else ""
    if not custom and msg.reply_to_message:
        custom = (msg.reply_to_message.text or msg.reply_to_message.caption or "").strip()
    custom = custom[:TAG_MESSAGE_MAX_LEN].strip() or DEFAULT_TAG_MESSAGE

    admins_map = await fetch_group_admins(context, chat_id)

    # تنظيف قوائم الاختيار المنتهية
    now_ts = time.time()
    for k in [k for k, v in TAG_PENDING.items() if now_ts - v.get("created", 0) > TAG_PENDING_TTL]:
        TAG_PENDING.pop(k, None)

    try:
        menu = await context.bot.send_message(
            chat_id=chat_id,
            text=build_tag_menu_text(custom),
            parse_mode='HTML',
            reply_markup=get_tag_menu_keyboard(chat_id, admins_map),
            disable_web_page_preview=True
        )
    except Exception:
        logging.exception("فشل إرسال قائمة التاغ")
        return

    TAG_PENDING[(chat_id, menu.message_id)] = {
        "text": custom,
        "admins": admins_map,
        "period": None,
        "created": now_ts
    }

    # نحذف رسالة الأمر للحفاظ على نظافة المجموعة (إن كانت الصلاحية متاحة)
    try:
        await msg.delete()
    except Exception:
        pass


async def tag_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أزرار التاغ الجماعي (القائمة + التأكيد + الإلغاء): متاحة فقط لمشرفي البوت ومشرفي المجموعة."""
    query = update.callback_query
    data = query.data or ""
    message = query.message
    if not message or message.chat.type == "private":
        await query.answer()
        return

    chat_id = message.chat.id
    user_id = query.from_user.id

    if not await is_group_admin(context, chat_id, user_id):
        await query.answer("❌ هذا الزر مخصص لمشرفي البوت ومشرفي المجموعة فقط.", show_alert=True)
        return

    # ---------- زر إلغاء التاغ ----------
    if data.startswith("tag_stop_"):
        try:
            job_id = int(data.replace("tag_stop_", ""))
        except ValueError:
            job_id = -1
        job = TAG_JOBS.get(chat_id)
        if not job or job["id"] != job_id:
            await query.answer("ℹ️ انتهت عملية التاغ بالفعل أو لم تعد نشطة.", show_alert=True)
            try:
                await message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return
        job["cancelled"] = True
        job["cancelled_by"] = query.from_user.full_name or query.from_user.first_name or "مشرف"
        job["cancelled_by_id"] = user_id
        await query.answer("🛑 تم إلغاء التاغ.")
        try:
            await message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    key = (chat_id, message.message_id)

    if data == "tag_close":
        TAG_PENDING.pop(key, None)
        await query.answer()
        try:
            await message.delete()
        except Exception:
            pass
        return

    pending = TAG_PENDING.get(key)
    if not pending or time.time() - pending.get("created", 0) > TAG_PENDING_TTL:
        TAG_PENDING.pop(key, None)
        await query.answer("⌛ انتهت صلاحية هذه القائمة، أعد كتابة أمر التاغ.", show_alert=True)
        try:
            await message.delete()
        except Exception:
            pass
        return

    if data == "tag_back":
        pending["period"] = None
        await query.answer()
        await _edit_html(
            message,
            build_tag_menu_text(pending["text"]),
            get_tag_menu_keyboard(chat_id, pending["admins"])
        )
        return

    if data.startswith("tag_pick_"):
        period_key = data.replace("tag_pick_", "")
        if period_key not in TAG_PERIOD_DAYS:
            await query.answer()
            return
        members = select_tag_members(chat_id, TAG_PERIOD_DAYS[period_key], pending["admins"])
        if not members:
            await query.answer("ℹ️ لا يوجد أعضاء مسجّلون ضمن هذه الفئة بعد.", show_alert=True)
            return
        pending["period"] = period_key
        await query.answer()
        await _edit_html(
            message,
            build_tag_confirm_text(period_key, len(members), pending["text"]),
            get_tag_confirm_keyboard()
        )
        return

    if data == "tag_go":
        period_key = pending.get("period")
        if period_key not in TAG_PERIOD_DAYS:
            await query.answer("ℹ️ اختر الفئة أولاً.", show_alert=True)
            return
        if chat_id in TAG_JOBS:
            await query.answer("⚠️ هناك عملية تاغ جارية بالفعل.", show_alert=True)
            return
        members = select_tag_members(chat_id, TAG_PERIOD_DAYS[period_key], pending["admins"])
        if not members:
            await query.answer("ℹ️ لا يوجد أعضاء ضمن هذه الفئة.", show_alert=True)
            return

        TAG_PENDING.pop(key, None)
        TAG_JOB_SEQ["n"] += 1
        job = {
            "id": TAG_JOB_SEQ["n"],
            "cancelled": False,
            "cancelled_by": None,
            "cancelled_by_id": None
        }
        TAG_JOBS[chat_id] = job
        await query.answer("🚀 بدأ التاغ الجماعي")
        try:
            await message.delete()
        except Exception:
            pass

        task = asyncio.create_task(run_tag_job(context, chat_id, job, members, pending["text"]))
        BACKGROUND_TASKS.add(task)
        task.add_done_callback(BACKGROUND_TASKS.discard)
        return

    await query.answer()


async def run_tag_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int, job, members, custom_text):
    """
    ينفّذ التاغ على دفعات (10 أعضاء لكل رسالة). كل دفعة تحمل الرسالة المرفقة وزر «إلغاء التاغ»؛
    وعند إرسال الدفعة التالية يُزال الزر من السابقة بحيث يبقى زر واحد ظاهر أسفل آخر دفعة.
    """
    batches = [members[i:i + TAG_BATCH_SIZE] for i in range(0, len(members), TAG_BATCH_SIZE)]
    total_batches = len(batches)
    header = h(custom_text)
    prev_msg_id = None
    tagged = 0
    failed = False

    cancel_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 إلغاء التاغ", callback_data=f"tag_stop_{job['id']}")]
    ])

    try:
        for idx, batch in enumerate(batches, 1):
            if job["cancelled"]:
                break

            lines = "\n".join(f"• {user_mention_html(uid, name)}" for uid, name in batch)
            body = (
                f"📢 <b>{header}</b>\n\n"
                f"{lines}\n\n"
                f"<i>📦 الدفعة {idx} من {total_batches}</i>"
            )

            sent = None
            for _ in range(3):
                try:
                    sent = await context.bot.send_message(
                        chat_id=chat_id,
                        text=body,
                        parse_mode='HTML',
                        reply_markup=cancel_kb,
                        disable_web_page_preview=True
                    )
                    break
                except RetryAfter as e:
                    await asyncio.sleep(retry_wait_seconds(e) + 1)

            if sent is None:
                failed = True
                break

            tagged += len(batch)

            # إزالة زر الإلغاء من الدفعة السابقة (يبقى الزر فقط تحت آخر دفعة)
            if prev_msg_id:
                try:
                    await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=prev_msg_id, reply_markup=None)
                except Exception:
                    pass
            prev_msg_id = sent.message_id

            if idx < total_batches:
                # انتظار موزّع على خطوات صغيرة ليُلاحَظ الإلغاء بسرعة
                waited = 0.0
                while waited < TAG_BATCH_DELAY and not job["cancelled"]:
                    await asyncio.sleep(0.5)
                    waited += 0.5
    except Exception:
        failed = True
        logging.exception("خطأ أثناء تنفيذ التاغ الجماعي")
    finally:
        TAG_JOBS.pop(chat_id, None)

    # إزالة زر الإلغاء من آخر دفعة
    if prev_msg_id:
        try:
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=prev_msg_id, reply_markup=None)
        except Exception:
            pass

    if job["cancelled"]:
        who = user_mention_html(job.get("cancelled_by_id") or 0, job.get("cancelled_by") or "مشرف")
        summary = f"🛑 تم إلغاء التاغ الجماعي بواسطة {who}.\n👥 تم منشن {tagged} من أصل {len(members)} عضواً."
    elif failed:
        summary = (
            f"⚠️ توقف التاغ الجماعي بسبب خطأ في الإرسال.\n👥 تم منشن {tagged} من أصل {len(members)} عضواً."
        )
    else:
        summary = f"✅ اكتمل التاغ الجماعي.\n👥 تم منشن {tagged} عضواً في {total_batches} رسالة."

    try:
        notice = await context.bot.send_message(chat_id=chat_id, text=summary, parse_mode='HTML')
        asyncio.create_task(delete_after_delay(notice, 30))
    except Exception:
        pass


# === 5. التحكم في الأزرار ===
async def handle_force_sub_recheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة زر '✅ تحقق مرة أخرى' الذي يظهر لأي عضو داخل المجموعة (وليس فقط للمشرفين)."""
    query = update.callback_query
    chat_id = query.message.chat.id

    # الزر الجديد يحمل معرّف العضو المستهدف: لا يسمح لغيره بالتحكم برسالته
    parts = query.data.split("_")
    if len(parts) >= 3 and parts[2].isdigit():
        target_id = int(parts[2])
        if query.from_user.id != target_id:
            await query.answer("❌ هذا الزر مخصص للعضو المذكور في الرسالة فقط.", show_alert=True)
            return

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
        subscribed = member_is_subscribed(member)
    except Exception:
        await query.answer("⚠️ تعذر التحقق حالياً، يرجى المحاولة مرة أخرى بعد قليل.", show_alert=True)
        return

    if subscribed:
        FS_WARN_TRACK.pop((chat_id, query.from_user.id), None)
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
    if action == "fs_recheck" or action.startswith("fs_recheck_"):
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
            "🧹 **أوامر مسح الرسائل (بدون رد):**\n"
            "• `مسح 100` أو `بوت امسح 100` : حذف آخر 100 رسالة (صور، فيديو، صوتيات... أي نوع).\n"
            "• `مسح 1000` : حذف آخر 1000 رسالة.\n"
            f"• الحد الأقصى: {PURGE_MAX_COUNT} رسالة في الأمر الواحد.\n"
            "• شرط التنفيذ: أن يكون المنفّذ مشرفاً في البوت **ومشرفاً في المجموعة** معاً.\n"
            "• يمكنك إضافة أوامر مسح أخرى من زر «🧹 أوامر مسح الرسائل».\n\n"
            "🏷️ **التاغ الجماعي (داخل المجموعة):**\n"
            "• `/تاغ نص الرسالة` أو `/tag نص الرسالة` : تظهر قائمة لاختيار الفئة "
            "(آخر 7 / 20 / 30 / 60 يوماً أو الجميع).\n"
            "• يمكنك كتابة `/تاغ` بالرد على رسالة ليُستخدم نصها كرسالة مرفقة بكل دفعة، "
            "أو كتابة كلمة «تاغ» وحدها لاستخدام الرسالة الافتراضية.\n"
            f"• يُمنشَن {TAG_BATCH_SIZE} أعضاء في كل رسالة، ويظهر زر «🛑 إلغاء التاغ» لمشرفي البوت والمجموعة فقط.\n"
            "• يعتمد التاغ على الأعضاء الذين رصدهم البوت (آخر رسالة كتبوها)، فيبدأ الرصد من لحظة تشغيل هذه الميزة.\n\n"
            "📣 **تثبيت الإذاعة:** في آخر خطوة قبل الإرسال تختار بين «إرسال عادي» و«إرسال مع تثبيت».\n\n"
            "🆘 **نداء الاستغاثة:** إذا كتب الأعضاء كلمة النداء المحددة عدداً من المرات المتتالية "
            "داخل المجموعة المستهدفة، يتم تفعيل الوضع الصامت تلقائياً."
        )
        await safe_edit_text(query.message, help_text, reply_markup=get_back_keyboard())

    # ---------- الإحصائيات وسجل المجموعات (جديد) ----------
    elif action == "show_stats":
        try:
            await query.message.edit_text(
                build_stats_html(bot_data),
                parse_mode='HTML',
                reply_markup=get_back_keyboard(),
                disable_web_page_preview=True
            )
        except Exception:
            logging.exception("فشل عرض الإحصائيات")
            await query.message.edit_text("❌ تعذّر عرض الإحصائيات حالياً.", reply_markup=get_back_keyboard())

    # ---------- الإشعارات والحماية المتقدمة (جديد) ----------
    elif action == "manage_settings":
        await safe_edit_text(query.message, get_settings_text(), reply_markup=get_settings_keyboard(bot_data))

    elif action.startswith("cfg_toggle_"):
        key = action.replace("cfg_toggle_", "")
        if key in DEFAULT_SETTINGS and isinstance(DEFAULT_SETTINGS[key], bool):
            bot_data["settings"][key] = not bot_data["settings"].get(key, DEFAULT_SETTINGS[key])
            save_data(bot_data)
        await safe_edit_text(query.message, get_settings_text(), reply_markup=get_settings_keyboard(bot_data))

    elif action == "cfg_strikes_limit":
        WAITING_STATES[user_id] = "cfg_strikes_limit"
        await query.message.edit_text(
            "🔢 **أرسل عدد المخالفات التي يُكتم بعدها العضو تلقائياً (رقم):**",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("manage_settings")
        )

    elif action == "cfg_strikes_minutes":
        WAITING_STATES[user_id] = "cfg_strikes_minutes"
        await query.message.edit_text(
            "⏱️ **أرسل مدة الكتم التلقائي بالدقائق (رقم):**",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard("manage_settings")
        )

    # ---------- بوتات الإنلاين المسموحة (جديد) ----------
    elif action == "manage_inline_bots":
        WAITING_STATES[user_id] = None
        await safe_edit_text(query.message, get_inline_bots_text(bot_data), reply_markup=get_inline_bots_keyboard())

    elif action == "inline_bot_add":
        WAITING_STATES[user_id] = "add_inline_bot"
        await safe_edit_text(
            query.message,
            "✏️ **أرسل يوزر بوت الإنلاين المسموح (مثال: @gif):**",
            reply_markup=get_back_keyboard("manage_inline_bots")
        )

    elif action == "inline_bot_clear":
        bot_data["allowed_inline_bots"] = []
        save_data(bot_data)
        await safe_edit_text(query.message, get_inline_bots_text(bot_data), reply_markup=get_inline_bots_keyboard())

    # ---------- أوامر مسح الرسائل (جديد) ----------
    elif action == "manage_purge":
        WAITING_STATES[user_id] = None
        await safe_edit_text(query.message, get_purge_text(bot_data), reply_markup=get_purge_keyboard(bot_data))

    elif action == "purge_add":
        WAITING_STATES[user_id] = "add_purge_cmd"
        await safe_edit_text(
            query.message,
            "✏️ **أرسل كلمة أو عبارة أمر المسح الجديد (بدون الرقم).**\n\n"
            "مثال: إذا أرسلت `نظف` فسيصبح الأمر داخل المجموعة: `نظف 100`",
            reply_markup=get_back_keyboard("manage_purge")
        )

    elif action.startswith("purge_del_"):
        try:
            idx = int(action.replace("purge_del_", ""))
        except ValueError:
            idx = -1
        cmds = get_purge_commands(bot_data)
        if len(cmds) <= 1:
            await safe_edit_text(
                query.message,
                "⚠️ لا يمكن حذف آخر أمر مسح متبقٍ. أضف أمراً جديداً أولاً ثم احذف القديم.\n\n" + get_purge_text(bot_data),
                reply_markup=get_purge_keyboard(bot_data)
            )
            return
        if 0 <= idx < len(cmds):
            removed_cmd = cmds.pop(idx)
            bot_data["purge_commands"] = cmds
            save_data(bot_data)
            await safe_edit_text(
                query.message,
                f"✅ تم حذف الأمر: {removed_cmd}\n\n" + get_purge_text(bot_data),
                reply_markup=get_purge_keyboard(bot_data)
            )
        else:
            await safe_edit_text(query.message, get_purge_text(bot_data), reply_markup=get_purge_keyboard(bot_data))

    elif action == "purge_reset":
        bot_data["purge_commands"] = list(DEFAULT_PURGE_COMMANDS)
        save_data(bot_data)
        await safe_edit_text(
            query.message,
            "✅ تمت استعادة أوامر المسح الافتراضية.\n\n" + get_purge_text(bot_data),
            reply_markup=get_purge_keyboard(bot_data)
        )

    # ---------- إخراج البوت من مجموعة عبر الإشعار (جديد) ----------
    elif action.startswith("leave_ask_"):
        gid = action.replace("leave_ask_", "")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، غادر الآن", callback_data=f"leave_do_{gid}")],
            [InlineKeyboardButton("❌ تراجع", callback_data="dismiss_msg")]
        ])
        await query.message.reply_text(
            f"⚠️ هل أنت متأكد من إخراج البوت من هذه الدردشة؟\n`{gid}`",
            parse_mode='Markdown',
            reply_markup=kb
        )

    elif action.startswith("leave_do_"):
        gid = action.replace("leave_do_", "")
        try:
            await context.bot.leave_chat(int(gid))
            await query.message.edit_text("✅ غادر البوت الدردشة بنجاح.")
        except Exception as e:
            await query.message.edit_text(f"❌ تعذّرت المغادرة: {e}")

    elif action == "dismiss_msg":
        try:
            await query.message.delete()
        except Exception:
            pass

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

    # ---------- الاشتراك الإجباري ----------
    elif action == "manage_force_sub":
        await query.message.edit_text(
            "📢 **إدارة الاشتراك الإجباري**\n\n"
            "من هنا يمكنك تفعيل الاشتراك الإجباري بشكل مستقل لكل مجموعة على حدة، بحيث يُطلب من "
            "الأعضاء الاشتراك في قناة محددة (خاصة بتلك المجموعة) قبل السماح لهم بالكتابة.\n\n"
            "💡 تُرسل رسالة التنبيه مع منشن (تاغ) للعضو المعني ليعلم أنها موجهة له خصيصاً.",
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
        await query.message.edit_text(
            "📝 **أرسل الآن منشور الإذاعة العامة، وسيصل إلى جميع المجموعات دفعة واحدة:**",
            reply_markup=get_back_keyboard()
        )

    elif action == "bc_users":
        WAITING_STATES[user_id] = "bc_msg_users"
        await query.message.edit_text("📝 **أرسل الآن منشور الإذاعة الخاص للمستخدمين:**", reply_markup=get_back_keyboard())

    elif action == "bc_channels":
        if not bot_data.get("channels"):
            await query.message.edit_text(
                "❌ لا توجد قنوات مسجلة للإذاعة بعد.\n\n"
                "أضف البوت كمشرف في القناة (مع صلاحية نشر الرسائل)، أو اربط القناة عبر "
                "«📢 الاشتراك الإجباري» ليتم تسجيلها تلقائياً، ثم أعد المحاولة.",
                reply_markup=get_back_keyboard()
            )
            return
        WAITING_STATES[user_id] = "bc_msg_channels"
        await query.message.edit_text(
            f"📝 **أرسل الآن منشور الإذاعة للقنوات ({len(bot_data.get('channels', {}))} قناة مسجلة):**",
            reply_markup=get_back_keyboard()
        )

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
        # الإرسال بدون أزرار: ننتقل أولاً لسؤال «إرسال عادي / إرسال مع تثبيت»
        await show_send_choice(query.message, user_id, None)

    elif action in ("bc_send_normal", "bc_send_pin"):
        data_bc = TEMP_BROADCAST.get(user_id)
        stored_markup = data_bc.get("markup") if data_bc else None
        await execute_broadcast(context, user_id, query.message, stored_markup, pin=(action == "bc_send_pin"))

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


# === 6. تنفيذ عملية الإذاعة (محسّنة: إرسال متزامن + تنظيف الوجهات الميتة + تقرير مفصل + تثبيت اختياري) ===
def _classify_send_error(err):
    """يصنّف خطأ الإرسال: dead = الوجهة لم تعد صالحة (طُرد البوت/حُذفت المجموعة/حُظر البوت)."""
    msg = str(err).lower()
    dead_markers = ["blocked", "kicked", "chat not found", "deleted", "not a member", "deactivated", "forbidden"]
    if any(marker in msg for marker in dead_markers):
        return "dead"
    return "fail"


async def execute_broadcast(context: ContextTypes.DEFAULT_TYPE, user_id: int, status_msg, reply_markup, pin=False):
    data_bc = TEMP_BROADCAST.get(user_id)
    if not data_bc:
        await status_msg.edit_text("❌ حدث خطأ، أعد المحاولة.", reply_markup=get_back_keyboard())
        return

    # حماية من الضغط المزدوج على زر الإرسال (حتى لا تُرسل الإذاعة مرتين)
    if data_bc.get("sending"):
        return
    data_bc["sending"] = True

    bc_type = data_bc["type"]
    msg_to_copy = data_bc["msg"]
    bot_data = load_data()

    await status_msg.edit_text("⏳ جاري الإذاعة مع التثبيت 📌..." if pin else "⏳ جاري الإذاعة...")

    targets = []
    if bc_type == "all":
        targets = list(bot_data.get("groups", {}).keys())
    elif bc_type == "channels":
        targets = list(bot_data.get("channels", {}).keys())
    elif bc_type == "users":
        targets = list(bot_data.get("users", []))
    elif bc_type.startswith("single_"):
        targets = [bc_type.replace("single_", "")]

    sem = asyncio.Semaphore(15)

    async def send_one(target_id):
        """يعيد (الوجهة، حالة الإرسال ok/fail/dead، حالة التثبيت None/pinned/pin_fail)."""
        async with sem:
            sent_msg = None
            for _ in range(2):
                try:
                    sent_msg = await msg_to_copy.copy(chat_id=int(target_id), reply_markup=reply_markup)
                    break
                except RetryAfter as e:
                    await asyncio.sleep(retry_wait_seconds(e) + 1)
                except (Forbidden, BadRequest) as e:
                    return target_id, _classify_send_error(e), None
                except Exception:
                    return target_id, "fail", None

            if sent_msg is None:
                return target_id, "fail", None

            pin_state = None
            if pin:
                # فشل التثبيت لا يُلغي نجاح الإرسال؛ يُسجَّل فقط في التقرير
                pin_state = "pin_fail"
                for _ in range(2):
                    try:
                        await context.bot.pin_chat_message(chat_id=int(target_id), message_id=sent_msg.message_id)
                        pin_state = "pinned"
                        break
                    except RetryAfter as e:
                        await asyncio.sleep(retry_wait_seconds(e) + 1)
                    except Exception:
                        break
            return target_id, "ok", pin_state

    results = await asyncio.gather(*[send_one(t) for t in targets]) if targets else []

    sent = sum(1 for _, r, _p in results if r == "ok")
    failed_ids = [t for t, r, _p in results if r != "ok"]
    dead_ids = [t for t, r, _p in results if r == "dead"]

    # تنظيف الوجهات الميتة تلقائياً (مجموعات/قنوات طُرد منها البوت / مستخدمون حظروا البوت)
    removed_count = 0
    if dead_ids:
        if bc_type == "users":
            for t in dead_ids:
                try:
                    bot_data["users"].remove(int(t))
                    removed_count += 1
                except (ValueError, KeyError):
                    pass
        else:
            for t in dead_ids:
                removed_group = bot_data["groups"].pop(str(t), None)
                removed_channel = bot_data.setdefault("channels", {}).pop(str(t), None)
                if removed_group is not None or removed_channel is not None:
                    removed_count += 1
        save_data(bot_data)

    TEMP_BROADCAST.pop(user_id, None)
    WAITING_STATES[user_id] = None

    def dest_name(t):
        return (
            bot_data.get("groups", {}).get(str(t))
            or bot_data.get("channels", {}).get(str(t))
            or str(t)
        )

    report = f"✅ تمت الإذاعة!\n- نجاح: {sent}\n- فشل: {len(failed_ids)}"

    if pin:
        pinned_count = sum(1 for _, r, p in results if p == "pinned")
        pin_failed_ids = [t for t, r, p in results if p == "pin_fail"]
        report += f"\n- 📌 تم التثبيت: {pinned_count}"
        if pin_failed_ids:
            report += f"\n- ⚠️ تعذّر التثبيت: {len(pin_failed_ids)} (أُرسلت الرسالة دون تثبيت)"

    if removed_count:
        if bc_type == "users":
            label = "مستخدم حظر البوت"
        elif bc_type == "channels":
            label = "قناة لم يعد البوت فيها"
        else:
            label = "مجموعة لم يعد البوت فيها"
        report += f"\n- 🧹 تمت إزالة {removed_count} ({label}) من القائمة تلقائياً"

    if failed_ids and bc_type != "users":
        names = [dest_name(t) for t in failed_ids[:10]]
        report += "\n\n⚠️ وجهات فشل الإرسال إليها:\n" + "\n".join(f"• {n}" for n in names)
        if len(failed_ids) > 10:
            report += f"\n… و {len(failed_ids) - 10} أخرى"

    if pin and bc_type != "users":
        pin_failed_ids = [t for t, r, p in results if p == "pin_fail"]
        if pin_failed_ids:
            names = [dest_name(t) for t in pin_failed_ids[:10]]
            report += (
                "\n\n📌 وجهات تعذّر التثبيت فيها (تأكد من صلاحية تثبيت الرسائل للبوت):\n"
                + "\n".join(f"• {n}" for n in names)
            )
            if len(pin_failed_ids) > 10:
                report += f"\n… و {len(pin_failed_ids) - 10} أخرى"

    await status_msg.edit_text(report, reply_markup=get_back_keyboard())


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
        btn_markup = parse_button_markup(update.message.text or "")
        if not btn_markup:
            await update.message.reply_text("❌ التنسيق غير صحيح! التأكد من إرسال رابط صحيح (https://...)\nحاول مرة أخرى:")
            return
        # بعد قبول الأزرار ننتقل لسؤال «إرسال عادي / إرسال مع تثبيت» قبل التنفيذ الفعلي
        await show_send_choice(update.message, user_id, btn_markup, as_new=True)

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

    # ---------- إعدادات نظام المخالفات (جديد) ----------
    elif state == "cfg_strikes_limit":
        WAITING_STATES[user_id] = None
        try:
            value = int(update.message.text.strip())
            if value < 1:
                raise ValueError
            bot_data["settings"]["strikes_limit"] = value
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم تحديث عدد المخالفات قبل الكتم إلى: {value}", reply_markup=get_back_keyboard("manage_settings"))
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال رقم صحيح أكبر من صفر.", reply_markup=get_back_keyboard("manage_settings"))

    elif state == "cfg_strikes_minutes":
        WAITING_STATES[user_id] = None
        try:
            value = int(update.message.text.strip())
            if value < 1:
                raise ValueError
            bot_data["settings"]["strikes_minutes"] = value
            save_data(bot_data)
            await update.message.reply_text(f"✅ تم تحديث مدة الكتم التلقائي إلى: {value} دقيقة", reply_markup=get_back_keyboard("manage_settings"))
        except ValueError:
            await update.message.reply_text("❌ يرجى إرسال رقم صحيح أكبر من صفر.", reply_markup=get_back_keyboard("manage_settings"))

    # ---------- إضافة أمر مسح جديد (جديد) ----------
    elif state == "add_purge_cmd":
        raw = update.message.text or ""
        cmd = normalize_text_spaces(raw).translate(_ARABIC_DIGITS)
        # نزيل أي رقم في نهاية النص إن أرسله المشرف مثل «نظف 100» ونحتفظ بالكلمة فقط
        cmd = re.sub(r"\s*\d+$", "", cmd).strip()

        if not cmd or len(cmd) > 30 or cmd.startswith("/") or re.search(r"[`*_\[\]]", cmd) or cmd.isdigit():
            await update.message.reply_text(
                "❌ أمر غير صالح. أرسل كلمة أو عبارة قصيرة (حتى 30 حرفاً) بدون رموز خاصة وبدون رقم، مثل: نظف\n"
                "أو اضغط رجوع للإلغاء.",
                reply_markup=get_back_keyboard("manage_purge")
            )
            return

        WAITING_STATES[user_id] = None
        cmds = get_purge_commands(bot_data)
        if any(normalize_text_spaces(c).lower() == cmd.lower() for c in cmds):
            await update.message.reply_text("⚠️ هذا الأمر موجود بالفعل.", reply_markup=get_back_keyboard("manage_purge"))
            return
        cmds.append(cmd)
        bot_data["purge_commands"] = cmds
        save_data(bot_data)
        await safe_reply_text(
            update.message,
            f"✅ تمت إضافة أمر المسح: `{cmd}`\nاستخدمه داخل المجموعة هكذا: `{cmd} 100`",
            reply_markup=get_back_keyboard("manage_purge")
        )

    # ---------- إضافة بوت إنلاين مسموح (جديد) ----------
    elif state == "add_inline_bot":
        raw = (update.message.text or "").strip()
        uname = raw.replace("https://t.me/", "").replace("t.me/", "").lstrip("@").strip().lower()
        if not re.match(r"^[a-z0-9_]{3,64}$", uname):
            await update.message.reply_text(
                "❌ يوزر غير صالح. أرسل يوزر البوت مثل: @gif\nأو اضغط رجوع للإلغاء.",
                reply_markup=get_back_keyboard("manage_inline_bots")
            )
            return
        WAITING_STATES[user_id] = None
        allowed = bot_data.setdefault("allowed_inline_bots", [])
        if uname in allowed:
            await update.message.reply_text("⚠️ هذا البوت موجود بالفعل في القائمة.", reply_markup=get_back_keyboard("manage_inline_bots"))
            return
        allowed.append(uname)
        save_data(bot_data)
        await update.message.reply_text(f"✅ تم السماح لبوت الإنلاين @{uname}.", reply_markup=get_back_keyboard("manage_inline_bots"))

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
            # القناة التي تأكدنا أن البوت مشرف فيها تُسجَّل أيضاً ضمن وجهات «إذاعة للقنوات»
            bot_data.setdefault("channels", {})[str(channel_chat.id)] = channel_chat.title or "قناة بدون اسم"
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


async def register_strike(context, chat_id, user_id):
    """
    نظام المخالفات: يسجّل مخالفة للعضو، وعند بلوغ الحد خلال ساعة يكتمه تلقائياً.
    يعيد (تم_الكتم, عدد_المخالفات, الحد, مدة_الكتم) أو (False, 0, 0, 0) إذا كان النظام معطلاً.
    """
    settings = load_data().get("settings", {})
    if not settings.get("strikes_enabled"):
        return False, 0, 0, 0

    limit = int(settings.get("strikes_limit", 3))
    minutes = int(settings.get("strikes_minutes", 10))
    key = (chat_id, user_id)
    now_ts = datetime.now().timestamp()

    track = STRIKE_TRACK.get(key)
    if not track or now_ts - track["ts"] > 3600:
        track = {"count": 0, "ts": now_ts}
    track["count"] += 1
    track["ts"] = now_ts
    STRIKE_TRACK[key] = track

    if track["count"] >= limit:
        STRIKE_TRACK.pop(key, None)
        try:
            until_date = datetime.now() + timedelta(minutes=minutes)
            await context.bot.restrict_chat_member(
                chat_id, user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_date
            )
            return True, limit, limit, minutes
        except Exception:
            return False, track["count"], limit, minutes

    return False, track["count"], limit, minutes


async def delete_and_warn(msg, context, chat_id, first_name, reason, user_id=None):
    try:
        await msg.delete()
    except Exception:
        return

    try:
        muted, count, limit, minutes = (False, 0, 0, 0)
        if user_id:
            muted, count, limit, minutes = await register_strike(context, chat_id, user_id)

        text = f"عذراً يا {first_name}، يمنع استخدام ({reason}) في المجموعة!"
        if muted:
            text += f"\n🔇 تم كتمك لمدة {minutes} دقيقة بسبب تكرار المخالفات."
        elif limit:
            text += f"\n⚠️ مخالفة {count}/{limit} — عند تكرارها سيتم كتمك تلقائياً."

        warning_msg = await context.bot.send_message(chat_id=chat_id, text=text)
        await asyncio.sleep(10 if muted else 5)
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
    الرسالة التحذيرية تتضمن منشن (تاغ) للعضو المستهدف ليعلم أنها موجهة له خصيصاً.
    """
    fs_config = bot_data.get("force_sub_groups", {}).get(str(chat_id))
    if not fs_config or not fs_config.get("enabled"):
        return True  # لا يوجد اشتراك إجباري مفعل لهذه المجموعة تحديداً

    channel_id = fs_config.get("channel_id")
    try:
        member = await context.bot.get_chat_member(channel_id, user_id)
        subscribed = member_is_subscribed(member)
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

    # منع تكرار رسائل التنبيه لنفس العضو إذا أرسل عدة رسائل متتالية (تبقى رسائله محذوفة)
    now_ts = datetime.now().timestamp()
    last_ts = FS_WARN_TRACK.get((chat_id, user_id), 0)
    if now_ts - last_ts < 25:
        return False
    FS_WARN_TRACK[(chat_id, user_id)] = now_ts

    channel_name = fs_config.get("channel_title") or "القناة"
    mention = user_mention_html(user_id, first_name)
    uname = getattr(update.effective_user, "username", None)
    uname_part = f" (@{h(uname)})" if uname else ""
    text = (
        f"🔒 عذراً يا {mention}{uname_part}،\n\n"
        f"⚠️ هذه الرسالة موجّهة إليك أنت خصيصاً.\n"
        f"يرجى الاشتراك أولاً في قناة «{h(channel_name)}» حتى تتمكن من المشاركة وإرسال الرسائل داخل هذه المجموعة.\n\n"
        "بعد الاشتراك اضغط على زر «تحقق مرة أخرى»."
    )
    try:
        warn = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode='HTML',
            reply_markup=build_force_sub_join_keyboard(fs_config, user_id)
        )
        asyncio.create_task(delete_after_delay(warn, 25))
    except Exception:
        pass

    return False


# ---------- منع الهمسات (ميزة جديدة) ----------
async def handle_whisper_block(update: Update, context: ContextTypes.DEFAULT_TYPE, msg, chat_id) -> bool:
    """
    الهمسات تُرسَل عبر بوتات الإنلاين الخارجية، وتظهر في المجموعة كرسالة «via @bot» (الحقل via_bot).
    عند تفعيل المنع تُحذف الرسالة فوراً حتى لو كان مرسلها مشرفاً في البوت والمجموعة،
    ثم تُرسل ملاحظة صارمة مختصرة تُحذف تلقائياً بعد ثوانٍ.
    يعيد True إذا حُذفت الرسالة.
    """
    via_bot = getattr(msg, "via_bot", None)
    if not via_bot:
        return False

    data = load_data()
    if not data.get("settings", {}).get("block_whispers", True):
        return False

    via_username = (getattr(via_bot, "username", None) or "").lower()
    if via_username and via_username in data.get("allowed_inline_bots", []):
        return False

    try:
        await msg.delete()
    except Exception:
        # لا توجد صلاحية حذف: لا يمكن المنع، نتجاوز بصمت
        return False

    try:
        user = update.effective_user
        if user and msg.sender_chat is None:
            who = user_mention_html(user.id, user.first_name)
        else:
            who = "المشرف"
        warn = await context.bot.send_message(
            chat_id=chat_id,
            text=f"🚫 {who}\nالهمسات ممنوعة منعاً باتاً في هذه المجموعة، وتم حذف رسالتك.",
            parse_mode='HTML'
        )
        asyncio.create_task(delete_after_delay(warn, 6))
    except Exception:
        pass
    return True


# ---------- حذف الملصقات المتحركة من الحسابات المميزة (ميزة جديدة) ----------
def is_premium_animated_sticker(msg, user):
    """
    يحدد هل الرسالة ملصق (ستيكر) متحرك/فيديو-ستيكر مرسل من حساب مميز (Premium).
    - الملصقات ذات تأثير بريميوم (premium_animation) لا يرسلها إلا أصحاب الحسابات المميزة.
    - الملصق المتحرك (TGS) وملصق الفيديو (WEBM) يُحذفان إذا كان مرسلهما مميزاً.
    لا يشمل الفيديو العادي MP4 ولا الصور المتحركة GIF.
    """
    sticker = getattr(msg, "sticker", None)
    if not sticker:
        return False
    if getattr(sticker, "premium_animation", None):
        return True
    sender_is_premium = bool(getattr(user, "is_premium", False)) if user else False
    if sender_is_premium and (getattr(sticker, "is_animated", False) or getattr(sticker, "is_video", False)):
        return True
    return False


# ---------- مسح آخر الرسائل (ميزة جديدة) ----------
async def run_purge(context: ContextTypes.DEFAULT_TYPE, chat_id: int, command_msg_id: int, count: int, requester):
    """
    ينفّذ المسح: يحذف رسالة الأمر نفسها + آخر (count) رسالة سابقة لها بحسب تسلسل معرّفات الرسائل
    (واجهة تيليجرام للبوتات لا تتيح جلب سجل الرسائل، لذا يُستخدم تسلسل المعرّفات).
    """
    ids = [i for i in range(command_msg_id, command_msg_id - count - 1, -1) if i >= 1]
    ok, error = await delete_message_ids(context, chat_id, ids)

    if ok:
        text = f"🧹 تم تنفيذ أمر المسح لآخر {count} رسالة بواسطة {user_mention_html(requester.id, requester.first_name)}."
    else:
        logging.warning("فشل أمر المسح: %s", error)
        text = (
            "❌ تعذّر تنفيذ المسح.\n"
            "تأكد أن البوت مشرف في المجموعة ولديه صلاحية «حذف الرسائل»."
        )
    try:
        notice = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='HTML')
        asyncio.create_task(delete_after_delay(notice, 6))
    except Exception:
        pass


async def handle_purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE, msg, chat_id, count):
    """يتحقق من حدود العدد ثم يشغّل المسح في الخلفية حتى لا يتعطل البوت أثناء المسح الكبير."""
    if count < 1:
        return

    if count > PURGE_MAX_COUNT:
        try:
            warn = await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ الحد الأقصى للمسح في الأمر الواحد هو {PURGE_MAX_COUNT} رسالة."
            )
            asyncio.create_task(delete_after_delay(warn, 6))
        except Exception:
            pass
        return

    task = asyncio.create_task(run_purge(context, chat_id, msg.message_id, count, update.effective_user))
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)


async def group_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return

    msg = update.message
    chat_id = update.effective_chat.id

    # استثناء منشورات القناة المربوطة
    if msg.is_automatic_forward:
        return

    # رصد نشاط الأعضاء لميزة التاغ الجماعي (آخر رسالة لكل عضو + الانضمام/المغادرة).
    # يتم قبل أي فلتر حتى تُحتسب رسائل الأعضاء حتى لو حذفها البوت، ولا يؤثر على سير الفلاتر.
    try:
        if msg.new_chat_members:
            for new_member in msg.new_chat_members:
                record_activity(chat_id, new_member, is_message=False)
        elif msg.left_chat_member:
            forget_member(chat_id, msg.left_chat_member.id)
        elif update.effective_user and not msg.sender_chat:
            record_activity(chat_id, update.effective_user, is_message=True)
    except Exception:
        logging.exception("خطأ أثناء رصد نشاط الأعضاء")

    # منع الهمسات: يُفحص قبل أي استثناء لأنه يشمل المشرفين أيضاً (حتى الأدمن المتخفي)
    if getattr(msg, "via_bot", None):
        if await handle_whisper_block(update, context, msg, chat_id):
            return

    # استثناء الرسائل المرسلة باسم الأدمن المتخفي (GroupAnonymousBot / اسم المجموعة)
    if msg.sender_chat and msg.sender_chat.id == chat_id:
        return

    user_id = update.effective_user.id if update.effective_user else None
    register_group(chat_id, update.effective_chat.title or "مجموعة")

    bot_data = load_data()

    # حذف رسائل الانضمام والمغادرة (إن كانت الميزة مفعلة)
    if msg.new_chat_members or msg.left_chat_member:
        if bot_data.get("settings", {}).get("clean_service"):
            try:
                await msg.delete()
            except Exception:
                pass
        return

    # أوامر مسح الرسائل: «مسح 100» / «بوت امسح 100» ... (مشرف بوت + مشرف مجموعة معاً)
    if msg.text and user_id:
        purge_count = match_purge_command(msg.text, get_purge_commands(bot_data))
        if purge_count is not None:
            if user_id in bot_data.get("admins", []) and await is_real_group_admin(context, chat_id, user_id):
                await handle_purge_command(update, context, msg, chat_id, purge_count)
                return

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

    # حذف الملصقات المتحركة/فيديو الستيكر/ملصقات بريميوم من أصحاب الحسابات المميزة
    if msg.sticker and bot_data.get("settings", {}).get("block_premium_stickers", True):
        if is_premium_animated_sticker(msg, update.effective_user):
            await delete_and_warn(msg, context, chat_id, first_name, "ملصق متحرك من حساب مميز", user_id)
            return

    # فحص الملصقات (ستيكرز) والصور المتحركة GIF
    if msg.sticker:
        is_animated_or_video = bool(msg.sticker.is_animated or msg.sticker.is_video)
        if bot_data.get("block_animated_stickers") and is_animated_or_video:
            await delete_and_warn(msg, context, chat_id, first_name, "ملصق متحرك", user_id)
            return
        if bot_data.get("block_stickers"):
            await delete_and_warn(msg, context, chat_id, first_name, "ملصقات (ستيكرز)", user_id)
            return

    if msg.animation and bot_data.get("block_animated_stickers"):
        await delete_and_warn(msg, context, chat_id, first_name, "صورة متحركة GIF", user_id)
        return

    # فحص الكلمات والإيموجي والروابط الممنوعة + نداء الاستغاثة
    text = msg.text or msg.caption
    if not text:
        return

    # نداء الاستغاثة (يُفحص بشكل مستقل عن باقي المخالفات)
    handled = await handle_rescue_keyword(update, context, chat_id, bot_data, text)
    if handled:
        return

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
        await delete_and_warn(msg, context, chat_id, first_name, reason, user_id)


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

    app.add_handler(CommandHandler("start", start_command))

    # أزرار التاغ الجماعي (يجب تسجيلها قبل المعالج العام للأزرار)
    app.add_handler(CallbackQueryHandler(tag_callback, pattern=r"^tag_"))
    app.add_handler(CallbackQueryHandler(button_click))

    # تتبع إضافة/إخراج/ترقية البوت داخل المجموعات والقنوات (ميزة جديدة)
    app.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    # رصد انضمام/مغادرة الأعضاء لتحديث سجل التاغ الجماعي (يتطلب أن يكون البوت مشرفاً)
    app.add_handler(ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))

    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (~filters.COMMAND), handle_private_message))

    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.Regex(r"^/(حظر|كتم|الغاء_الحظر|الغاء_الكتم|ban|mute|unban|unmute)"), admin_actions_handler))

    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_filter))

    # أمر التاغ الجماعي: في مجموعة معالجات مستقلة (group=1) حتى لا يتعارض مع فلاتر الحماية في group_filter
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.Regex(TAG_CMD_REGEX), tag_command_handler),
        group=1
    )

    app.add_error_handler(global_error_handler)

    print("البوت يعمل بنجاح...")
    await app.initialize()
    await app.start()

    # حفظ سجل نشاط الأعضاء دورياً في الخلفية
    flush_task = asyncio.create_task(activity_flush_loop())
    BACKGROUND_TASKS.add(flush_task)
    flush_task.add_done_callback(BACKGROUND_TASKS.discard)

    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "my_chat_member", "chat_member"]
    )
    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
