import os
import json
import logging
import time
import asyncio
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "0"))
SETTINGS_FILE = Path("settings.json")

DEFAULT_WELCOME_MESSAGE = (
    "أهلا صديقنا 😊\n\n"
    "اكتب طلبك هنا (اي ملزمة او ملخص او ملف معين تريد ينضاف للبوت)\n"
    "وراح يتم اضافته ان شاء الله 😇"
)
DEFAULT_REQUEST_RECEIVED = (
    "حسناً صديقنا…تم ارسال طلبك للمشرفين وسوف يتم الرد باسرع وقت 😇"
)

COMMANDS_USER = (
    "📋 *الأوامر المتاحة:*\n\n"
    "▪️ الاوامر — عرض هذه القائمة\n"
    "▪️ /start — بدء المحادثة\n"
    "▪️ /myid — معرفة رقمك"
)
COMMANDS_ADMIN = (
    "📋 *أوامر المشرف:*\n\n"
    "▪️ /admins — قائمة المشرفين\n"
    "▪️ /banned — قائمة المحظورين\n"
    "▪️ /ban <id أو @يوزر> — حظر مستخدم مباشرةً\n"
    "▪️ /settings — إعدادات البوت\n"
    "▪️ /myid — معرفة رقمك\n"
    "▪️ الاوامر — عرض هذه القائمة"
)
COMMANDS_OWNER = (
    "📋 *أوامر المالك:*\n\n"
    "🔧 *إعداد المجموعة:*\n"
    "▪️ /setgroup — سجّل المجموعة (أرسله داخل المجموعة)\n\n"
    "👥 *إدارة المشرفين:*\n"
    "▪️ /settings — لوحة الإعدادات التفاعلية\n"
    "▪️ /admins — قائمة المشرفين\n\n"
    "🚫 *الحظر:*\n"
    "▪️ /banned — قائمة المحظورين\n"
    "▪️ /ban <id أو @يوزر> — حظر مستخدم مباشرةً\n\n"
    "⚙️ *عام:*\n"
    "▪️ /myid — معرفة رقمك\n"
    "▪️ الاوامر — عرض هذه القائمة"
)

ARABIC_COMMANDS_FILTER = filters.Regex(r"^(الاوامر|الأوامر|اوامر|أوامر)$")

owner_state: dict[int, str] = {}
_topic_locks: dict[int, asyncio.Lock] = {}
_group_admin_cache: dict[int, tuple[bool, float]] = {}
_GROUP_ADMIN_CACHE_TTL = 300
# group_msg_id → (user_id, user_msg_id)  — in-memory only, resets on restart
_msg_id_map: dict[int, tuple[int, int]] = {}


def get_topic_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _topic_locks:
        _topic_locks[user_id] = asyncio.Lock()
    return _topic_locks[user_id]


async def is_group_admin(context, user_id: int) -> bool:
    group_id = get_group_id()
    if not group_id:
        return False
    now = time.time()
    cached = _group_admin_cache.get(user_id)
    if cached and (now - cached[1]) < _GROUP_ADMIN_CACHE_TTL:
        return cached[0]
    try:
        member = await context.bot.get_chat_member(group_id, user_id)
        result = member.status in ("administrator", "creator")
    except Exception:
        result = False
    _group_admin_cache[user_id] = (result, now)
    return result


async def is_effective_admin(context, user_id: int) -> bool:
    return is_admin(user_id) or await is_group_admin(context, user_id)


_group_kicked_cache: dict[int, tuple[bool, float]] = {}
_GROUP_KICKED_CACHE_TTL = 300


async def is_kicked_from_group(context, user_id: int) -> bool:
    group_id = get_group_id()
    if not group_id:
        return False
    now = time.time()
    cached = _group_kicked_cache.get(user_id)
    if cached and (now - cached[1]) < _GROUP_KICKED_CACHE_TTL:
        return cached[0]
    try:
        member = await context.bot.get_chat_member(group_id, user_id)
        result = member.status == "kicked"
    except Exception:
        result = False
    _group_kicked_cache[user_id] = (result, now)
    return result


# ── Settings load/save ────────────────────────────────────────────────────────

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("group_chat_id", None)
                data.setdefault("admins", [])
                data.setdefault("user_topic_map", {})
                data.setdefault("topic_user_map", {})
                data.setdefault("last_notified", {})
                data.setdefault("confirm_delivery", True)
                data.setdefault("welcome_message", DEFAULT_WELCOME_MESSAGE)
                data.setdefault("request_received_message", DEFAULT_REQUEST_RECEIVED)
                data.setdefault("notification_cooldown", 7200)
                data.setdefault("username_to_id", {})
                data.setdefault("pending_admin_usernames", [])
                data.setdefault("banned_users", [])
                return data
        except Exception as e:
            logger.error(f"Failed to load settings: {e}")
    return {
        "group_chat_id": None,
        "admins": [],
        "user_topic_map": {},
        "topic_user_map": {},
        "last_notified": {},
        "confirm_delivery": True,
        "welcome_message": DEFAULT_WELCOME_MESSAGE,
        "request_received_message": DEFAULT_REQUEST_RECEIVED,
        "notification_cooldown": 7200,
        "username_to_id": {},
        "pending_admin_usernames": [],
        "banned_users": [],
    }


def save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")


settings = load_settings()


# ── Getters ───────────────────────────────────────────────────────────────────

def get_group_id() -> int | None:
    return settings.get("group_chat_id")

def get_admins() -> list[int]:
    return settings.get("admins", [])

def is_owner(chat_id: int) -> bool:
    return chat_id == OWNER_CHAT_ID

def is_admin(chat_id: int) -> bool:
    return is_owner(chat_id) or chat_id in get_admins()

def get_user_topic_map() -> dict[int, int]:
    return {int(k): int(v) for k, v in settings.get("user_topic_map", {}).items()}

def get_topic_user_map() -> dict[int, int]:
    return {int(k): int(v) for k, v in settings.get("topic_user_map", {}).items()}

def get_welcome_message() -> str:
    return settings.get("welcome_message", DEFAULT_WELCOME_MESSAGE)

def get_request_received() -> str:
    return settings.get("request_received_message", DEFAULT_REQUEST_RECEIVED)

def get_notification_cooldown() -> int:
    return int(settings.get("notification_cooldown", 7200))

def is_confirm_delivery_enabled() -> bool:
    return settings.get("confirm_delivery", True)

def get_banned_users() -> list[int]:
    return [int(x) for x in settings.get("banned_users", [])]

def is_banned(user_id: int) -> bool:
    return user_id in get_banned_users()

def ban_user(user_id: int) -> None:
    banned = get_banned_users()
    if user_id not in banned:
        banned.append(user_id)
        settings["banned_users"] = banned
        save_settings(settings)

def unban_user_id(user_id: int) -> None:
    banned = get_banned_users()
    if user_id in banned:
        banned.remove(user_id)
        settings["banned_users"] = banned
        save_settings(settings)

def get_ban_display(user_id: int) -> str:
    uid_str = str(user_id)
    for uname, uid in settings.get("username_to_id", {}).items():
        if str(uid) == uid_str:
            return f"@{uname} (`{user_id}`)"
    return f"`{user_id}`"

def build_banned_text() -> str:
    banned = get_banned_users()
    if not banned:
        return "🚫 *قائمة المحظورين*\n\nلا يوجد محظورون حالياً."
    lines = "\n".join(f"• {get_ban_display(uid)}" for uid in banned)
    return f"🚫 *قائمة المحظورين* ({len(banned)}):\n\n{lines}"

def build_banned_keyboard() -> InlineKeyboardMarkup:
    banned = get_banned_users()
    buttons = []
    for uid in banned:
        display = get_ban_display(uid).replace("`", "")
        buttons.append([InlineKeyboardButton(f"🔓 رفع الحظر عن {display}", callback_data=f"unban_{uid}")])
    buttons.append([InlineKeyboardButton("🔄 تحديث", callback_data="banned_panel")])
    return InlineKeyboardMarkup(buttons)


# ── Helpers ───────────────────────────────────────────────────────────────────

def save_topic_mapping(user_id: int, topic_id: int) -> None:
    settings["user_topic_map"][str(user_id)] = topic_id
    settings["topic_user_map"][str(topic_id)] = user_id
    save_settings(settings)

def should_notify_user(user_id: int) -> bool:
    last = settings.get("last_notified", {}).get(str(user_id))
    if last is None:
        return True
    return (time.time() - last) >= get_notification_cooldown()

def mark_user_notified(user_id: int) -> None:
    settings.setdefault("last_notified", {})[str(user_id)] = time.time()
    save_settings(settings)

def track_username(user_id: int, username: str | None) -> None:
    if username:
        settings.setdefault("username_to_id", {})[username.lower()] = user_id
        save_settings(settings)

def resolve_username(username: str) -> int | None:
    return settings.get("username_to_id", {}).get(username.lower().lstrip("@"))

def check_pending_admin(user_id: int, username: str | None) -> None:
    if not username:
        return
    pending = settings.get("pending_admin_usernames", [])
    uname = username.lower()
    if uname in pending:
        admins = get_admins()
        if user_id not in admins and user_id != OWNER_CHAT_ID:
            admins.append(user_id)
            settings["admins"] = admins
        pending.remove(uname)
        settings["pending_admin_usernames"] = pending
        save_settings(settings)
        logger.info(f"Auto-promoted pending admin @{username} ({user_id})")

def format_cooldown(seconds: int) -> str:
    hours = seconds // 3600
    mins = (seconds % 3600) // 60
    if hours > 0 and mins > 0:
        return f"{hours} ساعة و{mins} دقيقة"
    elif hours > 0:
        return f"{hours} ساعة" if hours > 1 else "ساعة واحدة"
    else:
        return f"{mins} دقيقة"

def get_admin_display(admin_id: int) -> str:
    uid_to_uname = {v: k for k, v in settings.get("username_to_id", {}).items()}
    username = uid_to_uname.get(admin_id)
    return f"@{username}" if username else f"`{admin_id}`"

def get_admin_display_plain(admin_id: int) -> str:
    uid_to_uname = {v: k for k, v in settings.get("username_to_id", {}).items()}
    username = uid_to_uname.get(admin_id)
    return f"@{username}" if username else str(admin_id)

def build_commands_text(chat_id: int) -> str:
    if is_owner(chat_id):
        return COMMANDS_OWNER
    elif is_admin(chat_id):
        return COMMANDS_ADMIN
    else:
        return COMMANDS_USER


# ── Settings UI ───────────────────────────────────────────────────────────────

def build_settings_text() -> str:
    group_id = get_group_id()
    user_count = len(settings.get("user_topic_map", {}))
    admins_count = len(get_admins())
    group_text = f"`{group_id}`" if group_id else "❌ غير مضبوطة"
    return (
        "⚙️ *إعدادات البوت*\n\n"
        f"📦 المجموعة: {group_text}\n"
        f"👥 المستخدمون: {user_count}\n"
        f"🛡 المشرفون: {admins_count}\n"
        f"👑 المالك: `{OWNER_CHAT_ID}`"
    )

def build_settings_keyboard() -> InlineKeyboardMarkup:
    confirm_icon = "✅" if is_confirm_delivery_enabled() else "❌"
    cooldown_text = format_cooldown(get_notification_cooldown())
    keyboard = [
        [InlineKeyboardButton(f"{confirm_icon} تأكيد الإرسال (•)", callback_data="toggle_confirm")],
        [InlineKeyboardButton("👥 إدارة المشرفين", callback_data="admins_panel")],
        [
            InlineKeyboardButton("✏️ رسالة الترحيب", callback_data="edit_welcome"),
            InlineKeyboardButton("✏️ رسالة الاستلام", callback_data="edit_request"),
        ],
        [InlineKeyboardButton(f"⏱ وقت التنبيه: {cooldown_text}", callback_data="edit_cooldown")],
    ]
    return InlineKeyboardMarkup(keyboard)

def build_admins_text() -> str:
    admins = get_admins()
    if admins:
        lines = "\n".join(f"• {get_admin_display(a)}" for a in admins)
    else:
        lines = "لا يوجد مشرفون"
    pending = settings.get("pending_admin_usernames", [])
    pending_text = ""
    if pending:
        p_lines = "\n".join(f"• @{u}" for u in pending)
        pending_text = f"\n\n⏳ *معلقون (لم يراسلوا البوت بعد):*\n{p_lines}"
    return (
        f"👥 *إدارة المشرفين*\n\n"
        f"👑 المالك: `{OWNER_CHAT_ID}`\n\n"
        f"المشرفون الحاليون:\n{lines}"
        f"{pending_text}"
    )

def build_admins_keyboard() -> InlineKeyboardMarkup:
    admins = get_admins()
    keyboard = [[InlineKeyboardButton("➕ إضافة مشرف بـ @يوزر", callback_data="add_admin")]]
    for admin_id in admins:
        label = f"🗑 {get_admin_display_plain(admin_id)}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"rm_{admin_id}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="settings_main")])
    return InlineKeyboardMarkup(keyboard)

async def send_settings_panel(msg) -> None:
    await msg.reply_text(
        build_settings_text(),
        parse_mode="Markdown",
        reply_markup=build_settings_keyboard(),
    )


# ── Command handlers ──────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(get_welcome_message())

async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_user.id if update.effective_user else update.effective_chat.id
    await update.message.reply_text(build_commands_text(sender), parse_mode="Markdown")

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id if update.effective_user else update.effective_chat.id
    await update.message.reply_text(f"🆔 رقمك: `{user_id}`", parse_mode="Markdown")

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_user.id
    if is_owner(sender) and sender in owner_state:
        owner_state.pop(sender)
        await update.message.reply_text("↩️ تم الإلغاء")
        await send_settings_panel(update.message)

async def setgroup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_user.id if update.effective_user else None
    if not sender or not is_owner(sender):
        await update.message.reply_text(
            f"⛔️ ليس لديك صلاحية لهذا الأمر.\nمعرّفك: `{sender}`",
            parse_mode="Markdown",
        )
        return
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        group_id = chat.id
        group_title = chat.title or "بدون اسم"
        settings["group_chat_id"] = group_id
        save_settings(settings)
        await update.message.reply_text(
            f"✅ تم تسجيل المجموعة بنجاح!\nالاسم: {group_title}\nالمعرف: `{group_id}`",
            parse_mode="Markdown",
        )
        logger.info(f"Group set to {group_id} ({group_title})")
    elif context.args:
        try:
            group_id = int(context.args[0])
            settings["group_chat_id"] = group_id
            save_settings(settings)
            await update.message.reply_text(f"✅ تم تعيين المجموعة: `{group_id}`", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("⚠️ الرقم غير صحيح.")
    else:
        await update.message.reply_text(
            "ℹ️ أضفني كمشرف في مجموعتك ثم أرسل /setgroup داخلها.\n"
            "أو: /setgroup <معرف المجموعة>"
        )

async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_user.id if update.effective_user else None
    if not sender or not await is_effective_admin(context, sender):
        return
    admins = get_admins()
    if not admins:
        text = f"👑 المالك: `{OWNER_CHAT_ID}`\n\n📋 لا يوجد مشرفون مضافون حالياً"
    else:
        admin_list = "\n".join(f"• {get_admin_display(a)}" for a in admins)
        text = f"👑 المالك: `{OWNER_CHAT_ID}`\n\n📋 المشرفون:\n{admin_list}"
    await update.message.reply_text(text, parse_mode="Markdown")

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_user.id if update.effective_user else None
    if not sender or not await is_effective_admin(context, sender):
        return
    msg = update.message
    args = context.args
    if not args:
        await msg.reply_text(
            "⚠️ الاستخدام: `/ban <رقم_المستخدم>` أو `/ban @يوزر`",
            parse_mode="Markdown",
        )
        return
    target = args[0].strip()
    target_id: int | None = None
    if target.startswith("@"):
        username = target[1:].lower()
        target_id = resolve_username(username)
        if target_id is None:
            await msg.reply_text(f"⚠️ @{username} لم يراسل البوت من قبل، لا يمكن إيجاد رقمه.")
            return
    else:
        try:
            target_id = int(target)
        except ValueError:
            await msg.reply_text("⚠️ أرسل رقماً صحيحاً أو @يوزر.")
            return
    if is_banned(target_id):
        await msg.reply_text(f"⚠️ المستخدم {get_ban_display(target_id)} محظور مسبقاً.", parse_mode="Markdown")
        return
    ban_user(target_id)
    logger.info(f"Admin {sender} banned user {target_id} via /ban command")
    await msg.reply_text(
        f"✅ تم حظر المستخدم {get_ban_display(target_id)} من خدمات البوت.",
        parse_mode="Markdown",
    )


async def banned_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_user.id if update.effective_user else None
    if not sender or not await is_effective_admin(context, sender):
        return
    await update.message.reply_text(
        build_banned_text(),
        parse_mode="Markdown",
        reply_markup=build_banned_keyboard(),
    )


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_user.id if update.effective_user else None
    if not sender or not await is_effective_admin(context, sender):
        return
    if is_owner(sender) and update.effective_chat.type == "private":
        await send_settings_panel(update.message)
    else:
        group_id = get_group_id()
        user_count = len(settings.get("user_topic_map", {}))
        admins_count = len(get_admins())
        group_text = f"`{group_id}`" if group_id else "❌ غير مضبوطة"
        text = (
            "⚙️ *إعدادات البوت:*\n\n"
            f"📦 المجموعة: {group_text}\n"
            f"👥 عدد المستخدمين: {user_count}\n"
            f"🛡 عدد المشرفين: {admins_count}\n"
            f"👑 المالك: `{OWNER_CHAT_ID}`"
        )
        await update.message.reply_text(text, parse_mode="Markdown")


# ── Callback query handler (inline buttons) ───────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    sender = update.effective_user.id
    data = query.data

    # ── Banned panel — متاح لكل المشرفين الفعليين ──
    if data == "banned_panel" or data.startswith("unban_"):
        if not await is_effective_admin(context, sender):
            return
        if data == "banned_panel":
            await query.edit_message_text(
                build_banned_text(),
                parse_mode="Markdown",
                reply_markup=build_banned_keyboard(),
            )
        elif data.startswith("unban_"):
            try:
                target_id = int(data[6:])
            except ValueError:
                return
            unban_user_id(target_id)
            await query.edit_message_text(
                build_banned_text(),
                parse_mode="Markdown",
                reply_markup=build_banned_keyboard(),
            )
        return

    if not is_owner(sender):
        return

    if data == "settings_main":
        await query.edit_message_text(
            build_settings_text(),
            parse_mode="Markdown",
            reply_markup=build_settings_keyboard(),
        )

    elif data == "toggle_confirm":
        settings["confirm_delivery"] = not is_confirm_delivery_enabled()
        save_settings(settings)
        await query.edit_message_text(
            build_settings_text(),
            parse_mode="Markdown",
            reply_markup=build_settings_keyboard(),
        )

    elif data == "admins_panel":
        await query.edit_message_text(
            build_admins_text(),
            parse_mode="Markdown",
            reply_markup=build_admins_keyboard(),
        )

    elif data == "add_admin":
        owner_state[sender] = "awaiting_add_admin"
        await query.edit_message_text(
            "👤 أرسل @يوزر المشرف الجديد:\n\n_(أرسل /cancel للإلغاء)_",
            parse_mode="Markdown",
        )

    elif data.startswith("rm_"):
        try:
            admin_id = int(data[3:])
        except ValueError:
            return
        admins = get_admins()
        if admin_id in admins:
            admins.remove(admin_id)
            settings["admins"] = admins
            save_settings(settings)
        await query.edit_message_text(
            build_admins_text(),
            parse_mode="Markdown",
            reply_markup=build_admins_keyboard(),
        )

    elif data == "edit_welcome":
        owner_state[sender] = "awaiting_welcome"
        await query.edit_message_text(
            f"✏️ *رسالة الترحيب الحالية:*\n\n{get_welcome_message()}\n\n"
            "أرسل النص الجديد:\n_(أرسل /cancel للإلغاء)_",
            parse_mode="Markdown",
        )

    elif data == "edit_request":
        owner_state[sender] = "awaiting_request"
        await query.edit_message_text(
            f"✏️ *رسالة الاستلام الحالية:*\n\n{get_request_received()}\n\n"
            "أرسل النص الجديد:\n_(أرسل /cancel للإلغاء)_",
            parse_mode="Markdown",
        )

    elif data == "edit_cooldown":
        owner_state[sender] = "awaiting_cooldown"
        await query.edit_message_text(
            f"⏱ *وقت التنبيه الحالي:* {format_cooldown(get_notification_cooldown())}\n\n"
            "أرسل الوقت الجديد *بالدقائق*:\n"
            "_(مثال: 120 = ساعتان، 30 = نصف ساعة)_\n\n"
            "_(أرسل /cancel للإلغاء)_",
            parse_mode="Markdown",
        )


# ── Owner private message handler ─────────────────────────────────────────────

async def handle_owner_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return
    sender = update.effective_user.id
    text = (msg.text or "").strip()
    state = owner_state.get(sender)

    if not state:
        await send_settings_panel(msg)
        return

    if state == "awaiting_add_admin":
        if not text.startswith("@"):
            await msg.reply_text("⚠️ يرجى إرسال @يوزر صحيح — مثال: @username")
            return
        username = text[1:].lower()
        user_id = resolve_username(username)
        if user_id is None:
            pending = settings.setdefault("pending_admin_usernames", [])
            if username not in pending:
                pending.append(username)
                save_settings(settings)
            await msg.reply_text(
                f"⚠️ @{username} لم يراسل البوت بعد.\n"
                "تمت إضافته للقائمة المعلقة وسيصبح مشرفاً تلقائياً عند أول مراسلة."
            )
        else:
            admins = get_admins()
            if user_id == OWNER_CHAT_ID or user_id in admins:
                await msg.reply_text(f"ℹ️ @{username} مالك أو مشرف بالفعل")
            else:
                admins.append(user_id)
                settings["admins"] = admins
                save_settings(settings)
                await msg.reply_text(f"✅ تمت إضافة @{username} كمشرف")
        owner_state.pop(sender, None)
        await send_settings_panel(msg)

    elif state == "awaiting_welcome":
        settings["welcome_message"] = text
        save_settings(settings)
        owner_state.pop(sender, None)
        await msg.reply_text("✅ تم تحديث رسالة الترحيب")
        await send_settings_panel(msg)

    elif state == "awaiting_request":
        settings["request_received_message"] = text
        save_settings(settings)
        owner_state.pop(sender, None)
        await msg.reply_text("✅ تم تحديث رسالة الاستلام")
        await send_settings_panel(msg)

    elif state == "awaiting_cooldown":
        try:
            minutes = int(text)
            if minutes < 1:
                raise ValueError
            seconds = minutes * 60
            settings["notification_cooldown"] = seconds
            save_settings(settings)
            owner_state.pop(sender, None)
            await msg.reply_text(f"✅ تم تحديث وقت التنبيه إلى {format_cooldown(seconds)}")
            await send_settings_panel(msg)
        except ValueError:
            await msg.reply_text("⚠️ أرسل رقماً صحيحاً بالدقائق — مثال: 120")


# ── Message handlers ──────────────────────────────────────────────────────────

async def handle_arabic_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_user.id if update.effective_user else update.effective_chat.id
    await update.message.reply_text(build_commands_text(sender), parse_mode="Markdown")


async def forward_or_copy(context, from_chat_id, to_chat_id, message_id, thread_id=None):
    try:
        await context.bot.forward_message(
            chat_id=to_chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
            message_thread_id=thread_id,
        )
    except Exception:
        await context.bot.copy_message(
            chat_id=to_chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
            message_thread_id=thread_id,
        )


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return

    user = update.effective_user
    chat_id = update.effective_chat.id
    group_id = get_group_id()

    if user and user.username:
        track_username(user.id, user.username)
        check_pending_admin(user.id, user.username)

    if user:
        if is_banned(user.id):
            await msg.reply_text("⛔️ لا يمكن إيصال طلبك، تم حظرك من جميع خدمات البوت.")
            return
        if not is_banned(user.id) and await is_kicked_from_group(context, user.id):
            ban_user(user.id)
            logger.info(f"Auto-banned user {user.id}: kicked from group")
            await msg.reply_text("⛔️ لا يمكن إيصال طلبك، تم حظرك من جميع خدمات البوت.")
            return

    if not group_id:
        return

    async with get_topic_lock(chat_id):
        user_name = user.full_name if user else "مجهول"
        username_part = f" (@{user.username})" if user and user.username else ""
        user_topic_map = get_user_topic_map()

        # ── حالة: لا يوجد تبويب (أول مراسلة) ──
        if chat_id not in user_topic_map:
            try:
                topic = await context.bot.create_forum_topic(chat_id=group_id, name=user_name)
                topic_id = topic.message_thread_id
                save_topic_mapping(chat_id, topic_id)
                logger.info(f"Created topic {topic_id} for user {chat_id}")
            except Exception as e:
                logger.error(f"Failed to create topic for user {chat_id}: {e}")
                return

            try:
                await context.bot.send_message(
                    chat_id=group_id,
                    message_thread_id=topic_id,
                    text=(
                        f"👤 مستخدم جديد\n"
                        f"الاسم: {user_name}{username_part}\n"
                        f"الرقم: `{chat_id}`"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Failed to send user info to topic {topic_id}: {e}")

            try:
                await forward_or_copy(context, chat_id, group_id, msg.message_id, thread_id=topic_id)
                logger.info(f"Forwarded first message from user {chat_id} to topic {topic_id}")
            except Exception as e:
                logger.error(f"Failed to forward first message for user {chat_id}: {e}")

            await msg.reply_text(get_request_received())
            mark_user_notified(chat_id)
            return

        # ── حالة: يوجد تبويب — حاول الإرسال، وإلا أعد إنشاءه ──
        topic_id = user_topic_map[chat_id]
        forward_ok = False
        try:
            await forward_or_copy(context, chat_id, group_id, msg.message_id, thread_id=topic_id)
            logger.info(f"Forwarded message from user {chat_id} to topic {topic_id}")
            forward_ok = True
        except Exception as e:
            logger.warning(f"Topic {topic_id} unavailable for user {chat_id}: {e}. Recreating...")
            settings["user_topic_map"].pop(str(chat_id), None)
            settings["topic_user_map"].pop(str(topic_id), None)
            save_settings(settings)

            try:
                new_topic = await context.bot.create_forum_topic(chat_id=group_id, name=user_name)
                new_topic_id = new_topic.message_thread_id
                save_topic_mapping(chat_id, new_topic_id)
                logger.info(f"Recreated topic {new_topic_id} for user {chat_id}")
            except Exception as te:
                logger.error(f"Failed to recreate topic for user {chat_id}: {te}")
                return

            try:
                await context.bot.send_message(
                    chat_id=group_id,
                    message_thread_id=new_topic_id,
                    text=(
                        f"👤 مستخدم (تبويب جديد)\n"
                        f"الاسم: {user_name}{username_part}\n"
                        f"الرقم: `{chat_id}`"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as ie:
                logger.error(f"Failed to send user info to new topic: {ie}")

            try:
                await forward_or_copy(context, chat_id, group_id, msg.message_id, thread_id=new_topic_id)
                logger.info(f"Forwarded message to new topic {new_topic_id} for user {chat_id}")
                forward_ok = True
            except Exception as fe:
                logger.error(f"Failed to forward to new topic {new_topic_id}: {fe}")

        if forward_ok and should_notify_user(chat_id):
            await msg.reply_text(get_request_received())
            mark_user_notified(chat_id)


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return

    group_id = get_group_id()
    if not group_id or update.effective_chat.id != group_id:
        return

    if not msg.message_thread_id:
        return

    if msg.from_user and msg.from_user.is_bot:
        return

    if not msg.from_user or not await is_group_admin(context, msg.from_user.id):
        return

    if msg.from_user.username:
        track_username(msg.from_user.id, msg.from_user.username)

    topic_id = msg.message_thread_id
    topic_user_map = get_topic_user_map()

    if topic_id not in topic_user_map:
        return

    target_user_id = topic_user_map[topic_id]

    msg_text = (msg.text or "").strip()

    ban_trigger = msg_text in ("حضر", "حظر")
    if ban_trigger:
        if is_banned(target_user_id):
            await msg.reply_text(f"⚠️ المستخدم `{target_user_id}` محظور مسبقاً.", parse_mode="Markdown")
        else:
            ban_user(target_user_id)
            logger.info(f"Admin {msg.from_user.id} banned user {target_user_id} via group topic trigger")
            await msg.reply_text(
                f"✅ تم حظر المستخدم {get_ban_display(target_user_id)} من خدمات البوت.",
                parse_mode="Markdown",
            )
        return

    if msg_text == "حذف" and msg.reply_to_message:
        replied_id = msg.reply_to_message.message_id
        entry = _msg_id_map.get(replied_id)
        if not entry:
            await msg.reply_text("⚠️ لا يوجد ربط لهذه الرسالة (ربما أُرسلت قبل آخر تشغيل للبوت).")
            return
        user_id, user_msg_id = entry
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=user_msg_id)
            logger.info(f"Admin {msg.from_user.id} deleted message {user_msg_id} for user {user_id}")
            try:
                await msg.delete()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"Failed to delete message {user_msg_id} for user {user_id}: {e}")
            await msg.reply_text(f"⚠️ تعذّر حذف الرسالة: {e}")
        return

    try:
        sent = await context.bot.copy_message(
            chat_id=target_user_id,
            from_chat_id=group_id,
            message_id=msg.message_id,
        )
        _msg_id_map[msg.message_id] = (target_user_id, sent.message_id)
        logger.info(f"Sent reply from topic {topic_id} to user {target_user_id} (user_msg={sent.message_id})")
        mark_user_notified(target_user_id)
        if is_confirm_delivery_enabled():
            try:
                await msg.reply_text("•")
            except Exception:
                pass
    except Forbidden as e:
        logger.error(f"User {target_user_id} blocked the bot: {e}")
        try:
            await msg.reply_text(
                f"⚠️ لم تصل الرسالة للمستخدم `{target_user_id}`\n\n"
                f"السبب: المستخدم حظر البوت ولا يمكن إرسال الرسائل إليه.",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Failed to send reply to user {target_user_id}: {e}")
        try:
            await msg.reply_text(
                f"⚠️ لم تصل الرسالة للمستخدم `{target_user_id}`\n\n"
                f"السبب: {e}\n\n"
                f"يرجى التحقق من المشكلة أعلاه.",
                parse_mode="Markdown",
            )
        except Exception:
            pass


# ── Group edited-message handler ──────────────────────────────────────────────

async def handle_group_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.edited_message
    if not msg:
        return

    group_id = get_group_id()
    if not group_id or update.effective_chat.id != group_id:
        return

    if not msg.message_thread_id:
        return

    if not msg.from_user or msg.from_user.is_bot:
        return

    if not await is_group_admin(context, msg.from_user.id):
        return

    entry = _msg_id_map.get(msg.message_id)
    if not entry:
        return

    user_id, user_msg_id = entry

    try:
        if msg.text:
            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=user_msg_id,
                text=msg.text,
                entities=msg.entities or None,
            )
        elif msg.caption is not None:
            await context.bot.edit_message_caption(
                chat_id=user_id,
                message_id=user_msg_id,
                caption=msg.caption,
                caption_entities=msg.caption_entities or None,
            )
        logger.info(f"Edited user message {user_msg_id} for user {user_id}")
    except Exception as e:
        logger.warning(f"Failed to edit message for user {user_id}: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    if OWNER_CHAT_ID == 0:
        raise ValueError("OWNER_CHAT_ID is not set")

    logger.info(f"Bot starting. OWNER_CHAT_ID={OWNER_CHAT_ID}")
    group_id = get_group_id()
    if group_id:
        logger.info(f"Group configured: {group_id}")
    else:
        logger.warning("No group configured yet.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", commands_cmd))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("setgroup", setgroup_cmd))
    app.add_handler(CommandHandler("admins", admins_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("banned", banned_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))

    app.add_handler(CallbackQueryHandler(handle_callback))

    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ARABIC_COMMANDS_FILTER,
        handle_arabic_commands,
    ))

    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.User(OWNER_CHAT_ID) & ~filters.COMMAND,
        handle_owner_private,
    ))

    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND,
        handle_group_message,
    ))

    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & filters.UpdateType.EDITED_MESSAGE,
        handle_group_edit,
    ))

    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_user_message,
    ))

    logger.info("Bot started with polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
