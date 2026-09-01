from flask import Flask, render_template, jsonify, request, session, redirect
import mysql.connector
import json
import os
import base64
import numpy as np
import cv2
import mediapipe as mp
import subprocess
import sys
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

monitor_processes = {}


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

    process = monitor_processes.get(student_id)

    if process is not None and process.poll() is None:
        return True, "Monitoring is already running"

    if not os.path.exists(LIVE_MONITOR_FILE):
        return False, "live_monitor.py was not found"

    session_id = f"SESSION_{int(datetime.now().timestamp())}"

    try:
        process = subprocess.Popen(
            [
                sys.executable,
                LIVE_MONITOR_FILE,
                str(student_id),
                str(session_id),
                str(exam_name)
            ],
            cwd=BASE_DIR
        )

        monitor_processes[student_id] = process

        print(
            f"Started monitor for {student_id} | "
            f"Exam: {exam_name} | Session: {session_id}"
        )

        return True, session_id

    except Exception as error:
        print("Live monitor start error:", error)
        return False, str(error)


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
            student_id=student_id
        )

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

    if not session.get("student_id"):
        return redirect("/")

    return render_template(
        "student_exam.html",
        exam_id=exam_id,
        student_id=session.get("student_id")
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

@app.route("/api/status")
def api_status():

    students = [

        normalize_student(
            student
        )

        for student in get_students()

    ]


    online_students = [

        student

        for student in students

        if str(
            student.get(
                "status",
                ""
            )
        ).upper() == "ONLINE"

    ]


    return jsonify({

        "success": True,

        "system":
            "PROCTIFY",

        "last_update":
            datetime.now().strftime(
                "%H:%M:%S"
            ),

        "student_count":
            len(
                online_students
            ),

        "students":
            students,

        "online_students":
            len(
                online_students
            )

    })
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
                    e.created_at
                FROM exams e
                INNER JOIN exam_assignments a
                    ON a.exam_id = e.exam_id
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

    try:

        submissions = []

        # ----------------------------------------------------
        # Read submission files
        # ----------------------------------------------------

        if os.path.exists(
            SUBMISSIONS_DIR
        ):

            for filename in os.listdir(
                SUBMISSIONS_DIR
            ):

                if not filename.endswith(
                    ".json"
                ):

                    continue


                submission_file = os.path.join(
                    SUBMISSIONS_DIR,
                    filename
                )


                try:

                    with open(
                        submission_file,
                        "r",
                        encoding="utf-8"
                    ) as file:

                        submission = json.load(
                            file
                        )


                    if isinstance(
                        submission,
                        dict
                    ):

                        submissions.append(
                            submission
                        )


                except Exception as error:

                    print(
                        "Submission read error:",
                        filename,
                        error
                    )


        # ----------------------------------------------------
        # Calculate counts
        # ----------------------------------------------------

        submitted_count = len(
            submissions
        )

        evaluated_count = 0

        pending_count = 0


        for submission in submissions:

            evaluated = submission.get(
                "evaluated",
                False
            )


            if evaluated is True:

                evaluated_count += 1

            else:

                pending_count += 1


        # ----------------------------------------------------
        # Response
        # ----------------------------------------------------

        return jsonify({

            "success": True,

            "submitted":
                submitted_count,

            "evaluated":
                evaluated_count,

            "pending":
                pending_count,

            "submissions":
                submissions

        })


    except Exception as error:

        print(
            "Submissions API error:",
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
# ============================================================
# SUBMIT EXAM ANSWERS
# ============================================================

@app.route(
    "/api/exams/<exam_id>/submit",
    methods=["POST"]
)
def submit_exam(exam_id):

    try:

        # ----------------------------------------------------
        # Locate exam
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

                "error":
                    "Exam not found"

            }), 404


        # ----------------------------------------------------
        # Read exam
        # ----------------------------------------------------

        with open(
            exam_file,
            "r",
            encoding="utf-8"
        ) as file:

            exam = json.load(file)


        # ----------------------------------------------------
        # Read submitted data
        # ----------------------------------------------------

        data = request.get_json()


        if not isinstance(
            data,
            dict
        ):

            return jsonify({

                "success": False,

                "error":
                    "Invalid submission data"

            }), 400


        student_id = str(
            data.get(
                "student_id",
                ""
            )
        ).strip()


        answers = data.get(
            "answers",
            {}
        )


        if not student_id:

            return jsonify({

                "success": False,

                "error":
                    "Student ID is required"

            }), 400


        if not isinstance(
            answers,
            dict
        ):

            return jsonify({

                "success": False,

                "error":
                    "Answers must be an object"

            }), 400


        # ----------------------------------------------------
        # Evaluate answers
        # ----------------------------------------------------

        questions = exam.get(
            "questions",
            []
        )


        correct_count = 0

        answered_count = 0


        for index, question in enumerate(
            questions
        ):

            question_key = str(index)


            student_answer = answers.get(
                question_key
            )


            if student_answer:

                answered_count += 1


            correct_answer = question.get(
                "correct_answer"
            )


            if (
                student_answer is not None
                and
                str(student_answer).upper()
                ==
                str(correct_answer).upper()
            ):

                correct_count += 1


        # ----------------------------------------------------
        # Calculate score
        # ----------------------------------------------------

        total_questions = len(
            questions
        )


        if total_questions > 0:

            score = round(
                (
                    correct_count /
                    total_questions
                ) * 100
            )

        else:

            score = 0


        # ----------------------------------------------------
        # Create submission ID
        # ----------------------------------------------------

        submission_id = (

            "SUBMISSION_" +

            datetime.now().strftime(
                "%Y%m%d_%H%M%S_%f"
            )

        )


        # ----------------------------------------------------
        # Submission data
        # ----------------------------------------------------

        submission = {

            "submission_id":
                submission_id,

            "exam_id":
                exam_id,

            "exam_name":
                exam.get(
                    "exam_name",
                    ""
                ),

            "student_id":
                student_id,

            "answers":
                answers,

            "total_questions":
                total_questions,

            "answered_questions":
                answered_count,

            "correct_answers":
                correct_count,

            "score":
                score,

            "evaluated":
                True,

            "status":
                "SUBMITTED",

            "submitted_at":
                datetime.now().isoformat()

        }


        # ----------------------------------------------------
        # Save submission
        # ----------------------------------------------------

        submission_file = os.path.join(

            SUBMISSIONS_DIR,

            submission_id + ".json"

        )


        with open(
            submission_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                submission,
                file,
                indent=4
            )


        # ----------------------------------------------------
        # Console information
        # ----------------------------------------------------

        print()
        print(
            "========================================"
        )
        print(
            "       PROCTIFY EXAM SUBMITTED"
        )
        print(
            "========================================"
        )
        print(
            "Student:",
            student_id
        )
        print(
            "Exam:",
            exam.get(
                "exam_name",
                ""
            )
        )
        print(
            "Score:",
            f"{score}%"
        )
        print(
            "Correct:",
            f"{correct_count}/{total_questions}"
        )
        print(
            "Saved:",
            submission_file
        )
        print(
            "========================================"
        )
        print()


        return jsonify({

            "success": True,

            "message":
                "Exam submitted successfully",

            "submission":
                submission

        })


    except Exception as error:

        print(
            "Exam submission error:",
            error
        )


        return jsonify({

            "success": False,

            "error":
                str(error)

        }), 500

# ============================================================
# ============================================================
# MONITOR STUDENT
# ============================================================

@app.route(
    "/monitor/<student_id>"
)
def monitor_student(student_id):

    students = get_students()


    selected_student = None


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

            selected_student = student

            break


    if selected_student is None:

        return (
            "Student not found",
            404
        )


    session_id = selected_student.get(
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


    return render_template(

        "monitor.html",

        student=selected_student,

        violations=violations,

        evidence=evidence,

        trust_history=trust_history

    )


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

        host="127.0.0.1",

        port=5000,

        debug=True

    )