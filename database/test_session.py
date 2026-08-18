from session_manager import create_session


student_id = "STUDENT_TEST_001"

session_id = create_session(
    student_id
)

print()
print("========================================")
print("NEW SESSION CREATED")
print("========================================")
print("Session ID:", session_id)
print("========================================")