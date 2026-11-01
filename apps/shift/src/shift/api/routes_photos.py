"""The photo routes (SHF-7): upload a receipt before its event, read it only from the approver side.

Two rules shape the whole file. A photo is written by the person recording the event, so the upload
takes the same `events.append` guard (and therefore the same CSRF proof) as the event itself; it is
read by the manager and the approvers — the people who decide the record — through the
`shift_event.approve` permission, and by nobody else. The path segment is only ever a SHA-256 hex
digest, so no request can name a file outside the store.
"""

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from shift.api.deps import require
from shift.domain.permissions import Permission
from shift.services.auth import Principal
from shift.services.photos import MAX_PHOTO_BYTES, PhotoError, PhotoRejected, PhotoStore, is_photo_hash

router = APIRouter(prefix="/api/v1", tags=["photos"])

PHOTO_NOT_JPEG = "عکس رسید باید JPEG باشد."
PHOTO_TOO_LARGE = "حجم عکس رسید بیش از حد مجاز است."
PHOTO_HASH_MISMATCH = "هش عکس با محتوایش نمی‌خواند."
PHOTO_NOT_FOUND = "عکس پیدا نشد."
PHOTO_RATE = "تعداد عکس‌های فرستاده‌شده زیاد است؛ چند دقیقه بعد دوباره امتحان کنید."
NO_PHOTO = "نقش شما اجازهٔ دیدن عکس را ندارد."


class PhotoOut(BaseModel):
    """What a successful upload fixed: the content hash the event will name, and its size."""

    sha256: str
    bytes: int


def get_photo_store(request: Request) -> PhotoStore:
    """The store created in the lifespan; routes never build their own folder."""
    return request.app.state.photos


def _checked_hash(sha256: str) -> str:
    """A path segment that is not a photo hash is a 404, never a path."""
    if not is_photo_hash(sha256):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PHOTO_NOT_FOUND)
    return sha256


@router.put(
    "/photos/{sha256}",
    response_model=PhotoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Store one receipt photo under its content hash",
)
async def put_photo(
    sha256: str,
    request: Request,
    principal: Annotated[Principal, Depends(require(Permission.EVENTS_APPEND))],
    store: Annotated[PhotoStore, Depends(get_photo_store)],
) -> PhotoOut:
    """Store the JPEG bytes when they hash to the name the phone claims; store nothing else.

    The phone downscales the photo and sends it before the event that names it, so the size cap is a
    guard against a client sending a whole camera dump, not a quality setting.
    """
    _checked_hash(sha256)
    if not request.app.state.photo_throttle.hit(principal.staff_id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=PHOTO_RATE)
    if (request.headers.get("content-type") or "").split(";")[0].strip().lower() != "image/jpeg":
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=PHOTO_NOT_JPEG)
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid Content-Length") from exc
        if length > MAX_PHOTO_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=PHOTO_TOO_LARGE)
    body = await request.body()
    if len(body) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=PHOTO_TOO_LARGE)
    if hashlib.sha256(body).hexdigest() != sha256:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=PHOTO_HASH_MISMATCH)
    try:
        store.save(body)
    except PhotoError as exc:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=PHOTO_TOO_LARGE) from exc
    return PhotoOut(sha256=sha256, bytes=len(body))


@router.get(
    "/photos/{sha256}",
    response_class=FileResponse,
    summary="Serve one receipt photo to the manager and the approvers",
)
def get_photo(
    sha256: str,
    _approver: Annotated[Principal, Depends(require(Permission.SHIFT_EVENT_APPROVE))],
    store: Annotated[PhotoStore, Depends(get_photo_store)],
) -> FileResponse:
    """The JPEG behind one hash, for the people who decide the record and for nobody else."""
    try:
        path = store.open_path(_checked_hash(sha256))
    except PhotoRejected as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PHOTO_NOT_FOUND) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
