from __future__ import annotations

import importlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from fastapi.testclient import TestClient
from PIL import Image


def load_app_module(data_dir: Path):
    os.environ["LABELDROP_DATA_DIR"] = str(data_dir)
    os.environ["LABELDROP_DEVICE"] = "/dev/usb/lp-test"
    sys.modules.pop("labeldrop.app", None)
    return importlib.import_module("labeldrop.app")


def png_bytes(mode: str = "RGBA") -> bytes:
    image = Image.new(mode, (2, 1), (255, 0, 0, 0) if mode == "RGBA" else "black")
    if mode == "RGBA":
        image.putpixel((1, 0), (0, 0, 0, 255))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def sideways_pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page(width=432, height=288)
    page.insert_text((48, 48), "USPS TEST LABEL", fontsize=28, rotate=90)
    page.insert_text((90, 52), "SIDEWAYS PDF FIXTURE", fontsize=18, rotate=90)
    return document.tobytes()


class LabelDropAppTests(unittest.TestCase):
    def test_normalize_png_flattens_transparency_to_white(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            appmod = load_app_module(Path(tmp))
            source = Path(tmp) / "source.png"
            target = Path(tmp) / "target.png"
            source.write_bytes(png_bytes())

            size = appmod.normalize_png(source, target)

            self.assertEqual(size, (2, 1))
            with Image.open(target) as normalized:
                self.assertEqual(normalized.mode, "RGB")
                self.assertEqual(normalized.getpixel((0, 0)), (255, 255, 255))
                self.assertEqual(normalized.getpixel((1, 0)), (0, 0, 0))

    def test_home_uploads_png_and_lists_recent_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            appmod = load_app_module(Path(tmp))
            with TestClient(appmod.app) as client:
                with patch.object(appmod, "printer_snapshot", return_value={"device": "/dev/usb/lp-test", "identify": "ok", "status": "ready", "ready": True}):
                    response = client.post(
                        "/upload",
                        files={"file": ("label.png", png_bytes(), "image/png")},
                        follow_redirects=False,
                    )

                    self.assertEqual(response.status_code, 303)
                    self.assertEqual(len(list(appmod.settings.upload_dir.glob("*.png"))), 1)
                    self.assertEqual(len(list(appmod.settings.processed_dir.glob("*.png"))), 1)
                    self.assertEqual(len(list(appmod.settings.processed_dir.glob("*.json"))), 1)

                    home = client.get("/")
                    self.assertEqual(home.status_code, 200)
                    self.assertIn("label.png", home.text)
                    self.assertIn("/processed/", home.text)
                    self.assertIn("Print test label", home.text)
                    self.assertIn("PNG to PNG", home.text)

    def test_pdf_upload_renders_first_page_and_records_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            appmod = load_app_module(Path(tmp))
            with TestClient(appmod.app) as client:
                response = client.post(
                    "/upload",
                    files={"file": ("etsy-usps-label.pdf", sideways_pdf_bytes(), "application/pdf")},
                    follow_redirects=False,
                )

                self.assertEqual(response.status_code, 303)
                self.assertEqual(len(list(appmod.settings.upload_dir.glob("*.pdf"))), 1)
                self.assertEqual(len(list(appmod.settings.processed_dir.glob("*.png"))), 1)

                job = appmod.recent_uploads()[0]
                self.assertEqual(job["original_type"], "pdf")
                self.assertEqual(job["rendered_type"], "png")
                self.assertEqual(job["rotation_applied"], 90)
                self.assertEqual(Path(job["processed_path"]).suffix.lower(), ".png")
                self.assertGreater(job["height"], job["width"])
                self.assertIn("rendered", job["dimensions"])
                self.assertIn("processed", job["dimensions"])

    def test_print_upload_records_success_without_real_printer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            appmod = load_app_module(Path(tmp))
            with TestClient(appmod.app) as client:
                client.post("/upload", files={"file": ("label.png", png_bytes(), "image/png")})
                job = appmod.recent_uploads()[0]

                with patch.object(appmod, "print_image", return_value=1234):
                    response = client.post(f"/print/{job['id']}", follow_redirects=False)

                self.assertEqual(response.status_code, 303)
                updated = appmod.load_job(job["id"])
                self.assertTrue(updated["last_print"]["ok"])
                self.assertEqual(updated["last_print"]["bytes_sent"], 1234)


if __name__ == "__main__":
    unittest.main()
