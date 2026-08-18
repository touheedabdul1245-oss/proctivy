import sqlite3
import os

# ============================================================
# PROCTIFY - DATABASE SETUP
# ============================================================

DATABASE_PATH = os.path.join(
    os.path.dirname(__file__),
    "proctify.db"
)


def get_connection():
    return sqlite3.connect(DATABASE_PATH)


def create_tables():

    connection = get_connection()
    cursor = connection.cursor()

    # --------------------------------------------------------
    # STUDENTS
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


    # --------------------------------------------------------
    # TEACHERS
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


    # --------------------------------------------------------
    # EXAMS
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_name TEXT NOT NULL,
            subject TEXT,
            teacher_id TEXT,
            start_time TEXT,
            end_time TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


    # --------------------------------------------------------
    # EXAM SESSIONS
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            student_id TEXT NOT NULL,
            exam_id INTEGER,
            start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_time TIMESTAMP,
            trust_score INTEGER DEFAULT 100,
            status TEXT DEFAULT 'ACTIVE'
        )
    """)


    # --------------------------------------------------------
    # VIOLATIONS
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            violation_type TEXT NOT NULL,
            severity TEXT,
            penalty INTEGER DEFAULT 0,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        )
    """)


    # --------------------------------------------------------
    # EVIDENCE
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            violation_id INTEGER,
            session_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


    # --------------------------------------------------------
    # TRUST SCORE HISTORY
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trust_score_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            old_score INTEGER,
            new_score INTEGER,
            reason TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


    connection.commit()
    connection.close()


if __name__ == "__main__":

    create_tables()

    print()
    print("========================================")
    print("PROCTIFY DATABASE")
    print("========================================")
    print("Database created successfully.")
    print(f"Location: {DATABASE_PATH}")
    print()
    