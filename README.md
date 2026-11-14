# hackumass2026

Virtual environment

python3 -m venv .venv\
source .venv/bin/activate

## Instruction
1) ``
pip install -U google-genai
``

2) add api key to .env file: GEMINI_API_KEY="YOUR_API_KEY"

3) Configure the login credentials in `.env`:
   `APP_USERNAME="your-username"` and `APP_PASSWORD="your-password"`.

4) Start the app with `uvicorn main:app --reload` and open
   `http://127.0.0.1:8000/login`.