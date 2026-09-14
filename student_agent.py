"""
PROCTIFY Student Monitoring Agent

Runs on the student's PC.

Purpose:
- Provides a localhost control API for the student browser.
- Starts/stops live_monitor.py locally.
- Does NOT expose the student's camera directly to the Internet.
- The existing live_monitor.py remains responsible for camera, microphone,
  YOLO, MediaPipe, Trust Score, violations, and evidence.

Usage:
    python student_agent.py

The browser can call:
    GET  http://127.0.0.1:8765/health
    POST http://127.0.0.1:8765/start
    POST http://127.0.0.1:8765/stop

Start request JSON:
{
    "student_id": "1",
    "session_id": "SESSION_123",
    "exam_name": "PROCTIFY EXAM"
}

Stop request JSON:
{
    "student_id": "1"
}
"""

import os
import sys
import subprocess
import threading
import time
from flask import Flask, jsonify, request
from werkzeug.serving import make_server


# ------------------------------------------------------------
# PATHS
# ------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_MONITOR_FILE = os.path.join(BASE_DIR, "detectors", "live_monitor.py")

AGENT_HOST = "127.0.0.1"
AGENT_PORT = 8765


# ------------------------------------------------------------
# FLASK APP
# ------------------------------------------------------------

app = Flask("proctify_student_agent")

monitor_process = None
monitor_lock = threading.Lock()

current_student_id = None
current_session_id = None
current_exam_name = None


# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------

def is_monitor_running():
    global monitor_process

    with monitor_lock:
        if monitor_process is None:
            return False

        if monitor_process.poll() is None:
            return True

        monitor_process = None
        return False


def monitor_status():
    running = is_monitor_running()

    return {
        "running": running,
        "student_id": current_student_id,
        "session_id": current_session_id,
        "exam_name": current_exam_name,
        "pid": monitor_process.pid if running and monitor_process else None
    }


def start_monitor(student_id, session_id, exam_name):
    global monitor_process
    global current_student_id
    global current_session_id
    global current_exam_name

    with monitor_lock:

        # Do not allow two monitors to run on the same student PC.
        if monitor_process is not None and monitor_process.poll() is None:
            return False, "A monitoring session is already running."

        if not os.path.isfile(LIVE_MONITOR_FILE):
            return False, f"live_monitor.py not found: {LIVE_MONITOR_FILE}"

        student_id = str(student_id).strip()
        session_id = str(session_id).strip()
        exam_name = str(exam_name).strip()

        if not student_id:
            return False, "student_id is required."

        if not session_id:
            return False, "session_id is required."

        if not exam_name:
            return False, "exam_name is required."

        try:
            # Windows:
            # CREATE_NO_WINDOW prevents a separate console window from
            # appearing for the monitor process.
            creationflags = 0

            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            monitor_process = subprocess.Popen(
                [
                    sys.executable,
                    LIVE_MONITOR_FILE,
                    student_id,
                    session_id,
                    exam_name
                ],
                cwd=BASE_DIR,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags
            )

            current_student_id = student_id
            current_session_id = session_id
            current_exam_name = exam_name

            # Give the process a short moment to fail immediately if the
            # environment/model/imports are broken.
            time.sleep(0.5)

            if monitor_process.poll() is not None:
                exit_code = monitor_process.returncode
                monitor_process = None
                current_student_id = None
                current_session_id = None
                current_exam_name = None

                return (
                    False,
                    f"Monitoring process exited immediately with code {exit_code}."
                )

            return True, "Monitoring started."

        except Exception as error:
            monitor_process = None
            current_student_id = None
            current_session_id = None
            current_exam_name = None

            return False, str(error)


def stop_monitor():
    global monitor_process
    global current_student_id
    global current_session_id
    global current_exam_name

    with monitor_lock:

        if monitor_process is None:
            current_student_id = None
            current_session_id = None
            current_exam_name = None
            return True, "No monitoring process was running."

        process = monitor_process

        try:
            if process.poll() is None:
                process.terminate()

                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

            monitor_process = None
            current_student_id = None
            current_session_id = None
            current_exam_name = None

            return True, "Monitoring stopped."

        except Exception as error:
            return False, str(error)


# ------------------------------------------------------------
# API ROUTES
# ------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "success": True,
        "agent": "PROCTIFY Student Agent",
        "status": "running",
        "monitor": monitor_status()
    })


@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "success": True,
        "monitor": monitor_status()
    })


@app.route("/start", methods=["POST", "OPTIONS"])
def start():

    # Browser CORS preflight.
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}

    student_id = data.get("student_id")
    session_id = data.get("session_id")
    exam_name = data.get("exam_name")

    success, message = start_monitor(
        student_id,
        session_id,
        exam_name
    )

    if not success:
        return jsonify({
            "success": False,
            "message": message,
            "monitor": monitor_status()
        }), 400

    return jsonify({
        "success": True,
        "message": message,
        "monitor": monitor_status()
    })


@app.route("/stop", methods=["POST", "OPTIONS"])
def stop():

    if request.method == "OPTIONS":
        return ("", 204)

    success, message = stop_monitor()

    return jsonify({
        "success": success,
        "message": message,
        "monitor": monitor_status()
    }), (200 if success else 500)


# ------------------------------------------------------------
# CORS HEADERS
# ------------------------------------------------------------

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# ------------------------------------------------------------
# CLEAN SHUTDOWN
# ------------------------------------------------------------

def shutdown_monitor():
    try:
        stop_monitor()
    except Exception:
        pass


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

if __name__ == "__main__":

    print("=" * 60)
    print("PROCTIFY STUDENT MONITORING AGENT")
    print("=" * 60)
    print(f"Project directory : {BASE_DIR}")
    print(f"Live monitor      : {LIVE_MONITOR_FILE}")
    print(f"Agent URL         : http://{AGENT_HOST}:{AGENT_PORT}")
    print()
    print("Waiting for the PROCTIFY exam browser...")
    print("Keep this window running during development.")
    print("=" * 60)

    try:
        app.run(
            host=AGENT_HOST,
            port=AGENT_PORT,
            debug=False,
            threaded=True,
            use_reloader=False
        )
    finally:
        shutdown_monitor()
