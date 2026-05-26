import os
import sqlite3
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from app.core import config

logger = logging.getLogger("app.repositories.task_repository")

class TaskRepository:
    def __init__(self, db_path: str = config.DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes tables for task persistence and audit trail."""
        logger.info(f"Initializing SQLite database at {self.db_path}")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Tasks table
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
            # Audit trail transitions table
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
            
            # Migration: add workflow_state_json column if it does not exist
            with self._get_connection() as conn:
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN workflow_state_json TEXT")
                    logger.info("Database migration: Added workflow_state_json column to tasks table")
                except sqlite3.OperationalError:
                    # Column already exists
                    pass


    def create_task(self, task_id: str, status: str, user_prompt: str) -> None:
        """Saves a new task in SCHEDULED state."""
        now = datetime.utcnow().isoformat() + "Z"
        logger.info(f"DB: Creating task {task_id} with prompt: '{user_prompt[:50]}'")
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, status, user_prompt, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, status, user_prompt, now, now)
            )
            conn.commit()

    def update_task_status(self, task_id: str, old_status: str, new_status: str) -> None:
        """Updates status of a task and writes to the transitions audit trail."""
        now = datetime.utcnow().isoformat() + "Z"
        logger.info(f"DB: Transitioning task {task_id} state from {old_status} -> {new_status}")
        with self._get_connection() as conn:
            # Update task status
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (new_status, now, task_id)
            )
            # Log transition to audit table
            conn.execute(
                "INSERT INTO task_transitions (task_id, old_state, new_state, transitioned_at) VALUES (?, ?, ?, ?)",
                (task_id, old_status, new_status, now)
            )
            conn.commit()

    def update_task_approval(self, task_id: str, approval_state: str, feedback: Optional[str] = None) -> None:
        """Saves HIL approval action and user feedback."""
        now = datetime.utcnow().isoformat() + "Z"
        logger.info(f"DB: Logging approval action {approval_state} for task {task_id}")
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tasks SET approval_state = ?, rejection_feedback = ?, updated_at = ? WHERE task_id = ?",
                (approval_state, feedback, now, task_id)
            )
            conn.commit()

    def update_task_final_output(self, task_id: str, final_output: str) -> None:
        """Stores final output execution result or outreach draft."""
        now = datetime.utcnow().isoformat() + "Z"
        logger.info(f"DB: Saving final output for task {task_id}")
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tasks SET final_output = ?, updated_at = ? WHERE task_id = ?",
                (final_output, now, task_id)
            )
            conn.commit()

    def update_task_memory(self, task_id: str, memory_data: Dict[str, Any]) -> None:
        """Persists workflow context/history variables for long-term memory."""
        now = datetime.utcnow().isoformat() + "Z"
        memory_json = json.dumps(memory_data)
        logger.info(f"DB: Storing memory for task {task_id}")
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tasks SET memory = ?, updated_at = ? WHERE task_id = ?",
                (memory_json, now, task_id)
            )
            conn.commit()

    def get_recent_successful_tasks(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieves recent successful tasks, helping populate agent preferences/history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_id, user_prompt, final_output, memory FROM tasks WHERE status = 'SUCCESS' ORDER BY updated_at DESC LIMIT ?",
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

    def update_task_workflow_state(self, task_id: str, state_json: str) -> None:
        """Persists the complete workflow state JSON to the database."""
        now = datetime.utcnow().isoformat() + "Z"
        logger.info(f"DB: Saving workflow state JSON for task {task_id}")
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tasks SET workflow_state_json = ?, updated_at = ? WHERE task_id = ?",
                (state_json, now, task_id)
            )
            conn.commit()

    def get_task_workflow_state(self, task_id: str) -> Optional[str]:
        """Retrieves the persisted workflow state JSON for a task."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT workflow_state_json FROM tasks WHERE task_id = ?", (task_id,))
            row = cursor.fetchone()
            if row:
                return row["workflow_state_json"]
        return None

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Fetches the complete database record for a single task."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

# Singleton instance
task_repo = TaskRepository()
