# PROCTIFY V2 — AI-Assisted Exam Integrity Platform

Live exam proctoring: the student's own PC runs the AI monitor, the results
stream to a central Flask server, and the teacher console shows live trust
scores, violations, evidence images, reports and evaluations.

Everything persists in **MySQL (`proctify_db`)**, which is the single
authoritative data store. There is no JSON/SQLite state left anywhere.

---

## 1. Run it

```bash
# 1. Central server (teacher + student web app + agent API)
python dashboard/app.py
#    -> http://127.0.0.1:5000

# 2. Student monitoring agent (runs on each student PC)
python student_agent.py
#    -> http://127.0.0.1:8765
```

| Role    | Where              | Credentials (seeded)          |
| ------- | ------------------ | ----------------------------- |
| Teacher | `http://127.0.0.1:5000/` (role: **Teacher**) | `teacher` / `teacher123` |
| Student | `http://127.0.0.1:5000/` (role: **Student**) | see the `students` table |

The server creates the `teachers` table and seeds the account above on first
start. Override with `PROCTIFY_TEACHER_USER` / `PROCTIFY_TEACHER_PASSWORD`.

### Multi-machine / cloud

Point every component at one central URL:

```bash
set PROCTIFY_SERVER_URL=https://your-tunnel.trycloudflare.com
```

Read by `student_agent.py` and `monitoring/monitoring_engine.py`. Leave unset
for a single-machine setup (defaults to `http://127.0.0.1:5000`).

### Environment variables

| Variable                       | Default                 | Purpose                                  |
| ------------------------------ | ----------------------- | ---------------------------------------- |
| `PROCTIFY_DB_HOST/USER/PASSWORD/NAME` | `localhost` / `root` / (`aasmaan@14`) / `proctify_db` | MySQL connection |
| `PROCTIFY_SERVER_URL`          | `http://127.0.0.1:5000` | Central server for agents/monitors       |
| `PROCTIFY_TEACHER_USER/PASSWORD` | `teacher` / `teacher123` | Seeded teacher account                 |
| `PROCTIFY_SECRET_KEY`          | dev default             | Flask session signing key                |
| `PROCTIFY_PORT`                | `5000`                  | Dashboard port                           |
| `PROCTIFY_VIDEO_PORT_BASE`     | `5000`                  | Base port for per-student video servers  |
| `PROCTIFY_VIDEO_IDLE_TIMEOUT`  | `8`                     | Seconds before a dead live feed closes   |

---

## 2. Architecture / workflow

```
Student browser            Student PC                     Central server                 Teacher browser
----------------           ----------------------         ----------------------         ----------------
login (MySQL auth)
 -> /student dashboard
 -> face verify  -------------------------------------->  POST /api/student/verify-face
 -> precheck       -> student_agent.py :8765
                       -> detectors/live_monitor.py
                          -> monitoring/monitoring_engine.py
                             -> POST /api/monitor/live-status  -> MySQL live_students
                             -> POST /api/monitor/violation    -> MySQL violations
                             -> POST /api/monitor/evidence     -> MySQL evidence + JPEG
                       -> POST /api/live/video/frame          -> in-memory newest frame
                                                                               -> GET /api/status
                                                                               -> GET /api/live/video/<id>
submit answers   ------------------------------------->  POST /api/exams/<id>/submit
                                                            Trust >= 50 -> AUTO score
                                                            Trust <  50 -> PENDING_REVIEW
                                                                               -> /api/submissions
                                                                               -> POST .../evaluate
Trust Score hits 0 -> POST /api/monitor/terminate ----->  session TERMINATED
                                                          live feed frames dropped
                                                          session report written
                                                                               -> banner + alerts
```

### Trust Score → 0 (automatic termination)

1. `monitoring_engine.record_violation()` drops the score to 0 and calls
   `_maybe_terminate()`.
2. It posts `/api/monitor/terminate`; the server marks the session
   `TERMINATED`, clears the buffered video frame (so the teacher's feed
   stops immediately) and writes `reports/session_report_<session>.json`.
3. `live_monitor.py` sees `is_terminated()`, publishes one final
   `TERMINATED` live status, breaks its loop and releases camera/microphone.
4. The teacher dashboard's termination watcher shows a red banner, the
   student leaves the live list, and the report opens from **Integrity
   Alerts**.

### Evaluation

| Path             | When                                      | Result                                   |
| ---------------- | ----------------------------------------- | ---------------------------------------- |
| **AUTO**         | Trust Score >= 50 at submission time      | score computed instantly, `EVALUATED`    |
| **MANUAL**       | Trust Score < 50 at submission time       | `PENDING_REVIEW`, no score               |
| **Re-grade**     | Teacher overrides any submission           | `EVALUATED`, `MANUAL`, report regenerated |

---

## 3. File map

### Backend

| Path                          | Role |
| ----------------------------- | ---- |
| `dashboard/app.py`            | The whole Flask backend: MySQL access, schema self-heal, teacher/student auth, page routes, every REST endpoint, report generation, deletes |
| `dashboard/templates/*.html`  | Frontend pages (server-rendered shells + inline JS that talks to the REST API) |
| `dashboard/static/style.css`  | Shared styling for the teacher/monitor pages |
| `student_agent.py`            | Per-student-PC agent on `:8765`. Starts/stops `live_monitor.py`, relays the newest processed JPEG to the central server |
| `detectors/live_monitor.py`   | The local AI monitor: YOLO (phone/earphone), MediaPipe (face/hands/gaze/head pose), audio, Trust Score, evidence capture |
| `monitoring/monitoring_engine.py` | Data layer used by the monitor: violations, evidence, live status, trust history, completed/terminated sessions, reports. Cloud mode posts to the central server; local mode writes MySQL directly |

### Frontend pages

| Template                 | Route                       | Purpose |
| ------------------------ | --------------------------- | ------- |
| `login.html`             | `/`                         | Role picker; teacher and student MySQL login |
| `index.html`             | `/teacher`                  | Teacher console: live **list** of online students, health, attention panel, current exam timer, enrollment, evaluations, monitoring reports, alerts, settings |
| `exams.html`             | `/exams`                    | Exam management incl. **Clear Data** and **Delete** |
| `exam_details.html`      | `/exam/<exam_id>`           | Exam config + questions, bulk Excel assign, clear data, delete |
| `host_exam.html`         | `/host-exam`                | Create an exam, add questions, assign students |
| `monitor.html`           | `/monitor/<student_id>`     | One student: live camera, trust, detections, terminated state |
| `student_dashboard.html` | `/student`                  | Student's assigned exams, face-verification chip, sign out |
| `student_verify.html`    | `/student/verify`           | Face verification + first-time face enrolment |
| `student_precheck.html`  | `/student/precheck/<id>`    | Camera/mic/tab/AI checks, starts the local agent, binds the monitoring session |
| `student_exam.html`      | `/student/exam/<id>`        | Takes the exam, timer, tab-switch reporting, submits |

### ML / dataset tooling (offline)

`train_model.py`, `split_dataset.py`, `merge_phone_dataset.py`,
`select_proctor_dataset.py`, `collrct_samples.py`, `evaluate_face.py`,
`evaluate_head_pose.py`, `check_missed_phones.py`, `enroll_student_face.py`,
and the `*_test.py` sensor/video smoke scripts. `models/face_landmarker/` and
`runs/detect/proctify_phone_earphone_v2/weights/` hold the model weights.

### Data on disk

| Path                        | Contents |
| --------------------------- | -------- |
| MySQL `proctify_db`         | `students`, `teachers`, `exams`, `exam_assignments`, `exam_sessions`, `exam_submissions`, `violations`, `evidence`, `trust_score_history`, `live_students` |
| `reports/evidence/`         | Evidence JPEGs referenced by the `evidence` table |
| `reports/submissions/`      | Generated per-submission HTML reports |
| `reports/session_report_*.json` | Monitoring session reports |
| `shared_data/face_profiles/`| Enrolled face images, named `<student_id>.jpg` |
| `evaluation/`, `evaluation_results/`, `dataset_phone_earphone/` | Offline evaluation datasets and outputs |

### REST API

```
POST   /api/teacher/login             POST /api/teacher/logout      GET  /api/teacher/session
POST   /api/student/login             POST /api/student/logout      GET  /api/student/verify-status
POST   /api/student/verify-face       POST /api/student/enroll-face
POST   /api/student/monitor/start     GET  /api/student/live/current
GET    /api/students                  POST /api/students/enroll     DELETE /api/students/<id>
POST   /api/students/enroll/excel      GET  /api/enrolled-students
POST   /api/exams                      GET  /api/exams              GET  /api/student/exams
GET    /api/exams/<exam_id>            DELETE /api/exams/<exam_id>
POST   /api/exams/<exam_id>/assign/excel
POST   /api/exams/<exam_id>/clear-data POST /api/exams/<exam_id>/complete
POST   /api/exams/<exam_id>/submit     POST /api/exams/<exam_id>/tab-switch
GET    /api/status                     GET  /api/summary            GET  /api/system
POST   /api/monitor/live-status        POST /api/monitor/violation
GET    /api/monitor/violations         GET  /api/monitor/evidence
POST   /api/monitor/evidence           POST /api/monitor/terminate
POST   /api/monitor/session-complete   POST /api/monitor/report
POST   /api/live/video/frame           GET  /api/live/video/<student_id>
GET    /api/submissions                GET  /api/submissions/<id>
DELETE /api/submissions/<id>           POST /api/submissions/<id>/evaluate
GET    /api/reports                    GET  /api/reports/<session_id>
GET    /api/evidence/<id>/image        GET  /submission-report/<id>
GET    /api/excel/enrollment-template  GET  /api/excel/assignment-template
GET    /health
```

Access rules: teacher pages and teacher-only APIs need a teacher session;
student pages need a student session; `/api/monitor/*` and
`/api/live/video/frame` are machine endpoints used by the student agent.
Students never receive `correct_answer` for an exam.

---

## 4. Tests

```bash
# Backend end-to-end (server must be running)
python test_end_to_end.py
#   -> 105 checks: auth, enrolment, exam, monitoring, violations,
#      evidence, termination + report, auto + manual evaluation,
#      exam/submission/student deletion, page wiring, logout

# Frontend render logic (no browser needed)
node test_dashboard_render.js
#   -> 25 checks running the REAL render functions against a stub DOM
```

Both suites must finish green and leave no rows or files behind.

---

## 5. Things intentionally removed

* `database/` (SQLite `proctify.db`, `trust_engine.py`, `session_manager.py`,
  `violation_logger.py`) — a second, unused database that could disagree with
  MySQL.
* `shared_data/exams.json`, `shared_data/live_status.json`,
  `shared_data/exams/`, `shared_data/submissions/` — stale file state.
* `search_hardcoded.py`, `audit_cmd.py`, `web_test.py` — one-off debug
  scripts pointing at deleted files/models.
* All monitoring/exam history was purged once so the database starts clean
  (the enrolled `students` rows are kept).
