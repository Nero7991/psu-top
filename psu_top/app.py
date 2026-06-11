"""Textual UI for psu-top."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

import serial
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, Sparkline, Static
from textual.worker import get_current_worker

from .scpi import PSUClient, PSUError

HISTORY = 600          # samples kept per channel (~3 min at the 0.3 s default)
CC_MARGIN = 0.05       # amps below the limit at which we call the mode CC
RECONNECT_DELAY = 2.0  # seconds between reopen attempts after a serial error


@dataclass
class Sample:
    volts: float
    amps: float
    set_volts: float
    set_amps: float
    output_on: bool


class MeterPanel(Vertical):
    """One bordered panel: live value + setpoint + sparkline history."""

    def __init__(self, title: str, unit: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.border_title = title
        self._unit = unit
        self._history: deque[float] = deque([0.0] * HISTORY, maxlen=HISTORY)

    def compose(self) -> ComposeResult:
        yield Static("--", classes="meter-value")
        yield Sparkline(list(self._history))

    def update_value(self, measured: float, setpoint: float) -> None:
        self._history.append(measured)
        self.query_one(".meter-value", Static).update(
            f"{measured:8.3f} {self._unit}   (set {setpoint:.3f})"
        )
        self.query_one(Sparkline).data = list(self._history)


class PSUTopApp(App):
    """htop-style monitor/controller for the Kiprim DC310S."""

    CSS = """
    #header { height: 1; background: $boost; }
    #power { height: 1; }
    MeterPanel { border: round $primary; height: 9; }
    .meter-value { height: 1; }
    Sparkline { height: 4; margin: 1 0 0 0; }
    #entry { display: none; }
    #entry.visible { display: block; }
    """

    # Without this, Textual auto-focuses the hidden #entry Input on mount and
    # it swallows the key bindings.
    AUTO_FOCUS = None

    BINDINGS = [
        ("o", "toggle_output", "Output on/off"),
        ("v", "set_voltage", "Set voltage"),
        ("c", "set_current", "Set current"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, port: str, baud: int, interval: float) -> None:
        super().__init__()
        self._port = port
        self._baud = baud
        self._interval = interval
        self._client: PSUClient | None = None
        self._identity = ""
        self._last: Sample | None = None
        self._entry_target: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(" connecting...", id="header")
        with Horizontal():
            yield MeterPanel("Voltage", "V", id="voltage")
            yield MeterPanel("Current", "A", id="current")
        yield Static(id="power")
        yield Input(id="entry")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._poll_loop, thread=True, exclusive=True)

    # ---- poll worker (runs in its own thread) ----

    def _poll_loop(self) -> None:
        worker = get_current_worker()
        while not worker.is_cancelled:
            try:
                if self._client is None:
                    self._connect()
                client = self._client
                sample = Sample(
                    volts=client.measured_voltage(),
                    amps=client.measured_current(),
                    set_volts=client.get_voltage_setpoint(),
                    set_amps=client.get_current_setpoint(),
                    output_on=client.get_output(),
                )
            except (PSUError, serial.SerialException, OSError):
                dead, self._client = self._client, None
                if dead is not None:
                    try:
                        dead.close()
                    except OSError:
                        pass
                if worker.is_cancelled:
                    return
                self.call_from_thread(self._show_disconnected)
                self._sleep(worker, RECONNECT_DELAY)
                continue
            if worker.is_cancelled:
                return
            self.call_from_thread(self._show_sample, sample)
            self._sleep(worker, self._interval)

    def _connect(self) -> None:
        ser = serial.Serial(self._port, self._baud, timeout=1)
        client = PSUClient(ser)
        idn = client.identify()                  # e.g. KIPRIM,DC310S,25012662,FV:V5.2.0
        self._identity = " ".join(idn.split(",")[:2])
        self._client = client

    @staticmethod
    def _sleep(worker, seconds: float) -> None:
        """Sleep in small steps so quit is not delayed by a long wait."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not worker.is_cancelled:
            time.sleep(0.1)

    # ---- UI updates (run on the app thread via call_from_thread) ----

    def _show_sample(self, sample: Sample) -> None:
        self._last = sample
        mode = ""
        if sample.output_on:
            mode = " (CC)" if sample.amps >= sample.set_amps - CC_MARGIN else " (CV)"
        state = ("ON" if sample.output_on else "OFF") + mode
        self.query_one("#header", Static).update(
            f" PSU  {self._identity}  {self._port}  [CONNECTED]   OUTPUT: {state}"
        )
        self.query_one("#voltage", MeterPanel).update_value(sample.volts, sample.set_volts)
        self.query_one("#current", MeterPanel).update_value(sample.amps, sample.set_amps)
        self.query_one("#power", Static).update(
            f" Power: {sample.volts * sample.amps:6.2f} W"
        )

    def _show_disconnected(self) -> None:
        self._last = None
        self.query_one("#header", Static).update(
            f" PSU  {self._identity or 'unknown'}  {self._port}  [DISCONNECTED] retrying every {RECONNECT_DELAY:.0f}s"
        )

    # ---- controls ----

    def action_toggle_output(self) -> None:
        client, last = self._client, self._last
        if client is None or last is None:
            return
        target = client.output_off if last.output_on else client.output_on
        self.run_worker(target, thread=True, exit_on_error=False)

    def action_set_voltage(self) -> None:
        self._prompt("voltage", "V")

    def action_set_current(self) -> None:
        self._prompt("current", "A")

    def _prompt(self, target: str, unit: str) -> None:
        if self._client is None:
            return
        self._entry_target = target
        entry = self.query_one("#entry", Input)
        entry.placeholder = f"new {target} ({unit}) -- Enter to apply, Esc to cancel"
        entry.value = ""
        entry.add_class("visible")
        entry.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        client, target = self._client, self._entry_target
        self._dismiss_entry()
        if client is None or target is None:
            return
        try:
            value = float(event.value)
        except ValueError:
            return
        setter = client.set_voltage if target == "voltage" else client.set_current
        self.run_worker(lambda: setter(value), thread=True, exit_on_error=False)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self._dismiss_entry()

    def _dismiss_entry(self) -> None:
        self._entry_target = None
        entry = self.query_one("#entry", Input)
        entry.remove_class("visible")
        self.set_focus(None)
