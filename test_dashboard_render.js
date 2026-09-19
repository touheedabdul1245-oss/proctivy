/*
 * PROCTIFY - DASHBOARD RENDER TEST
 * =================================
 *
 * Executes the REAL render functions from
 * dashboard/templates/index.html against a stub DOM.
 *
 * This catches the class of runtime error that produced the broken
 * teacher-dashboard rendering: missing elements, wrong selectors and
 * reading properties off null.
 *
 * Run:
 *
 *     node test_dashboard_render.js
 */

const fs = require("fs");
const path = require("path");

// ---------------------------------------------------------------------
// EXTRACT THE REAL RENDER FUNCTIONS FROM THE DASHBOARD TEMPLATE
// ---------------------------------------------------------------------

const templatePath = path.join(__dirname, "dashboard", "templates", "index.html");
const template = fs.readFileSync(templatePath, "utf8");

const scriptBlocks = [];
const scriptRe = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
let match;
while ((match = scriptRe.exec(template)) !== null) {
  scriptBlocks.push(match[1]);
}
const dashboardJs = scriptBlocks.join("\n");

function sliceFunction(name) {
  const start = dashboardJs.indexOf("function " + name + "(");
  if (start === -1) throw new Error("Missing function: " + name);
  const braceStart = dashboardJs.indexOf("{", start);
  let depth = 0;
  for (let i = braceStart; i < dashboardJs.length; i += 1) {
    if (dashboardJs[i] === "{") {
      depth += 1;
    } else if (dashboardJs[i] === "}") {
      depth -= 1;
      if (depth === 0) return dashboardJs.slice(start, i + 1);
    }
  }
  throw new Error("Unbalanced function: " + name);
}

const renderedFunctions = [
  "escapeHtml",
  "updateStatusTag",
  "getActivityText",
  "updateStudentHealth",
  "createStudentCard",
  "renderStudentCards",
  "updateStudentCard",
  "updateAttentionPanel",
  "updateMonitoringHealth",
  "extractStudents",
  "submissionCardHtml",
]
  .map(sliceFunction)
  .join("\n\n");

const src =
  "var currentStudentId = null;\n" +
  "var lucide = { createIcons: function(){} };\n" +
  "var window = { location: { href: '' }, confirm: function(){return true;}, alert: function(){} };\n" +
  renderedFunctions;

// ---------------------------------------------------------------------
// MINIMAL DOM STUB
// ---------------------------------------------------------------------

function makeElement(tag) {
  const el = {
    tagName: String(tag || "div").toUpperCase(),
    children: [],
    innerHTML: "",
    innerText: "",
    style: {},
    dataset: {},
    onclick: null,
    classes: new Set(),
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() {
        return false;
      },
    },
    setAttribute() {},
    removeAttribute() {},
    appendChild(child) {
      el.children.push(child);
      return child;
    },
    insertBefore(child) {
      el.children.unshift(child);
      return child;
    },
    remove() {},
    addEventListener() {},
    querySelector() {
      return makeElement("div");
    },
    querySelectorAll() {
      return [];
    },
    scrollIntoView() {},
    closest() {
      return null;
    },
    getAttribute() {
      return null;
    },
  };

  el.classList = {
    add(...names) {
      names.forEach((n) => el.classes.add(n));
    },
    remove(...names) {
      names.forEach((n) => el.classes.delete(n));
    },
    toggle(name, on) {
      if (on) el.classes.add(name);
      else el.classes.delete(name);
    },
    contains(name) {
      return el.classes.has(name);
    },
  };

  Object.defineProperty(el, "className", {
    get() {
      return Array.from(el.classes).join(" ");
    },
    set(value) {
      el.classes = new Set(String(value).split(/\s+/).filter(Boolean));
    },
  });

  return el;
}

const registry = {};
[
  "studentGrid",
  "studentGridEmpty",
  "attentionList",
  "pulseCount",
  "pulseDot",
  "healthOverall",
  "cameraHealthDot",
  "audioHealthDot",
  "aiHealthDot",
  "tabHealthDot",
  "activeStudents",
  "totalStudents",
  "examStudentCount",
  "submittedStudents",
  "evaluatedStudents",
  "pendingSubmissions",
  "messageCount",
].forEach((id) => {
  registry[id] = makeElement("div");
});

const removedRows = [];
registry.studentGrid.querySelectorAll = (selector) => {
  if (selector === ".student-row" || selector === ".student-card") return removedRows;
  return [];
};

global.document = {
  getElementById: (id) => registry[id] || null,
  createElement: (tag) => makeElement(tag),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
};

// ---------------------------------------------------------------------
// LOAD THE REAL FUNCTIONS
// ---------------------------------------------------------------------

eval(src);

// ---------------------------------------------------------------------
// FIXTURES
// ---------------------------------------------------------------------

const healthyStudent = {
  student_id: "student1",
  status: "ONLINE",
  trust_score: 85,
  risk_level: "MEDIUM",
  camera_available: true,
  audio_available: true,
  ai_available: true,
  tab_available: true,
  person_count: 1,
  phone: false,
  phone_count: 0,
  gaze: "CENTER",
  head_direction: "LOOK_CENTER",
  last_event: "AUDIO VIOLATION",
};

const atRiskStudent = {
  ...healthyStudent,
  student_id: "student2",
  trust_score: 0,
  risk_level: "HIGH",
  phone: true,
  phone_count: 1,
  camera_available: false,
};

// ---------------------------------------------------------------------
// CHECKS
// ---------------------------------------------------------------------

let failures = 0;

function check(name, fn) {
  try {
    fn();
    console.log("  [PASS] " + name);
  } catch (error) {
    failures += 1;
    console.log("  [FAIL] " + name + " -> " + error.message);
  }
}

function attentionText() {
  const list = registry.attentionList;
  const children = (list.children || []).map((c) => c.innerHTML || "").join(" ");
  return (list.innerHTML + " " + children).trim();
}

console.log("PROCTIFY DASHBOARD RENDER TEST");
console.log("=".repeat(60));

check("extractStudents handles {students:[...]}", () => {
  if (extractStudents({ students: [healthyStudent] }).length !== 1) {
    throw new Error("wrong length");
  }
});

check("extractStudents handles a single student object", () => {
  if (extractStudents(healthyStudent).length !== 1) throw new Error("wrong length");
});

check("extractStudents tolerates garbage input", () => {
  if (extractStudents(null).length !== 0) throw new Error("null not handled");
  if (extractStudents("x").length !== 0) throw new Error("string not handled");
  if (extractStudents(undefined).length !== 0) throw new Error("undefined not handled");
});

check("renderStudentCards renders one LIST ROW per online student", () => {
  registry.studentGrid.children = [];
  removedRows.length = 0;

  renderStudentCards([healthyStudent, atRiskStudent]);

  if (registry.studentGrid.children.length !== 2) {
    throw new Error(
      "expected 2 rows, got " + registry.studentGrid.children.length
    );
  }

  const row = registry.studentGrid.children[0];
  if (!row.classes.has("student-row")) throw new Error("student-row class missing");
  if (row.classes.has("student-card")) throw new Error("still rendering cards");
  if (row.innerHTML.indexOf("student-identity") === -1) {
    throw new Error("identity cell missing");
  }
  if (row.innerHTML.indexOf("Trust") === -1) throw new Error("trust cell missing");
});

check("renderStudentCards shows the empty state when nobody is live", () => {
  registry.studentGrid.children = [];
  renderStudentCards([]);
  if (registry.studentGrid.children.length !== 0) throw new Error("rows left behind");
  if (registry.studentGridEmpty.style.display !== "") {
    throw new Error("empty placeholder not shown");
  }
});

check("renderStudentCards hides the empty state when students are live", () => {
  renderStudentCards([healthyStudent]);
  if (registry.studentGridEmpty.style.display !== "none") {
    throw new Error("empty placeholder still visible");
  }
});

check("renderStudentCards survives a non-array input", () => {
  renderStudentCards(null);
  renderStudentCards(undefined);
});

check("createStudentCard produces the expected row skeleton", () => {
  const card = createStudentCard(healthyStudent);
  [
    "student-top",
    "student-avatar",
    "status-tag",
    "student-identity",
    "student-name",
    "student-id",
    "student-score",
    "student-score-label",
    "score-bar",
    "student-footer",
  ].forEach((cls) => {
    if (card.innerHTML.indexOf(cls) === -1) throw new Error("missing " + cls);
  });
});

check("createStudentCard escapes a hostile student id", () => {
  const card = createStudentCard({ student_id: "<script>x</script>" });
  if (card.innerHTML.indexOf("<script>") !== -1) throw new Error("not escaped");
});

check("updateStudentCard tolerates missing students and cards", () => {
  updateStudentCard(null, healthyStudent);
  updateStudentCard(createStudentCard(healthyStudent), null);
});

check("updateStudentCard handles a missing trust score", () => {
  updateStudentCard(createStudentCard(healthyStudent), {
    ...healthyStudent,
    trust_score: null,
  });
});

check("updateStudentCard handles a real zero trust score", () => {
  updateStudentCard(createStudentCard(healthyStudent), atRiskStudent);
});

check("updateStudentCard does not clobber the selected student id", () => {
  currentStudentId = "student1";
  updateStudentCard(createStudentCard(healthyStudent), atRiskStudent);
  if (currentStudentId !== "student1") {
    throw new Error("selection was overwritten with " + currentStudentId);
  }
});

check("getActivityText prioritises tab and phone events", () => {
  if (
    getActivityText({ tab_status: "VIOLATION", tab_switch_count: 2 }).indexOf(
      "Tab switch"
    ) === -1
  ) {
    throw new Error("tab event not prioritised");
  }
  if (getActivityText({ phone: true }).indexOf("Phone") === -1) {
    throw new Error("phone event not prioritised");
  }
  if (getActivityText({ person_count: 3 }).indexOf("Multiple") === -1) {
    throw new Error("multiple person not detected");
  }
});

check("getActivityText falls back to online/offline wording", () => {
  if (getActivityText({ status: "ONLINE" }) !== "Monitoring active") {
    throw new Error("online fallback wrong");
  }
  if (getActivityText({}) !== "Monitoring offline") {
    throw new Error("offline fallback wrong");
  }
});

check("updateStatusTag maps each risk level to a badge", () => {
  const tag = makeElement("span");

  updateStatusTag(tag, "LOW");
  if (!tag.classes.has("safe")) throw new Error("LOW is not safe");

  updateStatusTag(tag, "MEDIUM");
  if (!tag.classes.has("warning")) throw new Error("MEDIUM is not warning");
  if (tag.classes.has("safe")) throw new Error("stale safe class left behind");

  updateStatusTag(tag, "HIGH");
  if (!tag.classes.has("danger")) throw new Error("HIGH is not danger");

  // A missing risk level falls back to LOW/safe, matching the backend
  // normalizer which always supplies a risk level.
  updateStatusTag(tag, undefined);
  if (!tag.classes.has("safe")) throw new Error("missing risk did not fall back");

  updateStatusTag(null, "LOW");
});

check("updateAttentionPanel flags only live, low-trust students", () => {
  updateAttentionPanel([healthyStudent, atRiskStudent]);
  const text = attentionText();
  if (text.indexOf("student2") === -1) {
    throw new Error("low trust student missing: " + text);
  }
  if (text.indexOf("student1") !== -1) {
    throw new Error("stable student should not be listed: " + text);
  }
});

check("updateAttentionPanel ignores offline students", () => {
  updateAttentionPanel([{ ...atRiskStudent, status: "OFFLINE" }]);
  if (attentionText().indexOf("All students stable") === -1) {
    throw new Error("offline student was treated as an alert");
  }
});

check("updateAttentionPanel shows the all-clear state", () => {
  updateAttentionPanel([healthyStudent]);
  if (attentionText().indexOf("All students stable") === -1) {
    throw new Error("all-clear state missing");
  }
});

check("updateMonitoringHealth goes IDLE with no students", () => {
  updateMonitoringHealth([]);
  if (registry.healthOverall.innerText !== "IDLE") {
    throw new Error("health not IDLE");
  }
});

check("updateMonitoringHealth reports OPTIMAL for a healthy room", () => {
  updateMonitoringHealth([healthyStudent]);
  if (registry.healthOverall.innerText !== "OPTIMAL") {
    throw new Error("health not OPTIMAL, got " + registry.healthOverall.innerText);
  }
});

check("updateMonitoringHealth reports DEGRADED when a device is down", () => {
  updateMonitoringHealth([atRiskStudent]);
  if (registry.healthOverall.innerText !== "DEGRADED") {
    throw new Error("health not DEGRADED");
  }
});

check("updateMonitoringHealth tolerates garbage input", () => {
  updateMonitoringHealth(null);
  updateMonitoringHealth(undefined);
});

check("submissionCardHtml escapes hostile values and keeps its structure", () => {
  const html = submissionCardHtml({
    submission_id: 1,
    student_id: "<img src=x onerror=alert(1)>",
    student_name: "Bad <b>Name</b>",
    exam_name: "Exam & Test",
    status: "PENDING_REVIEW",
    evaluation_type: "MANUAL",
    trust_score: 30,
    total_questions: 2,
    violation_count: 3,
    evidence_count: 1,
    submitted_at: "2026-01-01 10:00:00",
  });

  if (html.indexOf("<img src=x") !== -1) throw new Error("HTML not escaped");
  if (html.indexOf("&lt;img") === -1) throw new Error("entity escaping missing");
  if (html.indexOf("NEEDS REVIEW") === -1) throw new Error("pending badge missing");
  if (html.indexOf("submission-actions") === -1) throw new Error("structure broken");
  if (html.indexOf('data-review="1"') === -1) throw new Error("review button missing");
  if (html.indexOf('data-delsub="1"') === -1) throw new Error("delete button missing");
});

check("submissionCardHtml marks auto-evaluated work as AUTO", () => {
  const html = submissionCardHtml({
    submission_id: 2,
    student_id: "student1",
    exam_name: "Exam",
    status: "EVALUATED",
    evaluation_type: "AUTO",
    score: 90,
    trust_score: 90,
    total_questions: 10,
  });

  if (html.indexOf("AUTO") === -1) throw new Error("auto badge missing");
  if (html.indexOf("NEEDS REVIEW") !== -1) throw new Error("wrongly flagged as pending");
});

console.log("");
console.log(
  failures === 0
    ? "DASHBOARD RENDER LOGIC: all checks passed"
    : "DASHBOARD RENDER LOGIC: " + failures + " failure(s)"
);

process.exit(failures === 0 ? 0 : 1);
