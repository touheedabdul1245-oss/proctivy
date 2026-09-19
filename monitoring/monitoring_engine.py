import cv2
import uuid
import os
import json
import mysql.connector
import requests

from datetime import datetime


# ============================================================
# PROCTIFY - MONITORING ENGINE
# MYSQL VERSION
# ============================================================


# ============================================================
# BASE DIRECTORY
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


# ============================================================
# DIRECTORIES
# ============================================================

REPORTS_DIR = os.path.join(
    BASE_DIR,
    "reports"
)


EVIDENCE_DIR = os.path.join(
    REPORTS_DIR,
    "evidence"
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
# MYSQL CONFIGURATION
# ============================================================

DB_CONFIG = {

    "host": "localhost",

    "user": "root",

    "password": "aasmaan@14",

    "database": "proctify_db"

}


# ============================================================
# CENTRAL PROCTIFY SERVER
# ============================================================
# In the final architecture the Student PC never connects directly
# to MySQL. The monitoring engine sends monitoring data to the central
# PROCTIFY server, and the server writes to MySQL.
# Default to the central PROCTIFY server on this machine. Point it at
# a tunnel/remote host for multi-PC exams. Setting this to an empty
# string forces the legacy direct-to-MySQL mode.
PROCTIFY_SERVER_URL = os.getenv(
    "PROCTIFY_SERVER_URL",
    "http://127.0.0.1:5000"
).strip().rstrip("/")

PROCTIFY_API_TIMEOUT = float(os.getenv(
    "PROCTIFY_API_TIMEOUT",
    "5"
))

CLOUD_MODE = bool(PROCTIFY_SERVER_URL)


# ============================================================
# VIOLATION PENALTIES
# ============================================================

PENALTIES = {

    "PHONE_DETECTED": 10,

    "EARPHONE_DETECTED": 10,

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
# DATABASE CONNECTION
# ============================================================

def get_database_connection():

    # --------------------------------------------------------
    # CLOUD MODE
    # --------------------------------------------------------
    # Never open a MySQL connection from the Student PC when a
    # central server URL is configured.
    if CLOUD_MODE:
        return None

    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        return connection

    except mysql.connector.Error as error:
        print()
        print("========================================")
        print("MYSQL CONNECTION ERROR")
        print("========================================")
        print(error)
        print("========================================")
        print()
        return None


# ============================================================
# MONITORING ENGINE
# ============================================================

class MonitoringEngine:


    # ========================================================
    # INITIALIZE
    # ========================================================

    def __init__(
        self,
        session_id,
        student_id="UNKNOWN",
        exam_name="PROCTIFY EXAM"
    ):

        self.session_id = str(
            session_id
        )

        # Session identity used for MySQL-based completed reports.
        self.student_id = str(
            student_id
        )

        self.exam_name = str(
            exam_name
        )


        self.trust_score = 100

        self.cheating_score = 0

        self.start_time = datetime.now()

        self.violation_count = 0

        # Set to True the moment the Trust Score reaches 0. The live
        # monitor loop watches this flag and shuts itself down, which
        # stops the student's camera and the teacher's live feed.
        self.terminated = False

        self.termination_reason = None

        # Exact violation associated with the next evidence image.
        # This prevents evidence from being linked to another student's
        # or another concurrent violation when multiple students are live.
        self.last_violation_id = None


        print()

        print(
            "========================================"
        )

        print(
            "PROCTIFY MONITORING ENGINE"
        )

        print(
            "========================================"
        )

        print(
            f"Session ID: {self.session_id}"
        )

        print(
            "Initial Trust Score: 100"
        )

        print(
            "Monitoring Engine Ready"
        )

        print(
            "========================================"
        )

        print()


    # ========================================================
    # RECORD VIOLATION
    # ========================================================

    def record_violation(
        self,
        violation_type,
        severity="MEDIUM",
        description=""
    ):

        penalty = PENALTIES.get(violation_type, 0)

        old_score = self.trust_score

        self.cheating_score += penalty
        self.trust_score = max(0, 100 - self.cheating_score)
        self.violation_count += 1

        payload = {
            "session_id": self.session_id,
            "student_id": self.student_id,
            "exam_name": self.exam_name,
            "violation_type": str(violation_type),
            "severity": str(severity),
            "penalty": int(penalty),
            "description": str(description),
            "old_score": int(old_score),
            "new_score": int(self.trust_score)
        }

        if CLOUD_MODE:
            try:
                response = requests.post(
                    f"{PROCTIFY_SERVER_URL}/api/monitor/violation",
                    json=payload,
                    timeout=PROCTIFY_API_TIMEOUT
                )
                response.raise_for_status()
                data = response.json()

                violation_id = data.get("violation_id")
                self.last_violation_id = violation_id

                print()
                print("========================================")
                print("PROCTIFY VIOLATION")
                print("========================================")
                print(f"Type       : {violation_type}")
                print(f"Severity   : {severity}")
                print(f"Penalty    : -{penalty}")
                print(f"Trust Score: {self.trust_score}")
                print(f"Stored via central server: YES")
                print("========================================")
                print()

                self._maybe_terminate()

                return violation_id

            except Exception as error:
                print()
                print("CENTRAL SERVER VIOLATION ERROR:")
                print(error)
                print()
                # Keep the in-memory score so monitoring can continue.
                return None

        connection = get_database_connection()

        if connection is None:
            print("Violation could not be stored in MySQL.")
            return None

        cursor = None

        try:
            cursor = connection.cursor()

            cursor.execute("""
                INSERT INTO violations
                (
                    session_id,
                    violation_type,
                    severity,
                    penalty,
                    description
                )
                VALUES (%s, %s, %s, %s, %s)
            """, (
                self.session_id,
                violation_type,
                severity,
                penalty,
                description
            ))

            violation_id = cursor.lastrowid
            self.last_violation_id = violation_id

            cursor.execute("""
                INSERT INTO trust_score_history
                (
                    session_id,
                    old_score,
                    new_score,
                    reason
                )
                VALUES (%s, %s, %s, %s)
            """, (
                self.session_id,
                old_score,
                self.trust_score,
                violation_type
            ))

            connection.commit()

            self._maybe_terminate()

            return violation_id

        except mysql.connector.Error as error:
            print("MYSQL VIOLATION ERROR:", error)
            connection.rollback()
            return None

        finally:
            if cursor is not None:
                cursor.close()
            connection.close()


    # ========================================================
    # SAVE EVIDENCE
    # ========================================================

    def save_evidence(
        self,
        frame,
        violation_type
    ):

        if CLOUD_MODE:
            try:
                success, encoded = cv2.imencode(".jpg", frame)

                if not success:
                    print("ERROR: Could not encode evidence image.")
                    return None

                files = {
                    "evidence": (
                        f"{violation_type}.jpg",
                        encoded.tobytes(),
                        "image/jpeg"
                    )
                }

                data = {
                    "session_id": self.session_id,
                    "student_id": self.student_id,
                    "exam_name": self.exam_name,
                    "violation_type": str(violation_type),
                    "violation_id": (
                        str(self.last_violation_id)
                        if self.last_violation_id is not None
                        else ""
                    )
                }

                response = requests.post(
                    f"{PROCTIFY_SERVER_URL}/api/monitor/evidence",
                    data=data,
                    files=files,
                    timeout=PROCTIFY_API_TIMEOUT
                )
                response.raise_for_status()

                result = response.json()
                file_path = result.get("file_path")

                print("Evidence uploaded through central server.")
                return file_path

            except Exception as error:
                print("CENTRAL SERVER EVIDENCE ERROR:", error)
                return None

        os.makedirs(EVIDENCE_DIR, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = uuid.uuid4().hex[:8]

        filename = (
            f"{violation_type}_{timestamp}_{unique_id}.jpg"
        )

        file_path = os.path.join(EVIDENCE_DIR, filename)

        success = cv2.imwrite(file_path, frame)

        if not success:
            print("ERROR: Could not save evidence image.")
            return None

        connection = get_database_connection()

        if connection is None:
            return file_path

        cursor = None

        try:
            cursor = connection.cursor()

            violation_id = self.last_violation_id

            # Compatibility fallback for legacy/local mode if evidence is
            # saved before record_violation returned an ID.
            if violation_id is None:
                cursor.execute("""
                    SELECT id
                    FROM violations
                    WHERE session_id = %s
                    ORDER BY id DESC
                    LIMIT 1
                """, (self.session_id,))

                result = cursor.fetchone()
                violation_id = result[0] if result else None

            cursor.execute("""
                INSERT INTO evidence
                (
                    violation_id,
                    session_id,
                    file_path
                )
                VALUES (%s, %s, %s)
            """, (
                violation_id,
                self.session_id,
                file_path
            ))

            connection.commit()
            return file_path

        except mysql.connector.Error as error:
            print("MYSQL EVIDENCE ERROR:", error)
            connection.rollback()
            return file_path

        finally:
            if cursor is not None:
                cursor.close()
            connection.close()


     # ========================================================
    # UPDATE LIVE STUDENT IN MYSQL
    # ========================================================

    # ========================================================
    # UPDATE LIVE STUDENT IN MYSQL
    # ========================================================

    def update_live_student(
        self,
        student_id,
        exam_name,
        status,
        phone,
        phone_count,
        person_count,
        face_count,
        hand_count,
        gaze,
        head_direction,
        audio,
        audio_volume,
        camera_available=False,
        audio_available=False,
        ai_available=False,
        tab_available=True,
        trust_score=None,
        risk_level=None
    ):

        if trust_score is None:
            trust_score = self.trust_score

        if risk_level is None:
            risk_level = self.get_risk_level()

        payload = {
            "student_id": str(student_id),
            "session_id": str(self.session_id),
            "exam_name": str(exam_name),
            "status": str(status),
            "trust_score": int(trust_score),
            "risk_level": str(risk_level),
            "phone": bool(phone),
            "phone_count": int(phone_count),
            "person_count": int(person_count),
            "face_count": int(face_count),
            "hand_count": int(hand_count),
            "gaze": str(gaze),
            "head_direction": str(head_direction),
            "audio": str(audio),
            "audio_volume": float(audio_volume),
            "camera_available": bool(camera_available),
            "audio_available": bool(audio_available),
            "ai_available": bool(ai_available),
            "tab_available": bool(tab_available)
        }

        if CLOUD_MODE:
            try:
                response = requests.post(
                    f"{PROCTIFY_SERVER_URL}/api/monitor/live-status",
                    json=payload,
                    timeout=PROCTIFY_API_TIMEOUT
                )
                response.raise_for_status()
                return bool(response.json().get("success", True))

            except Exception as error:
                print("CENTRAL SERVER LIVE STATUS ERROR:", error)
                return False

        connection = get_database_connection()

        if connection is None:
            print(
                "Live student update failed: "
                "MySQL connection unavailable."
            )
            return False

        cursor = None

        try:
            cursor = connection.cursor()

            query = """
                INSERT INTO live_students
                (
                    student_id,
                    session_id,
                    exam_name,
                    status,
                    trust_score,
                    risk_level,
                    phone,
                    phone_count,
                    person_count,
                    face_count,
                    hand_count,
                    gaze,
                    head_direction,
                    audio,
                    audio_volume,
                    camera_available,
                    audio_available,
                    ai_available,
                    tab_available
                )
                VALUES
                (
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    session_id = VALUES(session_id),
                    exam_name = VALUES(exam_name),
                    status = VALUES(status),
                    trust_score = VALUES(trust_score),
                    risk_level = VALUES(risk_level),
                    phone = VALUES(phone),
                    phone_count = VALUES(phone_count),
                    person_count = VALUES(person_count),
                    face_count = VALUES(face_count),
                    hand_count = VALUES(hand_count),
                    gaze = VALUES(gaze),
                    head_direction = VALUES(head_direction),
                    audio = VALUES(audio),
                    audio_volume = VALUES(audio_volume),
                    camera_available = VALUES(camera_available),
                    audio_available = VALUES(audio_available),
                    ai_available = VALUES(ai_available),
                    tab_available = VALUES(tab_available)
            """

            values = (
                str(student_id),
                str(self.session_id),
                str(exam_name),
                str(status),
                int(trust_score),
                str(risk_level),
                int(bool(phone)),
                int(phone_count),
                int(person_count),
                int(face_count),
                int(hand_count),
                str(gaze),
                str(head_direction),
                str(audio),
                float(audio_volume),
                int(bool(camera_available)),
                int(bool(audio_available)),
                int(bool(ai_available)),
                int(bool(tab_available))
            )

            cursor.execute(query, values)
            connection.commit()
            return True

        except mysql.connector.Error as error:
            print("MYSQL LIVE STUDENT UPDATE ERROR:", error)
            connection.rollback()
            return False

        finally:
            if cursor is not None:
                cursor.close()
            connection.close()


    # ========================================================
    # AUTOMATIC TERMINATION AT TRUST SCORE 0
    # ========================================================

    def is_terminated(self):

        return bool(self.terminated)


    def _maybe_terminate(self):
        """
        Once the Trust Score reaches zero the session is over:

          1. the session is persisted as TERMINATED,
          2. the teacher's live feed is stopped by the server,
          3. a report is generated for the teacher.
        """

        if self.terminated:

            return False

        if int(self.trust_score) > 0:

            return False

        self.terminated = True

        self.termination_reason = "TRUST_SCORE_REACHED_ZERO"

        print()
        print("========================================")
        print("PROCTIFY SESSION TERMINATED")
        print("========================================")
        print("Trust Score reached 0 - monitoring stops.")
        print(f"Student: {self.student_id}")
        print(f"Session: {self.session_id}")
        print("========================================")
        print()

        return self.terminate_session()


    def terminate_session(self):
        """
        Tell the central server to terminate the session.

        The server marks the session TERMINATED, drops the buffered
        live frames (so the teacher's feed stops immediately) and
        writes the session report.

        In local (non-cloud) mode the same state is written directly
        to MySQL and the report is written to reports/.
        """

        if CLOUD_MODE:

            try:

                response = requests.post(
                    f"{PROCTIFY_SERVER_URL}/api/monitor/terminate",
                    json={
                        "session_id": self.session_id,
                        "student_id": self.student_id,
                        "exam_name": self.exam_name,
                        "final_trust_score": int(self.trust_score),
                        "final_risk_level": self.get_risk_level()
                    },
                    timeout=PROCTIFY_API_TIMEOUT
                )

                response.raise_for_status()

                data = response.json()

                print(
                    "Central termination accepted. Report:",
                    data.get("report_path")
                )

                return True

            except Exception as error:

                print("CENTRAL TERMINATION ERROR:", error)

                return False

        # ----------------------------------------------------
        # LOCAL MODE
        # ----------------------------------------------------

        self.save_completed_exam_session(
            datetime.now(),
            status="TERMINATED"
        )

        self.generate_report(terminated=True)

        return True


    # ========================================================
    # GET TRUST SCORE
    # ========================================================

    def get_trust_score(self):

        return int(

            self.trust_score

        )


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

        if CLOUD_MODE:
            try:
                response = requests.get(
                    f"{PROCTIFY_SERVER_URL}/api/monitor/violations",
                    params={"session_id": self.session_id},
                    timeout=PROCTIFY_API_TIMEOUT
                )
                response.raise_for_status()
                return response.json().get("violations", [])

            except Exception as error:
                print("CENTRAL SERVER VIOLATION READ ERROR:", error)
                return []

        connection = get_database_connection()

        if connection is None:
            return []

        cursor = None

        try:
            cursor = connection.cursor()

            cursor.execute("""
                SELECT
                    id,
                    violation_type,
                    severity,
                    penalty,
                    description,
                    timestamp
                FROM violations
                WHERE session_id = %s
                ORDER BY id ASC
            """, (self.session_id,))

            rows = cursor.fetchall()

            return [
                {
                    "id": row[0],
                    "type": row[1],
                    "severity": row[2],
                    "penalty": row[3],
                    "description": row[4],
                    "timestamp": str(row[5])
                }
                for row in rows
            ]

        except mysql.connector.Error as error:
            print("MYSQL READ ERROR:", error)
            return []

        finally:
            if cursor is not None:
                cursor.close()
            connection.close()


    # ========================================================
    # GET SESSION EVIDENCE
    # ========================================================

    def get_session_evidence(self):

        if CLOUD_MODE:
            try:
                response = requests.get(
                    f"{PROCTIFY_SERVER_URL}/api/monitor/evidence",
                    params={"session_id": self.session_id},
                    timeout=PROCTIFY_API_TIMEOUT
                )
                response.raise_for_status()
                return response.json().get("evidence", [])

            except Exception as error:
                print("CENTRAL SERVER EVIDENCE READ ERROR:", error)
                return []

        connection = get_database_connection()

        if connection is None:
            return []

        cursor = None

        try:
            cursor = connection.cursor()

            cursor.execute("""
                SELECT
                    id,
                    violation_id,
                    file_path,
                    timestamp
                FROM evidence
                WHERE session_id = %s
                ORDER BY id ASC
            """, (self.session_id,))

            rows = cursor.fetchall()

            return [
                {
                    "id": row[0],
                    "violation_id": row[1],
                    "file_path": row[2],
                    "timestamp": str(row[3])
                }
                for row in rows
            ]

        except mysql.connector.Error as error:
            print("MYSQL EVIDENCE READ ERROR:", error)
            return []

        finally:
            if cursor is not None:
                cursor.close()
            connection.close()


    # ========================================================
    # SAVE COMPLETED EXAM SESSION TO MYSQL
    # ========================================================

    def save_completed_exam_session(
        self,
        end_time,
        status="COMPLETED"
    ):

        status = str(status).upper()

        if status not in ("COMPLETED", "TERMINATED"):
            status = "COMPLETED"

        payload = {
            "session_id": self.session_id,
            "student_id": self.student_id,
            "exam_name": self.exam_name,
            "start_time": self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "final_trust_score": int(self.get_trust_score()),
            "final_risk_level": str(self.get_risk_level())
        }

        if CLOUD_MODE:
            try:
                response = requests.post(
                    f"{PROCTIFY_SERVER_URL}/api/monitor/session-complete",
                    json=payload,
                    timeout=PROCTIFY_API_TIMEOUT
                )
                response.raise_for_status()
                return bool(response.json().get("success", True))

            except Exception as error:
                print("CENTRAL SERVER EXAM SESSION ERROR:", error)
                return False

        connection = get_database_connection()

        if connection is None:
            return False

        cursor = None

        try:
            cursor = connection.cursor()

            cursor.execute("""
                INSERT INTO exam_sessions
                (
                    session_id,
                    student_id,
                    exam_name,
                    start_time,
                    end_time,
                    status,
                    final_trust_score,
                    final_risk_level
                )
                VALUES
                (
                    %s, %s, %s, %s, %s,
                    %s,
                    %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    student_id = VALUES(student_id),
                    exam_name = VALUES(exam_name),
                    start_time = VALUES(start_time),
                    end_time = VALUES(end_time),
                    status = VALUES(status),
                    final_trust_score = VALUES(final_trust_score),
                    final_risk_level = VALUES(final_risk_level)
            """, (
                self.session_id,
                self.student_id,
                self.exam_name,
                self.start_time,
                end_time,
                status,
                int(self.get_trust_score()),
                str(self.get_risk_level())
            ))

            connection.commit()
            return True

        except Exception as error:
            print("MYSQL EXAM SESSION SAVE ERROR:", error)
            try:
                connection.rollback()
            except Exception:
                pass
            return False

        finally:
            if cursor is not None:
                cursor.close()
            connection.close()


    # ========================================================
    # GENERATE SESSION REPORT
    # ========================================================

    def generate_report(self, terminated=False):

        end_time = datetime.now()

        report_status = "TERMINATED" if terminated else "COMPLETED"

        if CLOUD_MODE:
            try:
                # Ensure the completed session is persisted in MySQL before
                # the central report is generated.
                self.save_completed_exam_session(end_time, status=report_status)

                response = requests.post(
                    f"{PROCTIFY_SERVER_URL}/api/monitor/report",
                    json={
                        "session_id": self.session_id,
                        "student_id": self.student_id,
                        "exam_name": self.exam_name,
                        "start_time": self.start_time.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "end_time": end_time.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "duration_seconds": round(
                            (end_time - self.start_time).total_seconds(),
                            2
                        ),
                        "cheating_score": int(self.cheating_score),
                        "status": report_status,
                        "terminated": bool(terminated),
                        "termination_reason": self.termination_reason,
                        "final_trust_score": int(self.trust_score),
                        "final_risk_level": self.get_risk_level()
                    },
                    timeout=PROCTIFY_API_TIMEOUT
                )
                response.raise_for_status()

                data = response.json()

                print()
                print("========================================")
                print("PROCTIFY SESSION REPORT GENERATED")
                print("========================================")
                print(f"Central report: {data.get('report_path', '')}")
                print(f"Trust Score: {self.trust_score}")
                print(f"Risk Level: {self.get_risk_level()}")
                print("========================================")
                print()

                return data.get("report_path")

            except Exception as error:
                print("CENTRAL SERVER REPORT ERROR:", error)
                return None

        self.save_completed_exam_session(end_time, status=report_status)

        violations = self.get_session_violations()
        evidence = self.get_session_evidence()

        safe_session_id = str(self.session_id).replace(" ", "_")

        report_filename = f"session_report_{safe_session_id}.json"

        report_path = os.path.join(
            REPORTS_DIR,
            report_filename
        )

        report_data = {
            "system": "PROCTIFY",
            "session_id": self.session_id,
            "student_id": self.student_id,
            "exam_name": self.exam_name,
            "status": report_status,
            "terminated": bool(terminated),
            "termination_reason": self.termination_reason,
            "start_time": self.start_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "end_time": end_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "duration_seconds": round(
                (end_time - self.start_time).total_seconds(),
                2
            ),
            "total_violations": len(violations),
            "total_evidence": len(evidence),
            "cheating_score": self.cheating_score,
            "final_trust_score": self.trust_score,
            "final_risk_level": self.get_risk_level(),
            "violations": violations,
            "evidence": evidence
        }

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

            return report_path

        except Exception as error:
            print("Report generation error:", error)
            return None

