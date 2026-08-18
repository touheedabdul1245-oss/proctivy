import cv2
import mediapipe as mp

# ============================================================
# PROCTIFY - FACE PRESENCE / MULTIPLE FACE DETECTION
# ============================================================

MODEL_PATH = "models/face_landmarker/face_landmarker.task"


# ------------------------------------------------------------
# MediaPipe Face Landmarker
# ------------------------------------------------------------

BaseOptions = mp.tasks.BaseOptions
FaceLandmarker = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode


options = FaceLandmarkerOptions(
    base_options=BaseOptions(
        model_asset_path=MODEL_PATH
    ),
    running_mode=VisionRunningMode.IMAGE,
    num_faces=5
)

landmarker = FaceLandmarker.create_from_options(options)


# ------------------------------------------------------------
# Webcam
# ------------------------------------------------------------

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("ERROR: Could not open webcam.")
    exit()


print()
print("========================================")
print("PROCTIFY FACE PRESENCE TEST")
print("========================================")
print("1 person  -> NORMAL")
print("2+ people -> MULTIPLE FACES")
print("0 people -> NO FACE")
print("Press Q to quit.")
print()


# ============================================================
# MAIN LOOP
# ============================================================

while True:

    ret, frame = cap.read()

    if not ret:
        print("ERROR: Could not read webcam.")
        break


    h, w = frame.shape[:2]


    # --------------------------------------------------------
    # Convert image
    # --------------------------------------------------------

    rgb = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2RGB
    )

    mp_image = mp.Image(
        image_format=mp.ImageFormat.SRGB,
        data=rgb
    )


    # --------------------------------------------------------
    # Detect faces
    # --------------------------------------------------------

    result = landmarker.detect(mp_image)


    face_count = len(result.face_landmarks)


    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if face_count == 0:

        status = "NO FACE"

    elif face_count == 1:

        status = "NORMAL"

    else:

        status = "MULTIPLE FACES"


    # --------------------------------------------------------
    # Draw face boxes
    # --------------------------------------------------------

    for face in result.face_landmarks:

        xs = []
        ys = []

        for landmark in face:

            xs.append(int(landmark.x * w))
            ys.append(int(landmark.y * h))


        if xs and ys:

            x1 = max(0, min(xs))
            y1 = max(0, min(ys))

            x2 = min(w - 1, max(xs))
            y2 = min(h - 1, max(ys))


            cv2.rectangle(
                frame,
                (x1, y1),
                (x2, y2),
                (255, 0, 0),
                2
            )


    # --------------------------------------------------------
    # Display face count
    # --------------------------------------------------------

    cv2.putText(
        frame,
        f"Faces: {face_count}",
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 0),
        2
    )


    # --------------------------------------------------------
    # Display status
    # --------------------------------------------------------

    cv2.putText(
        frame,
        status,
        (20, 85),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 255, 255),
        2
    )


    # --------------------------------------------------------
    # Show
    # --------------------------------------------------------

    cv2.imshow(
        "PROCTIFY - Face Presence",
        frame
    )


    # --------------------------------------------------------
    # Quit
    # --------------------------------------------------------

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break


# ============================================================
# CLEANUP
# ============================================================

cap.release()
landmarker.close()
cv2.destroyAllWindows()

print("Face presence test stopped.")