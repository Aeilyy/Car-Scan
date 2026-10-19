import asyncio
import datetime as dt
import io
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

from excel_export import build_workbook
from extractor import extract_fields, warm_up

DATA = Path(os.getenv("DATA_DIR", str(BASE / "data")))
PHOTOS = DATA / "photos"
PENDING = DATA / "pending"
DB_PATH = DATA / "entries.db"
for d in (PHOTOS, PENDING):
    d.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD = 25 * 1024 * 1024

app = FastAPI(title="CarScan", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


with db() as c:
    c.execute(
        """CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            owner_name TEXT NOT NULL DEFAULT '',
            vin TEXT NOT NULL DEFAULT '',
            license_plate TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            photo TEXT NOT NULL
        )"""
    )


def cleanup_pending(max_age_hours: int = 24) -> None:
    cutoff = time.time() - max_age_hours * 3600
    for f in PENDING.glob("*.jpg"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


cleanup_pending()


def normalize_image(raw: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((2400, 2400))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


@app.on_event("startup")
async def _load_models():
    await run_in_threadpool(warm_up)


@app.get("/api/ping")
def ping():
    return {"ok": True, "app": "carscan"}


@app.post("/api/scan")
async def scan(photo: UploadFile = File(...)):
    raw = await photo.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Photo is larger than 25 MB. Take it again at a lower resolution.")
    try:
        jpeg = await run_in_threadpool(normalize_image, raw)
    except Exception:
        raise HTTPException(400, "This file isn't a readable image. Take the photo again.")

    upload_id = uuid.uuid4().hex
    (PENDING / f"{upload_id}.jpg").write_bytes(jpeg)
    result = await run_in_threadpool(extract_fields, jpeg)
    return {"upload_id": upload_id, **result}


class EntryIn(BaseModel):
    upload_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    owner_name: str = Field("", max_length=200)
    vin: str = Field("", max_length=40)
    license_plate: str = Field("", max_length=20)
    description: str = Field("", max_length=2000)


@app.post("/api/entries")
def create_entry(e: EntryIn):
    src = PENDING / f"{e.upload_id}.jpg"
    if not src.exists():
        raise HTTPException(404, "The photo expired. Scan the paperwork again.")
    now = dt.datetime.now().replace(microsecond=0)
    photo_name = f"{now:%Y%m%d-%H%M%S}-{e.upload_id[:8]}.jpg"
    src.replace(PHOTOS / photo_name)
    with db() as c:
        cur = c.execute(
            "INSERT INTO entries (created_at, owner_name, vin, license_plate, description, photo)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                now.isoformat(),
                e.owner_name.strip(),
                e.vin.strip().upper(),
                e.license_plate.strip().upper(),
                e.description.strip(),
                photo_name,
            ),
        )
    return {"ok": True, "id": cur.lastrowid}


@app.get("/api/stats")
def stats():
    with db() as c:
        row = c.execute("SELECT COUNT(*) AS n, MAX(created_at) AS last FROM entries").fetchone()
    return {"count": row["n"], "last": row["last"]}


@app.get("/api/export")
def export():
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM entries ORDER BY created_at, id")]
    data = build_workbook(rows, PHOTOS)
    filename = f"vehicles_{dt.datetime.now():%Y-%m-%d_%H-%M}.xlsx"
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")
