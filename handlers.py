import re
import logging
from datetime import date, timedelta
from typing import Optional, Tuple

from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)

from config import Config
from database import Database
from keyboards import (
    get_role_keyboard, confirm_inline, cancel_keyboard, accountant_action_inline
)

logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────
INCOME_INPUT   = 1
INCOME_CONFIRM = 2
SUBMIT_CONFIRM = 3
RATE_INPUT     = 5

# ── Uzbek locale helpers ───────────────────────────────────────────────────
UZ_MONTHS = {
    1: "yanvar", 2: "fevral", 3: "mart", 4: "aprel",
    5: "may",    6: "iyun",   7: "iyul",  8: "avgust",
    9: "sentabr", 10: "oktabr", 11: "noyabr", 12: "dekabr",
}
UZ_DAYS = {
    0: "Dushanba", 1: "Seshanba", 2: "Chorshanba",
    3: "Payshanba", 4: "Juma",    5: "Shanba",    6: "Yakshanba",
}


def get_week_start(d: date = None) -> date:
    if d is None:
        d = date.today()
    return d - timedelta(days=d.weekday())


def format_date_uz(d: date) -> str:
    return f"{d.day}-{UZ_MONTHS[d.month]}, {UZ_DAYS[d.weekday()]}"


def format_week_range(week_start: date) -> str:
    week_end = week_start + timedelta(days=6)
    if week_start.month == week_end.month:
        return f"{week_start.day}–{week_end.day} {UZ_MONTHS[week_start.month]}"
    return (f"{week_start.day} {UZ_MONTHS[week_start.month]}"
            f" – {week_end.day} {UZ_MONTHS[week_end.month]}")


def parse_income_text(text: str) -> Optional[Tuple[str, float, str]]:
    text = text.strip()
    pattern = r"^(.+?)\s+([\d][.\d,]*)\s*(\$|so'?m|usd|uzs)?$"
    match = re.match(pattern, text, re.IGNORECASE)
    if not match:
        return None
    desc = match.group(1).strip().upper()
    amount_str = match.group(2).replace(",", "")
    currency_raw = (match.group(3) or "").strip().lower()
    try:
        amount = float(amount_str)
    except ValueError:
        return None
    if currency_raw in ("$", "usd"):
        currency = "USD"
    elif currency_raw in ("so'm", "som", "uzs"):
        currency = "UZS"
    else:
        currency = "UZS"
    return desc, amount, currency


# ── /start ─────────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]
        user = update.effective_user
        role = config.get_role(user.id)

        if role == "unknown":
            await update.message.reply_text("⛔ Sizga ruxsat yo'q.")
            return

        db.upsert_user(user.id, user.username or "", user.full_name or "", role)
        role_labels = {"worker": "Ishchi 🧑‍💼", "accountant": "Bugalter 🧮", "owner": "Owner 👁"}

        await update.message.reply_text(
            f"👋 Xush kelibsiz, <b>{user.full_name}</b>!\n"
            f"🎭 Rol: <b>{role_labels.get(role, role)}</b>",
            parse_mode="HTML",
            reply_markup=get_role_keyboard(role),
        )
    except Exception as e:
        logger.error(f"start_handler: {e}")
        await update.message.reply_text("Xatolik yuz berdi.")


# ── Income conversation ────────────────────────────────────────────────────

async def income_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    if config.get_role(update.effective_user.id) not in ("worker", "accountant"):
        return ConversationHandler.END

    await update.message.reply_text(
        "💰 <b>Kirim qo'shish</b>\n\n"
        "Kirim turini va summani yozing:\n\n"
        "<code>TAS JED 30$</code>\n"
        "<code>HOTEL 80 USD</code>\n"
        "<code>BILET 500000 so'm</code>\n"
        "<code>TRANSFER 1500000</code>  ← so'm (default)\n\n"
        "Bekor qilish: /cancel",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    return INCOME_INPUT


async def income_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        if text == "❌ Bekor qilish":
            return await income_cancel(update, context)

        parsed = parse_income_text(text)
        if not parsed:
            await update.message.reply_text(
                "❌ Format noto'g'ri. Qaytadan yozing:\n\n"
                "<code>TAS JED 30$</code>  yoki  <code>HOTEL 500000 so'm</code>",
                parse_mode="HTML",
            )
            return INCOME_INPUT

        desc, amount, currency = parsed
        context.user_data["pending_income"] = {
            "description": desc, "amount": amount, "currency": currency
        }

        display = f"${amount:,.2f}" if currency == "USD" else f"{amount:,.0f} so'm"
        await update.message.reply_text(
            f"📝 <b>Tasdiqlash</b>\n"
            f"──────────────────────\n"
            f"Tur:    <b>{desc}</b>\n"
            f"Summa:  <b>{display}</b>\n\n"
            f"Tasdiqlaysizmi?",
            parse_mode="HTML",
            reply_markup=confirm_inline("income_yes", "income_no"),
        )
        return INCOME_CONFIRM
    except Exception as e:
        logger.error(f"income_input: {e}")
        return INCOME_INPUT


async def income_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    user_id = query.from_user.id
    role = config.get_role(user_id)

    if query.data == "income_no":
        context.user_data.pop("pending_income", None)
        await query.edit_message_text("❌ Bekor qilindi.")
        await context.bot.send_message(user_id, "Bosh menyu:", reply_markup=get_role_keyboard(role))
        return ConversationHandler.END

    income = context.user_data.pop("pending_income", None)
    if not income:
        await query.edit_message_text("Xatolik: ma'lumot topilmadi.")
        return ConversationHandler.END

    try:
        rate = db.get_current_rate(config.usd_to_uzs)
        amount_usd = income["amount"] if income["currency"] == "USD" else income["amount"] / rate
        week_start = get_week_start()

        db.add_income(
            user_id=user_id,
            description=income["description"],
            amount=income["amount"],
            currency=income["currency"],
            amount_usd=amount_usd,
            week_start=week_start,
        )

        _, _, week_total = db.get_week_usd_uzs_totals(user_id, week_start)
        today = date.today()
        display = f"${income['amount']:,.2f}" if income["currency"] == "USD" else f"{income['amount']:,.0f} so'm"

        await query.edit_message_text(
            f"💰 <b>Kirim qayd etildi</b>\n"
            f"─────────────────────────\n"
            f"📝 Tur:      <b>{income['description']}</b>\n"
            f"💵 Summa:    <b>{display}</b>\n"
            f"📅 Sana:     <b>{format_date_uz(today)}</b>\n"
            f"📊 Bu hafta: <b>${week_total:,.2f}</b>",
            parse_mode="HTML",
        )
        await context.bot.send_message(user_id, "Bosh menyu:", reply_markup=get_role_keyboard(role))

        # Owner notification
        if config.owner_id and config.owner_id != user_id:
            try:
                chat = await context.bot.get_chat(user_id)
                today_total = db.get_today_total_usd(user_id)
                await context.bot.send_message(
                    config.owner_id,
                    f"💸 <b>Yangi kirim</b>\n"
                    f"─────────────────────────\n"
                    f"👤 Ishchi:  <b>{chat.full_name}</b>\n"
                    f"📝 Tur:     <b>{income['description']}</b>\n"
                    f"💵 Summa:   <b>{display}</b>\n"
                    f"📊 Bugun:   <b>${today_total:,.2f}</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        return ConversationHandler.END
    except Exception as e:
        logger.error(f"income_confirm: {e}")
        await query.edit_message_text("Xatolik yuz berdi.")
        return ConversationHandler.END


async def income_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    role = config.get_role(update.effective_user.id)
    context.user_data.pop("pending_income", None)
    await update.message.reply_text("❌ Bekor qilindi.", reply_markup=get_role_keyboard(role))
    return ConversationHandler.END


def income_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💰 Kirim qo'shish$"), income_start)],
        states={
            INCOME_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, income_input),
            ],
            INCOME_CONFIRM: [
                CallbackQueryHandler(income_confirm, pattern="^income_(yes|no)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", income_cancel),
            MessageHandler(filters.Regex("^❌ Bekor qilish$"), income_cancel),
        ],
    )


# ── Show week — USD/UZS breakdown ─────────────────────────────────────────

async def show_week_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]
        user_id = update.effective_user.id

        if config.get_role(user_id) not in ("worker", "accountant"):
            return

        week_start = get_week_start()
        incomes = db.get_week_incomes(user_id, week_start)

        if not incomes:
            await update.message.reply_text(
                f"📊 Bu hafta (<b>{format_week_range(week_start)}</b>) kirim yo'q.",
                parse_mode="HTML",
            )
            return

        usd_direct, uzs_total, total_eq = db.get_week_usd_uzs_totals(user_id, week_start)
        today = date.today()

        lines = [
            f"📊 <b>Bu hafta kirimlaringiz</b>",
            f"─────────────────────────",
            f"📅 <b>{format_week_range(week_start)}, {today.year}</b>",
            "",
        ]
        for idx, rec in enumerate(incomes, 1):
            amt = f"${rec['amount']:,.2f}" if rec["currency"] == "USD" else f"{rec['amount']:,.0f} so'm"
            lines.append(f"{idx}. {rec['description']} — <b>{amt}</b>")

        lines.append("─────────────────────────")
        if usd_direct > 0:
            lines.append(f"💵 Jami USD:  <b>${usd_direct:,.2f}</b>")
        if uzs_total > 0:
            lines.append(f"💴 Jami UZS:  <b>{uzs_total:,.0f} so'm</b>")
        if usd_direct > 0 and uzs_total > 0:
            lines.append(f"💰 Umumiy:    <b>${total_eq:,.2f}</b>  (~$ga)")
        else:
            lines.append(f"💰 Jami:      <b>${total_eq:,.2f}</b>")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"show_week_handler: {e}")


# ── Show month — hafta bo'yicha breakdown ─────────────────────────────────

async def show_month_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]
        user_id = update.effective_user.id

        if config.get_role(user_id) not in ("worker", "accountant"):
            return

        today = date.today()
        weekly = db.get_month_weekly_summary(user_id, today.year, today.month)

        if not weekly:
            await update.message.reply_text(
                f"📅 Bu oy (<b>{UZ_MONTHS[today.month]}</b>) kirim yo'q.", parse_mode="HTML"
            )
            return

        total_usd = sum(w["total_usd"] for w in weekly)
        lines = [
            f"📅 <b>{UZ_MONTHS[today.month].capitalize()} {today.year} — oylik hisobot</b>",
            "─────────────────────────",
        ]
        for w in weekly:
            ws = date.fromisoformat(w["week_start"])
            week_range = format_week_range(ws)
            if w["is_submitted"]:
                action = w["submission_action"]
                if action == "confirmed":
                    status = "✅ tasdiqlandi"
                elif action == "rejected":
                    status = "❌ rad etildi"
                else:
                    status = "⏳ kutilmoqda"
            else:
                status = "📤 topshirilmagan"
            lines.append(f"📦 <b>{week_range}</b>:  ${w['total_usd']:,.2f}  {status}")

        lines += ["─────────────────────────", f"💰 Jami: <b>${total_usd:,.2f}</b>"]
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"show_month_handler: {e}")


# ── /hisobotim — shaxsiy Excel ────────────────────────────────────────────

async def hisobotim_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]
        user_id = update.effective_user.id
        role = config.get_role(user_id)

        if role not in ("worker", "accountant"):
            return

        today = date.today()
        incomes = db.get_month_incomes(user_id, today.year, today.month)

        if not incomes:
            await update.message.reply_text(
                f"📅 Bu oy ({UZ_MONTHS[today.month]}) kirim yo'q."
            )
            return

        from reports import generate_worker_excel
        chat = await context.bot.get_chat(user_id)
        excel_path = generate_worker_excel(
            incomes, chat.full_name or str(user_id), today.year, today.month
        )
        total_usd = sum(i["amount_usd"] for i in incomes)

        with open(excel_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"mening_kirimlarim_{today.year}_{today.month:02d}.xlsx",
                caption=(
                    f"📋 <b>Shaxsiy hisobot — {UZ_MONTHS[today.month]} {today.year}</b>\n"
                    f"💵 Jami: <b>${total_usd:,.2f}</b>\n"
                    f"📦 Kirimlar: <b>{len(incomes)} ta</b>"
                ),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"hisobotim_handler: {e}")
        await update.message.reply_text(f"Xatolik: {e}")


# ── Submit week conversation ───────────────────────────────────────────────

async def submit_week_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]
        user_id = update.effective_user.id

        if config.get_role(user_id) not in ("worker", "accountant"):
            return ConversationHandler.END

        week_start = get_week_start()

        if db.has_week_submission(user_id, week_start):
            await update.message.reply_text(
                f"ℹ️ Bu hafta (<b>{format_week_range(week_start)}</b>) allaqachon topshirilgan.",
                parse_mode="HTML",
            )
            return ConversationHandler.END

        incomes = db.get_week_incomes(user_id, week_start)
        if not incomes:
            await update.message.reply_text("❌ Bu hafta kirim yo'q. Avval kirim qo'shing.")
            return ConversationHandler.END

        total_usd = sum(i["amount_usd"] for i in incomes)
        total_uzs = sum(i["amount"] for i in incomes if i["currency"] == "UZS")

        context.user_data["pending_submit"] = {
            "week_start": week_start.isoformat(),
            "total_usd": total_usd,
            "total_uzs": total_uzs,
            "count": len(incomes),
        }

        await update.message.reply_text(
            f"📦 <b>Haftalik hisobot</b>\n"
            f"─────────────────────────\n"
            f"📅 Hafta:    <b>{format_week_range(week_start)}</b>\n"
            f"📋 Kirimlar: <b>{len(incomes)} ta</b>\n"
            f"💵 Jami:     <b>${total_usd:,.2f}</b>\n\n"
            f"Bugalterga topshirasizmi?",
            parse_mode="HTML",
            reply_markup=confirm_inline("submit_yes", "submit_no"),
        )
        return SUBMIT_CONFIRM
    except Exception as e:
        logger.error(f"submit_week_start: {e}")
        return ConversationHandler.END


async def submit_week_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    user_id = query.from_user.id
    role = config.get_role(user_id)

    if query.data == "submit_no":
        context.user_data.pop("pending_submit", None)
        await query.edit_message_text("❌ Bekor qilindi.")
        await context.bot.send_message(user_id, "Bosh menyu:", reply_markup=get_role_keyboard(role))
        return ConversationHandler.END

    submit = context.user_data.pop("pending_submit", None)
    if not submit:
        await query.edit_message_text("Xatolik: ma'lumot topilmadi.")
        return ConversationHandler.END

    try:
        week_start = date.fromisoformat(submit["week_start"])
        submission_id = db.create_submission(
            worker_id=user_id,
            week_start=week_start,
            total_usd=submit["total_usd"],
            total_uzs=submit["total_uzs"],
        )

        await query.edit_message_text(
            f"✅ <b>Topshirildi!</b>\n\nBugalter tasdiqlaganida xabar keladi.",
            parse_mode="HTML",
        )
        await context.bot.send_message(user_id, "Bosh menyu:", reply_markup=get_role_keyboard(role))

        # Notify accountant
        try:
            worker = await context.bot.get_chat(user_id)
            full_name = worker.full_name or str(user_id)
            await context.bot.send_message(
                config.accountant_id,
                f"📦 <b>Haftalik hisobot</b>\n"
                f"─────────────────────────\n"
                f"👤 Ishchi:    <b>{full_name}</b>\n"
                f"📅 Hafta:     <b>{format_week_range(week_start)}</b>\n"
                f"💵 Jami:      <b>${submit['total_usd']:,.2f}</b>\n"
                f"📋 Kirimlar:  <b>{submit['count']} ta</b>",
                parse_mode="HTML",
                reply_markup=accountant_action_inline(submission_id),
            )
        except Exception:
            pass

        return ConversationHandler.END
    except Exception as e:
        logger.error(f"submit_week_confirm: {e}")
        await query.edit_message_text("Xatolik yuz berdi.")
        return ConversationHandler.END


async def submit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    role = config.get_role(update.effective_user.id)
    context.user_data.pop("pending_submit", None)
    await update.message.reply_text("❌ Bekor qilindi.", reply_markup=get_role_keyboard(role))
    return ConversationHandler.END


def submit_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📦 Bugalterga topshirish$"), submit_week_start)],
        states={
            SUBMIT_CONFIRM: [
                CallbackQueryHandler(submit_week_confirm, pattern="^submit_(yes|no)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", submit_cancel),
            MessageHandler(filters.Regex("^❌ Bekor qilish$"), submit_cancel),
        ],
    )


# ── Valyuta kursi conversation (accountant) ───────────────────────────────

async def rate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    if config.get_role(update.effective_user.id) != "accountant":
        return ConversationHandler.END

    current_rate = db.get_current_rate(config.usd_to_uzs)
    await update.message.reply_text(
        f"💱 <b>Valyuta kursi</b>\n\n"
        f"Hozirgi kurs: <b>1$ = {current_rate:,.0f} so'm</b>\n\n"
        f"Yangi kurs kiriting:\n<code>12800</code>\n\n"
        f"Bekor qilish: /cancel",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    return RATE_INPUT


async def rate_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        if text == "❌ Bekor qilish":
            return await rate_cancel(update, context)

        try:
            new_rate = float(text.replace(",", "").replace(" ", ""))
            if not (1000 <= new_rate <= 200000):
                raise ValueError("out of range")
        except ValueError:
            await update.message.reply_text(
                "❌ Noto'g'ri format. Son kiriting, masalan: <code>12800</code>",
                parse_mode="HTML",
            )
            return RATE_INPUT

        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]
        user_id = update.effective_user.id

        db.set_rate(new_rate, user_id)

        await update.message.reply_text(
            f"✅ <b>Kurs yangilandi!</b>\n\n💱 1$ = <b>{new_rate:,.0f} so'm</b>",
            parse_mode="HTML",
            reply_markup=get_role_keyboard("accountant"),
        )

        if config.owner_id:
            try:
                await context.bot.send_message(
                    config.owner_id,
                    f"💱 <b>Kurs yangilandi</b>\n\n1$ = <b>{new_rate:,.0f} so'm</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        return ConversationHandler.END
    except Exception as e:
        logger.error(f"rate_input: {e}")
        return RATE_INPUT


async def rate_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    role = config.get_role(update.effective_user.id)
    await update.message.reply_text("❌ Bekor qilindi.", reply_markup=get_role_keyboard(role))
    return ConversationHandler.END


def rate_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💱 Kurs$"), rate_start)],
        states={
            RATE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, rate_input),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", rate_cancel),
            MessageHandler(filters.Regex("^❌ Bekor qilish$"), rate_cancel),
        ],
    )


# ── Accountant: ishchilar ro'yxati + pending ──────────────────────────────

async def workers_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]
        user_id = update.effective_user.id

        if config.get_role(user_id) != "accountant":
            return

        workers = db.get_all_users_by_role("worker")
        week_start = get_week_start()
        pending_subs = db.get_pending_submissions()
        pending_worker_ids = {s["worker_id"] for s in pending_subs}

        if not workers:
            await update.message.reply_text("👥 Hech qanday ishchi topilmadi.")
            return

        lines = [
            f"👥 <b>Ishchilar — {format_week_range(week_start)}</b>",
            "─────────────────────────",
        ]
        for w in workers:
            wid = w["telegram_user_id"]
            name = w["full_name"] or w["username"] or str(wid)
            _, _, total = db.get_week_usd_uzs_totals(wid, week_start)
            incomes = db.get_week_incomes(wid, week_start)

            if wid in pending_worker_ids:
                status = "⏳ tasdiqlash kutilmoqda"
            elif db.has_week_submission(wid, week_start):
                status = "✅ tasdiqlandi"
            elif incomes:
                status = "📤 hali topshirmagan"
            else:
                status = "— kirim yo'q"

            lines.append(f"🧑‍💼 <b>{name}</b>")
            lines.append(f"   ${total:,.2f} ({len(incomes)} ta)  {status}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

        # Pending submissions bilan qayta amallar
        if pending_subs:
            await update.message.reply_text(
                f"⏳ <b>{len(pending_subs)} ta tasdiqlash kutilmoqda:</b>",
                parse_mode="HTML",
            )
            for sub in pending_subs:
                sub_ws = date.fromisoformat(sub["week_start"])
                name = sub.get("full_name") or sub.get("username") or str(sub["worker_id"])
                await update.message.reply_text(
                    f"📦 <b>{name}</b>\n"
                    f"📅 {format_week_range(sub_ws)}\n"
                    f"💵 ${sub['total_usd']:,.2f}",
                    parse_mode="HTML",
                    reply_markup=accountant_action_inline(sub["id"]),
                )
    except Exception as e:
        logger.error(f"workers_list_handler: {e}")


async def weekly_report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]
        user_id = update.effective_user.id

        if config.get_role(user_id) != "accountant":
            return

        week_start = get_week_start()
        incomes = db.get_all_incomes_for_week(week_start)

        if not incomes:
            await update.message.reply_text(
                f"📊 Bu hafta ({format_week_range(week_start)}) kirim yo'q."
            )
            return

        total_usd = sum(i["amount_usd"] for i in incomes)
        by_worker: dict = {}
        for inc in incomes:
            name = inc.get("full_name") or inc.get("username") or str(inc["user_id"])
            by_worker.setdefault(name, {"total": 0.0, "count": 0})
            by_worker[name]["total"] += inc["amount_usd"]
            by_worker[name]["count"] += 1

        lines = [
            f"📊 <b>Haftalik hisobot — {format_week_range(week_start)}</b>",
            "─────────────────────────",
        ]
        for name, data in by_worker.items():
            lines.append(f"👤 <b>{name}</b>: ${data['total']:,.2f} ({data['count']} ta)")
        lines += ["─────────────────────────", f"💵 <b>Umumiy jami: ${total_usd:,.2f}</b>"]

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"weekly_report_handler: {e}")


async def monthly_report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]
        user_id = update.effective_user.id

        if config.get_role(user_id) != "accountant":
            return

        today = date.today()
        incomes = db.get_all_incomes_for_month(today.year, today.month)

        if not incomes:
            await update.message.reply_text(
                f"📅 Bu oy ({UZ_MONTHS[today.month]}) kirim yo'q."
            )
            return

        from reports import generate_monthly_excel
        excel_path = generate_monthly_excel(incomes, today.year, today.month)
        total_usd = sum(i["amount_usd"] for i in incomes)

        with open(excel_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"hisobot_{today.year}_{today.month:02d}.xlsx",
                caption=(
                    f"📋 <b>Oylik hisobot — {UZ_MONTHS[today.month]} {today.year}</b>\n"
                    f"💵 Jami: <b>${total_usd:,.2f}</b>\n"
                    f"📦 Kirimlar: <b>{len(incomes)} ta</b>"
                ),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"monthly_report_handler: {e}")
        await update.message.reply_text(f"Xatolik: {e}")


# ── Owner handlers ─────────────────────────────────────────────────────────

async def owner_all_workers_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]
        user_id = update.effective_user.id

        if config.get_role(user_id) != "owner":
            return

        week_start = get_week_start()
        workers    = db.get_all_users_by_role("worker")
        accountants = db.get_all_users_by_role("accountant")
        pending_subs = db.get_pending_submissions()
        pending_ids  = {s["worker_id"] for s in pending_subs}

        if not workers and not accountants:
            await update.message.reply_text("👥 Hech qanday xodim topilmadi.")
            return

        # Workers sorted by total (medal ranking)
        workers_data = []
        for w in workers:
            _, _, total = db.get_week_usd_uzs_totals(w["telegram_user_id"], week_start)
            workers_data.append((w, total))
        workers_data.sort(key=lambda x: x[1], reverse=True)

        medals = ["🥇", "🥈", "🥉"]
        total_all      = sum(t for _, t in workers_data)
        submitted_total = 0.0
        pending_total   = 0.0

        for w, total in workers_data:
            wid = w["telegram_user_id"]
            if wid in pending_ids:
                pending_total += total
            elif db.has_week_submission(wid, week_start):
                submitted_total += total
            else:
                pending_total += total

        lines = [
            f"👥 <b>Barcha ishchilar — {format_week_range(week_start)}</b>",
            "─────────────────────────",
        ]
        for idx, (w, total) in enumerate(workers_data):
            medal = medals[idx] if idx < 3 else f"{idx+1}."
            name = w["full_name"] or w["username"] or str(w["telegram_user_id"])
            lines.append(f"{medal} <b>{name}</b>  ${total:,.2f}")

        for acc in accountants:
            _, _, total = db.get_week_usd_uzs_totals(acc["telegram_user_id"], week_start)
            total_all += total
            name = acc["full_name"] or acc["username"] or str(acc["telegram_user_id"])
            lines.append(f"🧮 <b>{name}</b>  ${total:,.2f}")

        lines += [
            "─────────────────────────",
            f"💰 Umumiy bu hafta: <b>${total_all:,.2f}</b>",
            f"📦 Topshirilgan:    <b>${submitted_total:,.2f}</b>",
            f"⏳ Kutilmoqda:      <b>${pending_total:,.2f}</b>",
        ]

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"owner_all_workers_handler: {e}")


async def owner_weekly_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]

        if config.get_role(update.effective_user.id) != "owner":
            return

        week_start = get_week_start()
        incomes = db.get_all_incomes_for_week(week_start)
        total_usd = sum(i["amount_usd"] for i in incomes)

        by_worker: dict = {}
        for inc in incomes:
            name = inc.get("full_name") or inc.get("username") or str(inc["user_id"])
            by_worker.setdefault(name, {"total": 0.0, "count": 0})
            by_worker[name]["total"] += inc["amount_usd"]
            by_worker[name]["count"] += 1

        lines = [
            f"📊 <b>Haftalik umumiy — {format_week_range(week_start)}</b>",
            "─────────────────────────",
        ]
        if by_worker:
            for name, data in sorted(by_worker.items(), key=lambda x: x[1]["total"], reverse=True):
                lines.append(f"👤 <b>{name}</b>: ${data['total']:,.2f} ({data['count']} ta)")
        else:
            lines.append("Bu hafta kirim yo'q.")
        lines += ["─────────────────────────", f"💵 <b>Umumiy: ${total_usd:,.2f}</b>"]

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"owner_weekly_handler: {e}")


async def owner_monthly_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        config: Config = context.bot_data["config"]
        db: Database = context.bot_data["db"]

        if config.get_role(update.effective_user.id) != "owner":
            return

        today = date.today()
        incomes = db.get_all_incomes_for_month(today.year, today.month)
        total_usd     = sum(i["amount_usd"] for i in incomes)
        confirmed_usd = sum(i["amount_usd"] for i in incomes if i["status"] == "confirmed")
        pending_usd   = total_usd - confirmed_usd

        by_worker: dict = {}
        for inc in incomes:
            name = inc.get("full_name") or inc.get("username") or str(inc["user_id"])
            by_worker.setdefault(name, {"total": 0.0, "count": 0})
            by_worker[name]["total"] += inc["amount_usd"]
            by_worker[name]["count"] += 1

        lines = [
            f"📅 <b>Oylik umumiy — {UZ_MONTHS[today.month].capitalize()} {today.year}</b>",
            "─────────────────────────",
        ]
        if by_worker:
            for name, data in sorted(by_worker.items(), key=lambda x: x[1]["total"], reverse=True):
                lines.append(f"👤 <b>{name}</b>: ${data['total']:,.2f} ({data['count']} ta)")
        else:
            lines.append("Bu oy kirim yo'q.")

        lines += [
            "─────────────────────────",
            f"💰 Umumiy:       <b>${total_usd:,.2f}</b>",
            f"✅ Tasdiqlangan: <b>${confirmed_usd:,.2f}</b>",
            f"⏳ Kutilmoqda:   <b>${pending_usd:,.2f}</b>",
        ]

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"owner_monthly_handler: {e}")
