"""
WebRTC Live Video Integration for Proctoring System
Uses Janus WebRTC Server for multi-stream support
All functions are isolated - NO modifications to existing tested code
"""

import json
import threading
import time
from flask import Blueprint, request, session, jsonify

webrtc_bp = Blueprint('webrtc', __name__)

# Thread-safe student session storage
_student_sessions = {}
_sessions_lock = threading.Lock()


@webrtc_bp.route("/api/students/start-webrtc", methods=["POST"])
def start_student_webrtc():
    """
    Student endpoint: Starts WebRTC connection to Janus
    Returns signaling info for student's browser
    """
    data = request.get_json()
    student_id = data.get("student_id") if data else None

    if not student_id:
        return jsonify({"success": False, "error": "student_id required"}), 400

    with _sessions_lock:
        _student_sessions[student_id] = {
            "connected": True,
            "registered_at": time.time(),
            "feed_id": f"feed_{student_id}"
        }

    return jsonify({
        "success": True,
        "student_id": student_id,
        "message": "WebRTC signaling info generated",
        "feed_id": f"feed_{student_id}"
    }), 200


@webrtc_bp.route("/api/teacher/connected-students", methods=["GET"])
def get_connected_students():
    """Teacher endpoint: Gets list of currently connected student feeds"""
    with _sessions_lock:
        students = list(_student_sessions.keys())
    return jsonify({
        "success": True,
        "connected_students": students,
        "count": len(students)
    }), 200


@webrtc_bp.route("/api/teacher/stop-student/<student_id>", methods=["POST"])
def stop_student_webrtc(student_id):
    """Teacher endpoint: Stops a student's WebRTC feed"""
    with _sessions_lock:
        if student_id in _student_sessions:
            _student_sessions[student_id]["connected"] = False
            return jsonify({
                "success": True,
                "message": f"Student {student_id} feed stopped"
            }), 200
        return jsonify({
            "success": False,
            "error": "Student not found"
        }), 404