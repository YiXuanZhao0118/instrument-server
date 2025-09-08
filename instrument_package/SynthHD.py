# instrument_package/SynthHD.py
"""
Windfreak SynthHD driver wrapper conforming to the server interface:

Required interface:
 - initialize(self) -> dict({"ok": bool, "message": str})
 - shutdown(self) -> None
 - is_opened(self) -> bool

All other public methods are exposed to the RPC/capabilities layer.
"""

from typing import Any, Optional, Dict


class SynthHDDriver:
    """
    Windfreak SynthHD wrapper for the instrument server.

    Notes
    -----
    * This driver opens the device lazily in `initialize()`.
    * `is_opened()` returns False if probing basic device info fails.
    * Channel index is 0 or 1 (SynthHD has two outputs).
    """

    def __init__(self, devpath: Optional[str] = None,
                 reset_on_connect: bool = False,
                 **_):
        """
        Args:
            devpath: Serial device path, e.g. "COM3" or "/dev/ttyUSB0".
                     (UI 可用「port」欄位，server 會自動映射為 devpath)
            reset_on_connect: If True, call device.init() after connecting (clears settings).
        """
        self.devpath = devpath or ""
        self.reset_on_connect = bool(reset_on_connect)

        self._SynthHD_cls = None    # resolved on initialize()
        self.device = None          # actual SynthHD instance
        self._opened = False
        self._last_error = ""

    # -------- required interface --------
    def initialize(self):
        """
        Open the SynthHD device and (optionally) reset settings.

        Returns:
            dict: {"ok": True, "message": "..."} on success
                  {"ok": False, "message": "<error>"} on failure
        """
        try:
            if not self.devpath:
                return {"ok": False, "message": "devpath is empty (set 'port' or 'devpath')"}

            if self._SynthHD_cls is None:
                # Lazy import so scanning this file doesn't require the package
                from windfreak import SynthHD as _SynthHD
                self._SynthHD_cls = _SynthHD

            # Open device
            self.device = self._SynthHD_cls(self.devpath)

            if self.reset_on_connect and hasattr(self.device, "init"):
                # init() typically resets settings to defaults
                self.device.init()

            # Mark open
            self._opened = True

            # Optional: a light probe here could be done, but we defer to is_opened()
            return {"ok": True, "message": f"SynthHD connected on {self.devpath}"}

        except Exception as e:
            self._opened = False
            self.device = None
            self._last_error = f"{type(e).__name__}: {e}"
            return {"ok": False, "message": self._last_error}

    def shutdown(self):
        """
        Close the device if open. Safe to call multiple times.
        """
        try:
            if self.device and hasattr(self.device, "close"):
                try:
                    self.device.close()
                except Exception:
                    # swallow close errors
                    pass
        finally:
            self._opened = False
            self.device = None

    def is_opened(self) -> bool:
        """
        Return True if device is responsive.

        Strategy:
            - Must have a device instance and flagged opened.
            - Probe harmless properties via `get_info()`.
            - Any exception -> False.
        """
        try:
            if not self._opened or self.device is None:
                return False
            _ = self.get_info()  # will raise if not responsive
            return True
        except Exception:
            return False

    # -------- helpers --------
    def _require_open(self):
        """
        Raise if not opened/responsive.
        """
        if not self.is_opened():
            raise RuntimeError("SynthHD is not opened. Call initialize() first.")

    def get_last_error(self) -> str:
        """
        Return last cached error string (if any).
        """
        return self._last_error

    def _get_channel(self, index: int):
        """
        Return channel object by index (0 or 1).
        """
        self._require_open()
        try:
            return self.device[int(index)]
        except Exception:
            raise ValueError(f"Invalid channel index: {index}")

    # -------- device info --------
    def get_info(self) -> Dict[str, Any]:
        """
        Return device info: model, serial_number, firmware_version, hardware_version, model_type.
        """
        self._require_open()
        info: Dict[str, Any] = {}
        for k in ("model", "serial_number", "firmware_version", "hardware_version", "model_type"):
            try:
                info[k] = getattr(self.device, k)
            except Exception:
                info[k] = None
        return info

    # -------- reference / trigger / global enables --------
    def set_reference_mode(self, mode: str):
        """
        Set reference mode.
        Example values (library dependent): 'external', 'internal 27mhz', 'internal 10mhz'.
        """
        self._require_open()
        self.device.reference_mode = mode

    def get_reference_mode(self) -> str:
        """
        Get current reference mode.
        """
        self._require_open()
        return self.device.reference_mode

    def set_trigger_mode(self, mode: str):
        """
        Set trigger mode (library dependent string).
        """
        self._require_open()
        self.device.trigger_mode = mode

    def get_trigger_mode(self) -> str:
        """
        Get trigger mode.
        """
        self._require_open()
        return self.device.trigger_mode

    def set_reference_frequency(self, frequency_hz: float):
        """
        Set external reference frequency in Hz.
        """
        self._require_open()
        self.device.reference_frequency = float(frequency_hz)

    def get_reference_frequency(self) -> float:
        """
        Get external reference frequency in Hz.
        """
        self._require_open()
        return float(self.device.reference_frequency)

    def set_sweep_enable(self, enable: bool):
        """
        Enable/disable sweep globally.
        """
        self._require_open()
        self.device.sweep_enable = bool(enable)

    def set_am_enable(self, enable: bool):
        """
        Enable/disable AM globally.
        """
        self._require_open()
        self.device.am_enable = bool(enable)

    def set_pulse_mod_enable(self, enable: bool):
        """
        Enable/disable pulse modulation globally.
        """
        self._require_open()
        self.device.pulse_mod_enable = bool(enable)

    def set_fm_enable(self, enable: bool):
        """
        Enable/disable FM globally.
        """
        self._require_open()
        self.device.fm_enable = bool(enable)

    # -------- per-channel controls --------
    def set_channel_frequency(self, channel_index: int, frequency_hz: float):
        """
        Set RF frequency of a given channel.

        Args:
            channel_index: 0 or 1
            frequency_hz: Frequency in Hz (typical SynthHD range 54e6..13.6e9).
        """
        ch = self._get_channel(channel_index)
        ch.frequency = float(frequency_hz)

    def get_channel_frequency(self, channel_index: int) -> float:
        """
        Get RF frequency (Hz) of a given channel.
        """
        ch = self._get_channel(channel_index)
        return float(ch.frequency)

    def set_channel_power(self, channel_index: int, power_dbm: float):
        """
        Set RF output power (dBm) of a given channel.
        """
        ch = self._get_channel(channel_index)
        ch.power = float(power_dbm)

    def get_channel_power(self, channel_index: int) -> float:
        """
        Get RF output power (dBm) of a given channel.
        """
        ch = self._get_channel(channel_index)
        return float(ch.power)

    def set_channel_phase(self, channel_index: int, phase_deg: float):
        """
        Set RF phase (deg) of a given channel.
        """
        ch = self._get_channel(channel_index)
        ch.phase = float(phase_deg)

    def get_channel_phase(self, channel_index: int) -> float:
        """
        Get RF phase (deg) of a given channel.
        """
        ch = self._get_channel(channel_index)
        return float(ch.phase)

    def enable_channel_output(self, channel_index: int, enable: bool = True) -> Dict[str, Any]:
        """
        Enable/disable RF output path on a channel (rf/pa/pll together).

        Returns:
            dict: {"channel": int, "enabled": bool}
        """
        ch = self._get_channel(channel_index)
        enable = bool(enable)
        # Keep original behavior: toggle rf, pa, pll together
        ch.rf_enable = enable
        ch.pa_enable = enable
        ch.pll_enable = enable
        return {"channel": int(channel_index), "enabled": enable}

    def get_channel_lock_status(self, channel_index: int) -> bool:
        """
        Return PLL lock status of a given channel.
        """
        ch = self._get_channel(channel_index)
        return bool(ch.lock_status)
