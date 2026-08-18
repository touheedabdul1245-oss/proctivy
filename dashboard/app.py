from flask import Flask, render_template, jsonify, request
import json
import os
import base64
import numpy as np
import cv2
import mediapipe as mp
from datetime import datetime


# ============================================================
# PROCTIFY - TEACHER DASHBOARD
# ============================================================

app = Flask(__name__)


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

def get_students():

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

    return render_template(
        "student_dashboard.html"
    )
# ============================================================
# STUDENT FACE VERIFICATION
# ============================================================

@app.route("/student/verify")
def student_verify():

    return render_template(
        "student_verify.html"
    )
# ============================================================
# STUDENT PRE-EXAM SYSTEM CHECK
# ============================================================

@app.route(
    "/student/precheck/<exam_id>",
    methods=["GET"]
)
def student_precheck(exam_id):

    return render_template(
        "student_precheck.html",
        exam_id=exam_id
    )
# ============================================================
# STUDENT EXAM PAGE
# ============================================================

@app.route(
    "/student/exam/<exam_id>",
    methods=["GET"]
)
def student_exam(exam_id):

    return render_template(
        "student_exam.html",
        exam_id=exam_id
    )
# ============================================================
# API - ALL STUDENTS
# ============================================================

@app.route("/api/students")
def api_students():

    students = get_students()

    students = [
        normalize_student(student)
        for student in students
    ]

    return jsonify({

        "success": True,

        "count": len(
            students
        ),

        "students": students

    })


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

            return jsonify({

                "success": True,

                "student": student

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

    data = load_live_data()

    students = [
        normalize_student(student)
        for student in get_students()
    ]

    return jsonify({

        "success": True,

        "system":
            data.get(
                "system",
                "PROCTIFY"
            ),

        "last_update":
            data.get(
                "last_update",
                ""
            ),

        "student_count":
            len(students),

        "students":
            students

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
        scheduled_start = str(
            data.get("scheduled_start", "")
        ).strip()

        if not scheduled_start:
            return jsonify({
                "success": False,
                "error": "Scheduled start time is required"
            }), 400


        # ----------------------------------------------------
        # VALIDATION
        # ----------------------------------------------------

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
                int(duration),

            "questions":
                questions,

            "question_count":
                len(questions),

            "status":
                "SCHEDULED",

            "created_at":
                datetime.now().isoformat(),

            "scheduled_start":
                scheduled_start

        }


        # ----------------------------------------------------
        # SAVE EXAM JSON
        # ----------------------------------------------------

        exam_file = os.path.join(
            EXAMS_DIR,
            exam_id + ".json"
        )


        with open(
            exam_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                exam_data,
                file,
                indent=4
            )


        print()
        print(
            "========================================"
        )
        print(
            "       PROCTIFY EXAM CREATED"
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
            len(questions)
        )
        print(
            "Duration:",
            duration,
            "minutes"
        )
        print(
            "Saved:",
            exam_file
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
# ============================================================
# GET AVAILABLE EXAMS
# ============================================================

@app.route(
    "/api/exams",
    methods=["GET"]
)
def get_exams():

    try:

        exams = []

        if os.path.exists(EXAMS_DIR):

            for filename in os.listdir(EXAMS_DIR):

                if not filename.endswith(".json"):
                    continue

                exam_file = os.path.join(
                    EXAMS_DIR,
                    filename
                )

                try:

                    with open(
                        exam_file,
                        "r",
                        encoding="utf-8"
                    ) as file:

                        exam = json.load(file)

                    if isinstance(exam, dict):

                        status = str(
                            exam.get("status", "")
                        ).upper()

                        scheduled_start = exam.get(
                            "scheduled_start"
                        )

                        if (
                            status == "SCHEDULED"
                            and scheduled_start
                        ):

                            try:

                                scheduled_time = datetime.fromisoformat(
                                    scheduled_start
                                )

                                if datetime.now() >= scheduled_time:

                                    exam["status"] = "ACTIVE"

                                    with open(
                                        exam_file,
                                        "w",
                                        encoding="utf-8"
                                    ) as update_file:

                                        json.dump(
                                            exam,
                                            update_file,
                                            indent=4
                                        )

                            except Exception as error:

                                print(
                                    "Scheduled exam check error:",
                                    filename,
                                    error
                                )

                        exams.append(exam)

                except Exception as error:

                    print(
                        "Exam read error:",
                        filename,
                        error
                    )


        # ----------------------------------------------------
        # Newest exam first
        # ----------------------------------------------------

        exams.sort(
            key=lambda exam:
                exam.get(
                    "created_at",
                    ""
                ),
            reverse=True
        )


        return jsonify({

            "success": True,

            "count": len(exams),

            "exams": exams

        })


    except Exception as error:

        print(
            "Get exams error:",
            error
        )

        return jsonify({

            "success": False,

            "error": str(error),

            "exams": []

        }), 500

# ============================================================
# GET SINGLE EXAM
# ============================================================

@app.route(
    "/api/exams/<exam_id>",
    methods=["GET"]
)
def get_single_exam(exam_id):

    try:

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


        with open(
            exam_file,
            "r",
            encoding="utf-8"
        ) as file:

            exam = json.load(
                file
            )


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
# MONITOR STUDENT
#
# NOTE:
# monitor.html will be created in the next step.
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


    return render_template(
        "monitor.html",
        student=selected_student
    )


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