import sqlite3
import json
import logging
from typing import Optional, List, Dict, Any

from app.core import config
from app.models.workflow_models import WorkflowSession, ExecutionPlan, RuntimeWorkflowState
from app.utils.time import utc_now_iso

logger = logging.getLogger("app.storage.workflow_repository")

class WorkflowRepository:
    def __init__(self, db_path: str = config.DB_PATH):
        self.db_path = db_path
        self._init_db_and_migrations()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db_and_migrations(self):
        """
        Initializes core tables and runs incremental schema migrations to add
        agentic runtime columns without breaking existing fields.
        """
        logger.info(f"Initializing WorkflowRepository SQLite database at {self.db_path}")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. Base tasks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    user_prompt TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approval_state TEXT,
                    rejection_feedback TEXT,
                    final_output TEXT,
                    memory TEXT
                )
            """)
            # 2. Transition log table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    old_state TEXT NOT NULL,
                    new_state TEXT NOT NULL,
                    transitioned_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks (task_id) ON DELETE CASCADE
                )
            """)
            conn.commit()

        # 3. Dynamic Column Migrations (Try-Catch safe addition)
        migrations = [
            ("workflow_state_json", "TEXT"),
            ("workflow_version", "INTEGER DEFAULT 1"),
            ("execution_plan", "TEXT"),
            ("tool_outputs", "TEXT"),
            ("websocket_session_metadata", "TEXT"),
            ("event_history", "TEXT"),
            ("retries_count", "INTEGER DEFAULT 0"),
            ("metrics", "TEXT")
        ]
        
        with self._get_connection() as conn:
            for column_name, column_type in migrations:
                try:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {column_name} {column_type}")
                    logger.info(f"Database migration: Added {column_name} column to tasks table")
                except sqlite3.OperationalError:
                    # Column already exists, safe to ignore
                    pass
            conn.commit()

    def save_session(self, session: WorkflowSession) -> None:
        """
        Persists a complete WorkflowSession into the SQLite database.
        Runs updates inside a transaction.
        """
        now = utc_now_iso()
        session.updated_at = now
        
        # Serialize fields
        execution_plan_json = session.execution_plan.model_dump_json()
        tool_outputs_json = session.workflow_state_json  # Map state for tool compatibility
        websocket_session_metadata_json = json.dumps(session.websocket_session_metadata)
        event_history_json = json.dumps(session.event_history)
        metrics_json = json.dumps(session.metrics)
        
        logger.info(f"DB: Saving workflow session {session.task_id} status={session.status.value}")
        
        with self._get_connection() as conn:
            # Check if task exists to determine INSERT vs UPDATE
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM tasks WHERE task_id = ?", (session.task_id,))
            exists = cursor.fetchone() is not None
            
            if exists:
                conn.execute("""
                    UPDATE tasks SET 
                        status = ?, 
                        updated_at = ?,
                        approval_state = ?, 
                        rejection_feedback = ?, 
                        workflow_version = ?,
                        execution_plan = ?,
                        tool_outputs = ?,
                        websocket_session_metadata = ?,
                        event_history = ?,
                        retries_count = ?,
                        metrics = ?,
                        workflow_state_json = ?
                    WHERE task_id = ?
                """, (
                    session.status.value,
                    now,
                    session.approval_state,
                    session.rejection_feedback,
                    session.workflow_version,
                    execution_plan_json,
                    tool_outputs_json,
                    websocket_session_metadata_json,
                    event_history_json,
                    session.retries_count,
                    metrics_json,
                    session.workflow_state_json,
                    session.task_id
                ))
            else:
                conn.execute("""
                    INSERT INTO tasks (
                        task_id, status, user_prompt, created_at, updated_at, 
                        approval_state, rejection_feedback, workflow_version, 
                        execution_plan, tool_outputs, websocket_session_metadata, 
                        event_history, retries_count, metrics, workflow_state_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session.task_id,
                    session.status.value,
                    session.user_prompt,
                    session.created_at,
                    now,
                    session.approval_state,
                    session.rejection_feedback,
                    session.workflow_version,
                    execution_plan_json,
                    tool_outputs_json,
                    websocket_session_metadata_json,
                    event_history_json,
                    session.retries_count,
                    metrics_json,
                    session.workflow_state_json
                ))
            conn.commit()

    def get_session(self, task_id: str) -> Optional[WorkflowSession]:
        """
        Retrieves a WorkflowSession from the SQLite database.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            # Helper to parse JSON fields safely
            def safe_parse_json(val: Optional[str], default: Any) -> Any:
                if not val:
                    return default
                try:
                    return json.loads(val)
                except Exception:
                    return default

            # Parse execution plan
            plan_data = safe_parse_json(row["execution_plan"], {"plan": []})
            try:
                execution_plan = ExecutionPlan.model_validate(plan_data)
            except Exception:
                execution_plan = ExecutionPlan(plan=[])

            # Reconstruct model
            session = WorkflowSession(
                task_id=row["task_id"],
                status=RuntimeWorkflowState(row["status"]) if row["status"] in [e.value for e in RuntimeWorkflowState] else RuntimeWorkflowState.CREATED,
                user_prompt=row["user_prompt"],
                execution_plan=execution_plan,
                workflow_state_json=row["workflow_state_json"] or "{}",
                websocket_session_metadata=safe_parse_json(row["websocket_session_metadata"], {}),
                approval_state=row["approval_state"],
                rejection_feedback=row["rejection_feedback"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                workflow_version=row["workflow_version"] or 1,
                event_history=safe_parse_json(row["event_history"], []),
                retries_count=row["retries_count"] or 0,
                memory_context=None, # Loaded dynamically by planner
                metrics=safe_parse_json(row["metrics"], {})
            )
            return session

    def log_state_transition(self, task_id: str, old_state: str, new_state: str) -> None:
        """
        Adds audit logs in the task_transitions table for database compatibility.
        """
        now = utc_now_iso()
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO task_transitions (task_id, old_state, new_state, transitioned_at) VALUES (?, ?, ?, ?)",
                (task_id, old_state, new_state, now)
            )
            conn.commit()

    def get_recent_successful_tasks(self, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Retrieves recent successful tasks from DB to load context into semantic memory.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_id, user_prompt, final_output, memory FROM tasks WHERE status = 'COMPLETED' OR status = 'SUCCESS' ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            )
            rows = cursor.fetchall()
            results = []
            for r in rows:
                results.append({
                    "task_id": r["task_id"],
                    "user_prompt": r["user_prompt"],
                    "final_output": r["final_output"],
                    "memory": json.loads(r["memory"]) if r["memory"] else {}
                })
            return results

    def update_task_memory(self, task_id: str, memory_data: Dict[str, Any]) -> None:
        """
        Saves user reference preferences into the legacy memory column.
        """
        now = utc_now_iso()
        memory_json = json.dumps(memory_data)
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tasks SET memory = ?, updated_at = ? WHERE task_id = ?",
                (memory_json, now, task_id)
            )
            conn.commit()

    def update_task_final_output(self, task_id: str, final_output: str) -> None:
        """
        Saves final approved output to the standard final_output column.
        """
        now = utc_now_iso()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tasks SET final_output = ?, updated_at = ? WHERE task_id = ?",
                (final_output, now, task_id)
            )
            conn.commit()

# Singleton instance
workflow_repo = WorkflowRepository()
