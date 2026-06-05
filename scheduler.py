import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram.ext import Application

from config import Config
from database import Database
from handlers import get_week_start, format_week_range, UZ_MONTHS

logger = logging.getLogger(__name__)


async def _remind_workers(context, second_call: bool = False):
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    week_start = get_week_start()

    for worker in db.get_all_users_by_role("worker"):
        wid = worker["telegram_user_id"]
        if db.has_week_submission(wid, week_start):
            continue
        incomes = db.get_week_incomes(wid, week_start)
        if not incomes:
            continue
        total = sum(i["amount_usd"] for i in incomes)
        try:
            if second_call:
                text = (
                    f"⏰ <b>Eslatma (oxirgi)!</b>\n\n"
                    f"Hali haftalik hisobotingizni topshirmadingiz.\n"
                    f"💵 Bu hafta: <b>${total:,.2f}</b>\n\n"
                    f"«📦 Bugalterga topshirish» tugmasini bosing."
                )
            else:
                text = (
                    f"📦 <b>Bugun shanba!</b>\n\n"
                    f"Haftalik kirimlaringizni bugalterga topshirishni unutmang.\n"
                    f"💵 Bu hafta: <b>${total:,.2f}</b>"
                )
            await context.bot.send_message(wid, text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"remind worker {wid}: {e}")


async def remind_submit_morning(context):
    await _remind_workers(context, second_call=False)


async def remind_submit_evening(context):
    await _remind_workers(context, second_call=True)


async def send_monthly_report_to_owner(context):
    from datetime import date, timedelta
    from reports import generate_monthly_excel

    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]

    if not config.owner_id:
        return

    today = date.today()
    # Faqat oyning oxirgi Yakshanbasi (keyingi Yakshanba boshqa oyda)
    next_sunday = today + timedelta(days=7)
    if next_sunday.month == today.month:
        return

    incomes = db.get_all_incomes_for_month(today.year, today.month)
    if not incomes:
        return

    try:
        excel_path = generate_monthly_excel(incomes, today.year, today.month)
        total_usd = sum(i["amount_usd"] for i in incomes)
        caption = (
            f"📋 <b>Oylik hisobot — {UZ_MONTHS[today.month]} {today.year}</b>\n"
            f"💵 Jami: <b>${total_usd:,.2f}</b>\n"
            f"📦 Jami kirimlar: <b>{len(incomes)} ta</b>"
        )
        with open(excel_path, "rb") as f:
            await context.bot.send_document(
                config.owner_id,
                document=f,
                filename=f"hisobot_{today.year}_{today.month:02d}.xlsx",
                caption=caption,
                parse_mode="HTML",
            )
        logger.info("Monthly report sent to owner")
    except Exception as e:
        logger.error(f"send_monthly_report_to_owner: {e}")


async def new_week_greeting(context):
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    week_start = get_week_start()

    staff = db.get_all_users_by_role("worker") + db.get_all_users_by_role("accountant")
    for member in staff:
        try:
            await context.bot.send_message(
                member["telegram_user_id"],
                f"🌅 <b>Yangi hafta boshlandi!</b>\n"
                f"📅 <b>{format_week_range(week_start)}</b>\n\n"
                f"Bugun ham omad! 💪",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"new_week_greeting {member['telegram_user_id']}: {e}")


def setup_scheduler(app: Application):
    config: Config = app.bot_data["config"]
    tz = ZoneInfo(config.timezone)
    jq = app.job_queue

    # Shanba 10:00 — birinchi eslatma
    jq.run_daily(remind_submit_morning, time=time(10, 0, tzinfo=tz), days=(5,))
    # Shanba 18:00 — ikkinchi eslatma
    jq.run_daily(remind_submit_evening, time=time(18, 0, tzinfo=tz), days=(5,))
    # Yakshanba 22:00 — oylik hisobot (oy oxiri tekshiruvi bilan)
    jq.run_daily(send_monthly_report_to_owner, time=time(22, 0, tzinfo=tz), days=(6,))
    # Dushanba 09:00 — yangi hafta salomlashuvi
    jq.run_daily(new_week_greeting, time=time(9, 0, tzinfo=tz), days=(0,))

    logger.info("Scheduler setup complete: 4 jobs registered")
