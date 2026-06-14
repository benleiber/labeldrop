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

import fitz
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps, UnidentifiedImageError

from munbyn_itpp130b import Label, TSPLGenerator, USBTransport
from munbyn_itpp130b.discovery import identify
from munbyn_itpp130b.transport import status as printer_status

LOGGER = logging.getLogger("labeldrop")
LABEL_4X6 = Label.shipping_4x6()
LABEL_WIDTH_DOTS = int(round(LABEL_4X6.width_mm * LABEL_4X6.dots_per_mm))
LABEL_HEIGHT_DOTS = int(round(LABEL_4X6.height_mm * LABEL_4X6.dots_per_mm))
PROCESSING_VERSION = 2


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


def flatten_to_white(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    base = Image.new("RGBA", rgba.size, "white")
    base.alpha_composite(rgba)
    return base.convert("RGB")


def crop_white_margins(image: Image.Image, threshold: int = 245, padding: int = 24) -> tuple[Image.Image, dict[str, int]]:
    grayscale = image.convert("L")
    binary = grayscale.point(lambda value: 255 if value < threshold else 0, mode="1")
    bbox = binary.getbbox()
    if bbox is None:
        return image, {"left": 0, "top": 0, "right": image.width, "bottom": image.height}

    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(image.width, bbox[2] + padding)
    bottom = min(image.height, bbox[3] + padding)
    return image.crop((left, top, right, bottom)), {"left": left, "top": top, "right": right, "bottom": bottom}


def normalize_label_image(image: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    flattened = flatten_to_white(image)
    cropped, crop_box = crop_white_margins(flattened)
    rotation_applied = 0
    if cropped.width > cropped.height:
        cropped = cropped.rotate(90, expand=True)
        rotation_applied = 90
    return cropped, {
        "rotation_applied": rotation_applied,
        "crop_box": crop_box,
        "cropped_dimensions": {"width": cropped.width, "height": cropped.height},
    }


def normalize_png(source: Path, target: Path) -> dict[str, Any]:
    try:
        with Image.open(source) as img:
            img.verify()
        with Image.open(source) as img:
            img.load()
            if img.format != "PNG":
                raise ValueError("Uploaded file is not a PNG")
            normalized, details = normalize_label_image(img)
            normalized.save(target, "PNG", optimize=True)
            return {
                "original_dimensions": {"width": img.width, "height": img.height},
                "processed_dimensions": {"width": normalized.width, "height": normalized.height},
                "rotation_applied": details["rotation_applied"],
                "crop_box": details["crop_box"],
            }
    except (UnidentifiedImageError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def fit_to_label_canvas(image: Image.Image) -> Image.Image:
    contained = ImageOps.contain(image, (LABEL_WIDTH_DOTS, LABEL_HEIGHT_DOTS), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (LABEL_WIDTH_DOTS, LABEL_HEIGHT_DOTS), "white")
    offset = (
        (LABEL_WIDTH_DOTS - contained.width) // 2,
        (LABEL_HEIGHT_DOTS - contained.height) // 2,
    )
    canvas.paste(contained, offset)
    return canvas


def render_pdf_first_page(source: Path, target: Path) -> dict[str, Any]:
    try:
        with fitz.open(source) as document:
            if document.page_count < 1:
                raise ValueError("Uploaded PDF has no pages")
            page = document.load_page(0)
            pixmap = page.get_pixmap(dpi=203, alpha=True)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"PDF render failed: {exc}") from exc

    mode = "RGBA" if pixmap.alpha else "RGB"
    rendered = Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples)
    normalized_label, details = normalize_label_image(rendered)
    normalized = fit_to_label_canvas(normalized_label)
    normalized.save(target, "PNG", optimize=True)
    return {
        "rendered_dimensions": {"width": pixmap.width, "height": pixmap.height},
        "cropped_dimensions": details["cropped_dimensions"],
        "processed_dimensions": {"width": normalized.width, "height": normalized.height},
        "rotation_applied": details["rotation_applied"],
        "crop_box": details["crop_box"],
    }


def process_upload(upload_path: Path, processed_path: Path, original_type: str) -> dict[str, Any]:
    if original_type == "png":
        png_result = normalize_png(upload_path, processed_path)
        return {
            "original_type": "png",
            "rendered_type": "png",
            "rotation_applied": png_result["rotation_applied"],
            "crop_box": png_result["crop_box"],
            "dimensions": {
                "original": png_result["original_dimensions"],
                "processed": png_result["processed_dimensions"],
            },
        }
    if original_type == "pdf":
        pdf_result = render_pdf_first_page(upload_path, processed_path)
        return {
            "original_type": "pdf",
            "rendered_type": "png",
            "rotation_applied": pdf_result["rotation_applied"],
            "crop_box": pdf_result["crop_box"],
            "dimensions": {
                "rendered": pdf_result["rendered_dimensions"],
                "cropped": pdf_result["cropped_dimensions"],
                "processed": pdf_result["processed_dimensions"],
            },
        }
    raise HTTPException(status_code=400, detail="Unsupported upload type")


def apply_processing_to_job(job: dict[str, Any]) -> dict[str, Any]:
    processing = process_upload(Path(job["upload_path"]), Path(job["processed_path"]), job["original_type"])
    job.update(
        {
            "rendered_type": processing["rendered_type"],
            "rotation_applied": processing["rotation_applied"],
            "crop_box": processing["crop_box"],
            "width": processing["dimensions"]["processed"]["width"],
            "height": processing["dimensions"]["processed"]["height"],
            "dimensions": processing["dimensions"],
            "processing_version": PROCESSING_VERSION,
        }
    )
    return job


def needs_reprocessing(job: dict[str, Any]) -> bool:
    if job.get("processing_version") != PROCESSING_VERSION:
        return True
    if job.get("crop_box") is None:
        return True
    dimensions = job.get("dimensions", {})
    if job.get("original_type") == "pdf" and "cropped" not in dimensions:
        return True
    return False


def print_image(path: Path) -> int:
    payload = TSPLGenerator(LABEL_4X6).from_image(path, invert=True)
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
    job_id = uuid.uuid4().hex
    original_name = Path(file.filename or "label.png").name
    suffix = Path(original_name).suffix.lower()
    type_by_suffix = {".png": "png", ".pdf": "pdf"}
    original_type = type_by_suffix.get(suffix)
    if original_type is None:
        return redirect_home(error="Please upload a PNG or PDF file.")

    upload_path = settings.upload_dir / f"{job_id}{suffix}"
    processed_path = settings.processed_dir / f"{job_id}.png"

    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        return redirect_home(error=f"{original_type.upper()} is too large.")

    upload_path.write_bytes(data)
    try:
        processing = process_upload(upload_path, processed_path, original_type)
    except HTTPException as exc:
        upload_path.unlink(missing_ok=True)
        processed_path.unlink(missing_ok=True)
        return redirect_home(error=str(exc.detail))

    job = {
        "id": job_id,
        "created_at": utc_now(),
        "original_name": original_name,
        "original_type": processing["original_type"],
        "processing_version": PROCESSING_VERSION,
        "rendered_type": processing["rendered_type"],
        "rotation_applied": processing["rotation_applied"],
        "crop_box": processing["crop_box"],
        "upload_path": str(upload_path),
        "processed_path": str(processed_path),
        "preview_url": f"/processed/{job_id}.png",
        "width": processing["dimensions"]["processed"]["width"],
        "height": processing["dimensions"]["processed"]["height"],
        "dimensions": processing["dimensions"],
        "last_print": None,
    }
    save_job(job)
    LOGGER.info(
        "Saved upload id=%s name=%s type=%s processed=%sx%s rotation=%s",
        job_id,
        original_name,
        processing["original_type"],
        job["width"],
        job["height"],
        processing["rotation_applied"],
    )
    return redirect_home(message="Upload ready to print.")


@app.post("/print/{job_id}")
def print_upload(job_id: str) -> RedirectResponse:
    job = load_job(job_id)
    try:
        if needs_reprocessing(job):
            LOGGER.info("Reprocessing legacy upload id=%s before print", job_id)
            job = apply_processing_to_job(job)
            save_job(job)
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
        payload = TSPLGenerator(LABEL_4X6).text_label("LabelDrop test")
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
