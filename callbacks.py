import logging
from datetime import date

from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)

from config import Config
from database import Database
from keyboards import get_role_keyboard
from handlers import format_week_range, UZ_MONTHS

logger = logging.getLogger(__name__)

REJECT_REASON = 10


# ── Accountant: confirm submission ─────────────────────────────────────────

async def accountant_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    user_id = query.from_user.id

    if config.get_role(user_id) != "accountant":
        await query.answer("Ruxsat yo'q!", show_alert=True)
        return

    try:
        submission_id = int(query.data.split("_")[-1])
        submission = db.get_submission(submission_id)

        if not submission:
            await query.edit_message_text("❌ Topshiriq topilmadi.")
            return

        if submission["accountant_action"]:
            await query.answer("Bu topshiriq allaqachon ko'rib chiqilgan!", show_alert=True)
            return

        db.confirm_submission(submission_id, user_id)
        worker_id = submission["worker_id"]
        week_start = date.fromisoformat(submission["week_start"])

        try:
            worker = await context.bot.get_chat(worker_id)
            full_name = worker.full_name or str(worker_id)
        except Exception:
            full_name = str(worker_id)

        await query.edit_message_text(
            f"✅ <b>Tasdiqlandi</b>\n"
            f"─────────────────────────\n"
            f"👤 Ishchi: <b>{full_name}</b>\n"
            f"📅 Hafta:  <b>{format_week_range(week_start)}</b>\n"
            f"💵 Jami:   <b>${submission['total_usd']:,.2f}</b>",
            parse_mode="HTML",
        )

        # Notify worker
        try:
            await context.bot.send_message(
                worker_id,
                f"✅ <b>Haftalik hisobotingiz tasdiqlandi!</b>\n\n"
                f"📅 Hafta: <b>{format_week_range(week_start)}</b>\n"
                f"💵 Jami:  <b>${submission['total_usd']:,.2f}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        # Notify owner
        if config.owner_id:
            try:
                await context.bot.send_message(
                    config.owner_id,
                    f"✅ <b>Tasdiqlandi</b>\n"
                    f"👤 {full_name} — ${submission['total_usd']:,.2f}",
                    parse_mode="HTML",
                )
            except Exception:
                pass
    except Exception as e:
        logger.error(f"accountant_confirm_callback: {e}")


# ── Accountant: reject submission (conversation) ───────────────────────────

async def acc_reject_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    user_id = query.from_user.id

    if config.get_role(user_id) != "accountant":
        await query.answer("Ruxsat yo'q!", show_alert=True)
        return ConversationHandler.END

    try:
        submission_id = int(query.data.split("_")[-1])
        submission = db.get_submission(submission_id)

        if not submission:
            await query.edit_message_text("❌ Topshiriq topilmadi.")
            return ConversationHandler.END

        if submission["accountant_action"]:
            await query.answer("Bu topshiriq allaqachon ko'rib chiqilgan!", show_alert=True)
            return ConversationHandler.END

        context.user_data["rejecting_submission_id"] = submission_id

        await query.edit_message_text(
            f"❌ <b>Rad etish</b>\n\n"
            f"Rad etish sababini yozing\n"
            f"(Bekor qilish: /cancel):",
            parse_mode="HTML",
        )
        return REJECT_REASON
    except Exception as e:
        logger.error(f"acc_reject_entry: {e}")
        return ConversationHandler.END


async def acc_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]
        user_id = update.effective_user.id

        submission_id = context.user_data.pop("rejecting_submission_id", None)
        if not submission_id:
            await update.message.reply_text("Xatolik yuz berdi.")
            return ConversationHandler.END

        note = update.message.text.strip()
        submission = db.get_submission(submission_id)
        if not submission:
            await update.message.reply_text("Topshiriq topilmadi.")
            return ConversationHandler.END

        db.reject_submission(submission_id, user_id, note)
        worker_id = submission["worker_id"]
        week_start = date.fromisoformat(submission["week_start"])

        await update.message.reply_text(
            f"✅ Rad etildi. Ishchiga xabar yuborildi.",
            reply_markup=get_role_keyboard(config.get_role(user_id)),
        )

        # Notify worker
        try:
            await context.bot.send_message(
                worker_id,
                f"❌ <b>Haftalik hisobotingiz rad etildi</b>\n\n"
                f"📅 Hafta: <b>{format_week_range(week_start)}</b>\n"
                f"💵 Jami:  <b>${submission['total_usd']:,.2f}</b>\n\n"
                f"📝 Sabab: <i>{note}</i>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        # Notify owner
        if config.owner_id:
            try:
                worker = await context.bot.get_chat(worker_id)
                full_name = worker.full_name or str(worker_id)
                await context.bot.send_message(
                    config.owner_id,
                    f"❌ <b>Rad etildi</b>\n"
                    f"👤 {full_name} — ${submission['total_usd']:,.2f}\n"
                    f"📝 Sabab: <i>{note}</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        return ConversationHandler.END
    except Exception as e:
        logger.error(f"acc_reject_reason: {e}")
        return ConversationHandler.END


async def reject_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    role = config.get_role(update.effective_user.id)
    context.user_data.pop("rejecting_submission_id", None)
    await update.message.reply_text("Bekor qilindi.", reply_markup=get_role_keyboard(role))
    return ConversationHandler.END


def reject_reason_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(acc_reject_entry, pattern="^acc_reject_")],
        states={
            REJECT_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, acc_reject_reason),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", reject_cancel),
        ],
    )
