import sqlite3
import os


# ============================================================
# PROCTIFY - VIOLATION LOGGER
# ============================================================

DATABASE_PATH = os.path.join(
    os.path.dirname(__file__),
    "proctify.db"
)


def log_violation(
    session_id,
    violation_type,
    severity="MEDIUM",
    penalty=0,
    description=""
):

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO violations
        (
            session_id,
            violation_type,
            severity,
            penalty,
            description
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            session_id,
            violation_type,
            severity,
            penalty,
            description
        )
    )

    connection.commit()

    connection.close()


def get_violations(session_id):

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            violation_type,
            severity,
            penalty,
            timestamp,
            description
        FROM violations
        WHERE session_id = ?
        ORDER BY timestamp ASC
        """,
        (session_id,)
    )

    violations = cursor.fetchall()

    connection.close()

    return violations
