import os

from dotenv import load_dotenv

from src.canvas import Canvas

load_dotenv()

api_url = os.getenv("API_URL")
access_token = os.getenv('ACCESS_TOKEN')
save_dir = os.getenv("SAVE_DIR")

canvas = Canvas(api_url, access_token, save_dir)

print(canvas.enrollments)
