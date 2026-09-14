import cv2
import os
import mediapipe as mp

# ==============================
# PATHS
# ==============================
BASE_DIR = "evaluation/face"

PRESENT_DIR = os.path.join(BASE_DIR, "face_present")
ABSENT_DIR = os.path.join(BASE_DIR, "face_absent")

# ==============================
# MEDIAPIPE FACE LANDMARKER
# ==============================
MODEL_PATH = "models/face_landmarker/face_landmarker.task"

BaseOptions = mp.tasks.BaseOptions
FaceLandmarker = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

options = FaceLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=MODEL_PATH),
    running_mode=VisionRunningMode.IMAGE,
    num_faces=2
)

# ==============================
# EVALUATION FUNCTION
# ==============================
def evaluate_folder(folder, expected_face):
    total = 0
    correct = 0

    with FaceLandmarker.create_from_options(options) as landmarker:

        for filename in os.listdir(folder):

            path = os.path.join(folder, filename)

            if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
                continue

            image = cv2.imread(path)

            if image is None:
                continue

            total += 1

            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=rgb
            )

            result = landmarker.detect(mp_image)

            detected_face = len(result.face_landmarks) > 0

            if detected_face == expected_face:
                correct += 1

    return total, correct


# ==============================
# RUN EVALUATION
# ==============================
print("\n===================================")
print("   PROCTIFY FACE EVALUATION")
print("===================================\n")

present_total, present_correct = evaluate_folder(
    PRESENT_DIR,
    True
)

absent_total, absent_correct = evaluate_folder(
    ABSENT_DIR,
    False
)

total = present_total + absent_total
correct = present_correct + absent_correct

accuracy = (correct / total * 100) if total > 0 else 0

print("Face Present:")
print(f"  Total   : {present_total}")
print(f"  Correct : {present_correct}")

print("\nFace Absent:")
print(f"  Total   : {absent_total}")
print(f"  Correct : {absent_correct}")

print("\n-----------------------------------")
print(f"Total Images : {total}")
print(f"Correct      : {correct}")
print(f"Accuracy     : {accuracy:.2f}%")
print("-----------------------------------")