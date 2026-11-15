import os
import secrets
from pathlib import Path

from auth import current_user, logged_in_user, router as auth_router
from autorecipe import router as autorecipe_router
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    Body
)
from fastapi.responses import FileResponse, RedirectResponse
from google import genai
from google.genai import types
from pydantic import BaseModel
from supabase import create_client, Client

# ---------- Configuration ----------
load_dotenv()
client = genai.Client()  # reads GEMINI_API_KEY
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
INDEX = Path(__file__).parent / "static" / "index.html"
MAX_PHOTOS = 4
MAX_BYTES = 30 * 1024 * 1024

app = FastAPI(title="FridgeChef")
app.include_router(auth_router)
app.include_router(autorecipe_router)


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

def db(call):
    try:
        return call.execute().data
    except Exception as e:
        raise HTTPException(502, f"Database error: {str(e)[:200]}")


# ---- user table ----
@app.post("/api/signup")
def signup(name: str = Body(...), password: str = Body(...)):
    name = name.strip()
    if not name or not password:
        raise HTTPException(400, "Enter a name and password.")
    if db(sb.table("user").select("user_id").eq("name", name)):
        raise HTTPException(400, "That name is taken.")
    row = db(sb.table("user").insert({"name": name, "password": password}))[0]
    return {"user_id": row["user_id"]}


@app.post("/api/login")
def login(name: str = Body(...), password: str = Body(...)):
    rows = db(sb.table("user").select("user_id").eq("name", name.strip()).eq("password", password))
    if not rows:
        raise HTTPException(401, "Wrong name or password.")
    return {"user_id": rows[0]["user_id"]}


@app.get("/api/users")
def get_users():
    return {"users": db(sb.table("user").select("user_id,name"))}


# ---- ingredients table ----
@app.get("/api/ingredients")
def get_ingredients(user_id: int):
    return {"items": db(sb.table("ingredients").select("*").eq("user_id", user_id))}


@app.post("/api/ingredients")
def add_ingredients(user_id: int = Body(...), items: list[str] = Body(...)):
    rows = [{"user_id": user_id, "ingredient": n.strip()} for n in items if n.strip()]
    if rows:
        db(sb.table("ingredients").insert(rows))
    return {"added": len(rows)}


# ---- recipes table ----
@app.get("/api/saved-recipes")
def get_saved_recipes(user_id: int):
    return {"recipes": db(sb.table("recipes").select("*").eq("user_id", user_id))}


@app.post("/api/saved-recipes")
def add_saved_recipe(user_id: int = Body(...), recipe: str = Body(...)):
    return db(sb.table("recipes").insert({"user_id": user_id, "recipe": recipe}))[0]