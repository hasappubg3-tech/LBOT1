import os
import logging
from telegram import Update, Bot
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
AMEER_TOKEN = os.environ.get("AMEER_BOT_TOKEN")
OWNER_CHAT_ID = 7305367169

FIXED_REPLY = (
    "أهلا صديقنا 😊\n\n"
    "اكتب طلبك هنا (اي ملزمة او ملخص او ملف معين تريد ينضاف للبوت)\n"
    "وراح يتم اضافته ان شاء الله 😇"
)

MESSAGE_REPLY = "حسناً صديقنا…تم ارسال طلبك للمشرفين وسوف يتم الرد باسرع وقت 😇"

reply_map: dict[int, int] = {}
greeted_users: set[int] = set()
pending_target_chat_id: int | None = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(FIXED_REPLY)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📋 قائمة الأوامر:\n\n/start - بدء المحادثة\n/help - المساعدة\n/myid - معرفة رقمك"
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"🆔 رقمك: `{chat_id}`", parse_mode="Markdown")


async def to_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global pending_target_chat_id
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: /to CHATID")
        return
    try:
        target_id = int(context.args[0])
        pending_target_chat_id = target_id
        await update.message.reply_text(
            f"✅ تم تحديد المستخدم ({target_id}) كهدف\n\n"
            "الآن حوّل له أي ملف أو رسالة من بوت الامير وسيوصله تلقائياً.\n\n"
            "إلغاء الهدف: /cancel"
        )
        logger.info(f"Pending target set to {target_id}")
    except ValueError:
        await update.message.reply_text("⚠️ الرقم غير صحيح")


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global pending_target_chat_id
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    pending_target_chat_id = None
    await update.message.reply_text("❌ تم إلغاء تحديد المستخدم")
    logger.info("Pending target cleared")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global pending_target_chat_id

    msg = update.message
    if not msg:
        return

    chat_id = update.effective_chat.id

    if chat_id == OWNER_CHAT_ID:
        if pending_target_chat_id is not None:
            target_id = pending_target_chat_id
            try:
                await context.bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=OWNER_CHAT_ID,
                    message_id=msg.message_id,
                )
                await msg.reply_text(f"✅ تم إرسال الملف للمستخدم ({target_id})")
                logger.info(f"File sent to user {target_id} via pending target")
            except Exception as e:
                await msg.reply_text(f"❌ فشل الإرسال للمستخدم ({target_id})")
                logger.error(f"Failed to send file to {target_id}: {e}")
            pending_target_chat_id = None
            return

        if msg.reply_to_message:
            reply_to_id = msg.reply_to_message.message_id
            if reply_to_id in reply_map:
                target_chat_id = reply_map[reply_to_id]
                try:
                    await context.bot.copy_message(
                        chat_id=target_chat_id,
                        from_chat_id=OWNER_CHAT_ID,
                        message_id=msg.message_id,
                    )
                    await msg.reply_text("✅ تم إرسال الرد للمستخدم")
                    logger.info(f"Reply sent to user {target_chat_id}")
                except Exception as e:
                    await msg.reply_text("❌ فشل الإرسال للمستخدم")
                    logger.error(f"Failed to send reply to {target_chat_id}: {e}")
        return

    if chat_id not in greeted_users:
        await msg.reply_text(MESSAGE_REPLY)
        greeted_users.add(chat_id)

    forwarded = await context.bot.forward_message(
        chat_id=OWNER_CHAT_ID,
        from_chat_id=chat_id,
        message_id=msg.message_id,
    )
    reply_map[forwarded.message_id] = chat_id

    sender_name = update.effective_user.first_name if update.effective_user else "مجهول"
    await context.bot.send_message(
        chat_id=OWNER_CHAT_ID,
        text=f"👆 لإرسال ملف لهذا المستخدم ({sender_name}):\n`/to {chat_id}`",
        parse_mode="Markdown",
    )
    logger.info(f"Forwarded message from user {chat_id} to owner")


def main() -> None:
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("to", to_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_message)
    )

    logger.info("Bot started with polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
