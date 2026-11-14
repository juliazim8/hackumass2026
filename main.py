import os
import secrets
from pathlib import Path

from auth import current_user, logged_in_user, router as auth_router
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse
from google import genai
from google.genai import types
from pydantic import BaseModel


# ---------- Configuration ----------
load_dotenv()
client = genai.Client()  # reads GEMINI_API_KEY
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
INDEX = Path(__file__).parent / "static" / "index.html"
MAX_PHOTOS = 4
MAX_BYTES = 30 * 1024 * 1024

app = FastAPI(title="FridgeChef")
app.include_router(auth_router)


# ---------- Schemas (the shape we ask Gemini to return) ----------
class Ingredient(BaseModel):
    name: str
    quantity_estimate: str
    confidence: str  # "high" | "medium" | "low"


class Pantry(BaseModel):
    items: list[Ingredient]


class Recipe(BaseModel):
    title: str
    minutes: int
    uses: list[str]
    missing: list[str]
    steps: list[str]


class Recipes(BaseModel):
    recipes: list[Recipe]


class RecipeRequest(BaseModel):
    items: list[str]
    diet: str = ""
    max_minutes: int = 45
    assume_staples: bool = True


# ---------- Gemini helper ----------
def ask_gemini(contents, schema):
    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0,
            ),
        )
    except Exception as e:
        msg = str(e)
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            raise HTTPException(429, "Rate limit reached. Wait a minute and try again.")
        if "404" in msg or "NOT_FOUND" in msg:
            raise HTTPException(502, f"Model '{MODEL}' not found. Set GEMINI_MODEL in .env.")
        raise HTTPException(502, f"Gemini request failed: {msg[:200]}")
    if resp.parsed is None:
        raise HTTPException(502, "Gemini returned an unreadable answer. Try again.")
    return resp.parsed


# ---------- Routes ----------
@app.get("/")
def home(request: Request):
    if not logged_in_user(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(INDEX)


@app.post("/api/detect", dependencies=[Depends(current_user)])
async def detect(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "Upload at least one photo.")
    if len(files) > MAX_PHOTOS:
        raise HTTPException(400, f"Upload at most {MAX_PHOTOS} photos.")

    parts = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(400, f"{f.filename} is over 8 MB. Use a smaller photo.")
        mime = f.content_type if (f.content_type or "").startswith("image/") else "image/jpeg"
        parts.append(types.Part.from_bytes(data=data, mime_type=mime))

    parts.append(
        "List every identifiable food item in these photos. "
        "Give a rough quantity and mark confidence honestly "
        "(high, medium, or low). Do not list items you cannot see."
    )
    pantry = ask_gemini(parts, Pantry)
    return pantry.model_dump()


@app.post("/api/recipes", dependencies=[Depends(current_user)])
def recipes(req: RecipeRequest):
    items = [i.strip() for i in req.items if i.strip()]
    if not items:
        raise HTTPException(400, "Add at least one ingredient.")

    staples = (
        "Assume salt, pepper, cooking oil, and water are available."
        if req.assume_staples
        else "Do not assume any pantry staples."
    )
    prompt = (
        f"Ingredients on hand: {', '.join(items)}.\n"
        f"Dietary needs: {req.diet or 'none'}.\n"
        f"Maximum cooking time: {req.max_minutes} minutes.\n"
        f"{staples}\n"
        "Suggest 3 recipes that mostly use the ingredients on hand. "
        "Put anything not on hand in 'missing'. Respect the dietary needs strictly. "
        "Include safe internal temperatures for any meat, poultry, or eggs."
    )
    return ask_gemini(prompt, Recipes).model_dump()