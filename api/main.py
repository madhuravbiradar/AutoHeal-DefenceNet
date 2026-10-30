"""FastAPI app: REST API + static dashboard.   Run:  python run.py   (or: uvicorn api.main:app)"""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.service import Runtime

ROOT = Path(__file__).resolve().parent.parent
rt = Runtime()


@asynccontextmanager
async def lifespan(app):
    rt.start()
    yield
    rt.stop()


app = FastAPI(title="Self-Healing Network Control Plane", version="1.0", lifespan=lifespan)


def viewer(authorization: Optional[str] = Header(None)):
    tok = (authorization or "").replace("Bearer ", "")
    who = rt.who(tok)
    if not who:
        raise HTTPException(401, "login required")
    return who


def operator(who=Depends(viewer)):
    if who["role"] != "operator":
        raise HTTPException(403, "operator role required")
    return who


class Login(BaseModel):
    username: str          # username or email
    password: str


class Signup(BaseModel):
    full_name: str = ""
    username: str = ""
    email: str = ""
    password: str = ""
    confirm_password: str = ""
    role: str = ""
    organization: str = ""
    operator_code: str = ""


class Inject(BaseModel):
    kind: str
    target: str


class Manual(BaseModel):
    action: str
    link: Optional[str] = None
    node: Optional[str] = None
    path: Optional[List[str]] = None


class Speed(BaseModel):
    tick_seconds: float


def _run(fn, *a):
    try:
        fn(*a)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/login")
def login(b: Login):
    r = rt.login(b.username, b.password)
    if not r:
        raise HTTPException(401, "bad credentials")
    return r


@app.post("/api/signup")
def signup(b: Signup):
    errors = rt.signup(b.model_dump() if hasattr(b, "model_dump") else b.dict())
    if errors:
        raise HTTPException(422, {"errors": errors})
    return {"ok": True}


@app.post("/api/logout")
def logout(authorization: Optional[str] = Header(None)):
    rt.logout((authorization or "").replace("Bearer ", ""))
    return {"ok": True}


@app.get("/api/me")
def me(who=Depends(viewer)):
    return who


@app.get("/api/state")
def state(who=Depends(viewer)):
    return rt.state()


@app.get("/api/alerts")
def alerts(who=Depends(viewer)):
    return rt.store.alerts()


@app.get("/api/incidents")
def incidents(who=Depends(viewer)):
    return rt.incidents()


@app.get("/api/incidents/{iid}")
def incident(iid: int, who=Depends(viewer)):
    r = rt.incident(iid)
    if not r:
        raise HTTPException(404, "no such incident")
    return r


@app.get("/api/audit")
def audit(who=Depends(viewer)):
    return rt.store.audit()


@app.get("/api/scenarios")
def scenarios(who=Depends(viewer)):
    return rt.scenarios()


@app.get("/api/compare")
def compare(who=Depends(viewer)):
    return rt.compare()


@app.post("/api/incidents/{iid}/approve")
def approve(iid: int, who=Depends(operator)):
    return _run(rt.approve, who, iid)


@app.post("/api/incidents/{iid}/reject")
def reject(iid: int, who=Depends(operator)):
    return _run(rt.reject, who, iid)


@app.post("/api/incidents/{iid}/manual")
def manual(iid: int, b: Manual, who=Depends(operator)):
    params = {k: v for k, v in (("link", b.link), ("node", b.node), ("path", b.path)) if v}
    return _run(rt.manual, who, iid, b.action, params)


@app.post("/api/pause")
def pause(who=Depends(operator)):
    return _run(rt.set_mode, who, "paused")


@app.post("/api/resume")
def resume(who=Depends(operator)):
    return _run(rt.set_mode, who, "auto")


@app.post("/api/inject")
def inject(b: Inject, who=Depends(operator)):
    return _run(rt.inject, who, b.kind, b.target)


@app.post("/api/reset")
def reset(wipe: bool = False, who=Depends(operator)):
    return _run(rt.reset, who, wipe)


@app.post("/api/scenarios/{name}/start")
def start_scenario(name: str, who=Depends(operator)):
    return _run(rt.start_scenario, who, name)


@app.post("/api/speed")
def speed(b: Speed, who=Depends(operator)):
    return _run(rt.set_speed, who, b.tick_seconds)


@app.get("/")
def index():
    return FileResponse(ROOT / "dashboard" / "index.html")


@app.get("/login")
def login_page():
    return FileResponse(ROOT / "dashboard" / "signin.html")


@app.get("/signup")
def signup_page():
    return FileResponse(ROOT / "dashboard" / "signup.html")


app.mount("/static", StaticFiles(directory=ROOT / "dashboard"), name="static")
