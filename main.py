import os
import json
import logging
from pathlib import Path
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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

WELCOME_MESSAGE = (
    "أهلا صديقنا 😊\n\n"
    "اكتب طلبك هنا (اي ملزمة او ملخص او ملف معين تريد ينضاف للبوت)\n"
    "وراح يتم اضافته ان شاء الله 😇"
)

REQUEST_RECEIVED = "حسناً صديقنا…تم ارسال طلبك للمشرفين وسوف يتم الرد باسرع وقت 😇"

COMMANDS_USER = (
    "📋 *الأوامر المتاحة:*\n\n"
    "▪️ الاوامر — عرض هذه القائمة\n"
    "▪️ /start — بدء المحادثة\n"
    "▪️ /myid — معرفة رقمك"
)

COMMANDS_ADMIN = (
    "📋 *أوامر المشرف:*\n\n"
    "▪️ /admins — قائمة المشرفين\n"
    "▪️ /settings — إعدادات البوت\n"
    "▪️ /myid — معرفة رقمك\n"
    "▪️ الاوامر — عرض هذه القائمة"
)

COMMANDS_OWNER = (
    "📋 *أوامر المالك:*\n\n"
    "🔧 *إعداد المجموعة:*\n"
    "▪️ /setgroup — سجّل المجموعة (أرسله داخل المجموعة)\n\n"
    "👥 *إدارة المشرفين:*\n"
    "▪️ /addadmin <رقم> — أضف مشرفاً\n"
    "▪️ /removeadmin <رقم> — احذف مشرفاً\n"
    "▪️ /admins — قائمة المشرفين\n\n"
    "⚙️ *عام:*\n"
    "▪️ /settings — إعدادات البوت\n"
    "▪️ /myid — معرفة رقمك\n"
    "▪️ الاوامر — عرض هذه القائمة"
)

ARABIC_COMMANDS_FILTER = filters.Regex(r"^(الاوامر|الأوامر|اوامر|أوامر)$")


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("group_chat_id", None)
                data.setdefault("admins", [])
                data.setdefault("user_topic_map", {})
                data.setdefault("topic_user_map", {})
                return data
        except Exception as e:
            logger.error(f"Failed to load settings: {e}")
    return {
        "group_chat_id": None,
        "admins": [],
        "user_topic_map": {},
        "topic_user_map": {},
    }


def save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")


settings = load_settings()


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


def save_topic_mapping(user_id: int, topic_id: int) -> None:
    settings["user_topic_map"][str(user_id)] = topic_id
    settings["topic_user_map"][str(topic_id)] = user_id
    save_settings(settings)


def build_commands_text(chat_id: int) -> str:
    if is_owner(chat_id):
        return COMMANDS_OWNER
    elif is_admin(chat_id):
        return COMMANDS_ADMIN
    else:
        return COMMANDS_USER


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_MESSAGE)


async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_chat.id
    await update.message.reply_text(
        build_commands_text(sender), parse_mode="Markdown"
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"🆔 رقمك: `{chat_id}`", parse_mode="Markdown")


async def setgroup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_chat.id

    if not is_owner(sender):
        await update.message.reply_text(
            f"⛔️ ليس لديك صلاحية لهذا الأمر.\n"
            f"معرّفك: `{sender}`",
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
            f"✅ تم تسجيل المجموعة بنجاح!\n"
            f"الاسم: {group_title}\n"
            f"المعرف: `{group_id}`",
            parse_mode="Markdown",
        )
        logger.info(f"Group set to {group_id} ({group_title}) by owner")
    elif context.args:
        try:
            group_id = int(context.args[0])
            settings["group_chat_id"] = group_id
            save_settings(settings)
            await update.message.reply_text(
                f"✅ تم تعيين المجموعة: `{group_id}`", parse_mode="Markdown"
            )
            logger.info(f"Group set to {group_id} by owner via argument")
        except ValueError:
            await update.message.reply_text("⚠️ الرقم غير صحيح. مثال: /setgroup -1001234567890")
    else:
        await update.message.reply_text(
            "ℹ️ لتسجيل المجموعة:\n"
            "1️⃣ أضفني كمشرف في مجموعتك\n"
            "2️⃣ أرسل /setgroup داخل المجموعة\n\n"
            "أو أرسل: /setgroup <معرف المجموعة>"
        )


async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_chat.id
    if not is_owner(sender):
        return

    if not context.args:
        await update.message.reply_text("⚠️ استخدم: /addadmin <رقم_المستخدم>")
        return

    try:
        new_admin = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ الرقم غير صحيح")
        return

    if new_admin == OWNER_CHAT_ID:
        await update.message.reply_text("ℹ️ أنت المالك بالفعل")
        return

    admins = get_admins()
    if new_admin in admins:
        await update.message.reply_text(f"ℹ️ المستخدم `{new_admin}` مشرف بالفعل", parse_mode="Markdown")
        return

    admins.append(new_admin)
    settings["admins"] = admins
    save_settings(settings)
    await update.message.reply_text(f"✅ تمت إضافة المشرف: `{new_admin}`", parse_mode="Markdown")
    logger.info(f"Admin {new_admin} added by owner")


async def removeadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_chat.id
    if not is_owner(sender):
        return

    if not context.args:
        await update.message.reply_text("⚠️ استخدم: /removeadmin <رقم_المستخدم>")
        return

    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ الرقم غير صحيح")
        return

    admins = get_admins()
    if target not in admins:
        await update.message.reply_text(f"ℹ️ المستخدم `{target}` ليس مشرفاً", parse_mode="Markdown")
        return

    admins.remove(target)
    settings["admins"] = admins
    save_settings(settings)
    await update.message.reply_text(f"✅ تمت إزالة المشرف: `{target}`", parse_mode="Markdown")
    logger.info(f"Admin {target} removed by owner")


async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_chat.id
    if not is_admin(sender):
        return

    admins = get_admins()
    if not admins:
        text = f"👑 المالك: `{OWNER_CHAT_ID}`\n\n📋 لا يوجد مشرفون مضافون حالياً"
    else:
        admin_list = "\n".join(f"• `{a}`" for a in admins)
        text = f"👑 المالك: `{OWNER_CHAT_ID}`\n\n📋 المشرفون:\n{admin_list}"
    await update.message.reply_text(text, parse_mode="Markdown")


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_chat.id
    if not is_admin(sender):
        return

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


async def handle_arabic_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = update.effective_chat.id
    await update.message.reply_text(
        build_commands_text(sender), parse_mode="Markdown"
    )


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return

    user = update.effective_user
    chat_id = update.effective_chat.id
    group_id = get_group_id()

    if not group_id:
        if is_owner(chat_id):
            await msg.reply_text(
                "⚠️ لم تُضبط المجموعة بعد.\n"
                "أضفني كمشرف في مجموعتك ثم أرسل /setgroup داخلها."
            )
        return

    user_topic_map = get_user_topic_map()

    if chat_id not in user_topic_map:
        user_name = user.full_name if user else "مجهول"
        username_part = f" (@{user.username})" if user and user.username else ""
        topic_name = f"{user_name}{username_part}"

        try:
            topic = await context.bot.create_forum_topic(
                chat_id=group_id,
                name=topic_name,
            )
            topic_id = topic.message_thread_id
            save_topic_mapping(chat_id, topic_id)

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
            logger.info(f"Created topic {topic_id} for user {chat_id} ({topic_name})")
        except Exception as e:
            logger.error(f"Failed to create topic for user {chat_id}: {e}")
            return

        await msg.reply_text(REQUEST_RECEIVED)

    user_topic_map = get_user_topic_map()
    topic_id = user_topic_map[chat_id]

    try:
        await context.bot.copy_message(
            chat_id=group_id,
            message_thread_id=topic_id,
            from_chat_id=chat_id,
            message_id=msg.message_id,
        )
        logger.info(f"Copied message from user {chat_id} to topic {topic_id}")
    except Exception as e:
        logger.error(f"Failed to forward message from user {chat_id} to topic {topic_id}: {e}")


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

    topic_id = msg.message_thread_id
    topic_user_map = get_topic_user_map()

    if topic_id not in topic_user_map:
        return

    target_user_id = topic_user_map[topic_id]

    try:
        await context.bot.copy_message(
            chat_id=target_user_id,
            from_chat_id=group_id,
            message_id=msg.message_id,
        )
        logger.info(f"Sent reply from topic {topic_id} to user {target_user_id}")
    except Exception as e:
        logger.error(f"Failed to send reply to user {target_user_id}: {e}")
        try:
            await msg.reply_text(f"❌ فشل الإرسال للمستخدم\nالسبب: {e}")
        except Exception:
            pass


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
        logger.warning("No group configured yet. Owner must run /setgroup inside the target group.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", commands_cmd))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("setgroup", setgroup_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("removeadmin", removeadmin_cmd))
    app.add_handler(CommandHandler("admins", admins_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))

    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ARABIC_COMMANDS_FILTER,
            handle_arabic_commands,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & ~filters.COMMAND,
            handle_group_message,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_user_message,
        )
    )

    logger.info("Bot started with polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
