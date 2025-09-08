# instrument_package/HighFinesse.py

"""

HighFinesse WLM driver wrapper conforming to the server's required interface:

 - initialize(self) -> dict({"ok": bool, "message": str})

 - shutdown(self) -> None

 - is_opened(self) -> bool



All other public methods are exposed to the RPC/capabilities layer.

"""



from typing import Any, Optional, Tuple





class HighFinesseDriver:

    """

    Driver wrapper for HighFinesse WLM.



    This class provides a thin, safe wrapper around the pylablib

    HighFinesse WLM driver, exposing a minimal and uniform interface for

    the instrument server:



      - `initialize()` constructs/opens the device.

      - `shutdown()` closes the device.

      - `is_opened()` checks if the device is responsive by probing

        `get_device_info()` (robust against stale handles).



    All other public methods map to the underlying driver's capabilities

    and are callable via RPC once the device is connected.

    """



    def __init__(self, version: Optional[str] = None,

                 dll_path: Optional[str] = None,

                 app_path: Optional[str] = None,

                 autostart: bool = True):

        """

        Build a WLM instance on demand (lazy), to avoid import errors during driver scanning.



        Args:

            version (str, optional): WLM DLL/driver version selector used by pylablib.

            dll_path (str, optional): Explicit path to the WLM DLL (if not on PATH).

            app_path (str, optional): Path to the WLM application (for autostart support).

            autostart (bool): Whether to auto-start background WLM service/app if needed.

        """

        self.version = version

        self.dll_path = dll_path

        self.app_path = app_path

        self.autostart = autostart



        self._WLM_cls = None   # resolved on first initialize()

        self.wm = None         # actual WLM instance

        self._opened = False



    # -------- required interface --------

    def initialize(self):

        """

        Try to create and open the WLM device.



        Returns:

            dict: {"ok": True/False, "message": "<status or error>"}

        """

        try:

            # Lazy import to prevent module-load failures if pylablib isn't installed yet

            if self._WLM_cls is None:

                from pylablib.devices.HighFinesse.wlm import WLM as _WLM

                self._WLM_cls = _WLM



            self.wm = self._WLM_cls(

                version=self.version,

                dll_path=self.dll_path,

                app_path=self.app_path,

                autostart=self.autostart

            )

            # Some pylablib versions open on construction; ensure explicit open if available

            if hasattr(self.wm, "open"):

                self.wm.open()



            # Mark opened optimistically; robust check is in is_opened()

            self._opened = True



            # Final verification: make sure device is responsive

            if not self.is_opened():

                self._opened = False

                return {"ok": False, "message": "WLM did not respond after initialize()"}



            return {"ok": True, "message": "HighFinesse WLM connected"}

        except Exception as e:

            self._opened = False

            self.wm = None

            return {"ok": False, "message": f"{type(e).__name__}: {e}"}



    def shutdown(self):

        """

        Close the device if opened. Safe to call multiple times.

        """

        try:

            if self.wm and hasattr(self.wm, "close"):

                self.wm.close()

        finally:

            self._opened = False

            self.wm = None



    def is_opened(self) -> bool:

        """

        Check if device is responsive by calling get_device_info().



        Returns:

            bool: True if device replies, False otherwise.

        """

        try:

            if not self.wm:

                return False

            # Robustness: if device/hardware is gone, this will raise.

            _ = self.wm.get_device_info()

            return True

        except Exception:

            return False



    # -------- convenience helpers (safe guards) --------

    def _require_open(self):

        """

        Raise RuntimeError if the device is not responsive.

        """

        if not self.is_opened():

            raise RuntimeError("HighFinesse WLM is not opened")



    # -------- wrapped capabilities (public methods) --------

    # Device info

    def get_device_info(self) -> Tuple[Any, Any, Any, Any]:

        """

        Get device information tuple.



        Returns:

            tuple: Typically (model, serial, version, details).

        """

        self._require_open()

        return self.wm.get_device_info()



    def get_model(self) -> Any:

        """

        Get device model string/code.



        Returns:

            Any: Model identifier.

        """

        self._require_open()

        model, *_ = self.wm.get_device_info()

        return model



    def get_serial_number(self) -> Any:

        """

        Get device serial number.



        Returns:

            Any: Serial number.

        """

        self._require_open()

        _model, serial, *_ = self.wm.get_device_info()

        return serial



    # Measurement control

    def start_measurement(self) -> None:

        """

        Start data acquisition/measurement on the WLM.

        """

        self._require_open()

        self.wm.start_measurement()



    def stop_measurement(self) -> None:

        """

        Stop data acquisition/measurement on the WLM.

        """

        self._require_open()

        self.wm.stop_measurement()



    def is_measurement_running(self) -> bool:

        """

        Check if a measurement is currently running.



        Returns:

            bool: True if running.

        """

        self._require_open()

        return self.wm.is_measurement_running()



    # Read mode

    def set_read_mode(self, mode: str) -> None:

        """

        Set the WLM read mode.



        Args:

            mode (str): Implementation-specific read mode (e.g., "buffered", "instant", ...).

        """

        self._require_open()

        self.wm.set_read_mode(mode)



    def get_read_mode(self) -> str:

        """

        Get current read mode.



        Returns:

            str: Read mode.

        """

        self._require_open()

        return self.wm.get_read_mode()



    # Channels

    def get_channels_number(self, refresh: bool = True) -> int:

        """

        Get number of channels.



        Args:

            refresh (bool): If True, query hardware; otherwise may use cached value.



        Returns:

            int: Channel count.

        """

        self._require_open()

        return self.wm.get_channels_number(refresh=refresh)



    def get_default_channel(self) -> int:

        """

        Get default channel index.



        Returns:

            int: Default channel.

        """

        self._require_open()

        return self.wm.get_default_channel()



    def set_default_channel(self, channel: int) -> None:

        """

        Set default channel index.



        Args:

            channel (int): Channel number to set as default.

        """

        self._require_open()

        self.wm.set_default_channel(channel)



    # Frequency / Wavelength

    def get_frequency(self, channel: Optional[int] = None,

                      error_on_invalid: bool = True,

                      wait: bool = True, timeout: float = 5.0) -> float:

        """

        Get optical frequency.



        Args:

            channel (int, optional): Channel number to read from (None = default).

            error_on_invalid (bool): Raise if value is invalid.

            wait (bool): Wait until a valid value is available.

            timeout (float): Max wait time in seconds.



        Returns:

            float: Frequency in Hz.

        """

        self._require_open()

        return self.wm.get_frequency(channel=channel, error_on_invalid=error_on_invalid,

                                     wait=wait, timeout=timeout)



    def get_wavelength(self, channel: Optional[int] = None,

                       error_on_invalid: bool = True,

                       wait: bool = True, timeout: float = 5.0) -> float:

        """

        Get optical wavelength (vacuum).



        Args:

            channel (int, optional): Channel number to read from (None = default).

            error_on_invalid (bool): Raise if value is invalid.

            wait (bool): Wait until a valid value is available.

            timeout (float): Max wait time in seconds.



        Returns:

            float: Wavelength in meters.

        """

        self._require_open()

        return self.wm.get_wavelength(channel=channel, error_on_invalid=error_on_invalid,

                                      wait=wait, timeout=timeout)



    # Exposure

    def get_exposure_mode(self, channel: Optional[int] = None) -> str:

        """

        Get current exposure mode.



        Args:

            channel (int, optional): Channel number (None = default).



        Returns:

            str: Exposure mode string.

        """

        self._require_open()

        return self.wm.get_exposure_mode(channel=channel)



    def set_exposure_mode(self, mode: str = 'auto', channel: Optional[int] = None) -> None:

        """

        Set exposure mode.



        Args:

            mode (str): 'auto' or implementation-specific values.

            channel (int, optional): Channel number (None = default).

        """

        self._require_open()

        self.wm.set_exposure_mode(mode=mode, channel=channel)



    def get_exposure(self, sensor: int = 1, channel: Optional[int] = None) -> Any:

        """

        Get exposure value(s).



        Args:

            sensor (int): Sensor index (often 1 or 2).

            channel (int, optional): Channel number (None = default).



        Returns:

            Any: Exposure level/structure as defined by pylablib.

        """

        self._require_open()

        return self.wm.get_exposure(sensor=sensor, channel=channel)



    def set_exposure(self, exposure: Any, sensor: int = 1, channel: Optional[int] = None) -> None:

        """

        Set exposure value(s).



        Args:

            exposure (Any): Exposure level/structure.

            sensor (int): Sensor index.

            channel (int, optional): Channel number (None = default).

        """

        self._require_open()

        self.wm.set_exposure(exposure, sensor=sensor, channel=channel)



    # Switcher

    def get_switcher_mode(self) -> str:

        """

        Get switcher mode.



        Returns:

            str: Switcher mode string.

        """

        self._require_open()

        return self.wm.get_switcher_mode()



    def set_switcher_mode(self, mode: str = 'on') -> None:

        """

        Set switcher mode.



        Args:

            mode (str): Mode value (e.g., 'on', 'off', ...).

        """

        self._require_open()

        self.wm.set_switcher_mode(mode=mode)



    def get_active_channel(self) -> int:

        """

        Get currently active channel.



        Returns:

            int: Active channel number.

        """

        self._require_open()

        return self.wm.get_active_channel()



    def set_active_channel(self, channel: int, automode: bool = True) -> None:

        """

        Set active channel.



        Args:

            channel (int): Channel number to activate.

            automode (bool): Whether to adapt switcher automatically.

        """

        self._require_open()

        self.wm.set_active_channel(channel, automode=automode)



    def is_switcher_channel_enabled(self, channel: int, automode: bool = True) -> bool:

        """

        Check if a channel is enabled for switcher.



        Args:

            channel (int): Channel number.

            automode (bool): Auto mode flag.



        Returns:

            bool: True if enabled.

        """

        self._require_open()

        return self.wm.is_switcher_channel_enabled(channel, automode=automode)



    def is_switcher_channel_shown(self, channel: int, automode: bool = True) -> bool:

        """

        Check if a channel is shown in switcher rotation.



        Args:

            channel (int): Channel number.

            automode (bool): Auto mode flag.



        Returns:

            bool: True if shown.

        """

        self._require_open()

        return self.wm.is_switcher_channel_shown(channel, automode=automode)



    def enable_switcher_channel(self, channel: int, enable: bool = True,

                                show: Optional[bool] = None, automode: bool = True) -> None:

        """

        Enable/disable a switcher channel and optionally show it.



        Args:

            channel (int): Channel number.

            enable (bool): Enable or disable the channel.

            show (bool, optional): Whether to include the channel in rotation.

            automode (bool): Auto mode flag.

        """

        self._require_open()

        self.wm.enable_switcher_channel(channel, enable=enable, show=show, automode=automode)



    # Pulse / precision

    def get_pulse_mode(self) -> str:

        """

        Get pulse mode.



        Returns:

            str: Pulse mode string.

        """

        self._require_open()

        return self.wm.get_pulse_mode()



    def set_pulse_mode(self, mode: str) -> None:

        """

        Set pulse mode.



        Args:

            mode (str): Pulse mode value.

        """

        self._require_open()

        self.wm.set_pulse_mode(mode)



    def get_precision_mode(self) -> str:

        """

        Get precision mode.



        Returns:

            str: Precision mode string.

        """

        self._require_open()

        return self.wm.get_precision_mode()



    def set_precision_mode(self, mode: str) -> None:

        """

        Set precision mode.



        Args:

            mode (str): Precision mode value.

        """

        self._require_open()

        self.wm.set_precision_mode(mode)



    # Measurement interval

    def get_measurement_interval(self):

        """

        Get measurement interval setting.



        Returns:

            Any: Interval structure/value as defined by pylablib.

        """

        self._require_open()

        return self.wm.get_measurement_interval()



    def set_measurement_interval(self, interval=None) -> None:

        """

        Set measurement interval.



        Args:

            interval (Any): Interval structure/value as defined by pylablib.

        """

        self._require_open()

        self.wm.set_measurement_interval(interval=interval)



    # Calibration

    def calibrate(self, source_type: str, source_frequency: float,

                  channel: Optional[int] = None) -> None:

        """

        Perform calibration using a known source.



        Args:

            source_type (str): Calibration source type.

            source_frequency (float): Known source frequency (Hz).

            channel (int, optional): Channel to calibrate (None = default).

        """

        self._require_open()

        self.wm.calibrate(source_type, source_frequency, channel=channel)



    def get_autocalibration_parameters(self):

        """

        Get autocalibration parameters.



        Returns:

            Any: Parameters structure/value as defined by pylablib.

        """

        self._require_open()

        return self.wm.get_autocalibration_parameters()



    def setup_autocalibration(self, enable: bool = True,

                              unit: Optional[str] = None,

                              period: Optional[Any] = None) -> None:

        """

        Configure autocalibration.



        Args:

            enable (bool): Enable/disable autocalibration.

            unit (str, optional): Period unit (implementation-specific).

            period (Any, optional): Period value/structure.

        """

        self._require_open()

        self.wm.setup_autocalibration(enable=enable, unit=unit, period=period)



    # Settings / variables

    def apply_settings(self, settings: dict) -> None:

        """

        Apply a dictionary of device settings.



        Args:

            settings (dict): Settings to apply.

        """

        self._require_open()

        self.wm.apply_settings(settings)



    def get_device_variable(self, key: str):

        """

        Get a device variable by key.



        Args:

            key (str): Variable key.



        Returns:

            Any: Value associated with the key.

        """

        self._require_open()

        return self.wm.get_device_variable(key)



    def set_device_variable(self, key: str, value: Any) -> None:

        """

        Set a device variable.



        Args:

            key (str): Variable key.

            value (Any): New value.

        """

        self._require_open()

        self.wm.set_device_variable(key, value)



    def get_full_info(self, include=0) -> dict:

        """

        Get full device info structure.



        Args:

            include (int): Implementation-specific flag for extra details.



        Returns:

            dict: Comprehensive info dictionary.

        """

        self._require_open()

        return self.wm.get_full_info(include=include)



    def get_full_status(self, include=0) -> dict:

        """

        Get full device status structure.



        Args:

            include (int): Implementation-specific flag for extra details.



        Returns:

            dict: Comprehensive status dictionary.

        """

        self._require_open()

        return self.wm.get_full_status(include=include)



    def get_settings(self, include=0) -> dict:

        """

        Get current device settings.



        Args:

            include (int): Implementation-specific flag for extra details.



        Returns:

            dict: Settings dictionary.

        """

        self._require_open()

        return self.wm.get_settings(include=include)

