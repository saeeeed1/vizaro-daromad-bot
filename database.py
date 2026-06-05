import sqlite3
import logging
from datetime import date, datetime
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)
DB_FILE = "daromad.db"


class Database:
    def __init__(self, db_file: str = DB_FILE):
        self.db_file = db_file

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    role TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS income_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL,
                    amount_usd REAL NOT NULL,
                    week_start DATE NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    FOREIGN KEY (user_id) REFERENCES users(telegram_user_id)
                );

                CREATE TABLE IF NOT EXISTS weekly_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    worker_id INTEGER NOT NULL,
                    week_start DATE NOT NULL,
                    total_usd REAL NOT NULL,
                    total_uzs REAL NOT NULL,
                    submitted_at TEXT NOT NULL,
                    accountant_action TEXT,
                    accountant_note TEXT,
                    actioned_at TEXT,
                    FOREIGN KEY (worker_id) REFERENCES users(telegram_user_id)
                );

                CREATE TABLE IF NOT EXISTS exchange_rates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usd_to_uzs REAL NOT NULL,
                    set_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
        logger.info("Database initialized")

    # ── Users ──────────────────────────────────────────────────────────────

    def upsert_user(self, telegram_user_id: int, username: str, full_name: str, role: str):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO users (telegram_user_id, username, full_name, role)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name
            """, (telegram_user_id, username, full_name, role))

    def get_user(self, telegram_user_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_all_users_by_role(self, role: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE role = ? AND is_active = 1", (role,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Income records ──────────────────────────────────────────────────────

    def add_income(self, user_id: int, description: str, amount: float,
                   currency: str, amount_usd: float, week_start: date) -> int:
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO income_records
                (user_id, description, amount, currency, amount_usd, week_start, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, description, amount, currency, amount_usd,
                  week_start.isoformat(), datetime.now().isoformat()))
        return cursor.lastrowid

    def get_week_incomes(self, user_id: int, week_start: date) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM income_records
                WHERE user_id = ? AND week_start = ?
                ORDER BY created_at
            """, (user_id, week_start.isoformat())).fetchall()
        return [dict(r) for r in rows]

    def get_week_total_usd(self, user_id: int, week_start: date) -> float:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT COALESCE(SUM(amount_usd), 0) AS total
                FROM income_records WHERE user_id = ? AND week_start = ?
            """, (user_id, week_start.isoformat())).fetchone()
        return float(row["total"]) if row else 0.0

    def get_month_incomes(self, user_id: int, year: int, month: int) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM income_records
                WHERE user_id = ? AND strftime('%Y-%m', week_start) = ?
                ORDER BY created_at
            """, (user_id, f"{year}-{month:02d}")).fetchall()
        return [dict(r) for r in rows]

    def get_today_total_usd(self, user_id: int) -> float:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT COALESCE(SUM(amount_usd), 0) AS total
                FROM income_records
                WHERE user_id = ? AND DATE(created_at) = ?
            """, (user_id, date.today().isoformat())).fetchone()
        return float(row["total"]) if row else 0.0

    def get_all_incomes_for_week(self, week_start: date) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT ir.*, u.full_name, u.username
                FROM income_records ir
                JOIN users u ON ir.user_id = u.telegram_user_id
                WHERE ir.week_start = ?
                ORDER BY u.full_name, ir.created_at
            """, (week_start.isoformat(),)).fetchall()
        return [dict(r) for r in rows]

    def get_all_incomes_for_month(self, year: int, month: int) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT ir.*, u.full_name, u.username
                FROM income_records ir
                JOIN users u ON ir.user_id = u.telegram_user_id
                WHERE strftime('%Y-%m', ir.week_start) = ?
                ORDER BY u.full_name, ir.created_at
            """, (f"{year}-{month:02d}",)).fetchall()
        return [dict(r) for r in rows]

    # ── Weekly submissions ──────────────────────────────────────────────────

    def has_week_submission(self, worker_id: int, week_start: date) -> bool:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT id FROM weekly_submissions
                WHERE worker_id = ? AND week_start = ?
            """, (worker_id, week_start.isoformat())).fetchone()
        return row is not None

    def create_submission(self, worker_id: int, week_start: date,
                          total_usd: float, total_uzs: float) -> int:
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO weekly_submissions
                (worker_id, week_start, total_usd, total_uzs, submitted_at)
                VALUES (?, ?, ?, ?, ?)
            """, (worker_id, week_start.isoformat(), total_usd, total_uzs,
                  datetime.now().isoformat()))
        return cursor.lastrowid

    def get_worker_submissions(self, worker_id: int) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM weekly_submissions WHERE worker_id = ?
                ORDER BY submitted_at DESC
            """, (worker_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_submission(self, submission_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM weekly_submissions WHERE id = ?", (submission_id,)
            ).fetchone()
        return dict(row) if row else None

    def confirm_submission(self, submission_id: int, accountant_id: int):
        actioned_at = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute("""
                UPDATE weekly_submissions
                SET accountant_action = 'confirmed', actioned_at = ?
                WHERE id = ?
            """, (actioned_at, submission_id))
            sub = conn.execute(
                "SELECT worker_id, week_start FROM weekly_submissions WHERE id = ?",
                (submission_id,)
            ).fetchone()
            if sub:
                conn.execute("""
                    UPDATE income_records SET status = 'confirmed'
                    WHERE user_id = ? AND week_start = ?
                """, (sub["worker_id"], sub["week_start"]))

    def reject_submission(self, submission_id: int, accountant_id: int, note: str):
        actioned_at = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute("""
                UPDATE weekly_submissions
                SET accountant_action = 'rejected', accountant_note = ?, actioned_at = ?
                WHERE id = ?
            """, (note, actioned_at, submission_id))
            sub = conn.execute(
                "SELECT worker_id, week_start FROM weekly_submissions WHERE id = ?",
                (submission_id,)
            ).fetchone()
            if sub:
                conn.execute("""
                    UPDATE income_records SET status = 'rejected'
                    WHERE user_id = ? AND week_start = ?
                """, (sub["worker_id"], sub["week_start"]))

    # ── Exchange rates ──────────────────────────────────────────────────────

    def get_current_rate(self, default: float = 12800.0) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT usd_to_uzs FROM exchange_rates ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return float(row["usd_to_uzs"]) if row else default

    def set_rate(self, usd_to_uzs: float, set_by: int):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO exchange_rates (usd_to_uzs, set_by, created_at)
                VALUES (?, ?, ?)
            """, (usd_to_uzs, set_by, datetime.now().isoformat()))

    # ── Extended queries ────────────────────────────────────────────────────

    def get_week_usd_uzs_totals(self, user_id: int, week_start: date) -> tuple:
        """Returns (usd_direct, uzs_total, total_usd_equivalent)"""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN currency='USD' THEN amount ELSE 0 END), 0) AS usd_direct,
                    COALESCE(SUM(CASE WHEN currency='UZS' THEN amount ELSE 0 END), 0) AS uzs_total,
                    COALESCE(SUM(amount_usd), 0) AS total_eq
                FROM income_records WHERE user_id = ? AND week_start = ?
            """, (user_id, week_start.isoformat())).fetchone()
        if row:
            return float(row["usd_direct"]), float(row["uzs_total"]), float(row["total_eq"])
        return 0.0, 0.0, 0.0

    def get_month_weekly_summary(self, user_id: int, year: int, month: int) -> List[Dict]:
        """Returns weekly breakdown for a month with submission status."""
        month_str = f"{year}-{month:02d}"
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    week_start,
                    COALESCE(SUM(CASE WHEN currency='USD' THEN amount ELSE 0 END), 0) AS usd_direct,
                    COALESCE(SUM(CASE WHEN currency='UZS' THEN amount ELSE 0 END), 0) AS uzs_total,
                    COALESCE(SUM(amount_usd), 0) AS total_usd,
                    COUNT(*) AS cnt
                FROM income_records
                WHERE user_id = ? AND strftime('%Y-%m', week_start) = ?
                GROUP BY week_start ORDER BY week_start
            """, (user_id, month_str)).fetchall()

            result = []
            for r in rows:
                sub = conn.execute("""
                    SELECT accountant_action FROM weekly_submissions
                    WHERE worker_id = ? AND week_start = ?
                """, (user_id, r["week_start"])).fetchone()
                result.append({
                    "week_start":        r["week_start"],
                    "total_usd":         float(r["total_usd"]),
                    "uzs_total":         float(r["uzs_total"]),
                    "count":             int(r["cnt"]),
                    "submission_action": sub["accountant_action"] if sub else None,
                    "is_submitted":      sub is not None,
                })
        return result

    def get_pending_submissions(self) -> List[Dict]:
        """Submissions awaiting accountant action."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT ws.*, u.full_name, u.username
                FROM weekly_submissions ws
                JOIN users u ON ws.worker_id = u.telegram_user_id
                WHERE ws.accountant_action IS NULL
                ORDER BY ws.submitted_at ASC
            """).fetchall()
        return [dict(r) for r in rows]
