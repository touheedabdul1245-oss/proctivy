import cv2
import os
import uuid
import time
import threading
import numpy as np
from flask import Flask, Response
import sounddevice as sd
import mediapipe as mp
import json
import sys
import re
from ultralytics import YOLO
from monitoring.monitoring_engine import MonitoringEngine

# ============================================================
# SHARED LIVE VIDEO FRAME
# ============================================================

latest_frame = None

frame_lock = threading.Lock()



# ============================================================
# PROCTIFY - LIVE MONITOR
# YOLO + FACE + GAZE + HAND + HEAD POSE + AUDIO
# + REAL-TIME TEACHER DASHBOARD CONNECTION
# ============================================================


# ============================================================
# STUDENT / SESSION INFORMATION
# ============================================================

# Usage:
#
# python live_monitor.py STUDENT_ID SESSION_ID EXAM_NAME
#
# Example:
#
# python live_monitor.py STU001 SESSION001 "Python Exam"
#
# If no arguments are supplied, test values are used.

if len(sys.argv) >= 2:
    STUDENT_ID = sys.argv[1]
else:
    STUDENT_ID = "STUDENT_001"

if len(sys.argv) >= 3:
    SESSION_ID = sys.argv[2]
else:
    SESSION_ID = f"SESSION_{int(time.time())}"

if len(sys.argv) >= 4:
    EXAM_NAME = " ".join(sys.argv[3:])
else:
    EXAM_NAME = "PROCTIFY EXAM"


print()
print("========================================")
print("        PROCTIFY LIVE MONITOR")
print("========================================")
print(f"Student ID : {STUDENT_ID}")
print(f"Session ID : {SESSION_ID}")
print(f"Exam       : {EXAM_NAME}")
print("========================================")
print()


# ============================================================
# PATHS
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

YOLO_MODEL_PATH = os.path.join(
    BASE_DIR,
    "models",
    "phone_detector.pt"
)


FACE_MODEL_PATH = os.path.join(
    BASE_DIR,
    "models",
    "face_landmarker",
    "face_landmarker.task"
)


HAND_MODEL_PATH = os.path.join(
    BASE_DIR,
    "models",
    "hand_landmarker",
    "hand_landmarker.task"
)


SHARED_DATA_DIR = os.path.join(
    BASE_DIR,
    "shared_data"
)


LIVE_STATUS_FILE = os.path.join(
    SHARED_DATA_DIR,
    "live_status.json"
)


os.makedirs(
    SHARED_DATA_DIR,
    exist_ok=True
)


# ============================================================
# MONITORING ENGINE
# ============================================================

print("Starting monitoring engine...")

monitoring_engine = MonitoringEngine(
    SESSION_ID,
    STUDENT_ID,
    EXAM_NAME
)

print("Monitoring engine ready.")


# ============================================================
# LIVE STATUS LOCK
# ============================================================

status_lock = threading.Lock()
# ============================================================
# MONITORING HEALTH
# ============================================================

# ============================================================
# MONITORING HEALTH
# ============================================================

CAMERA_AVAILABLE = False
AI_AVAILABLE = False
audio_available = False


# ============================================================
# RISK LEVEL
# ============================================================

def calculate_risk(score):

    if score >= 80:
        return "LOW"

    elif score >= 50:
        return "MEDIUM"

    else:
        return "HIGH"


# ============================================================
# UPDATE LIVE STATUS JSON
# ============================================================
def update_live_status(
    phone_count,
    person_count,
    face_count,
    hand_count,
    gaze,
    head_direction,
    audio_text,
    audio_volume,
    eyes_closed,
    closed_duration,
    yaw,
    pitch,
    calibration_complete,
    last_event="",
    status="ONLINE"
):

    # ========================================================
    # MYSQL IS THE PRIMARY LIVE DATA SOURCE
    # ========================================================

    trust_score = int(
        monitoring_engine.get_trust_score()
    )

    risk_level = str(
        monitoring_engine.get_risk_level()
    )


    # ========================================================
    # SYSTEM READINESS STATUS
    # ========================================================

    camera_available = bool(
        CAMERA_AVAILABLE
        and
        status == "ONLINE"
    )

    audio_ready = bool(
        audio_available
        and
        status == "ONLINE"
    )

    ai_ready = bool(
        AI_AVAILABLE
        and
        status == "ONLINE"
    )

    tab_available = bool(
        status == "ONLINE"
    )


    try:

        monitoring_engine.update_live_student(

            student_id=
                STUDENT_ID,

            exam_name=
                EXAM_NAME,

            status=
                status,

            phone=
                bool(phone_count > 0),

            phone_count=
                int(phone_count),

            person_count=
                int(person_count),

            face_count=
                int(face_count),

            hand_count=
                int(hand_count),

            gaze=
                str(gaze),

            head_direction=
                str(head_direction),

            audio=
                str(audio_text),

            audio_volume=
                float(audio_volume),


            # ------------------------------------------------
            # SYSTEM READINESS FLAGS
            # ------------------------------------------------

            camera_available=
                camera_available,

            audio_available=
                audio_ready,

            ai_available=
                ai_ready,

            tab_available=
                tab_available,


            trust_score=
                trust_score,

            risk_level=
                risk_level

        )

    except Exception as error:

        print()

        print(
            "MYSQL LIVE STUDENT UPDATE ERROR:"
        )

        print(
            error
        )

        print()


# ============================================================
# MARK STUDENT OFFLINE
# ============================================================

def mark_student_offline():

    # ========================================================
    # MYSQL IS THE PRIMARY LIVE DATA SOURCE
    # ========================================================

    try:

        monitoring_engine.update_live_student(

            student_id=
                STUDENT_ID,

            exam_name=
                EXAM_NAME,

            status=
                "OFFLINE",

            phone=
                False,

            phone_count=
                0,

            person_count=
                0,

            face_count=
                0,

            hand_count=
                0,

            gaze=
                "NO FACE",

            head_direction=
                "NO FACE",

            audio=
                "STOPPED",

            audio_volume=
                0.0,


            # ------------------------------------------------
            # SYSTEM READINESS FLAGS
            # ------------------------------------------------

            camera_available=
                False,

            audio_available=
                False,

            ai_available=
                False,

            tab_available=
                False,


            trust_score=
                int(
                    monitoring_engine.get_trust_score()
                ),

            risk_level=
                str(
                    monitoring_engine.get_risk_level()
                )

        )

        print(
            "Student marked OFFLINE in MySQL."
        )

    except Exception as error:

        print(
            "MYSQL OFFLINE UPDATE ERROR:",
            error
        )
# ============================================================
# YOLO
# ============================================================

print("Loading YOLO model...")

yolo_model = YOLO(
    YOLO_MODEL_PATH
)

print(
    "YOLO model loaded successfully."
)
AI_AVAILABLE = True


CLASS_NAMES = [
    "book",
    "extra_person",
    "laptop",
    "phone",
    "student"
]


# ============================================================
# MEDIAPIPE
# ============================================================

BaseOptions = mp.tasks.BaseOptions

VisionRunningMode = (
    mp.tasks.vision.RunningMode
)

FaceLandmarker = (
    mp.tasks.vision.FaceLandmarker
)

FaceLandmarkerOptions = (
    mp.tasks.vision.FaceLandmarkerOptions
)

HandLandmarker = (
    mp.tasks.vision.HandLandmarker
)

HandLandmarkerOptions = (
    mp.tasks.vision.HandLandmarkerOptions
)


# ============================================================
# FACE LANDMARKER
# ============================================================

face_options = FaceLandmarkerOptions(

    base_options=BaseOptions(
        model_asset_path=FACE_MODEL_PATH
    ),

    running_mode=VisionRunningMode.IMAGE,

    num_faces=2
)


face_landmarker = (
    FaceLandmarker.create_from_options(
        face_options
    )
)


print(
    "Face landmarker loaded successfully."
)


# ============================================================
# HAND LANDMARKER
# ============================================================

hand_options = HandLandmarkerOptions(

    base_options=BaseOptions(
        model_asset_path=HAND_MODEL_PATH
    ),

    running_mode=VisionRunningMode.IMAGE,

    num_hands=2
)


hand_landmarker = (
    HandLandmarker.create_from_options(
        hand_options
    )
)


print(
    "Hand landmarker loaded successfully."
)


# ============================================================
# AUDIO SETTINGS
# ============================================================

SAMPLE_RATE = 48000

CHANNELS = 1

SPEECH_THRESHOLD = 0.0025

VIOLATION_TIME = 1.5


# ============================================================
# AUDIO STATE
# ============================================================

speaking_start = None

audio_violation = False

audio_volume = 0.0

audio_lock = threading.Lock()


# ============================================================
# AUDIO CALLBACK
# ============================================================

def audio_callback(
    indata,
    frames,
    time_info,
    status
):

    global speaking_start

    global audio_violation

    global audio_volume


    if status:

        print(
            "Audio status:",
            status
        )


    volume = np.sqrt(
        np.mean(
            np.square(indata)
        )
    )


    current_time = time.time()


    with audio_lock:

        audio_volume = float(
            volume
        )


        if volume > SPEECH_THRESHOLD:

            if speaking_start is None:

                speaking_start = (
                    current_time
                )


            speaking_duration = (
                current_time -
                speaking_start
            )


            if (
                speaking_duration >=
                VIOLATION_TIME
            ):

                audio_violation = True


        else:

            speaking_start = None

            audio_violation = False


# ============================================================
# START AUDIO MONITOR
# ============================================================

try:

    audio_stream = sd.InputStream(

        samplerate=SAMPLE_RATE,

        channels=CHANNELS,

        callback=audio_callback
    )

    audio_stream.start()

    print(
        "Audio monitoring started successfully."
    )

    audio_available = True


except Exception as error:

    print(
        "WARNING: Audio could not start."
    )

    print(
        "Audio error:",
        error
    )

    audio_stream = None

    audio_available = False


# ============================================================
# GAZE SETTINGS
# ============================================================

LEFT_THRESHOLD = 0.60

RIGHT_THRESHOLD = 0.40

DOWN_THRESHOLD = 0.55

EYES_CLOSED_TIME = 2.0

EYE_CLOSED_THRESHOLD = 8

eyes_closed_start = None


# ============================================================
# EYE LANDMARKS
# ============================================================

LEFT_EYE_LEFT = 33

LEFT_EYE_RIGHT = 133

LEFT_EYE_TOP = 159

LEFT_EYE_BOTTOM = 145

LEFT_IRIS = 468


RIGHT_EYE_LEFT = 362

RIGHT_EYE_RIGHT = 263

RIGHT_EYE_TOP = 386

RIGHT_EYE_BOTTOM = 374

RIGHT_IRIS = 473


# ============================================================
# GAZE FUNCTIONS
# ============================================================

def get_point(
    landmark,
    width,
    height
):

    return np.array([

        landmark.x * width,

        landmark.y * height
    ])


def horizontal_ratio(
    face,
    left_idx,
    right_idx,
    iris_idx,
    width,
    height
):

    left = get_point(
        face[left_idx],
        width,
        height
    )

    right = get_point(
        face[right_idx],
        width,
        height
    )

    iris = get_point(
        face[iris_idx],
        width,
        height
    )


    eye_width = np.linalg.norm(
        right - left
    )


    if eye_width == 0:

        return 0.5


    return np.linalg.norm(
        iris - left
    ) / eye_width


def vertical_ratio(
    face,
    top_idx,
    bottom_idx,
    iris_idx,
    width,
    height
):

    top = get_point(
        face[top_idx],
        width,
        height
    )

    bottom = get_point(
        face[bottom_idx],
        width,
        height
    )

    iris = get_point(
        face[iris_idx],
        width,
        height
    )


    eye_height = np.linalg.norm(
        bottom - top
    )


    if eye_height == 0:

        return 0.5


    return np.linalg.norm(
        iris - top
    ) / eye_height


def get_gaze(
    face,
    width,
    height
):

    global eyes_closed_start


    # --------------------------------------------------------
    # Horizontal gaze
    # --------------------------------------------------------

    left_horizontal = horizontal_ratio(

        face,

        LEFT_EYE_LEFT,

        LEFT_EYE_RIGHT,

        LEFT_IRIS,

        width,

        height
    )


    right_horizontal = horizontal_ratio(

        face,

        RIGHT_EYE_LEFT,

        RIGHT_EYE_RIGHT,

        RIGHT_IRIS,

        width,

        height
    )


    horizontal_value = (

        left_horizontal +

        right_horizontal

    ) / 2


    # --------------------------------------------------------
    # Vertical gaze
    # --------------------------------------------------------

    left_vertical = vertical_ratio(

        face,

        LEFT_EYE_TOP,

        LEFT_EYE_BOTTOM,

        LEFT_IRIS,

        width,

        height
    )


    right_vertical = vertical_ratio(

        face,

        RIGHT_EYE_TOP,

        RIGHT_EYE_BOTTOM,

        RIGHT_IRIS,

        width,

        height
    )


    vertical_value = (

        left_vertical +

        right_vertical

    ) / 2


    # --------------------------------------------------------
    # Eye closure
    # --------------------------------------------------------

    left_top = get_point(

        face[LEFT_EYE_TOP],

        width,

        height
    )


    left_bottom = get_point(

        face[LEFT_EYE_BOTTOM],

        width,

        height
    )


    right_top = get_point(

        face[RIGHT_EYE_TOP],

        width,

        height
    )


    right_bottom = get_point(

        face[RIGHT_EYE_BOTTOM],

        width,

        height
    )


    left_eye_height = np.linalg.norm(

        left_bottom -

        left_top
    )


    right_eye_height = np.linalg.norm(

        right_bottom -

        right_top
    )


    eyes_closed = (

        left_eye_height <

        EYE_CLOSED_THRESHOLD

        and

        right_eye_height <

        EYE_CLOSED_THRESHOLD
    )


    # --------------------------------------------------------
    # Closed-eye timer
    # --------------------------------------------------------

    if eyes_closed:

        if eyes_closed_start is None:

            eyes_closed_start = time.time()


        closed_duration = (

            time.time() -

            eyes_closed_start
        )


    else:

        eyes_closed_start = None

        closed_duration = 0


    # --------------------------------------------------------
    # Gaze logic
    # --------------------------------------------------------

    if closed_duration >= EYES_CLOSED_TIME:

        gaze = "LOOKING DOWN"

    elif horizontal_value > LEFT_THRESHOLD:

        gaze = "LOOKING LEFT"

    elif horizontal_value < RIGHT_THRESHOLD:

        gaze = "LOOKING RIGHT"

    elif vertical_value > DOWN_THRESHOLD:

        gaze = "LOOKING DOWN"

    else:

        gaze = "CENTER"


    return (

        gaze,

        horizontal_value,

        vertical_value,

        eyes_closed,

        closed_duration
    )


# ============================================================
# HEAD POSE MODEL
# ============================================================

model_points = np.array([

    (0.0, 0.0, 0.0),

    (0.0, -330.0, -65.0),

    (-225.0, 170.0, -135.0),

    (225.0, 170.0, -135.0),

    (-150.0, -150.0, -125.0),

    (150.0, -150.0, -125.0)

], dtype=np.float64)


# ============================================================
# HEAD POSE CALIBRATION
# ============================================================

CALIBRATION_FRAMES = 30

calibration_yaws = []

calibration_pitches = []

calibration_complete = False

calibration_yaw = 0.0

calibration_pitch = 0.0


# ============================================================
# GET RAW HEAD ANGLES
# ============================================================

def get_head_angles(
    face,
    width,
    height
):

    focal_length = width


    camera_matrix = np.array([

        [focal_length, 0, width / 2],

        [0, focal_length, height / 2],

        [0, 0, 1]

    ], dtype=np.float64)


    dist_coeffs = np.zeros(
        (4, 1),
        dtype=np.float64
    )


    image_points = np.array([

        (
            face[1].x * width,
            face[1].y * height
        ),

        (
            face[152].x * width,
            face[152].y * height
        ),

        (
            face[33].x * width,
            face[33].y * height
        ),

        (
            face[263].x * width,
            face[263].y * height
        ),

        (
            face[61].x * width,
            face[61].y * height
        ),

        (
            face[291].x * width,
            face[291].y * height
        )

    ], dtype=np.float64)


    success, rotation_vector, translation_vector = (

        cv2.solvePnP(

            model_points,

            image_points,

            camera_matrix,

            dist_coeffs,

            flags=cv2.SOLVEPNP_ITERATIVE
        )
    )


    if not success:

        return None, None


    rotation_matrix, _ = cv2.Rodrigues(
        rotation_vector
    )


    angles = cv2.RQDecomp3x3(
        rotation_matrix
    )[0]


    pitch = float(
        angles[0]
    )

    yaw = float(
        angles[1]
    )


    return yaw, pitch


# ============================================================
# CLASSIFY CALIBRATED HEAD POSE
# ============================================================

def classify_head_pose(
    yaw,
    pitch
):

    yaw_difference = (

        yaw -

        calibration_yaw
    )


    pitch_difference = (

        pitch -

        calibration_pitch
    )


    YAW_THRESHOLD = 20

    DOWN_THRESHOLD = 15


    if yaw_difference > YAW_THRESHOLD:

        return "LOOK_RIGHT"


    elif yaw_difference < -YAW_THRESHOLD:

        return "LOOK_LEFT"


    elif pitch_difference > DOWN_THRESHOLD:

        return "LOOK_DOWN"


    else:

        return "LOOK_CENTER"


# ============================================================
# WEBCAM
# ============================================================

cap = cv2.VideoCapture(0)


if not cap.isOpened():

    print(
        "ERROR: Could not open webcam."
    )

    if audio_stream is not None:

        audio_stream.stop()

        audio_stream.close()

    monitoring_engine = None

    exit()
CAMERA_AVAILABLE = True

# ============================================================
# LIVE VIDEO STREAM SERVER
# ============================================================

video_app = Flask("proctify_live_video")

latest_frame = None
frame_lock = threading.Lock()


@video_app.route("/video_feed")
def video_feed():

    def generate():

        global latest_frame

        while True:

            with frame_lock:

                if latest_frame is None:
                    continue

                success, buffer = cv2.imencode(
                    ".jpg",
                    latest_frame
                )

                if not success:
                    continue

                frame_bytes = buffer.tobytes()

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + frame_bytes
                + b"\r\n"
            )

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


def start_video_server():

    # --------------------------------------------------------
    # Each student gets a different video port
    # STUDENT_001 -> 5001
    # STUDENT_002 -> 5002
    # STUDENT_003 -> 5003
    # --------------------------------------------------------

    try:
        student_match = re.search(
            r"(\d+)$",
            str(STUDENT_ID)
        )

        if student_match is None:
            raise ValueError(
                "Student ID has no trailing number."
            )

        student_number = int(
            student_match.group(1)
        )

    except (ValueError, IndexError):
        student_number = 1


    video_port = 5000 + student_number


    print(
        f"Student video server:"
        f" http://127.0.0.1:{video_port}/video_feed"
    )


    video_app.run(

        host="127.0.0.1",

        port=video_port,

        debug=False,

        threaded=True,

        use_reloader=False

    )


video_server_thread = threading.Thread(
    target=start_video_server,
    daemon=True
)

video_server_thread.start()

print(
    "Live video stream available at:"
)

print(
    "Student-specific video port is assigned automatically."
)
# ============================================================
# VIOLATION COOLDOWNS
# ============================================================

PHONE_COOLDOWN = 5

PERSON_COOLDOWN = 5

GAZE_COOLDOWN = 5

AUDIO_COOLDOWN = 5

NO_FACE_COOLDOWN = 5


last_phone_violation = 0

last_person_violation = 0

last_gaze_violation = 0

last_audio_violation = 0

last_noface_violation = 0


# ============================================================
# CURRENT EVENT
# ============================================================

last_event = "MONITORING STARTED"


# ============================================================
# INITIAL LIVE STATUS
# ============================================================

update_live_status(

    phone_count=0,

    person_count=1,

    face_count=0,

    hand_count=0,

    gaze="NO FACE",

    head_direction="NO FACE",

    audio_text="STARTING",

    audio_volume=0,

    eyes_closed=False,

    closed_duration=0,

    yaw=0,

    pitch=0,

    calibration_complete=False,

    last_event="MONITORING STARTED",

    status="ONLINE"
)


# ============================================================
# MAIN LOOP
# ============================================================

try:

    while True:

        ret, frame = cap.read()


        if not ret:

            print(
                "ERROR: Could not read webcam."
            )

            break


        height, width = frame.shape[:2]


        # ====================================================
        # YOLO
        # ====================================================

        results = yolo_model(

            frame,

            conf=0.40,

            verbose=False
        )


        phone_count = 0

        person_count = 0

        current_time = time.time()


        for result in results:

            for box in result.boxes:

                class_id = int(
                    box.cls[0]
                )


                confidence = float(
                    box.conf[0]
                )


                if class_id >= len(
                    CLASS_NAMES
                ):

                    continue


                class_name = (
                    CLASS_NAMES[class_id]
                )


                # ------------------------------------------------
                # Hide book
                # ------------------------------------------------

                if class_name == "book":

                    continue


                if class_name == "phone":

                    phone_count += 1


                elif class_name in [

                    "student",

                    "extra_person"

                ]:

                    person_count += 1


                x1, y1, x2, y2 = map(

                    int,

                    box.xyxy[0]
                )


                cv2.rectangle(

                    frame,

                    (x1, y1),

                    (x2, y2),

                    (0, 255, 0),

                    2
                )


                cv2.putText(

                    frame,

                    f"{class_name} {confidence:.2f}",

                    (
                        x1,
                        max(
                            y1 - 10,
                            20
                        )
                    ),

                    cv2.FONT_HERSHEY_SIMPLEX,

                    0.6,

                    (0, 255, 0),

                    2
                )


        # ====================================================
        # PHONE VIOLATION
        # ====================================================

        if phone_count > 0:

            if calibration_complete:

                if (

                    current_time -

                    last_phone_violation

                    >= PHONE_COOLDOWN

                ):

                    monitoring_engine.record_violation(

                        "PHONE_DETECTED",

                        severity="HIGH",

                        description=(
                            "Phone detected "
                            "during examination."
                        )
                    )


                    last_phone_violation = (
                        current_time
                    )


                    last_event = (
                        "PHONE DETECTED"
                    )


                    try:

                        monitoring_engine.save_evidence(

                            frame,

                            "PHONE_DETECTED"
                        )

                    except Exception as error:

                        print(
                            "Evidence error:",
                            error
                        )


        # ====================================================
        # MULTIPLE PERSON VIOLATION
        # ====================================================

        if person_count > 1:

            if calibration_complete:

                if (

                    current_time -

                    last_person_violation

                    >= PERSON_COOLDOWN

                ):

                    monitoring_engine.record_violation(

                        "MULTIPLE_PERSON",

                        severity="HIGH",

                        description=(
                            "Multiple persons "
                            "detected in examination area."
                        )
                    )


                    last_person_violation = (
                        current_time
                    )


                    last_event = (
                        "MULTIPLE PERSONS"
                    )


                    try:

                        monitoring_engine.save_evidence(

                            frame,

                            "MULTIPLE_PERSON"
                        )

                    except Exception as error:

                        print(
                            "Evidence error:",
                            error
                        )


        # ====================================================
        # MEDIAPIPE IMAGE
        # ====================================================

        rgb = cv2.cvtColor(

            frame,

            cv2.COLOR_BGR2RGB
        )


        mp_image = mp.Image(

            image_format=mp.ImageFormat.SRGB,

            data=rgb
        )


        # ====================================================
        # FACE
        # ====================================================

        face_result = (

            face_landmarker.detect(

                mp_image
            )
        )


        face_count = len(

            face_result.face_landmarks
        )


        gaze = "NO FACE"

        head_direction = "NO FACE"

        horizontal_value = 0

        vertical_value = 0

        yaw = 0

        pitch = 0

        eyes_closed = False

        closed_duration = 0


        # ====================================================
        # FACE PROCESSING
        # ====================================================

        if face_count > 0:

            face = (

                face_result.face_landmarks[0]
            )


            # ------------------------------------------------
            # GAZE
            # ------------------------------------------------

            (

                gaze,

                horizontal_value,

                vertical_value,

                eyes_closed,

                closed_duration

            ) = get_gaze(

                face,

                width,

                height
            )


            # ------------------------------------------------
            # HEAD ANGLES
            # ------------------------------------------------

            raw_yaw, raw_pitch = (

                get_head_angles(

                    face,

                    width,

                    height
                )
            )


            if raw_yaw is not None:

                yaw = raw_yaw

                pitch = raw_pitch


                # ============================================
                # CALIBRATION
                # ============================================

                if not calibration_complete:

                    calibration_yaws.append(
                        raw_yaw
                    )

                    calibration_pitches.append(
                        raw_pitch
                    )


                    progress = len(
                        calibration_yaws
                    )


                    cv2.putText(

                        frame,

                        "CALIBRATING - LOOK STRAIGHT",

                        (20, 300),

                        cv2.FONT_HERSHEY_SIMPLEX,

                        0.8,

                        (0, 255, 255),

                        2
                    )


                    cv2.putText(

                        frame,

                        f"Calibration: {progress}/{CALIBRATION_FRAMES}",

                        (20, 335),

                        cv2.FONT_HERSHEY_SIMPLEX,

                        0.7,

                        (0, 255, 255),

                        2
                    )


                    if progress >= CALIBRATION_FRAMES:

                        calibration_yaw = float(

                            np.mean(

                                calibration_yaws
                            )
                        )


                        calibration_pitch = float(

                            np.mean(

                                calibration_pitches
                            )
                        )


                        calibration_complete = True


                        last_event = (
                            "CALIBRATION COMPLETE"
                        )


                        print()

                        print(
                            "========================================"
                        )

                        print(
                            "HEAD CALIBRATION COMPLETE"
                        )

                        print(
                            f"Center Yaw   : "
                            f"{calibration_yaw:.2f}"
                        )

                        print(
                            f"Center Pitch : "
                            f"{calibration_pitch:.2f}"
                        )

                        print(
                            "========================================"
                        )

                        print()


                # ============================================
                # CLASSIFY AFTER CALIBRATION
                # ============================================

                if calibration_complete:

                    head_direction = (

                        classify_head_pose(

                            raw_yaw,

                            raw_pitch
                        )
                    )


            # ------------------------------------------------
            # DRAW IRIS
            # ------------------------------------------------

            for idx in [

                LEFT_IRIS,

                RIGHT_IRIS

            ]:

                point = face[idx]


                x = int(

                    point.x * width
                )


                y = int(

                    point.y * height
                )


                cv2.circle(

                    frame,

                    (x, y),

                    5,

                    (255, 0, 0),

                    -1
                )


        else:

            eyes_closed_start = None


            if not calibration_complete:

                cv2.putText(

                    frame,

                    "FACE REQUIRED FOR CALIBRATION",

                    (20, 300),

                    cv2.FONT_HERSHEY_SIMPLEX,

                    0.8,

                    (0, 0, 255),

                    2
                )


        # ====================================================
        # NO FACE VIOLATION
        # ====================================================

        if (

            face_count == 0

            and calibration_complete

        ):

            if (

                current_time -

                last_noface_violation

                >= NO_FACE_COOLDOWN

            ):

                monitoring_engine.record_violation(

                    "NO_FACE",

                    severity="MEDIUM",

                    description=(
                        "Student face "
                        "not detected."
                    )
                )


                last_noface_violation = (
                    current_time
                )


                last_event = (
                    "FACE NOT DETECTED"
                )


        # ====================================================
        # GAZE VIOLATION
        # ====================================================

        if (

            gaze in [

                "LOOKING_LEFT",

                "LOOKING_RIGHT",

                "LOOKING_DOWN"

            ]

            and calibration_complete

        ):

            if (

                current_time -

                last_gaze_violation

                >= GAZE_COOLDOWN

            ):

                if gaze == "LOOKING_LEFT":

                    violation_type = (
                        "LOOKING_LEFT"
                    )

                elif gaze == "LOOKING_RIGHT":

                    violation_type = (
                        "LOOKING_RIGHT"
                    )

                else:

                    violation_type = (
                        "LOOKING_DOWN"
                    )


                monitoring_engine.record_violation(

                    violation_type,

                    severity="MEDIUM",

                    description=(
                        f"Student gaze: "
                        f"{gaze}"
                    )
                )


                last_gaze_violation = (
                    current_time
                )


                last_event = (
                    gaze
                )


        # ====================================================
        # EYES CLOSED VIOLATION
        # ====================================================

        if (

            eyes_closed

            and

            closed_duration >= EYES_CLOSED_TIME

            and

            calibration_complete

        ):

            if (

                current_time -

                last_gaze_violation

                >= GAZE_COOLDOWN

            ):

                monitoring_engine.record_violation(

                    "EYES_CLOSED",

                    severity="MEDIUM",

                    description=(
                        "Eyes remained "
                        "closed for extended duration."
                    )
                )


                last_gaze_violation = (
                    current_time
                )


                last_event = (
                    "EYES CLOSED"
                )


        # ====================================================
        # HANDS
        # ====================================================

        hand_result = (

            hand_landmarker.detect(

                mp_image
            )
        )


        hand_count = len(

            hand_result.hand_landmarks
        )


        for hand in (

            hand_result.hand_landmarks
        ):

            for landmark in hand:

                x = int(

                    landmark.x * width
                )


                y = int(

                    landmark.y * height
                )


                cv2.circle(

                    frame,

                    (x, y),

                    3,

                    (0, 255, 255),

                    -1
                )


        # ====================================================
        # AUDIO STATUS
        # ====================================================

        with audio_lock:

            current_volume = (
                audio_volume
            )

            current_audio_violation = (
                audio_violation
            )


            if speaking_start is not None:

                speaking_duration = (

                    time.time() -

                    speaking_start
                )

            else:

                speaking_duration = 0


        if not audio_available:

            audio_text = "UNAVAILABLE"

        elif current_audio_violation:

            audio_text = "VIOLATION"

        elif speaking_start is not None:

            audio_text = (

                f"SPEAKING "
                f"{speaking_duration:.1f}s"
            )

        else:

            audio_text = "LISTENING"


        # ====================================================
        # AUDIO VIOLATION
        # ====================================================

        if current_audio_violation:

            if (

                current_time -

                last_audio_violation

                >= AUDIO_COOLDOWN

            ):

                monitoring_engine.record_violation(

                    "AUDIO_VIOLATION",

                    severity="MEDIUM",

                    description=(
                        "Continuous speech "
                        "detected during examination."
                    )
                )


                last_audio_violation = (
                    current_time
                )


                last_event = (
                    "AUDIO VIOLATION"
                )


                try:

                    monitoring_engine.save_evidence(

                        frame,

                        "AUDIO_VIOLATION"
                    )

                except Exception as error:

                    print(
                        "Evidence error:",
                        error
                    )


        # ====================================================
        # STATUS
        # ====================================================

        cv2.putText(

            frame,

            f"Phone: {phone_count}",

            (20, 30),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.65,

            (0, 255, 255),

            2
        )


        cv2.putText(

            frame,

            f"Persons: {person_count}",

            (20, 60),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.65,

            (0, 255, 255),

            2
        )


        cv2.putText(

            frame,

            f"Faces: {face_count}",

            (20, 90),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.65,

            (255, 255, 0),

            2
        )


        cv2.putText(

            frame,

            f"Hands: {hand_count}",

            (20, 120),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.65,

            (255, 255, 0),

            2
        )


        cv2.putText(

            frame,

            f"Gaze: {gaze}",

            (20, 155),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.65,

            (0, 255, 255),

            2
        )


        cv2.putText(

            frame,

            f"Head: {head_direction}",

            (20, 190),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.65,

            (0, 255, 0),

            2
        )


        cv2.putText(

            frame,

            f"Audio: {audio_text}",

            (20, 225),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.65,

            (0, 165, 255),

            2
        )


        cv2.putText(

            frame,

            f"Volume: {current_volume:.4f}",

            (20, 255),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.55,

            (255, 255, 255),

            2
        )


        cv2.putText(

            frame,

            f"Yaw: {yaw:.1f}",

            (20, 285),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.55,

            (255, 255, 255),

            2
        )


        cv2.putText(

            frame,

            f"Pitch: {pitch:.1f}",

            (20, 315),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.55,

            (255, 255, 255),

            2
        )


        # ====================================================
        # CALIBRATION STATUS
        # ====================================================

        if calibration_complete:

            cv2.putText(

                frame,

                "HEAD CALIBRATED",

                (20, 350),

                cv2.FONT_HERSHEY_SIMPLEX,

                0.65,

                (0, 255, 0),

                2
            )


        # ====================================================
        # AUDIO ALERT
        # ====================================================

        if current_audio_violation:

            cv2.putText(

                frame,

                "!!! AUDIO VIOLATION !!!",

                (20, 390),

                cv2.FONT_HERSHEY_SIMPLEX,

                0.8,

                (0, 0, 255),

                2
            )


        # ====================================================
        # TRUST SCORE
        # ====================================================

        trust_score = (
            monitoring_engine.get_trust_score()
        )


        risk_level = (
            monitoring_engine.get_risk_level()
        )


        cv2.putText(

            frame,

            f"Trust Score: {trust_score}",

            (20, 430),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.7,

            (0, 255, 0),

            2
        )


        cv2.putText(

            frame,

            f"Risk: {risk_level}",

            (20, 465),

            cv2.FONT_HERSHEY_SIMPLEX,

            0.7,

            (0, 255, 255),

            2
        )


        # ====================================================
        # SEND REAL DATA TO TEACHER DASHBOARD
        # ====================================================

        update_live_status(

            phone_count=phone_count,

            person_count=person_count,

            face_count=face_count,

            hand_count=hand_count,

            gaze=gaze,

            head_direction=head_direction,

            audio_text=audio_text,

            audio_volume=current_volume,

            eyes_closed=eyes_closed,

            closed_duration=closed_duration,

            yaw=yaw,

            pitch=pitch,

            calibration_complete=calibration_complete,

            last_event=last_event,

            status="ONLINE"
        )

        # ====================================================
# UPDATE BROWSER LIVE FRAME
# ====================================================

        with frame_lock:

            latest_frame = frame.copy()
        # ====================================================
        # DISPLAY
        # ====================================================

        cv2.imshow(

            "PROCTIFY - Live Monitor",

            frame
        )


        # ====================================================
        # QUIT
        # ====================================================

        if (

            cv2.waitKey(1) & 0xFF

            == ord("q")

        ):

            break


finally:

    # ========================================================
    # GENERATE FINAL SESSION REPORT
    # ========================================================

    report_path = monitoring_engine.generate_report()

    # ========================================================
    # MYSQL ALREADY CONTAINS THE REAL STUDENT / EXAM IDENTITY
    # ========================================================

    # The MonitoringEngine was initialized with STUDENT_ID and EXAM_NAME.
    # Any generated JSON report is only a secondary export; MySQL remains
    # the primary source for sessions, violations, evidence, and reports.

    # ========================================================
    # MARK STUDENT OFFLINE
    # ========================================================

    mark_student_offline()


    # ========================================================
    # CLEANUP
    # ========================================================

    cap.release()


    if audio_stream is not None:

        audio_stream.stop()

        audio_stream.close()


    face_landmarker.close()

    hand_landmarker.close()

    cv2.destroyAllWindows()


    print()
    print(
        "========================================"
    )
    print(
        "PROCTIFY LIVE MONITOR STOPPED"
    )
    print(
        f"Student: {STUDENT_ID}"
    )
    print(
        f"Final Trust Score: "
        f"{monitoring_engine.get_trust_score()}"
    )
    print(
        f"Final Risk: "
        f"{monitoring_engine.get_risk_level()}"
    )
    print(
        "========================================"
    )