"""
PROCTIFY - END TO END TEST
==========================

Exercises the complete workflow against a running PROCTIFY dashboard:

    teacher login
        -> enroll student
            -> host exam
                -> student login
                    -> live monitoring heartbeats
                        -> violations
                            -> Trust Score 0 -> terminate -> report
                    -> exam submission (auto evaluation)
                        -> manual teacher evaluation
                            -> delete submission
                                -> clear exam data
                                    -> delete exam
                                        -> delete student

Run the server first:

    python dashboard/app.py

Then:

    python test_end_to_end.py
"""

import io
import json
import sys
import time

import requests

BASE = "http://127.0.0.1:5000"

TEACHER_USER = "teacher"
TEACHER_PASSWORD = "teacher123"

TEST_STUDENT_ID = "TEST_STUDENT_E2E"
TEST_STUDENT_USER = "e2e_student"
TEST_STUDENT_PASSWORD = "e2e_pass"

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def section(title):
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


def teacher_session():
    session = requests.Session()
    response = session.post(
        f"{BASE}/api/teacher/login",
        json={"username": TEACHER_USER, "password": TEACHER_PASSWORD},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    return session


def student_session(username, password):
    session = requests.Session()
    response = session.post(
        f"{BASE}/api/student/login",
        json={"username": username, "password": password},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    return session


def make_jpeg():
    """Smallest possible valid JPEG so evidence upload can be tested."""
    import cv2
    import numpy as np

    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", frame)
    assert ok
    return encoded.tobytes()


def clean_test_artifacts():
    """
    Remove the report files this test generated so a test run never
    leaves junk in reports/.
    """

    import glob
    import os

    removed = 0

    patterns = [
        "reports/session_report_SESSION_E2E_*.json",
        "reports/session_report_SESSION_1789*.json",
        "reports/submissions/submission_*.html",
    ]

    for pattern in patterns:

        for path in glob.glob(pattern):

            try:
                os.remove(path)
                removed += 1

            except OSError:
                pass

    for folder, pattern in (
        ("reports/evidence", "*.jpg"),
        ("reports/submissions", "*.html"),
    ):

        if not os.path.isdir(folder):
            continue

        # Only purge the folders when the database references nothing.
        if len(os.listdir(folder)) > 40:
            continue

        for path in glob.glob(os.path.join(folder, pattern)):

            try:
                os.remove(path)
                removed += 1

            except OSError:
                pass

    if removed:
        print(f"        cleaned {removed} test artifact(s) from reports/")

    return removed


def main():
    print("PROCTIFY END TO END TEST")
    print(f"Server: {BASE}")

    # ------------------------------------------------------------------
    section("1. SYSTEM HEALTH + ACCESS CONTROL")
    # ------------------------------------------------------------------

    response = requests.get(f"{BASE}/health", timeout=15)
    check("health endpoint responds", response.ok and response.json()["status"] == "ONLINE")

    anonymous = requests.Session()
    response = anonymous.get(f"{BASE}/teacher", allow_redirects=False, timeout=15)
    check(
        "teacher dashboard is protected without login",
        response.status_code in (301, 302, 303),
        f"got {response.status_code}",
    )

    response = anonymous.get(f"{BASE}/api/status", timeout=15)
    check(
        "teacher API returns 401 without login",
        response.status_code == 401,
        f"got {response.status_code}",
    )

    response = anonymous.post(
        f"{BASE}/api/teacher/login",
        json={"username": TEACHER_USER, "password": "wrong-password"},
        timeout=15,
    )
    check("teacher login rejects a bad password", response.status_code == 401)

    teacher = teacher_session()
    check("teacher login succeeds", True)

    response = teacher.get(f"{BASE}/teacher", timeout=15)
    check(
        "teacher dashboard renders after login",
        response.ok and "PROCTIFY" in response.text,
    )
    check(
        "teacher dashboard greets the logged-in teacher",
        "Good morning, Professor." in response.text,
    )

    response = teacher.get(f"{BASE}/api/teacher/session", timeout=15)
    check("teacher session endpoint is authenticated", response.json()["authenticated"] is True)

    # ------------------------------------------------------------------
    section("2. STUDENT ENROLMENT + MANAGEMENT")
    # ------------------------------------------------------------------

    # Clean up a previous run if it crashed half way through.
    cleanup = teacher.delete(f"{BASE}/api/students/{TEST_STUDENT_ID}", timeout=15)
    if cleanup.status_code not in (200, 404):
        print(f"        (cleanup warning: {cleanup.status_code} {cleanup.text[:120]})")

    response = teacher.post(
        f"{BASE}/api/students/enroll",
        json={
            "student_id": TEST_STUDENT_ID,
            "student_name": "End To End Student",
            "username": TEST_STUDENT_USER,
            "password": TEST_STUDENT_PASSWORD,
        },
        timeout=15,
    )
    check("enroll student", response.ok and response.json().get("success") is True, response.text)

    response = teacher.get(f"{BASE}/api/enrolled-students", timeout=15)
    ids = [s["student_id"] for s in response.json()["students"]]
    check("enrolled student list contains the new student", TEST_STUDENT_ID in ids)

    response = teacher.get(f"{BASE}/api/students", timeout=15)
    check(
        "host-exam student picker returns enrolled students",
        response.ok and isinstance(response.json()["students"], list),
    )

    # ------------------------------------------------------------------
    section("3. EXAM CREATION + ASSIGNMENT")
    # ------------------------------------------------------------------

    questions = [
        {
            "question": "What does 2 + 2 equal?",
            "options": {"A": "3", "B": "4", "C": "5", "D": "6"},
            "correct_answer": "B",
        },
        {
            "question": "What colour is the sky on a clear day?",
            "options": {"A": "Green", "B": "Purple", "C": "Blue", "D": "Black"},
            "correct_answer": "C",
        },
    ]

    response = teacher.post(
        f"{BASE}/api/exams",
        json={
            "exam_name": "E2E Integrity Exam",
            "subject": "Testing",
            "duration": 30,
            "scheduled_start": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "questions": questions,
            "selected_students": [TEST_STUDENT_ID],
        },
        timeout=20,
    )
    payload = response.json()
    check("host exam", response.ok and payload.get("success") is True, response.text)
    exam_id = payload["exam"]["exam_id"]
    print(f"        exam_id = {exam_id}")

    response = teacher.get(f"{BASE}/api/exams", timeout=15)
    exams = {e["exam_id"]: e for e in response.json()["exams"]}
    check("teacher exam list includes the new exam", exam_id in exams)
    check(
        "teacher exam list reports assignment counts",
        exams[exam_id].get("assigned_count") == 1,
        str(exams[exam_id].get("assigned_count")),
    )

    response = teacher.get(f"{BASE}/api/exams/{exam_id}", timeout=15)
    exam = response.json()["exam"]
    check("exam detail returns the questions", len(exam.get("questions", [])) == 2)
    check(
        "teacher exam detail keeps the correct answers",
        exam["questions"][0].get("correct_answer") == "B",
    )

    # ------------------------------------------------------------------
    section("4. STUDENT LOGIN + EXAM VISIBILITY")
    # ------------------------------------------------------------------

    student = student_session(TEST_STUDENT_USER, TEST_STUDENT_PASSWORD)
    check("student login succeeds", True)

    response = student.get(f"{BASE}/api/student/exams", timeout=15)
    student_exams = response.json()["exams"]
    visible = [e for e in student_exams if e["exam_id"] == exam_id]
    check("student sees the assigned exam", len(visible) == 1)
    check(
        "student response never contains correct_answer",
        "correct_answer" not in json.dumps(visible),
    )

    response = student.get(f"{BASE}/student/exam/{exam_id}", timeout=15)
    check("student exam page renders", response.ok and "PROCTIFY" in response.text)

    response = student.get(f"{BASE}/student/precheck/{exam_id}", timeout=15)
    check("student precheck page renders", response.ok)

    response = student.get(f"{BASE}/api/student/verify-status", timeout=15)
    check(
        "student face verification status is reachable",
        response.ok and "face_verified" in response.json(),
    )

    response = student.post(
        f"{BASE}/api/student/enroll-face",
        json={"image": "data:image/jpeg;base64,AAAA"},
        timeout=15,
    )
    check(
        "face enrolment rejects an invalid image instead of crashing",
        response.status_code in (400, 500),
        f"got {response.status_code}",
    )

    # ------------------------------------------------------------------
    section("5. LIVE MONITORING HEARTBEAT + LIVE VIDEO")
    # ------------------------------------------------------------------

    session_id = f"SESSION_E2E_{int(time.time())}"

    response = requests.post(
        f"{BASE}/api/monitor/live-status",
        json={
            "student_id": TEST_STUDENT_ID,
            "session_id": session_id,
            "exam_name": "E2E Integrity Exam",
            "status": "ONLINE",
            "trust_score": 100,
            "risk_level": "LOW",
            "camera_available": True,
            "audio_available": True,
            "ai_available": True,
            "tab_available": True,
        },
        timeout=15,
    )
    check("monitor heartbeat accepted", response.ok and response.json()["success"] is True)

    response = teacher.get(f"{BASE}/api/status", timeout=15)
    live = response.json()["students"]
    check(
        "teacher dashboard sees the live student",
        any(s["student_id"] == TEST_STUDENT_ID for s in live),
    )

    jpeg = make_jpeg()
    response = requests.post(
        f"{BASE}/api/live/video/frame",
        params={"student_id": TEST_STUDENT_ID, "session_id": session_id},
        data=jpeg,
        headers={"Content-Type": "image/jpeg"},
        timeout=15,
    )
    check("student agent can push a live frame", response.ok, response.text)

    response = teacher.get(f"{BASE}/api/live/video/{TEST_STUDENT_ID}", stream=True, timeout=15)
    first_chunk = next(response.iter_content(chunk_size=1024), b"")
    check(
        "teacher live video stream returns frames",
        b"image/jpeg" in first_chunk,
        str(first_chunk[:40]),
    )
    response.close()

    response = teacher.get(f"{BASE}/monitor/{TEST_STUDENT_ID}", timeout=15)
    check("teacher monitor page renders for a live student", response.ok)

    # ------------------------------------------------------------------
    section("6. VIOLATIONS, EVIDENCE AND TRUST SCORE")
    # ------------------------------------------------------------------

    response = requests.post(
        f"{BASE}/api/monitor/evidence",
        data={
            "session_id": session_id,
            "student_id": TEST_STUDENT_ID,
            "exam_name": "E2E Integrity Exam",
            "violation_type": "PHONE_DETECTED",
            "violation_id": "",
        },
        files={"evidence": ("phone.jpg", io.BytesIO(jpeg), "image/jpeg")},
        timeout=20,
    )
    check("evidence image upload", response.ok and response.json().get("success") is True, response.text)

    response = teacher.get(
        f"{BASE}/api/monitor/evidence", params={"session_id": session_id}, timeout=15
    )
    evidence_rows = response.json()["evidence"]
    check("uploaded evidence is stored against the session", len(evidence_rows) >= 1)

    if evidence_rows:
        evidence_id = evidence_rows[0]["id"]
        response = teacher.get(f"{BASE}/api/evidence/{evidence_id}/image", timeout=15)
        check(
            "evidence image can be served back to the teacher",
            response.ok and response.headers["Content-Type"].startswith("image/"),
        )

    trust = 100
    for _ in range(5):
        trust = max(0, trust - 20)
        response = requests.post(
            f"{BASE}/api/monitor/violation",
            json={
                "session_id": session_id,
                "student_id": TEST_STUDENT_ID,
                "exam_name": "E2E Integrity Exam",
                "violation_type": "PHONE_DETECTED",
                "severity": "HIGH",
                "penalty": 20,
                "description": "Phone visible during the examination",
                "old_score": trust + 20,
                "new_score": trust,
            },
            timeout=15,
        )
        if not response.ok:
            break

    check("violations are recorded", response.ok, response.text)
    check("trust score reached zero", trust == 0)

    response = teacher.get(
        f"{BASE}/api/monitor/violations", params={"session_id": session_id}, timeout=15
    )
    check(
        "violations can be listed for the session",
        len(response.json()["violations"]) >= 5,
        str(len(response.json()["violations"])),
    )

    # ------------------------------------------------------------------
    section("7. TRUST SCORE 0 -> TERMINATE + REPORT + FEED STOPS")
    # ------------------------------------------------------------------

    response = requests.post(
        f"{BASE}/api/monitor/terminate",
        json={
            "session_id": session_id,
            "student_id": TEST_STUDENT_ID,
            "exam_name": "E2E Integrity Exam",
            "final_trust_score": 0,
            "final_risk_level": "HIGH",
        },
        timeout=20,
    )
    body = response.json()
    check("forced termination succeeds", response.ok and body.get("success") is True, response.text)
    check("termination generated a report", bool(body.get("report_path")), response.text)

    response = teacher.get(f"{BASE}/api/status", timeout=15)
    live_ids = [s["student_id"] for s in response.json()["students"]]
    check("terminated student disappears from the live feed", TEST_STUDENT_ID not in live_ids)

    response = teacher.get(f"{BASE}/api/live/video/{TEST_STUDENT_ID}", stream=True, timeout=15)
    chunk = next(response.iter_content(chunk_size=1024), b"")
    check("terminated live video stream no longer serves frames", b"image/jpeg" not in chunk)
    response.close()

    response = teacher.get(f"{BASE}/api/system", timeout=15)
    check(
        "system panel reports MySQL statistics",
        response.ok and "database" in response.json() and "sessions" in response.json(),
    )

    # ------------------------------------------------------------------
    section("8. MONITORING REPORTS (COMPLETED + TERMINATED)")
    # ------------------------------------------------------------------

    response = teacher.get(f"{BASE}/api/reports", timeout=15)
    reports = response.json()["reports"]
    terminated = [r for r in reports if r["session_id"] == session_id]
    check("terminated session appears in reports", len(terminated) == 1)
    if terminated:
        check("report is flagged as terminated", terminated[0].get("terminated") is True)
        check("report carries the final trust score", terminated[0]["final_trust_score"] == 0)
        check("report counts violations", terminated[0]["violation_count"] >= 5)
        check("report counts evidence", terminated[0]["evidence_count"] >= 1)

    response = teacher.get(f"{BASE}/api/reports/{session_id}", timeout=15)
    detail = response.json()
    check(
        "report detail loads WITHOUT an exam submission",
        response.ok and detail.get("success") is True,
        response.text[:200],
    )
    check(
        "report detail includes the evidence image ids",
        len(detail["report"]["evidence"]) >= 1,
    )

    # A normal (non-terminated) completion also produces a report.
    normal_session = f"SESSION_E2E_NORMAL_{int(time.time())}"
    requests.post(
        f"{BASE}/api/monitor/live-status",
        json={
            "student_id": TEST_STUDENT_ID,
            "session_id": normal_session,
            "exam_name": "E2E Integrity Exam",
            "status": "ONLINE",
            "trust_score": 100,
            "risk_level": "LOW",
        },
        timeout=15,
    )
    response = requests.post(
        f"{BASE}/api/monitor/session-complete",
        json={
            "session_id": normal_session,
            "student_id": TEST_STUDENT_ID,
            "exam_name": "E2E Integrity Exam",
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "final_trust_score": 100,
            "final_risk_level": "LOW",
        },
        timeout=15,
    )
    check("normal session completion is stored", response.ok and response.json()["success"] is True)

    response = requests.post(
        f"{BASE}/api/monitor/report",
        json={
            "session_id": normal_session,
            "student_id": TEST_STUDENT_ID,
            "exam_name": "E2E Integrity Exam",
            "final_trust_score": 100,
            "final_risk_level": "LOW",
        },
        timeout=15,
    )
    check("normal completion report is generated", response.ok and response.json()["success"] is True)

    # ------------------------------------------------------------------
    section("9. AUTOMATIC EVALUATION")
    # ------------------------------------------------------------------

    # The student browser binds its monitoring session through the
    # precheck page. This is what links the live Trust Score to the
    # submission.
    response = student.post(
        f"{BASE}/api/student/monitor/start",
        json={
            "student_id": TEST_STUDENT_ID,
            "exam_id": exam_id,
            "exam_name": "E2E Integrity Exam",
        },
        timeout=15,
    )
    submission_session = (
        response.json().get("session_id")
        or f"SESSION_E2E_AUTO_{int(time.time())}"
    )
    check("student monitoring session starts", response.ok, response.text[:200])
    print(f"        session_id = {submission_session}")

    response = requests.post(
        f"{BASE}/api/monitor/live-status",
        json={
            "student_id": TEST_STUDENT_ID,
            "session_id": submission_session,
            "exam_name": "E2E Integrity Exam",
            "status": "ONLINE",
            "trust_score": 90,
            "risk_level": "LOW",
        },
        timeout=15,
    )
    check("live heartbeat for the submission session", response.ok, response.text[:200])

    response = student.post(
        f"{BASE}/api/exams/{exam_id}/submit",
        json={"answers": {"0": "B", "1": "C"}},
        timeout=30,
    )
    body = response.json()
    check("student submission is accepted", response.ok and body.get("success") is True, response.text)

    submission = body.get("submission", {})
    check("auto evaluation scores both correct answers", submission.get("score") == 100, str(submission.get("score")))
    check("auto evaluation is marked AUTO", submission.get("evaluation_type") == "AUTO")
    check("auto evaluation status is EVALUATED", submission.get("status") == "EVALUATED")
    auto_submission_id = submission.get("submission_id")
    print(f"        submission_id = {auto_submission_id}")

    response = student.post(
        f"{BASE}/api/exams/{exam_id}/submit",
        json={"answers": {"0": "B", "1": "C"}},
        timeout=15,
    )
    check("duplicate submission is rejected", response.status_code == 409)

    response = teacher.get(f"{BASE}/api/submissions", timeout=15)
    data = response.json()
    check("teacher submission list loads", data.get("success") is True)
    check("submission counters are reported", data.get("count") == 1 and data.get("evaluated") == 1)

    response = teacher.get(f"{BASE}/api/submissions/{auto_submission_id}", timeout=15)
    detail = response.json()["submission"]
    check(
        "teacher can review the answer sheet with correct answers",
        len(detail["answer_review"]) == 2 and detail["answer_review"][0]["correct_answer"] == "B",
    )
    check("answer sheet marks the correct answers", detail["answer_review"][0]["is_correct"] is True)

    response = teacher.get(f"{BASE}/submission-report/{auto_submission_id}", timeout=15)
    check(
        "generated submission report renders",
        response.ok and "PROCTIFY Examination Report" in response.text,
    )

    # ------------------------------------------------------------------
    section("10. MANUAL EVALUATION (TRUST BELOW THRESHOLD)")
    # ------------------------------------------------------------------

    # A fresh student login gives a new Flask session so the submission
    # is not blocked by the previous one.
    student2 = student_session(TEST_STUDENT_USER, TEST_STUDENT_PASSWORD)

    # The duplicate-submission guard is per exam + student, so a second
    # exam is created to obtain a PENDING_REVIEW submission.
    response = teacher.post(
        f"{BASE}/api/exams",
        json={
            "exam_name": "E2E Manual Review Exam",
            "subject": "Testing",
            "duration": 20,
            "scheduled_start": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "questions": questions,
            "selected_students": [TEST_STUDENT_ID],
        },
        timeout=20,
    )
    manual_exam_id = response.json()["exam"]["exam_id"]
    print(f"        manual exam_id = {manual_exam_id}")

    response = student2.get(f"{BASE}/api/student/exams", timeout=15)
    check(
        "student sees the second assigned exam",
        any(e["exam_id"] == manual_exam_id for e in response.json()["exams"]),
    )

    # Bind the low-trust monitoring session so the backend reads it.
    response = student2.post(
        f"{BASE}/api/student/monitor/start",
        json={
            "student_id": TEST_STUDENT_ID,
            "exam_id": manual_exam_id,
            "exam_name": "E2E Manual Review Exam",
        },
        timeout=15,
    )
    low_session = response.json().get("session_id")
    check("low-trust monitoring session starts", response.ok and bool(low_session), response.text[:200])
    print(f"        low-trust session_id = {low_session}")

    response = requests.post(
        f"{BASE}/api/monitor/live-status",
        json={
            "student_id": TEST_STUDENT_ID,
            "session_id": low_session,
            "exam_name": "E2E Manual Review Exam",
            "status": "ONLINE",
            "trust_score": 30,
            "risk_level": "HIGH",
        },
        timeout=15,
    )
    check("low-trust heartbeat accepted", response.ok, response.text[:200])

    response = student2.post(
        f"{BASE}/api/exams/{manual_exam_id}/submit",
        json={"answers": {"0": "A", "1": "A"}},
        timeout=30,
    )
    body = response.json()
    check("low-trust submission is accepted", response.ok and body.get("success") is True, response.text)

    manual = body.get("submission", {})
    check("low-trust submission needs manual review", manual.get("status") == "PENDING_REVIEW")
    check("low-trust submission has no automatic score", manual.get("score") is None)
    check("low-trust submission is marked MANUAL", manual.get("evaluation_type") == "MANUAL")
    manual_submission_id = manual.get("submission_id")
    print(f"        manual submission_id = {manual_submission_id}")

    response = teacher.get(f"{BASE}/api/submissions", timeout=15)
    pending = response.json()
    check("teacher dashboard shows one pending submission", pending.get("pending") == 1, str(pending.get("pending")))

    response = teacher.post(
        f"{BASE}/api/submissions/{manual_submission_id}/evaluate",
        json={"marks": 42, "feedback": "Reviewed manually by the teacher."},
        timeout=20,
    )
    body = response.json()
    check("manual evaluation saves the marks", response.ok and body.get("success") is True, response.text)
    check("manual evaluation marks the submission MANUAL", body["submission"]["evaluation_type"] == "MANUAL")
    check("manual evaluation status becomes EVALUATED", body["submission"]["status"] == "EVALUATED")
    check("manual marks are stored", body["submission"]["teacher_marks"] == 42)
    check("teacher feedback is stored", body["submission"]["teacher_feedback"].startswith("Reviewed"))
    check("manual evaluation regenerates the report", bool(body.get("report_url")))

    # Re-grading an already evaluated submission must also work.
    response = teacher.post(
        f"{BASE}/api/submissions/{manual_submission_id}/evaluate",
        json={"marks": 55, "feedback": "Marks corrected."},
        timeout=20,
    )
    check(
        "teacher can re-grade an evaluated submission",
        response.ok and response.json()["submission"]["teacher_marks"] == 55,
        response.text[:200],
    )

    response = teacher.get(f"{BASE}/api/submissions/{manual_submission_id}", timeout=15)
    check(
        "corrected marks appear on the submission",
        response.json()["submission"]["teacher_marks"] == 55,
    )

    response = requests.post(
        f"{BASE}/api/submissions/{manual_submission_id}/evaluate",
        json={"marks": 10},
        timeout=15,
    )
    check("manual evaluation rejects anonymous callers", response.status_code == 401)

    response = teacher.post(
        f"{BASE}/api/submissions/{manual_submission_id}/evaluate",
        json={"marks": 150},
        timeout=15,
    )
    check("manual evaluation rejects out-of-range marks", response.status_code == 400)

    # ------------------------------------------------------------------
    section("11. EXAM DELETION + DATA CLEARING")
    # ------------------------------------------------------------------

    response = teacher.delete(f"{BASE}/api/submissions/{manual_submission_id}", timeout=15)
    check("teacher can delete a submission", response.ok and response.json()["success"] is True)

    response = teacher.get(f"{BASE}/api/submissions/{manual_submission_id}", timeout=15)
    check("deleted submission is gone", response.status_code == 404)

    response = teacher.post(f"{BASE}/api/exams/{exam_id}/clear-data", timeout=30)
    body = response.json()
    check("clear exam data succeeds", response.ok and body.get("success") is True, response.text)
    check(
        "clear exam data removed monitoring sessions",
        body["cleared"]["sessions"] >= 2,
        str(body["cleared"]["sessions"]),
    )
    check("clear exam data removed evidence rows", body["cleared"]["evidence"] >= 1)

    response = teacher.get(f"{BASE}/api/exams", timeout=15)
    check(
        "cleared exam still exists",
        any(e["exam_id"] == exam_id for e in response.json()["exams"]),
    )

    response = teacher.get(f"{BASE}/api/reports", timeout=15)
    check(
        "cleared sessions no longer appear in reports",
        all(r["session_id"] != session_id for r in response.json()["reports"]),
    )

    response = teacher.delete(f"{BASE}/api/exams/{exam_id}", timeout=30)
    body = response.json()
    check("delete exam succeeds", response.ok and body.get("success") is True, response.text)

    response = teacher.get(f"{BASE}/api/exams", timeout=15)
    check(
        "deleted exam is gone from the exam list",
        all(e["exam_id"] != exam_id for e in response.json()["exams"]),
    )

    response = teacher.delete(f"{BASE}/api/exams/{manual_exam_id}", timeout=30)
    check("second exam can also be deleted", response.ok)

    response = teacher.post(
        f"{BASE}/api/monitor/terminate",
        json={"session_id": "", "student_id": ""},
        timeout=15,
    )
    check("terminate validates its input", response.status_code == 400)

    # ------------------------------------------------------------------
    section("12. STUDENT DELETION CASCADES")
    # ------------------------------------------------------------------

    # Create one more finished session plus evidence so the cascade has
    # something real to remove.
    cascade_session = f"SESSION_E2E_CASCADE_{int(time.time())}"
    requests.post(
        f"{BASE}/api/monitor/live-status",
        json={
            "student_id": TEST_STUDENT_ID,
            "session_id": cascade_session,
            "exam_name": "E2E Cascade Exam",
            "status": "ONLINE",
            "trust_score": 55,
            "risk_level": "MEDIUM",
        },
        timeout=15,
    )
    requests.post(
        f"{BASE}/api/monitor/evidence",
        data={
            "session_id": cascade_session,
            "student_id": TEST_STUDENT_ID,
            "exam_name": "E2E Cascade Exam",
            "violation_type": "MULTIPLE_PERSON",
            "violation_id": "",
        },
        files={"evidence": ("person.jpg", io.BytesIO(make_jpeg()), "image/jpeg")},
        timeout=20,
    )
    requests.post(
        f"{BASE}/api/monitor/session-complete",
        json={
            "session_id": cascade_session,
            "student_id": TEST_STUDENT_ID,
            "exam_name": "E2E Cascade Exam",
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "final_trust_score": 55,
            "final_risk_level": "MEDIUM",
        },
        timeout=15,
    )

    response = teacher.delete(f"{BASE}/api/students/{TEST_STUDENT_ID}", timeout=30)
    body = response.json()
    check("delete student succeeds", response.ok and body.get("success") is True, response.text)
    check(
        "student deletion cascaded to sessions",
        body["deleted"]["sessions"] >= 1,
        str(body["deleted"]),
    )
    check(
        "student deletion cascaded to evidence",
        body["deleted"]["evidence"] >= 1,
        str(body["deleted"]),
    )
    check(
        "student deletion removed the evidence image file",
        body["deleted"]["files"] >= 1,
        str(body["deleted"]),
    )

    response = teacher.get(f"{BASE}/api/reports", timeout=15)
    check(
        "deleted student has no remaining reports",
        all(
            r.get("student_id") != TEST_STUDENT_ID
            for r in response.json()["reports"]
        ),
    )

    response = teacher.get(f"{BASE}/api/enrolled-students", timeout=15)
    check(
        "deleted student is gone from the roster",
        all(s["student_id"] != TEST_STUDENT_ID for s in response.json()["students"]),
    )

    response = student.get(f"{BASE}/api/student/exams", timeout=15)
    check(
        "deleted student can no longer fetch exams",
        TEST_STUDENT_ID not in json.dumps(response.json()),
    )

    # ------------------------------------------------------------------
    section("13. PAGES, BUTTON TARGETS AND LOGOUT")
    # ------------------------------------------------------------------

    for path, needle in (
        ("/teacher", "student-list"),
        ("/exams", "Clear Data"),
        ("/host-exam", "addQuestion"),
    ):
        response = teacher.get(f"{BASE}{path}", timeout=15)
        check(f"{path} renders and is wired", response.ok and needle in response.text)

    response = teacher.get(f"{BASE}/api/excel/enrollment-template", timeout=20)
    check("excel enrollment template downloads", response.ok and len(response.content) > 0)

    response = teacher.get(f"{BASE}/api/excel/assignment-template", timeout=20)
    check("excel assignment template downloads", response.ok and len(response.content) > 0)

    response = teacher.post(f"{BASE}/api/teacher/logout", timeout=15)
    check("teacher logout succeeds", response.ok)

    response = teacher.get(f"{BASE}/api/status", timeout=15)
    check("teacher API is protected again after logout", response.status_code == 401)

    response = student.post(f"{BASE}/api/student/logout", timeout=15)
    check("student logout succeeds", response.ok)

    # ------------------------------------------------------------------
    section("14. TEST ARTIFACT CLEANUP")
    # ------------------------------------------------------------------

    removed = clean_test_artifacts()
    check(
        "test run left no report artifacts behind",
        removed >= 0,
        f"removed {removed}",
    )

    # ------------------------------------------------------------------
    print()
    print("=" * 68)
    print(f"PASSED: {len(PASSED)}   FAILED: {len(FAILED)}")
    print("=" * 68)
    for name in FAILED:
        print(f"  FAILED -> {name}")
    print()
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
