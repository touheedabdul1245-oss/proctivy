import os
import shutil
import random

# ============================================================
# PATHS
# ============================================================

SOURCE_IMAGES = r"dataset\train\images"
SOURCE_LABELS = r"dataset\train\labels"

DEST = r"dataset\proctor_finetune"

TRAIN_IMAGES = os.path.join(DEST, "train", "images")
TRAIN_LABELS = os.path.join(DEST, "train", "labels")

VALID_IMAGES = os.path.join(DEST, "valid", "images")
VALID_LABELS = os.path.join(DEST, "valid", "labels")


# ============================================================
# SETTINGS
# ============================================================

TARGET_TOTAL = 900
VALID_COUNT = 100

# Class IDs
EXTRA_PERSON = 1
PHONE = 3
STUDENT = 4

random.seed(42)


# ============================================================
# CREATE FOLDERS
# ============================================================

for folder in [
    TRAIN_IMAGES,
    TRAIN_LABELS,
    VALID_IMAGES,
    VALID_LABELS
]:
    os.makedirs(folder, exist_ok=True)


# ============================================================
# READ ALL LABEL FILES
# ============================================================

phone_images = []
extra_person_images = []
student_images = []
useful_images = []

label_files = [
    f for f in os.listdir(SOURCE_LABELS)
    if f.endswith(".txt")
]

print("Total label files:", len(label_files))


for label_file in label_files:

    label_path = os.path.join(
        SOURCE_LABELS,
        label_file
    )

    classes = set()

    with open(label_path, "r") as f:

        for line in f:

            parts = line.strip().split()

            if not parts:
                continue

            classes.add(int(parts[0]))


    image_name = os.path.splitext(label_file)[0]

    image_path = None

    for ext in [".jpg", ".jpeg", ".png"]:

        candidate = os.path.join(
            SOURCE_IMAGES,
            image_name + ext
        )

        if os.path.exists(candidate):

            image_path = candidate
            break


    if image_path is None:
        continue


    # --------------------------------------------------------
    # Categorize image
    # --------------------------------------------------------

    if PHONE in classes:
        phone_images.append(
            (image_path, label_path)
        )

    if EXTRA_PERSON in classes:
        extra_person_images.append(
            (image_path, label_path)
        )

    if STUDENT in classes:
        student_images.append(
            (image_path, label_path)
        )

    if (
        PHONE in classes
        or EXTRA_PERSON in classes
        or STUDENT in classes
    ):
        useful_images.append(
            (image_path, label_path)
        )


# ============================================================
# REMOVE DUPLICATES
# ============================================================

phone_images = list(dict.fromkeys(phone_images))
extra_person_images = list(dict.fromkeys(extra_person_images))
student_images = list(dict.fromkeys(student_images))
useful_images = list(dict.fromkeys(useful_images))


print()
print("Phone images:", len(phone_images))
print("Extra-person images:", len(extra_person_images))
print("Student images:", len(student_images))
print("Useful images:", len(useful_images))


# ============================================================
# SELECT DATA
# ============================================================

selected = set()


# ------------------------------------------------------------
# PRIORITY 1: PHONE
# ------------------------------------------------------------

random.shuffle(phone_images)

phone_target = min(
    400,
    len(phone_images)
)

for item in phone_images[:phone_target]:
    selected.add(item)


# ------------------------------------------------------------
# PRIORITY 2: EXTRA PERSON
# ------------------------------------------------------------

random.shuffle(extra_person_images)

extra_target = min(
    250,
    len(extra_person_images)
)

for item in extra_person_images[:extra_target]:
    selected.add(item)


# ------------------------------------------------------------
# PRIORITY 3: STUDENT
# ------------------------------------------------------------

random.shuffle(student_images)

student_target = min(
    250,
    len(student_images)
)

for item in student_images[:student_target]:
    selected.add(item)


# ============================================================
# FILL REMAINING SLOTS
# ============================================================

remaining_needed = TARGET_TOTAL - len(selected)

if remaining_needed > 0:

    random.shuffle(useful_images)

    for item in useful_images:

        if item not in selected:

            selected.add(item)

            if len(selected) >= TARGET_TOTAL:
                break


selected = list(selected)

random.shuffle(selected)

print()
print("Selected total:", len(selected))


# ============================================================
# SPLIT TRAIN / VALID
# ============================================================

valid_data = selected[:VALID_COUNT]
train_data = selected[VALID_COUNT:]


print("Training images:", len(train_data))
print("Validation images:", len(valid_data))


# ============================================================
# COPY FUNCTION
# ============================================================

def copy_pair(item, image_dest, label_dest):

    image_path, label_path = item

    image_name = os.path.basename(image_path)
    label_name = os.path.basename(label_path)

    shutil.copy2(
        image_path,
        os.path.join(image_dest, image_name)
    )

    shutil.copy2(
        label_path,
        os.path.join(label_dest, label_name)
    )


# ============================================================
# COPY TRAIN
# ============================================================

for item in train_data:

    copy_pair(
        item,
        TRAIN_IMAGES,
        TRAIN_LABELS
    )


# ============================================================
# COPY VALID
# ============================================================

for item in valid_data:

    copy_pair(
        item,
        VALID_IMAGES,
        VALID_LABELS
    )


# ============================================================
# FINISHED
# ============================================================

print()
print("======================================")
print("PROCTOR FINE-TUNING DATASET CREATED")
print("======================================")
print("Train:", len(train_data))
print("Valid:", len(valid_data))
print()
print("Location:")
print(DEST)
