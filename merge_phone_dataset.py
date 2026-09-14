import os
import shutil

# ============================================================
# PATHS
# ============================================================

SOURCE = r"C:\Users\touhe\Downloads\Mobile Phone.v1i.yolov11\train"

DEST = r"C:\Users\touhe\PROCTIFY_V2\dataset\train"

SOURCE_IMAGES = os.path.join(SOURCE, "images")
SOURCE_LABELS = os.path.join(SOURCE, "labels")

DEST_IMAGES = os.path.join(DEST, "images")
DEST_LABELS = os.path.join(DEST, "labels")


# ============================================================
# MERGE
# ============================================================

image_files = [
    f for f in os.listdir(SOURCE_IMAGES)
    if f.lower().endswith((".jpg", ".jpeg", ".png"))
]

copied_images = 0
copied_labels = 0

for image_file in image_files:

    base_name = os.path.splitext(image_file)[0]

    source_image = os.path.join(
        SOURCE_IMAGES,
        image_file
    )

    source_label = os.path.join(
        SOURCE_LABELS,
        base_name + ".txt"
    )

    destination_image = os.path.join(
        DEST_IMAGES,
        image_file
    )

    destination_label = os.path.join(
        DEST_LABELS,
        base_name + ".txt"
    )

    # Safety check
    if os.path.exists(destination_image):
        print(f"SKIPPED duplicate image: {image_file}")
        continue

    if not os.path.exists(source_label):
        print(f"SKIPPED missing label: {image_file}")
        continue

    # Copy image
    shutil.copy2(
        source_image,
        destination_image
    )

    # Convert class ID 0 -> 3
    with open(source_label, "r") as f:
        lines = f.readlines()

    converted_lines = []

    for line in lines:

        parts = line.strip().split()

        if not parts:
            continue

        # Dataset has only one class: phone = 0
        if parts[0] == "0":
            parts[0] = "3"

        converted_lines.append(
            " ".join(parts)
        )

    # Write converted label
    with open(destination_label, "w") as f:
        for line in converted_lines:
            f.write(line + "\n")

    copied_images += 1
    copied_labels += 1


# ============================================================
# RESULT
# ============================================================

print("\n===================================")
print(" PHONE DATASET MERGE COMPLETE")
print("===================================")

print(f"Images copied : {copied_images}")
print(f"Labels copied : {copied_labels}")

print("\nClass conversion:")
print("Downloaded dataset: 0 = phone")
print("PROCTIFY dataset   : 3 = phone")

print("\nExisting validation/test sets were NOT modified.")
print("===================================")