import asyncio
import logging
import sys
import warnings

warnings.filterwarnings("ignore", message=".*per_message=False.*", category=UserWarning)

from telegram import Update, MenuButtonWebApp, WebAppInfo, MenuButtonDefault
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

from config import load_config, Config
from database import Database
from handlers import (
    start_handler, show_week_handler, show_month_handler,
    workers_list_handler, weekly_report_handler, monthly_report_handler,
    owner_all_workers_handler, owner_weekly_handler, owner_monthly_handler,
    income_conversation_handler, submit_conversation_handler,
    hisobotim_handler,
)
from callbacks import accountant_confirm_callback, reject_reason_conversation_handler
from scheduler import setup_scheduler

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("daromad-bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def haftalik_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'📊 Haftalik' — accountant va owner uchun umumiy dispatcher."""
    config: Config = context.bot_data["config"]
    role = config.get_role(update.effective_user.id)
    if role == "accountant":
        await weekly_report_handler(update, context)
    elif role == "owner":
        await owner_weekly_handler(update, context)


async def _post_init(app: Application) -> None:
    """Bot ishga tushgandan keyin: API server + Menu Button sozlash."""
    from api import start_api_server
    config: Config = app.bot_data["config"]

    # ── API server parallel ishga tushadi ──────────────────────────────────
    asyncio.create_task(start_api_server())
    logger.info("API server task yaratildi (port 8081)")

    # ── Menu Button (WebApp yoki Default) ──────────────────────────────────
    if config.miniapp_url:
        try:
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="📱 Dashboard",
                    web_app=WebAppInfo(url=config.miniapp_url),
                )
            )
            logger.info(f"MenuButtonWebApp sozlandi: {config.miniapp_url}")
        except Exception as e:
            logger.warning(f"MenuButtonWebApp sozlanmadi: {e}")
    else:
        try:
            await app.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        except Exception:
            pass


def main():
    config = load_config()
    db = Database()
    db.init_db()

    app = Application.builder().token(config.token).post_init(_post_init).build()
    app.bot_data["config"] = config
    app.bot_data["db"] = db

    # ── ConversationHandlers ───────────────────────────────────────────────
    app.add_handler(income_conversation_handler())
    app.add_handler(submit_conversation_handler())
    app.add_handler(reject_reason_conversation_handler())

    # ── Commands ───────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",      start_handler))
    app.add_handler(CommandHandler("hisobotim",  hisobotim_handler))
    app.add_handler(CommandHandler("dashboard",  start_handler))

    # ── Inline callbacks ───────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(accountant_confirm_callback, pattern="^acc_confirm_"))

    # ── Reply keyboard buttons ─────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex("^📊 Bu hafta$"),        show_week_handler))
    app.add_handler(MessageHandler(filters.Regex("^📅 Bu oy$"),            show_month_handler))
    app.add_handler(MessageHandler(filters.Regex("^👥 Ishchilar$"),        workers_list_handler))
    app.add_handler(MessageHandler(filters.Regex("^📋 Oylik hisobot$"),    monthly_report_handler))
    app.add_handler(MessageHandler(filters.Regex("^👥 Barcha ishchilar$"), owner_all_workers_handler))
    app.add_handler(MessageHandler(filters.Regex("^📅 Oylik$"),            owner_monthly_handler))
    app.add_handler(MessageHandler(filters.Regex("^📊 Haftalik$"),         haftalik_dispatch))

    setup_scheduler(app)

    logger.info("Daromad bot ishga tushdi...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
