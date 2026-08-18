import cv2
import os
import mediapipe as mp


# ============================================================
# PROCTIFY - STUDENT FACE ENROLLMENT
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)


# ============================================================
# PATHS
# ============================================================

FACE_MODEL = os.path.join(
    BASE_DIR,
    "models",
    "face_landmarker",
    "face_landmarker.task"
)

FACE_DIR = os.path.join(
    BASE_DIR,
    "shared_data",
    "face_profiles"
)

os.makedirs(
    FACE_DIR,
    exist_ok=True
)


STUDENT_ID = "STUDENT_001"

OUTPUT_FILE = os.path.join(
    FACE_DIR,
    f"{STUDENT_ID}.jpg"
)


# ============================================================
# CHECK MODEL
# ============================================================

if not os.path.exists(FACE_MODEL):

    print()
    print("ERROR: Face landmarker model not found.")
    print()
    print(FACE_MODEL)
    print()

    raise SystemExit


# ============================================================
# MEDIAPIPE FACE LANDMARKER
# ============================================================

BaseOptions = mp.tasks.BaseOptions
FaceLandmarker = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = (
    mp.tasks.vision.FaceLandmarkerOptions
)
RunningMode = mp.tasks.vision.RunningMode


options = FaceLandmarkerOptions(

    base_options=BaseOptions(
        model_asset_path=FACE_MODEL
    ),

    running_mode=RunningMode.VIDEO,

    num_faces=1

)


landmarker = FaceLandmarker.create_from_options(
    options
)


# ============================================================
# CAMERA
# ============================================================

camera = cv2.VideoCapture(0)


if not camera.isOpened():

    print()
    print("ERROR: Could not open camera.")
    print()

    landmarker.close()

    raise SystemExit


print()
print("========================================")
print(" PROCTIFY STUDENT FACE ENROLLMENT")
print("========================================")
print()
print("Student ID:", STUDENT_ID)
print()
print("Position your face clearly inside the frame.")
print("Press SPACE to capture.")
print("Press Q to cancel.")
print()


frame_timestamp = 0


try:

    while True:

        success, frame = camera.read()


        if not success:

            print(
                "ERROR: Could not read camera."
            )

            break


        frame_timestamp += 1


        # ----------------------------------------------------
        # Convert OpenCV BGR → RGB
        # ----------------------------------------------------

        rgb_frame = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2RGB
        )


        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb_frame
        )


        # ----------------------------------------------------
        # Face detection / landmarks
        # ----------------------------------------------------

        result = landmarker.detect_for_video(
            mp_image,
            frame_timestamp
        )


        face_detected = (
            result.face_landmarks is not None
            and
            len(result.face_landmarks) > 0
        )


        # ----------------------------------------------------
        # Draw face boundary
        # ----------------------------------------------------

        if face_detected:

            landmarks = result.face_landmarks[0]


            height, width, _ = frame.shape


            xs = [
                int(
                    landmark.x * width
                )
                for landmark in landmarks
            ]

            ys = [
                int(
                    landmark.y * height
                )
                for landmark in landmarks
            ]


            x1 = max(
                0,
                min(xs)
            )

            y1 = max(
                0,
                min(ys)
            )

            x2 = min(
                width - 1,
                max(xs)
            )

            y2 = min(
                height - 1,
                max(ys)
            )


            # Add padding around face

            padding_x = int(
                (x2 - x1) * 0.25
            )

            padding_y = int(
                (y2 - y1) * 0.35
            )


            x1 = max(
                0,
                x1 - padding_x
            )

            y1 = max(
                0,
                y1 - padding_y
            )

            x2 = min(
                width,
                x2 + padding_x
            )

            y2 = min(
                height,
                y2 + padding_y
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
                "FACE DETECTED",
                (x1, max(25, y1 - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2
            )


        else:

            cv2.putText(
                frame,
                "NO FACE DETECTED",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 255),
                2
            )


        # ----------------------------------------------------
        # Instructions
        # ----------------------------------------------------

        cv2.putText(
            frame,
            "SPACE = CAPTURE    Q = QUIT",
            (20, frame.shape[0] - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2
        )


        cv2.imshow(
            "PROCTIFY - Face Enrollment",
            frame
        )


        key = cv2.waitKey(1) & 0xFF


        # ----------------------------------------------------
        # QUIT
        # ----------------------------------------------------

        if key == ord("q"):

            print()
            print("Enrollment cancelled.")
            print()

            break


        # ----------------------------------------------------
        # CAPTURE
        # ----------------------------------------------------

        if key == 32:

            if not face_detected:

                print()
                print(
                    "No face detected."
                )
                print(
                    "Please position your face clearly."
                )
                print()

                continue


            # ------------------------------------------------
            # Get face bounding box again
            # ------------------------------------------------

            landmarks = result.face_landmarks[0]

            height, width, _ = frame.shape


            xs = [
                int(
                    landmark.x * width
                )
                for landmark in landmarks
            ]

            ys = [
                int(
                    landmark.y * height
                )
                for landmark in landmarks
            ]


            x1 = max(
                0,
                min(xs)
            )

            y1 = max(
                0,
                min(ys)
            )

            x2 = min(
                width - 1,
                max(xs)
            )

            y2 = min(
                height - 1,
                max(ys)
            )


            padding_x = int(
                (x2 - x1) * 0.25
            )

            padding_y = int(
                (y2 - y1) * 0.35
            )


            x1 = max(
                0,
                x1 - padding_x
            )

            y1 = max(
                0,
                y1 - padding_y
            )

            x2 = min(
                width,
                x2 + padding_x
            )

            y2 = min(
                height,
                y2 + padding_y
            )


            face_image = frame[
                y1:y2,
                x1:x2
            ]


            if face_image.size == 0:

                print(
                    "Could not crop face."
                )

                continue


            # ------------------------------------------------
            # Save reference image
            # ------------------------------------------------

            saved = cv2.imwrite(
                OUTPUT_FILE,
                face_image
            )


            if saved:

                print()
                print("========================================")
                print(" FACE PROFILE SAVED")
                print("========================================")
                print()
                print(
                    "Student:",
                    STUDENT_ID
                )
                print()
                print(
                    "File:",
                    OUTPUT_FILE
                )
                print()

                break


            else:

                print()
                print(
                    "ERROR: Could not save face image."
                )
                print()


finally:

    camera.release()

    cv2.destroyAllWindows()

    landmarker.close()


print()
print("Enrollment program finished.")
print()