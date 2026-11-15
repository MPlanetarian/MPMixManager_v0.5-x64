# Nintendo Controller Support on this Kernel

The running kernel (`7.3.4-mplanetarian-dreamworlds-ultra-v1`) was built with
`# CONFIG_HID_NINTENDO is not set`, so a Switch Pro Controller or Joy-Con cannot
appear until that driver exists. `hid-nintendo` is now installed as a module
built from the kernel's own build tree, and it matches this kernel exactly.

## What is installed

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
