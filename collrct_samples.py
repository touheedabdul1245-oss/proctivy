import cv2
import os
import time

# Change this for the category you are currently collecting
CATEGORY = "one_person"

SAVE_DIR = os.path.join(
    "evaluation",
    "person",
    CATEGORY
)

os.makedirs(SAVE_DIR, exist_ok=True)

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("ERROR: Could not open webcam.")
    exit()

existing = len([
    f for f in os.listdir(SAVE_DIR)
    if f.lower().endswith((".jpg", ".jpeg", ".png"))
])

count = existing

print("=" * 50)
print(f"Collecting samples: {CATEGORY}")
print(f"Saving to: {SAVE_DIR}")
print("Press SPACE to save an image")
print("Press Q to quit")
print("=" * 50)

while True:
    ret, frame = cap.read()

    if not ret:
        print("ERROR: Could not read webcam.")
        break

    display = frame.copy()

    cv2.putText(
        display,
        f"{CATEGORY} | Samples: {count}",
        (20, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 0),
        2
    )

    cv2.putText(
        display,
        "SPACE = Save | Q = Quit",
        (20, 70),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2
    )

    cv2.imshow("PROCTIFY Evaluation Data Collection", display)

    key = cv2.waitKey(1) & 0xFF

    if key == ord(" "):
        count += 1

        filename = os.path.join(
            SAVE_DIR,
            f"{CATEGORY}_{count:03d}.jpg"
        )

        cv2.imwrite(filename, frame)

        print(f"Saved: {filename}")

        time.sleep(0.15)

    elif key == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()

print(f"\nFinished. Total samples: {count}")