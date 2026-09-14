import cv2
import os
import mediapipe as mp
import numpy as np


# ============================================================
# PATHS
# ============================================================

BASE_DIR = "evaluation/head_pose"

NORMAL_DIR = os.path.join(BASE_DIR, "normal")
DEVIATION_DIR = os.path.join(BASE_DIR, "deviation")

MODEL_PATH = "models/face_landmarker/face_landmarker.task"


# ============================================================
# MEDIAPIPE FACE LANDMARKER
# ============================================================

BaseOptions = mp.tasks.BaseOptions
FaceLandmarker = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

options = FaceLandmarkerOptions(
    base_options=BaseOptions(
        model_asset_path=MODEL_PATH
    ),
    running_mode=VisionRunningMode.IMAGE,
    num_faces=1
)


# ============================================================
# SAME 3D MODEL POINTS USED FOR HEAD POSE
# ============================================================

model_points = np.array([
    (0.0, 0.0, 0.0),            # Nose
    (0.0, -330.0, -65.0),        # Chin
    (-225.0, 170.0, -135.0),     # Left eye
    (225.0, 170.0, -135.0),      # Right eye
    (-150.0, -150.0, -125.0),    # Left mouth
    (150.0, -150.0, -125.0)      # Right mouth
], dtype=np.float64)


# ============================================================
# GET RAW HEAD ANGLES
# SAME LOGIC AS PROCTIFY
# ============================================================

def get_head_angles(face, width, height):

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

    success, rotation_vector, translation_vector = cv2.solvePnP(
        model_points,
        image_points,
        camera_matrix,
        dist_coeffs,
        flags=cv2.SOLVEPNP_ITERATIVE
    )

    if not success:
        return None, None

    rotation_matrix, _ = cv2.Rodrigues(
        rotation_vector
    )

    angles = cv2.RQDecomp3x3(
        rotation_matrix
    )[0]

    pitch = float(angles[0])
    yaw = float(angles[1])

    return yaw, pitch


# ============================================================
# CALCULATE CALIBRATION
# SAME LOGIC AS PROCTIFY
# ============================================================

def calculate_calibration():

    yaws = []
    pitches = []

    files = sorted(
        os.listdir(NORMAL_DIR)
    )

    with FaceLandmarker.create_from_options(options) as landmarker:

        for filename in files:

            if not filename.lower().endswith(
                (".jpg", ".jpeg", ".png")
            ):
                continue

            path = os.path.join(
                NORMAL_DIR,
                filename
            )

            image = cv2.imread(path)

            if image is None:
                continue

            height, width = image.shape[:2]

            rgb = cv2.cvtColor(
                image,
                cv2.COLOR_BGR2RGB
            )

            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=rgb
            )

            result = landmarker.detect(
                mp_image
            )

            if len(result.face_landmarks) == 0:
                continue

            face = result.face_landmarks[0]

            yaw, pitch = get_head_angles(
                face,
                width,
                height
            )

            if yaw is not None:

                yaws.append(yaw)
                pitches.append(pitch)

    if len(yaws) == 0:
        return None, None

    calibration_yaw = float(
        np.mean(yaws)
    )

    calibration_pitch = float(
        np.mean(pitches)
    )

    return calibration_yaw, calibration_pitch


# ============================================================
# SAME CLASSIFICATION LOGIC AS PROCTIFY
# ============================================================

def classify_head_pose(
    yaw,
    pitch,
    calibration_yaw,
    calibration_pitch
):

    yaw_difference = (
        yaw - calibration_yaw
    )

    pitch_difference = (
        pitch - calibration_pitch
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
# EVALUATE NORMAL IMAGES
# ============================================================

def evaluate_normal(
    calibration_yaw,
    calibration_pitch
):

    total = 0
    correct = 0

    with FaceLandmarker.create_from_options(options) as landmarker:

        for filename in os.listdir(NORMAL_DIR):

            if not filename.lower().endswith(
                (".jpg", ".jpeg", ".png")
            ):
                continue

            path = os.path.join(
                NORMAL_DIR,
                filename
            )

            image = cv2.imread(path)

            if image is None:
                continue

            total += 1

            height, width = image.shape[:2]

            rgb = cv2.cvtColor(
                image,
                cv2.COLOR_BGR2RGB
            )

            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=rgb
            )

            result = landmarker.detect(
                mp_image
            )

            if len(result.face_landmarks) == 0:
                continue

            face = result.face_landmarks[0]

            yaw, pitch = get_head_angles(
                face,
                width,
                height
            )

            if yaw is None:
                continue

            prediction = classify_head_pose(
                yaw,
                pitch,
                calibration_yaw,
                calibration_pitch
            )

            if prediction == "LOOK_CENTER":
                correct += 1

    return total, correct


# ============================================================
# EVALUATE DEVIATION IMAGES
# ============================================================

def evaluate_deviation(
    calibration_yaw,
    calibration_pitch
):

    total = 0
    correct = 0

    with FaceLandmarker.create_from_options(options) as landmarker:

        for filename in os.listdir(DEVIATION_DIR):

            if not filename.lower().endswith(
                (".jpg", ".jpeg", ".png")
            ):
                continue

            path = os.path.join(
                DEVIATION_DIR,
                filename
            )

            image = cv2.imread(path)

            if image is None:
                continue

            total += 1

            height, width = image.shape[:2]

            rgb = cv2.cvtColor(
                image,
                cv2.COLOR_BGR2RGB
            )

            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=rgb
            )

            result = landmarker.detect(
                mp_image
            )

            if len(result.face_landmarks) == 0:
                continue

            face = result.face_landmarks[0]

            yaw, pitch = get_head_angles(
                face,
                width,
                height
            )

            if yaw is None:
                continue

            prediction = classify_head_pose(
                yaw,
                pitch,
                calibration_yaw,
                calibration_pitch
            )

            if prediction != "LOOK_CENTER":
                correct += 1

    return total, correct


# ============================================================
# MAIN
# ============================================================

print("\n===================================")
print("   PROCTIFY HEAD POSE EVALUATION")
print("===================================\n")


# ------------------------------------------------------------
# CALIBRATION
# ------------------------------------------------------------

print("Calculating calibration from NORMAL images...")

calibration_yaw, calibration_pitch = (
    calculate_calibration()
)

if calibration_yaw is None:

    print("\nERROR: Could not calculate calibration.")
    print("Make sure normal images contain a detectable face.")
    exit()


print(
    f"Calibration Yaw   : {calibration_yaw:.2f}"
)

print(
    f"Calibration Pitch : {calibration_pitch:.2f}"
)


# ------------------------------------------------------------
# NORMAL
# ------------------------------------------------------------

normal_total, normal_correct = evaluate_normal(
    calibration_yaw,
    calibration_pitch
)

normal_accuracy = (
    normal_correct / normal_total * 100
    if normal_total > 0 else 0
)


print("\nNORMAL:")
print(f"  Total   : {normal_total}")
print(f"  Correct : {normal_correct}")
print(f"  Accuracy: {normal_accuracy:.2f}%")


# ------------------------------------------------------------
# DEVIATION
# ------------------------------------------------------------

deviation_total, deviation_correct = evaluate_deviation(
    calibration_yaw,
    calibration_pitch
)

deviation_accuracy = (
    deviation_correct / deviation_total * 100
    if deviation_total > 0 else 0
)


print("\nDEVIATION:")
print(f"  Total   : {deviation_total}")
print(f"  Correct : {deviation_correct}")
print(f"  Accuracy: {deviation_accuracy:.2f}%")


# ------------------------------------------------------------
# OVERALL
# ------------------------------------------------------------

total_images = (
    normal_total +
    deviation_total
)

total_correct = (
    normal_correct +
    deviation_correct
)

overall_accuracy = (
    total_correct /
    total_images *
    100
    if total_images > 0 else 0
)


print("\n-----------------------------------")
print(f"Total Images  : {total_images}")
print(f"Total Correct : {total_correct}")
print(f"Accuracy      : {overall_accuracy:.2f}%")
print("-----------------------------------")