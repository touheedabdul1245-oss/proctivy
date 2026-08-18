import cv2
import mediapipe as mp
import numpy as np
import time

# ============================================================
# PROCTIFY - EYE GAZE TEST
#
# CENTER / LEFT / RIGHT / DOWN
# UP is intentionally ignored
#
# Eyes closed for >= 2 seconds -> LOOKING DOWN
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
# 2. EYE LANDMARKS
# ------------------------------------------------------------

# LEFT EYE
LEFT_EYE_LEFT = 33
LEFT_EYE_RIGHT = 133
LEFT_EYE_TOP = 159
LEFT_EYE_BOTTOM = 145
LEFT_IRIS = 468

# RIGHT EYE
RIGHT_EYE_LEFT = 362
RIGHT_EYE_RIGHT = 263
RIGHT_EYE_TOP = 386
RIGHT_EYE_BOTTOM = 374
RIGHT_IRIS = 473


# ------------------------------------------------------------
# 3. SETTINGS
# ------------------------------------------------------------

# Horizontal gaze
LEFT_THRESHOLD = 0.60
RIGHT_THRESHOLD = 0.40

# Vertical gaze
DOWN_THRESHOLD = 0.55

# Eyes closed duration
EYES_CLOSED_TIME = 2.0

# Eye height threshold
EYE_CLOSED_THRESHOLD = 8


# ------------------------------------------------------------
# 4. EYE CLOSED TIMER
# ------------------------------------------------------------

eyes_closed_start = None


# ------------------------------------------------------------
# 5. WEBCAM
# ------------------------------------------------------------

cap = cv2.VideoCapture(0)

if not cap.isOpened():

    print("ERROR: Could not open webcam.")
    exit()


print()
print("========================================")
print("PROCTIFY EYE GAZE TEST")
print("========================================")
print("CENTER / LEFT / RIGHT / DOWN")
print("UP is ignored.")
print("Eyes closed >= 2 seconds -> LOOKING DOWN")
print("Press Q to quit.")
print()


# ------------------------------------------------------------
# 6. HELPER FUNCTION
# ------------------------------------------------------------

def get_point(landmark, width, height):

    return np.array([
        landmark.x * width,
        landmark.y * height
    ])


# ------------------------------------------------------------
# 7. HORIZONTAL GAZE
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# 8. VERTICAL GAZE
# ------------------------------------------------------------

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


# ============================================================
# 9. MAIN LOOP
# ============================================================

while True:

    ret, frame = cap.read()

    if not ret:

        print("ERROR: Could not read webcam.")
        break


    h, w = frame.shape[:2]


    # --------------------------------------------------------
    # MEDIAPIPE
    # --------------------------------------------------------

    rgb = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2RGB
    )

    mp_image = mp.Image(
        image_format=mp.ImageFormat.SRGB,
        data=rgb
    )

    result = landmarker.detect(mp_image)


    # --------------------------------------------------------
    # FACE FOUND
    # --------------------------------------------------------

    if result.face_landmarks:

        face = result.face_landmarks[0]


        # ====================================================
        # HORIZONTAL GAZE
        # ====================================================

        left_horizontal = horizontal_ratio(
            face,
            LEFT_EYE_LEFT,
            LEFT_EYE_RIGHT,
            LEFT_IRIS,
            w,
            h
        )

        right_horizontal = horizontal_ratio(
            face,
            RIGHT_EYE_LEFT,
            RIGHT_EYE_RIGHT,
            RIGHT_IRIS,
            w,
            h
        )

        horizontal_ratio_value = (
            left_horizontal +
            right_horizontal
        ) / 2


        # ====================================================
        # VERTICAL GAZE
        # ====================================================

        left_vertical = vertical_ratio(
            face,
            LEFT_EYE_TOP,
            LEFT_EYE_BOTTOM,
            LEFT_IRIS,
            w,
            h
        )

        right_vertical = vertical_ratio(
            face,
            RIGHT_EYE_TOP,
            RIGHT_EYE_BOTTOM,
            RIGHT_IRIS,
            w,
            h
        )

        vertical_ratio_value = (
            left_vertical +
            right_vertical
        ) / 2


        # ====================================================
        # EYE CLOSURE
        # ====================================================

        left_top = get_point(
            face[LEFT_EYE_TOP],
            w,
            h
        )

        left_bottom = get_point(
            face[LEFT_EYE_BOTTOM],
            w,
            h
        )

        right_top = get_point(
            face[RIGHT_EYE_TOP],
            w,
            h
        )

        right_bottom = get_point(
            face[RIGHT_EYE_BOTTOM],
            w,
            h
        )


        left_eye_height = np.linalg.norm(
            left_bottom - left_top
        )

        right_eye_height = np.linalg.norm(
            right_bottom - right_top
        )


        eyes_closed = (
            left_eye_height < EYE_CLOSED_THRESHOLD
            and
            right_eye_height < EYE_CLOSED_THRESHOLD
        )


        # ====================================================
        # CLOSED EYES TIMER
        # ====================================================

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


        # ====================================================
        # GAZE CLASSIFICATION
        # ====================================================

        if closed_duration >= EYES_CLOSED_TIME:

            gaze = "LOOKING DOWN"

        elif horizontal_ratio_value > LEFT_THRESHOLD:

            # Reversed intentionally because your
            # webcam test showed the opposite mapping.
            gaze = "LOOKING LEFT"

        elif horizontal_ratio_value < RIGHT_THRESHOLD:

            gaze = "LOOKING RIGHT"

        elif vertical_ratio_value > DOWN_THRESHOLD:

            gaze = "LOOKING DOWN"

        else:

            # CENTER includes looking straight
            # and looking UP.
            gaze = "CENTER"


        # ====================================================
        # DISPLAY VALUES
        # ====================================================

        cv2.putText(
            frame,
            f"H: {horizontal_ratio_value:.3f}",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 0),
            2
        )

        cv2.putText(
            frame,
            f"V: {vertical_ratio_value:.3f}",
            (20, 65),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 0),
            2
        )


        # ----------------------------------------------------
        # EYE STATUS
        # ----------------------------------------------------

        if eyes_closed:

            cv2.putText(
                frame,
                f"EYES CLOSED: {closed_duration:.1f}s",
                (20, 95),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 165, 255),
                2
            )

        else:

            cv2.putText(
                frame,
                "EYES OPEN",
                (20, 95),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 255, 0),
                2
            )


        # ====================================================
        # FINAL GAZE
        # ====================================================

        cv2.putText(
            frame,
            gaze,
            (20, 140),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 255),
            2
        )


        # ====================================================
        # DRAW IRIS
        # ====================================================

        for idx in [
            LEFT_IRIS,
            RIGHT_IRIS
        ]:

            point = face[idx]

            x = int(point.x * w)
            y = int(point.y * h)

            cv2.circle(
                frame,
                (x, y),
                5,
                (255, 0, 0),
                -1
            )


    # --------------------------------------------------------
    # NO FACE
    # --------------------------------------------------------

    else:

        eyes_closed_start = None

        cv2.putText(
            frame,
            "NO FACE",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 0, 255),
            2
        )


    # --------------------------------------------------------
    # SHOW
    # --------------------------------------------------------

    cv2.imshow(
        "PROCTIFY - Eye Gaze",
        frame
    )


    # --------------------------------------------------------
    # QUIT
    # --------------------------------------------------------

    if cv2.waitKey(1) & 0xFF == ord("q"):

        break


# ============================================================
# CLEANUP
# ============================================================

cap.release()
landmarker.close()
cv2.destroyAllWindows()

print("Gaze test stopped.")