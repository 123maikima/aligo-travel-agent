"""
PostgreSQL 长期记忆存储后端。

该模块负责把 LongTermMemory 的 JSON 结构映射到 PostgreSQL 表中，
以便后续逐步从文件存储迁移到关系型持久化。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, List, Optional
import json
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover - 可选依赖
    psycopg = None
    dict_row = None
    Jsonb = None


class PostgresLongTermStore:
    """将长期记忆完整快照保存到 PostgreSQL。"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", False))
        self.available = psycopg is not None and self.enabled

    def _build_dsn(self) -> str:
        parts = [
            f"host={self.config.get('host', 'localhost')}",
            f"port={self.config.get('port', 5432)}",
            f"dbname={self.config.get('dbname', 'travel_agent')}",
            f"user={self.config.get('user', 'postgres')}",
        ]
        password = self.config.get("password")
        if password:
            parts.append(f"password={password}")
        sslmode = self.config.get("sslmode")
        if sslmode:
            parts.append(f"sslmode={sslmode}")
        connect_timeout = self.config.get("connect_timeout")
        if connect_timeout is not None:
            parts.append(f"connect_timeout={int(connect_timeout)}")
        return " ".join(parts)

    def _to_datetime(self, value: Any, default: Optional[datetime] = None) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return datetime.fromisoformat(value.strip())
            except ValueError:
                pass
        return default or datetime.now()

    def _jsonb(self, value: Any):
        """Wrap Python values for JSONB columns when psycopg is available."""
        return Jsonb(value)

    @contextmanager
    def _connect(self):
        if not self.available:
            raise RuntimeError("PostgreSQL backend is disabled or psycopg is not installed")
        conn = psycopg.connect(self._build_dsn(), row_factory=dict_row)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_schema(self):
        """初始化表结构，幂等执行。"""
        ddl = """
        CREATE TABLE IF NOT EXISTS ltm_users (
            user_id VARCHAR(64) PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ltm_preferences (
            id BIGSERIAL PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL REFERENCES ltm_users(user_id) ON DELETE CASCADE,
            pref_type VARCHAR(64) NOT NULL,
            value JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, pref_type)
        );

        CREATE TABLE IF NOT EXISTS ltm_chat_history (
            id BIGSERIAL PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL REFERENCES ltm_users(user_id) ON DELETE CASCADE,
            session_id VARCHAR(64),
            role VARCHAR(16) NOT NULL,
            content TEXT NOT NULL,
            metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_ltm_chat_user_time ON ltm_chat_history(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_ltm_chat_user_session ON ltm_chat_history(user_id, session_id, created_at);

        CREATE TABLE IF NOT EXISTS ltm_trip_history (
            id BIGSERIAL PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL REFERENCES ltm_users(user_id) ON DELETE CASCADE,
            trip_id VARCHAR(64) NOT NULL UNIQUE,
            origin VARCHAR(64),
            destination VARCHAR(64),
            start_date DATE,
            end_date DATE,
            purpose VARCHAR(128),
            details JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_ltm_trip_user_time ON ltm_trip_history(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_ltm_trip_user_destination ON ltm_trip_history(user_id, destination);

        CREATE TABLE IF NOT EXISTS ltm_statistics (
            user_id VARCHAR(64) PRIMARY KEY REFERENCES ltm_users(user_id) ON DELETE CASCADE,
            total_trips INTEGER NOT NULL DEFAULT 0,
            total_messages INTEGER NOT NULL DEFAULT 0,
            total_queries INTEGER NOT NULL DEFAULT 0,
            frequent_destinations JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                for statement in [stmt.strip() for stmt in ddl.split(";") if stmt.strip()]:
                    cur.execute(statement)
        logger.info("PostgreSQL long-term memory schema ensured")

    def load_snapshot(self, user_id: str) -> Optional[Dict[str, Any]]:
        """加载用户完整快照；若无记录则返回 None。"""
        if not self.available:
            return None

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, created_at, updated_at FROM ltm_users WHERE user_id = %s", (user_id,))
                user_row = cur.fetchone()
                if not user_row:
                    return None

                cur.execute(
                    """
                    SELECT pref_type, value
                    FROM ltm_preferences
                    WHERE user_id = %s
                    ORDER BY id ASC
                    """,
                    (user_id,),
                )
                preferences = []
                for row in cur.fetchall():
                    preferences.append({"type": row["pref_type"], "value": row["value"]})

                cur.execute(
                    """
                    SELECT session_id, role, content, metadata, created_at
                    FROM ltm_chat_history
                    WHERE user_id = %s
                    ORDER BY created_at ASC, id ASC
                    """,
                    (user_id,),
                )
                chat_history = []
                for row in cur.fetchall():
                    chat_history.append({
                        "role": row["role"],
                        "content": row["content"],
                        "timestamp": row["created_at"].isoformat() if row.get("created_at") else "",
                        "session_id": row["session_id"],
                        "metadata": row["metadata"] or {},
                    })

                cur.execute(
                    """
                    SELECT trip_id, origin, destination, start_date, end_date, purpose, details, created_at
                    FROM ltm_trip_history
                    WHERE user_id = %s
                    ORDER BY created_at ASC, id ASC
                    """,
                    (user_id,),
                )
                trip_history = []
                for row in cur.fetchall():
                    trip = {
                        "trip_id": row["trip_id"],
                        "timestamp": row["created_at"].isoformat() if row.get("created_at") else "",
                        "origin": row["origin"],
                        "destination": row["destination"],
                        "start_date": row["start_date"].isoformat() if row.get("start_date") else "",
                        "end_date": row["end_date"].isoformat() if row.get("end_date") else "",
                        "purpose": row["purpose"],
                    }
                    details = row["details"] or {}
                    if isinstance(details, dict):
                        trip.update(details)
                    trip_history.append(trip)

                cur.execute(
                    """
                    SELECT total_trips, total_messages, total_queries, frequent_destinations, updated_at
                    FROM ltm_statistics
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                stats_row = cur.fetchone()
                statistics = {
                    "total_trips": 0,
                    "total_messages": 0,
                    "total_queries": 0,
                    "frequent_destinations": {},
                }
                if stats_row:
                    statistics["total_trips"] = stats_row["total_trips"] or 0
                    statistics["total_messages"] = stats_row["total_messages"] or 0
                    statistics["total_queries"] = stats_row["total_queries"] or 0
                    statistics["frequent_destinations"] = stats_row["frequent_destinations"] or {}

        return {
            "user_id": user_id,
            "created_at": user_row["created_at"].isoformat() if user_row.get("created_at") else "",
            "updated_at": user_row["updated_at"].isoformat() if user_row.get("updated_at") else "",
            "preferences": preferences,
            "chat_history": chat_history,
            "trip_history": trip_history,
            "statistics": statistics,
        }

    def save_snapshot(self, user_id: str, data: Dict[str, Any]):
        """用完整快照覆盖用户数据。"""
        if not self.available:
            return

        created_at = self._to_datetime(data.get("created_at"))
        updated_at = self._to_datetime(data.get("updated_at"))
        preferences = data.get("preferences", []) or []
        chat_history = data.get("chat_history", []) or []
        trip_history = data.get("trip_history", []) or []
        statistics = data.get("statistics", {}) or {}

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ltm_users (user_id, created_at, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET updated_at = EXCLUDED.updated_at
                    """,
                    (user_id, created_at, updated_at),
                )

                cur.execute(
                    """
                    INSERT INTO ltm_statistics (
                        user_id, total_trips, total_messages, total_queries, frequent_destinations, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        total_trips = EXCLUDED.total_trips,
                        total_messages = EXCLUDED.total_messages,
                        total_queries = EXCLUDED.total_queries,
                        frequent_destinations = EXCLUDED.frequent_destinations,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        user_id,
                        int(statistics.get("total_trips", 0) or 0),
                        int(statistics.get("total_messages", 0) or 0),
                        int(statistics.get("total_queries", 0) or 0),
                        self._jsonb(statistics.get("frequent_destinations", {}) or {}),
                        updated_at,
                    ),
                )

                # 快照保存采用“先清理、再重建”的方式，逻辑简单且与 JSON 文件结构一致。
                cur.execute("DELETE FROM ltm_preferences WHERE user_id = %s", (user_id,))
                cur.execute("DELETE FROM ltm_chat_history WHERE user_id = %s", (user_id,))
                cur.execute("DELETE FROM ltm_trip_history WHERE user_id = %s", (user_id,))

                for pref in preferences:
                    if not isinstance(pref, dict):
                        continue
                    cur.execute(
                        """
                        INSERT INTO ltm_preferences (user_id, pref_type, value, created_at, updated_at)
                        VALUES (%s, %s, %s, NOW(), NOW())
                        ON CONFLICT (user_id, pref_type) DO UPDATE SET
                            value = EXCLUDED.value,
                            updated_at = NOW()
                        """,
                        (user_id, pref.get("type", ""), self._jsonb(pref.get("value"))),
                    )

                for msg in chat_history:
                    if not isinstance(msg, dict):
                        continue
                    cur.execute(
                        """
                        INSERT INTO ltm_chat_history (user_id, session_id, role, content, metadata, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            user_id,
                            msg.get("session_id"),
                            msg.get("role", "unknown"),
                            msg.get("content", ""),
                            self._jsonb(msg.get("metadata", {}) or {}),
                            self._to_datetime(msg.get("timestamp"), updated_at),
                        ),
                    )

                for trip in trip_history:
                    if not isinstance(trip, dict):
                        continue
                    details = dict(trip)
                    trip_id = details.get("trip_id") or f"{user_id}_trip_{len(trip_history)}"
                    details["trip_id"] = trip_id
                    details.setdefault("timestamp", updated_at)
                    start_date = trip.get("start_date") or None
                    end_date = trip.get("end_date") or None
                    if isinstance(start_date, str) and start_date:
                        try:
                            start_date = date.fromisoformat(start_date[:10])
                        except ValueError:
                            start_date = None
                    if isinstance(end_date, str) and end_date:
                        try:
                            end_date = date.fromisoformat(end_date[:10])
                        except ValueError:
                            end_date = None
                    cur.execute(
                        """
                        INSERT INTO ltm_trip_history (
                            user_id, trip_id, origin, destination, start_date, end_date, purpose, details, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (trip_id) DO UPDATE SET
                            origin = EXCLUDED.origin,
                            destination = EXCLUDED.destination,
                            start_date = EXCLUDED.start_date,
                            end_date = EXCLUDED.end_date,
                            purpose = EXCLUDED.purpose,
                            details = EXCLUDED.details,
                            created_at = EXCLUDED.created_at
                        """,
                        (
                            user_id,
                            trip_id,
                            trip.get("origin"),
                            trip.get("destination"),
                            start_date,
                            end_date,
                            trip.get("purpose"),
                            self._jsonb(json.loads(json.dumps(details, ensure_ascii=False))),
                            self._to_datetime(trip.get("timestamp"), updated_at),
                        ),
                    )

        logger.debug("Saved PostgreSQL snapshot for user %s", user_id)

    def delete_user(self, user_id: str):
        """删除用户所有 PostgreSQL 数据。"""
        if not self.available:
            return

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ltm_users WHERE user_id = %s", (user_id,))
