from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, UnidentifiedImageError

from munbyn_itpp130b import Label, TSPLGenerator, USBTransport
from munbyn_itpp130b.discovery import identify
from munbyn_itpp130b.transport import status as printer_status

LOGGER = logging.getLogger("labeldrop")


@dataclass(frozen=True)
class Settings:
    device: str = os.environ.get("LABELDROP_DEVICE", "/dev/usb/lp0")
    host: str = os.environ.get("LABELDROP_HOST", "0.0.0.0")
    port: int = int(os.environ.get("LABELDROP_PORT", "8000"))
    data_dir: Path = Path(os.environ.get("LABELDROP_DATA_DIR", "data"))
    max_upload_bytes: int = int(os.environ.get("LABELDROP_MAX_UPLOAD_BYTES", str(16 * 1024 * 1024)))

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"


settings = Settings()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LABELDROP_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app_: FastAPI):
    configure_logging()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info("LabelDrop started with device=%s data_dir=%s", settings.device, settings.data_dir)
    yield


app = FastAPI(title="LabelDrop", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.mount("/processed", StaticFiles(directory=str(settings.processed_dir), check_dir=False), name="processed")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def metadata_path(job_id: str) -> Path:
    return settings.processed_dir / f"{job_id}.json"


def load_job(job_id: str) -> dict[str, Any]:
    path = metadata_path(job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Upload not found")
    return json.loads(path.read_text(encoding="utf-8"))


def save_job(job: dict[str, Any]) -> None:
    metadata_path(job["id"]).write_text(json.dumps(job, indent=2), encoding="utf-8")


def redirect_home(**params: str) -> RedirectResponse:
    return RedirectResponse(f"/?{urlencode(params)}", status_code=303)


def recent_uploads(limit: int = 10) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for path in settings.processed_dir.glob("*.json"):
        try:
            jobs.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("Skipping unreadable metadata file: %s", path)
    return sorted(jobs, key=lambda item: item.get("created_at", ""), reverse=True)[:limit]


def printer_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "device": settings.device,
        "identify": "unknown",
        "status": "unknown",
        "ready": False,
    }
    try:
        snapshot["identify"] = identify().human_summary()
    except Exception as exc:  # pragma: no cover - hardware-dependent
        snapshot["identify"] = f"identify failed: {exc}"
    try:
        status = printer_status(settings.device)
        snapshot["status"] = status.human_summary()
        snapshot["ready"] = bool(status.ready)
    except Exception as exc:  # pragma: no cover - hardware-dependent
        snapshot["status"] = f"status failed: {exc}"
    return snapshot


def normalize_png(source: Path, target: Path) -> tuple[int, int]:
    try:
        with Image.open(source) as img:
            img.verify()
        with Image.open(source) as img:
            img.load()
            if img.format != "PNG":
                raise ValueError("Uploaded file is not a PNG")
            normalized = Image.new("RGBA", img.size, "white")
            if img.mode == "RGBA":
                normalized.alpha_composite(img.convert("RGBA"))
            else:
                normalized.alpha_composite(img.convert("RGBA"))
            normalized.convert("RGB").save(target, "PNG", optimize=True)
            return normalized.size
    except (UnidentifiedImageError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def print_image(path: Path) -> int:
    payload = TSPLGenerator(Label.shipping_4x6()).from_image(path, invert=True)
    return USBTransport(settings.device).send(payload)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, message: str | None = None, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "printer": printer_snapshot(),
            "uploads": recent_uploads(),
            "message": message,
            "error": error,
        },
    )


@app.post("/upload")
async def upload_label(file: UploadFile = File(...)) -> RedirectResponse:
    if file.content_type not in {"image/png", "application/octet-stream"}:
        return redirect_home(error="Please upload a PNG file.")

    job_id = uuid.uuid4().hex
    original_name = Path(file.filename or "label.png").name
    upload_path = settings.upload_dir / f"{job_id}.png"
    processed_path = settings.processed_dir / f"{job_id}.png"

    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        return redirect_home(error="PNG is too large.")

    upload_path.write_bytes(data)
    try:
        width, height = normalize_png(upload_path, processed_path)
    except HTTPException as exc:
        upload_path.unlink(missing_ok=True)
        return redirect_home(error=str(exc.detail))

    job = {
        "id": job_id,
        "created_at": utc_now(),
        "original_name": original_name,
        "upload_path": str(upload_path),
        "processed_path": str(processed_path),
        "preview_url": f"/processed/{job_id}.png",
        "width": width,
        "height": height,
        "last_print": None,
    }
    save_job(job)
    LOGGER.info("Saved upload id=%s name=%s size=%sx%s", job_id, original_name, width, height)
    return redirect_home(message="Upload ready to print.")


@app.post("/print/{job_id}")
def print_upload(job_id: str) -> RedirectResponse:
    job = load_job(job_id)
    try:
        sent = print_image(Path(job["processed_path"]))
        job["last_print"] = {"at": utc_now(), "ok": True, "bytes_sent": sent}
        save_job(job)
        LOGGER.info("Printed upload id=%s bytes=%s", job_id, sent)
        return redirect_home(message="Label sent to printer.")
    except Exception as exc:  # pragma: no cover - hardware-dependent
        job["last_print"] = {"at": utc_now(), "ok": False, "error": str(exc)}
        save_job(job)
        LOGGER.exception("Print failed for upload id=%s", job_id)
        return redirect_home(error=f"Print failed: {exc}")


@app.post("/print-test")
def print_test_label() -> RedirectResponse:
    try:
        payload = TSPLGenerator(Label.shipping_4x6()).text_label("LabelDrop test")
        sent = USBTransport(settings.device).send(payload)
        LOGGER.info("Printed test label bytes=%s", sent)
        return redirect_home(message="Test label sent to printer.")
    except Exception as exc:  # pragma: no cover - hardware-dependent
        LOGGER.exception("Test label print failed")
        return redirect_home(error=f"Test label failed: {exc}")


def main() -> None:
    import uvicorn

    uvicorn.run("labeldrop.app:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
