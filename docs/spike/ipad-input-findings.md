# iPad input findings

Spike: what finger and Apple Pencil input reaches the FreeShape Qt app when it is streamed to an iPad browser through Selkies. The spike closed early. The browser side was measured, the Selkies side was read from code, and what Qt receives is a prediction that was never measured.

## Summary

1. What the iPad browser reports: pen and finger are told apart by `pointerType`; pen pressure is graded (highest seen 0.41), finger pressure is always 0, two fingers are tracked together and also fire gesture events, and there is no pen hover. Evidence: "Baseline".
2. What Selkies sends: finger, pen and mouse all become the same mouse message (`m,x,y,mask,0,seq`; in trackpad mode `m2` with relative deltas), in both touch modes, with no touch or pen message. Evidence: "Selkies input path".
3. What Qt receives: mouse only, in both touch modes, with no `QTouchEvent`, `QTabletEvent`, pressure or hover. This is a prediction from reading the Selkies code, not a measurement; the Qt probe was never run under Selkies. Evidence: "Selkies input path", "Results".
4. Whether fingers and pen work together: no, because the browser never reports them at the same time, even when a finger is dragged while the pen is down. Evidence: "Baseline".
5. Whether a side channel would carry raw events to Qt: not tested. Reading the code (I1.2) shows it is feasible, since the browser has pressure and tilt, the server could accept a new message type and the app could read it directly, but it is the input half of a browser client, so it was not pursued. Evidence: "Selkies input path" (candidate patch points), "Recommendation and decision".

One field observation, not confirmed: loading real FreeShape through Selkies on the iPad on 15 Nov, the pen was treated as text input instead of navigating. The likely cause is iPadOS Scribble writing into the hidden text field Selkies keeps for its on-screen keyboard.

## Baseline

Measured in the I1.1 browser probe on 2026-11-15: first-generation Apple Pencil, Chrome on iPadOS 18.6.2 (which runs on WebKit). Safari itself was not tested. Raw log: `docs/spike/ipad-input-logs/i11-browser-baseline.jsonl`. Reproduce the per-gesture summary with:

```
python3 scripts/spike/ipad_input/browser_probe/probe_server.py summary --log docs/spike/ipad-input-logs/i11-browser-baseline.jsonl
```

| Gesture | Pointer types | Most pointers down at once | Touches in one TouchEvent | Pressure | Hover (moves with nothing down) | Gesture events |
|---|---|---|---|---|---|---|
| Finger tap | touch | 1 | 1 | touch 0 | none | none |
| Finger drag | touch | 1 | 1 | touch 0 | none | none |
| Two-finger drag | touch | 2 | 2 | touch 0 | none | gesturestart, gesturechange, gestureend |
| Pinch | touch | 2 | 2 | touch 0 | none | gesturestart, gesturechange, gestureend |
| Pen tap | pen, touch | 1 | 1 | pen 0 to 0.18 | none | none |
| Pen drag | pen | 1 | 1 | pen 0 to 0.30 | none | none |
| Pen above glass | pen | 1 | 1 | pen 0.001 to 0.08 | none | none |
| Pen down plus finger drag | pen, touch | 1 | 1 | pen 0.01 to 0.41, touch 0 | none | none |

Findings:

- Pen and finger are told apart by `pointerType` (`pen` or `touch`). Touch Events also label the pen `stylus` and the finger `direct`.
- Pen pressure is graded: 46 to 152 distinct values per gesture. The highest seen is 0.41. The full scale was not tested, so do not assume 1.0 is reachable.
- Finger pressure is always 0.
- Two fingers are tracked as two pointers in one TouchEvent, and they also fire `gesturestart`, `gesturechange` and `gestureend`.
- There is no pen hover. Pen above glass produced no moves with nothing down.
- Pen and finger are never reported together. In "pen down plus finger drag" the most pointers down at once is 1, although the user did drag a finger while the pen was down.
- WebKit gives a finger implicit pointer capture (`gotpointercapture` and `lostpointercapture` appear on finger gestures) but not the pen. The probe never called `setPointerCapture`, so an explicit call for the pen is untested.
- The "pen tap" row includes some touch pointers alongside the pen. The cause was not investigated.

## Selkies input path

Read-only study of the running `freeshape` container (Selkies 2.2.1, pixelflux 2.2.3, labwc 0.9.7, Wayland session) on 2026-11-15. Client code is `/usr/share/selkies/web/selkies-core.js` (minified; function names below are the class methods, which survive minification). Server code is `/lsiopy/lib/python3.14/site-packages/selkies/`. Nothing was run in the container beyond `cat`, `ls`, `grep`, `readlink`, `ps` and reads of `/proc`.

### Summary

- Every input device collapses to the Selkies mouse message `m,x,y,buttonMask,0,seq` (absolute) or `m2,dx,dy,buttonMask,0,seq` (relative). There is no touch message and no pen message.
- Pen pressure, tilt, hover and finger contact points are never sent. The server has no API to inject touch or tablet events.
- Compositor-side, labwc sees one pointer and one keyboard, in every touch mode.
- Prediction for what Qt receives: mouse only, in both touch modes. Not measured; the hand tests (I1.5) were withdrawn.

### Transport

The container runs in WebSockets mode (`settings.py` default `mode=websockets`; no WebRTC variable is set, so `init-selkies-config` leaves `SELKIES_MODE` unset and dual mode false). The client sends each message as one text frame on the WebSocket: `cc` (the `initializeInput` closure) calls `u.send(msg)`. The server loop in `websockets_mode.py` (around line 6248) calls `input_handler.on_message(message, display_id, conn_id=id(websocket))`. In WebRTC mode the client sends motion (`sendMotion`) on an unordered `pointer` data channel (`webrtc_engine.py:2658`) and everything else on the ordered channel; both reach the same `on_message` (`webrtc_mode.py:1815`). Both transports share one handler, so the findings below hold for both.

### Wire messages

Wire format is comma-separated text. `seq` is a per-connection counter appended by `_sendPointer`.

| Device | Message | Sender (client) | Receiver (server) |
|---|---|---|---|
| Mouse | `m,x,y,mask,0,seq`; wheel is a mask bit pulse with a non-zero fourth field | `_mouseButtonMovement` builds it, `_sendPointer` sends it, `_queueCoalescedMouseMove` / `_flushCoalescedMouseMove` batch motion. Listeners: `mousedown` on the element, `mousemove`/`mouseup` on window, `pointerrawupdate` on window | `input_handler.py` `_dispatch_message`, branch `msg_type in ["m","m2"]` (~line 9276), then `send_x11_mouse` (line 6568) |
| Finger, direct touch mode | Also `m,x,y,mask,0,seq`. Mask bit 0 is the left button (held while one finger drags); bit 2 is the right button (long press, 750 ms) | `_handleTouchEvent`, `_calculateTouchCoordinates`, `_sendMouseState` | same as mouse |
| Finger, trackpad mode | `m2,dx,dy,mask,0,seq`. Taps are button pulses with `dx=dy=0`. Also `_pointer_echo,1` to ask the server to echo the pointer position, which the page draws | `_handleTrackpadEvent`, `_trackpadMove`, `_trackpadReleaseButton` | `_dispatch_message` `m2` branch; `_set_pointer_echo` for the echo |
| Two fingers | Wheel pulses (mask bits 3/4 vertical, 6/7 horizontal with a magnitude) or, on Wayland, `sf,dx,dy` and `sfe` (finger scroll). Pinch becomes Ctrl+wheel notches via `_sendZoomNotches` | `_twoFingerStart`, `_twoFingerMove`, `_drainScrollNotches`, `_fingerScroll`, `_endFingerScroll` | `m` branch; `sf`/`sfe` branch (~line 9306) calling `_wl_finger_scroll` and pixelflux `inject_finger_scroll` |
| Pen | `m,x,y,mask,0,seq`, absolute, same as mouse. Bit 0 is the tip. Barrel button maps to bit 2 via `xt()`, which swaps JS `buttons` bits 1 and 2. Eraser (JS button 5) is bit 5, which the server folds into the primary button | `_handlePointerDown`, `_handlePointerMove`, `_handlePointerUp` (pointer events, `pointerType==="pen"` only) call `_mouseButtonMovement`; `_handleRawPointerUpdate` and `_sendCoalescedContact` add high-rate and coalesced samples | `send_x11_mouse` docstring and code at lines 6568-6610 (eraser fold); constants `MOUSE_MASK_BIT_ERASER` / `MOUSE_MASK_BIT_PRIMARY` at line 9772 |

Pressure, tilt, `width`/`height` and pen hover are never read. `getPredictedEvents()` is read for the cursor position only. The server accepts only the first five fields as integers and drops the message otherwise (`input_handler.py` ~line 9279). New message types would also need adding to the allowed-prefix tuples near line 173 for shared viewers.

### The three client behaviours

1. `pointerType` versus Touch Events. The client reads both. Pointer Events are used for `pen` only: `_handlePointerDown/Move/Up` return immediately unless `e.pointerType === "pen"`, and `_handleRawPointerUpdate` routes `touch` to the trackpad code and `mouse`/`pen` to the mouse code. Fingers use Touch Events only (`touchstart/move/end/cancel`, bound in `attach_context` when `"ontouchstart" in window`). Pointer events with `pointerType === "touch"` are ignored.
2. `setPointerCapture`. Never called. A grep of `selkies-core.js` finds no `setPointerCapture` or `releasePointerCapture`. WebKit's implicit capture for fingers is therefore left as is. The pen has no capture, so a pen drag that leaves the overlay element continues only through the window-level `mousemove`/`mouseup` and `pointerrawupdate` listeners.
3. `gesturestart` / `gesturechange` / `gestureend`. Not listened to. The client does its own two-finger recognition from Touch Events (`_twoFingerStart`/`_twoFingerMove`: above a 10 px movement threshold it classifies pinch or scroll). It calls `preventDefault()` on touch events it handles (listeners are registered `passive:false`), which is what stops Safari from using the gesture itself.

Stylus handling worth knowing: the pen also produces Touch Events with `touchType === "stylus"`. In direct mode `_handleTouchEvent` calls helper `It()`, which returns true when every changed touch is a stylus, and then only calls `preventDefault()`. So the pen is not double-counted as a finger. In trackpad mode `_handleTrackpadEvent` has no such filter, so a pen is treated as a finger. Upstream issue #456 (stylus taps registering several times on iPadOS) was fixed upstream in commit b8f845f; the 2.2.1 client in this container already contains the stylus filter.

Timing constants in the direct-touch path: tap is under 250 ms and under 10 px of movement; a long press of 750 ms with under 15 px of movement sends a 50 ms right-button pulse; a tap sends a 10 ms left-button pulse.

### Touch modes

There are two modes and no others. The client class has one flag, `_trackpadMode`.

| Mode | Behaviour | Switch | What the compositor sees |
|---|---|---|---|
| Direct touch (default) | A touch is an absolute pointer position. One finger drags with the left button held. Tap is a click. Long press is a right click. Two fingers scroll or pinch | Dashboard sidebar button, which posts `touchinput:touch` | One pointer device. Absolute motion plus button events |
| Trackpad | Finger moves the pointer by relative deltas with acceleration and a speed multiplier. Tap clicks. Double-tap-drag drags. Two fingers scroll; a short two- or three-finger tap sends a right or middle button pulse (`buttonMask|=e` in `_handleTrackpadEvent`, not traced further). The page draws the pointer from the server echo | Dashboard sidebar button, which posts `touchinput:trackpad` | One pointer device. Relative motion plus button events |

How each is set:

- UI toggle: the dashboard bundle `assets/index-BD8uW1M7.js` toggles with `Fp({type: e ? "touchinput:touch" : "touchinput:trackpad"})`, a `window.postMessage` that `selkies-core.js` handles in its `message` listener, calling `setTrackpadMode(true|false)`.
- Persistence: `localStorage` key `<origin+pathname with non-alphanumerics replaced by "_">_trackpadMode` (the prefix comes from `Oe()`), read at startup via `U("trackpadMode", false)`. `trackpad_speed` is stored the same way.
- URL: no query parameter or hash selects the mode. The page reads `?token=` and the hashes `#shared`, `#player2..4`, `#display2`. None affects touch.
- Environment variable: none sets the mode. `SELKIES_UI_SIDEBAR_SHOW_TRACKPAD` (`settings.py:537`, default true) only hides or shows the sidebar button.
- Shared mode (`#shared` or a viewer): the touch listeners only call `preventDefault()`; no input is sent.

What labwc sees. Selkies injects input with the in-process pixelflux `ScreenCapture` object (`input_handler.py:4805`, `self.wayland_input`). Its injection API is `inject_mouse_move` (absolute), `inject_relative_mouse_move`, `inject_mouse_button`, `inject_mouse_scroll`, `inject_finger_scroll(_end)`, `inject_key(s)` (strings in `pixelflux.cpython-314-x86_64-linux-gnu.so`). There is no touch or tablet call. pixelflux's compositor (socket `wayland-1`) forwards these to its seat, and labwc is a nested client of it, so labwc's wlroots Wayland backend gets a `wl_pointer` and a `wl_keyboard`. On X11 sessions the same `send_x11_mouse` path uses XTEST (`send_mouse`, line 5892) or a uinput mouse socket when `uinput_mouse_socket_path` is set; both are mouse only. Not determined: the exact device name labwc reports and whether pixelflux's seat advertises a `wl_touch` capability. Querying the Wayland registry needs a client such as `wayland-info`, which this read-only pass did not run. It does not change the conclusion, since nothing injects touch.

### Prediction for what Qt receives

This is a prediction. The hand test that would check it (I1.5) was not run.

Mouse only, in both touch modes. A finger will arrive at Qt as a mouse (direct: absolute left-drag, trackpad: relative moves), and the pen as a mouse with a left button in direct mode. In trackpad mode the pen's pointer events are discarded (`_mouseButtonMovement` returns early when `_trackpadMode`), and its stylus Touch Events drive the relative cursor like a finger. Qt will see no `QTouchEvent`, no `QTabletEvent`, no pressure, no hover, and `Qt::MouseEventNotSynthesized` mouse events from a single pointer. Expected side effects: two-finger scroll arrives as wheel events (or Wayland finger-scroll), and pinch arrives as Ctrl+wheel.

Candidate patch points, if pressure were wanted through Selkies (untested, not pursued):

1. Client, send pressure: `_handlePointerDown/_Move/_Up` and `_mouseButtonMovement` in `selkies-core.js` (add a pen message with `pressure`, `tiltX`, `tiltY`, `pointerId`). The page is served from `/usr/share/selkies/web`, which is rebuilt each start (see below), so a patch needs a bind mount at the source directory.
2. Server, receive: a new branch in `_dispatch_message` beside `m`/`m2` (`input_handler.py` ~line 9276), plus the prefix tuples at ~line 173.
3. Server, inject: pixelflux has no tablet or touch injection. Either write a uinput tablet device from the server (the code already has a uinput helper at ~line 2806 and a uinput mouse socket) or deliver the samples to the app directly. For FreeShape the simplest route is a side channel (I1.6, not run) that sends pen samples straight to the Qt app and ignores the compositor.
4. App, no Selkies change: read pen state from a side channel and combine it with the mouse position Selkies already delivers.

### Upstream pen and stylus support

- selkies-project/selkies issue 112, "Support extended stylus or touch gestures": closed as completed on 2026-03-06. The maintainer reply says there are touch gestures and stylus works on iOS; it points at the client input code. No pressure or tablet support is claimed. https://github.com/selkies-project/selkies/issues/112
- Issue 456, "Stylus Taps Registering Multiple Times" (iPadOS): fixed in commit b8f845f ("#456 fix stylus double register add test"), closed 2026-10-17. https://github.com/selkies-project/selkies/issues/456
- Issue 565, "Touch: a tap whose lift the page handles more than 750 ms after its touch becomes a right click": open. Fix proposed in pull request 599 (merged). https://github.com/selkies-project/selkies/issues/565 and https://github.com/selkies-project/selkies/pull/599
- A search for pen pressure, tilt or tablet support in the Selkies issue list found none. Those issue pages were read through a summarising fetch, not the raw page, so treat details beyond the titles and states as secondhand.

### Launcher, Wayland socket, environment, nginx, web root

Launcher copy rule. `/etc/s6-overlay/s6-rc.d/init-selkies-config/run` sets `CONF_DIR=$HOME/.config/labwc` and `DEF_AUTOSTART=/defaults/autostart_wayland` when `PIXELFLUX_WAYLAND=true`. It copies `DEF_AUTOSTART` to `$CONF_DIR/autostart` only if `$CONF_DIR/autostart` does not exist (`if [[ ! -f "$CONF_DIR/autostart" ]]`). An existing autostart is never overwritten, so a probe would need to replace `$HOME/.config/labwc/autostart` (here `/config/.config/labwc/autostart`) or run on a fresh `/config`. The same script chmods the file to 644 `abc:abc`, or to root-owned 550 when `RESTART_APP=true`. `menu.xml` follows the same copy-once rule. `rc.xml` does not: it is regenerated from `/defaults/labwc.xml` on every start, so edits to it are lost. In this container the live autostart equals `/defaults/autostart_wayland` and holds one line: `cd /config/Desktop && PYTHONPATH=/src exec /opt/freeshape-venv/bin/python -m freeshape`.

Wayland socket. The runtime dir is `XDG_RUNTIME_DIR=/config/.XDG` and holds `wayland-0` and `wayland-1` (plus `labwc.sock`). Reading `/proc/net/unix` against the file descriptors of each process shows labwc (pid 458) listening on `wayland-0` and on `labwc.sock`, and the Selkies process (pid 309, pixelflux's compositor) listening on `wayland-1`. labwc itself runs with `WAYLAND_DISPLAY=wayland-1` (`startwm_wayland.sh` exports it) and is a client of Selkies. Apps started by labwc connect to `wayland-0`. Xwayland is `:0` (`Xwayland :0 -rootless`, started by labwc, socket `/tmp/.X11-unix/X0`).

Environment of an autostart child. Taken from `/proc/470/environ` (pid 470, `python -m freeshape`, started by labwc's autostart; read as user `abc`). Of note: `DISPLAY=:0` and `WAYLAND_DISPLAY=wayland-0` are both set, and `XDG_RUNTIME_DIR=/config/.XDG`. This differs from the launcher assumption in the brief (`DISPLAY=:1`, no `WAYLAND_DISPLAY`): `DISPLAY=:1` appears only in labwc's own environment and in the container's `docker exec` environment, not in the child. The `/proc` file shows the environment at exec time, so the app can still change it in-process.

```
BWRAP=/config/.local/bin/bwrap
CUPS_SERVER=/config/.XDG/selkies-cups/cups.sock
CUSTOM_WS_PORT=8082
DISABLE_DRI3=false
DISPLAY=:0
DRI_NODE=/dev/dri/renderD128
DRINODE=/dev/dri/renderD128
__GL_SYNC_TO_VBLANK=0
HOME=/config
HOSTNAME=1fffd33c50b3
_JAVA_AWT_WM_NONREPARENTING=1
LABWC_PID=458
LABWC_VER=0.9.7-dirty
LANG=en_US.UTF-8
LANGUAGE=en_US.UTF-8
LD_PRELOAD=/usr/lib/selkies_v4l2_interposer.so
NO_FULL=true
NO_GAMEPAD=true
NVIDIA_DRIVER_CAPABILITIES=all
PATH=/usr/local/games:/usr/games:/command:/opt/freeshape-venv/bin:/lsiopy/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PERL5LIB=/usr/local/bin
PGID=983
PIXELFLUX_WAYLAND=true
PUID=1000
PULSE_RUNTIME_PATH=/defaults
PWD=/config/Desktop
PYTHONPATH=/src
S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0
S6_STAGE2_HOOK=/docker-mods
S6_VERBOSITY=1
SELKIES_ALLOWED_ORIGINS=*
SELKIES_COMMAND_ENABLED=true
SELKIES_ENABLE_BASIC_AUTH=false
SELKIES_ENABLE_DUAL_MODE=false
SELKIES_ENABLE_PLAYER2=false|locked
SELKIES_ENABLE_PLAYER3=false|locked
SELKIES_ENABLE_PLAYER4=false|locked
SELKIES_ENCODER=h264enc,h265enc,vp8enc,vp9enc,av1enc,jpeg
SELKIES_GAMEPAD_ENABLED=false|locked
SELKIES_GAMEPAD_ON_START=false
SELKIES_INTERPOSER=/usr/lib/selkies_input_interposer.so
SELKIES_RATE_CONTROL_MODE=crf,cbr
SELKIES_UINPUT_GAMEPAD=false
SELKIES_UI_SIDEBAR_SHOW_GAMEPADS=false|locked
SELKIES_UI_SIDEBAR_SHOW_WEBCAM=true
SELKIES_VIDEO_STREAMING_MODE=false
SELKIES_WEBCAM_ENABLED=true
SELKIES_WEBCAM_INTERPOSER=/usr/lib/selkies_v4l2_interposer.so
SHELL=/bin/bash
SHLVL=1
START_DOCKER=true
TERM=xterm
TITLE=FreeShape
TZ=Europe/London
UV_PYTHON_INSTALL_DIR=/opt/uv-python
VIRTUAL_ENV=/opt/freeshape-venv
WAYLAND_DISPLAY=wayland-0
XCURSOR_SIZE=24
XCURSOR_THEME=breeze_cursors
XDG_CURRENT_DESKTOP=labwc:wlroots
XDG_RUNTIME_DIR=/config/.XDG
XKB_DEFAULT_LAYOUT=gb
XKB_DEFAULT_RULES=evdev
```

`XKB_DEFAULT_LAYOUT=gb` comes from `/config/.config/labwc/environment`; `startwm_wayland.sh` exports `us`, which that file overrides. `LD_PRELOAD` holds only the v4l2 interposer because `NO_GAMEPAD=true`.

nginx. The template is `/defaults/default.conf`. `/etc/s6-overlay/s6-rc.d/init-nginx/run` line 28 runs `cp /defaults/default.conf /etc/nginx/sites-available/default` on every start, then `sed` replaces `3000` (HTTP port), `3001` (HTTPS port), `CWS` (8082) and `SUBFOLDER` (default `/`). `sites-enabled/default` is a symlink to it. Any edit made in the live file is lost at the next start, so a change must go into the template (a bind mount of a replacement `/defaults/default.conf`). The template serves the web root at `location SUBFOLDER` with `alias /usr/share/selkies/web/`, proxies `SUBFOLDERapi` to `127.0.0.1:CWS` (the Selkies WebSocket), and listens on 3000 plain and 3001 with TLS (`/config/ssl/cert.pem`).

Web root. `/usr/share/selkies/web/`. The same init script runs `rm -Rf /usr/share/selkies/web` and then `cp -a /usr/share/selkies/$DASHBOARD /usr/share/selkies/web` (default `DASHBOARD=selkies-dashboard`), then overwrites `favicon.ico`, `icon.png` and writes `manifest.json`. A bind mount placed on `web/` or on a file inside it would be removed or fail at start. A mount for a side-channel patch would belong on `/usr/share/selkies/selkies-dashboard` (the copy source), or the `DASHBOARD` variable can point at a different directory under `/usr/share/selkies/`.

### Not determined

- The device name and capabilities labwc reports for the pixelflux seat (needs a Wayland client; not run, to keep the container untouched).
- Whether stock 2.2.1 produces duplicate mouse events from the compatibility `mousedown`/`mouseup` events Safari fires after a pen tap. The element-level `mousedown` and window `mouseup` listeners would call `_mouseButtonMovement` a second time; whether Safari actually sends them for the pen is a hand-test question that was not run (I1.5).
- The `touch-action` value on the overlay element. No `touch-action` rule for it appears in `index.html` or the JS, so it is not set by Selkies; behaviour depends on the browser default and on the `preventDefault()` calls above.

## Results

The hand-test matrix (I1.4, I1.5) and the side channel (I1.6) were not run. The spike was closed early after the decision below, so the work would not have changed the outcome. Consequences:

- Nothing was measured on the Qt side. The Qt probe (`scripts/spike/ipad_input/qt_probe.py`) has a passing self-test and was never run under Selkies. Every statement about what Qt receives is a prediction from the Selkies code.
- The candidate patch points in "Selkies input path" are untested. No Selkies file was patched and no bind mount was made.
- The "Not determined" items above stay open.

## Recommendation and decision

Decision (Adam, 2026-11-15): the iPad client will be browser-native, a backend plus a browser front end for a single user, instead of the Qt window streamed through Selkies.

Reasons:

- Selkies delivers mouse only.
- Real pen and touch in Qt would need a side channel, which is the input half of a browser client.
- A Qt gesture layer and compact layout would be thrown away when the UI moves to the browser.
- The video stream is soft and adds latency.

Follow-on work package: the browser client, planned separately. Its ADR will be written with that work package on a branch from master. This spike branch never merges to master, so no ADR is written here.

Design consequences for the browser client:

- A snap preview has to show while the pen is down, because there is no pen hover.
- A finger-held modifier has to be an on-screen toggle, because pen and finger are never reported together.

## Decision log text

Plain text for the Drive decision log. The Drive connector cannot edit that document, so paste this by hand.

D1 (route: stream the Qt app through Selkies): superseded on 2026-11-15 by the browser-native decision below. Its assumption that Selkies passes real touch and stylus input is false. Selkies sends finger, pen and mouse as the same mouse message in both touch modes and has no touch or pen message. Source: the I1.2 reading of the Selkies code. What Qt receives was not measured, because the Qt hand tests were withdrawn.

D4 (pen hover): closed. The iPad browser reports no pen hover on this iPad and first-generation Pencil (Chrome on iPadOS 18.6.2, WebKit). Source: the I1.1 browser probe. Consequence: a snap preview has to show while the pen is down.

D10 (pen and finger together): dropped. The browser never reports a pen and a finger at the same time, even when a finger is dragged while the pen is down. Source: the I1.1 browser probe. Consequence: a finger-held modifier has to be an on-screen toggle.

New decision (Adam, 2026-11-15): the iPad client will be browser-native, a backend plus a browser front end for a single user, instead of the Qt window streamed through Selkies. Reasons: Selkies delivers mouse only; real pen and touch in Qt would need a side channel, which is the input half of a browser client; a Qt gesture layer and compact layout would be thrown away when the UI moves to the browser; the video stream is soft and adds latency. Follow-on: the browser client work package, planned separately, with its own ADR written on a branch from master.

Unconfirmed field note, 2026-11-15: loading real FreeShape through Selkies on the iPad for the first time, the pen was treated as text input instead of navigating. The likely cause is iPadOS Scribble writing into the hidden text field Selkies keeps for its on-screen keyboard. Not confirmed.

## What is left on this branch

- `scripts/spike/ipad_input/browser_probe/`: the I1.1 browser probe and its server, including the `summary` command.
- `scripts/spike/ipad_input/qt_probe.py` and `test_qt_probe.py`: the Qt probe window and its self-test. The probe was never run under Selkies.
- `docs/spike/ipad-input-logs/i11-browser-baseline.jsonl`: the raw I1.1 log.
- Nothing was pushed to master. This branch does not merge to master.
- No `freeshape-touch` container and no `config-touch` directory were ever created. On 2026-11-15 `docker ps -a` showed no `freeshape-touch` and `/home/ncr/freeshape/config-touch` did not exist.
- The I1.1 preview hostname `ipad-probe` was removed on 2026-11-15. `preview ls` that day listed no ipad or touch hostname.
