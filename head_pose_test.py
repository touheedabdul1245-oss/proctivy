import cv2
import numpy as np
import mediapipe as mp


# ============================================================
# PROCTIFY - HEAD POSE DETECTION
# MediaPipe Face Landmarker + OpenCV solvePnP
# ============================================================


# ------------------------------------------------------------
# 1. FACE LANDMARK MODEL
# ------------------------------------------------------------

MODEL_PATH = "models/face_landmarker/face_landmarker.task"

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

landmarker = FaceLandmarker.create_from_options(options)


# ------------------------------------------------------------
# 2. 3D FACE MODEL POINTS
# ------------------------------------------------------------

model_points = np.array([
    (0.0, 0.0, 0.0),            # Nose
    (0.0, -330.0, -65.0),       # Chin
    (-225.0, 170.0, -135.0),    # Left eye
    (225.0, 170.0, -135.0),     # Right eye
    (-150.0, -150.0, -125.0),   # Left mouth
    (150.0, -150.0, -125.0)     # Right mouth
], dtype=np.float64)


# ------------------------------------------------------------
# 3. MEDIAPIPE LANDMARK INDEXES
# ------------------------------------------------------------

LANDMARK_IDS = [
    1,      # Nose
    152,    # Chin
    33,     # Left eye
    263,    # Right eye
    61,     # Left mouth
    291     # Right mouth
]


# ------------------------------------------------------------
# 4. CALIBRATION SETTINGS
# ------------------------------------------------------------

CALIBRATION_FRAMES = 60

calibration_yaw = []
calibration_pitch = []

calibrated_yaw = None
calibrated_pitch = None


# ------------------------------------------------------------
# 5. WEBCAM
# ------------------------------------------------------------

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("ERROR: Could not open webcam.")
    exit()

print()
print("========================================")
print("PROCTIFY HEAD POSE DETECTION")
print("========================================")
print("Look straight at the screen.")
print("Calibration will start automatically.")
print("Do not move during calibration.")
print()
print("Press Q to quit.")
print()


# ------------------------------------------------------------
# 6. MAIN LOOP
# ------------------------------------------------------------

while True:

    ret, frame = cap.read()

    if not ret:
        print("ERROR: Could not read webcam frame.")
        break

    h, w = frame.shape[:2]


    # --------------------------------------------------------
    # CAMERA MATRIX
    # --------------------------------------------------------

    focal_length = w

    camera_matrix = np.array([
        [focal_length, 0, w / 2],
        [0, focal_length, h / 2],
        [0, 0, 1]
    ], dtype=np.float64)

    dist_coeffs = np.zeros((4, 1))


    # --------------------------------------------------------
    # CONVERT FRAME FOR MEDIAPIPE
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
    # FACE LANDMARK DETECTION
    # --------------------------------------------------------

    result = landmarker.detect(mp_image)


    if result.face_landmarks:

        face = result.face_landmarks[0]

        image_points = []


        # ----------------------------------------------------
        # GET REQUIRED FACIAL POINTS
        # ----------------------------------------------------

        for idx in LANDMARK_IDS:

            landmark = face[idx]

            x = landmark.x * w
            y = landmark.y * h

            image_points.append(
                (x, y)
            )


        image_points = np.array(
            image_points,
            dtype=np.float64
        )


        # ----------------------------------------------------
        # SOLVE PNP
        # ----------------------------------------------------

        success, rotation_vector, translation_vector = cv2.solvePnP(
            model_points,
            image_points,
            camera_matrix,
            dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE
        )


        if success:

            # ------------------------------------------------
            # ROTATION MATRIX
            # ------------------------------------------------

            rotation_matrix, _ = cv2.Rodrigues(
                rotation_vector
            )


            # ------------------------------------------------
            # GET EULER ANGLES
            # ------------------------------------------------

            angles, _, _, _, _, _ = cv2.RQDecomp3x3(
                rotation_matrix
            )

            pitch = angles[0]
            yaw = angles[1]
            roll = angles[2]


            # ------------------------------------------------
            # CALIBRATION
            # ------------------------------------------------

            if calibrated_yaw is None:

                calibration_yaw.append(yaw)
                calibration_pitch.append(pitch)

                progress = len(calibration_yaw)

                cv2.putText(
                    frame,
                    f"CALIBRATING: {progress}/{CALIBRATION_FRAMES}",
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 255, 255),
                    2
                )

                cv2.putText(
                    frame,
                    "LOOK STRAIGHT AT THE SCREEN",
                    (20, 75),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 255),
                    2
                )


                # --------------------------------------------
                # FINISH CALIBRATION
                # --------------------------------------------

                if progress >= CALIBRATION_FRAMES:

                    calibrated_yaw = np.mean(
                        calibration_yaw
                    )

                    calibrated_pitch = np.mean(
                        calibration_pitch
                    )

                    print()
                    print("========================================")
                    print("CALIBRATION COMPLETE")
                    print("========================================")
                    print(
                        f"Base Yaw   : {calibrated_yaw:.2f}"
                    )
                    print(
                        f"Base Pitch : {calibrated_pitch:.2f}"
                    )
                    print()


            # ------------------------------------------------
            # AFTER CALIBRATION
            # ------------------------------------------------

            else:

                yaw_difference = (
                    yaw - calibrated_yaw
                )

                pitch_difference = (
                    pitch - calibrated_pitch
                )


                # --------------------------------------------
                # HEAD DIRECTION
                # --------------------------------------------

                YAW_THRESHOLD = 25
                DOWN_THRESHOLD = 20

                if yaw_difference > YAW_THRESHOLD:

                    direction = "LOOKING RIGHT"

                elif yaw_difference < -YAW_THRESHOLD:

                    direction = "LOOKING LEFT"

                elif pitch_difference > DOWN_THRESHOLD:

                    direction = "LOOKING DOWN"

                else:

                    # CENTER includes normal position AND looking UP
                    direction = "CENTER"


                # --------------------------------------------
                # DISPLAY RAW VALUES
                # --------------------------------------------

                cv2.putText(
                    frame,
                    f"Yaw: {yaw:.1f}",
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 0),
                    2
                )

                cv2.putText(
                    frame,
                    f"Pitch: {pitch:.1f}",
                    (20, 70),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 0),
                    2
                )

                cv2.putText(
                    frame,
                    f"Roll: {roll:.1f}",
                    (20, 100),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 0),
                    2
                )


                # --------------------------------------------
                # DISPLAY DIFFERENCES FROM NORMAL
                # --------------------------------------------

                cv2.putText(
                    frame,
                    f"Yaw Diff: {yaw_difference:.1f}",
                    (20, 135),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 0),
                    2
                )

                cv2.putText(
                    frame,
                    f"Pitch Diff: {pitch_difference:.1f}",
                    (20, 165),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 0),
                    2
                )


                # --------------------------------------------
                # DISPLAY DIRECTION
                # --------------------------------------------

                cv2.putText(
                    frame,
                    direction,
                    (20, 210),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (0, 255, 255),
                    2
                )


            # ------------------------------------------------
            # DRAW THE 6 PNP LANDMARKS
            # ------------------------------------------------

            for point in image_points:

                x = int(point[0])
                y = int(point[1])

                cv2.circle(
                    frame,
                    (x, y),
                    4,
                    (255, 0, 0),
                    -1
                )


    else:

        # ----------------------------------------------------
        # NO FACE
        # ----------------------------------------------------

        cv2.putText(
            frame,
            "NO FACE DETECTED",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 0, 255),
            2
        )


    # --------------------------------------------------------
    # SHOW WINDOW
    # --------------------------------------------------------

    cv2.imshow(
        "PROCTIFY - Head Pose",
        frame
    )


    # --------------------------------------------------------
    # QUIT
    # --------------------------------------------------------

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break


# ------------------------------------------------------------
# CLEANUP
# ------------------------------------------------------------

cap.release()
landmarker.close()
cv2.destroyAllWindows()

print("Head pose detection stopped.")