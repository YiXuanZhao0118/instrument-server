# instrument_package/Mcculw_USB3104.py
"""
MCC UL (mcculw) USB-31xx AO/DO driver that conforms to server interface:
 - initialize(self) -> dict({"ok": bool, "message": str})
 - shutdown(self) -> None
 - is_opened(self) -> bool

Public methods (set_analog_output / set_digital_output / getters) are exposed for RPC/capabilities.
"""

from typing import Any, Dict, Optional


class McculwDriver:
    """
    Driver wrapper for MCC USB-31xx (e.g., USB-3104/3114) analog & digital outputs.

    This class exposes a uniform interface for the instrument server:
      - `initialize()` binds the MCC UL board, configures AO ranges and one DO port.
      - `shutdown()` releases the board.
      - `is_opened()` verifies responsiveness by probing harmless board queries
        (`ul.get_board_name` and `DaqDeviceInfo(...).product_name`).
    """

    def __init__(self,
                 board_num: int = 0,
                 ao_channels: int = 8,
                 voltage_range: str = "BIP10VOLTS",
                 use_device_detection: bool = True,
                 dev_id_list: Optional[list] = None):
        """
        Args:
            board_num (int): UL board number (default 0).
            ao_channels (int): number of AO channels (USB-3104: 8, USB-3114: 16).
            voltage_range (str): ULRange name string, e.g. "BIP10VOLTS".
            use_device_detection (bool): run device inventory and create_daq_device.
            dev_id_list (list[int] | None): optional list of product_id to filter for.
        """
        self.board_num = int(board_num)
        self.ao_channels = int(ao_channels)
        self.voltage_range_name = str(voltage_range)
        self.use_device_detection = bool(use_device_detection)
        self.dev_id_list = list(dev_id_list) if dev_id_list else []

        # lazy-loaded UL objects/modules
        self._ul = None
        self._DaqDeviceInfo = None
        self._InterfaceType = None
        self._InfoType = None
        self._BoardInfo = None
        self._ULRange = None
        self._DigitalIODirection = None

        # runtime state
        self.device = None
        self.daq_dev_info = None
        self.ao_range = None
        self.port = None
        self._opened = False
        self._last_error = ""

        # soft caches
        self._ao_last_value: Dict[int, float] = {}
        self._digital_last_value: Optional[int] = None

    # -------- required interface --------
    def initialize(self):
        """
        Initialize MCC device, configure AO ranges for all channels and one DO port for output.

        Returns:
            dict: {"ok": True/False, "message": "..."}.
        """
        try:
            # lazy import
            from mcculw import ul as _ul
            from mcculw.device_info import DaqDeviceInfo as _DaqDeviceInfo
            from mcculw.enums import InfoType as _InfoType, BoardInfo as _BoardInfo
            from mcculw.enums import InterfaceType as _InterfaceType
            from mcculw.enums import ULRange as _ULRange, DigitalIODirection as _DigitalIODirection

            self._ul = _ul
            self._DaqDeviceInfo = _DaqDeviceInfo
            self._InterfaceType = _InterfaceType
            self._InfoType = _InfoType
            self._BoardInfo = _BoardInfo
            self._ULRange = _ULRange
            self._DigitalIODirection = _DigitalIODirection

            if self.use_device_detection:
                # ignore InstaCal and enumerate connected devices
                self._ul.ignore_instacal()
                devices = self._ul.get_daq_device_inventory(self._InterfaceType.ANY)
                if not devices:
                    msg = "No MCC DAQ devices found."
                    self._last_error = msg
                    return {"ok": False, "message": msg}

                # pick first or by dev_id_list
                self.device = devices[0]
                if self.dev_id_list:
                    self.device = next((d for d in devices if d.product_id in self.dev_id_list), None)
                    if not self.device:
                        msg = f"No DAQ device found in device ID list: {self.dev_id_list}"
                        self._last_error = msg
                        return {"ok": False, "message": msg}

                # bind board number
                self._ul.create_daq_device(self.board_num, self.device)

            # Query board capabilities
            self.daq_dev_info = self._DaqDeviceInfo(self.board_num)
            if not self.daq_dev_info.supports_analog_output:
                msg = "This DAQ device does not support analog output."
                self._last_error = msg
                return {"ok": False, "message": msg}

            # Resolve ULRange by name
            try:
                rng = getattr(self._ULRange, self.voltage_range_name)
            except AttributeError:
                return {"ok": False, "message": f"Invalid ULRange name: {self.voltage_range_name}"}

            # Configure AO ranges for all channels (BOARDINFO/DACRANGE per channel index)
            for ch in range(self.ao_channels):
                self._ul.set_config(self._InfoType.BOARDINFO, self.board_num, ch, self._BoardInfo.DACRANGE, rng)

            # cache first supported ao range for v_out
            ao_info = self.daq_dev_info.get_ao_info()
            self.ao_range = ao_info.supported_ranges[0] if ao_info.supported_ranges else rng

            # Configure digital output port (first output-capable port)
            dio_info = self.daq_dev_info.get_dio_info()
            self.port = next((p for p in dio_info.port_info if p.supports_output), None)
            if not self.port:
                msg = "This DAQ device does not support digital output."
                self._last_error = msg
                return {"ok": False, "message": msg}
            if self.port.is_port_configurable:
                self._ul.d_config_port(self.board_num, self.port.type, self._DigitalIODirection.OUT)

            # Optimistically open then verify with probe
            self._opened = True
            if not self.is_opened():
                self._opened = False
                return {"ok": False, "message": "DAQ did not respond after initialize()"}

            return {"ok": True, "message": f"MCC device ready on board {self.board_num} with {self.ao_channels} AO channels"}

        except Exception as e:
            self._opened = False
            self._last_error = f"{type(e).__name__}: {e}"
            return {"ok": False, "message": self._last_error}

    def shutdown(self):
        """
        Release the DAQ device. Safe to call multiple times.
        """
        try:
            if self._ul:
                try:
                    # stop any ongoing I/O if needed (analog output is immediate mode here)
                    pass
                finally:
                    # release board binding
                    try:
                        self._ul.release_daq_device(self.board_num)
                    except Exception:
                        pass
        finally:
            self._opened = False
            self.device = None
            self.daq_dev_info = None
            self.ao_range = None
            self.port = None

    def is_opened(self) -> bool:
        """
        Verify device responsiveness with harmless queries.

        It checks:
          1) UL module present,
          2) `ul.get_board_name(board_num)` works,
          3) `DaqDeviceInfo(board_num).product_name` is readable.

        Returns:
            bool: True if the device replies, False otherwise.
        """
        try:
            if not self._opened or not self._ul:
                return False
            # 1) board name probe (raises if board is not bound/available)
            _ = self._ul.get_board_name(self.board_num)
            # 2) info object probe (will raise if board not present)
            _info = self._DaqDeviceInfo(self.board_num)
            _ = getattr(_info, "product_name", None)  # touch a property
            return True
        except Exception:
            return False

    # -------- capabilities (public methods) --------
    def set_analog_output(self, channel: int, voltage: float):
        """
        Set analog output voltage on a channel (software-caches last written value).

        Args:
            channel (int): AO channel index.
            voltage (float): Output voltage.
        Returns:
            dict: {"channel": int, "voltage": float}
        """
        self._require_open()
        if self.ao_range is None:
            raise RuntimeError("Analog output range not set. Call initialize() first.")
        self._ul.v_out(self.board_num, int(channel), self.ao_range, float(voltage))
        self._ao_last_value[int(channel)] = float(voltage)
        return {"channel": int(channel), "voltage": float(voltage)}

    def get_analog_output(self, channel: int):
        """
        Return last written AO voltage from software cache.

        Args:
            channel (int): AO channel index.
        Returns:
            float | None: Last written voltage if available.
        """
        return self._ao_last_value.get(int(channel), None)

    def set_digital_output(self, value: int):
        """
        Write to the first output-capable digital port.

        Args:
            value (int): Raw port value (e.g., 0..255 for 8-bit port).
        Returns:
            dict: {"port": "<port_type>", "value": int}
        """
        self._require_open()
        if self.port is None:
            raise RuntimeError("Digital output port not configured. Call initialize() first.")
        self._ul.d_out(self.board_num, self.port.type, int(value))
        self._digital_last_value = int(value)
        return {"port": str(self.port.type), "value": int(value)}

    def get_digital_output(self):
        """
        Return last written digital port value from software cache.

        Returns:
            int | None: Last written port value if available.
        """
        return self._digital_last_value

    def get_last_error(self) -> str:
        """
        Return last cached error string if any.

        Returns:
            str: Last error message.
        """
        return self._last_error

    # -------- helpers --------
    def _require_open(self):
        """
        Raise RuntimeError if device is not responsive.
        """
        if not self.is_opened():
            raise RuntimeError("DAQ device is not initialized/opened")
