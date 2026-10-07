from datetime import datetime

from pydantic import BaseModel, Field


class CardCreate(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    landmark_text: str | None = Field(default=None, max_length=1000)
    gate_photo_url: str | None = None
    voice_note_url: str | None = None
    is_public: bool = False


class CardUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    landmark_text: str | None = Field(default=None, max_length=1000)
    gate_photo_url: str | None = None
    voice_note_url: str | None = None
    is_public: bool | None = None
    is_active: bool | None = None


class CardCreated(BaseModel):
    id: str
    short_code: str
    share_url: str
    edit_token: str  # shown once; store in the browser
    created_at: datetime


class CardOut(BaseModel):
    short_code: str
    title: str | None
    lat: float
    lng: float
    landmark_text: str | None
    gate_photo_url: str | None
    voice_note_url: str | None
    is_public: bool
    created_at: datetime


class CorrectionIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


class CorrectionOut(BaseModel):
    id: str
    note: str
    created_at: datetime


class MediaOut(BaseModel):
    url: str
    bytes: int
