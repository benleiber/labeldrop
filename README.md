# LabelDrop

LabelDrop is a LAN-only FastAPI print server for a MUNBYN ITPP130B attached to a Debian VM through `/dev/usb/lp0`.

The app layer handles phone uploads, PNG normalization, previews, and print buttons. The reusable printer logic stays in [`munbyn-itpp130b-linux`](https://github.com/benleiber/munbyn-itpp130b-linux), which is consumed as a Python dependency.

Fresh machine setup: [docs/QUICKSTART.md](docs/QUICKSTART.md)

This is an unofficial community project. It is not affiliated with, endorsed by, or supported by MUNBYN. MUNBYN and ITPP130B are trademarks of their respective owners.

## Current Scope

- FastAPI backend with a simple HTML frontend.
- Home page with printer status, PNG/PDF upload form, and recent uploads.
- Upload storage in `data/uploads/`.
- Normalized preview/print PNGs in `data/processed/`.
- Print uploaded PNGs and rendered PDFs through the `munbyn-itpp130b` toolkit.
- Print a basic test label.
- Basic application logging.
- Optional permanent service via `systemd`.

Out of scope: marketplace integrations, accounts, internet exposure, Docker, CUPS, a database, auto-crop, and advanced rotation.

## Debian Setup

Install system packages:

```bash
sudo apt-get update
sudo apt-get install -y git python3.11 python3.11-venv python3-pip usbutils
```

Clone and install LabelDrop:

```bash
git clone <this-repo-url> labeldrop
cd labeldrop
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Confirm the printer is visible and writable:

```bash
lsusb -d 5958:0130
ls -l /dev/usb/lp0
munbyn-itpp130b identify
munbyn-itpp130b status --device /dev/usb/lp0
```

The user running LabelDrop should be in the `lp` group. If needed:

```bash
sudo usermod -aG lp "$USER"
```

Start a fresh login session after changing groups.

## Local Development

Run the server:

```bash
. .venv/bin/activate
uvicorn labeldrop.app:app --host 0.0.0.0 --port 8000 --reload
```

From a phone on the same LAN, open:

```text
http://192.168.68.82:8000/
```

Useful environment variables:

```bash
export LABELDROP_DEVICE=/dev/usb/lp0
export LABELDROP_DATA_DIR=data
export LABELDROP_HOST=0.0.0.0
export LABELDROP_PORT=8000
export LABELDROP_LOG_LEVEL=INFO
```

The console log records startup, upload saves, successful prints, and print failures.

Run the minimal test suite:

```bash
python -m unittest discover -s tests
```

## systemd Service

Install the unit:

```bash
sudo cp systemd/labeldrop.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable labeldrop
sudo systemctl start labeldrop
```

Check service state and logs:

```bash
sudo systemctl status labeldrop
journalctl -u labeldrop -f
```

The checked-in unit defaults to:

- `User=ben`
- `WorkingDirectory=/home/ben/labeldrop`
- `LABELDROP_DEVICE=/dev/usb/lp0`
- `LABELDROP_DATA_DIR=data`
- `LABELDROP_HOST=0.0.0.0`
- `LABELDROP_PORT=8000`

If you deploy under a different local user or path, update the unit file before copying it into `/etc/systemd/system/`.

Full notes: [docs/SYSTEMD.md](docs/SYSTEMD.md)

## Workflow

1. Visit the local page from a LAN browser.
2. Upload a PNG or PDF label file.
3. Review the generated preview in recent uploads.
4. Press `Print` on that upload.
5. LabelDrop generates TSPL with the driver toolkit and writes it to `/dev/usb/lp0`.

PDF uploads render page 1, flatten onto white, rotate landscape pages to portrait, and save a processed PNG preview before printing.

`Print test label` sends a simple text label and is useful for checking transport before trying an uploaded file.

## Notes

- This prototype has no authentication and should stay on a trusted local network.
- PNG uploads flatten transparency onto white and preserve their image size.
- PDF uploads render the first page to PNG, rotate landscape output to portrait, and fit onto a 4x6 portrait canvas.
- Application state is filesystem-only JSON metadata beside processed images; there is no database.
