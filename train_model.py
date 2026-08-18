from ultralytics import YOLO

model = YOLO("yolo11n.yaml")

model.train(
    data="dataset/data.yaml",
    epochs=10,
    imgsz=512,
    batch=8,
    name="proctify_exam_test",
    pretrained=False
)

print("Training completed!")