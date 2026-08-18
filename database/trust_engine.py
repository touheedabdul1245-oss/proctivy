import sqlite3
import os


# ============================================================
# PROCTIFY - TRUST SCORE ENGINE
# ============================================================

DATABASE_PATH = os.path.join(
    os.path.dirname(__file__),
    "proctify.db"
)


# ============================================================
# PENALTIES
# ============================================================

PENALTIES = {
    "PHONE_DETECTED": 10,
    "MULTIPLE_PERSONS": 10,
    "LOOKING_LEFT": 5,
    "LOOKING_RIGHT": 5,
    "LOOKING_DOWN": 5,
    "AUDIO_VIOLATION": 5
}


# ============================================================
# RISK LEVEL
# ============================================================

def get_risk_level(score):

    if score >= 80:

        return "LOW"

    elif score >= 50:

        return "MEDIUM"

    else:

        return "HIGH"


# ============================================================
# APPLY PENALTY
# ============================================================

def apply_penalty(
    session_id,
    violation_type
):

    if violation_type not in PENALTIES:

        print(
            f"Unknown violation: {violation_type}"
        )

        return None


    penalty = PENALTIES[
        violation_type
    ]


    connection = sqlite3.connect(
        DATABASE_PATH
    )

    cursor = connection.cursor()


    # --------------------------------------------------------
    # Get current trust score
    # --------------------------------------------------------

    cursor.execute(
        """
        SELECT trust_score
        FROM exam_sessions
        WHERE session_id = ?
        """,
        (session_id,)
    )


    result = cursor.fetchone()


    if result is None:

        connection.close()

        print(
            "Session not found."
        )

        return None


    old_score = result[0]


    # --------------------------------------------------------
    # Calculate new score
    # --------------------------------------------------------

    new_score = max(
        0,
        old_score - penalty
    )


    # --------------------------------------------------------
    # Update session
    # --------------------------------------------------------

    cursor.execute(
        """
        UPDATE exam_sessions
        SET trust_score = ?
        WHERE session_id = ?
        """,
        (
            new_score,
            session_id
        )
    )


    # --------------------------------------------------------
    # Save trust score history
    # --------------------------------------------------------

    cursor.execute(
        """
        INSERT INTO trust_score_history
        (
            session_id,
            old_score,
            new_score,
            reason
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            session_id,
            old_score,
            new_score,
            violation_type
        )
    )


    connection.commit()

    connection.close()


    # --------------------------------------------------------
    # Calculate risk
    # --------------------------------------------------------

    risk = get_risk_level(
        new_score
    )


    # --------------------------------------------------------
    # Display
    # --------------------------------------------------------

    print()
    print("========================================")
    print("PROCTIFY VIOLATION")
    print("========================================")
    print(
        f"Type       : {violation_type}"
    )
    print(
        f"Penalty    : -{penalty}"
    )
    print(
        f"Trust Score: {new_score}"
    )
    print(
        f"Risk Level : {risk}"
    )
    print(
        "========================================")


    # IMPORTANT:
    # Always return TWO values.
    return new_score, risk