import cv2
from ultralytics import YOLO

# ============================================================
# 1. LOAD MODEL
# ============================================================

MODEL_PATH = "models/phone_detector.pt"

model = YOLO(MODEL_PATH)

print("Model loaded successfully.")
print("Classes:", model.names)


# ============================================================
# 2. CLASS IDs
# ============================================================

BOOK = 0
EXTRA_PERSON = 1
LAPTOP = 2
PHONE = 3
STUDENT = 4


# ============================================================
# 3. CLASS-SPECIFIC CONFIDENCE THRESHOLDS
# ============================================================

CONF_THRESHOLDS = {
    BOOK: 0.95,          # Book only if >= 80%
    EXTRA_PERSON: 0.50,  # Extra person
    LAPTOP: 0.60,        # Laptop
    PHONE: 0.40,         # Phone
    STUDENT: 0.50        # Student
}


# ============================================================
# 4. ALL CLASSES ENABLED
# ============================================================

ENABLED_CLASSES = {
    BOOK,
    EXTRA_PERSON,
    LAPTOP,
    PHONE,
    STUDENT
}


# ============================================================
# 5. START WEBCAM
# ============================================================

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("ERROR: Could not open webcam.")
    exit()

print("Webcam started.")
print("Press Q to quit.")


# ============================================================
# 6. WEBCAM LOOP
# ============================================================

while True:

    ret, frame = cap.read()

    if not ret:
        print("ERROR: Could not read webcam frame.")
        break


    # --------------------------------------------------------
    # YOLO DETECTION
    # --------------------------------------------------------

    # Low global threshold.
    # Individual class thresholds are applied below.

    results = model(
        frame,
        conf=0.20,
        verbose=False
    )


    # --------------------------------------------------------
    # PROCESS DETECTIONS
    # --------------------------------------------------------

    if len(results) > 0:

        result = results[0]

        if result.boxes is not None:

            for box in result.boxes:

                cls_id = int(box.cls[0])
                confidence = float(box.conf[0])


                # ------------------------------------------------
                # Ignore unknown classes
                # ------------------------------------------------

                if cls_id not in ENABLED_CLASSES:
                    continue


                # ------------------------------------------------
                # Get class-specific threshold
                # ------------------------------------------------

                threshold = CONF_THRESHOLDS.get(
                    cls_id,
                    0.50
                )


                # ------------------------------------------------
                # Apply confidence threshold
                # ------------------------------------------------

                if confidence < threshold:
                    continue


                # ------------------------------------------------
                # Get bounding box
                # ------------------------------------------------

                x1, y1, x2, y2 = map(
                    int,
                    box.xyxy[0]
                )


                # ------------------------------------------------
                # Get class name
                # ------------------------------------------------

                class_name = model.names[cls_id]


                # ------------------------------------------------
                # Create label
                # ------------------------------------------------

                label = f"{class_name} {confidence:.2f}"


                # ------------------------------------------------
                # Draw bounding box
                # ------------------------------------------------

                cv2.rectangle(
                    frame,
                    (x1, y1),
                    (x2, y2),
                    (0, 255, 0),
                    2
                )


                # ------------------------------------------------
                # Draw label background
                # ------------------------------------------------

                (text_width, text_height), baseline = cv2.getTextSize(
                    label,
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    2
                )

                cv2.rectangle(
                    frame,
                    (x1, y1 - text_height - baseline - 5),
                    (x1 + text_width, y1),
                    (0, 255, 0),
                    -1
                )


                # ------------------------------------------------
                # Draw label
                # ------------------------------------------------

                cv2.putText(
                    frame,
                    label,
                    (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 0, 0),
                    2
                )


    # ========================================================
    # DISPLAY
    # ========================================================

    cv2.imshow(
        "PROCTIFY - Webcam Detection",
        frame
    )


    # ========================================================
    # QUIT
    # ========================================================

    key = cv2.waitKey(1) & 0xFF

    if key == ord("q"):
        break


# ============================================================
# CLEANUP
# ============================================================

cap.release()
cv2.destroyAllWindows()

print("Webcam stopped.")