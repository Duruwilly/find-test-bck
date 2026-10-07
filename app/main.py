import hashlib
import io
import re
import secrets
import time
from urllib.parse import urlparse
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from nanoid import generate
from PIL import Image, ImageOps
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import Card, CardView, Correction, get_session, init_db
from .schemas import (
    CardCreate,
    CardCreated,
    CardOut,
    CardUpdate,
    CorrectionIn,
    CorrectionOut,
    MediaOut,
)

ALPHABET = "23456789abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O/1/l/I
MEDIA_DIR = Path(settings.media_dir)
(MEDIA_DIR / "photos").mkdir(parents=True, exist_ok=True)
(MEDIA_DIR / "voice").mkdir(parents=True, exist_ok=True)

VOICE_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


app = FastAPI(title="FindMe API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


# ---- tiny in-memory rate limiter (MVP; swap for Redis when you scale) ----
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, window: int) -> None:
    now = time.time()
    q = _hits[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many requests, try again shortly")
    q.append(now)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def hash_ip(ip: str) -> str:
    return hashlib.sha256(f"{settings.ip_hash_salt}:{ip}".encode()).hexdigest()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def get_active_card(code: str, session: AsyncSession) -> Card:
    card = (await session.execute(select(Card).where(Card.short_code == code))).scalar_one_or_none()
    if not card or not card.is_active:
        raise HTTPException(404, "Card not found or revoked")
    return card


MEDIA_PATH_RE = re.compile(r"^/media/(photos|voice)/[A-Za-z0-9_-]+\.(webp|webm|ogg|opus|m4a|mp3)$")


def base_url(request: Request) -> str:
    """Public address of this API. Derived from the request unless API_BASE_URL is set,
    so no per-environment URL config is needed behind Render/Fly/etc. proxies."""
    if settings.api_base_url:
        return settings.api_base_url.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc)
    return f"{proto}://{host.split(',')[0].strip()}"


def normalize_media(url: str | None) -> str | None:
    """Accept only media we stored ourselves. Saves a host-independent path like
    /media/photos/abc.webp so the database survives domain changes or a move to R2."""
    if not url:
        return None
    path = urlparse(url).path
    if not MEDIA_PATH_RE.match(path) or not (MEDIA_DIR / path[len("/media/"):]).is_file():
        raise HTTPException(400, "Media must be uploaded through /api/media/upload")
    return path


def absolutize(value: str | None, request: Request) -> str | None:
    if value and value.startswith("/"):
        return f"{base_url(request)}{value}"
    return value  # already absolute (older rows) or empty


def card_out(card: Card, request: Request) -> CardOut:
    return CardOut(
        short_code=card.short_code,
        title=card.title,
        lat=card.lat,
        lng=card.lng,
        landmark_text=card.landmark_text,
        gate_photo_url=absolutize(card.gate_photo_url, request),
        voice_note_url=absolutize(card.voice_note_url, request),
        is_public=card.is_public,
        created_at=card.created_at,
    )


@app.get("/api/health")
async def health():
    return {"ok": True}


# ---------------------------- cards ----------------------------
@app.post("/api/cards", response_model=CardCreated, status_code=201)
async def create_card(body: CardCreate, request: Request, session: AsyncSession = Depends(get_session)):
    rate_limit(f"create:{client_ip(request)}", limit=10, window=3600)
    data = body.model_dump()
    data["gate_photo_url"] = normalize_media(data["gate_photo_url"])
    data["voice_note_url"] = normalize_media(data["voice_note_url"])

    token = secrets.token_urlsafe(24)
    for _ in range(5):
        card = Card(
            short_code=generate(ALPHABET, 7),
            edit_token_hash=hash_token(token),
            **data,
        )
        session.add(card)
        try:
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()
    else:
        raise HTTPException(500, "Could not allocate short code")

    return CardCreated(
        id=card.id,
        short_code=card.short_code,
        share_url=f"{settings.public_base_url}/c/{card.short_code}",
        edit_token=token,
        created_at=card.created_at,
    )


@app.get("/api/cards/{code}", response_model=CardOut)
async def get_card(code: str, request: Request, session: AsyncSession = Depends(get_session)):
    return card_out(await get_active_card(code, session), request)


@app.patch("/api/cards/{code}", response_model=CardOut)
async def update_card(
    code: str,
    request: Request,
    body: CardUpdate,
    x_edit_token: str = Header(...),
    session: AsyncSession = Depends(get_session),
):
    card = (await session.execute(select(Card).where(Card.short_code == code))).scalar_one_or_none()
    if not card or not secrets.compare_digest(card.edit_token_hash, hash_token(x_edit_token)):
        raise HTTPException(403, "Invalid edit token")
    data = body.model_dump(exclude_unset=True)
    for key in ("gate_photo_url", "voice_note_url"):
        if key in data:
            data[key] = normalize_media(data[key])
    for k, v in data.items():
        setattr(card, k, v)
    await session.commit()
    return card_out(card, request)


@app.delete("/api/cards/{code}", status_code=204)
async def revoke_card(code: str, x_edit_token: str = Header(...), session: AsyncSession = Depends(get_session)):
    card = (await session.execute(select(Card).where(Card.short_code == code))).scalar_one_or_none()
    if not card or not secrets.compare_digest(card.edit_token_hash, hash_token(x_edit_token)):
        raise HTTPException(403, "Invalid edit token")
    card.is_active = False
    await session.commit()


# ---------------------------- media ----------------------------
@app.post("/api/media/upload", response_model=MediaOut, status_code=201)
async def upload_media(request: Request, kind: str, file: UploadFile = File(...)):
    rate_limit(f"upload:{client_ip(request)}", limit=20, window=3600)
    if kind not in ("photo", "voice"):
        raise HTTPException(400, "kind must be 'photo' or 'voice'")

    limit = settings.max_photo_bytes if kind == "photo" else settings.max_voice_bytes
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(413, "File too large")

    name = secrets.token_urlsafe(12)
    if kind == "photo":
        try:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
        except Exception:
            raise HTTPException(400, "Not a valid image")
        img.thumbnail((settings.photo_max_edge, settings.photo_max_edge))
        path = MEDIA_DIR / "photos" / f"{name}.webp"
        # Re-encoding also strips EXIF (including embedded GPS) from the photo.
        img.save(path, "WEBP", quality=settings.photo_quality, method=6)
        rel = f"photos/{path.name}"
    else:
        ctype = (file.content_type or "").split(";")[0].strip().lower()
        ext = VOICE_TYPES.get(ctype)
        if not ext:
            raise HTTPException(400, "Unsupported audio type")
        path = MEDIA_DIR / "voice" / f"{name}.{ext}"
        path.write_bytes(raw)
        rel = f"voice/{path.name}"

    return MediaOut(url=f"{base_url(request)}/media/{rel}", bytes=path.stat().st_size)


# ------------------------- corrections -------------------------
@app.post("/api/cards/{code}/corrections", status_code=201)
async def add_correction(
    code: str, body: CorrectionIn, request: Request, session: AsyncSession = Depends(get_session)
):
    rate_limit(f"corr:{client_ip(request)}", limit=10, window=3600)
    card = await get_active_card(code, session)
    session.add(Correction(card_id=card.id, note=body.note.strip()))
    await session.commit()
    return {"ok": True, "status": "pending_review"}


@app.get("/api/cards/{code}/corrections", response_model=list[CorrectionOut])
async def list_corrections(code: str, session: AsyncSession = Depends(get_session)):
    card = await get_active_card(code, session)
    rows = await session.execute(
        select(Correction)
        .where(Correction.card_id == card.id, Correction.is_approved.is_(True))
        .order_by(Correction.created_at.desc())
        .limit(20)
    )
    return rows.scalars().all()


# --------------------------- analytics ---------------------------
@app.post("/api/cards/{code}/view", status_code=204)
async def log_view(code: str, request: Request, session: AsyncSession = Depends(get_session)):
    card = await get_active_card(code, session)
    session.add(
        CardView(
            card_id=card.id,
            viewer_ip_hash=hash_ip(client_ip(request)),
            user_agent=(request.headers.get("user-agent") or "")[:300],
        )
    )
    await session.execute(update(Card).where(Card.id == card.id).values(view_count=Card.view_count + 1))
    await session.commit()
