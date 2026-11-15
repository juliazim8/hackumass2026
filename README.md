# hackumass2026

## Virtual environment

### Mac
```
python3 -m venv .venv\
source .venv/bin/activate
```

### Windows
```
python -m venv .venv
.venv\Scripts\Activate.ps1
```

## Instructions
1) Set up virtual environment above

2) create .env file

3) add api key to .env file: GEMINI_API_KEY="YOUR_API_KEY"

4) set gemini model in .env file: GEMINI_MODEL = "gemini-3.5-flash-lite"

4) pip install -r requirements.txt

5) run ```python -m uvicorn main:app --reload```

## Recipe history setup

Run [`database/recipe_history_steps.sql`](database/recipe_history_steps.sql) in the Supabase SQL Editor
to add storage for recipe ingredients and steps before saving recipes from the app.


## Structure

main.py - API\
autorecipe.py - Gemini endpoint for generating a recipe for a requested dish\
static - folder for UI\
static\index.html - main UI page, including the specific-dish recipe feature\
test_photos\ - test photos of fridges\
app.py - to test gemini api connection is working (IGNORE)\
detect.py - to test if gemini api can parse photo for ingredients (IGNORE)
