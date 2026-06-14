# LabelDrop Quickstart

This guide gets a fresh Debian machine from first boot to a hosted LAN print server for the MUNBYN ITPP130B.

The end result is:

- LabelDrop running on Debian
- hosted on port `8000`
- reachable from a phone on the same LAN
- able to accept PNG or one-page PDF labels
- able to print through `/dev/usb/lp0`

This setup is LAN-only and has no authentication.

## 1. Start with Debian

These steps assume:

- a fresh Debian 12 style install
- a local user account you can log into
- internet access on the Debian box
- the MUNBYN printer physically connected to the Debian machine

## 2. Install system packages

```bash
sudo apt-get update
sudo apt-get install -y git python3.11 python3.11-venv python3-pip usbutils
```

## 3. Clone LabelDrop

```bash
cd ~
git clone https://github.com/benleiber/labeldrop.git
cd ~/labeldrop
```

## 4. Create the Python environment

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## 5. Verify the printer is visible

Check that Debian sees the printer:

```bash
lsusb -d 5958:0130
ls -l /dev/usb/lp0
```

Expected signs:

- `lsusb` shows `5958:0130`
- `/dev/usb/lp0` exists

Check LabelDrop's printer dependency path:

```bash
munbyn-itpp130b identify
munbyn-itpp130b status --device /dev/usb/lp0
```

Expected result:

- identify finds the MUNBYN device
- status reports the device is ready and writable

## 6. Make sure your user can print

The LabelDrop user should be in the `lp` group:

```bash
sudo usermod -aG lp "$USER"
```

After running that, log out and log back in before continuing.

Then re-activate the virtualenv:

```bash
cd ~/labeldrop
. .venv/bin/activate
```

## 7. Run the test suite

```bash
python -m unittest discover -s tests
```

Expected result:

- all tests pass

## 8. Install LabelDrop as a service

Copy the checked-in `systemd` unit:

```bash
sudo cp systemd/labeldrop.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable labeldrop
sudo systemctl start labeldrop
```

Check service status:

```bash
sudo systemctl status labeldrop
```

Watch logs:

```bash
journalctl -u labeldrop -f
```

Expected result:

- service is `active (running)`
- logs show Uvicorn listening on `0.0.0.0:8000`

## 9. Find the Debian machine's LAN IP

One easy way:

```bash
hostname -I
```

Use the LAN address from that output. Example:

```text
192.168.68.82
```

## 10. Open LabelDrop from your phone

From a phone on the same Wi-Fi or LAN, open:

```text
http://YOUR-DEBIAN-IP:8000/
```

Example:

```text
http://192.168.68.82:8000/
```

## 11. First print

Recommended first-run sequence:

1. Open the page on your phone.
2. Press `Print test label`.
3. Confirm the printer feeds and prints the test label.
4. Tap `Choose File`.
5. Pick a PNG or one-page PDF shipping label.
6. Tap `Upload label`.
7. Confirm the preview appears under recent uploads.
8. Tap `Print` on that upload.

Expected result:

- the upload is accepted
- a preview image appears
- the print job succeeds
- the label prints through the MUNBYN printer

## 12. Updating later

When you want the latest repo changes:

```bash
cd ~/labeldrop
git pull --ff-only
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m unittest discover -s tests
sudo systemctl restart labeldrop
```

## Troubleshooting

### The page does not load on the phone

Check:

```bash
sudo systemctl status labeldrop
curl -I http://127.0.0.1:8000/
```

If localhost works but the phone cannot connect, check:

- the Debian machine's IP
- local firewall rules
- whether the phone is on the same LAN

### The printer is not found

Check:

```bash
lsusb -d 5958:0130
ls -l /dev/usb/lp0
```

If `/dev/usb/lp0` is missing, Debian does not currently own the printer device.

### LabelDrop says the printer is not ready

Check:

```bash
munbyn-itpp130b status --device /dev/usb/lp0
id
```

Make sure:

- your user is in the `lp` group
- the printer is connected
- the printer has labels loaded

### Upload works but the label prints wrong

Try:

- uploading the file again so a fresh processed preview is created
- checking that the preview looks upright before pressing `Print`
- using a one-page PDF or PNG only

## Related docs

- [README.md](../README.md)
- [SYSTEMD.md](./SYSTEMD.md)
