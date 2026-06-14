# LabelDrop systemd setup

LabelDrop can run as a permanent local service under `systemd`.

## Unit file

The repo includes [`systemd/labeldrop.service`](../systemd/labeldrop.service).

Before installing, confirm these fields match the deployed machine:

- `User=ben`
- `WorkingDirectory=/home/ben/labeldrop`
- `ExecStart=/home/ben/labeldrop/.venv/bin/uvicorn ...`

If the service should run as another local user, update `User`, `WorkingDirectory`, and `ExecStart` to point at that user's deployed repo and virtualenv.

## Install

```bash
sudo cp systemd/labeldrop.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable labeldrop
sudo systemctl start labeldrop
```

## Verify

```bash
sudo systemctl status labeldrop
journalctl -u labeldrop -f
```

The service listens on `0.0.0.0:8000` and uses these environment variables:

- `LABELDROP_DEVICE=/dev/usb/lp0`
- `LABELDROP_DATA_DIR=data`
- `LABELDROP_HOST=0.0.0.0`
- `LABELDROP_PORT=8000`
