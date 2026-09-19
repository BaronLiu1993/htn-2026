import os


# Automated tests must never inherit developer credentials or consume live APIs.
# Individual agent tests inject their own scripted transport when OpenAI behavior
# is under test.
os.environ["FEDERATO_CLIENT_ID"] = ""
os.environ["FEDERATO_CLIENT_SECRET"] = ""
os.environ["OPENAI_API_KEY"] = ""
