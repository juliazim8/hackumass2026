from pathlib import Path

from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter()
LOGIN_PAGE = Path(__file__).parent / "static" / "login.html"
SIGNUP_PAGE = Path(__file__).parent / "static" / "signup.html"
COOKIE = "fridgechef_session"

# token -> username. Lives in memory, so everyone is logged out when the server restarts.
SESSIONS: dict[str, str] = {}


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


@router.get("/signup")
def signup_page(request: Request):
    if logged_in_user(request):
        return RedirectResponse("/", status_code=303)
    return FileResponse(SIGNUP_PAGE)


@router.post("/api/logout")
def logout(request: Request, response: Response):
    SESSIONS.pop(request.cookies.get(COOKIE, ""), None)
    response.delete_cookie(COOKIE)
    return {"ok": True}
