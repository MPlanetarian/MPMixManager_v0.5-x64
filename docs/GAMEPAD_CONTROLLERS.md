# Controller Support on this Kernel

## What the running kernel can do

The Manager's bridge is controller-agnostic, but each pad needs a kernel driver.
On the kernel this machine is currently booted into (`7.3.0-mplanetarian-dreamworlds-ultra-v1`):

| Pad | Driver | State on 7.3.0 |
|---|---|---|
| Xbox (any) | `xpad` | Ready |
| Steam Controller | `hid_steam` | Ready |
| Switch Pro / Joy-Con | `hid_nintendo` | Built as a module for 7.3.4 only |
| DualShock 4 / DualSense | `hid_playstation` | Built as a module for 7.3.4 only |

`7.3.4-mplanetarian-dreamworlds-ultra-v1` was built with
`CONFIG_HID_NINTENDO=m`, `CONFIG_HID_PLAYSTATION=m`, `CONFIG_HID_SONY=m` and
`CONFIG_HID_STEAM=m`, and those modules are installed for it. Boot that entry
and all four families are available. On 7.3.0 only Xbox and Steam pads appear.

Boot the 7.3.4 entry from the boot menu, or rebuild the drivers for 7.3.0 the
same way described below.

## What is installed for 7.3.4

| Item | Value |
|---|---|
| Module | `/lib/modules/7.3.4-mplanetarian-dreamworlds-ultra-v1/kernel/drivers/hid/hid-nintendo.ko` |
| Source tree | `$HOME/kernel-builds-734-trimmed` |
| Config line | `CONFIG_HID_NINTENDO=m` (was `# CONFIG_HID_NINTENDO is not set`) |
| Registered pads | Switch Pro Controller, Joy-Con (L/R), NES/SNES/N64 pads, over USB and Bluetooth |

## Check it is working

```bash
lsmod | grep hid_nintendo            # loaded?
modinfo -n hid_nintendo              # where the module lives
sudo dmesg | grep -i nintendo        # what the driver saw
./scripts/gamepad_bridge.py --doctor # does the Manager see the pad?
```

A Switch Pro Controller over USB shows up as `Nintendo Co., Ltd. Pro Controller`.
The driver maps A to `BTN_EAST`, B to `BTN_SOUTH`, X to `BTN_NORTH` and Y to
`BTN_WEST`, which is why the Manager's Nintendo layout confirms with A and goes
back with B.

## If it stops working after an OS or kernel update

The module is written into `/usr/lib/modules/...`, which is read-only on this
Bazzite (ostree) system. An OS update replaces that tree, and the module goes
with it. Rebuild it the same way:

```bash
cd $HOME/kernel-builds-734-trimmed   # must match `uname -r`
./scripts/config --module HID_NINTENDO
make -j"$(nproc)" olddefconfig
make -j"$(nproc)" M=drivers/hid CONFIG_HID_NINTENDO=m modules
sudo install -m 644 drivers/hid/hid-nintendo.ko \
  "/lib/modules/$(uname -r)/kernel/drivers/hid/hid-nintendo.ko"
sudo depmod "$(uname -r)"
sudo modprobe hid_nintendo
```

Then confirm the SELinux label is `modules_object_t`, or udev will not autoload
it at boot:

```bash
ls -Z "/lib/modules/$(uname -r)/kernel/drivers/hid/hid-nintendo.ko"
sudo restorecon -v "/lib/modules/$(uname -r)/kernel/drivers/hid/hid-nintendo.ko"
```

If a future kernel is built from source, set `CONFIG_HID_NINTENDO=m` in its
`.config` before building, and the module is produced with the rest of the tree.

## Steam Controller

A Steam Controller plugged in over USB is picked up by `hid_steam` (already
built into this kernel), but it starts in *lizard mode*: it presents itself as a
keyboard and a mouse instead of a pad, so it never appears in `--list` and its
trackpads move the pointer around the menus.

The Manager fixes this by itself. `gamepad_bridge.py` opens the pad's gamepad
node, and `hid_steam` disables lizard mode whenever that node is open, so the
pad drives the menus without Steam running. Nothing needs to be configured.

| Steam input | Key the Manager reads |
|---|---|
| Left pad, or the D-pad | UP / DOWN / LEFT / RIGHT (repeats while held) |
| A | ENTER |
| B | ESC |
| X | SPACE (play or pause) |
| Left / right shoulder | PAGE_UP / PAGE_DOWN |
| Left / right trigger | `CHAR:[` / `CHAR:n` (previous / next track) |

Two details differ from the other pads, and the bridge handles both:

- The left pad and D-pad arrive as `BTN_DPAD_*` key presses, not as hat axes.
- The triggers arrive as `ABS_HAT2X` / `ABS_HAT2Y` (**not** `ABS_HAT1*`, which is
  what earlier versions of the bridge listened for), and the left pad arrives on
  `ABS_HAT0X` / `ABS_HAT0Y` as a full `-32767..32767` axis rather than `-1..1`.

If the pad is silent, `./scripts/gamepad_bridge.py --doctor` now says whether it
is in lizard mode rather than claiming the driver is missing. Hold the Steam
button and press **Y** to switch the pad on if it has gone to sleep.

## Bluetooth pads need CONFIG_BT_HIDP

This kernel has `# CONFIG_BT_HIDP is not set`, so *no* controller can connect
over Bluetooth whatever driver it needs: `bluetoothctl` reports
`br-connection-create-socket`, and the pad never reaches `hid_steam`,
`hid-nintendo` or `hid-playstation`. USB works. For a wireless Switch Pro
Controller or DualShock 4, either use its USB cable or boot a kernel built with
`CONFIG_BT_HIDP=m`.

## PlayStation controllers needed the same fix

This kernel was also built with `CONFIG_HID_PLAYSTATION` and `CONFIG_HID_SONY` off.
A DualShock 4 plugged in was bound by `hid-generic`, which registers a joystick
node but sends no button events, so the pad looked connected and did nothing.
`hid-playstation` and `hid-sony` are now built and installed the same way as
`hid-nintendo`. After they load, the kernel rebinds the pad and logs
`playstation ...: Registered DualShock4 controller`.

If a PlayStation pad ever looks connected but silent, check with:

```bash
lsmod | grep -E 'hid_playstation|hid_sony'
sudo dmesg | grep -i playstation | tail -n 3
```

## Rebuild everything at once

```bash
cd $HOME/kernel-builds-734-trimmed
./scripts/config --module HID_NINTENDO --module HID_PLAYSTATION --module HID_SONY
make -j"$(nproc)" olddefconfig
make -j"$(nproc)" M=drivers/hid CONFIG_HID_NINTENDO=m CONFIG_HID_PLAYSTATION=m CONFIG_HID_SONY=m modules
for module in hid-nintendo hid-playstation hid-sony; do
  sudo install -m 644 "drivers/hid/${module}.ko" \
    "/lib/modules/$(uname -r)/kernel/drivers/hid/${module}.ko"
done
sudo depmod "$(uname -r)"
sudo modprobe hid_nintendo; sudo modprobe hid_playstation; sudo modprobe hid_sony
```

## Notes

- The module is unsigned, so `dmesg` reports `module verification failed` and the
  kernel is marked tainted. Signature enforcement is off, so the driver loads and
  works.
- If the pad still does not appear after the driver loads, it is connected to a
  different host. A controller keeps its light on for the device it is paired to,
  which is not always this computer.
