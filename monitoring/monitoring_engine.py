import cv2
import uuid
import sqlite3
import os
import json
from datetime import datetime


# ============================================================
# PROCTIFY - MONITORING ENGINE
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)


# ============================================================
# DIRECTORIES
# ============================================================

DATABASE_DIR = os.path.join(
    BASE_DIR,
    "database"
)

REPORTS_DIR = os.path.join(
    BASE_DIR,
    "reports"
)

EVIDENCE_DIR = os.path.join(
    REPORTS_DIR,
    "evidence"
)


os.makedirs(
    DATABASE_DIR,
    exist_ok=True
)

os.makedirs(
    REPORTS_DIR,
    exist_ok=True
)

os.makedirs(
    EVIDENCE_DIR,
    exist_ok=True
)


# ============================================================
# DATABASE
# ============================================================

DATABASE_PATH = os.path.join(
    DATABASE_DIR,
    "proctify.db"
)


# ============================================================
# VIOLATION PENALTIES
# ============================================================

PENALTIES = {
    "PHONE_DETECTED": 10,
    "MULTIPLE_PERSON": 10,
    "LOOKING_LEFT": 5,
    "LOOKING_RIGHT": 5,
    "LOOKING_DOWN": 5,
    "EYES_CLOSED": 5,
    "NO_FACE": 10,
    "AUDIO_VIOLATION": 10,
    "TAB_SWITCH": 10
}


# ============================================================
# MONITORING ENGINE
# ============================================================

class MonitoringEngine:

    def __init__(self, session_id):

        self.session_id = session_id

        self.trust_score = 100

        self.cheating_score = 0

        self.start_time = datetime.now()

        self.violation_count = 0


        # ----------------------------------------------------
        # Make sure database tables exist
        # ----------------------------------------------------

        self.initialize_database()


    # ========================================================
    # INITIALIZE DATABASE
    # ========================================================

    def initialize_database(self):

        connection = sqlite3.connect(
            DATABASE_PATH
        )

        cursor = connection.cursor()


        # ----------------------------------------------------
        # Violations table
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS violations
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                violation_type TEXT,
                severity TEXT,
                penalty INTEGER,
                description TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


        # ----------------------------------------------------
        # Trust score history
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trust_score_history
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                old_score REAL,
                new_score REAL,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


        # ----------------------------------------------------
        # Evidence table
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                violation_id INTEGER,
                session_id TEXT,
                file_path TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


        connection.commit()

        connection.close()


    # ========================================================
    # RECORD VIOLATION
    # ========================================================

    def record_violation(
        self,
        violation_type,
        severity="MEDIUM",
        description=""
    ):

        # ----------------------------------------------------
        # Get penalty
        # ----------------------------------------------------

        penalty = PENALTIES.get(
            violation_type,
            0
        )


        # ----------------------------------------------------
        # Old score
        # ----------------------------------------------------

        old_score = self.trust_score


        # ----------------------------------------------------
        # Apply penalty
        # ----------------------------------------------------

        self.cheating_score += penalty

        self.trust_score = max(
            0,
            100 - self.cheating_score
        )


        self.violation_count += 1


        # ----------------------------------------------------
        # Database connection
        # ----------------------------------------------------

        connection = sqlite3.connect(
            DATABASE_PATH
        )

        cursor = connection.cursor()


        # ----------------------------------------------------
        # Store violation
        # ----------------------------------------------------

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
                self.session_id,
                violation_type,
                severity,
                penalty,
                description
            )
        )


        # ----------------------------------------------------
        # Store score history
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO trust_score_history
            (
                session_id,
                old_score,
                new_score,
                reason
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                self.session_id,
                old_score,
                self.trust_score,
                violation_type
            )
        )


        connection.commit()

        connection.close()


        # ----------------------------------------------------
        # Console output
        # ----------------------------------------------------

        print()
        print("========================================")
        print("PROCTIFY VIOLATION")
        print("========================================")
        print(f"Type       : {violation_type}")
        print(f"Penalty    : -{penalty}")
        print(f"Trust Score: {self.trust_score}")
        print(f"Time       : {datetime.now()}")
        print("========================================")


    # ========================================================
    # SAVE EVIDENCE
    # ========================================================

    def save_evidence(
        self,
        frame,
        violation_type
    ):

        evidence_dir = EVIDENCE_DIR

        os.makedirs(
            evidence_dir,
            exist_ok=True
        )


        # ----------------------------------------------------
        # Create unique filename
        # ----------------------------------------------------

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        unique_id = uuid.uuid4().hex[:8]

        filename = (
            f"{violation_type}_"
            f"{timestamp}_"
            f"{unique_id}.jpg"
        )


        file_path = os.path.join(
            evidence_dir,
            filename
        )


        # ----------------------------------------------------
        # Save webcam frame
        # ----------------------------------------------------

        success = cv2.imwrite(
            file_path,
            frame
        )


        if not success:

            print(
                "ERROR: Could not save evidence image."
            )

            return None


        # ----------------------------------------------------
        # Store evidence information in SQL
        # ----------------------------------------------------

        connection = sqlite3.connect(
            DATABASE_PATH
        )

        cursor = connection.cursor()


        # ----------------------------------------------------
        # Find most recent violation
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT id
            FROM violations
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                self.session_id,
            )
        )


        result = cursor.fetchone()


        violation_id = (
            result[0]
            if result
            else None
        )


        # ----------------------------------------------------
        # Store evidence
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO evidence
            (
                violation_id,
                session_id,
                file_path
            )
            VALUES (?, ?, ?)
            """,
            (
                violation_id,
                self.session_id,
                file_path
            )
        )


        connection.commit()

        connection.close()


        # ----------------------------------------------------
        # Console output
        # ----------------------------------------------------

        print()
        print("Evidence saved:")
        print(file_path)


        return file_path


    # ========================================================
    # GET TRUST SCORE
    # ========================================================

    def get_trust_score(self):

        return self.trust_score


    # ========================================================
    # GET RISK LEVEL
    # ========================================================

    def get_risk_level(self):

        if self.trust_score >= 80:

            return "LOW"

        elif self.trust_score >= 50:

            return "MEDIUM"

        else:

            return "HIGH"


    # ========================================================
    # GET SESSION VIOLATIONS
    # ========================================================

    def get_session_violations(self):

        connection = sqlite3.connect(
            DATABASE_PATH
        )

        cursor = connection.cursor()


        cursor.execute(
            """
            SELECT
                id,
                violation_type,
                severity,
                penalty,
                description,
                timestamp
            FROM violations
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (
                self.session_id,
            )
        )


        rows = cursor.fetchall()


        connection.close()


        violations = []


        for row in rows:

            violations.append(
                {
                    "id": row[0],
                    "type": row[1],
                    "severity": row[2],
                    "penalty": row[3],
                    "description": row[4],
                    "timestamp": row[5]
                }
            )


        return violations


    # ========================================================
    # GET SESSION EVIDENCE
    # ========================================================

    def get_session_evidence(self):

        connection = sqlite3.connect(
            DATABASE_PATH
        )

        cursor = connection.cursor()


        cursor.execute(
            """
            SELECT
                id,
                violation_id,
                file_path,
                timestamp
            FROM evidence
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (
                self.session_id,
            )
        )


        rows = cursor.fetchall()


        connection.close()


        evidence = []


        for row in rows:

            evidence.append(
                {
                    "id": row[0],
                    "violation_id": row[1],
                    "file_path": row[2],
                    "timestamp": row[3]
                }
            )


        return evidence


    # ========================================================
    # GENERATE SESSION REPORT
    # ========================================================

    def generate_report(self):

        end_time = datetime.now()


        # ----------------------------------------------------
        # Get database information
        # ----------------------------------------------------

        violations = self.get_session_violations()

        evidence = self.get_session_evidence()


        # ----------------------------------------------------
        # Create report filename
        # ----------------------------------------------------

        safe_session_id = (
            str(self.session_id)
            .replace(
                " ",
                "_"
            )
        )


        report_filename = (
            f"session_report_"
            f"{safe_session_id}.json"
        )


        report_path = os.path.join(
            REPORTS_DIR,
            report_filename
        )


        # ----------------------------------------------------
        # Create report data
        # ----------------------------------------------------

        report_data = {

            "system":
                "PROCTIFY",

            "session_id":
                self.session_id,

            "start_time":
                self.start_time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),

            "end_time":
                end_time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),

            "duration_seconds":
                round(
                    (
                        end_time -
                        self.start_time
                    ).total_seconds(),
                    2
                ),

            "total_violations":
                len(violations),

            "total_evidence":
                len(evidence),

            "cheating_score":
                self.cheating_score,

            "final_trust_score":
                self.trust_score,

            "final_risk_level":
                self.get_risk_level(),

            "violations":
                violations,

            "evidence":
                evidence
        }


        # ----------------------------------------------------
        # Write report
        # ----------------------------------------------------

        try:

            with open(
                report_path,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    report_data,
                    file,
                    indent=4
                )


            print()
            print("========================================")
            print("PROCTIFY SESSION REPORT GENERATED")
            print("========================================")
            print(
                f"Report: {report_path}"
            )
            print(
                f"Violations: {len(violations)}"
            )
            print(
                f"Evidence: {len(evidence)}"
            )
            print(
                f"Trust Score: {self.trust_score}"
            )
            print(
                f"Risk Level: {self.get_risk_level()}"
            )
            print("========================================")
            print()


            return report_path


        except Exception as error:

            print(
                "Report generation error:",
                error
            )

            return None
