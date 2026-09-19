"""
PROCTIFY Student Monitoring Agent
Distributed/cloud-ready version.

Runs on each student's PC.
The browser talks only to localhost:8765.
The local live_monitor.py performs AI monitoring.
The Student Agent periodically fetches the latest already-processed
JPEG from the local AI monitor and uploads that JPEG to the central
PROCTIFY server.

IMPORTANT:
This version does NOT relay an infinite local MJPEG stream.
That prevents local buffering and keeps CPU/network usage reasonable.
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

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

LIVE_MONITOR_FILE = os.path.join(
    BASE_DIR,
    "detectors",
    "live_monitor.py"
)

AGENT_HOST = "127.0.0.1"
AGENT_PORT = 8765

# The central PROCTIFY server. Runs on this same machine by default,
# and can be pointed at a tunnel or a remote host for multi-PC exams:
#
#   set PROCTIFY_SERVER_URL=https://your-tunnel.trycloudflare.com
#
CENTRAL_SERVER_URL = os.environ.get(
    "PROCTIFY_SERVER_URL",
    "https://adequate-mariah-surrounding-values.trycloudflare.com"
).rstrip("/")


# Local live_monitor.py video server ports. This keeps the dashboard
# (5000) and the student agent (8765) out of the video port range.
VIDEO_PORT_BASE = int(
    os.environ.get("PROCTIFY_VIDEO_PORT_BASE", "5000")
)

# One fresh processed frame approximately every second.
VIDEO_UPLOAD_INTERVAL = 1.0

HEARTBEAT_INTERVAL = 3.0


# ============================================================
# FLASK APP
# ============================================================

app = Flask("proctify_student_agent")

monitor_process = None
monitor_lock = threading.Lock()


# ============================================================
# THREAD STATE
# ============================================================

video_thread = None
video_stop_event = threading.Event()

heartbeat_thread = None
heartbeat_stop_event = threading.Event()


# ============================================================
# CURRENT SESSION
# ============================================================

current_student_id = None
current_session_id = None
current_exam_name = None


# ============================================================
# HELPERS
# ============================================================

def get_student_video_port(student_id):

    text = str(student_id).strip()

    digits = ""

    for char in reversed(text):

        if char.isdigit():
            digits = char + digits

        else:
            break

    if not digits:
        return VIDEO_PORT_BASE + 1

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

        "student_id":
            current_student_id,

        "session_id":
            current_session_id,

        "exam_name":
            current_exam_name,

        "pid":
            monitor_process.pid
            if running and monitor_process
            else None,

        "camera_available":
            bool(running),

        "audio_available":
            bool(running),

        "ai_available":
            bool(running),

        "tab_available":
            bool(running)
    }


# ============================================================
# CENTRAL HEARTBEAT
# ============================================================

def send_live_heartbeat(
    student_id,
    session_id
):

    heartbeat_url = (
        f"{CENTRAL_SERVER_URL}"
        f"/api/monitor/live-status"
    )

    session = requests.Session()

    while not heartbeat_stop_event.is_set():

        try:

            response = session.post(

                heartbeat_url,

                json={
                    "student_id":
                        str(student_id),

                    "session_id":
                        str(session_id),

                    "status":
                        "ONLINE"
                },

                timeout=(2, 2)
            )

            if not response.ok:

                print(
                    "Central heartbeat rejected:",
                    response.status_code
                )

        except requests.Timeout:

            print(
                "Central heartbeat timeout - "
                "skipping this heartbeat."
            )

        except requests.RequestException as error:

            print(
                "Central heartbeat connection error:",
                error
            )

        heartbeat_stop_event.wait(
            HEARTBEAT_INTERVAL
        )

    try:
        session.close()
    except Exception:
        pass


def start_heartbeat(
    student_id,
    session_id
):

    global heartbeat_thread

    heartbeat_stop_event.clear()

    heartbeat_thread = threading.Thread(

        target=send_live_heartbeat,

        args=(
            student_id,
            session_id
        ),

        daemon=True,

        name="proctify-central-heartbeat"
    )

    heartbeat_thread.start()


def stop_heartbeat():

    global heartbeat_thread

    heartbeat_stop_event.set()

    if (
        heartbeat_thread is not None
        and heartbeat_thread.is_alive()
    ):

        heartbeat_thread.join(
            timeout=3
        )

    heartbeat_thread = None


# ============================================================
# LATEST PROCESSED FRAME RELAY
# ============================================================
# High-smoothness cloud video transport.
#
# IMPORTANT:
# - live_monitor.py already keeps ONE pre-encoded JPEG in memory.
# - We fetch that JPEG frequently from localhost.
# - Fetching and uploading are separate threads.
# - Only the newest frame is kept; old frames are discarded.
# - A slow Cloudflare request therefore cannot block local capture.
#
# Target:
#   local fetch  ~10 FPS
#   central upload ~5 FPS
#
# This is intentionally "latest frame" transport rather than an
# infinite MJPEG relay. It is much more tolerant of variable
# Internet latency.

VIDEO_FETCH_INTERVAL = 0.10       # ~10 local fetches/sec
VIDEO_UPLOAD_INTERVAL = 0.20      # ~5 cloud uploads/sec

video_fetch_thread = None
video_upload_thread = None

video_frame_lock = threading.Lock()
video_latest_frame = None
video_frame_sequence = 0

video_fetch_stop_event = threading.Event()
video_upload_stop_event = threading.Event()


def fetch_latest_video_frames(student_id):
    """Continuously fetch the newest processed JPEG from local AI."""

    global video_latest_frame
    global video_frame_sequence

    port = get_student_video_port(student_id)

    local_frame_url = (
        f"http://127.0.0.1:{port}/latest_frame"
    )

    print()
    print("Starting local processed-frame fetcher...")
    print("Local frame endpoint:", local_frame_url)
    print()

    session = requests.Session()

    # A very short connect timeout prevents a broken local server from
    # holding this thread. Read timeout is also short because the
    # /latest_frame endpoint returns exactly one JPEG.
    request_timeout = (0.5, 1.0)

    while not video_fetch_stop_event.is_set():

        try:
            response = session.get(
                local_frame_url,
                timeout=request_timeout
            )

            try:
                if response.status_code == 200 and response.content:
                    frame = response.content

                    # Keep ONLY the newest JPEG.
                    with video_frame_lock:
                        video_latest_frame = frame
                        video_frame_sequence += 1

                elif response.status_code not in (204, 200):
                    print(
                        "Local latest-frame rejected:",
                        response.status_code
                    )

            finally:
                response.close()

        except requests.Timeout:
            # Do not spam the terminal. A single missed frame is harmless.
            pass

        except requests.RequestException as error:
            print(
                "Local latest-frame connection error:",
                error
            )

        video_fetch_stop_event.wait(
            VIDEO_FETCH_INTERVAL
        )

    try:
        session.close()
    except Exception:
        pass

    print("Local processed-frame fetcher stopped.")


def upload_latest_video_frames(student_id, session_id):
    """Upload the newest locally processed JPEG to the central server."""

    global video_latest_frame

    central_frame_url = (
        f"{CENTRAL_SERVER_URL}"
        f"/api/live/video/frame"
    )

    print("Starting central processed-frame uploader...")
    print("Central frame endpoint:", central_frame_url)
    print()

    session = requests.Session()

    # Do not use a long timeout here. If Cloudflare/Internet is slow,
    # abandon that upload and immediately continue with newer frames.
    request_timeout = (1.5, 2.5)

    last_uploaded_sequence = -1

    while not video_upload_stop_event.is_set():

        frame = None
        sequence = -1

        with video_frame_lock:
            if (
                video_latest_frame is not None
                and video_frame_sequence != last_uploaded_sequence
            ):
                frame = video_latest_frame
                sequence = video_frame_sequence

        if frame is not None:

            try:
                # Send the JPEG as the RAW HTTP request body.
                # The student/session IDs are query parameters.
                #
                # This avoids multipart/form-data parsing through
                # Cloudflare, which was causing intermittent 400/500
                # errors at the central Flask endpoint.
                upload_url = (
                    f"{central_frame_url}"
                    f"?student_id={requests.utils.quote(str(student_id))}"
                    f"&session_id={requests.utils.quote(str(session_id))}"
                )

                response = session.post(
                    upload_url,
                    data=frame,
                    headers={
                        "Content-Type": "image/jpeg",
                        "Cache-Control": "no-cache"
                    },
                    timeout=request_timeout
                )

                try:
                    if response.ok:
                        last_uploaded_sequence = sequence

                    elif response.status_code not in (408, 429, 502, 503, 504):
                        print(
                            "Central video upload rejected:",
                            response.status_code
                        )

                finally:
                    response.close()

            except requests.Timeout:
                # The important behavior: do NOT retry the same old frame.
                # The next loop will take whatever newer frame is available.
                pass

            except requests.RequestException as error:
                print(
                    "Central video upload error:",
                    error
                )

        video_upload_stop_event.wait(
            VIDEO_UPLOAD_INTERVAL
        )

    try:
        session.close()
    except Exception:
        pass

    print("Central processed-frame uploader stopped.")


def relay_latest_video(student_id, session_id):
    """
    Compatibility wrapper.

    The actual relay is now two independent workers:
      1. local frame fetcher
      2. central frame uploader

    Keeping them separate prevents network latency from blocking
    localhost frame acquisition.
    """

    global video_fetch_thread
    global video_upload_thread

    video_fetch_stop_event.clear()
    video_upload_stop_event.clear()

    video_fetch_thread = threading.Thread(
        target=fetch_latest_video_frames,
        args=(student_id,),
        daemon=True,
        name="proctify-local-frame-fetcher"
    )

    video_upload_thread = threading.Thread(
        target=upload_latest_video_frames,
        args=(student_id, session_id),
        daemon=True,
        name="proctify-central-frame-uploader"
    )

    video_fetch_thread.start()
    video_upload_thread.start()

    # Keep this function alive until the relay is asked to stop.
    while (
        not video_stop_event.is_set()
    ):
        video_stop_event.wait(0.5)

    video_fetch_stop_event.set()
    video_upload_stop_event.set()

    if (
        video_fetch_thread is not None
        and video_fetch_thread.is_alive()
    ):
        video_fetch_thread.join(timeout=2)

    if (
        video_upload_thread is not None
        and video_upload_thread.is_alive()
    ):
        video_upload_thread.join(timeout=3)

    video_fetch_thread = None
    video_upload_thread = None

    print("Latest-frame video relay stopped.")


def start_video_relay(student_id, session_id):

    global video_thread

    video_stop_event.clear()

    video_thread = threading.Thread(
        target=relay_latest_video,
        args=(student_id, session_id),
        daemon=True,
        name="proctify-latest-video-relay"
    )

    video_thread.start()


def stop_video_relay():

    global video_thread

    video_stop_event.set()
    video_fetch_stop_event.set()
    video_upload_stop_event.set()

    if (
        video_thread is not None
        and video_thread.is_alive()
    ):
        video_thread.join(timeout=6)

    video_thread = None


# ============================================================
# START / STOP MONITOR
# ============================================================

def start_monitor(
    student_id,
    session_id,
    exam_name
):

    global monitor_process

    global current_student_id
    global current_session_id
    global current_exam_name


    with monitor_lock:

        if (
            monitor_process is not None
            and monitor_process.poll() is None
        ):

            return (
                False,
                "A monitoring session is already running."
            )


        if not os.path.isfile(
            LIVE_MONITOR_FILE
        ):

            return (
                False,
                f"live_monitor.py not found: "
                f"{LIVE_MONITOR_FILE}"
            )


        student_id = str(
            student_id or ""
        ).strip()

        session_id = str(
            session_id or ""
        ).strip()

        exam_name = str(
            exam_name or ""
        ).strip()


        if not student_id:
            return False, "student_id is required."

        if not session_id:
            return False, "session_id is required."

        if not exam_name:
            return False, "exam_name is required."


        try:

            creationflags = 0

            if os.name == "nt":

                creationflags = (
                    subprocess.CREATE_NO_WINDOW
                )


            monitor_env = os.environ.copy()

            monitor_env[
                "PROCTIFY_SERVER_URL"
            ] = CENTRAL_SERVER_URL

            monitor_env[
                "PROCTIFY_STUDENT_ID"
            ] = student_id

            monitor_env[
                "PROCTIFY_SESSION_ID"
            ] = session_id


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


            time.sleep(0.8)


            if monitor_process.poll() is not None:

                exit_code = (
                    monitor_process.returncode
                )

                monitor_process = None

                current_student_id = None
                current_session_id = None
                current_exam_name = None

                return (
                    False,
                    "Monitoring process exited "
                    f"immediately with code {exit_code}."
                )


            # Live-status updates are already sent by live_monitor.py
            # in a dedicated background worker. Do not send a second
            # heartbeat request through Cloudflare; it can overwrite
            # live status and wastes bandwidth.

            start_video_relay(
                student_id,
                session_id
            )


            return (
                True,
                "Monitoring started."
            )


        except Exception as error:

            monitor_process = None

            current_student_id = None
            current_session_id = None
            current_exam_name = None

            return (
                False,
                str(error)
            )


def stop_monitor():

    global monitor_process

    global current_student_id
    global current_session_id
    global current_exam_name


    with monitor_lock:

        stop_video_relay()

        # No separate heartbeat thread is used. live_monitor.py sends
        # the complete authoritative live-status payload.


        if monitor_process is None:

            current_student_id = None
            current_session_id = None
            current_exam_name = None

            return (
                True,
                "No monitoring process was running."
            )


        process = monitor_process


        try:

            if process.poll() is None:

                process.terminate()

                try:

                    process.wait(
                        timeout=5
                    )

                except subprocess.TimeoutExpired:

                    process.kill()

                    process.wait(
                        timeout=2
                    )


            monitor_process = None

            current_student_id = None
            current_session_id = None
            current_exam_name = None


            return (
                True,
                "Monitoring stopped."
            )


        except Exception as error:

            return (
                False,
                str(error)
            )


# ============================================================
# API ROUTES
# ============================================================

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify({

        "success":
            True,

        "agent":
            "PROCTIFY Student Agent",

        "status":
            "running",

        "central_server":
            CENTRAL_SERVER_URL,

        "monitor":
            monitor_status()
    })


@app.route(
    "/status",
    methods=["GET"]
)
def status():

    return jsonify({

        "success":
            True,

        "monitor":
            monitor_status()
    })


@app.route(
    "/start",
    methods=["POST", "OPTIONS"]
)
def start():

    if request.method == "OPTIONS":

        return (
            "",
            204
        )


    data = (
        request.get_json(
            silent=True
        )
        or {}
    )


    success, message = start_monitor(

        data.get("student_id"),

        data.get("session_id"),

        data.get("exam_name")
    )


    if not success:

        return jsonify({

            "success":
                False,

            "message":
                message,

            "monitor":
                monitor_status()

        }), 400


    return jsonify({

        "success":
            True,

        "message":
            message,

        "central_server":
            CENTRAL_SERVER_URL,

        "monitor":
            monitor_status()
    })


@app.route(
    "/stop",
    methods=["POST", "OPTIONS"]
)
def stop():

    if request.method == "OPTIONS":

        return (
            "",
            204
        )


    success, message = stop_monitor()


    return jsonify({

        "success":
            success,

        "message":
            message,

        "monitor":
            monitor_status()

    }), (
        200
        if success
        else 500
    )


# ============================================================
# CORS
# ============================================================

@app.after_request
def add_cors_headers(response):

    response.headers[
        "Access-Control-Allow-Origin"
    ] = "*"

    response.headers[
        "Access-Control-Allow-Methods"
    ] = "GET, POST, OPTIONS"

    response.headers[
        "Access-Control-Allow-Headers"
    ] = "Content-Type"

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

    print(
        "Project directory :",
        BASE_DIR
    )

    print(
        "Live monitor      :",
        LIVE_MONITOR_FILE
    )

    print(
        "Agent URL         :",
        f"http://{AGENT_HOST}:{AGENT_PORT}"
    )

    print(
        "Central server    :",
        CENTRAL_SERVER_URL
    )

    print()

    print(
        "Waiting for the PROCTIFY exam browser..."
    )

    print(
        "Keep this window running during development."
    )

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
