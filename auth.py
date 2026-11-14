import os
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

router = APIRouter()
LOGIN_PAGE = Path(__file__).parent / "static" / "login.html"
COOKIE = "fridgechef_session"

# token -> username. Lives in memory, so everyone is logged out when the server restarts.
SESSIONS: dict[str, str] = {}


class LoginRequest(BaseModel):
    username: str
    password: str


def logged_in_user(request: Request):
    return SESSIONS.get(request.cookies.get(COOKIE, ""))


def current_user(request: Request) -> str:
    """Dependency: use on any route that needs a logged-in user."""
    user = logged_in_user(request)
    if not user:
        raise HTTPException(401, "Please sign in.")
    return user


@router.get("/login")
def login_page(request: Request):
    if logged_in_user(request):
        return RedirectResponse("/", status_code=303)
    return FileResponse(LOGIN_PAGE)


@router.post("/api/login")
def login(req: LoginRequest, response: Response):
    expected_user = os.getenv("APP_USERNAME", "cooked")
    expected_pass = os.getenv("APP_PASSWORD", "fr")
    if not expected_user or not expected_pass:
        raise HTTPException(500, "APP_USERNAME and APP_PASSWORD are not set in .env.")

    user_ok = secrets.compare_digest(req.username.encode(), expected_user.encode())
    pass_ok = secrets.compare_digest(req.password.encode(), expected_pass.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(401, "Incorrect username or password.")

    token = secrets.token_urlsafe(32)
    SESSIONS[token] = req.username
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7
    )
    return {"ok": True}


@router.post("/api/logout")
def logout(request: Request, response: Response):
    SESSIONS.pop(request.cookies.get(COOKIE, ""), None)
    response.delete_cookie(COOKIE)
    return {"ok": True}
