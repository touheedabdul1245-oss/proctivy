import sqlite3
import os
import uuid


# ============================================================
# PROCTIFY - SESSION MANAGER
# ============================================================

DATABASE_PATH = os.path.join(
    os.path.dirname(__file__),
    "proctify.db"
)


def create_session(
    student_id,
    exam_id=None
):

    session_id = (
        "SESSION_" +
        uuid.uuid4().hex[:12].upper()
    )


    connection = sqlite3.connect(
        DATABASE_PATH
    )

    cursor = connection.cursor()


    cursor.execute(
        """
        INSERT INTO exam_sessions
        (
            session_id,
            student_id,
            exam_id,
            trust_score,
            status
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            session_id,
            student_id,
            exam_id,
            100,
            "ACTIVE"
        )
    )


    connection.commit()

    connection.close()


    return session_id


def update_trust_score(
    session_id,
    trust_score
):

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    cursor = connection.cursor()


    cursor.execute(
        """
        UPDATE exam_sessions
        SET trust_score = ?
        WHERE session_id = ?
        """,
        (
            trust_score,
            session_id
        )
    )


    connection.commit()

    connection.close()


def end_session(
    session_id
):

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    cursor = connection.cursor()


    cursor.execute(
        """
        UPDATE exam_sessions
        SET
            end_time = CURRENT_TIMESTAMP,
            status = 'COMPLETED'
        WHERE session_id = ?
        """,
        (session_id,)
    )


    connection.commit()

    connection.close()
