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
from dashboard import webrtc
app.register_blueprint(webrtc.webrtc_bp)
from dashboard.batch_reports import batch_reports_bp
app.register_blueprint(batch_reports_bp)
app.secret_key = os.environ.get("PROCTIFY_SECRET_KEY", "proctify-dev-secret-key")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),

    # Re-read templates on every render so HTML edits show up
    # without restarting the server.
    TEMPLATES_AUTO_RELOAD=True
)

# ============================================================
# TEACHER AUTHENTICATION GUARD
# ============================================================
# Teacher pages and teacher-only APIs now require a real login.
# Student pages, student APIs and the student-agent monitoring
# endpoints stay exactly as they were.
# ============================================================

TEACHER_ONLY_PAGES = (
    "/teacher",
    "/exams",
    "/exam/",
    "/host-exam",
    "/monitor/",
    "/submission-report/"
)

TEACHER_ONLY_APIS = (
    "/api/status",
    "/api/students",
    "/api/summary",
    "/api/system",
    "/api/enrolled-students",
    "/api/submissions",
    "/api/reports",
    "/api/evidence/",
    "/api/live/video/",
    "/api/monitor/violations",
    "/api/excel/",
    "/api/batches"
)


def teacher_logged_in():

    return bool(session.get("teacher_id"))


@app.before_request
def require_teacher_login():

    path = request.path or ""

    # The student-agent frame upload is a POST and must stay open.
    if path == "/api/live/video/frame":
        return None

    if path.startswith(TEACHER_ONLY_PAGES):

        if not teacher_logged_in():
            return redirect("/")

        return None

    if path.startswith(TEACHER_ONLY_APIS):

        if not teacher_logged_in():

            return jsonify({
                "success": False,
                "error": "Teacher login required."
            }), 401

        return None

    return None

# ============================================================
# MYSQL CONFIGURATION
# ============================================================

DB_CONFIG = {

    "host": os.environ.get("PROCTIFY_DB_HOST", "localhost"),

    "user": os.environ.get("PROCTIFY_DB_USER", "root"),

    "password": os.environ.get("PROCTIFY_DB_PASSWORD", "aasmaan@14"),

    "database": os.environ.get("PROCTIFY_DB_NAME", "proctify_db")

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
# SCHEMA SELF-HEAL
# ============================================================
# The application owns its own schema so a fresh MySQL server
# always works without a manual migration step.
#
#   1. `teachers` backs the real teacher login.
#   2. exam_sessions.status must allow 'TERMINATED', otherwise the
#      forced termination that fires at Trust Score 0 is rejected by
#      MySQL strict mode (error 1265: data truncated).
# ============================================================

TEACHER_DEFAULT_USERNAME = os.environ.get(
    "PROCTIFY_TEACHER_USER",
    "teacher"
)

TEACHER_DEFAULT_PASSWORD = os.environ.get(
    "PROCTIFY_TEACHER_PASSWORD",
    "teacher123"
)


def ensure_mysql_schema():

    connection = get_database_connection()

    if connection is None:

        print("SCHEMA: MySQL unavailable - schema not verified.")

        return False

    cursor = None

    try:

        cursor = connection.cursor()

        # ----------------------------------------------------
        # TEACHERS
        # ----------------------------------------------------

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS teachers (
                teacher_id   VARCHAR(50)  NOT NULL PRIMARY KEY,
                teacher_name VARCHAR(100) NOT NULL,
                username     VARCHAR(100) NOT NULL UNIQUE,
                password     VARCHAR(255) NOT NULL,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("SELECT COUNT(*) FROM teachers")

        if cursor.fetchone()[0] == 0:

            cursor.execute("""
                INSERT INTO teachers
                    (teacher_id, teacher_name, username, password)
                VALUES (%s, %s, %s, %s)
            """, (
                "TEACHER_001",
                "Professor",
                TEACHER_DEFAULT_USERNAME,
                TEACHER_DEFAULT_PASSWORD
            ))

            print(
                "SCHEMA: seeded teacher account ->",
                TEACHER_DEFAULT_USERNAME,
                "/",
                TEACHER_DEFAULT_PASSWORD
            )

        # ----------------------------------------------------
        # BATCHES (grouping for Excel-enrolled students)
        # ----------------------------------------------------

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS batches (
                batch_id INT AUTO_INCREMENT PRIMARY KEY,
                batch_name VARCHAR(255) NOT NULL,
                source_filename VARCHAR(255) DEFAULT NULL,
                student_count INT DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS batch_members (
                id INT AUTO_INCREMENT PRIMARY KEY,
                batch_id INT NOT NULL,
                student_id VARCHAR(100) NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_batch_student (batch_id, student_id),
                FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # ----------------------------------------------------
        # exam_sessions.status  ->  allow TERMINATED
        # ----------------------------------------------------

        cursor.execute("""
            SELECT COLUMN_TYPE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'exam_sessions'
              AND COLUMN_NAME = 'status'
        """)

        row = cursor.fetchone()

        if row:

            column_type = str(row[0] or "").upper()

            if "TERMINATED" not in column_type:

                cursor.execute("""
                    ALTER TABLE exam_sessions
                    MODIFY COLUMN status
                        ENUM('ONGOING','COMPLETED','TERMINATED')
                        NOT NULL DEFAULT 'ONGOING'
                """)

                print("SCHEMA: exam_sessions.status now allows TERMINATED.")

        connection.commit()

        return True

    except Exception as error:

        print("SCHEMA ERROR:", error)

        try:
            connection.rollback()
        except Exception:
            pass

        return False

    finally:

        if cursor is not None:
            cursor.close()

        connection.close()


# ============================================================
# PATHS
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

LIVE_MONITOR_FILE = os.path.join(
    BASE_DIR,
    "detectors",
    "live_monitor.py"
)

# Student-side agent. The AI monitor runs on the student's PC,
# not on this Flask/teacher server.
#
# In a 2-PC LAN setup the student agent lives on a different machine,
# so this must be the LAN IP of that machine, e.g.:
#
#   set PROCTIFY_AGENT_URL=http://192.168.1.100:8765
#
STUDENT_AGENT_URL = os.environ.get(
    "PROCTIFY_AGENT_URL",
    "http://127.0.0.1:8765"
)

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

# Maximum persistent monitoring records per exam session.
MAX_VIOLATIONS_PER_SESSION = 15
MAX_EVIDENCE_PER_SESSION = 15


# ============================================================
# GET STUDENTS
# ============================================================
# MySQL `live_students` is the only source of live monitoring
# state. The legacy JSON fallback has been removed so a stale
# file can never feed junk into the dashboard.
# ============================================================

def get_students():

    cleaned_students = []

    for student in get_students_from_database():

        if isinstance(student, dict):

            cleaned_students.append(student.copy())

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
    # Availability flags — MySQL stores 0/1 integers.
    # JavaScript checks with strict equality (=== true).
    # Convert to proper booleans so the dashboard dots work.
    # --------------------------------------------------------

    for flag in (
        "camera_available",
        "audio_available",
        "ai_available",
        "tab_available",
    ):
        student[flag] = bool(student.get(flag))


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
# STRIP CORRECT ANSWERS FOR STUDENT-FACING RESPONSES
# ============================================================
# Students must never receive the correct_answer field. This
# helper mutates the exam dict in place and is applied whenever
# the request is served to a logged-in student.

def strip_correct_answers_from_exam(exam):

    questions = exam.get("questions")

    if not isinstance(questions, list):
        return exam

    for question in questions:

        if isinstance(question, dict):

            question.pop("correct_answer", None)
            question.pop("correct_answer_index", None)
            question.pop("explanation", None)

    return exam

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


@app.after_request
def set_cache_headers(response):
    """
    Prevent browsers from serving active exam pages from
    back-forward cache (bfcache).  Active exam pages must
    always re-execute the server-side submission check.
    """
    path = request.path or ""

    if path.startswith("/student/exam/"):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    if path.startswith("/student/precheck/"):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    if path.startswith("/api/exams/") and path.endswith("/submit"):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    return response


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
# TEACHER AUTHENTICATION - MYSQL
# ============================================================

@app.route(
    "/api/teacher/login",
    methods=["POST"]
)
def teacher_login():

    connection = None
    cursor = None

    try:

        data = request.get_json(silent=True) or {}

        username = str(data.get("username", "")).strip()
        password = str(data.get("password", "")).strip()

        if not username or not password:

            return jsonify({
                "success": False,
                "error": "Username and password are required."
            }), 400

        connection = get_database_connection()

        if connection is None:

            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT teacher_id, teacher_name, username, password
            FROM teachers
            WHERE username = %s
            LIMIT 1
        """, (username,))

        teacher = cursor.fetchone()

        if teacher is None or str(teacher.get("password")) != password:

            return jsonify({
                "success": False,
                "error": "Invalid username or password."
            }), 401

        session.permanent = True

        session["teacher_id"] = str(teacher["teacher_id"])
        session["teacher_name"] = str(teacher["teacher_name"])

        return jsonify({
            "success": True,
            "teacher": {
                "teacher_id": teacher["teacher_id"],
                "teacher_name": teacher["teacher_name"],
                "username": teacher["username"]
            },
            "redirect": "/teacher"
        })

    except Exception as error:

        print("Teacher login error:", error)

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


@app.route(
    "/api/teacher/logout",
    methods=["POST", "GET"]
)
def teacher_logout():

    session.pop("teacher_id", None)
    session.pop("teacher_name", None)

    return jsonify({"success": True})


@app.route("/api/teacher/session")
def teacher_session():

    if not teacher_logged_in():

        return jsonify({
            "success": True,
            "authenticated": False
        })

    return jsonify({
        "success": True,
        "authenticated": True,
        "teacher": {
            "teacher_id": session.get("teacher_id"),
            "teacher_name": session.get("teacher_name")
        }
    })


# ============================================================
# TEACHER DASHBOARD
# ============================================================

@app.route("/teacher")
def teacher_dashboard():

    return render_template(
        "index.html",
        teacher_name=session.get("teacher_name") or "Professor"
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
        student_name=session.get("student_name", ""),
        face_verified=bool(session.get("face_verified"))
    )


@app.route(
    "/api/student/logout",
    methods=["POST", "GET"]
)
def student_logout():

    session.pop("student_id", None)
    session.pop("student_name", None)
    session.pop("student_username", None)
    session.pop("face_verified", None)
    session.pop("active_session_id", None)
    session.pop("active_exam_id", None)

    return jsonify({"success": True})


@app.route(
    "/api/student/verify-status"
)
def student_verify_status():

    student_id = session.get("student_id")

    if not student_id:

        return jsonify({
            "success": False,
            "error": "Student login required."
        }), 401

    profile_path = os.path.join(
        FACE_PROFILES_DIR,
        f"{student_id}.jpg"
    )

    return jsonify({
        "success": True,
        "student_id": student_id,
        "face_profile_exists": os.path.isfile(profile_path),
        "face_verified": bool(session.get("face_verified"))
    })


@app.route(
    "/api/student/enroll-face",
    methods=["POST"]
)
def student_enroll_face():
    """
    First-time face enrolment.

    A student with no stored profile captures one frame from their own
    camera and it becomes their reference image. This is what makes the
    verification step reachable on a fresh installation instead of
    dead-ending on "No enrolled face profile found".
    """

    student_id = session.get("student_id")

    if not student_id:

        return jsonify({
            "success": False,
            "error": "Student login required."
        }), 401

    try:

        data = request.get_json(silent=True) or {}

        image_data = data.get("image")

        if not image_data:

            return jsonify({
                "success": False,
                "error": "No camera image received."
            }), 400

        if "," in str(image_data):

            image_data = str(image_data).split(",", 1)[1]

        image_array = np.frombuffer(
            base64.b64decode(image_data),
            dtype=np.uint8
        )

        image = cv2.imdecode(
            image_array,
            cv2.IMREAD_COLOR
        )

        if image is None:

            return jsonify({
                "success": False,
                "error": "Invalid camera image."
            }), 400

        descriptor = create_face_descriptor(image)

        if descriptor is None:

            return jsonify({
                "success": False,
                "error": "No face detected. Look at the camera and try again."
            }), 400

        os.makedirs(FACE_PROFILES_DIR, exist_ok=True)

        profile_path = os.path.join(
            FACE_PROFILES_DIR,
            f"{student_id}.jpg"
        )

        if not cv2.imwrite(profile_path, image):

            return jsonify({
                "success": False,
                "error": "Unable to save the face profile."
            }), 500

        session["face_verified"] = True

        return jsonify({
            "success": True,
            "enrolled": True,
            "student_id": student_id
        })

    except Exception as error:

        print("Face enrolment error:", error)

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


# ============================================================
# STUDENT FACE VERIFICATION
# ============================================================

@app.route("/student/verify")
def student_verify():

    if not session.get("student_id"):
        return redirect("/")

    return render_template(
        "student_verify.html",
        student_id=session.get("student_id")
    )


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

        # Only students who were assigned this exam may enter it.
        cursor.execute(
            """
            SELECT assignment_id
            FROM exam_assignments
            WHERE exam_id = %s
              AND student_id = %s
            LIMIT 1
            """,
            (exam_id, str(student_id))
        )

        if cursor.fetchone() is None:
            return "This exam is not assigned to your account.", 403

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
                SELECT assignment_id
                FROM exam_assignments
                WHERE exam_id = %s
                  AND student_id = %s
                LIMIT 1
                """,
                (exam_id, str(student_id))
            )

            if cursor.fetchone() is None:
                return "This exam is not assigned to your account.", 403

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
    "/api/student/exam/<exam_id>/status",
    methods=["GET"]
)
def api_student_exam_status(exam_id):
    """
    Lightweight check: has this student already submitted
    or been terminated for this exam?  Used by the exam
    page to revalidate after bfcache restore.
    """
    student_id = session.get("student_id")
    if not student_id:
        return jsonify({
            "submitted": True,
            "reason": "not_logged_in"
        })

    connection = None
    cursor = None
    try:
        connection = get_database_connection()
        if connection is None:
            return jsonify({
                "submitted": False,
                "reason": "db_error"
            })

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT submission_id, status
            FROM exam_submissions
            WHERE exam_id = %s
              AND student_id = %s
            ORDER BY submitted_at DESC
            LIMIT 1
            """,
            (str(exam_id), str(student_id))
        )
        sub = cursor.fetchone()
        if sub is not None:
            return jsonify({
                "submitted": True,
                "reason": "already_submitted",
                "submission_status": sub.get("status")
            })

        cursor.execute(
            """
            SELECT status
            FROM exam_sessions
            WHERE student_id = %s
              AND exam_name = (
                  SELECT exam_name
                  FROM exams
                  WHERE exam_id = %s
                  LIMIT 1
              )
            ORDER BY start_time DESC
            LIMIT 1
            """,
            (str(student_id), str(exam_id))
        )
        sess = cursor.fetchone()
        if sess is not None and (
            sess.get("status") or ""
        ).upper() == "TERMINATED":
            return jsonify({
                "submitted": True,
                "reason": "terminated"
            })

        return jsonify({
            "submitted": False
        })

    except Exception as error:
        print("Exam status check error:", error)
        return jsonify({
            "submitted": False,
            "reason": "error"
        })
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


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


@app.route(
    "/api/student/live/violations",
    methods=["GET"]
)
def api_student_live_violations():
    """
    Student-facing endpoint: return recent violations
    and current trust score for the logged-in student's
    active exam session.
    """
    student_id = session.get("student_id")

    if not student_id:
        return jsonify({
            "success": False,
            "error": "Student not logged in"
        }), 401

    connection = None
    cursor = None

    try:
        connection = get_database_connection()
        if connection is None:
            return jsonify({
                "success": False,
                "error": "Database unavailable"
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT session_id, trust_score, risk_level "
            "FROM live_students "
            "WHERE student_id = %s "
            "AND status IN ('ONLINE','TERMINATED') "
            "ORDER BY last_update DESC LIMIT 1",
            (str(student_id),)
        )
        live_row = cursor.fetchone()

        if not live_row:
            return jsonify({
                "success": True,
                "trust_score": None,
                "risk_level": None,
                "violations": [],
                "terminated": False
            })

        session_id = live_row.get("session_id")
        trust_score = live_row.get("trust_score")
        risk_level = live_row.get("risk_level")
        terminated = (
            str(live_row.get("risk_level", ""))
            == "TERMINATED"
            or trust_score == 0
        )

        violations = []

        if session_id:
            since = request.args.get("since", "")
            if since:
                cursor.execute(
                    "SELECT id, violation_type, severity, "
                    "penalty, description, timestamp "
                    "FROM violations "
                    "WHERE session_id = %s "
                    "AND timestamp > %s "
                    "ORDER BY timestamp DESC "
                    "LIMIT 10",
                    (str(session_id), since)
                )
            else:
                cursor.execute(
                    "SELECT id, violation_type, severity, "
                    "penalty, description, timestamp "
                    "FROM violations "
                    "WHERE session_id = %s "
                    "ORDER BY timestamp DESC "
                    "LIMIT 5",
                    (str(session_id),)
                )
            violations = cursor.fetchall()

            for v in violations:
                for key, val in list(v.items()):
                    if isinstance(val, datetime):
                        v[key] = val.isoformat()

        return jsonify({
            "success": True,
            "trust_score": trust_score,
            "risk_level": risk_level,
            "terminated": (
                str(live_row.get("risk_level", ""))
                in ("TERMINATED",)
                or trust_score == 0
            ),
            "violations": violations
        })

    except Exception as error:
        print("Student violations error:", error)
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route(
    "/api/student/exam-result/<exam_id>",
    methods=["GET"]
)
def api_student_exam_result(exam_id):
    """
    Student-facing: return their submission result
    for a specific exam.
    """
    student_id = session.get("student_id")
    if not student_id:
        return jsonify({
            "success": False,
            "error": "Student not logged in"
        }), 401

    connection = None
    cursor = None
    try:
        connection = get_database_connection()
        if connection is None:
            return jsonify({
                "success": False,
                "error": "Database unavailable"
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT exam_name FROM exams "
            "WHERE exam_id = %s LIMIT 1",
            (str(exam_id),)
        )
        exam_row = cursor.fetchone()
        exam_name = (
            exam_row.get("exam_name", "")
            if exam_row else ""
        )

        cursor.execute(
            """
            SELECT submission_id, exam_id,
                   student_id, session_id, score,
                   teacher_marks, trust_score,
                   total_questions, submitted_at,
                   evaluated_at, status,
                   evaluation_type, teacher_feedback
            FROM exam_submissions
            WHERE exam_id = %s
              AND student_id = %s
            ORDER BY submission_id DESC
            LIMIT 1
            """,
            (str(exam_id), str(student_id))
        )
        sub = cursor.fetchone()

        if sub is None:
            return jsonify({
                "success": True,
                "exam_name": exam_name,
                "submission": None
            })

        for key, val in list(sub.items()):
            if isinstance(val, datetime):
                sub[key] = val.isoformat()

        return jsonify({
            "success": True,
            "exam_name": exam_name,
            "submission": sub
        })

    except Exception as error:
        print("Student result error:", error)
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
                s.student_id,
                s.student_name,
                s.username
            FROM students s
            LEFT JOIN batch_members bm ON bm.student_id = s.student_id
            WHERE bm.id IS NULL
            ORDER BY s.created_at DESC
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

    if not teacher_logged_in():

        return jsonify({
            "success": False,
            "error": "Teacher login required."
        }), 401

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


        # ----------------------------------------------------
        # STUDENT ID FROM THE FLASK SESSION
        # ----------------------------------------------------
        # The student identity is the server-side authenticated
        # session. A browser-supplied student_id is never trusted.

        student_id = session.get(
            "student_id"
        )

        if not student_id:

            return jsonify({

                "success": False,

                "verified": False,

                "error":
                    "Student login required."

            }), 401

        student_id = str(
            student_id
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

            # A student with no stored profile can enrol one from this
            # same page instead of hitting a dead end.
            return jsonify({

                "success": False,

                "verified": False,

                "profile_missing": True,

                "can_enroll": True,

                "error":
                    "No face profile has been enrolled for this account yet."

            }), 200


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

        if verified:

            session["face_verified"] = True

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
    scored_count = 0

    for student in students:

        score = student.get(
            "trust_score",
            None
        )

        # A missing or non-numeric score is never fabricated as 100.
        # A genuine score of 0 is preserved as 0.
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = None

        risk = student.get(
            "risk_level"
        )

        if risk is None:
            risk = calculate_risk(
                score if score is not None else 100
            )

        risk = str(risk or "").upper()

        if risk == "LOW":

            low_risk += 1

        elif risk == "MEDIUM":

            medium_risk += 1

        else:

            high_risk += 1

        if score is not None:

            total_score += score
            scored_count += 1


    # --------------------------------------------------------
    # Average score
    # --------------------------------------------------------

    if scored_count > 0:

        average_score = round(
            total_score /
            scored_count
        )

    else:

        average_score = 0


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
    """
    System information for the teacher Settings panel.
    Everything is read from MySQL - no JSON files are involved.
    """

    connection = None
    cursor = None

    stats = {
        "database": DB_CONFIG["database"],
        "sessions": 0,
        "enrolled_students": 0,
        "exams": 0,
        "submissions": 0,
        "violations": 0,
        "evidence": 0,
        "live_students": 0
    }

    try:

        connection = get_database_connection()

        if connection is not None:

            cursor = connection.cursor()

            for key, table in (
                ("sessions", "exam_sessions"),
                ("enrolled_students", "students"),
                ("exams", "exams"),
                ("submissions", "exam_submissions"),
                ("violations", "violations"),
                ("evidence", "evidence"),
                ("live_students", "live_students")
            ):

                cursor.execute(f"SELECT COUNT(*) FROM {table}")

                stats[key] = int(cursor.fetchone()[0])

        students = get_students()

        online_count = sum(
            1
            for student in students
            if str(student.get("status") or "").upper() == "ONLINE"
        )

        return jsonify({

            **stats,

            "success": True,

            "system": "PROCTIFY",

            "students": len(students),

            "online_students": online_count,

            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        })

    except Exception as error:

        print("System info error:", error)

        return jsonify({

            **stats,

            "success": False,

            "error": str(error)

        }), 500

    finally:

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()

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

        selected_batches = data.get(
            "selected_batches",
            []
        )

        if not isinstance(selected_batches, list):
            selected_batches = []

        selected_batches = [
            int(batch_id)
            for batch_id in selected_batches
            if str(batch_id).strip()
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

        if not selected_students and not selected_batches:

            return jsonify({
                "success": False,
                "error": "At least one student or batch must be assigned"
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
        # RESOLVE BATCH MEMBERS
        # ----------------------------------------------------

        batch_student_ids = set()
        student_batch_map = {}

        for batch_id in selected_batches:

            cursor.execute(
                "SELECT batch_id, batch_name "
                "FROM batches "
                "WHERE batch_id = %s "
                "LIMIT 1",
                (batch_id,)
            )

            batch = cursor.fetchone()

            if batch is None:
                continue

            cursor.execute(
                "SELECT bm.student_id "
                "FROM batch_members bm "
                "WHERE bm.batch_id = %s",
                (batch_id,)
            )

            for member in cursor.fetchall():
                sid = str(member[0])

                cursor.execute(
                    "SELECT student_id "
                    "FROM students "
                    "WHERE student_id = %s "
                    "LIMIT 1",
                    (sid,)
                )

                if cursor.fetchone() is not None:
                    batch_student_ids.add(sid)
                    student_batch_map[sid] = int(batch_id)


        # ----------------------------------------------------
        # COMBINE + DEDUPLICATE
        # ----------------------------------------------------

        all_student_ids = list(
            set(selected_students) | batch_student_ids
        )

        if not all_student_ids:

            return jsonify({
                "success": False,
                "error": (
                    "Selected batch(es) contain "
                    "no valid students."
                )
            }), 400


        # ----------------------------------------------------
        # SAVE EXAM -> STUDENT ASSIGNMENTS
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS exam_assignments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                exam_id VARCHAR(100) NOT NULL,
                student_id VARCHAR(100) NOT NULL,
                batch_id INT DEFAULT NULL,
                assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_exam_student (exam_id, student_id)
            )
            """
        )

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'exam_assignments'
              AND COLUMN_NAME = 'batch_id'
        """
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "ALTER TABLE exam_assignments "
                "ADD COLUMN batch_id INT DEFAULT NULL "
                "AFTER student_id"
            )

        assignment_query = """
            INSERT IGNORE INTO exam_assignments
                (exam_id, student_id, batch_id)
            VALUES (%s, %s, %s)
        """

        assigned_count = 0

        for student_id in all_student_ids:
            batch_for_student = student_batch_map.get(
                student_id
            )
            cursor.execute(
                assignment_query,
                (exam_id, student_id, batch_for_student)
            )
            if cursor.rowcount == 1:
                assigned_count += 1

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

            "assigned_count":
                assigned_count,

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

            # Teachers see EVERY exam, including EXPIRED and COMPLETED
            # ones, so they can review and delete old examinations.
            # Everyone else keeps the previous non-expired view.
            if teacher_logged_in():

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
                        (
                            SELECT COUNT(*)
                            FROM exam_assignments a
                            WHERE a.exam_id = e.exam_id
                        ) AS assigned_count,
                        (
                            SELECT COUNT(*)
                            FROM exam_submissions s
                            WHERE s.exam_id = e.exam_id
                        ) AS submission_count
                    FROM exams e
                    ORDER BY e.created_at DESC
                """

                cursor.execute(select_query)

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


            # A logged-in student must never receive correct_answer.
            if session.get("student_id"):
                strip_correct_answers_from_exam(exam)


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


        # A logged-in student must never receive correct_answer.
        if session.get("student_id"):
            strip_correct_answers_from_exam(exam)


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

    connection = None
    cursor = None

    try:

        # ----------------------------------------------------
        # STUDENT ID FROM THE FLASK SESSION
        # ----------------------------------------------------
        # The student identity is the server-side session, never a
        # browser-supplied value. Reject the request if the student
        # is not logged in.

        student_id = session.get(
            "student_id"
        )

        if not student_id:

            return jsonify({
                "success": False,
                "error": "Student login required."
            }), 401

        student_id = str(student_id).strip()

        connection = get_database_connection()

        if connection is None:

            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

        cursor = connection.cursor(dictionary=True)


        # ----------------------------------------------------
        # VERIFY EXAM EXISTS AND IS ACTIVE (MYSQL)
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT exam_id, exam_name, status
            FROM exams
            WHERE exam_id = %s
            LIMIT 1
            """,
            (exam_id,)
        )

        exam = cursor.fetchone()

        if exam is None:

            return jsonify({
                "success": False,
                "error": "Exam not found"
            }), 404

        if str(
            exam.get("status", "")
        ).upper() != "ACTIVE":

            return jsonify({
                "success": False,
                "error": "Exam is not active"
            }), 400


        # ----------------------------------------------------
        # VERIFY ASSIGNMENT
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT assignment_id
            FROM exam_assignments
            WHERE exam_id = %s
              AND student_id = %s
            LIMIT 1
            """,
            (exam_id, student_id)
        )

        if cursor.fetchone() is None:

            return jsonify({
                "success": False,
                "error": "This exam is not assigned to the logged-in student."
            }), 403


        # ----------------------------------------------------
        # FIND ACTIVE MONITORING SESSION
        # ----------------------------------------------------

        session_id = session.get(
            "active_session_id"
        )

        cursor.execute(
            """
            SELECT session_id, trust_score, risk_level, status
            FROM live_students
            WHERE student_id = %s
            LIMIT 1
            """,
            (student_id,)
        )

        live_row = cursor.fetchone()

        if live_row is None or str(
            (live_row.get("status") or "").upper()
        ) != "ONLINE":

            return jsonify({
                "success": False,
                "error": "Student monitoring session not found"
            }), 404

        if not session_id:
            session_id = live_row.get("session_id")

        session_id = str(session_id or "")

        if not session_id:
            session_id = str(
                live_row.get("session_id") or ""
            )


        # ----------------------------------------------------
        # TAB SWITCH PENALTY
        # ----------------------------------------------------
        # Consistent with monitoring_engine.PENALTIES["TAB_SWITCH"] = 10.

        penalty = 10

        try:
            current_trust = int(
                live_row.get("trust_score", 100)
            )
        except (ValueError, TypeError):
            current_trust = 100

        new_trust = max(0, current_trust - penalty)

        if new_trust >= 80:
            new_risk = "LOW"
        elif new_trust >= 50:
            new_risk = "MEDIUM"
        else:
            new_risk = "HIGH"


        # ----------------------------------------------------
        # RECORD VIOLATION (MYSQL)
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
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                session_id,
                "TAB_SWITCH",
                "MEDIUM",
                penalty,
                "Student switched away from the examination tab"
            )
        )

        cursor.execute(
            """
            INSERT INTO trust_score_history
            (
                session_id,
                old_score,
                new_score,
                reason
            )
            VALUES (%s, %s, %s, %s)
            """,
            (
                session_id,
                current_trust,
                new_trust,
                "TAB_SWITCH"
            )
        )


        # ----------------------------------------------------
        # UPDATE LIVE STUDENT ROW (MYSQL)
        # ----------------------------------------------------

        cursor.execute(
            """
            UPDATE live_students
            SET
                trust_score = %s,
                risk_level = %s,
                last_event = 'TAB SWITCH',
                last_update = NOW(),
                tab_available = 0
            WHERE student_id = %s
              AND session_id = %s
            """,
            (new_trust, new_risk, student_id, session_id)
        )


        # ----------------------------------------------------
        # CURRENT TAB SWITCH COUNT FOR THIS SESSION
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT COUNT(*) AS c
            FROM violations
            WHERE session_id = %s
              AND violation_type = 'TAB_SWITCH'
            """,
            (session_id,)
        )

        tab_switch_count = int(
            (cursor.fetchone() or {}).get("c", 0) or 0
        )

        connection.commit()

        print(
            "TAB SWITCH RECORDED:",
            student_id,
            "Exam:",
            exam_id,
            "Session:",
            session_id,
            "Count:",
            tab_switch_count,
            "Trust:",
            new_trust
        )


        return jsonify({

            "success": True,

            "message":
                "Tab switch recorded",

            "student_id":
                student_id,

            "exam_id":
                exam_id,

            "session_id":
                session_id,

            "trust_score":
                new_trust,

            "risk_level":
                new_risk,

            "tab_switch_count":
                tab_switch_count

        })


    except Exception as error:

        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass

        print(
            "Tab switch error:",
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
# COMPLETE EXAM
# ============================================================

@app.route(
    "/api/exams/<exam_id>/complete",
    methods=["POST"]
)
def complete_exam(exam_id):

    if not teacher_logged_in():

        return jsonify({
            "success": False,
            "error": "Teacher login required."
        }), 401


    connection = None
    cursor = None

    try:

        connection = get_database_connection()

        if connection is None:

            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL"
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT exam_id, exam_name, status
            FROM exams
            WHERE exam_id = %s
            LIMIT 1
            """,
            (exam_id,)
        )

        exam = cursor.fetchone()

        if exam is None:

            return jsonify({
                "success": False,
                "error": "Exam not found"
            }), 404

        if str(exam.get("status", "")).upper() == "COMPLETED":

            return jsonify({
                "success": False,
                "error": "Exam is already completed"
            }), 409

        cursor.execute(
            """
            UPDATE exams
            SET status = 'COMPLETED'
            WHERE exam_id = %s
            """,
            (exam_id,)
        )

        connection.commit()

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
            exam.get("exam_name", "Unknown")
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

        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass

        print(
            "Exam completion error:",
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

        # ----------------------------------------------------
        # EXAM QUESTIONS (teacher review of every answer)
        # ----------------------------------------------------
        # This endpoint is teacher-only, so the correct answers are
        # returned here to power the manual evaluation screen.
        # ----------------------------------------------------

        questions = []

        cursor.execute("""
            SELECT questions
            FROM exams
            WHERE exam_id = %s
            LIMIT 1
        """, (str(submission.get("exam_id")),))

        question_row = cursor.fetchone()

        if question_row:

            raw_questions = question_row.get("questions")

            if isinstance(raw_questions, str):

                try:
                    raw_questions = json.loads(raw_questions)
                except Exception:
                    raw_questions = []

            if isinstance(raw_questions, list):
                questions = raw_questions

        answers = submission.get("answers")

        if isinstance(answers, str):

            try:
                answers = json.loads(answers)
            except Exception:
                answers = {}

        if not isinstance(answers, dict):
            answers = {}

        # Build a fully reviewable answer sheet.
        review = []

        for index, question in enumerate(questions):

            if not isinstance(question, dict):
                continue

            key = str(index)

            given = answers.get(key)

            if given is None:
                given = answers.get(index)

            correct = question.get("correct_answer")

            given_text = str(given).strip() if given is not None else ""
            correct_text = str(correct).strip() if correct is not None else ""

            options = question.get("options") if isinstance(
                question.get("options"), dict
            ) else {}

            review.append({
                "index": index,
                "question": question.get("question"),
                "options": options,
                "student_answer": given_text or None,
                "student_answer_text": options.get(given_text) if given_text else None,
                "correct_answer": correct_text or None,
                "correct_answer_text": options.get(correct_text) if correct_text else None,
                "is_correct": bool(
                    given_text and correct_text and
                    given_text.upper() == correct_text.upper()
                )
            })

        submission["answers"] = answers
        submission["questions"] = questions
        submission["answer_review"] = review
        submission["answered_count"] = sum(
            1 for item in review if item["student_answer"]
        )

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

# How long the MJPEG stream keeps waiting for a fresh frame before it
# closes itself. This is what makes the teacher's live feed actually
# stop when a student disconnects or is terminated.
LIVE_VIDEO_IDLE_TIMEOUT = float(
    os.environ.get("PROCTIFY_VIDEO_IDLE_TIMEOUT", "8")
)


def _monitor_json_datetime(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _release_mysql_lock(cursor, lock_name):
    """
    Release a MySQL named lock.

    RELEASE_LOCK returns a result set, so it must be fetched. Leaving it
    unread made the next statement fail with "Unread result found" and
    broke the whole request (evidence upload and violation logging).
    """

    if cursor is None or not lock_name:
        return

    try:
        cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        cursor.fetchone()
    except Exception as error:
        print("MySQL lock release error:", error)


def _safe_close_cursor(cursor):

    if cursor is None:
        return

    try:
        cursor.close()
    except Exception as error:
        print("Cursor close warning:", error)


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

        if status not in {"ONLINE", "OFFLINE", "TERMINATED"}:
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
        raw_old = data.get("old_score")
        old_score = int(raw_old) if raw_old is not None else 100

        raw_new = data.get("new_score")
        new_score = int(raw_new) if raw_new is not None else 100

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

        # Keep persistent violation history capped at 15 records per session.
        # A MySQL named lock prevents two simultaneous monitor requests from
        # both seeing 14 records and inserting a 15th/16th record together.
        lock_name = f"proctify_violation_{session_id}"[:64]
        cursor.execute("SELECT GET_LOCK(%s, 5)", (lock_name,))
        lock_acquired = bool(cursor.fetchone()[0])

        if not lock_acquired:
            return jsonify({
                "success": False,
                "error": "Unable to acquire violation storage lock."
            }), 503

        try:
            cursor.execute("""
                SELECT COUNT(*)
                FROM violations
                WHERE session_id = %s
            """, (session_id,))
            stored_violation_count = int(cursor.fetchone()[0] or 0)

            if stored_violation_count < MAX_VIOLATIONS_PER_SESSION:
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
            else:
                violation_id = None
        finally:
            _release_mysql_lock(cursor, lock_name)

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
            "stored": violation_id is not None,
            "limit_reached": violation_id is None,
            "violation_id": (
                int(violation_id)
                if violation_id is not None
                else None
            )
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
        _safe_close_cursor(cursor)
        if connection is not None:
            connection.close()


@app.route("/api/monitor/evidence", methods=["POST"])
def monitor_evidence():
    connection = None
    cursor = None
    evidence_lock_name = None
    evidence_lock_held = False

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
        connection = get_database_connection()
        if connection is None:
            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

        cursor = connection.cursor()

        # Never create a physical evidence file after the 15-image limit.
        # Hold a MySQL named lock through the count + insert so concurrent
        # evidence uploads cannot push the session above 15 records.
        lock_name = f"proctify_evidence_{session_id}"[:64]
        evidence_lock_name = lock_name
        cursor.execute("SELECT GET_LOCK(%s, 5)", (lock_name,))
        lock_acquired = bool(cursor.fetchone()[0])
        evidence_lock_held = lock_acquired

        if not lock_acquired:
            return jsonify({
                "success": False,
                "error": "Unable to acquire evidence storage lock."
            }), 503

        cursor.execute("""
            SELECT COUNT(*)
            FROM evidence
            WHERE session_id = %s
        """, (session_id,))
        stored_evidence_count = int(cursor.fetchone()[0] or 0)

        if stored_evidence_count >= MAX_EVIDENCE_PER_SESSION:
            _release_mysql_lock(cursor, lock_name)
            evidence_lock_held = False
            return jsonify({
                "success": True,
                "stored": False,
                "limit_reached": True,
                "message": "Maximum 15 evidence images reached."
            })

        evidence_dir = os.path.join(BASE_DIR, "reports", "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        file_path = os.path.join(evidence_dir, filename)

        uploaded.save(file_path)

        if not os.path.isfile(file_path):
            return jsonify({
                "success": False,
                "error": "Evidence image could not be saved."
            }), 500

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

        _release_mysql_lock(cursor, lock_name)
        evidence_lock_held = False

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
            if evidence_lock_held and evidence_lock_name:
                _release_mysql_lock(cursor, evidence_lock_name)
            _safe_close_cursor(cursor)
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


def write_session_report(session_id, student_id, exam_name, extra=None):
    """
    Build the JSON session report for one monitoring session.

    Shared by /api/monitor/report (normal completion) and
    /api/monitor/terminate (Trust Score hit 0), so a terminated
    student ALWAYS has a report the teacher can open.
    """

    extra = extra or {}

    connection = get_database_connection()

    if connection is None:

        return None

    cursor = None

    try:

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

        safe_session_id = str(session_id).replace(" ", "_")

        report_path = os.path.join(
            report_dir,
            f"session_report_{safe_session_id}.json"
        )

        report_data = {
            "system": "PROCTIFY",
            "session_id": session_id,
            "student_id": student_id,
            "exam_name": exam_name,
            "start_time": extra.get("start_time"),
            "end_time": extra.get("end_time"),
            "duration_seconds": extra.get("duration_seconds", 0),
            "cheating_score": extra.get("cheating_score", 0),
            "terminated": bool(extra.get("terminated")),
            "termination_reason": extra.get("termination_reason"),
            "final_trust_score": session_row.get(
                "final_trust_score",
                extra.get("final_trust_score", 100)
            ),
            "final_risk_level": session_row.get(
                "final_risk_level",
                extra.get("final_risk_level", "LOW")
            ),
            "total_violations": len(violations),
            "total_evidence": len(evidence),
            "violations": violations,
            "evidence": evidence
        }

        with open(report_path, "w", encoding="utf-8") as file:

            json.dump(report_data, file, indent=4)

        return report_path

    except Exception as error:

        print("Session report write error:", error)

        return None

    finally:

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


@app.route("/api/monitor/terminate", methods=["POST"])
def monitor_terminate():
    """
    Persist a forced exam termination when the Trust Score reaches zero.
    Normal exam submission and its evaluation workflow are untouched.

    On termination the teacher's live feed for this student is stopped
    immediately (buffered frames are dropped) and a session report is
    generated so it appears in the teacher's Reports panel.
    """
    connection = None
    cursor = None

    try:
        data = request.get_json(silent=True) or {}

        session_id = str(data.get("session_id", "")).strip()
        student_id = str(data.get("student_id", "")).strip()
        exam_name = str(data.get("exam_name", "PROCTIFY EXAM")).strip()
        final_trust_score = max(
            0,
            min(100, int(data.get("final_trust_score", 0) or 0))
        )
        final_risk_level = (
            str(data.get("final_risk_level", "HIGH")).strip()
            or "HIGH"
        )

        if not session_id or not student_id:
            return jsonify({
                "success": False,
                "error": "session_id and student_id are required."
            }), 400

        connection = get_database_connection()
        if connection is None:
            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT start_time
            FROM exam_sessions
            WHERE session_id = %s
            LIMIT 1
        """, (session_id,))
        existing_session = cursor.fetchone() or {}
        start_time = existing_session.get("start_time") or datetime.now()
        end_time = datetime.now()

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
            VALUES (%s, %s, %s, %s, %s, 'TERMINATED', %s, %s)
            ON DUPLICATE KEY UPDATE
                student_id = VALUES(student_id),
                exam_name = VALUES(exam_name),
                end_time = VALUES(end_time),
                status = 'TERMINATED',
                final_trust_score = VALUES(final_trust_score),
                final_risk_level = VALUES(final_risk_level)
        """, (
            session_id,
            student_id,
            exam_name,
            start_time,
            end_time,
            final_trust_score,
            final_risk_level
        ))

        cursor.execute("""
            UPDATE live_students
            SET status = 'TERMINATED',
                trust_score = %s,
                risk_level = %s,
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
        """, (
            final_trust_score,
            final_risk_level,
            student_id,
            session_id
        ))

        # ----------------------------------------------------
        # AUTO-CREATE SUBMISSION FOR TERMINATED EXAMS
        # ----------------------------------------------------
        # When trust score reaches 0 the student cannot submit
        # normally.  Create a submission record so the teacher
        # can see it in the Submissions panel and evaluate it.
        # ----------------------------------------------------

        exam_id_for_submission = None

        try:
            cursor.execute(
                "SELECT exam_id FROM exams "
                "WHERE exam_name = %s LIMIT 1",
                (exam_name,)
            )
            eid_row = cursor.fetchone()
            if eid_row:
                exam_id_for_submission = (
                    eid_row.get("exam_id")
                    if isinstance(eid_row, dict)
                    else eid_row[0]
                )
        except Exception:
            pass

        has_submission = False

        if exam_id_for_submission:
            try:
                cursor.execute(
                    "SELECT submission_id "
                    "FROM exam_submissions "
                    "WHERE session_id = %s LIMIT 1",
                    (session_id,)
                )
                existing = cursor.fetchone()
                if existing:
                    has_submission = True
            except Exception:
                pass

        if exam_id_for_submission and not has_submission:
            try:
                cursor.execute(
                    """
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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        exam_id_for_submission,
                        student_id,
                        session_id,
                        "{}",
                        None,
                        None,
                        final_trust_score,
                        0,
                        end_time,
                        None,
                        "PENDING_REVIEW",
                        "MANUAL",
                        "Auto-created on termination (Trust Score reached 0)"
                    )
                )
            except Exception as sub_err:
                print("Termination submission creation error:", sub_err)

        connection.commit()

        # If this Flask instance owns the monitor process, stop it only
        # after the authoritative MySQL termination state is committed.
        process = monitor_processes.get(student_id)
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
            except Exception as error:
                print("Monitor termination process error:", error)

        monitor_processes.pop(student_id, None)
        monitor_session_ids.pop(student_id, None)

        with CENTRAL_LIVE_LOCK:
            CENTRAL_LIVE_HEARTBEATS.pop(student_id, None)

        # ----------------------------------------------------
        # STOP THE TEACHER'S LIVE FEED INSTANTLY
        # ----------------------------------------------------
        # Dropping the buffered frame starves the MJPEG stream, so
        # /api/live/video/<student_id> stops emitting and the teacher
        # monitor falls back to its terminated state.
        # ----------------------------------------------------
        with LIVE_VIDEO_LOCK:
            LIVE_VIDEO_FRAMES.pop(student_id, None)

        # ----------------------------------------------------
        # GENERATE THE TERMINATION REPORT
        # ----------------------------------------------------
        report_path = None

        try:

            cursor.execute("""
                SELECT start_time, end_time
                FROM exam_sessions
                WHERE session_id = %s
                LIMIT 1
            """, (session_id,))

            row = cursor.fetchone() or {}

            start_time_value = row.get("start_time")
            end_time_value = row.get("end_time") or end_time

            duration_seconds = 0

            try:

                if start_time_value is not None:
                    duration_seconds = round(
                        (
                            end_time_value - start_time_value
                        ).total_seconds(),
                        2
                    )

            except Exception:
                duration_seconds = 0

            report_path = write_session_report(
                session_id,
                student_id,
                exam_name,
                {
                    "start_time": _monitor_json_datetime(start_time_value),
                    "end_time": _monitor_json_datetime(end_time_value),
                    "duration_seconds": duration_seconds,
                    "final_trust_score": final_trust_score,
                    "final_risk_level": final_risk_level,
                    "terminated": True,
                    "termination_reason": "TRUST_SCORE_REACHED_ZERO"
                }
            )

        except Exception as error:

            print("Termination report error:", error)

        return jsonify({
            "success": True,
            "terminated": True,
            "session_id": session_id,
            "trust_score": final_trust_score,
            "risk_level": final_risk_level,
            "report_path": report_path
        })

    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        print("Central termination error:", error)
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

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
        raw_trust = data.get("final_trust_score")
        trust_score = (
            int(raw_trust)
            if raw_trust is not None
            else 100
        )
        risk_level = str(data.get("final_risk_level", "LOW"))

        session_status = str(
            data.get("status", "COMPLETED") or "COMPLETED"
        ).strip().upper()

        if session_status not in ("COMPLETED", "TERMINATED"):
            session_status = "COMPLETED"

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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                student_id = VALUES(student_id),
                exam_name = VALUES(exam_name),
                start_time = VALUES(start_time),
                end_time = VALUES(end_time),
                status = VALUES(status),
                final_trust_score = VALUES(final_trust_score),
                final_risk_level = VALUES(final_risk_level)
        """, (
            session_id,
            student_id,
            exam_name,
            start_time,
            end_time,
            session_status,
            trust_score,
            risk_level
        ))

        cursor.execute("""
            UPDATE live_students
            SET status = %s,
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
        """, (session_status, student_id, session_id))

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

        report_path = write_session_report(
            session_id,
            student_id,
            exam_name,
            {
                "start_time": data.get("start_time"),
                "end_time": data.get("end_time"),
                "duration_seconds": data.get("duration_seconds", 0),
                "cheating_score": data.get("cheating_score", 0),
                "final_trust_score": data.get("final_trust_score", 100),
                "final_risk_level": data.get("final_risk_level", "LOW")
            }
        )

        if report_path is None:

            return jsonify({
                "success": False,
                "error": "Report could not be generated."
            }), 500

        return jsonify({
            "success": True,
            "report_path": report_path
        })

    except Exception as error:

        print("Central report generation error:", error)

        return jsonify({"success": False, "error": str(error)}), 500


@app.route("/api/live/video/frame", methods=["POST"])
def receive_live_video_frame():
    """
    Receive ONE already-encoded JPEG from a student agent.

    The preferred transport is:
        POST /api/live/video/frame?student_id=...&session_id=...
        Content-Type: image/jpeg
        Body: raw JPEG bytes

    This avoids multipart/form-data parsing through Cloudflare, which
    was producing intermittent Flask 400/500 errors.

    Multipart uploads are still accepted for compatibility with older
    student agents.
    """
    try:
        # --------------------------------------------------------
        # FIRST: read lightweight identity fields from query string.
        # Query parameters avoid multipart parser failures.
        # --------------------------------------------------------
        student_id = str(
            request.args.get(
                "student_id",
                ""
            )
        ).strip()

        session_id = str(
            request.args.get(
                "session_id",
                ""
            )
        ).strip()

        raw = b""

        content_type = (
            request.headers.get(
                "Content-Type",
                ""
            ).lower()
        )

        # --------------------------------------------------------
        # PREFERRED: raw JPEG body
        # --------------------------------------------------------
        if content_type.startswith("image/jpeg"):

            raw = request.get_data(
                cache=False,
                as_text=False
            )

        else:

            # ----------------------------------------------------
            # COMPATIBILITY: multipart/form-data
            # ----------------------------------------------------
            #
            # Older student agents send:
            #   data = student_id/session_id
            #   files = frame
            #
            # Only invoke Flask's multipart parser when needed.
            # ----------------------------------------------------
            try:

                if not student_id:
                    student_id = str(
                        request.form.get(
                            "student_id",
                            ""
                        )
                    ).strip()

                if not session_id:
                    session_id = str(
                        request.form.get(
                            "session_id",
                            ""
                        )
                    ).strip()

                uploaded = request.files.get(
                    "frame"
                )

                if uploaded is not None:
                    raw = uploaded.read()

                else:
                    raw = request.get_data(
                        cache=False,
                        as_text=False
                    )

            except Exception as parse_error:

                # Do not turn a malformed/aborted upload into an
                # internal server error. The student agent will simply
                # send the next newest frame.
                print(
                    "Live video request parsing skipped:",
                    parse_error
                )

                return jsonify({
                    "success": False,
                    "error": "Invalid video frame request."
                }), 400

        # --------------------------------------------------------
        # VALIDATION
        # --------------------------------------------------------
        if (
            not student_id
            or not session_id
            or not raw
        ):

            return jsonify({
                "success": False,
                "error": (
                    "student_id, session_id and JPEG frame "
                    "are required."
                )
            }), 400

        # --------------------------------------------------------
        # BASIC JPEG VALIDATION
        # --------------------------------------------------------
        #
        # JPEG files normally start with FF D8 and end with FF D9.
        # Rejecting invalid bodies prevents corrupt data from replacing
        # a good frame in the teacher stream.
        # --------------------------------------------------------
        if (
            len(raw) < 100
            or not raw.startswith(b"\xff\xd8")
        ):

            return jsonify({
                "success": False,
                "error": "Invalid JPEG frame."
            }), 400

        now_mono = time.monotonic()

        # --------------------------------------------------------
        # STORE ONLY THE NEWEST FRAME
        # --------------------------------------------------------
        with LIVE_VIDEO_LOCK:

            LIVE_VIDEO_FRAMES[student_id] = {
                "session_id": session_id,
                "frame": raw,
                "updated": now_mono
            }

        # --------------------------------------------------------
        # VIDEO FRAME ALSO ACTS AS LIVE HEARTBEAT
        # --------------------------------------------------------
        with CENTRAL_LIVE_LOCK:

            heartbeat = (
                CENTRAL_LIVE_HEARTBEATS.get(
                    student_id
                )
            )

            if (
                heartbeat is not None
                and heartbeat.get("session_id")
                == session_id
            ):

                heartbeat["updated"] = now_mono

        return jsonify({
            "success": True,
            "student_id": student_id,
            "session_id": session_id,
            "frame_bytes": len(raw)
        })

    except Exception as error:

        print(
            "Live video receive error:",
            error
        )

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500


@app.route("/api/live/video/<student_id>")
def live_video_stream(student_id):
    """
    MJPEG stream of the newest processed frame for one student.

    The stream closes by itself once frames stop arriving (student went
    offline, or the session was terminated at Trust Score 0). Without
    this the browser held an empty multipart response open forever and
    the teacher's monitor never noticed the feed had ended.
    """
    student_id = str(student_id).strip()

    def generate():
        last_sent = None
        idle_since = time.monotonic()

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
                idle_since = time.monotonic()
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Cache-Control: no-cache\r\n\r\n"
                    + frame
                    + b"\r\n"
                )

            if time.monotonic() - idle_since > LIVE_VIDEO_IDLE_TIMEOUT:
                # No fresh frame for a while: the live feed is over.
                print(
                    "Live video stream closed for",
                    student_id,
                    "(no frames for",
                    LIVE_VIDEO_IDLE_TIMEOUT,
                    "seconds)"
                )
                return

            time.sleep(0.05)

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )# ============================================================
# MONITORING REPORTS
# ============================================================
# Reports are SESSION-centric, not submission-centric.
#
# Every finished monitoring session (COMPLETED or TERMINATED)
# produces exactly one report, even when the student was force
# terminated at Trust Score 0 and never submitted any answers.
# ============================================================

def _risk_from_trust(trust):

    if trust is None:
        return "LOW"

    try:
        score = int(trust)
    except (TypeError, ValueError):
        return "LOW"

    if score < 50:
        return "HIGH"

    if score < 75:
        return "MEDIUM"

    return "LOW"


def _session_submission_cursor(cursor, session_id):

    cursor.execute("""
        SELECT
            submission_id,
            exam_id,
            score,
            teacher_marks,
            teacher_feedback,
            total_questions,
            submitted_at,
            evaluated_at,
            status,
            evaluation_type,
            trust_score
        FROM exam_submissions
        WHERE session_id = %s
        ORDER BY submission_id DESC
        LIMIT 1
    """, (str(session_id),))

    return cursor.fetchone()


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
                es.session_id,
                es.student_id,
                s.student_name,
                es.exam_name,
                es.start_time,
                es.end_time,
                es.status,
                es.final_trust_score,
                es.final_risk_level,
                (
                    SELECT COUNT(*)
                    FROM violations v
                    WHERE v.session_id = es.session_id
                ) AS violation_count,
                (
                    SELECT COUNT(*)
                    FROM evidence ev
                    WHERE ev.session_id = es.session_id
                ) AS evidence_count,
                (
                    SELECT sub.submission_id
                    FROM exam_submissions sub
                    WHERE sub.session_id = es.session_id
                    ORDER BY sub.submission_id DESC
                    LIMIT 1
                ) AS submission_id,
                (
                    SELECT sub.score
                    FROM exam_submissions sub
                    WHERE sub.session_id = es.session_id
                    ORDER BY sub.submission_id DESC
                    LIMIT 1
                ) AS score,
                (
                    SELECT sub.status
                    FROM exam_submissions sub
                    WHERE sub.session_id = es.session_id
                    ORDER BY sub.submission_id DESC
                    LIMIT 1
                ) AS submission_status,
                (
                    SELECT sub.evaluation_type
                    FROM exam_submissions sub
                    WHERE sub.session_id = es.session_id
                    ORDER BY sub.submission_id DESC
                    LIMIT 1
                ) AS evaluation_type
            FROM exam_sessions es
            LEFT JOIN students s ON s.student_id = es.student_id
            WHERE UPPER(es.status) IN ('COMPLETED', 'TERMINATED')
            ORDER BY es.start_time DESC
        """)

        rows = cursor.fetchall()
        reports = []

        for row in rows:

            trust = row.get("final_trust_score")

            for key, value in list(row.items()):
                if isinstance(value, datetime):
                    row[key] = value.isoformat()

            status = str(row.get("status") or "").upper()

            reports.append({
                **row,
                "final_trust_score": trust,
                "final_risk_level": (
                    row.get("final_risk_level") or _risk_from_trust(trust)
                ),
                "violation_count": int(row.get("violation_count") or 0),
                "evidence_count": int(row.get("evidence_count") or 0),
                "terminated": status == "TERMINATED"
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
    """
    Full report for one monitoring session.

    Works even when the session was force-terminated at Trust Score 0
    and therefore has NO exam_submission row.
    """
    connection = None
    cursor = None
    try:
        session_id = str(session_id).strip()

        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Unable to connect to MySQL."}), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                es.session_id,
                es.student_id,
                s.student_name,
                es.exam_name,
                es.start_time,
                es.end_time,
                es.status,
                es.final_trust_score,
                es.final_risk_level
            FROM exam_sessions es
            LEFT JOIN students s ON s.student_id = es.student_id
            WHERE es.session_id = %s
            LIMIT 1
        """, (session_id,))

        session_row = cursor.fetchone()

        if session_row is None:
            return jsonify({
                "success": False,
                "error": "No monitoring session found for this ID."
            }), 404

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

        submission = _session_submission_cursor(cursor, session_id)

        for collection in (violations, evidence):
            for row in collection:
                for key, value in list(row.items()):
                    if isinstance(value, datetime):
                        row[key] = value.isoformat()

        for key, value in list(session_row.items()):
            if isinstance(value, datetime):
                session_row[key] = value.isoformat()

        if submission:
            for key, value in list(submission.items()):
                if isinstance(value, datetime):
                    submission[key] = value.isoformat()

        trust = session_row.get("final_trust_score")

        report = {
            **session_row,
            "exam_id": (submission or {}).get("exam_id"),
            "submission_id": (submission or {}).get("submission_id"),
            "score": (submission or {}).get("score"),
            "teacher_marks": (submission or {}).get("teacher_marks"),
            "teacher_feedback": (submission or {}).get("teacher_feedback"),
            "total_questions": (submission or {}).get("total_questions"),
            "submitted_at": (submission or {}).get("submitted_at"),
            "evaluated_at": (submission or {}).get("evaluated_at"),
            "status": session_row.get("status"),
            "evaluation_type": (submission or {}).get("evaluation_type"),
            "final_trust_score": trust,
            "final_risk_level": (
                session_row.get("final_risk_level") or _risk_from_trust(trust)
            ),
            "terminated": str(session_row.get("status") or "").upper() == "TERMINATED",
            "violations": violations,
            "evidence": evidence,
            "total_violations": len(violations),
            "total_evidence": len(evidence)
        }

        report_url = None

        if submission:
            submission_id = int(submission["submission_id"])
            generate_submission_report(submission_id)
            report_url = f"/submission-report/{submission_id}"

        return jsonify({
            "success": True,
            "report": report,
            "report_url": report_url
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
    """
    Teacher manual evaluation.

    Works for BOTH cases:
      - PENDING_REVIEW  (Trust Score was below the auto threshold)
      - EVALUATED       (teacher overrides / corrects an auto score)

    The generated HTML report is always refreshed afterwards.
    """
    connection = None
    cursor = None
    try:
        if not teacher_logged_in():
            return jsonify({"success": False, "error": "Teacher login required."}), 401

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

        previous_status = str(row["status"] or "").upper()
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
            "reevaluated": previous_status == "EVALUATED",
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
# DATA CLEANUP - DELETE / CLEAR
# ============================================================
# Deleting an exam, a student or a single submission also removes
# every dependent row AND the evidence JPEG on disk. Nothing is
# left orphaned in MySQL or in reports/evidence.
# ============================================================

EVIDENCE_DIR = os.path.join(BASE_DIR, "reports", "evidence")


def _remove_evidence_files(file_paths):

    removed = 0

    for raw_path in file_paths or []:

        if not raw_path:
            continue

        candidates = [

            str(raw_path),

            os.path.join(
                EVIDENCE_DIR,
                os.path.basename(str(raw_path))
            )
        ]

        for candidate in candidates:

            try:

                if os.path.isfile(candidate):

                    os.remove(candidate)
                    removed += 1
                    break

            except Exception as error:

                print("Evidence delete error:", candidate, error)

    return removed


def _first_column(row):
    """
    Read the first column of a row from EITHER a tuple cursor or a
    dictionary cursor.

    Mixing the two was the bug that made delete/clear fail with the
    confusing error "0" (str(KeyError(0))).
    """

    if row is None:
        return None

    if isinstance(row, dict):

        for value in row.values():
            return value

        return None

    try:
        return row[0]
    except (IndexError, TypeError, KeyError):
        return None


def _collect_session_ids(cursor, exam_id):
    """
    Every monitoring session that belongs to an exam.

    Sessions are matched through the exam id directly, or through any
    submission for that exam. A JOIN is used instead of a scalar
    subquery so two exams sharing a name cannot break the statement.
    """

    cursor.execute("""
        SELECT DISTINCT es.session_id
        FROM exam_sessions es
        INNER JOIN exams e ON e.exam_name = es.exam_name
        WHERE e.exam_id = %s

        UNION

        SELECT DISTINCT s.session_id
        FROM exam_submissions s
        WHERE s.exam_id = %s
          AND s.session_id IS NOT NULL
    """, (exam_id, exam_id))

    session_ids = []

    for row in cursor.fetchall():

        value = _first_column(row)

        if value:
            session_ids.append(str(value))

    return session_ids


def _delete_sessions_data(cursor, session_ids):

    totals = {
        "sessions": 0,
        "violations": 0,
        "evidence": 0,
        "files": 0
    }

    for session_id in session_ids:

        cursor.execute(
            "SELECT file_path FROM evidence WHERE session_id = %s",
            (session_id,)
        )

        file_paths = [
            _first_column(row)
            for row in cursor.fetchall()
        ]

        totals["files"] += _remove_evidence_files(file_paths)

        cursor.execute(
            "DELETE FROM evidence WHERE session_id = %s",
            (session_id,)
        )
        totals["evidence"] += cursor.rowcount

        cursor.execute(
            "DELETE FROM violations WHERE session_id = %s",
            (session_id,)
        )
        totals["violations"] += cursor.rowcount

        cursor.execute(
            "DELETE FROM trust_score_history WHERE session_id = %s",
            (session_id,)
        )

        cursor.execute(
            "DELETE FROM exam_sessions WHERE session_id = %s",
            (session_id,)
        )
        totals["sessions"] += cursor.rowcount

    return totals


@app.route(
    "/api/exams/<exam_id>",
    methods=["DELETE"]
)
def delete_exam(exam_id):
    """Delete an exam and every record + evidence file it produced."""

    if not teacher_logged_in():

        return jsonify({
            "success": False,
            "error": "Teacher login required."
        }), 401

    connection = None
    cursor = None

    try:

        exam_id = str(exam_id).strip()

        connection = get_database_connection()

        if connection is None:

            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT exam_id, exam_name FROM exams WHERE exam_id = %s LIMIT 1",
            (exam_id,)
        )

        exam = cursor.fetchone()

        if exam is None:

            return jsonify({
                "success": False,
                "error": "Exam not found."
            }), 404

        session_ids = _collect_session_ids(cursor, exam_id)

        removed = _delete_sessions_data(cursor, session_ids)

        cursor.execute(
            "DELETE FROM exam_submissions WHERE exam_id = %s",
            (exam_id,)
        )
        submissions_deleted = cursor.rowcount

        cursor.execute(
            "DELETE FROM exam_assignments WHERE exam_id = %s",
            (exam_id,)
        )
        assignments_deleted = cursor.rowcount

        cursor.execute(
            "DELETE FROM exams WHERE exam_id = %s",
            (exam_id,)
        )
        exams_deleted = cursor.rowcount

        connection.commit()

        return jsonify({
            "success": True,
            "deleted": {
                "exam_id": exam_id,
                "exam_name": exam.get("exam_name"),
                "exams": exams_deleted,
                "assignments": assignments_deleted,
                "submissions": submissions_deleted,
                **removed
            }
        })

    except Exception as error:

        if connection is not None:

            try:
                connection.rollback()
            except Exception:
                pass

        print("Delete exam error:", error)

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


@app.route(
    "/api/exams/<exam_id>/clear-data",
    methods=["POST"]
)
def clear_exam_data(exam_id):
    """
    Wipe only the recorded data of an exam (sessions, violations,
    evidence, submissions) and keep the exam itself intact.
    """

    if not teacher_logged_in():

        return jsonify({
            "success": False,
            "error": "Teacher login required."
        }), 401

    connection = None
    cursor = None

    try:

        exam_id = str(exam_id).strip()

        connection = get_database_connection()

        if connection is None:

            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT exam_id, exam_name FROM exams WHERE exam_id = %s LIMIT 1",
            (exam_id,)
        )

        exam = cursor.fetchone()

        if exam is None:

            return jsonify({
                "success": False,
                "error": "Exam not found."
            }), 404

        session_ids = _collect_session_ids(cursor, exam_id)

        removed = _delete_sessions_data(cursor, session_ids)

        cursor.execute(
            "DELETE FROM exam_submissions WHERE exam_id = %s",
            (exam_id,)
        )
        submissions_deleted = cursor.rowcount

        connection.commit()

        return jsonify({
            "success": True,
            "cleared": {
                "exam_id": exam_id,
                "submissions": submissions_deleted,
                **removed
            }
        })

    except Exception as error:

        if connection is not None:

            try:
                connection.rollback()
            except Exception:
                pass

        print("Clear exam data error:", error)

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


@app.route(
    "/api/submissions/<int:submission_id>",
    methods=["DELETE"]
)
def delete_submission(submission_id):

    if not teacher_logged_in():

        return jsonify({
            "success": False,
            "error": "Teacher login required."
        }), 401

    connection = None
    cursor = None

    try:

        connection = get_database_connection()

        if connection is None:

            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "DELETE FROM exam_submissions WHERE submission_id = %s",
            (int(submission_id),)
        )

        deleted = cursor.rowcount

        connection.commit()

        # Remove the generated HTML report as well.
        report_path = os.path.join(
            SUBMISSION_REPORTS_DIR,
            f"submission_{int(submission_id)}.html"
        )

        try:

            if os.path.isfile(report_path):
                os.remove(report_path)

        except Exception as error:

            print("Submission report delete error:", error)

        return jsonify({
            "success": True,
            "deleted": deleted
        })

    except Exception as error:

        if connection is not None:

            try:
                connection.rollback()
            except Exception:
                pass

        print("Delete submission error:", error)

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()


@app.route(
    "/api/students/<student_id>",
    methods=["DELETE"]
)
def delete_student(student_id):
    """
    Remove a student account together with every session, violation,
    evidence file and submission that belongs to it.
    """

    if not teacher_logged_in():

        return jsonify({
            "success": False,
            "error": "Teacher login required."
        }), 401

    connection = None
    cursor = None

    try:

        student_id = str(student_id).strip()

        connection = get_database_connection()

        if connection is None:

            return jsonify({
                "success": False,
                "error": "Unable to connect to MySQL."
            }), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT student_id FROM students WHERE student_id = %s LIMIT 1",
            (student_id,)
        )

        if cursor.fetchone() is None:

            return jsonify({
                "success": False,
                "error": "Student not found."
            }), 404

        cursor.execute(
            "SELECT DISTINCT session_id FROM exam_sessions WHERE student_id = %s",
            (student_id,)
        )

        session_ids = []

        for row in cursor.fetchall():

            value = _first_column(row)

            if value:
                session_ids.append(str(value))

        # Submissions can reference a session that has no exam_sessions row.
        cursor.execute(
            "SELECT DISTINCT session_id FROM exam_submissions WHERE student_id = %s",
            (student_id,)
        )

        for row in cursor.fetchall():

            value = _first_column(row)

            if value and str(value) not in session_ids:
                session_ids.append(str(value))

        removed = _delete_sessions_data(cursor, session_ids)

        cursor.execute(
            "DELETE FROM exam_submissions WHERE student_id = %s",
            (student_id,)
        )
        submissions_deleted = cursor.rowcount

        cursor.execute(
            "DELETE FROM exam_assignments WHERE student_id = %s",
            (student_id,)
        )
        assignments_deleted = cursor.rowcount

        cursor.execute(
            "DELETE FROM batch_members WHERE student_id = %s",
            (student_id,)
        )

        cursor.execute("""
            UPDATE batches b
            SET student_count = (
                SELECT COUNT(*) FROM batch_members bm WHERE bm.batch_id = b.batch_id
            )
        """)

        cursor.execute(
            "DELETE FROM live_students WHERE student_id = %s",
            (student_id,)
        )

        cursor.execute(
            "DELETE FROM students WHERE student_id = %s",
            (student_id,)
        )

        connection.commit()

        # Face profile image is part of the student's data.
        profile_path = os.path.join(
            FACE_PROFILES_DIR,
            f"{student_id}.jpg"
        )

        try:

            if os.path.isfile(profile_path):
                os.remove(profile_path)

        except Exception as error:

            print("Face profile delete error:", error)

        return jsonify({
            "success": True,
            "deleted": {
                "student_id": student_id,
                "assignments": assignments_deleted,
                "submissions": submissions_deleted,
                **removed
            }
        })

    except Exception as error:

        if connection is not None:

            try:
                connection.rollback()
            except Exception:
                pass

        print("Delete student error:", error)

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

        # End the active exam session after successful submission.
        # The student remains logged in so they can return to their
        # dashboard and see the submission result in the report.
        session.pop("active_session_id", None)
        session.pop("active_exam_id", None)


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
              AND (
                    (
                        UPPER(ls.status) = 'ONLINE'
                        AND ls.last_update >= (NOW() - INTERVAL 5 SECOND)
                    )
                    OR UPPER(ls.status) = 'TERMINATED'
              )
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
            trust_history=trust_history,
            terminated=(
                str(selected_student.get("status") or "").upper()
                == "TERMINATED"
            )
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
                s.student_id,
                s.student_name,
                s.username,
                s.created_at
            FROM students s
            LEFT JOIN batch_members bm ON bm.student_id = s.student_id
            WHERE bm.id IS NULL
            ORDER BY s.created_at DESC
            """
        )

        students = cursor.fetchall()

        cursor.execute("SELECT COUNT(*) AS cnt FROM students")
        _total_row = cursor.fetchone()
        total_count = _total_row["cnt"] if _total_row else len(students)

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
            "total_count": total_count,
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
# EXCEL UPLOAD HELPERS
# ============================================================

def _normalize_excel_header(header):
    return header.replace(" ", "").strip().lower()


def _excel_headers(worksheet):
    headers = []
    for cell in worksheet[1]:
        headers.append(str(cell.value or "").strip())
    return headers


def _excel_rows(worksheet):
    rows = []
    for row in worksheet.iter_rows(min_row=2, values_only=True):
        if row is None:
            continue
        values = ["" if v is None else str(v).strip() for v in row]
        if not any(values):
            continue
        rows.append(values)
    return rows


# ============================================================
# EXCEL BULK STUDENT ENROLLMENT
# ============================================================

@app.route(
    "/api/students/enroll/excel",
    methods=["POST"]
)
def excel_enroll_students():

    try:
        import openpyxl
        import io
    except ImportError:
        return jsonify({
            "success": False,
            "error": "openpyxl is not installed on the server."
        }), 500

    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded."}), 400

    uploaded = request.files["file"]

    if not uploaded.filename.lower().endswith(".xlsx"):
        return jsonify({"success": False, "error": "Please upload a .xlsx file."}), 400

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(uploaded.read()))
        sheet = workbook.active
    except Exception as error:
        return jsonify({"success": False, "error": f"Cannot read workbook: {error}"}), 400

    headers = _excel_headers(sheet)
    normalized = {_normalize_excel_header(h): i for i, h in enumerate(headers)}

    print("[EXCEL UPLOAD] Received headers:", headers)
    print("[EXCEL UPLOAD] Normalized headers:", normalized)

    id_index = normalized.get("student_id")
    if id_index is None:
        id_index = normalized.get("studentid")
    name_index = normalized.get("student_name")
    if name_index is None:
        name_index = normalized.get("studentname")
    username_index = normalized.get("username")
    password_index = normalized.get("password")

    missing = []
    if id_index is None:
        missing.append("student_id")
    if name_index is None:
        missing.append("student_name")
    if username_index is None:
        missing.append("username")
    if password_index is None:
        missing.append("password")

    if missing:
        found_headers = [h for h in headers if h.strip()]
        return jsonify({
            "success": False,
            "error": "Invalid Excel format.\n\nMissing columns: " + ", ".join(missing) + "\n\nFound columns: " + (", ".join(found_headers) if found_headers else "none")
        }), 400

    rows = _excel_rows(sheet)

    connection = get_database_connection()

    if connection is None:
        return jsonify({"success": False, "error": "Database unavailable."}), 500

    cursor = None
    inserted = 0
    duplicate = 0
    failures = []
    total_students = len(rows)

    filename = uploaded.filename.replace(".xlsx", "") if uploaded.filename else "excel"

    try:
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT batch_id FROM batches WHERE batch_name = %s LIMIT 1",
            (filename,)
        )
        existing_batch = cursor.fetchone()

        if existing_batch:
            return jsonify({
                "success": False,
                "error": f"Batch '{filename}' already exists. Please rename the file or delete the existing batch first."
            }), 409

        cursor.execute(
            "INSERT INTO batches (batch_name, source_filename, student_count) VALUES (%s, %s, 0)",
            (filename, uploaded.filename)
        )
        batch_id = cursor.lastrowid

        inserted_student_ids = []
        row_number = 1
        for row in rows:

            row_number += 1

            if id_index >= len(row) or name_index >= len(row):
                failures.append({"row": row_number, "reason": "Row has insufficient columns"})
                continue

            student_id = row[id_index] if id_index < len(row) else ""
            student_name = row[name_index] if name_index < len(row) else ""
            if username_index is not None and username_index < len(row) and row[username_index]:
                username = row[username_index]
            else:
                username = student_name.split()[0].lower() if student_name else student_id
            if password_index is not None and password_index < len(row) and row[password_index]:
                password = row[password_index]
            else:
                import hashlib
                password = hashlib.sha256(student_id.encode()).hexdigest()[:12]

            if not student_id or not student_name:
                failures.append({"row": row_number, "reason": "Missing student_id or student_name"})
                continue

            cursor.execute(
                "SELECT student_id FROM students WHERE student_id = %s OR username = %s LIMIT 1",
                (student_id, username)
            )

            if cursor.fetchone() is not None:
                duplicate += 1
                cursor.execute(
                    "SELECT student_id FROM students WHERE student_id = %s LIMIT 1",
                    (student_id,)
                )
                existing = cursor.fetchone()
                if existing:
                    inserted_student_ids.append(str(existing["student_id"]))
                continue

            cursor.execute(
                """
                INSERT INTO students
                    (student_id, student_name, username, password)
                VALUES (%s, %s, %s, %s)
                """,
                (student_id, student_name, username, password)
            )

            inserted += 1
            inserted_student_ids.append(student_id)

        for sid in inserted_student_ids:
            cursor.execute(
                "INSERT IGNORE INTO batch_members (batch_id, student_id) VALUES (%s, %s)",
                (batch_id, sid)
            )

        cursor.execute(
            "UPDATE batches SET student_count = (SELECT COUNT(*) FROM batch_members WHERE batch_id = %s) WHERE batch_id = %s",
            (batch_id, batch_id)
        )

        connection.commit()

        return jsonify({
            "success": True,
            "inserted": inserted,
            "duplicate": duplicate,
            "failures": failures,
            "total_students": total_students,
            "filename": filename,
            "batch_id": batch_id,
            "batch_name": filename
        }), 200

    except mysql.connector.Error as error:
        if connection is not None:
            connection.rollback()
        return jsonify({"success": False, "error": f"Database error: {error}"}), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


# ============================================================
# EXCEL BULK EXAM ASSIGNMENT
# ============================================================

@app.route(
    "/api/exams/<exam_id>/assign/excel",
    methods=["POST"]
)
def excel_assign_exam(exam_id):

    if not teacher_logged_in():

        return jsonify({
            "success": False,
            "error": "Teacher login required."
        }), 401

    try:
        import openpyxl
        import io
    except ImportError:
        return jsonify({
            "success": False,
            "error": "openpyxl is not installed on the server."
        }), 500

    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded."}), 400

    uploaded = request.files["file"]

    if not uploaded.filename.lower().endswith(".xlsx"):
        return jsonify({"success": False, "error": "Please upload a .xlsx file."}), 400

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(uploaded.read()))
        sheet = workbook.active
    except Exception as error:
        return jsonify({"success": False, "error": f"Cannot read workbook: {error}"}), 400

    headers = _excel_headers(sheet)
    normalized = {_normalize_excel_header(h): i for i, h in enumerate(headers)}

    id_index = normalized.get("student_id") or normalized.get("studentid")

    if id_index is None:
        return jsonify({
            "success": False,
            "error": "Workbook must have a header row with a 'student_id' column."
        }), 400

    rows = _excel_rows(sheet)
    student_ids = [row[id_index] for row in rows if id_index < len(row) and row[id_index]]

    connection = get_database_connection()

    if connection is None:
        return jsonify({"success": False, "error": "Database unavailable."}), 500

    cursor = None
    assigned = 0
    unknown = []
    total_students = len(student_ids)

    try:
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT exam_id FROM exams WHERE exam_id = %s LIMIT 1",
            (str(exam_id),)
        )

        if cursor.fetchone() is None:
            return jsonify({"success": False, "error": "Exam not found."}), 404

        cursor.execute(
            "SELECT student_id FROM students WHERE student_id IN (%s)" % (
                ",".join(["%s"] * len(student_ids))
            ),
            tuple(student_ids)
        )

        known = {str(row["student_id"]) for row in cursor.fetchall()}

        for student_id in student_ids:
            if student_id not in known:
                unknown.append(student_id)
                continue
            cursor.execute(
                "INSERT IGNORE INTO exam_assignments (exam_id, student_id) VALUES (%s, %s)",
                (str(exam_id), student_id)
            )
            if cursor.rowcount == 1:
                assigned += 1

        connection.commit()

        filename = uploaded.filename.replace(".xlsx", "") if uploaded.filename else "excel"

        return jsonify({
            "success": True,
            "assigned": assigned,
            "unknown": unknown,
            "total_students": total_students,
            "filename": filename
        }), 200

    except mysql.connector.Error as error:
        if connection is not None:
            connection.rollback()
        return jsonify({"success": False, "error": f"Database error: {error}"}), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


# ============================================================
# EXCEL TEMPLATES
# ============================================================

@app.route("/api/excel/enrollment-template")
def excel_enrollment_template():

    try:
        import openpyxl
        import io
    except ImportError:
        return jsonify({"success": False, "error": "openpyxl is not installed on the server."}), 500

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Students"
    sheet.append(["student_id", "student_name", "username", "password"])
    sheet.append(["101", "Student Name 1", "student101", "proctify123"])
    sheet.append(["102", "Student Name 2", "student102", "proctify123"])

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="proctify_students_template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/api/excel/assignment-template")
def excel_assignment_template():

    try:
        import openpyxl
        import io
    except ImportError:
        return jsonify({"success": False, "error": "openpyxl is not installed on the server."}), 500

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Assignments"
    sheet.append(["student_id"])
    sheet.append(["101"])
    sheet.append(["102"])

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="proctify_assignment_template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ============================================================
# BATCH MANAGEMENT API
# ============================================================

@app.route("/api/batches", methods=["GET"])
def get_batches():

    if not teacher_logged_in():
        return jsonify({"success": False, "error": "Teacher login required."}), 401

    connection = None
    cursor = None

    try:
        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Database unavailable."}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT batch_id, batch_name, source_filename, student_count, created_at
            FROM batches
            ORDER BY created_at DESC
        """)
        batches = cursor.fetchall()

        for b in batches:
            if isinstance(b.get("created_at"), datetime):
                b["created_at"] = b["created_at"].isoformat()

        return jsonify({"success": True, "batches": batches})

    except Exception as error:
        print("Get batches error:", error)
        return jsonify({"success": False, "error": str(error), "batches": []}), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/batches/<int:batch_id>/students", methods=["GET"])
def get_batch_students(batch_id):

    if not teacher_logged_in():
        return jsonify({"success": False, "error": "Teacher login required."}), 401

    connection = None
    cursor = None

    try:
        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Database unavailable."}), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT batch_id, batch_name, student_count FROM batches WHERE batch_id = %s", (batch_id,))
        batch = cursor.fetchone()
        if not batch:
            return jsonify({"success": False, "error": "Batch not found."}), 404

        cursor.execute("""
            SELECT s.student_id, s.student_name, s.username, s.created_at
            FROM students s
            INNER JOIN batch_members bm ON bm.student_id = s.student_id
            WHERE bm.batch_id = %s
            ORDER BY s.student_id
        """, (batch_id,))
        students = cursor.fetchall()

        for s in students:
            if isinstance(s.get("created_at"), datetime):
                s["created_at"] = s["created_at"].isoformat()

        return jsonify({
            "success": True,
            "batch": batch,
            "students": students,
            "count": len(students)
        })

    except Exception as error:
        print("Get batch students error:", error)
        return jsonify({"success": False, "error": str(error)}), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/batches/<int:batch_id>", methods=["DELETE"])
def delete_batch(batch_id):

    if not teacher_logged_in():
        return jsonify({"success": False, "error": "Teacher login required."}), 401

    connection = None
    cursor = None

    try:
        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Database unavailable."}), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT batch_id, batch_name FROM batches WHERE batch_id = %s", (batch_id,))
        batch = cursor.fetchone()
        if not batch:
            return jsonify({"success": False, "error": "Batch not found."}), 404

        cursor.execute("DELETE FROM batch_members WHERE batch_id = %s", (batch_id,))
        cursor.execute("DELETE FROM batches WHERE batch_id = %s", (batch_id,))
        connection.commit()

        return jsonify({"success": True, "message": f"Batch '{batch['batch_name']}' deleted."})

    except Exception as error:
        print("Delete batch error:", error)
        if connection is not None:
            connection.rollback()
        return jsonify({"success": False, "error": str(error)}), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


# ============================================================
# BATCH-BASED EXAM ASSIGNMENT
# ============================================================

@app.route("/api/exams/<exam_id>/assign-batch", methods=["POST"])
def assign_batch_to_exam(exam_id):

    if not teacher_logged_in():
        return jsonify({"success": False, "error": "Teacher login required."}), 401

    data = request.get_json(silent=True) or {}
    batch_ids = data.get("batch_ids", [])

    if not isinstance(batch_ids, list) or not batch_ids:
        return jsonify({"success": False, "error": "No batch IDs provided."}), 400

    connection = None
    cursor = None

    try:
        connection = get_database_connection()
        if connection is None:
            return jsonify({"success": False, "error": "Database unavailable."}), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT exam_id FROM exams WHERE exam_id = %s LIMIT 1", (str(exam_id),))
        if cursor.fetchone() is None:
            return jsonify({"success": False, "error": "Exam not found."}), 404

        assigned = 0
        already_assigned = 0
        unknown_students = []
        assigned_batch_names = []

        for bid in batch_ids:
            cursor.execute("SELECT batch_id, batch_name FROM batches WHERE batch_id = %s", (int(bid),))
            batch = cursor.fetchone()
            if not batch:
                continue

            assigned_batch_names.append(batch["batch_name"])

            cursor.execute("""
                SELECT bm.student_id
                FROM batch_members bm
                WHERE bm.batch_id = %s
            """, (int(bid),))
            members = cursor.fetchall()

            for member in members:
                sid = str(member["student_id"])
                cursor.execute(
                    "SELECT student_id FROM students WHERE student_id = %s LIMIT 1",
                    (sid,)
                )
                if cursor.fetchone() is None:
                    unknown_students.append(sid)
                    continue

                cursor.execute(
                    "INSERT IGNORE INTO exam_assignments (exam_id, student_id, batch_id) VALUES (%s, %s, %s)",
                    (str(exam_id), sid, int(bid))
                )
                if cursor.rowcount == 1:
                    assigned += 1
                else:
                    already_assigned += 1

        connection.commit()

        return jsonify({
            "success": True,
            "assigned": assigned,
            "already_assigned": already_assigned,
            "unknown": unknown_students,
            "batch_names": assigned_batch_names
        }), 200

    except mysql.connector.Error as error:
        if connection is not None:
            connection.rollback()
        return jsonify({"success": False, "error": f"Database error: {error}"}), 500

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


# ============================================================
# REPORT EXPORT — PDF & EXCEL
# ============================================================

@app.route("/api/reports/export/pdf", methods=["POST"])
def export_reports_pdf():
    """
    Teacher selects multiple session IDs and
    downloads a PDF with all their reports.
    """
    if not teacher_logged_in():
        return jsonify({
            "success": False,
            "error": "Teacher login required."
        }), 401

    data = request.get_json(silent=True) or {}
    session_ids = data.get("session_ids", [])

    if not isinstance(session_ids, list) or not session_ids:
        return jsonify({
            "success": False,
            "error": "No session IDs provided."
        }), 400

    connection = None
    cursor = None
    try:
        from fpdf import FPDF

        connection = get_database_connection()
        if connection is None:
            return jsonify({
                "success": False,
                "error": "Database unavailable."
            }), 500

        cursor = connection.cursor(dictionary=True)

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 18)
        pdf.cell(0, 12, "PROCTIFY", ln=True)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(
            0, 8,
            f"Exam Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            ln=True
        )
        pdf.ln(6)

        for sid in session_ids:
            cursor.execute("""
                SELECT
                    es.session_id, es.student_id,
                    s.student_name, es.exam_name,
                    es.start_time, es.end_time,
                    es.status, es.final_trust_score,
                    es.final_risk_level
                FROM exam_sessions es
                LEFT JOIN students s
                    ON s.student_id = es.student_id
                WHERE es.session_id = %s
                LIMIT 1
            """, (str(sid),))
            row = cursor.fetchone()
            if not row:
                continue

            cursor.execute("""
                SELECT COUNT(*) AS cnt
                FROM violations v
                WHERE v.session_id = %s
            """, (str(sid),))
            vcnt = cursor.fetchone() or {}
            violation_count = vcnt.get("cnt", 0)

            cursor.execute("""
                SELECT COUNT(*) AS cnt
                FROM evidence ev
                WHERE ev.session_id = %s
            """, (str(sid),))
            ecnt = cursor.fetchone() or {}
            evidence_count = ecnt.get("cnt", 0)

            cursor.execute("""
                SELECT score, status, evaluation_type,
                       teacher_marks, teacher_feedback
                FROM exam_submissions
                WHERE session_id = %s
                ORDER BY submission_id DESC
                LIMIT 1
            """, (str(sid),))
            sub = cursor.fetchone() or {}

            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(
                0, 9,
                f"Student: {row.get('student_name') or row.get('student_id', 'UNKNOWN')}",
                ln=True
            )
            pdf.set_font("Helvetica", "", 10)

            trust = row.get("final_trust_score")
            score = sub.get("score")
            status_val = (
                sub.get("status") or row.get("status") or ""
            ).upper()
            result = "PENDING"
            if status_val == "EVALUATED" and score is not None:
                result = "PASS" if float(score) >= 50 else "FAIL"

            lines = [
                f"Student ID: {row.get('student_id', '')}",
                f"Exam: {row.get('exam_name', '')}",
                f"Status: {status_val}",
                f"Marks: {score if score is not None else 'PENDING'}",
                f"Trust Score: {trust if trust is not None else '—'}",
                f"Risk Level: {row.get('final_risk_level', '—')}",
                f"Result: {result}",
                f"Violations: {violation_count}",
                f"Evidence: {evidence_count}",
                f"Evaluation: {sub.get('evaluation_type', '—')}",
            ]
            if sub.get("teacher_feedback"):
                lines.append(
                    f"Feedback: {sub['teacher_feedback']}"
                )

            for line in lines:
                pdf.cell(0, 6, line, ln=True)

            pdf.ln(4)
            pdf.line(
                10, pdf.get_y(), 200, pdf.get_y()
            )
            pdf.ln(4)

        import io
        pdf_bytes = pdf.output()
        buf = io.BytesIO(pdf_bytes)
        buf.seek(0)

        return send_file(
            buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=(
                f"proctify_report_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            )
        )

    except Exception as error:
        print("PDF export error:", error)
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


@app.route("/api/reports/export/excel", methods=["POST"])
def export_reports_excel():
    """
    Teacher selects multiple session IDs and
    downloads an Excel file with all their reports.
    """
    if not teacher_logged_in():
        return jsonify({
            "success": False,
            "error": "Teacher login required."
        }), 401

    data = request.get_json(silent=True) or {}
    session_ids = data.get("session_ids", [])

    if not isinstance(session_ids, list) or not session_ids:
        return jsonify({
            "success": False,
            "error": "No session IDs provided."
        }), 400

    connection = None
    cursor = None
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment

        connection = get_database_connection()
        if connection is None:
            return jsonify({
                "success": False,
                "error": "Database unavailable."
            }), 500

        cursor = connection.cursor(dictionary=True)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "PROCTIFY Reports"

        headers = [
            "Student Name", "Student ID", "Exam Name",
            "Marks", "Max Marks", "Percentage",
            "Trust Score", "Risk Level",
            "Violations", "Evidence",
            "Pass/Fail", "Status",
            "Evaluation", "Termination Reason"
        ]

        header_font = Font(bold=True)
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        row_num = 2

        for sid in session_ids:
            cursor.execute("""
                SELECT
                    es.session_id, es.student_id,
                    s.student_name, es.exam_name,
                    es.start_time, es.end_time,
                    es.status, es.final_trust_score,
                    es.final_risk_level
                FROM exam_sessions es
                LEFT JOIN students s
                    ON s.student_id = es.student_id
                WHERE es.session_id = %s
                LIMIT 1
            """, (str(sid),))
            row = cursor.fetchone()
            if not row:
                continue

            cursor.execute("""
                SELECT COUNT(*) AS cnt
                FROM violations v
                WHERE v.session_id = %s
            """, (str(sid),))
            vcnt = cursor.fetchone() or {}
            violation_count = vcnt.get("cnt", 0)

            cursor.execute("""
                SELECT COUNT(*) AS cnt
                FROM evidence ev
                WHERE ev.session_id = %s
            """, (str(sid),))
            ecnt = cursor.fetchone() or {}
            evidence_count = ecnt.get("cnt", 0)

            cursor.execute("""
                SELECT score, status, evaluation_type,
                       teacher_marks, teacher_feedback
                FROM exam_submissions
                WHERE session_id = %s
                ORDER BY submission_id DESC
                LIMIT 1
            """, (str(sid),))
            sub = cursor.fetchone() or {}

            trust = row.get("final_trust_score")
            score = sub.get("score")
            status_val = (
                sub.get("status") or row.get("status") or ""
            ).upper()
            result = "PENDING"
            if status_val == "EVALUATED" and score is not None:
                result = "PASS" if float(score) >= 50 else "FAIL"

            termination_reason = ""
            if status_val == "TERMINATED":
                termination_reason = "Trust Score reached 0"

            values = [
                row.get("student_name", ""),
                row.get("student_id", ""),
                row.get("exam_name", ""),
                score if score is not None else "PENDING",
                100,
                (
                    f"{float(score):.1f}%"
                    if score is not None else "—"
                ),
                trust if trust is not None else "—",
                row.get("final_risk_level", "—"),
                violation_count,
                evidence_count,
                result,
                status_val,
                sub.get("evaluation_type", "—"),
                termination_reason,
            ]

            for col, val in enumerate(values, 1):
                ws.cell(
                    row=row_num, column=col,
                    value=val
                )
            row_num += 1

        for col_cells in ws.columns:
            max_len = 0
            col_letter = col_cells[0].column_letter
            for cell in col_cells:
                try:
                    if cell.value:
                        max_len = max(
                            max_len,
                            len(str(cell.value))
                        )
                except Exception:
                    pass
            ws.column_dimensions[
                col_letter
            ].width = min(max_len + 3, 30)

        import io
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        return send_file(
            buf,
            mimetype=(
                "application/vnd.openxmlformats-"
                "officedocument.spreadsheetml.sheet"
            ),
            as_attachment=True,
            download_name=(
                f"proctify_report_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            )
        )

    except Exception as error:
        print("Excel export error:", error)
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

    if ensure_mysql_schema():

        print(
            "MySQL schema: OK  (database:",
            DB_CONFIG["database"],
            ")"
        )

    print(
        "Teacher login:",
        TEACHER_DEFAULT_USERNAME,
        "/",
        TEACHER_DEFAULT_PASSWORD
    )

    print(
        "Student login:",
        "/  (select the Student role)"
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

    app.run(

        host="0.0.0.0",

        port=int(os.environ.get("PROCTIFY_PORT", 5000)),

        debug=False,

        threaded=True

    )