import os
import shutil
import random

base = os.path.join("dataset", "custom_phone")

image_dir = os.path.join(base, "images")
label_dir = os.path.join(base, "labels")

train_image_dir = os.path.join(base, "train", "images")
train_label_dir = os.path.join(base, "train", "labels")

valid_image_dir = os.path.join(base, "valid", "images")
valid_label_dir = os.path.join(base, "valid", "labels")


# Create all required folders
os.makedirs(train_image_dir, exist_ok=True)
os.makedirs(train_label_dir, exist_ok=True)
os.makedirs(valid_image_dir, exist_ok=True)
os.makedirs(valid_label_dir, exist_ok=True)


# Get original images
images = [
    f for f in os.listdir(image_dir)
    if f.lower().endswith((".jpg", ".jpeg", ".png"))
]

print("Original images found:", len(images))

# Shuffle consistently
random.seed(42)
random.shuffle(images)

# Split
valid_images = images[:6]
train_images = images[6:]


# Copy validation files
for filename in valid_images:
    name = os.path.splitext(filename)[0]

    shutil.copy2(
        os.path.join(image_dir, filename),
        os.path.join(valid_image_dir, filename)
    )

    shutil.copy2(
        os.path.join(label_dir, name + ".txt"),
        os.path.join(valid_label_dir, name + ".txt")
    )


# Copy training files
for filename in train_images:
    name = os.path.splitext(filename)[0]

    shutil.copy2(
        os.path.join(image_dir, filename),
        os.path.join(train_image_dir, filename)
    )

    shutil.copy2(
        os.path.join(label_dir, name + ".txt"),
        os.path.join(train_label_dir, name + ".txt")
    )


print("TRAIN:", len(train_images))
print("VALID:", len(valid_images))
print("Dataset split completed.")