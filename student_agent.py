"""
PROCTIFY Student Monitoring Agent
Distributed/cloud-ready version.

Runs on each student's PC.
The browser talks only to localhost:8765.
The local live_monitor.py performs AI monitoring.
Processed JPEG frames are relayed to the central PROCTIFY Flask server.
"""

import os
import sys
import subprocess
import threading
import time
import requests

from flask import Flask, jsonify, request


# ============================================================
# PATHS / CONFIGURATION
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_MONITOR_FILE = os.path.join(BASE_DIR, "detectors", "live_monitor.py")

AGENT_HOST = "127.0.0.1"
AGENT_PORT = 8765

CENTRAL_SERVER_URL = os.environ.get(
    "PROCTIFY_SERVER_URL",
    "https://chances-korean-simultaneously-kidney.trycloudflare.com"
).rstrip("/")

VIDEO_PORT_BASE = 5000
VIDEO_RELAY_INTERVAL = 0.15


# ============================================================
# FLASK APP
# ============================================================

app = Flask("proctify_student_agent")

monitor_process = None
monitor_lock = threading.Lock()

relay_thread = None
relay_stop_event = threading.Event()

heartbeat_thread = None
heartbeat_stop_event = threading.Event()
HEARTBEAT_INTERVAL = 2.0

current_student_id = None
current_session_id = None
current_exam_name = None


# ============================================================
# HELPERS
# ============================================================

def get_student_video_port(student_id):
    """
    live_monitor.py uses port 5000 + trailing numeric student ID.
    Examples:
        student 1 -> 5001
        student 2 -> 5002
    """
    text = str(student_id).strip()

    digits = ""
    for char in reversed(text):
        if char.isdigit():
            digits = char + digits
        else:
            break

    if not digits:
        return VIDEO_PORT_BASE

    return VIDEO_PORT_BASE + int(digits)


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
        "pid": monitor_process.pid if running and monitor_process else None,

        # These flags are consumed by the student pre-check page.
        # The actual camera/AI/audio initialization is performed by
        # live_monitor.py immediately after the process starts.
        "camera_available": bool(running),
        "audio_available": bool(running),
        "ai_available": bool(running),
        "tab_available": bool(running)
    }


# ============================================================
# CENTRAL LIVE HEARTBEAT
# ============================================================

def send_live_heartbeat(student_id, session_id):
    """Keep central LIVE presence alive independently of AI speed."""
    heartbeat_url = f"{CENTRAL_SERVER_URL}/api/monitor/live-status"

    while not heartbeat_stop_event.is_set():
        try:
            response = requests.post(
                heartbeat_url,
                json={
                    "student_id": str(student_id),
                    "session_id": str(session_id),
                    "status": "ONLINE"
                },
                timeout=3
            )
            if not response.ok:
                print("Central heartbeat rejected:", response.status_code)
        except requests.RequestException as error:
            print("Central heartbeat connection error:", error)

        heartbeat_stop_event.wait(HEARTBEAT_INTERVAL)


def start_heartbeat(student_id, session_id):
    global heartbeat_thread
    heartbeat_stop_event.clear()
    heartbeat_thread = threading.Thread(
        target=send_live_heartbeat,
        args=(student_id, session_id),
        daemon=True,
        name="proctify-central-heartbeat"
    )
    heartbeat_thread.start()


def stop_heartbeat():
    global heartbeat_thread
    heartbeat_stop_event.set()
    if heartbeat_thread is not None and heartbeat_thread.is_alive():
        heartbeat_thread.join(timeout=3)
    heartbeat_thread = None


# ============================================================
# PROCESSED VIDEO RELAY
# ============================================================

def relay_processed_video(student_id, session_id):
    """Relay processed MJPEG frames to the central server and reconnect if needed."""
    port = get_student_video_port(student_id)
    video_url = f"http://127.0.0.1:{port}/video_feed"
    frame_url = f"{CENTRAL_SERVER_URL}/api/live/video/frame"

    print()
    print("Starting processed video relay...")
    print("Local processed video:", video_url)
    print("Central video endpoint:", frame_url)
    print()

    while not relay_stop_event.is_set():
        response = None
        try:
            response = requests.get(video_url, stream=True, timeout=(3, None))
            if not response.ok:
                response.close()
                time.sleep(0.5)
                continue

            print("Connected to local processed video.")
            buffer = b""

            for chunk in response.iter_content(chunk_size=4096):
                if relay_stop_event.is_set():
                    break
                if not chunk:
                    continue

                buffer += chunk

                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start < 0:
                        if len(buffer) > 65536:
                            buffer = buffer[-65536:]
                        break

                    end = buffer.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        buffer = buffer[start:]
                        break

                    jpeg = buffer[start:end + 2]
                    buffer = buffer[end + 2:]

                    if not jpeg:
                        continue

                    try:
                        upload = requests.post(
                            frame_url,
                            data={
                                "student_id": str(student_id),
                                "session_id": str(session_id)
                            },
                            files={
                                "frame": ("frame.jpg", jpeg, "image/jpeg")
                            },
                            timeout=3
                        )
                        if not upload.ok:
                            print("Central video upload rejected:", upload.status_code)
                    except requests.RequestException as error:
                        print("Central video upload error:", error)

                    if VIDEO_RELAY_INTERVAL > 0:
                        time.sleep(VIDEO_RELAY_INTERVAL)

            if not relay_stop_event.is_set():
                time.sleep(0.5)

        except requests.RequestException:
            if not relay_stop_event.is_set():
                time.sleep(0.5)
        except Exception as error:
            print("Processed video relay error:", error)
            if not relay_stop_event.is_set():
                time.sleep(0.5)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    print("Processed video relay stopped.")

def start_video_relay(student_id, session_id):
    global relay_thread

    relay_stop_event.clear()

    relay_thread = threading.Thread(
        target=relay_processed_video,
        args=(student_id, session_id),
        daemon=True,
        name="proctify-video-relay"
    )

    relay_thread.start()


def stop_video_relay():
    global relay_thread

    relay_stop_event.set()

    if relay_thread is not None and relay_thread.is_alive():
        relay_thread.join(timeout=3)

    relay_thread = None


# ============================================================
# START / STOP MONITOR
# ============================================================

def start_monitor(student_id, session_id, exam_name):
    global monitor_process
    global current_student_id
    global current_session_id
    global current_exam_name

    with monitor_lock:

        if monitor_process is not None and monitor_process.poll() is None:
            return False, "A monitoring session is already running."

        if not os.path.isfile(LIVE_MONITOR_FILE):
            return False, f"live_monitor.py not found: {LIVE_MONITOR_FILE}"

        student_id = str(student_id or "").strip()
        session_id = str(session_id or "").strip()
        exam_name = str(exam_name or "").strip()

        if not student_id:
            return False, "student_id is required."

        if not session_id:
            return False, "session_id is required."

        if not exam_name:
            return False, "exam_name is required."

        try:
            creationflags = 0

            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            monitor_env = os.environ.copy()

            # live_monitor.py uses these to operate in distributed mode.
            monitor_env["PROCTIFY_SERVER_URL"] = CENTRAL_SERVER_URL
            monitor_env["PROCTIFY_STUDENT_ID"] = student_id
            monitor_env["PROCTIFY_SESSION_ID"] = session_id

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
                creationflags=creationflags,
                env=monitor_env
            )

            current_student_id = student_id
            current_session_id = session_id
            current_exam_name = exam_name

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

            start_heartbeat(student_id, session_id)

            start_video_relay(
                student_id,
                session_id
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

        stop_heartbeat()
        stop_video_relay()

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


# ============================================================
# API ROUTES
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "success": True,
        "agent": "PROCTIFY Student Agent",
        "status": "running",
        "central_server": CENTRAL_SERVER_URL,
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
        "central_server": CENTRAL_SERVER_URL,
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


# ============================================================
# CORS
# ============================================================

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# ============================================================
# CLEAN SHUTDOWN
# ============================================================

def shutdown_monitor():
    try:
        stop_monitor()
    except Exception:
        pass


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("=" * 65)
    print("PROCTIFY STUDENT MONITORING AGENT")
    print("=" * 65)
    print("Project directory :", BASE_DIR)
    print("Live monitor      :", LIVE_MONITOR_FILE)
    print("Agent URL         :", f"http://{AGENT_HOST}:{AGENT_PORT}")
    print("Central server    :", CENTRAL_SERVER_URL)
    print()
    print("Waiting for the PROCTIFY exam browser...")
    print("Keep this window running during development.")
    print("=" * 65)

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
