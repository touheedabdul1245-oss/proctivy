from flask import Flask, render_template, jsonify, request, session, redirect, send_file, Response
import mysql.connector
import html as html_lib
import json
import os
import base64
import numpy as np
import cv2
import mediapipe as mp
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta


# ============================================================
# PROCTIFY - TEACHER DASHBOARD
# ============================================================

app = Flask(__name__)
app.secret_key = os.environ.get("PROCTIFY_SECRET_KEY", "proctify-dev-secret-key")

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
# GET DATABASE CONNECTION
# ============================================================

def get_database_connection():

    try:

        connection = mysql.connector.connect(
            **DB_CONFIG
        )

        return connection

    except mysql.connector.Error as error:

        print(
            "MySQL connection error:",
            error
        )

        return None


# ============================================================
# PATHS
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

LIVE_STATUS_FILE = os.path.join(
    BASE_DIR,
    "shared_data",
    "live_status.json"
)

EXAMS_FILE = os.path.join(
    BASE_DIR,
    "shared_data",
    "exams.json"
)

LIVE_MONITOR_FILE = os.path.join(
    BASE_DIR,
    "detectors",
    "live_monitor.py"
)

# Student-side agent. The AI monitor runs on the student's PC,
# not on this Flask/teacher server.
STUDENT_AGENT_URL = "http://127.0.0.1:8765"

monitor_processes = {}

# Stores the MySQL-linked monitoring session ID for each
# currently running student monitor process.
monitor_session_ids = {}

# Central-server heartbeat state for distributed student agents.
# This is intentionally in-memory because live presence is ephemeral;
# MySQL remains the authoritative store for persistent monitoring data.
CENTRAL_LIVE_HEARTBEATS = {}
CENTRAL_LIVE_TIMEOUT = 4.0
CENTRAL_LIVE_LOCK = threading.Lock()


# ============================================================
# READ LIVE STATUS
# ============================================================

def load_live_data():

    if not os.path.exists(
        LIVE_STATUS_FILE
    ):
        return {
            "system": "PROCTIFY",
            "students": []
        }

    try:

        with open(
            LIVE_STATUS_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if not isinstance(data, dict):

            return {
                "system": "PROCTIFY",
                "students": []
            }

        return data

    except Exception as error:

        print(
            "Live status read error:",
            error
        )

        return {
            "system": "PROCTIFY",
            "students": []
        }


# ============================================================
# GET STUDENTS
# ============================================================

# ============================================================
# GET STUDENTS
# ============================================================

def get_students():

    # --------------------------------------------------------
    # FIRST: TRY MYSQL
    # --------------------------------------------------------

    database_students = (
        get_students_from_database()
    )


    if database_students:

        cleaned_students = []


        for student in database_students:

            if isinstance(
                student,
                dict
            ):

                cleaned_students.append(
                    student.copy()
                )


        return cleaned_students


    # --------------------------------------------------------
    # FALLBACK: LIVE STATUS JSON
    #
    # This keeps your existing monitoring system working
    # while MySQL integration is being completed.
    # --------------------------------------------------------

    data = load_live_data()


    students = data.get(
        "students",
        []
    )


    if not isinstance(
        students,
        list
    ):

        return []


    cleaned_students = []


    for student in students:

        if isinstance(
            student,
            dict
        ):

            cleaned_students.append(
                student.copy()
            )


    return cleaned_students

    data = load_live_data()

    students = data.get(
        "students",
        []
    )

    if not isinstance(
        students,
        list
    ):
        return []

    cleaned_students = []

    for student in students:

        if isinstance(
            student,
            dict
        ):

            cleaned_students.append(
                student.copy()
            )

    return cleaned_students


# ============================================================
# CALCULATE RISK
# ============================================================

def calculate_risk(score):

    try:

        score = int(score)

    except (
        ValueError,
        TypeError
    ):

        score = 100

    if score >= 80:

        return "LOW"

    elif score >= 50:

        return "MEDIUM"

    else:

        return "HIGH"


# ============================================================
# NORMALIZE STUDENT
# ============================================================

def normalize_student(student):

    student = student.copy()

    # --------------------------------------------------------
    # Trust score
    # --------------------------------------------------------

    try:

        student["trust_score"] = int(
            student.get(
                "trust_score",
                100
            )
        )

    except (
        ValueError,
        TypeError
    ):

        student["trust_score"] = 100


    # --------------------------------------------------------
    # Risk level
    # --------------------------------------------------------

    student["risk_level"] = (
        student.get(
            "risk_level"
        )
        or calculate_risk(
            student["trust_score"]
        )
    )


    # --------------------------------------------------------
    # Basic information
    # --------------------------------------------------------

    student.setdefault(
        "student_id",
        "UNKNOWN"
    )

    student.setdefault(
        "session_id",
        "UNKNOWN"
    )

    student.setdefault(
        "exam_name",
        "PROCTIFY EXAM"
    )

    student.setdefault(
        "status",
        "OFFLINE"
    )


    # --------------------------------------------------------
    # Detection information
    # --------------------------------------------------------

    student.setdefault(
        "phone",
        False
    )

    student.setdefault(
        "phone_count",
        0
    )

    student.setdefault(
        "person_count",
        1
    )

    student.setdefault(
        "face_count",
        0
    )

    student.setdefault(
        "hand_count",
        0
    )

    student.setdefault(
        "gaze",
        "NO FACE"
    )

    student.setdefault(
        "head_direction",
        "NO FACE"
    )

    student.setdefault(
        "audio",
        "LISTENING"
    )

    student.setdefault(
        "audio_volume",
        0
    )

    student.setdefault(
        "eyes_closed",
        False
    )

    student.setdefault(
        "closed_duration",
        0
    )

    student.setdefault(
        "yaw",
        0
    )

    student.setdefault(
        "pitch",
        0
    )

    student.setdefault(
        "calibration_complete",
        False
    )


    # --------------------------------------------------------
    # Event information
    # --------------------------------------------------------

    student.setdefault(
        "last_event",
        "MONITORING STARTED"
    )

    student.setdefault(
        "last_update",
        datetime.now().strftime(
            "%H:%M:%S"
        )
    )

    return student
# ============================================================
# GET LIVE STUDENTS FROM MYSQL
# ============================================================

def get_students_from_database():

    connection = get_database_connection()

    if connection is None:

        return []


    cursor = None


    try:

        cursor = connection.cursor(
            dictionary=True
        )


        query = """

            SELECT

                ls.*,

                (
                    SELECT
                        REPLACE(
                            v.violation_type,
                            '_',
                            ' '
                        )

                    FROM violations v

                    WHERE
                        v.session_id = ls.session_id

                    ORDER BY
                        v.timestamp DESC

                    LIMIT 1

                ) AS last_event

            FROM live_students ls

            ORDER BY
                ls.student_id ASC

        """


        cursor.execute(
            query
        )


        rows = cursor.fetchall()


        students = []


        for row in rows:

            if not isinstance(
                row,
                dict
            ):
                continue


            student_id = str(
                row.get(
                    "student_id",
                    ""
                )
            )


            if not student_id:
                continue


            students.append(
                row
            )


        return students


    except mysql.connector.Error as error:

        print(
            "MySQL live students error:",
            error
        )

        return []


    finally:

        if cursor is not None:

            cursor.close()


        connection.close()

# ============================================================
# GET STUDENT VIOLATIONS FROM MYSQL
# ============================================================

def get_student_violations(
    session_id
):

    connection = get_database_connection()


    if connection is None:

        return []


    cursor = None


    try:

        cursor = connection.cursor(
            dictionary=True
        )


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

            ORDER BY timestamp DESC

        """


        cursor.execute(

            query,

            (
                str(session_id),
            )

        )


        violations = cursor.fetchall()


        return violations


    except mysql.connector.Error as error:

        print(
            "MySQL violation read error:",
            error
        )

        return []


    finally:

        if cursor is not None:

            cursor.close()


        connection.close()


# ============================================================
# GET STUDENT EVIDENCE FROM MYSQL
# ============================================================

def get_student_evidence(
    session_id
):

    connection = get_database_connection()


    if connection is None:

        return []


    cursor = None


    try:

        cursor = connection.cursor(
            dictionary=True
        )


        query = """

            SELECT

                id,

                violation_id,

                file_path,

                timestamp

            FROM evidence

            WHERE session_id = %s

            ORDER BY timestamp DESC

        """


        cursor.execute(

            query,

            (
                str(session_id),
            )

        )


        evidence = cursor.fetchall()


        return evidence


    except mysql.connector.Error as error:

        print(
            "MySQL evidence read error:",
            error
        )

        return []


    finally:

        if cursor is not None:

            cursor.close()


        connection.close()


# ============================================================
# GET TRUST SCORE HISTORY FROM MYSQL
# ============================================================

def get_trust_score_history(
    session_id
):

    connection = get_database_connection()


    if connection is None:

        return []


    cursor = None


    try:

        cursor = connection.cursor(
            dictionary=True
        )


        query = """

            SELECT

                id,

                old_score,

                new_score,

                reason,

                timestamp

            FROM trust_score_history

            WHERE session_id = %s

            ORDER BY timestamp ASC

        """


        cursor.execute(

            query,

            (
                str(session_id),
            )

        )


        history = cursor.fetchall()


        return history


    except mysql.connector.Error as error:

        print(
            "MySQL trust history error:",
            error
        )

        return []


    finally:

        if cursor is not None:

            cursor.close()


        connection.close()

# ============================================================
# CREATE FACE DESCRIPTOR
# ============================================================

def create_face_descriptor(image):

    try:

        if image is None:
            return None


        rgb_image = cv2.cvtColor(
            image,
            cv2.COLOR_BGR2RGB
        )


        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb_image
        )


        options = mp.tasks.vision.FaceLandmarkerOptions(

            base_options=mp.tasks.BaseOptions(
                model_asset_path=FACE_MODEL
            ),

            running_mode=mp.tasks.vision.RunningMode.IMAGE,

            num_faces=1

        )


        with mp.tasks.vision.FaceLandmarker.create_from_options(
            options
        ) as landmarker:

            result = landmarker.detect(
                mp_image
            )


        if not result.face_landmarks:

            return None


        landmarks = result.face_landmarks[0]


        points = np.array(
            [
                [
                    landmark.x,
                    landmark.y,
                    landmark.z
                ]

                for landmark in landmarks
            ],
            dtype=np.float32
        )


        # ----------------------------------------------------
        # Normalize around the face center
        # ----------------------------------------------------

        center = np.mean(
            points,
            axis=0
        )


        points = points - center


        # Normalize scale

        scale = np.linalg.norm(
            points
        )


        if scale == 0:

            return None


        points = points / scale


        return points.flatten()


    except Exception as error:

        print(
            "Face descriptor error:",
            error
        )

        return None

# ============================================================
# HOME PAGE
# ============================================================

# ============================================================
# LOGIN PAGE
# ============================================================

@app.route("/")
def login():

    return render_template(
        "login.html"
    )
# ============================================================
# TEACHER DASHBOARD
# ============================================================

@app.route("/teacher")
def teacher_dashboard():

    return render_template(
        "index.html"
    )
# ============================================================
# EXAMS PAGE
# ============================================================

@app.route("/exams")
def exams_page():

    return render_template(
        "exams.html"
    )
# ============================================================
# EXAM DETAILS PAGE
# ============================================================

@app.route("/exam/<exam_id>")
def exam_details(exam_id):

    return render_template(
        "exam_details.html",
        exam_id=exam_id
    )
# ============================================================
# STUDENT DASHBOARD
# ============================================================

@app.route("/student")
def student_dashboard():

    if not session.get("student_id"):
        return redirect("/")

    return render_template(
        "student_dashboard.html",
        student_id=session.get("student_id"),
        student_name=session.get("student_name", "")
    )


# ============================================================
# STUDENT FACE VERIFICATION
# ============================================================

@app.route("/student/verify")
def student_verify():

    if not session.get("student_id"):
        return redirect("/")

    return render_template("student_verify.html")


# ============================================================
# START LIVE MONITOR
# ============================================================

def start_live_monitor(student_id, exam_id, exam_name):
    """
    Prepare the monitoring session for the authenticated student.

    IMPORTANT:
    Flask does NOT start live_monitor.py here. The student's browser
    will start it through the local PROCTIFY Student Agent at
    http://127.0.0.1:8765.

    MySQL remains the authoritative source for student/exam/session
    records, violations, evidence, Trust Score and submissions.
    """
    student_id = str(student_id).strip()
    exam_id = str(exam_id).strip()
    exam_name = str(exam_name).strip()

    if not student_id:
        return False, "Student ID is required"

    if not exam_id:
        return False, "Exam ID is required"

    if not exam_name:
        return False, "Exam name is required"

    existing_session_id = session.get("active_session_id")
    existing_exam_id = session.get("active_exam_id")

    if (
        existing_session_id
        and existing_exam_id
        and str(existing_exam_id) == exam_id
    ):
        return True, str(existing_session_id)

    session_id = (
        f"SESSION_{int(datetime.now().timestamp())}_{student_id}"
    )

    session["active_session_id"] = str(session_id)
    session["active_exam_id"] = str(exam_id)

    print(
        f"Prepared student-side monitor for {student_id} | "
        f"Exam: {exam_name} | Session: {session_id}"
    )

    return True, session_id


def mark_student_offline_in_database(student_id, session_id=None):
    """
    Force the student's live monitoring row to OFFLINE.

    This is required because terminating a subprocess on Windows can
    prevent the child's Python finally-block from running. The teacher
    dashboard therefore must explicitly close the live session here.
    """
    connection = None
    cursor = None

    try:
        connection = get_database_connection()

        if connection is None:
            print("Unable to mark student OFFLINE: MySQL unavailable.")
            return False

        cursor = connection.cursor()

        if session_id:
            cursor.execute(
                """
                UPDATE live_students
                SET
                    status = 'OFFLINE',
                    phone = 0,
                    phone_count = 0,
                    person_count = 0,
                    face_count = 0,
                    hand_count = 0,
                    gaze = 'NO FACE',
                    head_direction = 'NO FACE',
                    audio = 'STOPPED',
                    audio_volume = 0,
                    camera_available = 0,
                    audio_available = 0,
                    ai_available = 0,
                    tab_available = 0
                WHERE student_id = %s
                  AND session_id = %s
                """,
                (str(student_id), str(session_id))
            )
        else:
            cursor.execute(
                """
                UPDATE live_students
                SET
                    status = 'OFFLINE',
                    phone = 0,
                    phone_count = 0,
                    person_count = 0,
                    face_count = 0,
                    hand_count = 0,
                    gaze = 'NO FACE',
                    head_direction = 'NO FACE',
                    audio = 'STOPPED',
                    audio_volume = 0,
                    camera_available = 0,
                    audio_available = 0,
                    ai_available = 0,
                    tab_available = 0
                WHERE student_id = %s
                """,
                (str(student_id),)
            )

        connection.commit()
        print(
            f"Marked {student_id} OFFLINE in MySQL"
            + (f" | Session: {session_id}" if session_id else "")
        )
        return True

    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        print("MySQL offline update error:", error)
        return False

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def stop_live_monitor(student_id, session_id=None):
    """
    Clean server-side monitoring state.

    The actual monitor process is owned by the Student Agent on the
    student's PC. This server function therefore does not terminate
    a subprocess. It marks the SQL live_students record OFFLINE.
    """
    student_id = str(student_id)

    effective_session_id = (
        session_id
        or monitor_session_ids.get(student_id)
        or session.get("active_session_id")
    )

    monitor_processes.pop(student_id, None)
    monitor_session_ids.pop(student_id, None)

    mark_student_offline_in_database(
        student_id,
        session_id=effective_session_id
    )

    if session.get("active_session_id"):
        session.pop("active_session_id", None)

    if session.get("active_exam_id"):
        session.pop("active_exam_id", None)


# ============================================================
# STUDENT PRE-EXAM SYSTEM CHECK
# ============================================================

@app.route(
    "/student/precheck/<exam_id>",
    methods=["GET"]
)
def student_precheck(exam_id):

    student_id = session.get("student_id")

    if not student_id:
        return redirect("/")

    connection = None
    cursor = None

    try:
        connection = get_database_connection()

        if connection is None:
            return "Unable to connect to MySQL", 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT exam_id, exam_name
            FROM exams
            WHERE exam_id = %s
            LIMIT 1
            """,
            (exam_id,)
        )

        exam = cursor.fetchone()

        if exam is None:
            return "Exam not found", 404

        # Do not allow a student to start an exam that was already submitted.
        cursor.execute(
            """
            SELECT submission_id, status
            FROM exam_submissions
            WHERE exam_id = %s
              AND student_id = %s
            ORDER BY submitted_at DESC
            LIMIT 1
            """,
            (exam_id, str(student_id))
        )

        existing_submission = cursor.fetchone()

        if existing_submission is not None:
            return redirect("/student")

        success, result = start_live_monitor(
            str(student_id),
            str(exam["exam_id"]),
            str(exam["exam_name"])
        )

        if not success:
            return f"Unable to start monitoring: {result}", 500

        return render_template(
            "student_precheck.html",
            exam_id=exam_id,
            student_id=student_id,
            session_id=session.get("active_session_id"),
            exam_name=exam["exam_name"],
            student_agent_url=STUDENT_AGENT_URL
        )

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


# ============================================================
# STUDENT MONITOR AGENT START INFORMATION
# ============================================================

@app.route(
    "/api/student/monitor/start",
    methods=["POST"]
)
def api_student_monitor_start():

    student_id = session.get("student_id")

    if not student_id:
        return jsonify({
            "success": False,
            "error": "Student login required."
        }), 401

    connection = None
    cursor = None

    try:
        data = request.get_json(silent=True) or {}
        exam_id = str(data.get("exam_id", "")).strip()

        if not exam_id:
            return jsonify({
                "success": False,
                "error": "Exam ID is required."
            }), 400

        connection = get_database_connection()

        if connection is None:
            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                e.exam_id,
                e.exam_name,
                e.status
            FROM exams e
            INNER JOIN exam_assignments a
                ON a.exam_id = e.exam_id
            WHERE e.exam_id = %s
              AND a.student_id = %s
            LIMIT 1
            """,
            (exam_id, str(student_id))
        )

        exam = cursor.fetchone()

        if exam is None:
            return jsonify({
                "success": False,
                "error": "Exam is not assigned to this student."
            }), 403

        cursor.execute(
            """
            SELECT submission_id
            FROM exam_submissions
            WHERE exam_id = %s
              AND student_id = %s
            ORDER BY submitted_at DESC
            LIMIT 1
            """,
            (exam_id, str(student_id))
        )

        if cursor.fetchone() is not None:
            return jsonify({
                "success": False,
                "error": "This exam has already been submitted."
            }), 409

        success, session_id = start_live_monitor(
            str(student_id),
            str(exam["exam_id"]),
            str(exam["exam_name"])
        )

        if not success:
            return jsonify({
                "success": False,
                "error": str(session_id)
            }), 500

        return jsonify({
            "success": True,
            "student_id": str(student_id),
            "exam_id": str(exam["exam_id"]),
            "exam_name": str(exam["exam_name"]),
            "session_id": str(session_id),
            "student_agent_url": STUDENT_AGENT_URL
        })

    except Exception as error:
        print("Student monitor preparation error:", error)
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


# ============================================================
# STUDENT EXAM PAGE
# ============================================================

@app.route(
    "/student/exam/<exam_id>",
    methods=["GET"]
)
def student_exam(exam_id):

    student_id = session.get("student_id")

    if not student_id:
        return redirect("/")

    connection = None
    cursor = None

    try:
        connection = get_database_connection()

        if connection is not None:
            cursor = connection.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT submission_id
                FROM exam_submissions
                WHERE exam_id = %s
                  AND student_id = %s
                ORDER BY submitted_at DESC
                LIMIT 1
                """,
                (exam_id, str(student_id))
            )

            if cursor.fetchone() is not None:
                return redirect("/student")

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return render_template(
        "student_exam.html",
        exam_id=exam_id,
        student_id=session.get("student_id"),
        student_agent_url=STUDENT_AGENT_URL
    )


@app.route(
    "/api/student/live/current",
    methods=["GET"]
)
def api_student_live_current():

    student_id = session.get("student_id")

    if not student_id:
        return jsonify({
            "success": False,
            "error": "Student is not logged in"
        }), 401

    try:

        students = get_students_from_database()

        for student in students:

            if str(
                student.get("student_id", "")
            ) == str(student_id):

                student = normalize_student(
                    student
                )

                return jsonify({
                    "success": True,
                    "student": student
                })

        return jsonify({
            "success": True,
            "student": {
                "student_id": str(student_id),
                "status": "STARTING",
                "camera_available": False,
                "audio_available": False,
                "ai_available": False,
                "tab_available": True
            }
        })

    except Exception as error:

        print(
            "Student live status error:",
            error
        )

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500
# ============================================================
# API - ALL STUDENTS
# ============================================================

@app.route("/api/students")
def api_students():

    # Host Exam needs the students enrolled by the teacher,
    # not only students currently present in live monitoring.
    connection = None
    cursor = None

    try:
        connection = get_database_connection()

        if connection is None:
            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL",
                "count": 0,
                "students": []
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                student_id,
                student_name,
                username
            FROM students
            ORDER BY created_at DESC
            """
        )

        students = cursor.fetchall()

        return jsonify({
            "success": True,
            "count": len(students),
            "students": students
        })

    except Exception as error:

        print("Student list error:", error)

        return jsonify({
            "success": False,
            "error": str(error),
            "count": 0,
            "students": []
        }), 500

    finally:

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()

# ============================================================
# ============================================================
# API - SINGLE STUDENT
# ============================================================

@app.route(
    "/api/student/<student_id>"
)
def api_student(student_id):

    students = get_students()


    for student in students:

        student = normalize_student(
            student
        )


        if str(
            student.get(
                "student_id"
            )
        ) == str(
            student_id
        ):


            session_id = student.get(
                "session_id",
                ""
            )


            violations = (
                get_student_violations(
                    session_id
                )
            )


            evidence = (
                get_student_evidence(
                    session_id
                )
            )


            trust_history = (
                get_trust_score_history(
                    session_id
                )
            )


            return jsonify({

                "success": True,

                "student": student,

                "violation_count":
                    len(
                        violations
                    ),

                "violations":
                    violations,

                "evidence":
                    evidence,

                "trust_score_history":
                    trust_history

            })


    return jsonify({

        "success": False,

        "error": "Student not found"

    }), 404

# ============================================================
# API - LIVE STATUS
# ============================================================

def is_live_monitor_running(student_id):
    """
    Return True only when the live_monitor.py subprocess that
    belongs to this student is currently running.
    """
    student_id = str(student_id)

    process = monitor_processes.get(student_id)

    if process is None:
        return False

    try:
        return process.poll() is None
    except Exception:
        return False


@app.route("/api/status")
def api_status():
    """
    Return currently-live students using MySQL as the authoritative
    cross-process source.

    A heartbeat updates live_students.last_update on every ONLINE
    heartbeat. Students are live only when:
      - status = ONLINE
      - last_update is within the last 5 seconds

    This avoids process-local CENTRAL_LIVE_HEARTBEATS being different
    between Flask workers/reloader processes.
    """
    connection = None
    cursor = None

    try:
        connection = get_database_connection()

        if connection is None:
            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL.",
                "system": "PROCTIFY",
                "student_count": 0,
                "online_students": 0,
                "students": []
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                ls.*,
                (
                    SELECT REPLACE(v.violation_type, '_', ' ')
                    FROM violations v
                    WHERE v.session_id = ls.session_id
                    ORDER BY v.timestamp DESC
                    LIMIT 1
                ) AS last_event
            FROM live_students ls
            WHERE UPPER(ls.status) = 'ONLINE'
              AND ls.last_update >= (NOW() - INTERVAL 5 SECOND)
            ORDER BY ls.student_id ASC
        """)

        rows = cursor.fetchall()

        students = [
            normalize_student(row)
            for row in rows
            if isinstance(row, dict)
        ]

        return jsonify({
            "success": True,
            "system": "PROCTIFY",
            "last_update": datetime.now().strftime("%H:%M:%S"),
            "student_count": len(students),
            "students": students,
            "online_students": len(students)
        })

    except Exception as error:
        print("Live status API error:", error)
        return jsonify({
            "success": False,
            "error": str(error),
            "system": "PROCTIFY",
            "student_count": 0,
            "online_students": 0,
            "students": []
        }), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


# ============================================================
# API - VERIFY STUDENT FACE
# ============================================================

@app.route(
    "/api/student/verify-face",
    methods=["POST"]
)
def verify_student_face():

    try:

        data = request.get_json()


        if not data:

            return jsonify({

                "success": False,

                "verified": False,

                "error":
                    "No verification data received."

            }), 400


        student_id = str(
            data.get(
                "student_id",
                "STUDENT_001"
            )
        ).strip()


        image_data = data.get(
            "image"
        )


        if not image_data:

            return jsonify({

                "success": False,

                "verified": False,

                "error":
                    "No camera image received."

            }), 400


        # ----------------------------------------------------
        # Find enrolled face
        # ----------------------------------------------------

        profile_file = os.path.join(
            FACE_PROFILES_DIR,
            f"{student_id}.jpg"
        )


        if not os.path.exists(
            profile_file
        ):

            return jsonify({

                "success": False,

                "verified": False,

                "error":
                    "No enrolled face profile found."

            }), 404


        # ----------------------------------------------------
        # Decode live camera image
        # ----------------------------------------------------

        if "," in image_data:

            image_data = image_data.split(
                ",",
                1
            )[1]


        image_bytes = base64.b64decode(
            image_data
        )


        image_array = np.frombuffer(
            image_bytes,
            dtype=np.uint8
        )


        live_image = cv2.imdecode(
            image_array,
            cv2.IMREAD_COLOR
        )


        if live_image is None:

            return jsonify({

                "success": False,

                "verified": False,

                "error":
                    "Invalid camera image."

            }), 400


        # ----------------------------------------------------
        # Load enrolled face
        # ----------------------------------------------------

        enrolled_image = cv2.imread(
            profile_file
        )


        if enrolled_image is None:

            return jsonify({

                "success": False,

                "verified": False,

                "error":
                    "Unable to read face profile."

            }), 500


        # ----------------------------------------------------
        # Create descriptors
        # ----------------------------------------------------

        enrolled_descriptor =create_face_descriptor(
                enrolled_image
            )


        live_descriptor = create_face_descriptor(
                live_image
            )


        if enrolled_descriptor is None:

            return jsonify({

                "success": False,

                "verified": False,

                "error":
                    "Could not detect the enrolled face."

            }), 500


        if live_descriptor is None:

            return jsonify({

                "success": True,

                "verified": False,

                "message":
                    "No face detected in camera."

            })


        # ----------------------------------------------------
        # Compare descriptors
        # ----------------------------------------------------

        distance = float(
            np.linalg.norm(
                enrolled_descriptor -
                live_descriptor
            )
        )


        # Prototype threshold.
        #
        # This should be calibrated using several
        # genuine/non-genuine samples before treating
        # it as a security-grade biometric threshold.

        threshold = 0.55


        verified = (
            distance <= threshold
        )


        return jsonify({

            "success": True,

            "verified": verified,

            "student_id": student_id,

            "distance": round(
                distance,
                4
            )

        })


    except Exception as error:

        print(
            "Face verification error:",
            error
        )


        return jsonify({

            "success": False,

            "verified": False,

            "error":
                str(error)

        }), 500
# ============================================================
# API - SUMMARY
# ============================================================

@app.route("/api/summary")
def api_summary():

    students = [
        normalize_student(student)
        for student in get_students()
    ]

    total_students = len(
        students
    )

    low_risk = 0
    medium_risk = 0
    high_risk = 0

    total_score = 0

    for student in students:

        score = student.get(
            "trust_score",
            100
        )

        total_score += score

        risk = student.get(
            "risk_level",
            calculate_risk(score)
        )

        if risk == "LOW":

            low_risk += 1

        elif risk == "MEDIUM":

            medium_risk += 1

        else:

            high_risk += 1


    # --------------------------------------------------------
    # Average score
    # --------------------------------------------------------

    if total_students > 0:

        average_score = round(
            total_score /
            total_students
        )

    else:

        average_score = 100


    return jsonify({

        "success": True,

        "total_students":
            total_students,

        "low_risk":
            low_risk,

        "medium_risk":
            medium_risk,

        "high_risk":
            high_risk,

        "average_trust_score":
            average_score

    })


# ============================================================
# API - SYSTEM INFORMATION
# ============================================================

@app.route("/api/system")
def api_system():

    data = load_live_data()

    students = get_students()

    online_count = 0

    for student in students:

        if student.get(
            "status"
        ) == "ONLINE":

            online_count += 1


    return jsonify({

        "success": True,

        "system":
            "PROCTIFY",

        "students":
            len(students),

        "online_students":
            online_count,

        "last_update":
            data.get(
                "last_update",
                ""
            )

    })

# ============================================================
# HOST EXAM
# ============================================================

@app.route("/host-exam")
def host_exam():

    return render_template(
        "host_exam.html"
        
    )

# ============================================================
# SAVE / CREATE EXAM
# ============================================================

EXAMS_DIR = os.path.join(
    BASE_DIR,
    "shared_data",
    "exams"
)
# ============================================================
# FACE VERIFICATION
# ============================================================

FACE_MODEL = os.path.join(
    BASE_DIR,
    "models",
    "face_landmarker",
    "face_landmarker.task"
)

FACE_PROFILES_DIR = os.path.join(
    BASE_DIR,
    "shared_data",
    "face_profiles"
)

os.makedirs(
    EXAMS_DIR,
    exist_ok=True
)
# ============================================================
# SUBMISSIONS STORAGE
# ============================================================

SUBMISSIONS_DIR = os.path.join(
    BASE_DIR,
    "shared_data",
    "submissions"
)

os.makedirs(
    SUBMISSIONS_DIR,
    exist_ok=True
)


@app.route(
    "/api/exams",
    methods=["POST"]
)
def create_exam():

    connection = None
    cursor = None

    try:

        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "error": "No exam data received"
            }), 400


        exam_name = str(
            data.get("exam_name", "")
        ).strip()

        subject = str(
            data.get("subject", "")
        ).strip()

        duration = data.get(
            "duration"
        )

        questions = data.get(
            "questions",
            []
        )

        selected_students = data.get(
            "selected_students",
            []
        )

        if not isinstance(selected_students, list):
            selected_students = []

        selected_students = [
            str(student_id).strip()
            for student_id in selected_students
            if str(student_id).strip()
        ]

        scheduled_start = str(
            data.get(
                "scheduled_start",
                ""
            )
        ).strip()


        # ----------------------------------------------------
        # VALIDATION
        # ----------------------------------------------------

        if not scheduled_start:

            return jsonify({
                "success": False,
                "error": "Scheduled start time is required"
            }), 400


        if not exam_name:

            return jsonify({
                "success": False,
                "error": "Exam name is required"
            }), 400


        if not subject:

            return jsonify({
                "success": False,
                "error": "Subject is required"
            }), 400


        if not duration:

            return jsonify({
                "success": False,
                "error": "Duration is required"
            }), 400


        if not questions:

            return jsonify({
                "success": False,
                "error": "At least one question is required"
            }), 400

        if not selected_students:

            return jsonify({
                "success": False,
                "error": "At least one student must be assigned"
            }), 400


        # ----------------------------------------------------
        # CONVERT DURATION
        # ----------------------------------------------------

        duration = int(
            duration
        )


        # ----------------------------------------------------
        # CONVERT SCHEDULED START TIME
        # ----------------------------------------------------
        #
        # Supports the datetime-local format sent
        # by the teacher exam creation page.
        #
        # Example:
        # 2026-08-27T14:30
        # ----------------------------------------------------

        try:

            scheduled_start_dt = datetime.fromisoformat(
                scheduled_start
            )

        except ValueError:

            return jsonify({
                "success": False,
                "error": "Invalid scheduled start time"
            }), 400


        # ----------------------------------------------------
        # CALCULATE EXAM END TIME
        # ----------------------------------------------------

        scheduled_end_dt = (
            scheduled_start_dt +
            timedelta(
                minutes=duration
            )
        )


        # ----------------------------------------------------
        # CREATE EXAM ID
        # ----------------------------------------------------

        exam_id = (

            "EXAM_" +

            datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )

        )


        # ----------------------------------------------------
        # DETERMINE INITIAL STATUS
        # ----------------------------------------------------

        current_time = datetime.now()

        if current_time >= scheduled_end_dt:

            exam_status = "EXPIRED"

        elif current_time >= scheduled_start_dt:

            exam_status = "ACTIVE"

        else:

            exam_status = "SCHEDULED"


        # ----------------------------------------------------
        # EXAM DATA
        # ----------------------------------------------------

        exam_data = {

            "exam_id":
                exam_id,

            "exam_name":
                exam_name,

            "subject":
                subject,

            "duration":
                duration,

            "questions":
                questions,

            "question_count":
                len(
                    questions
                ),

            "scheduled_start":
                scheduled_start_dt.isoformat(),

            "scheduled_end":
                scheduled_end_dt.isoformat(),

            "status":
                exam_status,

            "assigned_students":
                selected_students,

            "created_at":
                datetime.now().isoformat()

        }


        # ----------------------------------------------------
        # CONNECT TO MYSQL
        # ----------------------------------------------------

        connection = (
            get_database_connection()
        )


        if connection is None:

            return jsonify({

                "success":
                    False,

                "error":
                    "Unable to connect to MySQL database"

            }), 500


        cursor = connection.cursor()


        # ----------------------------------------------------
        # INSERT EXAM INTO MYSQL
        # ----------------------------------------------------

        query = """

            INSERT INTO exams (

                exam_id,

                exam_name,

                subject,

                duration,

                questions,

                question_count,

                scheduled_start,

                scheduled_end,

                status

            )

            VALUES (

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

        """


        cursor.execute(

            query,

            (

                exam_id,

                exam_name,

                subject,

                duration,

                json.dumps(
                    questions
                ),

                len(
                    questions
                ),

                scheduled_start_dt,

                scheduled_end_dt,

                exam_status

            )

        )

        # ----------------------------------------------------
        # SAVE EXAM -> STUDENT ASSIGNMENTS
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS exam_assignments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                exam_id VARCHAR(100) NOT NULL,
                student_id VARCHAR(100) NOT NULL,
                assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_exam_student (exam_id, student_id)
            )
            """
        )

        assignment_query = """
            INSERT IGNORE INTO exam_assignments
                (exam_id, student_id)
            VALUES (%s, %s)
        """

        for student_id in selected_students:
            cursor.execute(
                assignment_query,
                (exam_id, student_id)
            )

        connection.commit()


        # ----------------------------------------------------
        # SUCCESS LOG
        # ----------------------------------------------------

        print()

        print(
            "========================================"
        )

        print(
            "   PROCTIFY EXAM CREATED IN MYSQL"
        )

        print(
            "========================================"
        )

        print(
            "Exam ID:",
            exam_id
        )

        print(
            "Exam Name:",
            exam_name
        )

        print(
            "Subject:",
            subject
        )

        print(
            "Questions:",
            len(
                questions
            )
        )

        print(
            "Duration:",
            duration,
            "minutes"
        )

        print(
            "Scheduled Start:",
            scheduled_start_dt
        )

        print(
            "Scheduled End:",
            scheduled_end_dt
        )

        print(
            "Status:",
            exam_status
        )

        print(
            "========================================"
        )

        print()


        return jsonify({

            "success":
                True,

            "message":
                "Exam created successfully",

            "exam":
                exam_data

        })


    except Exception as error:

        if connection is not None:

            connection.rollback()


        print(
            "Exam creation error:",
            error
        )


        return jsonify({

            "success":
                False,

            "error":
                str(error)

        }), 500


    finally:

        if cursor is not None:

            cursor.close()


        if connection is not None:

            connection.close()
# ============================================================
# GET AVAILABLE EXAMS
# ============================================================

@app.route(
    "/api/exams",
    methods=["GET"]
)
@app.route(
    "/api/student/exams",
    methods=["GET"]
)
def get_exams():

    connection = None
    cursor = None

    try:

        connection = get_database_connection()


        if connection is None:

            return jsonify({

                "success": False,

                "error": "Unable to connect to MySQL database",

                "exams": []

            }), 500


        cursor = connection.cursor(
            dictionary=True
        )


        # ----------------------------------------------------
        # UPDATE EXAM STATUS BASED ON CURRENT TIME
        # ----------------------------------------------------

        current_time = datetime.now()


        # Any exam whose end time has passed is EXPIRED

        expire_query = """

            UPDATE exams

            SET status = 'EXPIRED'

            WHERE scheduled_end <= %s

            AND status != 'EXPIRED'

        """


        cursor.execute(

            expire_query,

            (
                current_time,
            )

        )


        # Scheduled exams become ACTIVE when their
        # scheduled start time arrives.

        activate_query = """

            UPDATE exams

            SET status = 'ACTIVE'

            WHERE scheduled_start <= %s

            AND scheduled_end > %s

            AND status = 'SCHEDULED'

        """


        cursor.execute(

            activate_query,

            (
                current_time,
                current_time
            )

        )


        connection.commit()


        # ----------------------------------------------------
        # GET ONLY NON-EXPIRED EXAMS
        # ----------------------------------------------------

        student_id = session.get("student_id")

        if student_id:

            # Student sees only exams assigned to their MySQL student_id.
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS exam_assignments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    exam_id VARCHAR(100) NOT NULL,
                    student_id VARCHAR(100) NOT NULL,
                    assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_exam_student (exam_id, student_id)
                )
                """
            )

            select_query = """
                SELECT
                    e.exam_id,
                    e.exam_name,
                    e.subject,
                    e.duration,
                    e.questions,
                    e.question_count,
                    e.scheduled_start,
                    e.scheduled_end,
                    e.status,
                    e.created_at,
                    CASE
                        WHEN s.submission_id IS NOT NULL THEN 1
                        ELSE 0
                    END AS attempted,
                    s.submission_id,
                    s.status AS submission_status,
                    s.score AS submission_score,
                    s.teacher_marks,
                    s.trust_score
                FROM exams e
                INNER JOIN exam_assignments a
                    ON a.exam_id = e.exam_id
                LEFT JOIN exam_submissions s
                    ON s.submission_id = (
                        SELECT MAX(s2.submission_id)
                        FROM exam_submissions s2
                        WHERE s2.exam_id = e.exam_id
                          AND s2.student_id = a.student_id
                    )
                WHERE a.student_id = %s
                  AND e.status != 'EXPIRED'
                ORDER BY e.created_at DESC
            """

            cursor.execute(
                select_query,
                (str(student_id),)
            )

        else:

            select_query = """
                SELECT
                    exam_id,
                    exam_name,
                    subject,
                    duration,
                    questions,
                    question_count,
                    scheduled_start,
                    scheduled_end,
                    status,
                    created_at
                FROM exams
                WHERE status != 'EXPIRED'
                ORDER BY created_at DESC
            """

            cursor.execute(select_query)


        exams = cursor.fetchall()


        # ----------------------------------------------------
        # CONVERT MYSQL VALUES FOR JSON RESPONSE
        # ----------------------------------------------------

        for exam in exams:

            exam["attempted"] = bool(
                exam.get("attempted")
            )

            questions = exam.get(
                "questions"
            )


            # MySQL JSON may arrive as a string
            # or already as a Python object.

            if isinstance(
                questions,
                str
            ):

                try:

                    exam["questions"] = json.loads(
                        questions
                    )

                except Exception:

                    exam["questions"] = []


            # Convert datetime values so Flask can
            # safely return them as JSON.

            for field in [

                "scheduled_start",

                "scheduled_end",

                "created_at"

            ]:

                value = exam.get(
                    field
                )


                if isinstance(
                    value,
                    datetime
                ):

                    exam[field] = value.isoformat()


        return jsonify({

            "success": True,

            "count": len(
                exams
            ),

            "exams": exams

        })


    except Exception as error:

        if connection is not None:

            connection.rollback()


        print(
            "Get exams error:",
            error
        )


        return jsonify({

            "success": False,

            "error": str(
                error
            ),

            "exams": []

        }), 500


    finally:

        if cursor is not None:

            cursor.close()


        if connection is not None:

            connection.close()
# ============================================================
# GET SINGLE EXAM
# ============================================================

@app.route(
    "/api/exams/<exam_id>",
    methods=["GET"]
)
def get_single_exam(exam_id):

    connection = None
    cursor = None

    try:

        connection = get_database_connection()


        if connection is None:

            return jsonify({

                "success": False,

                "error":
                    "Unable to connect to MySQL database"

            }), 500


        cursor = connection.cursor(
            dictionary=True
        )


        # ----------------------------------------------------
        # UPDATE EXAM STATUS USING CURRENT TIME
        # ----------------------------------------------------

        current_time = datetime.now()


        # Expire the exam if its end time has passed.

        cursor.execute(

            """

            UPDATE exams

            SET status = 'EXPIRED'

            WHERE exam_id = %s

            AND scheduled_end <= %s

            AND status != 'EXPIRED'

            """,

            (
                exam_id,
                current_time
            )

        )


        # Activate the exam if its scheduled start time
        # has arrived and it has not expired.

        cursor.execute(

            """

            UPDATE exams

            SET status = 'ACTIVE'

            WHERE exam_id = %s

            AND scheduled_start <= %s

            AND scheduled_end > %s

            AND status = 'SCHEDULED'

            """,

            (
                exam_id,
                current_time,
                current_time
            )

        )


        connection.commit()


        # ----------------------------------------------------
        # GET EXAM FROM MYSQL
        # ----------------------------------------------------

        cursor.execute(

            """

            SELECT

                exam_id,

                exam_name,

                subject,

                duration,

                questions,

                question_count,

                scheduled_start,

                scheduled_end,

                status,

                created_at

            FROM exams

            WHERE exam_id = %s

            LIMIT 1

            """,

            (
                exam_id,
            )

        )


        exam = cursor.fetchone()


        if not exam:

            return jsonify({

                "success": False,

                "error":
                    "Exam not found"

            }), 404


        # ----------------------------------------------------
        # CONVERT QUESTIONS
        # ----------------------------------------------------

        if isinstance(
            exam.get("questions"),
            str
        ):

            try:

                exam["questions"] = json.loads(
                    exam["questions"]
                )

            except Exception:

                exam["questions"] = []


        # ----------------------------------------------------
        # CONVERT DATETIME VALUES
        # ----------------------------------------------------

        for field in [

            "scheduled_start",

            "scheduled_end",

            "created_at"

        ]:

            value = exam.get(
                field
            )


            if isinstance(
                value,
                datetime
            ):

                exam[field] = value.isoformat()


        return jsonify({

            "success": True,

            "exam": exam

        })


    except Exception as error:

        print(
            "Single exam read error:",
            error
        )


        return jsonify({

            "success": False,

            "error":
                str(error)

        }), 500


    finally:

        if cursor is not None:

            cursor.close()


        if connection is not None:

            connection.close()
# ============================================================
# REPORT TAB SWITCH
# ============================================================

@app.route(
    "/api/exams/<exam_id>/tab-switch",
    methods=["POST"]
)
def report_tab_switch(exam_id):

    try:

        data = request.get_json(
            silent=True
        ) or {}

        student_id = str(
            data.get(
                "student_id",
                "STUDENT_001"
            )
        ).strip()

        if not student_id:
            student_id = "STUDENT_001"


        # ----------------------------------------------------
        # VERIFY EXAM EXISTS
        # ----------------------------------------------------

        exam_file = os.path.join(
            EXAMS_DIR,
            exam_id + ".json"
        )

        if not os.path.exists(
            exam_file
        ):

            return jsonify({
                "success": False,
                "error": "Exam not found"
            }), 404


        # ----------------------------------------------------
        # VERIFY EXAM IS ACTIVE
        # ----------------------------------------------------

        with open(
            exam_file,
            "r",
            encoding="utf-8"
        ) as file:

            exam = json.load(file)


        if str(
            exam.get(
                "status",
                ""
            )
        ).upper() != "ACTIVE":

            return jsonify({
                "success": False,
                "error":
                    "Exam is not active"
            }), 400


        # ----------------------------------------------------
        # READ LIVE STATUS
        # ----------------------------------------------------

        live_data = load_live_data()


        students = live_data.get(
            "students",
            []
        )


        if not isinstance(
            students,
            list
        ):

            students = []


        student_found = False

        updated_students = []


        # ----------------------------------------------------
        # UPDATE STUDENT
        # ----------------------------------------------------

        for student in students:

            if not isinstance(
                student,
                dict
            ):
                continue


            if str(
                student.get(
                    "student_id",
                    ""
                )
            ) == student_id:

                student_found = True


                # --------------------------------------------
                # TAB SWITCH COUNT
                # --------------------------------------------

                tab_switch_count = int(
                    student.get(
                        "tab_switch_count",
                        0
                    )
                ) + 1


                student[
                    "tab_switch_count"
                ] = tab_switch_count


                # --------------------------------------------
                # TAB STATUS
                # --------------------------------------------

                student[
                    "tab_status"
                ] = "VIOLATION"


                # --------------------------------------------
                # LAST TAB EVENT
                # --------------------------------------------

                current_time =datetime.now()


                student[
                    "last_tab_switch"
                ] = (
                    current_time.isoformat()
                )


                student[
                    "tab_violation_until"
                ] = (
                    datetime.now().timestamp()
                    + 10
                )


                student[
                    "last_event"
                ] = "TAB SWITCH"


                student[
                    "last_event_time"
                ] = (
                    current_time.strftime(
                        "%H:%M:%S"
                    )
                )


                # --------------------------------------------
                # TRUST SCORE PENALTY
                # --------------------------------------------

                try:

                    current_trust = int(
                        student.get(
                            "trust_score",
                            100
                        )
                    )

                except (
                    ValueError,
                    TypeError
                ):

                    current_trust = 100


                student[
                    "trust_score"
                ] = max(
                    0,
                    current_trust - 5
                )


                # --------------------------------------------
                # RISK LEVEL
                # --------------------------------------------

                trust_score = student[
                    "trust_score"
                ]


                if trust_score >= 80:

                    student[
                        "risk_level"
                    ] = "LOW"

                elif trust_score >= 50:

                    student[
                        "risk_level"
                    ] = "MEDIUM"

                else:

                    student[
                        "risk_level"
                    ] = "HIGH"


            updated_students.append(
                student
            )


        # ----------------------------------------------------
        # STUDENT NOT FOUND
        # ----------------------------------------------------

        if not student_found:

            return jsonify({
                "success": False,
                "error":
                    "Student monitoring session not found"
            }), 404


        # ----------------------------------------------------
        # FINAL LIVE STATUS
        # ----------------------------------------------------

        live_data[
            "system"
        ] = "PROCTIFY"


        live_data[
            "last_update"
        ] = datetime.now().strftime(
            "%H:%M:%S"
        )


        live_data[
            "students"
        ] = updated_students


        # ----------------------------------------------------
        # ATOMIC WRITE
        # ----------------------------------------------------

        temp_file = (
            LIVE_STATUS_FILE +
            ".tab.tmp"
        )


        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                live_data,
                file,
                indent=4
            )


        os.replace(
            temp_file,
            LIVE_STATUS_FILE
        )


        print(
            "TAB SWITCH RECORDED:",
            student_id,
            "Exam:",
            exam_id,
            "Count:",
            tab_switch_count
        )


        return jsonify({

            "success": True,

            "message":
                "Tab switch recorded",

            "student_id":
                student_id,

            "exam_id":
                exam_id,

            "tab_switch_count":
                tab_switch_count

        })


    except Exception as error:

        print(
            "Tab switch error:",
            error
        )


        return jsonify({

            "success": False,

            "error":
                str(error)

        }), 500
# ============================================================
# COMPLETE EXAM
# ============================================================

@app.route(
    "/api/exams/<exam_id>/complete",
    methods=["POST"]
)
def complete_exam(exam_id):

    try:

        exam_file = os.path.join(
            EXAMS_DIR,
            exam_id + ".json"
        )

        if not os.path.exists(exam_file):

            return jsonify({
                "success": False,
                "error": "Exam not found"
            }), 404


        with open(
            exam_file,
            "r",
            encoding="utf-8"
        ) as file:

            exam = json.load(file)


        exam["status"] = "COMPLETED"

        exam["completed_at"] = (
            datetime.now().isoformat()
        )


        with open(
            exam_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                exam,
                file,
                indent=4
            )


        print()
        print(
            "========================================"
        )
        print(
            "       PROCTIFY EXAM COMPLETED"
        )
        print(
            "========================================"
        )
        print(
            "Exam ID:",
            exam_id
        )
        print(
            "Exam:",
            exam.get(
                "exam_name",
                "Unknown"
            )
        )
        print(
            "========================================"
        )
        print()


        return jsonify({

            "success": True,

            "message":
                "Exam completed successfully",

            "exam":
                exam

        })


    except Exception as error:

        print(
            "Exam completion error:",
            error
        )

        return jsonify({

            "success": False,

            "error":
                str(error)

        }), 500
# ============================================================
# SUBMISSIONS API
# ============================================================

@app.route("/api/submissions")
def api_submissions():

    connection = None
    cursor = None

    try:

        connection = get_database_connection()

        if connection is None:

            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL",
                "submitted": 0,
                "evaluated": 0,
                "pending": 0,
                "submissions": []
            }), 500

        cursor = connection.cursor(
            dictionary=True
        )

        # MySQL is the primary source for submission cards.
        # Student identity is joined from the students table.
        query = """
            SELECT
                es.submission_id,
                es.exam_id,
                e.exam_name,
                es.student_id,
                s.student_name,
                es.session_id,
                es.answers,
                es.score,
                es.teacher_marks,
                es.trust_score,
                es.total_questions,
                es.submitted_at,
                es.evaluated_at,
                es.status,
                es.evaluation_type,
                es.teacher_feedback,

                (
                    SELECT COUNT(*)
                    FROM violations v
                    WHERE v.session_id = es.session_id
                ) AS violation_count,

                (
                    SELECT COUNT(*)
                    FROM evidence ev
                    WHERE ev.session_id = es.session_id
                ) AS evidence_count

            FROM exam_submissions es

            LEFT JOIN students s
                ON s.student_id = es.student_id

            LEFT JOIN exams e
                ON e.exam_id = es.exam_id

            ORDER BY
                es.submitted_at DESC
        """

        cursor.execute(query)

        rows = cursor.fetchall()

        submissions = []

        for row in rows:

            answers = row.get("answers")

            if isinstance(answers, str):

                try:
                    row["answers"] = json.loads(answers)

                except Exception:
                    row["answers"] = {}

            for field in [
                "submitted_at",
                "evaluated_at"
            ]:

                value = row.get(field)

                if isinstance(value, datetime):
                    row[field] = value.isoformat()

            # Compatibility fields for the teacher dashboard.
            status = str(
                row.get("status") or "SUBMITTED"
            ).upper()

            evaluation_type = str(
                row.get("evaluation_type") or ""
            ).upper()

            row["evaluated"] = (
                status == "EVALUATED"
            )

            row["pending_review"] = (
                status == "PENDING_REVIEW"
            )

            row["evaluation_type"] = (
                evaluation_type
                if evaluation_type
                else (
                    "MANUAL"
                    if status == "PENDING_REVIEW"
                    else "AUTO"
                )
            )

            submissions.append(row)

        submitted_count = len(submissions)

        evaluated_count = sum(
            1
            for item in submissions
            if str(
                item.get("status") or ""
            ).upper() == "EVALUATED"
        )

        pending_count = sum(
            1
            for item in submissions
            if str(
                item.get("status") or ""
            ).upper() == "PENDING_REVIEW"
        )

        return jsonify({

            "success": True,

            "submitted":
                submitted_count,

            "evaluated":
                evaluated_count,

            "pending":
                pending_count,

            "count":
                submitted_count,

            "submissions":
                submissions

        })

    except Exception as error:

        print(
            "Submissions MySQL API error:",
            error
        )

        return jsonify({

            "success": False,

            "error":
                str(error),

            "submitted": 0,

            "evaluated": 0,

            "pending": 0,

            "submissions": []

        }), 500

    finally:

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()



# ============================================================
# SUBMISSION REPORTS + TEACHER MANUAL EVALUATION
# ============================================================

SUBMISSION_REPORTS_DIR = os.path.join(
    BASE_DIR, "reports", "submissions"
)
os.makedirs(SUBMISSION_REPORTS_DIR, exist_ok=True)


def get_submission_report_data(submission_id):
    connection = None
    cursor = None
    try:
        connection = get_database_connection()
        if connection is None:
            return None
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                es.submission_id, es.exam_id, e.exam_name, e.subject,
                es.student_id, s.student_name, s.username,
                es.session_id, es.answers, es.score, es.teacher_marks,
                es.trust_score, es.total_questions, es.submitted_at,
                es.evaluated_at, es.status, es.evaluation_type,
                es.teacher_feedback
            FROM exam_submissions es
            LEFT JOIN students s ON s.student_id = es.student_id
            LEFT JOIN exams e ON e.exam_id = es.exam_id
            WHERE es.submission_id = %s
            LIMIT 1
        """, (int(submission_id),))
        submission = cursor.fetchone()
        if submission is None:
            return None

        session_id = submission.get("session_id")
        violations = []
        evidence = []

        if session_id:
            cursor.execute("""
                SELECT id, violation_type, severity, penalty,
                       description, timestamp
                FROM violations
                WHERE session_id = %s
                ORDER BY timestamp ASC
            """, (str(session_id),))
            violations = cursor.fetchall()

            cursor.execute("""
                SELECT id, violation_id, session_id, file_path, timestamp
                FROM evidence
                WHERE session_id = %s
                ORDER BY timestamp ASC
            """, (str(session_id),))
            evidence = cursor.fetchall()

        for row in [submission] + violations + evidence:
            for key, value in list(row.items()):
                if isinstance(value, datetime):
                    row[key] = value.isoformat()

        status = str(submission.get("status") or "").upper()
        score = submission.get("score")
        result = None if score is None else (
            "PASS" if float(score) >= 50 else "FAIL"
        )

        submission["pass_fail"] = result
        submission["violations"] = violations
        submission["evidence"] = evidence
        submission["total_violations"] = len(violations)
        submission["total_evidence"] = len(evidence)
        submission["is_pending"] = status == "PENDING_REVIEW"
        return submission

    except Exception as error:
        print("Submission report data error:", error)
        return None
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def generate_submission_report(submission_id):
    report = get_submission_report_data(submission_id)
    if report is None:
        return None

    def esc(value):
        return html_lib.escape(
            str(value if value is not None else "")
        )

    score = report.get("score")
    result = report.get("pass_fail") or "PENDING"

    violations_html = "".join(
        f"""
        <tr>
          <td>{esc(v.get("violation_type"))}</td>
          <td>{esc(v.get("severity"))}</td>
          <td>{esc(v.get("penalty"))}</td>
          <td>{esc(v.get("description"))}</td>
          <td>{esc(v.get("timestamp"))}</td>
        </tr>
        """
        for v in report["violations"]
    ) or "<tr><td colspan='5'>No violations recorded.</td></tr>"

    evidence_html = "".join(
        f"""
        <div class="evidence">
          <img src="/api/evidence/{int(e["id"])}/image">
          <small>Evidence #{int(e["id"])} · {esc(e.get("timestamp"))}</small>
        </div>
        """
        for e in report["evidence"]
    ) or "<p>No evidence images recorded.</p>"

    report_html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>PROCTIFY Report</title>
<style>
body{{font-family:Arial,sans-serif;margin:40px;color:#222}}
.summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.box{{border:1px solid #ddd;border-radius:10px;padding:16px}}
.label{{font-size:12px;color:#777}} .value{{font-size:24px;font-weight:bold}}
table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #ddd;padding:8px;text-align:left}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}
.evidence img{{width:100%;height:240px;object-fit:contain;border:1px solid #ddd;border-radius:8px}}
.evidence small{{display:block;color:#666;margin-top:5px}}
</style></head><body>
<h1>PROCTIFY Examination Report</h1>
<p>
<b>Student:</b> {esc(report.get("student_name") or report.get("student_id"))}<br>
<b>Student ID:</b> {esc(report.get("student_id"))}<br>
<b>Exam:</b> {esc(report.get("exam_name") or report.get("exam_id"))}<br>
<b>Submission ID:</b> {esc(report.get("submission_id"))}
</p>
<div class="summary">
<div class="box"><div class="label">MARKS</div><div class="value">{esc(score if score is not None else "PENDING")}</div></div>
<div class="box"><div class="label">TRUST SCORE</div><div class="value">{esc(report.get("trust_score"))}</div></div>
<div class="box"><div class="label">RESULT</div><div class="value">{esc(result)}</div></div>
<div class="box"><div class="label">EVALUATION</div><div class="value">{esc(report.get("evaluation_type"))}</div></div>
</div>
<h2>Violation History</h2>
<table><thead><tr><th>Violation</th><th>Severity</th><th>Penalty</th><th>Description</th><th>Time</th></tr></thead>
<tbody>{violations_html}</tbody></table>
<h2>Evidence</h2><div class="grid">{evidence_html}</div>
</body></html>"""

    path = os.path.join(
        SUBMISSION_REPORTS_DIR,
        f"submission_{int(submission_id)}.html"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_html)
    return path



# ============================================================
# DISTRIBUTED MONITORING / LIVE VIDEO
# ============================================================
# Student PCs run the AI monitor locally. These endpoints are the
# central-server bridge. MySQL remains the authoritative data source
# for monitoring state, violations, evidence and completed sessions.
# Processed video is ephemeral and kept in server memory only.
# ============================================================

LIVE_VIDEO_FRAMES = {}
LIVE_VIDEO_LOCK = threading.Lock()
LIVE_VIDEO_MAX_AGE = 5.0


def _monitor_json_datetime(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


@app.route("/api/monitor/live-status", methods=["POST"])
def monitor_live_status():
    connection = None
    cursor = None

    try:
        data = request.get_json(silent=True) or {}

        student_id = str(data.get("student_id", "")).strip()
        session_id = str(data.get("session_id", "")).strip()
        exam_name = str(data.get("exam_name", "PROCTIFY EXAM")).strip()
        status = str(data.get("status", "OFFLINE")).strip().upper()

        if not student_id or not session_id:
            return jsonify({
                "success": False,
                "error": "student_id and session_id are required."
            }), 400

        if status not in {"ONLINE", "OFFLINE"}:
            return jsonify({
                "success": False,
                "error": "Invalid monitoring status."
            }), 400

        # Maintain a short-lived central heartbeat so the teacher dashboard
        # reflects real activity instead of stale SQL ONLINE rows.
        with CENTRAL_LIVE_LOCK:
            if status == "ONLINE":
                CENTRAL_LIVE_HEARTBEATS[student_id] = {
                    "session_id": session_id,
                    "updated": time.monotonic()
                }
            else:
                CENTRAL_LIVE_HEARTBEATS.pop(student_id, None)

        connection = get_database_connection()
        if connection is None:
            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

        cursor = connection.cursor()

        values = (
            student_id,
            session_id,
            exam_name,
            status,
            int(data.get("trust_score", 100) if data.get("trust_score") is not None else 100),
            str(data.get("risk_level", "LOW")),
            int(bool(data.get("phone", False))),
            int(data.get("phone_count", 0) or 0),
            int(data.get("person_count", 0) or 0),
            int(data.get("face_count", 0) or 0),
            int(data.get("hand_count", 0) or 0),
            str(data.get("gaze", "NO FACE")),
            str(data.get("head_direction", "NO FACE")),
            str(data.get("audio", "LISTENING")),
            float(data.get("audio_volume", 0) or 0),
            int(bool(data.get("camera_available", False))),
            int(bool(data.get("audio_available", False))),
            int(bool(data.get("ai_available", False))),
            int(bool(data.get("tab_available", False)))
        )

        # MySQL UPSERT: update existing student or insert a new one.
        # last_update is the shared, database-backed heartbeat timestamp.
        upsert_sql = """
            INSERT INTO live_students
            (
                student_id, session_id, exam_name, status,
                trust_score, risk_level,
                phone, phone_count, person_count, face_count,
                hand_count, gaze, head_direction,
                audio, audio_volume,
                camera_available, audio_available,
                ai_available, tab_available, last_update
            )
            VALUES
            (
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s, NOW()
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
                tab_available = VALUES(tab_available),
                last_update = NOW()
        """

        cursor.execute(upsert_sql, values)

        connection.commit()

        return jsonify({
            "success": True,
            "student_id": student_id,
            "session_id": session_id,
            "status": status
        })

    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        print("Central live-status error:", error)
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/monitor/violation", methods=["POST"])
def monitor_violation():
    connection = None
    cursor = None

    try:
        data = request.get_json(silent=True) or {}

        session_id = str(data.get("session_id", "")).strip()
        student_id = str(data.get("student_id", "")).strip()
        violation_type = str(data.get("violation_type", "")).strip()
        severity = str(data.get("severity", "MEDIUM")).strip()
        description = str(data.get("description", "")).strip()
        penalty = int(data.get("penalty", 0) or 0)
        old_score = int(data.get("old_score", 100) or 100)
        new_score = int(data.get("new_score", 100) or 100)

        if not session_id or not student_id or not violation_type:
            return jsonify({
                "success": False,
                "error": "session_id, student_id and violation_type are required."
            }), 400

        connection = get_database_connection()
        if connection is None:
            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

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
            session_id,
            violation_type,
            severity,
            penalty,
            description
        ))

        violation_id = cursor.lastrowid

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
            session_id,
            old_score,
            new_score,
            violation_type
        ))

        # Keep the authoritative live student row synchronized with the
        # score calculated by the student-side AI engine.
        cursor.execute("""
            UPDATE live_students
            SET trust_score = %s,
                risk_level = CASE
                    WHEN %s >= 80 THEN 'LOW'
                    WHEN %s >= 50 THEN 'MEDIUM'
                    ELSE 'HIGH'
                END
            WHERE student_id = %s
              AND session_id = %s
        """, (
            new_score,
            new_score,
            new_score,
            student_id,
            session_id
        ))

        connection.commit()

        return jsonify({
            "success": True,
            "violation_id": int(violation_id)
        })

    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        print("Central violation error:", error)
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/monitor/evidence", methods=["POST"])
def monitor_evidence():
    connection = None
    cursor = None

    try:
        session_id = str(request.form.get("session_id", "")).strip()
        student_id = str(request.form.get("student_id", "")).strip()
        violation_type = str(request.form.get("violation_type", "VIOLATION")).strip()
        violation_id_raw = str(request.form.get("violation_id", "")).strip()
        uploaded = request.files.get("evidence")

        if not session_id or not student_id or uploaded is None:
            return jsonify({
                "success": False,
                "error": "session_id, student_id and evidence image are required."
            }), 400

        safe_type = "".join(
            ch if ch.isalnum() or ch in "-_" else "_"
            for ch in violation_type
        ) or "VIOLATION"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{safe_type}_{student_id}_{timestamp}.jpg"
        evidence_dir = os.path.join(BASE_DIR, "reports", "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        file_path = os.path.join(evidence_dir, filename)

        uploaded.save(file_path)

        if not os.path.isfile(file_path):
            return jsonify({
                "success": False,
                "error": "Evidence image could not be saved."
            }), 500

        connection = get_database_connection()
        if connection is None:
            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

        cursor = connection.cursor()

        # Use the exact violation created by the monitoring engine when
        # available. Keep the old same-session/type lookup as a compatibility
        # fallback for older agents.
        violation_id = None
        if violation_id_raw:
            try:
                requested_violation_id = int(violation_id_raw)
            except (TypeError, ValueError):
                requested_violation_id = None

            if requested_violation_id is not None:
                cursor.execute("""
                    SELECT id
                    FROM violations
                    WHERE id = %s
                      AND session_id = %s
                    LIMIT 1
                """, (requested_violation_id, session_id))
                violation_row = cursor.fetchone()
                violation_id = violation_row[0] if violation_row else None

        if violation_id is None:
            cursor.execute("""
                SELECT id
                FROM violations
                WHERE session_id = %s
                  AND violation_type = %s
                ORDER BY id DESC
                LIMIT 1
            """, (session_id, violation_type))
            violation_row = cursor.fetchone()
            violation_id = violation_row[0] if violation_row else None

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
            session_id,
            file_path
        ))

        evidence_id = cursor.lastrowid
        connection.commit()

        return jsonify({
            "success": True,
            "evidence_id": int(evidence_id),
            "violation_id": int(violation_id) if violation_id is not None else None,
            "file_path": file_path
        })

    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        print("Central evidence error:", error)
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/monitor/violations", methods=["GET"])
def monitor_violations():
    connection = None
    cursor = None
    try:
        session_id = str(request.args.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"success": False, "error": "session_id is required."}), 400

        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Unable to connect to MySQL."}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, violation_type, severity, penalty, description, timestamp
            FROM violations
            WHERE session_id = %s
            ORDER BY timestamp DESC
        """, (session_id,))
        rows = cursor.fetchall()

        for row in rows:
            for key, value in list(row.items()):
                row[key] = _monitor_json_datetime(value)

        return jsonify({"success": True, "violations": rows})

    except Exception as error:
        print("Central violation read error:", error)
        return jsonify({"success": False, "error": str(error), "violations": []}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/monitor/evidence", methods=["GET"])
def monitor_evidence_list():
    connection = None
    cursor = None
    try:
        session_id = str(request.args.get("session_id", "")).strip()
        if not session_id:
            return jsonify({"success": False, "error": "session_id is required."}), 400

        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Unable to connect to MySQL."}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, violation_id, session_id, file_path, timestamp
            FROM evidence
            WHERE session_id = %s
            ORDER BY timestamp DESC
        """, (session_id,))
        rows = cursor.fetchall()

        for row in rows:
            for key, value in list(row.items()):
                row[key] = _monitor_json_datetime(value)

        return jsonify({"success": True, "evidence": rows})

    except Exception as error:
        print("Central evidence read error:", error)
        return jsonify({"success": False, "error": str(error), "evidence": []}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/monitor/session-complete", methods=["POST"])
def monitor_session_complete():
    connection = None
    cursor = None
    try:
        data = request.get_json(silent=True) or {}

        session_id = str(data.get("session_id", "")).strip()
        student_id = str(data.get("student_id", "")).strip()
        exam_name = str(data.get("exam_name", "PROCTIFY EXAM")).strip()
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        trust_score = int(data.get("final_trust_score", 100) or 100)
        risk_level = str(data.get("final_risk_level", "LOW"))

        if not session_id or not student_id:
            return jsonify({
                "success": False,
                "error": "session_id and student_id are required."
            }), 400

        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Unable to connect to MySQL."}), 500

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
            VALUES (%s, %s, %s, %s, %s, 'COMPLETED', %s, %s)
            ON DUPLICATE KEY UPDATE
                student_id = VALUES(student_id),
                exam_name = VALUES(exam_name),
                start_time = VALUES(start_time),
                end_time = VALUES(end_time),
                status = 'COMPLETED',
                final_trust_score = VALUES(final_trust_score),
                final_risk_level = VALUES(final_risk_level)
        """, (
            session_id,
            student_id,
            exam_name,
            start_time,
            end_time,
            trust_score,
            risk_level
        ))

        cursor.execute("""
            UPDATE live_students
            SET status = 'OFFLINE',
                phone = 0,
                phone_count = 0,
                person_count = 0,
                face_count = 0,
                hand_count = 0,
                gaze = 'NO FACE',
                head_direction = 'NO FACE',
                audio = 'STOPPED',
                audio_volume = 0,
                camera_available = 0,
                audio_available = 0,
                ai_available = 0,
                tab_available = 0
            WHERE student_id = %s
              AND session_id = %s
        """, (student_id, session_id))

        connection.commit()

        return jsonify({
            "success": True,
            "session_id": session_id
        })

    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        print("Central session-complete error:", error)
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/monitor/report", methods=["POST"])
def monitor_report():
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get("session_id", "")).strip()
        student_id = str(data.get("student_id", "")).strip()
        exam_name = str(data.get("exam_name", "PROCTIFY EXAM")).strip()

        if not session_id or not student_id:
            return jsonify({
                "success": False,
                "error": "session_id and student_id are required."
            }), 400

        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Unable to connect to MySQL."}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT final_trust_score, final_risk_level
            FROM exam_sessions
            WHERE session_id = %s
            LIMIT 1
        """, (session_id,))
        session_row = cursor.fetchone() or {}

        cursor.execute("""
            SELECT id, violation_type, severity, penalty, description, timestamp
            FROM violations
            WHERE session_id = %s
            ORDER BY timestamp ASC
        """, (session_id,))
        violations = cursor.fetchall()

        cursor.execute("""
            SELECT id, violation_id, file_path, timestamp
            FROM evidence
            WHERE session_id = %s
            ORDER BY timestamp ASC
        """, (session_id,))
        evidence = cursor.fetchall()

        for collection in (violations, evidence):
            for row in collection:
                for key, value in list(row.items()):
                    row[key] = _monitor_json_datetime(value)

        report_dir = os.path.join(BASE_DIR, "reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(
            report_dir,
            f"session_report_{session_id.replace(' ', '_')}.json"
        )

        report_data = {
            "system": "PROCTIFY",
            "session_id": session_id,
            "student_id": student_id,
            "exam_name": exam_name,
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "duration_seconds": data.get("duration_seconds", 0),
            "cheating_score": data.get("cheating_score", 0),
            "final_trust_score": session_row.get(
                "final_trust_score",
                data.get("final_trust_score", 100)
            ),
            "final_risk_level": session_row.get(
                "final_risk_level",
                data.get("final_risk_level", "LOW")
            ),
            "total_violations": len(violations),
            "total_evidence": len(evidence),
            "violations": violations,
            "evidence": evidence
        }

        with open(report_path, "w", encoding="utf-8") as file:
            json.dump(report_data, file, indent=4)

        cursor.close()
        connection.close()
        cursor = None
        connection = None

        return jsonify({
            "success": True,
            "report_path": report_path
        })

    except Exception as error:
        print("Central report generation error:", error)
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/live/video/frame", methods=["POST"])
def receive_live_video_frame():
    try:
        student_id = str(request.form.get("student_id", "")).strip()
        session_id = str(request.form.get("session_id", "")).strip()
        uploaded = request.files.get("frame")

        if uploaded is None:
            # Also accept raw request bodies for compatibility with future agents.
            raw = request.get_data()
        else:
            raw = uploaded.read()

        if not student_id or not session_id or not raw:
            return jsonify({
                "success": False,
                "error": "student_id, session_id and frame are required."
            }), 400

        now_mono = time.monotonic()

        with LIVE_VIDEO_LOCK:
            LIVE_VIDEO_FRAMES[student_id] = {
                "session_id": session_id,
                "frame": raw,
                "updated": now_mono
            }

        # Video frames are also a live heartbeat from the student agent.
        with CENTRAL_LIVE_LOCK:
            heartbeat = CENTRAL_LIVE_HEARTBEATS.get(student_id)
            if heartbeat is not None and heartbeat.get("session_id") == session_id:
                heartbeat["updated"] = now_mono

        return jsonify({"success": True})

    except Exception as error:
        print("Live video receive error:", error)
        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/live/video/<student_id>")
def live_video_stream(student_id):
    student_id = str(student_id).strip()

    def generate():
        last_sent = None

        while True:
            with LIVE_VIDEO_LOCK:
                item = LIVE_VIDEO_FRAMES.get(student_id)
                if item is not None:
                    age = time.monotonic() - item["updated"]
                    frame = item["frame"] if age <= LIVE_VIDEO_MAX_AGE else None
                else:
                    frame = None

            if frame is not None and frame != last_sent:
                last_sent = frame
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Cache-Control: no-cache\r\n\r\n"
                    + frame
                    + b"\r\n"
                )

            time.sleep(0.05)

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


# ============================================================
# SQL-ONLY REPORT COMPATIBILITY ENDPOINTS
# ============================================================
# These endpoints exist so older/cached dashboard JavaScript that
# calls /api/reports still reads exclusively from MySQL.
# ============================================================

@app.route("/api/reports", methods=["GET"])
def api_reports_sql_only():
    connection = None
    cursor = None
    try:
        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Unable to connect to MySQL.", "reports": []}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                es.submission_id,
                es.exam_id,
                e.exam_name,
                es.student_id,
                s.student_name,
                es.session_id,
                es.score,
                es.teacher_marks,
                es.trust_score,
                es.total_questions,
                es.submitted_at,
                es.evaluated_at,
                es.status,
                es.evaluation_type,
                es.teacher_feedback
            FROM exam_submissions es
            LEFT JOIN students s ON s.student_id = es.student_id
            LEFT JOIN exams e ON e.exam_id = es.exam_id
            ORDER BY es.submitted_at DESC
        """)
        rows = cursor.fetchall()
        reports = []

        for row in rows:
            session_id = row.get("session_id")
            violation_count = 0
            evidence_count = 0

            if session_id:
                cursor.execute(
                    "SELECT COUNT(*) AS c FROM violations WHERE session_id = %s",
                    (str(session_id),)
                )
                violation_count = int((cursor.fetchone() or {}).get("c", 0) or 0)

                cursor.execute(
                    "SELECT COUNT(*) AS c FROM evidence WHERE session_id = %s",
                    (str(session_id),)
                )
                evidence_count = int((cursor.fetchone() or {}).get("c", 0) or 0)

            trust = row.get("trust_score")
            if trust is None:
                risk = "LOW"
            elif int(trust) < 50:
                risk = "HIGH"
            elif int(trust) < 75:
                risk = "MEDIUM"
            else:
                risk = "LOW"

            for key, value in list(row.items()):
                if isinstance(value, datetime):
                    row[key] = value.isoformat()

            reports.append({
                **row,
                "final_trust_score": row.get("trust_score"),
                "final_risk_level": risk,
                "violation_count": violation_count,
                "evidence_count": evidence_count
            })

        return jsonify({"success": True, "reports": reports})

    except Exception as error:
        print("SQL reports API error:", error)
        return jsonify({"success": False, "error": str(error), "reports": []}), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/reports/<session_id>", methods=["GET"])
def api_report_by_session_sql_only(session_id):
    connection = None
    cursor = None
    try:
        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Unable to connect to MySQL."}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT submission_id
            FROM exam_submissions
            WHERE session_id = %s
            ORDER BY submission_id DESC
            LIMIT 1
        """, (str(session_id),))
        row = cursor.fetchone()

        if row is None:
            return jsonify({
                "success": False,
                "error": "No MySQL submission found for this session."
            }), 404

        submission_id = int(row["submission_id"])
        report = get_submission_report_data(submission_id)

        if report is None:
            return jsonify({"success": False, "error": "Submission report not found."}), 404

        trust = report.get("trust_score")
        if trust is None:
            risk = "LOW"
        elif int(trust) < 50:
            risk = "HIGH"
        elif int(trust) < 75:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        report["final_trust_score"] = trust
        report["final_risk_level"] = risk
        report["violation_count"] = len(report.get("violations", []))
        report["evidence_count"] = len(report.get("evidence", []))

        generate_submission_report(submission_id)

        return jsonify({
            "success": True,
            "report": report,
            "report_url": f"/submission-report/{submission_id}"
        })

    except Exception as error:
        print("SQL report detail error:", error)
        return jsonify({"success": False, "error": str(error)}), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/submissions/<int:submission_id>")
def api_submission_detail(submission_id):
    report = get_submission_report_data(submission_id)
    if report is None:
        return jsonify({"success": False, "error": "Submission not found"}), 404

    path = generate_submission_report(submission_id)
    return jsonify({
        "success": True,
        "submission": report,
        "report_url": f"/submission-report/{submission_id}" if path else None
    })


@app.route("/api/submissions/<int:submission_id>/evaluate", methods=["POST"])
def evaluate_submission(submission_id):
    connection = None
    cursor = None
    try:
        data = request.get_json(silent=True) or {}
        try:
            marks = float(data.get("marks"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Valid marks are required."}), 400

        if marks < 0 or marks > 100:
            return jsonify({"success": False, "error": "Marks must be between 0 and 100."}), 400

        marks = round(marks)
        feedback = str(data.get("feedback") or "").strip()

        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Unable to connect to MySQL."}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT submission_id, status
            FROM exam_submissions
            WHERE submission_id = %s
            LIMIT 1
        """, (int(submission_id),))
        row = cursor.fetchone()

        if row is None:
            return jsonify({"success": False, "error": "Submission not found."}), 404

        if str(row["status"] or "").upper() != "PENDING_REVIEW":
            return jsonify({"success": False, "error": "Submission is already evaluated."}), 409

        now = datetime.now()
        cursor.execute("""
            UPDATE exam_submissions
            SET score = %s,
                teacher_marks = %s,
                teacher_feedback = %s,
                evaluated_at = %s,
                status = 'EVALUATED',
                evaluation_type = 'MANUAL'
            WHERE submission_id = %s
        """, (marks, marks, feedback or None, now, int(submission_id)))

        connection.commit()
        path = generate_submission_report(submission_id)
        report = get_submission_report_data(submission_id)

        return jsonify({
            "success": True,
            "submission": report,
            "report_url": f"/submission-report/{submission_id}" if path else None
        })

    except Exception as error:
        if connection is not None:
            try: connection.rollback()
            except Exception: pass
        print("Manual evaluation error:", error)
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if cursor is not None: cursor.close()
        if connection is not None: connection.close()


@app.route("/submission-report/<int:submission_id>")
def submission_report_page(submission_id):
    path = generate_submission_report(submission_id)
    if path is None:
        return "Submission report not found", 404
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@app.route("/api/evidence/<int:evidence_id>/image")
def api_evidence_image(evidence_id):
    connection = None
    cursor = None
    try:
        connection = get_database_connection()
        if connection is None:
            return "MySQL unavailable", 500
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT file_path FROM evidence WHERE id = %s LIMIT 1",
            (int(evidence_id),)
        )
        row = cursor.fetchone()
        if not row or not row.get("file_path"):
            return "Evidence not found", 404

        filename = os.path.basename(str(row["file_path"]))
        evidence_dir = os.path.join(BASE_DIR, "reports", "evidence")
        path = os.path.join(evidence_dir, filename)

        if not os.path.isfile(path):
            return "Evidence image not found", 404

        return send_file(path, mimetype="image/jpeg")
    except Exception as error:
        print("Evidence image error:", error)
        return "Unable to load evidence", 500
    finally:
        if cursor is not None: cursor.close()
        if connection is not None: connection.close()


# ============================================================
# SUBMIT EXAM ANSWERS - MYSQL PRIMARY
# ============================================================

@app.route(
    "/api/exams/<exam_id>/submit",
    methods=["POST"]
)
def submit_exam(exam_id):

    connection = None
    cursor = None

    try:

        # ----------------------------------------------------
        # REQUIRE LOGGED-IN STUDENT
        # ----------------------------------------------------

        student_id = session.get(
            "student_id"
        )

        if not student_id:

            return jsonify({

                "success": False,

                "error":
                    "Student login required."

            }), 401

        student_id = str(
            student_id
        ).strip()


        # ----------------------------------------------------
        # READ ANSWERS
        # ----------------------------------------------------

        data = request.get_json(
            silent=True
        ) or {}

        answers = data.get(
            "answers",
            {}
        )

        if not isinstance(
            answers,
            dict
        ):

            return jsonify({

                "success": False,

                "error":
                    "Answers must be an object."

            }), 400


        # ----------------------------------------------------
        # CONNECT MYSQL
        # ----------------------------------------------------

        connection = get_database_connection()

        if connection is None:

            return jsonify({

                "success": False,

                "error":
                    "Unable to connect to MySQL."

            }), 500

        cursor = connection.cursor(
            dictionary=True
        )


        # ----------------------------------------------------
        # VERIFY STUDENT EXISTS
        # ----------------------------------------------------

        cursor.execute(

            """
            SELECT
                student_id,
                student_name
            FROM students
            WHERE student_id = %s
            LIMIT 1
            """,

            (
                student_id,
            )

        )

        student = cursor.fetchone()

        if student is None:

            return jsonify({

                "success": False,

                "error":
                    "Logged-in student was not found in MySQL."

            }), 404


        # ----------------------------------------------------
        # GET EXAM FROM MYSQL
        # ----------------------------------------------------

        cursor.execute(

            """
            SELECT
                exam_id,
                exam_name,
                questions,
                question_count,
                status
            FROM exams
            WHERE exam_id = %s
            LIMIT 1
            """,

            (
                exam_id,
            )

        )

        exam = cursor.fetchone()

        if exam is None:

            return jsonify({

                "success": False,

                "error":
                    "Exam not found in MySQL."

            }), 404


        # ----------------------------------------------------
        # VERIFY ASSIGNMENT
        # ----------------------------------------------------

        cursor.execute(

            """
            SELECT
                assignment_id
            FROM exam_assignments
            WHERE exam_id = %s
              AND student_id = %s
            LIMIT 1
            """,

            (
                exam_id,
                student_id
            )

        )

        assignment = cursor.fetchone()

        if assignment is None:

            return jsonify({

                "success": False,

                "error":
                    "This exam is not assigned to the logged-in student."

            }), 403


        # ----------------------------------------------------
        # LOAD QUESTIONS FROM MYSQL
        # ----------------------------------------------------

        questions = exam.get(
            "questions"
        )

        if isinstance(
            questions,
            str
        ):

            try:

                questions = json.loads(
                    questions
                )

            except Exception:

                questions = []

        if not isinstance(
            questions,
            list
        ):

            questions = []


        if not questions:

            return jsonify({

                "success": False,

                "error":
                    "Exam has no valid questions."

            }), 400


        # ----------------------------------------------------
        # FIND MONITORING SESSION
        # ----------------------------------------------------

        session_id = session.get(
            "active_session_id"
        )

        if not session_id:

            # Fall back to the latest MySQL session for
            # this student/exam if the browser session
            # was refreshed.
            cursor.execute(

                """
                SELECT
                    es.session_id
                FROM exam_sessions es
                WHERE es.student_id = %s
                  AND es.exam_name = %s
                ORDER BY
                    es.start_time DESC
                LIMIT 1
                """,

                (
                    student_id,
                    exam["exam_name"]
                )

            )

            session_row = cursor.fetchone()

            if session_row:

                session_id = str(
                    session_row["session_id"]
                )


        # ----------------------------------------------------
        # GET CURRENT TRUST SCORE FROM MYSQL
        # ----------------------------------------------------

        trust_score = 100
        risk_level = "LOW"

        if session_id:

            cursor.execute(

                """
                SELECT
                    final_trust_score,
                    final_risk_level
                FROM exam_sessions
                WHERE session_id = %s
                LIMIT 1
                """,

                (
                    str(session_id),
                )

            )

            session_row = cursor.fetchone()

            if session_row:

                if session_row.get(
                    "final_trust_score"
                ) is not None:

                    trust_score = int(
                        session_row[
                            "final_trust_score"
                        ]
                    )

                risk_level = str(
                    session_row.get(
                        "final_risk_level"
                    ) or
                    (
                        "LOW"
                        if trust_score >= 80
                        else (
                            "MEDIUM"
                            if trust_score >= 50
                            else "HIGH"
                        )
                    )
                )

            # The live_students row contains the continuously
            # updated monitoring score while the monitor is running.
            cursor.execute(

                """
                SELECT
                    trust_score,
                    risk_level
                FROM live_students
                WHERE student_id = %s
                  AND session_id = %s
                LIMIT 1
                """,

                (
                    student_id,
                    str(session_id)
                )

            )

            live_row = cursor.fetchone()

            if live_row:

                if live_row.get(
                    "trust_score"
                ) is not None:

                    trust_score = int(
                        live_row[
                            "trust_score"
                        ]
                    )

                risk_level = str(
                    live_row.get(
                        "risk_level"
                    ) or risk_level
                )


        trust_score = max(
            0,
            min(
                100,
                int(trust_score)
            )
        )


        # ----------------------------------------------------
        # EVALUATE ANSWERS
        # ----------------------------------------------------

        correct_count = 0
        answered_count = 0

        for index, question in enumerate(
            questions
        ):

            if not isinstance(
                question,
                dict
            ):
                continue

            question_key = str(
                index
            )

            student_answer = answers.get(
                question_key
            )

            if (
                student_answer is not None
                and
                str(student_answer).strip()
            ):

                answered_count += 1

            correct_answer = question.get(
                "correct_answer"
            )

            if (
                student_answer is not None
                and
                correct_answer is not None
                and
                str(student_answer).strip().upper()
                ==
                str(correct_answer).strip().upper()
            ):

                correct_count += 1


        total_questions = len(
            questions
        )


        automatic_score = round(

            (
                correct_count /
                total_questions
            ) * 100

        )


        # ----------------------------------------------------
        # TRUST SCORE DECISION
        # ----------------------------------------------------

        if trust_score >= 50:

            final_score = automatic_score
            submission_status = "EVALUATED"
            evaluation_type = "AUTO"
            evaluated_at = datetime.now()

        else:

            final_score = None
            submission_status = "PENDING_REVIEW"
            evaluation_type = "MANUAL"
            evaluated_at = None


        # ----------------------------------------------------
        # PREVENT DUPLICATE SUBMISSIONS
        # ----------------------------------------------------

        duplicate_query = """

            SELECT
                submission_id,
                status
            FROM exam_submissions
            WHERE exam_id = %s
              AND student_id = %s
            ORDER BY submitted_at DESC
            LIMIT 1

        """

        cursor.execute(

            duplicate_query,

            (
                exam_id,
                student_id
            )

        )

        existing_submission = cursor.fetchone()

        if existing_submission is not None:

            return jsonify({

                "success": False,

                "error":
                    "This exam has already been submitted.",

                "submission_id":
                    existing_submission[
                        "submission_id"
                    ],

                "status":
                    existing_submission[
                        "status"
                    ]

            }), 409


        # ----------------------------------------------------
        # INSERT MYSQL SUBMISSION
        # ----------------------------------------------------

        insert_query = """

            INSERT INTO exam_submissions
            (
                exam_id,
                student_id,
                session_id,
                answers,
                score,
                teacher_marks,
                trust_score,
                total_questions,
                submitted_at,
                evaluated_at,
                status,
                evaluation_type,
                teacher_feedback
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
                %s
            )

        """

        submitted_at = datetime.now()

        cursor.execute(

            insert_query,

            (
                str(exam_id),
                student_id,
                str(session_id) if session_id else None,
                json.dumps(answers),
                final_score,
                final_score if submission_status == "EVALUATED" else None,
                trust_score,
                total_questions,
                submitted_at,
                evaluated_at,
                submission_status,
                evaluation_type,
                None
            )

        )

        submission_id = cursor.lastrowid


        # ----------------------------------------------------
        # UPDATE EXAM SESSION TO FINAL STATE
        # ----------------------------------------------------

        if session_id:

            cursor.execute(

                """
                UPDATE exam_sessions
                SET
                    status = 'COMPLETED',
                    end_time = %s,
                    final_trust_score = %s,
                    final_risk_level = %s
                WHERE session_id = %s
                """,

                (
                    submitted_at,
                    trust_score,
                    risk_level,
                    str(session_id)
                )

            )


        connection.commit()

        # The examination is finished. Stop the local live monitor
        # immediately so camera/AI monitoring does not continue.
        stop_live_monitor(
            student_id,
            session_id=session_id
        )

        # End the student's login session after successful submission.
        # A later login will see this exam as ATTEMPTED and cannot reopen it.
        session.clear()


        # ----------------------------------------------------
        # RESPONSE
        # ----------------------------------------------------

        submission_response = {

            "submission_id":
                submission_id,

            "exam_id":
                exam_id,

            "exam_name":
                exam["exam_name"],

            "student_id":
                student_id,

            "student_name":
                student["student_name"],

            "session_id":
                session_id,

            "total_questions":
                total_questions,

            "answered_questions":
                answered_count,

            "correct_answers":
                correct_count,

            "score":
                final_score,

            "automatic_score":
                automatic_score,

            "trust_score":
                trust_score,

            "risk_level":
                risk_level,

            "status":
                submission_status,

            "evaluation_type":
                evaluation_type,

            "submitted_at":
                submitted_at.isoformat()

        }


        print()
        print(
            "========================================"
        )
        print(
            "       PROCTIFY MYSQL EXAM SUBMITTED"
        )
        print(
            "========================================"
        )
        print(
            "Submission ID:",
            submission_id
        )
        print(
            "Student:",
            student_id
        )
        print(
            "Exam:",
            exam["exam_name"]
        )
        print(
            "Trust Score:",
            trust_score
        )
        print(
            "Automatic Score:",
            automatic_score
        )
        print(
            "Final Status:",
            submission_status
        )
        print(
            "========================================"
        )
        print()


        return jsonify({

            "success":
                True,

            "message":
                (
                    "Exam evaluated and submitted successfully."
                    if submission_status == "EVALUATED"
                    else
                    "Exam submitted and sent for teacher review."
                ),

            "submission":
                submission_response

        })


    except mysql.connector.Error as error:

        if connection is not None:
            connection.rollback()

        print(
            "MySQL exam submission error:",
            error
        )

        return jsonify({

            "success": False,

            "error":
                str(error)

        }), 500


    except Exception as error:

        if connection is not None:
            connection.rollback()

        print(
            "Exam submission error:",
            error
        )

        return jsonify({

            "success": False,

            "error":
                str(error)

        }), 500

    finally:

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


# ============================================================
# ============================================================
# MONITOR STUDENT
# ============================================================

@app.route(
    "/monitor/<student_id>"
)
def monitor_student(student_id):
    connection = None
    cursor = None

    try:
        connection = get_database_connection()
        if connection is None:
            return "Unable to connect to MySQL", 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                ls.*,
                (
                    SELECT REPLACE(v.violation_type, '_', ' ')
                    FROM violations v
                    WHERE v.session_id = ls.session_id
                    ORDER BY v.timestamp DESC
                    LIMIT 1
                ) AS last_event
            FROM live_students ls
            WHERE ls.student_id = %s
              AND UPPER(ls.status) = 'ONLINE'
              AND ls.last_update >= (NOW() - INTERVAL 5 SECOND)
            LIMIT 1
        """, (str(student_id),))

        selected_student = cursor.fetchone()

        if selected_student is None:
            return "Student is not currently live.", 404

        selected_student = normalize_student(selected_student)
        session_id = selected_student.get("session_id", "")

        violations = get_student_violations(session_id)
        evidence = get_student_evidence(session_id)
        trust_history = get_trust_score_history(session_id)

        return render_template(
            "monitor.html",
            student=selected_student,
            violations=violations,
            evidence=evidence,
            trust_history=trust_history
        )

    except Exception as error:
        print("Monitor student error:", error)
        return "Unable to load student monitor.", 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


# ============================================================
# STUDENT ENROLLMENT - MYSQL
# ============================================================
#
# The teacher can enroll any number of students. Every enrolled
# student is stored in the MySQL `students` table using the existing
# columns:
#   student_id, student_name, username, password, created_at
#
# POST /api/students/enroll
# Body:
# {
#     "student_id": "STUDENT_001",
#     "student_name": "Student Name",
#     "username": "student01",
#     "password": "password"
# }
#
# GET /api/enrolled-students
# Returns all students enrolled by the teacher.
# ============================================================

@app.route(
    "/api/students/enroll",
    methods=["POST"]
)
def enroll_student():

    connection = None
    cursor = None

    try:

        data = request.get_json(
            silent=True
        ) or {}

        student_id = str(
            data.get(
                "student_id",
                ""
            )
        ).strip()

        student_name = str(
            data.get(
                "student_name",
                ""
            )
        ).strip()

        username = str(
            data.get(
                "username",
                ""
            )
        ).strip()

        password = str(
            data.get(
                "password",
                ""
            )
        ).strip()

        if not student_id:

            return jsonify({
                "success": False,
                "error": "Student ID is required"
            }), 400

        if not student_name:

            return jsonify({
                "success": False,
                "error": "Student name is required"
            }), 400

        if not username:

            return jsonify({
                "success": False,
                "error": "Username is required"
            }), 400

        if not password:

            return jsonify({
                "success": False,
                "error": "Password is required"
            }), 400

        connection = get_database_connection()

        if connection is None:

            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL"
            }), 500

        cursor = connection.cursor(
            dictionary=True
        )

        # --------------------------------------------------------
        # CHECK STUDENT ID
        # --------------------------------------------------------

        cursor.execute(
            """
            SELECT student_id
            FROM students
            WHERE student_id = %s
            LIMIT 1
            """,
            (
                student_id,
            )
        )

        if cursor.fetchone():

            return jsonify({
                "success": False,
                "error": "Student ID already exists"
            }), 409

        # --------------------------------------------------------
        # CHECK USERNAME
        # --------------------------------------------------------

        cursor.execute(
            """
            SELECT username
            FROM students
            WHERE username = %s
            LIMIT 1
            """,
            (
                username,
            )
        )

        if cursor.fetchone():

            return jsonify({
                "success": False,
                "error": "Username already exists"
            }), 409

        # --------------------------------------------------------
        # INSERT STUDENT INTO MYSQL
        # --------------------------------------------------------

        cursor.execute(
            """
            INSERT INTO students (
                student_id,
                student_name,
                username,
                password,
                created_at
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                NOW()
            )
            """,
            (
                student_id,
                student_name,
                username,
                password
            )
        )

        connection.commit()

        enrolled_student = {
            "student_id": student_id,
            "student_name": student_name,
            "username": username
        }

        print()
        print(
            "========================================"
        )
        print(
            "       PROCTIFY STUDENT ENROLLED"
        )
        print(
            "========================================"
        )
        print(
            "Student ID:",
            student_id
        )
        print(
            "Student Name:",
            student_name
        )
        print(
            "Username:",
            username
        )
        print(
            "========================================"
        )
        print()

        return jsonify({
            "success": True,
            "message": "Student enrolled successfully",
            "student": enrolled_student
        }), 201

    except mysql.connector.Error as error:

        if connection is not None:
            connection.rollback()

        print(
            "Student enrollment MySQL error:",
            error
        )

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    except Exception as error:

        if connection is not None:
            connection.rollback()

        print(
            "Student enrollment error:",
            error
        )

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


# ============================================================
# GET ALL ENROLLED STUDENTS - MYSQL
# ============================================================

@app.route(
    "/api/enrolled-students",
    methods=["GET"]
)
def get_enrolled_students():

    connection = None
    cursor = None

    try:

        connection = get_database_connection()

        if connection is None:

            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL",
                "count": 0,
                "students": []
            }), 500

        cursor = connection.cursor(
            dictionary=True
        )

        cursor.execute(
            """
            SELECT
                student_id,
                student_name,
                username,
                created_at
            FROM students
            ORDER BY created_at DESC
            """
        )

        students = cursor.fetchall()

        for student in students:

            created_at = student.get(
                "created_at"
            )

            if isinstance(
                created_at,
                datetime
            ):

                student[
                    "created_at"
                ] = created_at.isoformat()

        return jsonify({
            "success": True,
            "count": len(students),
            "students": students
        })

    except mysql.connector.Error as error:

        print(
            "Enrolled students MySQL read error:",
            error
        )

        return jsonify({
            "success": False,
            "error": str(error),
            "count": 0,
            "students": []
        }), 500

    except Exception as error:

        print(
            "Enrolled students read error:",
            error
        )

        return jsonify({
            "success": False,
            "error": str(error),
            "count": 0,
            "students": []
        }), 500

    finally:

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return jsonify({

        "status":
            "ONLINE",

        "system":
            "PROCTIFY",

        "time":
            datetime.now().isoformat()

    })

# ============================================================
# STUDENT AUTHENTICATION - MYSQL
# ============================================================

@app.route(
    "/api/student/login",
    methods=["POST"]
)
def student_login():

    connection = None
    cursor = None

    try:

        data = request.get_json(
            silent=True
        ) or {}

        username = str(
            data.get(
                "username",
                ""
            )
        ).strip()

        password = str(
            data.get(
                "password",
                ""
            )
        ).strip()


        # ----------------------------------------------------
        # VALIDATE INPUT
        # ----------------------------------------------------

        if not username or not password:

            return jsonify({

                "success": False,

                "error":
                    "Username and password are required"

            }), 400


        # ----------------------------------------------------
        # CONNECT TO MYSQL
        # ----------------------------------------------------

        connection = get_database_connection()


        if connection is None:

            return jsonify({

                "success": False,

                "error":
                    "Unable to connect to MySQL"

            }), 500


        cursor = connection.cursor(
            dictionary=True
        )


        # ----------------------------------------------------
        # CHECK STUDENT CREDENTIALS
        # ----------------------------------------------------

        cursor.execute(

            """
            SELECT
                student_id,
                student_name,
                username
            FROM students
            WHERE username = %s
            AND password = %s
            LIMIT 1
            """,

            (
                username,
                password
            )

        )


        student = cursor.fetchone()


        # ----------------------------------------------------
        # INVALID LOGIN
        # ----------------------------------------------------

        if student is None:

            return jsonify({

                "success": False,

                "error":
                    "Invalid username or password"

            }), 401


        # ----------------------------------------------------
        # VALID LOGIN
        # ----------------------------------------------------

        session["student_id"] = str(student["student_id"])
        session["student_name"] = str(student["student_name"])
        session["username"] = str(student["username"])

        return jsonify({

            "success": True,

            "message":
                "Login successful",

            "student": {

                "student_id":
                    student["student_id"],

                "student_name":
                    student["student_name"],

                "username":
                    student["username"]

            }

        }), 200


    except mysql.connector.Error as error:

        print(
            "Student login MySQL error:",
            error
        )

        return jsonify({

            "success": False,

            "error":
                "Database authentication error"

        }), 500


    except Exception as error:

        print(
            "Student login error:",
            error
        )

        return jsonify({

            "success": False,

            "error":
                "Authentication failed"

        }), 500


    finally:

        if cursor is not None:

            cursor.close()


        if connection is not None:

            connection.close()
# ============================================================
# RUN SERVER
# ============================================================

if __name__ == "__main__":

    print()
    print(
        "========================================"
    )
    print(
        "       PROCTIFY TEACHER DASHBOARD"
    )
    print(
        "========================================"
    )
    print(
        "Live status file:"
    )
    print(
        LIVE_STATUS_FILE
    )
    print()
    print(
        "Dashboard:"
    )
    print(
        "http://127.0.0.1:5000"
    )
    print(
        "========================================"
    )
    print()

    app.run(

        host="0.0.0.0",

        port=5000,

        debug=True

    )