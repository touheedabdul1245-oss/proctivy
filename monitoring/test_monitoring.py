import cv2
import numpy as np

from monitoring_engine import MonitoringEngine


# ============================================================
# CREATE TEST FRAME
# ============================================================

frame = np.zeros(
    (480, 640, 3),
    dtype=np.uint8
)


cv2.putText(
    frame,
    "PROCTIFY TEST EVIDENCE",
    (120, 240),
    cv2.FONT_HERSHEY_SIMPLEX,
    1,
    (255, 255, 255),
    2
)


# ============================================================
# CREATE MONITORING ENGINE
# ============================================================

engine = MonitoringEngine(
    session_id="TEST_EVIDENCE_001"
)


print("Initial Trust Score:")
print(engine.get_trust_score())


# ============================================================
# RECORD VIOLATION
# ============================================================

engine.record_violation(
    "PHONE_DETECTED",
    severity="HIGH",
    description="Test phone detection"
)


# ============================================================
# SAVE EVIDENCE
# ============================================================

path = engine.save_evidence(
    frame,
    "PHONE_DETECTED"
)


print()
print("Evidence successfully created.")
print("File:", path)