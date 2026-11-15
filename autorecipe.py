import os

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from auth import current_user

load_dotenv()
client = genai.Client()
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

router = APIRouter()


class DishRecipeRequest(BaseModel):
    dish: str = Field(min_length=1, max_length=120)
    items: list[str] = Field(min_length=1)
    diet: str = ""
    max_minutes: int = 45
    assume_staples: bool = True


class DishRecipe(BaseModel):
    title: str
    minutes: int
    uses: list[str]
    missing: list[str]
    steps: list[str]


@router.post("/api/recipe-for-dish", dependencies=[Depends(current_user)])
def recipe_for_dish(req: DishRecipeRequest):
    dish = req.dish.strip()
    items = [item.strip() for item in req.items if item.strip()]
    if not dish:
        raise HTTPException(400, "Enter the dish you want to make.")
    if not items:
        raise HTTPException(400, "Add at least one ingredient.")
    if req.max_minutes < 1:
        raise HTTPException(400, "Cooking time must be at least one minute.")

    staples = (
        "Assume salt, pepper, cooking oil, and water are available."
        if req.assume_staples
        else "Do not assume any pantry staples."
    )
    prompt = (
        f"The user wants to make: {dish}.\n"
        f"Ingredients on hand: {', '.join(items)}.\n"
        f"Dietary needs: {req.diet.strip() or 'none'}.\n"
        f"Maximum cooking time: {req.max_minutes} minutes.\n"
        f"{staples}\n"
        "Create one practical recipe for the requested dish. Use the ingredients on hand "
        "where possible. List ingredients that are not on hand in 'missing'. Respect "
        "dietary needs strictly. Include safe internal temperatures for any meat, "
        "poultry, or eggs in the instructions."
    )

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=DishRecipe,
                temperature=0,
            ),
        )
    except Exception as exc:
        message = str(exc)
        if "429" in message or "RESOURCE_EXHAUSTED" in message:
            raise HTTPException(429, "Rate limit reached. Wait a minute and try again.")
        if "404" in message or "NOT_FOUND" in message:
            raise HTTPException(502, f"Model '{MODEL}' not found. Set GEMINI_MODEL in .env.")
        raise HTTPException(502, f"Gemini request failed: {message[:200]}") from exc

    if response.parsed is None:
        raise HTTPException(502, "Gemini returned an unreadable recipe. Try again.")
    return {"recipe": response.parsed.model_dump()}