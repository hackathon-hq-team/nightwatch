"""HTTP dependency fixture: a demo token authorizes GET /profile; all others get 401."""
from fastapi import Depends, FastAPI, Header, HTTPException


class AccessDenied(ValueError):
    """The request supplied no valid demo access token."""


app = FastAPI()


def require_token(x_token: str | None = Header(default=None)) -> str:
    """Adapt the X-Token header to an HTTP authentication decision."""
    # This constant is fixture data, not an API credential or real authentication system.
    if x_token != "demo-token":
        raise AccessDenied("Missing or invalid X-Token")
    return x_token


@app.get("/profile")
def read_profile(token: str = Depends(require_token)) -> dict:
    return {"user": "demo", "scope": "read"}


@app.get("/health")
def health() -> dict:
    """Health checks remain public and return 200."""
    return {"status": "ok"}
