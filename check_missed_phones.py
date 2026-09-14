from pathlib import Path

GT_DIR = Path("dataset_phone_earphone/test/labels")
PRED_DIR = Path("runs/detect/predict-6/labels")


def iou(box1, box2):
    x1, y1, w1, h1 = box1
    x2, y2, w2, h2 = box2

    a_x1 = x1 - w1 / 2
    a_y1 = y1 - h1 / 2
    a_x2 = x1 + w1 / 2
    a_y2 = y1 + h1 / 2

    b_x1 = x2 - w2 / 2
    b_y1 = y2 - h2 / 2
    b_x2 = x2 + w2 / 2
    b_y2 = y2 + h2 / 2

    inter_x1 = max(a_x1, b_x1)
    inter_y1 = max(a_y1, b_y1)
    inter_x2 = min(a_x2, b_x2)
    inter_y2 = min(a_y2, b_y2)

    iw = max(0, inter_x2 - inter_x1)
    ih = max(0, inter_y2 - inter_y1)

    intersection = iw * ih

    area1 = w1 * h1
    area2 = w2 * h2

    union = area1 + area2 - intersection

    return intersection / union if union > 0 else 0


missed_images = []

for gt_file in sorted(GT_DIR.glob("*.txt")):

    gt_phones = []

    for line in gt_file.read_text().splitlines():
        parts = line.split()

        if len(parts) == 5 and parts[0] == "0":
            gt_phones.append([
                float(parts[1]),
                float(parts[2]),
                float(parts[3]),
                float(parts[4])
            ])

    if not gt_phones:
        continue

    pred_file = PRED_DIR / gt_file.name

    predictions = []

    if pred_file.exists():

        for line in pred_file.read_text().splitlines():

            parts = line.split()

            # prediction format:
            # class x y width height confidence

            if len(parts) >= 5 and parts[0] == "0":

                predictions.append([
                    float(parts[1]),
                    float(parts[2]),
                    float(parts[3]),
                    float(parts[4])
                ])

    for gt in gt_phones:

        best_iou = 0

        for pred in predictions:
            score = iou(gt, pred)
            best_iou = max(best_iou, score)

        if best_iou < 0.5:
            missed_images.append(
                (gt_file.name, round(best_iou, 3))
            )

print()
print("ACTUAL MISSED / POORLY MATCHED PHONE IMAGES")
print("============================================")

for name, score in missed_images:
    print(f"{name} -> best IoU: {score}")

print("============================================")
print(f"Total missed/poor phone images: {len(missed_images)}")