
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
RECOMMENDED_RECIPES = Path(__file__).parent / "static" / "recommended_recipes.html"

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


class AccountRequest(BaseModel):
    name: str
    password: str


class SavedRecipeRequest(BaseModel):
    title: str
    steps: list[str] = Field(default_factory=list)


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


def replace_user_ingredients(user_id: int, items: list[Ingredient]) -> int:
    rows = [
        {
            "user_id": user_id,
            "ingredient": item.name.strip(),
            "quantity_estimate": (
                int(item.quantity_estimate.strip())
                if item.quantity_estimate.strip().isdecimal()
                else None
            ),
        }
        for item in items
        if item.name.strip()
    ]

    db(sb.table("ingredients").delete().eq("user_id", user_id))
    remaining = db(
        sb.table("ingredients")
        .select("id")
        .eq("user_id", user_id)
    )
    if remaining:
        raise HTTPException(
            502,
            "Could not remove the previous ingredient list. No new ingredients were added.",
        )

    if rows:
        db(sb.table("ingredients").insert(rows))
    return len(rows)


# ---------- Routes ----------
@app.get("/")
def home(request: Request):
    if not logged_in_user(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(INDEX)
@app.get("/recommended_recipes.html")
def recommended_recipes_page():
    return FileResponse(RECOMMENDED_RECIPES)

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
    replace_user_ingredients(get_user_id(username), pantry.items)
    return pantry.model_dump()


@app.post("/api/recipes")
def recipes(
    req: RecipeRequest,
    username: str = Depends(current_user),
):
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
    return {"added": replace_user_ingredients(user_id, req.items)}


# ---------- Saved recipes ----------
@app.get("/api/saved-recipes")
def get_saved_recipes(username: str = Depends(current_user)):
    user_id = get_user_id(username)
    return {"recipes": db(
        sb.table("recipes").select("*").eq("user_id", user_id)
    )}

@app.post("/api/saved-recipes")
def add_saved_recipe(
    req: SavedRecipeRequest,
    username: str = Depends(current_user),
):
    user_id = get_user_id(username)

    existing = db(
        sb.table("recipes")
        .select("*")
        .eq("user_id", user_id)
        .eq("recipe", req.title)
    )

    if existing:
        saved = existing[0]
        return db(
            sb.table("recipes")
            .update({
                "quantity": (saved["quantity"] or 0) + 1,
                "steps": req.steps,
            })
            .eq("id", saved["id"])
        )[0]

    return db(
        sb.table("recipes").insert({
            "user_id": user_id,
            "recipe": req.title,
            "quantity": 1,
            "steps": req.steps,
        })
    )[0]


@app.delete("/api/saved-recipes/{recipe_id}")
def delete_saved_recipe(
    recipe_id: int,
    username: str = Depends(current_user),
):
    user_id = get_user_id(username)
    deleted = db(
        sb.table("recipes")
        .delete()
        .eq("id", recipe_id)
        .eq("user_id", user_id)
        .select("id")
    )
    if not deleted:
        raise HTTPException(404, "Recipe not found.")
    return {"deleted": True}