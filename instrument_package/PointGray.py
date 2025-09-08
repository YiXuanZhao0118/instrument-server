# instrument_package/PointGray.py
"""
FLIR / Point Grey (Spinnaker / PySpin) camera driver wrapper conforming to the
server's required interface:

 - initialize(self) -> dict({"ok": bool, "message": str})
 - shutdown(self) -> None
 - is_opened(self) -> bool

All other public methods are exposed to the RPC/capabilities layer.

Notes:
- Lazy import of PySpin to avoid import errors during driver scanning.
- JSON-friendly image capture: grab_one_image() returns PNG as base64.
- For on-disk saving, use grab_and_save() which returns the absolute file path.
"""

from __future__ import annotations
from typing import Optional, Dict, Any, Tuple
import os
import base64

class PointGrayDriver:
    """
    Driver wrapper around PySpin (Spinnaker) for FLIR/Point Grey cameras.
    """

    def __init__(self, cam_index: int = 0):
        """
        Args:
            cam_index: index of the camera to open from the system camera list.
        """
        self.cam_index = int(cam_index)

        # Lazy-loaded library handle
        self._PySpin = None  # resolved on initialize()

        # Spinnaker objects
        self.system = None
        self.cam_list = None
        self.cam = None
        self.nodemap = None

        # State
        self._opened = False
        self.acquiring = False

    # -------- required interface --------
    def initialize(self) -> Dict[str, Any]:
        """
        Create the Spinnaker system, pick the camera by index, and initialize it.

        Returns:
            dict: {"ok": True/False, "message": "<status or error>"}
        """
        try:
            if self._PySpin is None:
                import PySpin as _PySpin  # type: ignore
                self._PySpin = _PySpin

            PS = self._PySpin
            self.system = PS.System.GetInstance()
            self.cam_list = self.system.GetCameras()
            num_cams = self.cam_list.GetSize()

            if num_cams == 0:
                self._cleanup_partial()
                return {"ok": False, "message": "No cameras found"}

            if self.cam_index < 0 or self.cam_index >= num_cams:
                self._cleanup_partial()
                return {
                    "ok": False,
                    "message": f"Invalid camera index {self.cam_index}; detected {num_cams} camera(s)"
                }

            self.cam = self.cam_list[self.cam_index]
            self.cam.Init()
            self.nodemap = self.cam.GetNodeMap()

            self._opened = True

            # Final verification
            if not self.is_opened():
                self.shutdown()
                return {"ok": False, "message": "Camera did not respond after Init()"}

            # Try to fetch minimal device info for the message
            info = self.get_device_info(safe=True)
            info_str = ""
            if info:
                model = info.get("DeviceModelName", "?")
                sn = info.get("DeviceSerialNumber", "?")
                info_str = f" ({model}, S/N={sn})"

            return {"ok": True, "message": f"PointGrey/FLIR camera initialized at index {self.cam_index}{info_str}"}
        except Exception as e:
            self.shutdown()
            return {"ok": False, "message": f"{type(e).__name__}: {e}"}

    def shutdown(self) -> None:
        """
        End acquisition if needed, de-initialize the camera, release the system.
        Safe to call multiple times.
        """
        try:
            PS = self._PySpin

            # Stop acquisition if running
            try:
                if self.cam and self.acquiring:
                    self.cam.EndAcquisition()
            except Exception:
                pass
            self.acquiring = False

            # DeInit camera
            try:
                if self.cam:
                    # Some versions offer IsInitialized()
                    if hasattr(self.cam, "IsInitialized"):
                        if self.cam.IsInitialized():
                            self.cam.DeInit()
                    else:
                        # Best-effort DeInit
                        self.cam.DeInit()
            except Exception:
                pass

            # Release camera list
            try:
                if self.cam_list is not None:
                    self.cam_list.Clear()
            except Exception:
                pass

            # Release system
            try:
                if self.system is not None and PS is not None:
                    self.system.ReleaseInstance()
            except Exception:
                pass
        finally:
            self._opened = False
            self.nodemap = None
            self.cam = None
            self.cam_list = None
            self.system = None

    def is_opened(self) -> bool:
        """
        Returns:
            bool: True if camera is initialized and responsive.
        """
        try:
            if not self.cam:
                return False
            if hasattr(self.cam, "IsInitialized"):
                return bool(self.cam.IsInitialized())
            # Fallback probe: attempt to access nodemap
            _ = self.nodemap or self.cam.GetNodeMap()
            return True
        except Exception:
            return False

    # -------- internal helper --------
    def _require_open(self) -> None:
        if not self.is_opened():
            raise RuntimeError("FLIR/Point Grey camera is not opened")

    def _nm(self):
        """Convenience accessor for the current nodemap with availability check."""
        self._require_open()
        return self.nodemap

    def _cleanup_partial(self) -> None:
        """Partial cleanup used when initialize() fails early."""
        try:
            if self.cam_list is not None:
                self.cam_list.Clear()
        except Exception:
            pass
        try:
            if self.system is not None and self._PySpin is not None:
                self.system.ReleaseInstance()
        except Exception:
            pass
        self.cam_list = None
        self.system = None

    # -------- device info --------
    def get_device_info(self, safe: bool = False) -> Dict[str, Any]:
        """
        Read common GenICam TL Device string nodes.

        Args:
            safe: if True, swallow errors and return {} on failure.

        Returns:
            dict with available keys among:
            DeviceVendorName, DeviceModelName, DeviceSerialNumber,
            DeviceVersion, DeviceFirmwareVersion
        """
        PS = self._PySpin
        try:
            self._require_open()
            tldev = self.cam.GetTLDeviceNodeMap()
            keys = [
                "DeviceVendorName", "DeviceModelName", "DeviceSerialNumber",
                "DeviceVersion", "DeviceFirmwareVersion"
            ]
            out: Dict[str, Any] = {}
            for k in keys:
                node = PS.CStringPtr(tldev.GetNode(k))
                if node and PS.IsAvailable(node) and PS.IsReadable(node):
                    out[k] = node.GetValue()
            return out
        except Exception:
            if safe:
                return {}
            raise

    # -------- acquisition control --------
    def begin_acquisition(self) -> None:
        """Start acquisition if not already running."""
        self._require_open()
        if self.acquiring:
            return
        self.cam.BeginAcquisition()
        self.acquiring = True

    def start_acquisition(self) -> None:
        """Alias of begin_acquisition()."""
        self.begin_acquisition()

    def end_acquisition(self) -> None:
        """Stop acquisition if running."""
        self._require_open()
        if not self.acquiring:
            return
        self.cam.EndAcquisition()
        self.acquiring = False

    def stop_acquisition(self) -> None:
        """Alias of end_acquisition()."""
        self.end_acquisition()

    # -------- trigger configuration --------
    def set_trigger_mode(self, enable: bool = False, trigger_source: str = "Software") -> None:
        """
        Enable/disable trigger mode and set trigger source.

        Args:
            enable: True to enable trigger mode
            trigger_source: e.g. "Software", "Line0", "Line1", ...
        """
        self._require_open()
        PS = self._PySpin
        nm = self._nm()

        trig_mode = PS.CEnumerationPtr(nm.GetNode("TriggerMode"))
        trig_src = PS.CEnumerationPtr(nm.GetNode("TriggerSource"))
        if not (trig_mode and trig_src):
            raise RuntimeError("Trigger nodes not found")

        # Off/On entries
        off_entry = trig_mode.GetEntryByName("Off")
        on_entry = trig_mode.GetEntryByName("On")
        trig_mode.SetIntValue(off_entry.GetValue())

        if enable:
            src_entry = trig_src.GetEntryByName(trigger_source)
            if not (src_entry and PS.IsAvailable(src_entry) and PS.IsReadable(src_entry)):
                raise RuntimeError(f"TriggerSource '{trigger_source}' not available")

            trig_src.SetIntValue(src_entry.GetValue())

            selector = PS.CEnumerationPtr(nm.GetNode("TriggerSelector"))
            if selector and PS.IsAvailable(selector) and PS.IsWritable(selector):
                fs = selector.GetEntryByName("FrameStart")
                if fs and PS.IsAvailable(fs) and PS.IsReadable(fs):
                    selector.SetIntValue(fs.GetValue())

            trig_mode.SetIntValue(on_entry.GetValue())

    def set_trigger_activation(self, activation: str = "RisingEdge") -> None:
        """Set TriggerActivation: 'RisingEdge', 'FallingEdge', ..."""
        self._require_open()
        PS = self._PySpin
        nm = self._nm()

        trig_mode = PS.CEnumerationPtr(nm.GetNode("TriggerMode"))
        if trig_mode:
            curr = trig_mode.GetCurrentEntry().GetSymbolic() if trig_mode.GetCurrentEntry() else None
            if curr != "On":
                raise RuntimeError("TriggerMode is not ON")

        act = PS.CEnumerationPtr(nm.GetNode("TriggerActivation"))
        if not (act and PS.IsAvailable(act) and PS.IsWritable(act)):
            raise RuntimeError("TriggerActivation not accessible")

        entry = act.GetEntryByName(activation)
        if not (entry and PS.IsAvailable(entry) and PS.IsReadable(entry)):
            raise RuntimeError(f"TriggerActivation '{activation}' not available")

        act.SetIntValue(entry.GetValue())

    def execute_software_trigger(self) -> None:
        """Execute software trigger when TriggerSource is Software."""
        self._require_open()
        PS = self._PySpin
        nm = self._nm()
        cmd = PS.CCommandPtr(nm.GetNode("TriggerSoftware"))
        if not (cmd and PS.IsAvailable(cmd) and PS.IsWritable(cmd)):
            raise RuntimeError("TriggerSoftware command not available")
        cmd.Execute()

    # -------- acquisition / image format --------
    def set_acquisition_mode(self, mode: str = "Continuous") -> None:
        """Set AcquisitionMode: 'Continuous', 'SingleFrame', 'MultiFrame'."""
        self._require_open()
        PS = self._PySpin
        nm = self._nm()
        acq = PS.CEnumerationPtr(nm.GetNode("AcquisitionMode"))
        if not (acq and PS.IsAvailable(acq) and PS.IsWritable(acq)):
            raise RuntimeError("AcquisitionMode not accessible")
        entry = acq.GetEntryByName(mode)
        if not (entry and PS.IsAvailable(entry) and PS.IsReadable(entry)):
            raise RuntimeError(f"Acquisition mode '{mode}' not available")
        acq.SetIntValue(entry.GetValue())

    def set_pixel_format(self, pixel_format: str = "Mono8") -> None:
        """Set PixelFormat: e.g. 'Mono8', 'RGB8', 'BayerRG8'."""
        self._require_open()
        PS = self._PySpin
        nm = self._nm()
        enum_node = PS.CEnumerationPtr(nm.GetNode("PixelFormat"))
        if not (enum_node and PS.IsAvailable(enum_node) and PS.IsWritable(enum_node)):
            raise RuntimeError("PixelFormat not accessible")
        target = enum_node.GetEntryByName(pixel_format)
        if not (target and PS.IsAvailable(target) and PS.IsReadable(target)):
            raise RuntimeError(f"PixelFormat '{pixel_format}' not available")
        enum_node.SetIntValue(target.GetValue())

    # -------- exposure / gain / gamma / black level --------
    def set_exposure_mode(self, mode: str = "Timed") -> None:
        """Set ExposureMode: 'Timed' or 'TriggerWidth' if supported."""
        self._require_open()
        PS = self._PySpin
        nm = self._nm()
        exp_mode = PS.CEnumerationPtr(nm.GetNode("ExposureMode"))
        if not (exp_mode and PS.IsAvailable(exp_mode) and PS.IsWritable(exp_mode)):
            raise RuntimeError("ExposureMode not accessible")
        entry = exp_mode.GetEntryByName(mode)
        if not (entry and PS.IsAvailable(entry) and PS.IsReadable(entry)):
            raise RuntimeError(f"Exposure mode '{mode}' not available")
        exp_mode.SetIntValue(entry.GetValue())

    def set_auto_exposure(self, mode: str = "Continuous") -> None:
        """Set ExposureAuto: 'Off', 'Once', 'Continuous'."""
        self._require_open()
        PS = self._PySpin
        nm = self._nm()
        exp_auto = PS.CEnumerationPtr(nm.GetNode("ExposureAuto"))
        if not (exp_auto and PS.IsAvailable(exp_auto) and PS.IsWritable(exp_auto)):
            raise RuntimeError("ExposureAuto not accessible")
        entry = exp_auto.GetEntryByName(mode)
        if not (entry and PS.IsAvailable(entry) and PS.IsReadable(entry)):
            raise RuntimeError(f"ExposureAuto '{mode}' not available")
        exp_auto.SetIntValue(entry.GetValue())

    def set_exposure_time(self, exposure_time_us: float = 20000.0) -> float:
        """
        Turn ExposureAuto off and set ExposureTime (microseconds).

        Returns:
            The clamped exposure time actually set (µs).
        """
        self._require_open()
        PS = self._PySpin
        nm = self._nm()

        # Turn auto exposure off
        exp_auto = PS.CEnumerationPtr(nm.GetNode("ExposureAuto"))
        if exp_auto and PS.IsAvailable(exp_auto) and PS.IsWritable(exp_auto):
            off_entry = exp_auto.GetEntryByName("Off")
            if off_entry and PS.IsAvailable(off_entry) and PS.IsReadable(off_entry):
                exp_auto.SetIntValue(off_entry.GetValue())

        exp = PS.CFloatPtr(nm.GetNode("ExposureTime"))
        if not (exp and PS.IsAvailable(exp) and PS.IsWritable(exp)):
            raise RuntimeError("ExposureTime not accessible")

        val = float(exposure_time_us)
        val = max(min(val, exp.GetMax()), exp.GetMin())
        exp.SetValue(val)
        return val

    def set_gain(self, gain_db: float = 24.0) -> float:
        """
        Turn GainAuto off and set Gain (dB).

        Returns:
            The clamped gain actually set (dB).
        """
        self._require_open()
        PS = self._PySpin
        nm = self._nm()

        gain_auto = PS.CEnumerationPtr(nm.GetNode("GainAuto"))
        if gain_auto and PS.IsAvailable(gain_auto) and PS.IsWritable(gain_auto):
            off_entry = gain_auto.GetEntryByName("Off")
            if off_entry and PS.IsAvailable(off_entry) and PS.IsReadable(off_entry):
                gain_auto.SetIntValue(off_entry.GetValue())

        gain = PS.CFloatPtr(nm.GetNode("Gain"))
        if not (gain and PS.IsAvailable(gain) and PS.IsWritable(gain)):
            raise RuntimeError("Gain not accessible")

        val = float(gain_db)
        val = max(min(val, gain.GetMax()), gain.GetMin())
        gain.SetValue(val)
        return val

    def set_gamma(self, gamma_value: float = 1.25) -> float:
        """Set Gamma value if supported; returns the clamped value."""
        self._require_open()
        PS = self._PySpin
        nm = self._nm()
        gamma = PS.CFloatPtr(nm.GetNode("Gamma"))
        if not (gamma and PS.IsAvailable(gamma) and PS.IsWritable(gamma)):
            raise RuntimeError("Gamma not accessible")
        val = float(gamma_value)
        val = max(min(val, gamma.GetMax()), gamma.GetMin())
        gamma.SetValue(val)
        return val

    def set_black_level(self, black_level_value: float = 0.0) -> float:
        """Set BlackLevel; turns BlackLevelAuto off if present. Returns clamped value."""
        self._require_open()
        PS = self._PySpin
        nm = self._nm()

        bl_auto = PS.CEnumerationPtr(nm.GetNode("BlackLevelAuto"))
        if bl_auto and PS.IsAvailable(bl_auto) and PS.IsWritable(bl_auto):
            off_entry = bl_auto.GetEntryByName("Off")
            if off_entry and PS.IsAvailable(off_entry) and PS.IsReadable(off_entry):
                bl_auto.SetIntValue(off_entry.GetValue())

        bl = PS.CFloatPtr(nm.GetNode("BlackLevel"))
        if not (bl and PS.IsAvailable(bl) and PS.IsWritable(bl)):
            raise RuntimeError("BlackLevel not accessible")

        val = float(black_level_value)
        val = max(min(val, bl.GetMax()), bl.GetMin())
        bl.SetValue(val)
        return val

    # -------- bandwidth / ROI --------
    def set_device_throughput(self, throughput_bps: int = 67_680_000) -> int:
        """
        Enable DeviceLinkThroughputLimitMode=On and set DeviceLinkThroughputLimit.
        Returns the clamped throughput value applied.
        """
        self._require_open()
        PS = self._PySpin
        nm = self._nm()

        mode = PS.CEnumerationPtr(nm.GetNode("DeviceLinkThroughputLimitMode"))
        if not (mode and PS.IsAvailable(mode) and PS.IsWritable(mode)):
            raise RuntimeError("DeviceLinkThroughputLimitMode not accessible")
        on_entry = mode.GetEntryByName("On")
        if not (on_entry and PS.IsAvailable(on_entry) and PS.IsReadable(on_entry)):
            raise RuntimeError("Cannot set DeviceLinkThroughputLimitMode to 'On'")
        mode.SetIntValue(on_entry.GetValue())

        thr = PS.CIntegerPtr(nm.GetNode("DeviceLinkThroughputLimit"))
        if not (thr and PS.IsAvailable(thr) and PS.IsWritable(thr)):
            raise RuntimeError("DeviceLinkThroughputLimit not accessible")

        val = int(throughput_bps)
        val = max(min(val, thr.GetMax()), thr.GetMin())
        thr.SetValue(val)
        return val

    def set_image_size(self, width: int, height: int, offset_x: int = 0, offset_y: int = 0) -> Tuple[int, int, int, int]:
        """
        Configure ROI: Width, Height, OffsetX, OffsetY (auto width/height disabled).

        Returns:
            (width, height, offset_x, offset_y) after clamping.
        """
        self._require_open()
        PS = self._PySpin
        nm = self._nm()

        # Disable autos
        w_auto = PS.CEnumerationPtr(nm.GetNode("WidthAuto"))
        if w_auto and PS.IsAvailable(w_auto) and PS.IsWritable(w_auto):
            off = w_auto.GetEntryByName("Off")
            if off:
                w_auto.SetIntValue(off.GetValue())
        h_auto = PS.CEnumerationPtr(nm.GetNode("HeightAuto"))
        if h_auto and PS.IsAvailable(h_auto) and PS.IsWritable(h_auto):
            off = h_auto.GetEntryByName("Off")
            if off:
                h_auto.SetIntValue(off.GetValue())

        ox = PS.CIntegerPtr(nm.GetNode("OffsetX"))
        oy = PS.CIntegerPtr(nm.GetNode("OffsetY"))
        w = PS.CIntegerPtr(nm.GetNode("Width"))
        h = PS.CIntegerPtr(nm.GetNode("Height"))

        if ox and PS.IsAvailable(ox): offset_x = min(int(offset_x), ox.GetMax())
        if oy and PS.IsAvailable(oy): offset_y = min(int(offset_y), oy.GetMax())
        if w and PS.IsAvailable(w):   width    = min(int(width), w.GetMax())
        if h and PS.IsAvailable(h):   height   = min(int(height), h.GetMax())

        if ox and PS.IsWritable(ox): ox.SetValue(offset_x)
        if oy and PS.IsWritable(oy): oy.SetValue(offset_y)
        if w and PS.IsWritable(w):   w.SetValue(width)
        if h and PS.IsWritable(h):   h.SetValue(height)

        return int(width), int(height), int(offset_x), int(offset_y)

    # -------- image grabbing --------
    def grab_one_image(self, timeout_ms: int = 1000, fmt: str = "png") -> Dict[str, Any]:
        """
        Grab a single image and return JSON-friendly data:
        { "ok": bool, "shape": [h, w] or [h, w, c], "dtype": str, "<fmt>_base64": str }

        Notes:
        - Requires acquisition to be started (begin_acquisition()).
        - Encodes image to PNG/JPEG using OpenCV without persistent dependency at import time.
        """
        self._require_open()
        if not self.acquiring:
            raise RuntimeError("Not acquiring; call begin_acquisition() first")

        PS = self._PySpin
        try:
            img = self.cam.GetNextImage(timeout_ms)
            try:
                if img.IsIncomplete():
                    return {"ok": False, "message": "Incomplete image"}

                frame = img.GetNDArray()  # numpy array
                shape = list(frame.shape)
                dtype = str(frame.dtype)

            finally:
                img.Release()
        except PS.SpinnakerException as ex:  # type: ignore[attr-defined]
            return {"ok": False, "message": f"SpinnakerException: {ex}"}

        # Lazy import cv2 only when needed
        try:
            import cv2  # type: ignore
        except Exception as e:
            return {"ok": False, "message": f"OpenCV not available: {e}"}

        # Encode to desired format
        ext = ".png" if fmt.lower() == "png" else ".jpg"
        success, buf = cv2.imencode(ext, frame)
        if not success:
            return {"ok": False, "message": "cv2.imencode failed"}

        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        key = f"{fmt.lower()}_base64"
        return {"ok": True, "shape": shape, "dtype": dtype, key: b64}

    def grab_and_save(self, filename: str = "output.png", timeout_ms: int = 1000) -> str:
        """
        Grab a single image and save to disk.

        Args:
            filename: absolute or relative path (relative to this file's directory)

        Returns:
            Absolute saved file path
        """
        data = self.grab_one_image(timeout_ms=timeout_ms, fmt="png")
        if not data.get("ok"):
            raise RuntimeError(f"grab_one_image failed: {data.get('message')}")

        png_b64 = data.get("png_base64")
        if not png_b64:
            raise RuntimeError("PNG base64 missing from grab_one_image result")

        raw = base64.b64decode(png_b64.encode("ascii"))
        # Resolve absolute path
        if not os.path.isabs(filename):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            filename = os.path.join(base_dir, filename)
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, "wb") as f:
            f.write(raw)
        return os.path.abspath(filename)
