import cv2
import uuid
import os
import json
import mysql.connector

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
# DATABASE CONNECTION
# ============================================================

def get_database_connection():

    try:

        connection = mysql.connector.connect(

            **DB_CONFIG

        )

        return connection


    except mysql.connector.Error as error:

        print()

        print(
            "========================================"
        )

        print(
            "MYSQL CONNECTION ERROR"
        )

        print(
            "========================================"
        )

        print(
            error
        )

        print(
            "========================================"
        )

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


        # ----------------------------------------------------
        # PENALTY
        # ----------------------------------------------------

        penalty = PENALTIES.get(

            violation_type,

            0

        )


        # ----------------------------------------------------
        # OLD SCORE
        # ----------------------------------------------------

        old_score = self.trust_score


        # ----------------------------------------------------
        # APPLY PENALTY
        # ----------------------------------------------------

        self.cheating_score += penalty


        self.trust_score = max(

            0,

            100 - self.cheating_score

        )


        self.violation_count += 1


        connection = get_database_connection()


        if connection is None:

            print(
                "Violation could not be stored in MySQL."
            )

            return None


        cursor = None


        try:

            cursor = connection.cursor()


            # ------------------------------------------------
            # INSERT VIOLATION
            # ------------------------------------------------

            query = """

                INSERT INTO violations

                (

                    session_id,

                    violation_type,

                    severity,

                    penalty,

                    description

                )

                VALUES

                (

                    %s,

                    %s,

                    %s,

                    %s,

                    %s

                )

            """


            values = (

                self.session_id,

                violation_type,

                severity,

                penalty,

                description

            )


            cursor.execute(

                query,

                values

            )


            violation_id = cursor.lastrowid


            # ------------------------------------------------
            # TRUST SCORE HISTORY
            # ------------------------------------------------

            score_query = """

                INSERT INTO trust_score_history

                (

                    session_id,

                    old_score,

                    new_score,

                    reason

                )

                VALUES

                (

                    %s,

                    %s,

                    %s,

                    %s

                )

            """


            score_values = (

                self.session_id,

                old_score,

                self.trust_score,

                violation_type

            )


            cursor.execute(

                score_query,

                score_values

            )


            connection.commit()


            # ------------------------------------------------
            # CONSOLE OUTPUT
            # ------------------------------------------------

            print()

            print(
                "========================================"
            )

            print(
                "PROCTIFY VIOLATION"
            )

            print(
                "========================================"
            )

            print(
                f"Type       : {violation_type}"
            )

            print(
                f"Severity   : {severity}"
            )

            print(
                f"Penalty    : -{penalty}"
            )

            print(
                f"Trust Score: {self.trust_score}"
            )

            print(
                f"Time       : {datetime.now()}"
            )

            print(
                "Stored in MySQL: YES"
            )

            print(
                "========================================"
            )

            print()


            return violation_id


        except mysql.connector.Error as error:

            print()

            print(
                "MYSQL VIOLATION ERROR:"
            )

            print(
                error
            )

            print()


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


        # ----------------------------------------------------
        # ENSURE EVIDENCE DIRECTORY EXISTS
        # ----------------------------------------------------

        os.makedirs(

            EVIDENCE_DIR,

            exist_ok=True

        )


        # ----------------------------------------------------
        # CREATE FILENAME
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

            EVIDENCE_DIR,

            filename

        )


        # ----------------------------------------------------
        # SAVE IMAGE
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
        # CONNECT MYSQL
        # ----------------------------------------------------

        connection = get_database_connection()


        if connection is None:

            return file_path


        cursor = None


        try:

            cursor = connection.cursor()


            # ------------------------------------------------
            # FIND LATEST VIOLATION
            # ------------------------------------------------

            query = """

                SELECT id

                FROM violations

                WHERE session_id = %s

                ORDER BY id DESC

                LIMIT 1

            """


            cursor.execute(

                query,

                (

                    self.session_id,

                )

            )


            result = cursor.fetchone()


            if result:

                violation_id = result[0]

            else:

                violation_id = None


            # ------------------------------------------------
            # INSERT EVIDENCE
            # ------------------------------------------------

            evidence_query = """

                INSERT INTO evidence

                (

                    violation_id,

                    session_id,

                    file_path

                )

                VALUES

                (

                    %s,

                    %s,

                    %s

                )

            """


            evidence_values = (

                violation_id,

                self.session_id,

                file_path

            )


            cursor.execute(

                evidence_query,

                evidence_values

            )


            connection.commit()


            print()

            print(

                "Evidence saved:"

            )

            print(

                file_path

            )

            print(

                "Evidence stored in MySQL."

            )

            print()


            return file_path


        except mysql.connector.Error as error:

            print(

                "MYSQL EVIDENCE ERROR:",

                error

            )


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


        # ----------------------------------------------------
        # USE CURRENT ENGINE VALUES IF NOT PROVIDED
        # ----------------------------------------------------

        if trust_score is None:

            trust_score = self.trust_score


        if risk_level is None:

            risk_level = self.get_risk_level()


        # ----------------------------------------------------
        # CONNECT TO MYSQL
        # ----------------------------------------------------

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


            # ------------------------------------------------
            # INSERT OR UPDATE LIVE STUDENT
            # ------------------------------------------------

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

                    %s,
                    %s,
                    %s,
                    %s,

                    %s,
                    %s,

                    %s,
                    %s,
                    %s,
                    %s,
                    %s,

                    %s,
                    %s,

                    %s,
                    %s,

                    %s,
                    %s,
                    %s,
                    %s

                )

                ON DUPLICATE KEY UPDATE

                    session_id =
                        VALUES(session_id),

                    exam_name =
                        VALUES(exam_name),

                    status =
                        VALUES(status),

                    trust_score =
                        VALUES(trust_score),

                    risk_level =
                        VALUES(risk_level),

                    phone =
                        VALUES(phone),

                    phone_count =
                        VALUES(phone_count),

                    person_count =
                        VALUES(person_count),

                    face_count =
                        VALUES(face_count),

                    hand_count =
                        VALUES(hand_count),

                    gaze =
                        VALUES(gaze),

                    head_direction =
                        VALUES(head_direction),

                    audio =
                        VALUES(audio),

                    audio_volume =
                        VALUES(audio_volume),

                    camera_available =
                        VALUES(camera_available),

                    audio_available =
                        VALUES(audio_available),

                    ai_available =
                        VALUES(ai_available),

                    tab_available =
                        VALUES(tab_available)

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


            cursor.execute(

                query,

                values

            )


            connection.commit()


            return True


        except mysql.connector.Error as error:

            print()

            print(
                "========================================"
            )

            print(
                "MYSQL LIVE STUDENT UPDATE ERROR"
            )

            print(
                "========================================"
            )

            print(
                error
            )

            print(
                "========================================"
            )

            print()


            connection.rollback()


            return False


        finally:

            if cursor is not None:

                cursor.close()


            connection.close()
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


        connection = get_database_connection()


        if connection is None:

            return []


        cursor = None


        try:

            cursor = connection.cursor()


            query = """

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

            """


            cursor.execute(

                query,

                (

                    self.session_id,

                )

            )


            rows = cursor.fetchall()


            violations = []


            for row in rows:

                violations.append({

                    "id":
                        row[0],

                    "type":
                        row[1],

                    "severity":
                        row[2],

                    "penalty":
                        row[3],

                    "description":
                        row[4],

                    "timestamp":
                        str(row[5])

                })


            return violations


        except mysql.connector.Error as error:

            print(

                "MYSQL READ ERROR:",

                error

            )


            return []


        finally:

            if cursor is not None:

                cursor.close()


            connection.close()


    # ========================================================
    # GET SESSION EVIDENCE
    # ========================================================

    def get_session_evidence(self):


        connection = get_database_connection()


        if connection is None:

            return []


        cursor = None


        try:

            cursor = connection.cursor()


            query = """

                SELECT

                    id,

                    violation_id,

                    file_path,

                    timestamp

                FROM evidence

                WHERE session_id = %s

                ORDER BY id ASC

            """


            cursor.execute(

                query,

                (

                    self.session_id,

                )

            )


            rows = cursor.fetchall()


            evidence = []


            for row in rows:

                evidence.append({

                    "id":
                        row[0],

                    "violation_id":
                        row[1],

                    "file_path":
                        row[2],

                    "timestamp":
                        str(row[3])

                })


            return evidence


        except mysql.connector.Error as error:

            print(

                "MYSQL EVIDENCE READ ERROR:",

                error

            )


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
        end_time
    ):

        connection = get_database_connection()

        if connection is None:

            print(
                "MYSQL EXAM SESSION ERROR: "
                "Database connection unavailable."
            )

            return False


        cursor = None


        try:

            cursor = connection.cursor()


            query = """
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
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'COMPLETED',
                    %s,
                    %s
                )
                ON DUPLICATE KEY UPDATE

                    student_id = VALUES(student_id),

                    exam_name = VALUES(exam_name),

                    start_time = VALUES(start_time),

                    end_time = VALUES(end_time),

                    status = 'COMPLETED',

                    final_trust_score =
                        VALUES(final_trust_score),

                    final_risk_level =
                        VALUES(final_risk_level)
            """


            cursor.execute(

                query,

                (

                    self.session_id,

                    self.student_id,

                    self.exam_name,

                    self.start_time,

                    end_time,

                    int(
                        self.get_trust_score()
                    ),

                    str(
                        self.get_risk_level()
                    )

                )

            )


            connection.commit()


            print()

            print(
                "========================================"
            )

            print(
                "MYSQL EXAM SESSION SAVED"
            )

            print(
                "========================================"
            )

            print(
                f"Student ID: {self.student_id}"
            )

            print(
                f"Exam: {self.exam_name}"
            )

            print(
                f"Session ID: {self.session_id}"
            )

            print(
                f"Trust Score: "
                f"{self.get_trust_score()}"
            )

            print(
                f"Risk Level: "
                f"{self.get_risk_level()}"
            )

            print(
                "========================================"
            )

            print()


            return True


        except Exception as error:

            print(
                "MYSQL EXAM SESSION SAVE ERROR:",
                error
            )


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

    def generate_report(self):


        end_time = datetime.now()


        # ----------------------------------------------------
        # MYSQL IS THE PRIMARY COMPLETED REPORT SOURCE
        # ----------------------------------------------------

        self.save_completed_exam_session(
            end_time
        )


        # ----------------------------------------------------
        # GET DATA FROM MYSQL
        # ----------------------------------------------------

        violations = (

            self.get_session_violations()

        )


        evidence = (

            self.get_session_evidence()

        )


        # ----------------------------------------------------
        # REPORT FILENAME
        # ----------------------------------------------------

        safe_session_id = (

            str(

                self.session_id

            ).replace(

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
        # REPORT DATA
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

                len(

                    violations

                ),


            "total_evidence":

                len(

                    evidence

                ),


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
        # WRITE REPORT
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

            print(

                "========================================"

            )

            print(

                "PROCTIFY SESSION REPORT GENERATED"

            )

            print(

                "========================================"

            )

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

            print(

                "========================================"

            )

            print()


            return report_path


        except Exception as error:

            print(

                "Report generation error:",

                error

            )
            return None