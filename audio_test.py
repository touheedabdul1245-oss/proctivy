import sounddevice as sd
import numpy as np
import time

# ============================================================
# PROCTIFY - AUDIO MONITORING TEST
# ============================================================

SAMPLE_RATE = 48000
CHANNELS = 1

# Volume threshold
SPEECH_THRESHOLD = 0.0025

# How long speech must continue
VIOLATION_TIME = 1.5


# ------------------------------------------------------------
# Variables
# ------------------------------------------------------------

speaking_start = None
violation_triggered = False


# ------------------------------------------------------------
# Audio callback
# ------------------------------------------------------------

def audio_callback(indata, frames, time_info, status):

    global speaking_start
    global violation_triggered

    if status:
        print("Audio status:", status)

    # Calculate volume
    volume = np.sqrt(
        np.mean(
            np.square(indata)
        )
    )

    current_time = time.time()

    # --------------------------------------------------------
    # Speech detected
    # --------------------------------------------------------

    if volume > SPEECH_THRESHOLD:

        if speaking_start is None:

            speaking_start = current_time

        speaking_duration = (
            current_time -
            speaking_start
        )

        print(
            f"\rSpeaking: {speaking_duration:.1f}s "
            f"| Volume: {volume:.4f}",
            end=""
        )

        # ----------------------------------------------------
        # 3-second violation
        # ----------------------------------------------------

        if (
            speaking_duration >= VIOLATION_TIME
            and
            not violation_triggered
        ):

            violation_triggered = True

            print()
            print()
            print("========================================")
            print("AUDIO VIOLATION DETECTED")
            print("Speaking detected for 1.5+ seconds")
            print("========================================")

    # --------------------------------------------------------
    # No speech
    # --------------------------------------------------------

    else:

        speaking_start = None
        violation_triggered = False

        print(
            f"\rListening... | Volume: {volume:.4f}",
            end=""
        )


# ============================================================
# START
# ============================================================

print()
print("========================================")
print("PROCTIFY AUDIO MONITORING TEST")
print("========================================")
print()
print("Speak continuously for 1.5 seconds.")
print("Then stop speaking.")
print("Press CTRL+C to stop.")
print()


try:

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        callback=audio_callback
    ):

        while True:

            time.sleep(0.1)


except KeyboardInterrupt:

    print()
    print()
    print("Audio monitoring stopped.")


except Exception as e:

    print()
    print("ERROR:", e)