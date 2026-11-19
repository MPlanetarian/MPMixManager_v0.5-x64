#!/usr/bin/env python3
"""MP Liquid Mixer - the four per-channel insert effects (no GTK imports).

Each mixer channel owns one insert. The FILTER knob beside its LOW EQ knob is
the effect's depth (``amount`` 0.0-1.0) and a numbered slot square picks which
of the four effects that knob drives:

    1  FLANGER  red      narrow swept comb notch, classic jet sweep
    2  DELAY    yellow   tempo-free dotted echo with feedback
    3  REVERB   blue     short diffusion tail
    4  GATER    orange   tempo-free rhythmic amplitude chop

FLANGER is the default for every channel. The effects button above each filter
knob arms or bypasses the insert, so a channel can keep its slot choice and
depth while it is switched out of the path.

Everything here is plain numpy so it can be unit-tested without a display and
so the player engine can use the exact same DSP the mixer draws. The mixer
process itself never runs this code: it sends ``set_effect`` over IPC and the
deck's engine applies it.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

MODES = ("FLANGER", "REVERB", "DELAY", "GATER")
# "FILTER" is not an effect: it is the plain resonant sweep the orange square
# selects, so the knob behaves like a classic DJ filter.
FILTER_MODE = "FILTER"
DEFAULT_MODE = "FLANGER"

# Short names and the slot colours used by the four squares on the mixer.
MODE_LABELS = {
    "FLANGER": "Flanger",
    "DELAY": "Delay",
    "REVERB": "Reverb",
    "GATER": "Gater",
    FILTER_MODE: "Filter",
}
MODE_COLORS = {
    "FLANGER": (235, 64, 72),     # red
    "DELAY": (250, 205, 90),      # yellow
    "REVERB": (72, 149, 239),     # blue
    "GATER": (60, 220, 120),      # green
    FILTER_MODE: (255, 150, 40),  # orange, the Filter switch
}


def normalize_mode(mode: Optional[str]) -> str:
    name = str(mode or "").strip().upper()
    if name == FILTER_MODE:
        return FILTER_MODE
    return name if name in MODES else DEFAULT_MODE


def clamp_amount(value: Any) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if amount < 0.0 else (1.0 if amount > 1.0 else amount)


class ChannelEffect:
    """One channel's stereo insert.

    The class is deliberately allocation-light inside ``process``: a 1024-frame
    block arrives 40-90 times a second per deck, and four decks can be live.
    State buffers are sized on the first block and reused.
    """

    def __init__(self, mode: str = DEFAULT_MODE, amount: float = 0.0,
                 enabled: bool = False, sample_rate: int = 44100) -> None:
        self.mode = normalize_mode(mode)
        self.amount = clamp_amount(amount)
        self.enabled = bool(enabled)
        self.sample_rate = int(sample_rate or 44100)
        self._np = None
        self._flanger_lines = None      # (2, delay_len) ring buffers
        self._flanger_index = 0
        self._delay_buffer = None       # (2, delay_len)
        self._delay_index = 0
        self._reverb = None             # (2, tail_len)
        self._reverb_index = 0
        self._gater_phase = 0.0
        self._filter_state = None
        self._filter_alpha = 0.0
        self._filter_cutoff = 0.0
        self._filter_high = False
        self._filter_rate = 0

    # -- configuration ----------------------------------------------------
    def configure(self, mode: Optional[str] = None, amount: Any = None,
                  enabled: Optional[bool] = None,
                  sample_rate: Optional[int] = None) -> None:
        if mode is not None:
            normalized = normalize_mode(mode)
            if normalized != self.mode:
                self.mode = normalized
                self._reset_state()
        if amount is not None:
            self.amount = clamp_amount(amount)
        if enabled is not None:
            self.enabled = bool(enabled)
        if sample_rate:
            rate = int(sample_rate)
            if rate > 0 and rate != self.sample_rate:
                self.sample_rate = rate
                self._reset_state()

    def apply(self, payload: Optional[Dict[str, Any]]) -> None:
        """Adopt a ``{"mode", "amount", "enabled"}`` IPC payload."""
        if not isinstance(payload, dict):
            return
        self.configure(mode=payload.get("mode"), amount=payload.get("amount"),
                       enabled=payload.get("enabled"))
        try:
            rate = int(payload.get("sample_rate") or 0)
        except (TypeError, ValueError):
            rate = 0
        if rate > 0:
            self.configure(sample_rate=rate)

    @property
    def active(self) -> bool:
        return bool(self.enabled and self.amount > 0.002)

    def to_dict(self) -> Dict[str, Any]:
        return {"mode": self.mode.lower(), "amount": round(self.amount, 4),
                "enabled": bool(self.enabled)}

    # -- state ------------------------------------------------------------
    def _reset_state(self) -> None:
        self._flanger_lines = None
        self._delay_buffer = None
        self._reverb = None
        self._gater_phase = 0.0
        self._filter_state = None
        self._filter_cutoff = 0.0

    def _numpy(self):
        if self._np is None:
            import numpy
            self._np = numpy
        return self._np

    # -- processing -------------------------------------------------------
    def process(self, block):
        """Return ``block`` with the insert applied, or unchanged when bypassed."""
        if not self.active:
            return block
        np = self._numpy()
        data = np.asarray(block, dtype=np.float32)
        if data.ndim == 1:
            data = np.column_stack((data, data))
        if len(data) == 0:
            return block
        if self.mode == FILTER_MODE:
            return self._filter(data)
        if self.mode == "FLANGER":
            return self._flanger(data)
        if self.mode == "DELAY":
            return self._delay(data)
        if self.mode == "REVERB":
            return self._reverb_tail(data)
        return self._gater(data)

    def _filter(self, data):
        """Centre-detented DJ filter: low-pass below the centre, high-pass above.

        Amount 0.5 is flat, so the knob's centre is a true bypass. Below the
        centre it closes a one-pole low pass down to 180 Hz; above it opens a
        high pass up to 6.5 kHz. A little feedback around the cutoff gives the
        sweep the resonance a DJ filter is expected to have.
        """
        np = self._numpy()
        rate = float(self.sample_rate)
        amount = float(self.amount)
        # Treat the knob as 0..1 with 0.5 as the detent.
        if 0.47 <= amount <= 0.53:
            return data
        if amount < 0.47:
            t = amount / 0.47                       # 0 at the bottom, 1 at the detent
            cutoff = 180.0 + (rate * 0.45 - 180.0) * max(0.0, t)
            high_pass = False
        else:
            t = (amount - 0.53) / 0.47              # 0 at the detent, 1 at the top
            cutoff = 120.0 + (6500.0 - 120.0) * max(0.0, 1.0 - t)
            high_pass = True
        cutoff = max(20.0, min(rate * 0.45, cutoff))
        if self._filter_state is None or self._filter_cutoff != cutoff \
                or self._filter_high != high_pass or self._filter_rate != rate:
            x = 2.0 * np.pi * cutoff / rate
            alpha = x / (x + 1.0)
            self._filter_alpha = float(alpha)
            self._filter_cutoff = cutoff
            self._filter_high = high_pass
            self._filter_rate = rate
            self._filter_state = np.zeros((2, 2), dtype=np.float32)
        alpha = self._filter_alpha
        state = self._filter_state
        out = np.empty_like(data)
        for frame in range(len(data)):
            for channel in range(2):
                low = state[channel, 0] + alpha * (data[frame, channel] - state[channel, 0])
                state[channel, 0] = low
                if high_pass:
                    out[frame, channel] = data[frame, channel] - low
                else:
                    out[frame, channel] = low
        # Resonance: a touch of the removed band folded back at full sweep.
        drive = 0.0 if 0.47 <= amount <= 0.53 else 0.55 * abs(amount - 0.5) * 2.0
        if drive > 0.0:
            out = out + (data - out) * float(min(0.45, drive * 0.45))
        return np.clip(out, -1.0, 1.0)

    def _flanger(self, data):
        np = self._numpy()
        frames = len(data)
        rate = float(self.sample_rate)
        # 5 ms to 30 ms sweep; the notch travels with ``amount`` so the knob is
        # an obvious jet sweep rather than a static comb.
        max_delay = max(2, int(0.030 * rate))
        if self._flanger_lines is None or self._flanger_lines.shape[1] != max_delay + 1:
            self._flanger_lines = np.zeros((2, max_delay + 1), dtype=np.float32)
            self._flanger_index = 0
        lines = self._flanger_lines
        depth = 0.25 + 0.70 * self.amount
        sweep_hz = 0.15 + 1.35 * self.amount
        phase = 2.0 * np.pi * sweep_hz / rate
        out = np.empty_like(data)
        index = self._flanger_index
        size = lines.shape[1]
        # A fractional read is interpolated between the two neighbouring taps:
        # without it a moving read pointer clicks once per sample.
        for frame in range(frames):
            for channel in range(2):
                lines[channel, index] = data[frame, channel]
            angle = phase * frame
            delay = 2.0 + (max_delay - 2) * (0.5 + 0.5 * np.sin(angle)) * depth
            read = index - delay
            if read < 0:
                read += size
            low = int(read)
            high = (low + 1) % size
            frac = read - low
            wet = (lines[:, low] * (1.0 - frac) + lines[:, high] * frac)
            out[frame] = data[frame] * (1.0 - 0.5 * self.amount) + wet * (0.85 * self.amount)
            index += 1
            if index >= size:
                index = 0
        self._flanger_index = index
        return np.clip(out, -1.0, 1.0)

    def _delay(self, data):
        np = self._numpy()
        frames = len(data)
        rate = float(self.sample_rate)
        # 60 ms to 320 ms, feedback rising with depth. Tempo-free on purpose:
        # the mixer has no per-deck beat clock for the insert.
        max_delay = max(4, int(0.35 * rate))
        if self._delay_buffer is None or self._delay_buffer.shape[1] != max_delay + 1:
            self._delay_buffer = np.zeros((2, max_delay + 1), dtype=np.float32)
            self._delay_index = 0
        buffer = self._delay_buffer
        size = buffer.shape[1]
        tap = int((0.060 + 0.260 * self.amount) * rate)
        tap = max(2, min(size - 1, tap))
        feedback = 0.20 + 0.45 * self.amount
        index = self._delay_index
        out = np.empty_like(data)
        for frame in range(frames):
            read = index - tap
            if read < 0:
                read += size
            echo = buffer[:, read]
            wet = data[frame] + echo * feedback
            buffer[:, index] = wet
            out[frame] = data[frame] * (1.0 - 0.35 * self.amount) + echo * (0.85 * self.amount)
            index += 1
            if index >= size:
                index = 0
        self._delay_index = index
        return np.clip(out, -1.0, 1.0)

    def _reverb_tail(self, data):
        np = self._numpy()
        frames = len(data)
        rate = float(self.sample_rate)
        tail = max(8, int(0.11 * rate))
        if self._reverb is None or self._reverb.shape[1] != tail:
            self._reverb = np.zeros((2, tail), dtype=np.float32)
            self._reverb_index = 0
        line = self._reverb
        index = self._reverb_index
        decay = 0.45 + 0.45 * self.amount
        damp = 0.18 + 0.50 * self.amount
        out = np.empty_like(data)
        for frame in range(frames):
            delayed = line[:, index].copy()
            # Two-tap cross feedback gives a wide, short diffusion tail
            # without a second buffer; damping is a one-pole low pass.
            wet = delayed * damp
            line[:, index] = data[frame] + wet * decay
            out[frame] = data[frame] * (1.0 - 0.30 * self.amount) + delayed * (0.90 * self.amount)
            index += 1
            if index >= tail:
                index = 0
        self._reverb_index = index
        return np.clip(out, -1.0, 1.0)

    def _gater(self, data):
        np = self._numpy()
        frames = len(data)
        rate = float(self.sample_rate)
        # 4 Hz to 14 Hz chop, square-ish but softened so it does not alias.
        rate_hz = 4.0 + 10.0 * self.amount
        step = rate_hz / rate
        phase = self._gater_phase
        out = np.empty_like(data)
        for frame in range(frames):
            phase += step
            if phase >= 1.0:
                phase -= 1.0
            # 55% open, then a 3% ramp so the edges do not click.
            open_gate = 1.0 if phase < 0.55 else 0.0
            if 0.52 <= phase <= 0.55 or 0.97 <= phase:
                open_gate = 0.5
            gain = 1.0 - (1.0 - 0.25) * (1.0 - open_gate) * self.amount
            out[frame] = data[frame] * gain
        self._gater_phase = phase
        return np.clip(out, -1.0, 1.0)


def make_effect(payload: Optional[Dict[str, Any]] = None,
                sample_rate: int = 44100) -> ChannelEffect:
    """An effect built from one IPC payload, with the documented defaults."""
    payload = payload if isinstance(payload, dict) else {}
    effect = ChannelEffect(mode=payload.get("mode"), amount=payload.get("amount"),
                           enabled=payload.get("enabled"), sample_rate=sample_rate)
    return effect
