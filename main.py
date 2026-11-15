
import os
import secrets
from pathlib import Path

from auth import current_user, logged_in_user, router as auth_router
from autorecipe import router as autorecipe_router
from auth import COOKIE, SESSIONS, current_user, logged_in_user, router as auth_router
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, Body
from fastapi.responses import FileResponse, RedirectResponse
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from supabase import create_client, Client

# ---------- Configuration ----------
load_dotenv()
client = genai.Client()
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
INDEX = Path(__file__).parent / "static" / "index.html"
MAX_PHOTOS = 4
MAX_BYTES = 30 * 1024 * 1024

app = FastAPI(title="FridgeChef")
app.include_router(auth_router)
app.include_router(autorecipe_router)


# ---------- Schemas ----------
class Ingredient(BaseModel):
    name: str
    quantity_estimate: str
    confidence: str


class Pantry(BaseModel):
    items: list[Ingredient]


class IngredientSaveRequest(BaseModel):
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
    allow_shopping: bool = False
    min_missing_items: int = Field(default=1, ge=0, le=20)
    max_missing_items: int = Field(default=3, ge=0, le=20)


class AccountRequest(BaseModel):
    name: str
    password: str


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


# ---------- Database helper ----------
def db(call):
    try:
        return call.execute().data
    except Exception as e:
        raise HTTPException(502, f"Database error: {str(e)[:200]}")


def get_user_id(username: str):
    users = db(sb.table("user").select("user_id").eq("name", username))
    if not users:
        raise HTTPException(404, "User not found.")
    return users[0]["user_id"]


# ---------- Routes ----------
@app.get("/")
def home(request: Request):
    if not logged_in_user(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(INDEX)


@app.post("/api/detect")
async def detect(
    files: list[UploadFile] = File(...),
    username: str = Depends(current_user),
):
    if not files:
        raise HTTPException(400, "Upload at least one photo.")
    if len(files) > MAX_PHOTOS:
        raise HTTPException(400, f"Upload at most {MAX_PHOTOS} photos.")

    parts = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(400, f"{f.filename} is over 30 MB. Use a smaller photo.")
        mime = f.content_type if (f.content_type or "").startswith("image/") else "image/jpeg"
        parts.append(types.Part.from_bytes(data=data, mime_type=mime))

    parts.append(
        "List every identifiable food item in these photos. "
        "Give a rough quantity ONLY as an integer value and mark confidence honestly "
        "(high, medium, or low). Do not list items you cannot see."
    )

    pantry = ask_gemini(parts, Pantry)
    return pantry.model_dump()


@app.post("/api/recipes")
def recipes(
    req: RecipeRequest,
    username: str = Depends(current_user),
):
    items = [i.strip() for i in req.items if i.strip()]
    if not items:
        raise HTTPException(400, "Add at least one ingredient.")
    if req.min_missing_items > req.max_missing_items:
        raise HTTPException(400, "Minimum shopping items cannot exceed the maximum.")

    staples = (
        "Assume salt, pepper, cooking oil, and water are available."
        if req.assume_staples
        else "Do not assume any pantry staples."
    )

    shopping_rule = (
        f"Only suggest recipes requiring between {req.min_missing_items} and "
        f"{req.max_missing_items} distinct ingredients not on hand. List all of those "
        "ingredients in 'missing' and do not exceed that range."
        if req.allow_shopping
        else "Only suggest recipes that can be made with ingredients on hand and assumed staples. "
        "The 'missing' list must be empty for every recipe."
    )
    prompt = (
        f"Ingredients on hand: {', '.join(items)}.\n"
        f"Dietary needs: {req.diet or 'none'}.\n"
        f"Maximum cooking time: {req.max_minutes} minutes.\n"
        f"{staples}\n"
        "Suggest up to 3 recipes. "
        f"{shopping_rule} "
        "Respect the dietary needs strictly. "
        "Include safe internal temperatures for any meat, poultry, or eggs."
    )
    result = ask_gemini(prompt, Recipes)
    result.recipes = [
        recipe for recipe in result.recipes
        if (
            req.min_missing_items
            <= len({name.strip().casefold() for name in recipe.missing if name.strip()})
            <= req.max_missing_items
            if req.allow_shopping
            else not recipe.missing
        )
    ]
    return result.model_dump()


# ---------- Signup ----------
@app.post("/api/signup")
def signup(req: AccountRequest, response: Response):
    name = req.name.strip()
    if not name or not req.password:
        raise HTTPException(400, "Enter a name and password.")
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    if db(sb.table("user").select("user_id").eq("name", name)):
        raise HTTPException(409, "That username is taken.")

    row = db(sb.table("user").insert({"name": name, "password": req.password}))[0]
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = name

    response.set_cookie(
        COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7
    )
    return {"user_id": row["user_id"]}


# ---------- Login ----------
@app.post("/api/login")
def login(req: AccountRequest, response: Response):
    name = req.name.strip()
    rows = db(sb.table("user").select("user_id,name,password").eq("name", name))

    if not rows or req.password != rows[0]["password"]:
        raise HTTPException(401, "Incorrect username or password.")

    token = secrets.token_urlsafe(32)
    SESSIONS[token] = name

    response.set_cookie(
        COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7
    )
    return {"user_id": rows[0]["user_id"]}


@app.get("/api/users")
def get_users():
    return {"users": db(sb.table("user").select("user_id,name"))}


# ---------- Ingredients ----------
@app.get("/api/ingredients")
def get_ingredients(username: str = Depends(current_user)):
    user_id = get_user_id(username)
    return {
        "items": db(
            sb.table("ingredients").select("*").eq("user_id", user_id)
        )
    }


@app.post("/api/ingredients")
def add_ingredients(
    req: IngredientSaveRequest,
    username: str = Depends(current_user),
):
    user_id = get_user_id(username)

    rows = [
        {
            "user_id": user_id,
            "ingredient": item.name.strip(),
            "quantity_estimate": item.quantity_estimate.strip(),
        }
        for item in req.items
        if item.name.strip()
    ]

    if rows:
        db(sb.table("ingredients").insert(rows))

    return {"added": len(rows)}


# ---------- Saved recipes ----------
@app.get("/api/saved-recipes")
def get_saved_recipes(user_id: int):
    return {"recipes": db(sb.table("recipes").select("*").eq("user_id", user_id))}


@app.post("/api/saved-recipes")
def add_saved_recipe(user_id: int = Body(...), recipe: str = Body(...)):
    return db(sb.table("recipes").insert({"user_id": user_id, "recipe": recipe}))[0]
