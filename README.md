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
python -m dashboard.app
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

### Student agent CLI options

```bash
python student_agent.py --server http://SERVER:5000 --bind 0.0.0.0 --port 8765
```

| Flag      | Env Var                | Default                 | Purpose                        |
| --------- | ---------------------- | ----------------------- | ------------------------------ |
| `--server`| `PROCTIFY_SERVER_URL`  | `http://127.0.0.1:5000` | Central server URL             |
| `--bind`  | `PROCTIFY_AGENT_URL`   | `127.0.0.1`             | Local bind address             |
| `--port`  |                        | `8765`                  | Local agent port               |

### Environment variables

| Variable                       | Default                 | Purpose                                  |
| ------------------------------ | ----------------------- | ---------------------------------------- |
| `PROCTIFY_DB_HOST/USER/PASSWORD/NAME` | `localhost` / `root` / (`aasmaan@14`) / `proctify_db` | MySQL connection |
| `PROCTIFY_SERVER_URL`          | `http://127.0.0.1:5000` | Central server for agents/monitors       |
| `PROCTIFY_TEACHER_USER/PASSWORD` | `teacher` / `teacher123` | Seeded teacher account                 |
| `PROCTIFY_SECRET_KEY`          | dev default             | Flask session signing key                |
| `PROCTIFY_PORT`                | `5000`                  | Dashboard port                           |
| `PROCTIFY_AGENT_URL`           | `127.0.0.1:8765`        | Student agent URL (used by exam page)    |
| `PROCTIFY_VIDEO_PORT_BASE`     | `5000`                  | Base port for per-student video servers  |
| `PROCTIFY_VIDEO_IDLE_TIMEOUT`  | `8`                     | Seconds before a dead live feed closes   |

---

## 2. Architecture / workflow

```
Student browser            Student PC                     Central server                 Teacher browser
----------------           ----------------------         ----------------------         ----------------
login (MySQL auth))
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

### Trust Score -> 0 (automatic termination)

1. `monitoring_engine.record_violation()` drops the score to 0 and calls
   `_maybe_terminate()`.
2. It posts `/api/monitor/terminate`; the server marks the session
   `TERMINATED`, clears the buffered video frame (so the teacher's feed
   stops immediately) and auto-creates an `exam_submissions` record with
   `trust_score = 0`, `status = PENDING_REVIEW`.
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
| **Terminated**   | Trust Score reached 0                      | `PENDING_REVIEW`, `trust_score = 0`      |

### Monitoring Reports (Batch-based)

The Monitoring Reports page shows batch+exam combinations with all students:

```
Batch 1
DBMS Internal Examination
7 Students

[VIEW REPORT]  [EXPORT Excel]
```

- Reports are grouped by batch + exam, not individual sessions
- Every student in the batch appears, including those who never attempted
- Students who never started show `EXAM NOT ATTEMPTED`
- Terminated students show `AUTO TERMINATED` with Trust Score 0
- Excel export uses `{BatchName} Report.xlsx` format
- Data always comes from the live database (never cached)

---

## 3. File map

### Backend

| Path                          | Role |
| ----------------------------- | ---- |
| `dashboard/app.py`            | The Flask backend: MySQL, schema, auth, page routes, REST endpoints, reports, deletes |
| `dashboard/batch_reports.py`  | Batch-based exam report API (Blueprint): batch report list, detail, Excel export |
| `dashboard/webrtc.py`         | WebRTC signaling blueprint (stub — actual video uses MJPEG over HTTP) |
| `dashboard/__init__.py`       | Package init (empty) |
| `dashboard/templates/*.html`  | Frontend pages (server-rendered shells + inline JS talking to the REST API) |
| `dashboard/static/style.css`  | Shared styling for teacher/monitor pages |
| `student_agent.py`            | Per-student-PC agent on `:8765`. Starts/stops `live_monitor.py`, relays frames to the central server |
| `detectors/live_monitor.py`   | Local AI monitor: YOLO (phone/earphone), MediaPipe (face/hands/gaze/head pose), audio, Trust Score, evidence |
| `monitoring/monitoring_engine.py` | Data layer: violations, evidence, live status, trust history, sessions, reports |

### Frontend pages

| Template                 | Route                       | Purpose |
| ------------------------ | --------------------------- | ------- |
| `login.html`             | `/`                         | Role picker; teacher and student MySQL login |
| `index.html`             | `/teacher`                  | Teacher console: live student list, health indicators, enrollment, evaluations, monitoring reports, alerts |
| `exams.html`             | `/exams`                    | Exam management incl. Clear Data and Delete |
| `exam_details.html`      | `/exam/<exam_id>`           | Exam config + questions, bulk Excel assign, clear data, delete |
| `host_exam.html`         | `/host-exam`                | Create an exam, add questions, assign students/batches |
| `monitor.html`           | `/monitor/<student_id>`     | One student: live camera, trust, detections, terminated state |
| `student_dashboard.html` | `/student`                  | Student's assigned exams, face-verification chip, View Result, sign out |
| `student_verify.html`    | `/student/verify`           | Face verification + first-time face enrolment |
| `student_precheck.html`  | `/student/precheck/<id>`    | Camera/mic/tab/AI checks, starts local agent, binds monitoring session |
| `student_exam.html`      | `/student/exam/<id>`        | Takes the exam, timer, tab-switch reporting, submit, BFCache protection |

### ML / dataset tooling (offline)

`train_model.py`, `split_dataset.py`, `merge_phone_dataset.py`,
`select_proctor_dataset.py`, `collrct_samples.py`, `evaluate_face.py`,
`evaluate_head_pose.py`, `check_missed_phones.py`, `enroll_student_face.py`,
and the `*_test.py` sensor/video smoke scripts. `models/face_landmarker/` and
`runs/detect/proctify_phone_earphone_v2/weights/` hold the model weights.

### Data on disk

| Path                        | Contents |
| --------------------------- | -------- |
| MySQL `proctify_db`         | `students`, `teachers`, `batches`, `batch_members`, `exams`, `exam_assignments` (with `batch_id`), `exam_sessions`, `exam_submissions`, `violations`, `evidence`, `trust_score_history`, `live_students` |
| `reports/evidence/`         | Evidence JPEGs referenced by the `evidence` table |
| `reports/submissions/`      | Generated per-submission HTML reports |
| `shared_data/face_profiles/`| Enrolled face images, named `<student_id>.jpg` |
| `dataset_phone_earphone/`   | Training dataset for phone/earphone detection |
| `runs/detect/`              | YOLO training runs including `proctify_phone_earphone_v2` |
| `models/`                   | Face and hand landmarker models |

---

## 4. REST API

```
# ---- Auth ----
POST   /api/teacher/login             POST /api/teacher/logout
GET    /api/teacher/session
POST   /api/student/login             POST /api/student/logout
GET    /api/student/verify-status

# ---- Face Verification ----
POST   /api/student/verify-face       POST /api/student/enroll-face

# ---- Monitoring ----
POST   /api/student/monitor/start     GET  /api/student/live/current
POST   /api/monitor/live-status       POST /api/monitor/violation
GET    /api/monitor/violations        GET  /api/monitor/evidence
POST   /api/monitor/evidence          POST /api/monitor/terminate
POST   /api/monitor/session-complete  POST /api/monitor/report

# ---- Live Video ----
POST   /api/live/video/frame          GET  /api/live/video/<student_id>

# ---- Students ----
GET    /api/students                  POST /api/students/enroll
DELETE /api/students/<id>             POST /api/students/enroll/excel
GET    /api/enrolled-students

# ---- Batches ----
GET    /api/batches                   GET  /api/batches/<id>/students
DELETE /api/batches/<id>

# ---- Exams ----
POST   /api/exams                     GET  /api/exams
GET    /api/exams/<exam_id>           DELETE /api/exams/<exam_id>
GET    /api/student/exams
POST   /api/exams/<exam_id>/assign/excel
POST   /api/exams/<exam_id>/assign-batch
POST   /api/exams/<exam_id>/clear-data
POST   /api/exams/<exam_id>/submit
POST   /api/exams/<exam_id>/tab-switch
GET    /api/student/exam/<exam_id>/status

# ---- Status / Dashboard ----
GET    /api/status                    GET  /api/summary
GET    /api/system                    GET  /api/student/live/violations
GET    /api/student/exam-result/<exam_id>

# ---- Submissions & Evaluation ----
GET    /api/submissions               GET  /api/submissions/<id>
DELETE /api/submissions/<id>
POST   /api/submissions/<id>/evaluate

# ---- Reports ----
GET    /api/reports                   GET  /api/reports/<session_id>
GET    /api/batch-reports
GET    /api/batch-reports/<batch_id>/<exam_id>
POST   /api/batch-reports/export/excel
POST   /api/reports/export/pdf
GET    /api/evidence/<id>/image
GET    /submission-report/<id>

# ---- Excel Templates ----
GET    /api/excel/enrollment-template
GET    /api/excel/assignment-template

# ---- Health ----
GET    /health
```

Access rules: teacher pages and teacher-only APIs need a teacher session;
student pages need a student session; `/api/monitor/*` and
`/api/live/video/frame` are machine endpoints used by the student agent.
Students never receive `correct_answer` for an exam.

---

## 5. Database schema

```sql
-- Core tables (created/migrated by app.py on startup)
teachers          (teacher_id, teacher_name, username, password, created_at)
students          (student_id, student_name, username, password, created_at)
batches           (batch_id, batch_name, source_filename, student_count, created_at)
batch_members     (id, batch_id, student_id, created_at)
exams             (exam_id, exam_name, subject, total_marks, duration_minutes,
                   questions JSON, status, created_at)
exam_assignments  (assignment_id, exam_id, student_id, batch_id, assigned_at)
exam_sessions     (session_id, student_id, exam_name, start_time, end_time,
                   status ENUM('ONGOING','COMPLETED','TERMINATED'),
                   final_trust_score, final_risk_level)
exam_submissions  (submission_id, exam_id, student_id, session_id, answers JSON,
                   score, teacher_marks, trust_score, total_questions,
                   submitted_at, evaluated_at, status, evaluation_type,
                   teacher_feedback)
violations        (id, session_id, violation_type, severity, penalty,
                   description, timestamp)
evidence          (id, violation_id, session_id, file_path, timestamp)
trust_score_history (id, session_id, old_score, new_score, reason, timestamp)
live_students     (student_id, session_id, exam_name, status, trust_score,
                   risk_level, phone, phone_count, person_count, face_count,
                   hand_count, gaze, head_direction, audio, audio_volume,
                   camera_available, audio_available, ai_available,
                   tab_available, last_event, last_update)
```

---

## 6. Tests

```bash
# Frontend render logic (no browser needed)
node test_dashboard_render.js
#   -> 25 checks running the REAL render functions against a stub DOM

# Batch enrollment API tests (server must be running)
python test_batch_enrollment.py

# Batch reports API test (server must be running)
python test_batch_reports.py
```

---

## 7. Security notes

- Teacher pages and APIs are gated by `@app.before_request` checking
  `session["teacher_id"]`.
- Student pages check `session["student_id"]`.
- `/api/monitor/*` and `/api/live/video/frame` are machine endpoints for the
  student agent.
- Students never receive `correct_answer` for an exam.
- Exam pages use `Cache-Control: no-store` to prevent browser bfcache from
  serving stale active exam pages after submission/termination.
- A `pageshow` / `popstate` handler reloads the exam page if restored from
  browser history, forcing server-side revalidation.
- The `/api/student/exam/<exam_id>/status` endpoint allows the frontend to
  revalidate submission state after bfcache restore.
- Duplicate submission prevention exists in both the route handler and the
  submit endpoint (returns 409).

---

## 8. Things intentionally removed

* `database/` (SQLite `proctify.db`, `trust_engine.py`, `session_manager.py`,
  `violation_logger.py`) -- a second, unused database that could disagree with
  MySQL.
* `shared_data/exams.json`, `shared_data/live_status.json`,
  `shared_data/exams/`, `shared_data/submissions/` -- stale file state.
* `search_hardcoded.py`, `audit_cmd.py`, `web_test.py` -- one-off debug
  scripts pointing at deleted files/models.
* All monitoring/exam history was purged once so the database starts clean
  (the enrolled `students` rows are kept).
"# FINAL_PROCTIFY" 
