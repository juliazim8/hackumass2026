import os, sys, json
from dotenv import load_dotenv
from pydantic import BaseModel
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client()

# One place to change the model. Use a name from YOUR AI Studio dashboard.
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

class Ingredient(BaseModel):
    name: str
    quantity_estimate: str
    confidence: str  # "high" | "medium" | "low"

class Pantry(BaseModel):
    items: list[Ingredient]

def detect(paths):
    parts = []
    for p in paths:
        with open(p, "rb") as f:
            parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/jpeg"))
    parts.append(
        "List every identifiable food item in these photos. "
        "Mark confidence honestly. Do not list items you cannot see."
    )
    resp = client.models.generate_content(
        model=MODEL,
        contents=parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Pantry,
            temperature=0,
        ),
    )
    return resp.parsed

if __name__ == "__main__":
    result = detect(sys.argv[1:])
    print(json.dumps(result.model_dump(), indent=2))