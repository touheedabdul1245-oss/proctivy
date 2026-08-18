import cv2
import mediapipe as mp

# ============================================================
# PROCTIFY - HAND LANDMARK TEST
# ============================================================

MODEL_PATH = "models/hand_landmarker/hand_landmarker.task"

BaseOptions = mp.tasks.BaseOptions
HandLandmarker = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode


# ------------------------------------------------------------
# Hand Landmarker
# ------------------------------------------------------------

options = HandLandmarkerOptions(
    base_options=BaseOptions(
        model_asset_path=MODEL_PATH
    ),
    running_mode=VisionRunningMode.IMAGE,
    num_hands=2
)

landmarker = HandLandmarker.create_from_options(options)


# ------------------------------------------------------------
# Webcam
# ------------------------------------------------------------

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("ERROR: Could not open webcam.")
    exit()


print()
print("========================================")
print("PROCTIFY HAND LANDMARK TEST")
print("========================================")
print("Show your hand to the camera.")
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
    # Convert frame
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
    # Detect hands
    # --------------------------------------------------------

    result = landmarker.detect(mp_image)

    hand_count = len(result.hand_landmarks)

    # --------------------------------------------------------
    # Draw landmarks
    # --------------------------------------------------------

    for hand in result.hand_landmarks:

        # Draw all 21 landmarks
        for landmark in hand:

            x = int(landmark.x * w)
            y = int(landmark.y * h)

            cv2.circle(
                frame,
                (x, y),
                4,
                (0, 255, 0),
                -1
            )

        # Draw connections
        connections = [
            (0, 1), (1, 2), (2, 3), (3, 4),
            (0, 5), (5, 6), (6, 7), (7, 8),
            (5, 9), (9, 10), (10, 11), (11, 12),
            (9, 13), (13, 14), (14, 15), (15, 16),
            (13, 17), (17, 18), (18, 19), (19, 20),
            (0, 17)
        ]

        for start, end in connections:

            x1 = int(hand[start].x * w)
            y1 = int(hand[start].y * h)

            x2 = int(hand[end].x * w)
            y2 = int(hand[end].y * h)

            cv2.line(
                frame,
                (x1, y1),
                (x2, y2),
                (255, 0, 0),
                2
            )


    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    cv2.putText(
        frame,
        f"Hands: {hand_count}",
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 255),
        2
    )


    # --------------------------------------------------------
    # Display
    # --------------------------------------------------------

    cv2.imshow(
        "PROCTIFY - Hand Landmarks",
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

print("Hand landmark test stopped.")