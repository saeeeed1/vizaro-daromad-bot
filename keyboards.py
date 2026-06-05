from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton


def worker_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["💰 Kirim qo'shish"],
        ["📊 Bu hafta", "📅 Bu oy"],
        ["📦 Bugalterga topshirish"],
    ], resize_keyboard=True)


def accountant_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["💰 Kirim qo'shish"],
        ["👥 Ishchilar", "📊 Haftalik"],
        ["📋 Oylik hisobot", "💱 Kurs"],
    ], resize_keyboard=True)


def owner_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["👥 Barcha ishchilar"],
        ["📊 Haftalik", "📅 Oylik"],
    ], resize_keyboard=True)


def get_role_keyboard(role: str) -> ReplyKeyboardMarkup | ReplyKeyboardRemove:
    if role == "worker":
        return worker_keyboard()
    if role == "accountant":
        return accountant_keyboard()
    if role == "owner":
        return owner_keyboard()
    return ReplyKeyboardRemove()


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["❌ Bekor qilish"]], resize_keyboard=True)


def confirm_inline(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ha", callback_data=yes_cb),
        InlineKeyboardButton("❌ Yo'q", callback_data=no_cb),
    ]])


def accountant_action_inline(submission_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"acc_confirm_{submission_id}"),
        InlineKeyboardButton("❌ Rad etish", callback_data=f"acc_reject_{submission_id}"),
    ]])
