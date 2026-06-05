import asyncio
import logging
from datetime import date, timedelta
from aiohttp import web

from config import load_config
from database import Database
from handlers import get_week_start, UZ_MONTHS, format_week_range

logger = logging.getLogger(__name__)

_config = None
_db = None


def _get_ctx():
    global _config, _db
    if _config is None:
        _config = load_config()
    if _db is None:
        _db = Database()
    return _config, _db


_ALLOWED_ORIGINS = {
    "https://vizaro-daromad.vercel.app",
    "https://web.telegram.org",
    "http://localhost:3000",
}


def _cors(resp: web.Response, origin: str = "*") -> web.Response:
    # Allow Vercel, Telegram WebApp va localhost
    allow = origin if origin in _ALLOWED_ORIGINS else "*"
    resp.headers["Access-Control-Allow-Origin"]  = allow
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Vary"] = "Origin"
    return resp


def _origin(req: web.Request) -> str:
    return req.headers.get("Origin", "*")


async def handle_options(req: web.Request) -> web.Response:
    return _cors(web.Response(status=200), _origin(req))


async def handle_me(req: web.Request) -> web.Response:
    config, db = _get_ctx()
    try:
        user_id = int(req.rel_url.query.get("user_id", 0))
    except ValueError:
        return _cors(web.json_response({"error": "invalid user_id"}, status=400), _origin(req))

    role = config.get_role(user_id)
    user = db.get_user(user_id)
    return _cors(web.json_response({
        "user_id":    user_id,
        "role":       role,
        "name":       user["full_name"] if user else "",
        "username":   user["username"]  if user else "",
        "authorized": role != "unknown",
    }), _origin(req))


async def handle_dashboard(req: web.Request) -> web.Response:
    config, db = _get_ctx()
    try:
        user_id = int(req.rel_url.query.get("user_id", 0))
    except ValueError:
        return _cors(web.json_response({"error": "invalid user_id"}, status=400), _origin(req))

    role = config.get_role(user_id)
    today = date.today()
    ws = get_week_start()
    UZ_DAYS_SHORT = {0: "Du", 1: "Se", 2: "Ch", 3: "Pa", 4: "Ju", 5: "Sh", 6: "Ya"}

    if role in ("worker", "accountant"):
        week_inc   = db.get_week_incomes(user_id, ws)
        month_inc  = db.get_month_incomes(user_id, today.year, today.month)
        week_total = db.get_week_total_usd(user_id, ws)
        month_total = sum(i["amount_usd"] for i in month_inc)

        subs = db.get_worker_submissions(user_id)
        confirmed = sum(1 for s in subs if s["accountant_action"] == "confirmed")
        pending   = sum(1 for s in subs if not s["accountant_action"])

        # 7-day chart
        chart = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            day_total = sum(
                inc["amount_usd"] for inc in db.get_week_incomes(user_id, get_week_start(d))
                if inc["created_at"][:10] == d.isoformat()
            )
            chart.append({"day": UZ_DAYS_SHORT[d.weekday()], "date": d.isoformat(), "total": round(day_total, 2)})

        recent = list(reversed(week_inc[-10:]))
        return _cors(web.json_response({
            "role":            role,
            "week_total":      round(week_total, 2),
            "month_total":     round(month_total, 2),
            "week_count":      len(week_inc),
            "month_count":     len(month_inc),
            "confirmed_count": confirmed,
            "pending_count":   pending,
            "chart":           chart,
            "recent":          [_fmt_income(i) for i in recent],
        }))

    elif role == "owner":
        workers     = db.get_all_users_by_role("worker") + db.get_all_users_by_role("accountant")
        week_inc    = db.get_all_incomes_for_week(ws)
        month_inc   = db.get_all_incomes_for_month(today.year, today.month)
        total_week  = sum(i["amount_usd"] for i in week_inc)
        total_month = sum(i["amount_usd"] for i in month_inc)

        pending_subs = db.get_pending_submissions()
        pending_ids  = {s["worker_id"] for s in pending_subs}

        submitted_total = 0.0
        pending_total   = 0.0
        worker_stats = []
        for w in workers:
            wid = w["telegram_user_id"]
            wtotal = db.get_week_total_usd(wid, ws)
            if wid in pending_ids:
                pending_total += wtotal;  sub_status = "pending"
            elif db.has_week_submission(wid, ws):
                submitted_total += wtotal; sub_status = "confirmed"
            else:
                pending_total += wtotal;  sub_status = "none"
            worker_stats.append({
                "id":         wid,
                "name":       w["full_name"] or w["username"] or str(wid),
                "role":       w["role"],
                "week_total": round(wtotal, 2),
                "sub_status": sub_status,
                "percentage": round(wtotal / total_week * 100, 1) if total_week else 0,
            })
        worker_stats.sort(key=lambda x: x["week_total"], reverse=True)

        # Stacked 7-day chart
        chart = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            entry: dict = {"day": UZ_DAYS_SHORT[d.weekday()], "date": d.isoformat()}
            for w in workers:
                wid = w["telegram_user_id"]
                name = w["full_name"] or w["username"] or str(wid)
                d_ws = get_week_start(d)
                day_t = sum(
                    inc["amount_usd"] for inc in db.get_week_incomes(wid, d_ws)
                    if inc["created_at"][:10] == d.isoformat()
                )
                entry[name] = round(day_t, 2)
            chart.append(entry)

        return _cors(web.json_response({
            "role":             "owner",
            "total_week":       round(total_week, 2),
            "total_month":      round(total_month, 2),
            "submitted_total":  round(submitted_total, 2),
            "pending_total":    round(pending_total, 2),
            "workers":          worker_stats,
            "chart":            chart,
        }))

    return _cors(web.json_response({"error": "unauthorized"}, status=403), _origin(req))


async def handle_history(req: web.Request) -> web.Response:
    config, db = _get_ctx()
    try:
        user_id = int(req.rel_url.query.get("user_id", 0))
        limit   = int(req.rel_url.query.get("limit", 30))
    except ValueError:
        return _cors(web.json_response({"error": "invalid params"}, status=400), _origin(req))

    role = config.get_role(user_id)
    today = date.today()

    if role in ("worker", "accountant"):
        inc = list(reversed(db.get_month_incomes(user_id, today.year, today.month)[-limit:]))
    elif role == "owner":
        inc = list(reversed(db.get_all_incomes_for_month(today.year, today.month)[-limit:]))
    else:
        return _cors(web.json_response({"error": "unauthorized"}, status=403), _origin(req))

    return _cors(web.json_response({
        "incomes": [_fmt_income(i) for i in inc],
        "total":   round(sum(i["amount_usd"] for i in inc), 2),
    }))


def _utc_to_local(dt_str: str, tz: str = "Asia/Tashkent") -> str:
    """UTC isoformat → 'YYYY-MM-DD HH:MM' mahalliy vaqt."""
    if not dt_str:
        return ""
    try:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        dt = _dt.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        loc = dt.astimezone(ZoneInfo(tz))
        return loc.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str[:16]


async def handle_accountant(req: web.Request) -> web.Response:
    config, db = _get_ctx()
    try:
        user_id = int(req.rel_url.query.get("user_id", 0))
    except ValueError:
        return _cors(web.json_response({"error": "invalid user_id"}, status=400), _origin(req))

    if config.get_role(user_id) != "accountant":
        return _cors(web.json_response({"error": "unauthorized"}, status=403), _origin(req))

    today = date.today()
    ws    = get_week_start()

    workers     = db.get_all_users_by_role("worker")
    submissions = db.get_week_submissions_with_worker(ws)
    sub_by_wid  = {s["worker_id"]: s for s in submissions}

    # ── Haftalik qabul qilingan va kutilmoqda ─────────────────────────────
    received: list = []
    pending_list: list = []

    for s in submissions:
        wid       = s["worker_id"]
        usd       = db.get_week_total_usd(wid, ws)
        inc_count = len(db.get_week_incomes(wid, ws))
        status    = s["accountant_action"] or "pending"
        item = {
            "worker_id":    wid,
            "worker_name":  s.get("full_name") or s.get("username") or str(wid),
            "total_usd":    round(usd, 2),
            "count":        inc_count,
            "confirmed_at": _utc_to_local(s["actioned_at"] or ""),
            "status":       status,
        }
        (received if status == "confirmed" else pending_list).append(item)

    # Workers who haven't submitted
    for w in workers:
        wid = w["telegram_user_id"]
        if wid in sub_by_wid:
            continue
        usd       = db.get_week_total_usd(wid, ws)
        inc_count = len(db.get_week_incomes(wid, ws))
        pending_list.append({
            "worker_id":    wid,
            "worker_name":  w["full_name"] or w["username"] or str(wid),
            "total_usd":    round(usd, 2),
            "count":        inc_count,
            "confirmed_at": "",
            "status":       "not_submitted",
        })

    # ── Oylik summary ─────────────────────────────────────────────────────
    month_incs          = db.get_all_incomes_for_month(today.year, today.month)
    m_confirmed         = [i for i in month_incs if i.get("status") == "confirmed"]
    m_usd_total         = sum(i["amount_usd"] for i in m_confirmed)
    m_workers_confirmed = len({i["user_id"] for i in m_confirmed})

    # ── Bugalterning o'z daromadi ─────────────────────────────────────────
    acc_week_inc = db.get_week_incomes(user_id, ws)
    acc_total    = db.get_week_total_usd(user_id, ws)
    acc_month_inc = db.get_month_incomes(user_id, today.year, today.month)
    acc_m_total   = sum(i["amount_usd"] for i in acc_month_inc)

    # ── Summary ───────────────────────────────────────────────────────────
    sum_usd = sum(r["total_usd"] for r in received)

    return _cors(web.json_response({
        "period":     "week",
        "week_label": format_week_range(ws),
        "received":   received,
        "pending":    pending_list,
        "summary": {
            "total_usd":       round(sum_usd, 2),
            "confirmed_count": len(received),
            "pending_count":   len(pending_list),
        },
        "month_summary": {
            "total_usd":       round(m_usd_total, 2),
            "confirmed_count": m_workers_confirmed,
        },
        "own_income": {
            "total_usd":   round(acc_total, 2),
            "count":       len(acc_week_inc),
            "month_total": round(acc_m_total, 2),
        },
    }), _origin(req))


def _fmt_income(inc: dict) -> dict:
    return {
        "id":          inc["id"],
        "description": inc["description"],
        "amount":      inc["amount"],
        "currency":    inc["currency"],
        "amount_usd":  round(inc["amount_usd"], 2),
        "date":        inc["created_at"][:10],
        "status":      inc.get("status", "pending"),
        "name":        inc.get("full_name", ""),
    }


async def start_api_server():
    app = web.Application()
    app.router.add_get("/api/me",          handle_me)
    app.router.add_get("/api/dashboard",   handle_dashboard)
    app.router.add_get("/api/history",     handle_history)
    app.router.add_get("/api/accountant",  handle_accountant)
    app.router.add_options("/{tail:.*}",   handle_options)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8081)
    await site.start()
    logger.info("API server http://0.0.0.0:8081 da ishlamoqda")
