# instrument_package/SpinCore.py
"""
SpinCore PulseBlaster driver wrapper that conforms to the server's required interface:
 - initialize(self) -> dict({"ok": bool, "message": str})
 - shutdown(self) -> None
 - is_opened(self) -> bool

Public methods are exposed to RPC/capabilities.
"""

from typing import Any, Dict, List, Optional


class SpinCoreDriver:
    """
    SpinCore PulseBlaster wrapper.

    This class provides a safe, uniform interface to SpinAPI for the instrument server:
      - `initialize()` selects the board, initializes it and sets the core clock.
      - `shutdown()` stops any running program and closes the library if supported.
      - `is_opened()` robustly probes the board by calling harmless SpinAPI queries
        (e.g. `pb_get_version()` / `pb_get_firmware_id()`).

    Args:
        board_number (int): board index (0-based).
        core_clock_mhz (float): core clock in MHz (e.g., 100.0).
        debug (int): spinapi debug level (0: off).
    """

    def __init__(self, board_number: int = 0, core_clock_mhz: float = 100.0, debug: int = 0):
        self.board_number = int(board_number)
        self.core_clock_mhz = float(core_clock_mhz)
        self.debug = int(debug)

        self._spin = None          # resolved on initialize()
        self._opened = False
        self._last_error = ""

    # ---------------- Required interface ----------------
    def initialize(self):
        """
        Initialize the PulseBlaster board.

        Returns:
            dict: {"ok": True/False, "message": "..."}
        """
        try:
            # Lazy import, so merely importing this module won't require spinapi.
            import spinapi as _spin
            self._spin = _spin

            # Debug level (0 = off)
            if hasattr(self._spin, "pb_set_debug"):
                self._spin.pb_set_debug(self.debug)

            # Count boards
            count = self._spin.pb_count_boards()
            if count is None or count < 1:
                msg = f"No SpinCore boards found (count={count})."
                self._last_error = msg
                return {"ok": False, "message": msg}

            # Select board and init
            self._spin.pb_select_board(self.board_number)
            rv = self._spin.pb_init()
            if rv != 0:
                msg = self._format_error("pb_init failed")
                self._last_error = msg
                return {"ok": False, "message": msg}

            # Set core clock
            self._spin.pb_core_clock(self.core_clock_mhz)

            # Mark opened, then verify with a probe
            self._opened = True
            if not self.is_opened():
                self._opened = False
                return {"ok": False, "message": "SpinCore board did not respond after initialize()"}

            return {"ok": True, "message": f"SpinCore board {self.board_number} initialized @ {self.core_clock_mhz} MHz"}

        except Exception as e:
            self._opened = False
            self._last_error = f"{type(e).__name__}: {e}"
            return {"ok": False, "message": self._last_error}

    def shutdown(self):
        """
        Stop and close the board. Safe to call multiple times.
        """
        try:
            if self._spin:
                # Stop any running program
                try:
                    if hasattr(self._spin, "pb_stop"):
                        self._spin.pb_stop()
                except Exception:
                    pass
                # Close driver/library if available
                if hasattr(self._spin, "pb_close"):
                    try:
                        self._spin.pb_close()
                    except Exception:
                        pass
        finally:
            self._opened = False
            self._spin = None

    def is_opened(self) -> bool:
        """
        Return True if the device is responsive.

        Strategy:
          1) Ensure library and opened flag are present.
          2) Re-select the board (harmless).
          3) Probe a harmless getter such as `pb_get_version()`; if unavailable,
             try `pb_get_firmware_id()` or `pb_get_version_info()`.

        Returns:
            bool: True if SpinAPI replies without raising.
        """
        try:
            if not self._opened or not self._spin:
                return False

            # Must be able to select current board without raising.
            if hasattr(self._spin, "pb_select_board"):
                self._spin.pb_select_board(self.board_number)

            # Prefer pb_get_version(); fall back to other harmless getters.
            probed = False
            if hasattr(self._spin, "pb_get_version"):
                _ = self._spin.pb_get_version()
                probed = True
            elif hasattr(self._spin, "pb_get_firmware_id"):
                _ = self._spin.pb_get_firmware_id()
                probed = True
            elif hasattr(self._spin, "pb_get_version_info"):
                _ = self._spin.pb_get_version_info()
                probed = True

            # If none of the getters exist, we still consider the board opened
            # after init; however, most SpinAPI builds provide at least one.
            return True if probed else True
        except Exception:
            return False

    # ---------------- Helpers ----------------
    def _format_error(self, prefix: str = "") -> str:
        """
        Fetch last spinapi error string and format with a prefix.

        Returns:
            str: formatted error text (may be empty).
        """
        msg = ""
        try:
            if self._spin and hasattr(self._spin, "pb_get_error"):
                err = self._spin.pb_get_error()
                # pb_get_error() may return bytes or str depending on binding version
                if isinstance(err, bytes):
                    msg = err.decode(errors="ignore")
                elif isinstance(err, str):
                    msg = err
        except Exception:
            pass
        return f"{prefix}: {msg}" if prefix else msg

    def _require_open(self):
        """
        Raise RuntimeError if the device is not responsive.
        """
        if not self.is_opened():
            raise RuntimeError("SpinCore board is not initialized/opened")

    def _get_attr(self, name: str):
        """
        Get an attribute/constant/function from spinapi, raising if missing.
        """
        if not self._spin or not hasattr(self._spin, name):
            raise AttributeError(f"spinapi has no attribute '{name}'")
        return getattr(self._spin, name)

    def _resolve_time_scale(self, scale: Any) -> float:
        """
        Resolve a time scale to seconds multiplier.

        Accepts:
          - "ns"/"us"/"ms"/"s" (spinapi attributes with numeric values)
          - numeric (float seconds multiplier)

        Returns:
            float: seconds multiplier.
        """
        if isinstance(scale, (int, float)):
            return float(scale)
        if isinstance(scale, str):
            s = scale.strip()
            if self._spin and hasattr(self._spin, s):
                val = getattr(self._spin, s)
                try:
                    return float(val)
                except Exception:
                    pass
        raise ValueError(f"Invalid time scale: {scale}")

    def _bits_to_flags(self, bits: List[int]) -> int:
        """
        Convert up to 24 bits list (len <= 24) to PulseBlaster 'flags' integer.

        The list is interpreted as [bit0, bit1, ..., bit23] (LSB first).
        If your UI builds [b0..b23], pass directly; if you build MSB->LSB, reverse before.

        Returns:
            int: flags.
        """
        if not bits:
            return 0
        if len(bits) > 24:
            raise ValueError("Sequence 'sequence' length must be <= 24")
        flags = 0
        for i, b in enumerate(bits):
            if b not in (0, 1, False, True):
                raise ValueError(f"Invalid bit value at index {i}: {b}")
            if int(b):
                flags |= (1 << i)
        return flags

    # ---------------- Public info method ----------------
    def get_board_info(self) -> Dict[str, Any]:
        """
        Return basic board/library information for diagnostics.

        Returns:
            dict: {"board_number", "version", "firmware_id", "board_count"}
        """
        self._require_open()
        info: Dict[str, Any] = {"board_number": self.board_number}
        try:
            if hasattr(self._spin, "pb_get_version"):
                info["version"] = self._spin.pb_get_version()
        except Exception:
            info["version"] = None
        try:
            if hasattr(self._spin, "pb_get_firmware_id"):
                info["firmware_id"] = self._spin.pb_get_firmware_id()
        except Exception:
            info["firmware_id"] = None
        try:
            info["board_count"] = self._spin.pb_count_boards()
        except Exception:
            info["board_count"] = None
        return info

    # ---------------- Public control methods ----------------
    def reset(self):
        """pb_reset()"""
        self._require_open()
        self._spin.pb_reset()

    def start(self):
        """pb_start()"""
        self._require_open()
        self._spin.pb_start()

    def stop(self):
        """pb_stop()"""
        self._require_open()
        self._spin.pb_stop()

    def start_programming(self):
        """pb_start_programming(PULSE_PROGRAM)"""
        self._require_open()
        mode = self._get_attr("PULSE_PROGRAM")
        self._spin.pb_start_programming(mode)

    def stop_programming(self):
        """pb_stop_programming()"""
        self._require_open()
        self._spin.pb_stop_programming()

    def get_last_error(self) -> str:
        """Return last cached error string (if any)."""
        return self._last_error

    # ---------------- Program execution ----------------
    def execute(self, data: List[Dict[str, Any]], *, auto_run: bool = True, reset_before: bool = True) -> List[int]:
        """
        Compile and (optionally) run a PulseBlaster program.

        Each item in 'data' must be a dict with keys:
          - "sequence": list of <=24 ints (0/1), bit0..bit23 (LSB first).
                        (If your list is MSB->LSB, reverse it before passing.)
          - "sequence type": spinapi instruction name, e.g. "WAIT", "CONTINUE", "BRANCH", "LOOP", ...
          - "sequence times": instruction data (e.g., loop count / branch addr / etc.)
          - "time range": length (float)
          - "time scale": time unit, one of "ns"/"us"/"ms"/"s", or a numeric seconds multiplier

        Returns:
            List[int]: instruction handles returned by pb_inst_pbonly.
        """
        self._require_open()

        seq_handles: List[int] = []

        # Stop any running program
        try:
            self._spin.pb_stop()
        except Exception:
            pass

        # Start programming
        self.start_programming()

        try:
            for idx, step in enumerate(data):
                # Validate fields
                if "sequence" not in step:
                    raise ValueError(f"Missing 'sequence' in step {idx}")
                if "sequence type" not in step:
                    raise ValueError(f"Missing 'sequence type' in step {idx}")
                if "sequence times" not in step:
                    raise ValueError(f"Missing 'sequence times' in step {idx}")
                if "time range" not in step:
                    raise ValueError(f"Missing 'time range' in step {idx}")
                if "time scale" not in step:
                    raise ValueError(f"Missing 'time scale' in step {idx}")

                bits: List[int] = list(step["sequence"])
                flags = self._bits_to_flags(bits)

                inst_name: str = str(step["sequence type"]).strip()
                inst_const = self._get_attr(inst_name)

                inst_data = int(step["sequence times"])

                t_range = float(step["time range"])
                scale = self._resolve_time_scale(step["time scale"])
                length = t_range * scale  # seconds

                handle = self._spin.pb_inst_pbonly(flags, inst_const, inst_data, length)
                seq_handles.append(int(handle))

        except Exception as e:
            # Abort programming on error
            try:
                self._spin.pb_stop_programming()
            except Exception:
                pass
            # Keep last error (include spinapi error text if present)
            spin_err = self._format_error()
            if spin_err:
                self._last_error = f"{type(e).__name__}: {e} | spinapi: {spin_err}"
            else:
                self._last_error = f"{type(e).__name__}: {e}"
            raise

        # Finish programming
        self.stop_programming()

        # Reset and start if requested
        if reset_before:
            self._spin.pb_reset()
        if auto_run:
            self._spin.pb_start()

        # Log any library-side error (non-fatal)
        try:
            err = self._format_error()
            if err:
                self._last_error = err
        except Exception:
            pass

        return seq_handles

    # ---------------- Example utility: execute with MSB->LSB input ----------------
    def execute_msb_sequence(self, data: List[Dict[str, Any]], **kwargs) -> List[int]:
        """
        Same as execute(), but assumes each 'sequence' list is MSB->LSB (bit23..bit0),
        and reverses it for you before programming.
        """
        converted: List[Dict[str, Any]] = []
        for step in data:
            step2 = dict(step)
            if "sequence" in step2 and isinstance(step2["sequence"], list):
                step2["sequence"] = list(step2["sequence"])[::-1]
            converted.append(step2)
        return self.execute(converted, **kwargs)


# ---------------- Standalone test ----------------
if __name__ == "__main__":
    # Minimal smoke test; requires spinapi and a connected board.
    prog = [
        {
            "sequence type": "WAIT",
            "sequence times": 0,
            "time range": 550.0,
            "time scale": "us",
            # MSB->LSB example (24 bits), will use execute_msb_sequence to auto-reverse:
            "sequence": [0,1,1,1,0,1,1,1,0,0,1,1,0,0,0,0,0,0,0,0,0,1,0,1]
        },
        {
            "sequence type": "CONTINUE",
            "sequence times": 0,
            "time range": 11.0,
            "time scale": "ms",
            "sequence": [1,1,1,1,0,1,1,0,1,0,1,1,0,0,0,0,0,0,0,0,0,1,0,1]
        },
        {
            "sequence type": "BRANCH",
            "sequence times": 0,
            "time range": 500.0,
            "time scale": "us",
            "sequence": [0,1,1,1,0,1,1,0,1,0,1,1,0,0,0,0,0,1,0,1,1,1,0,1]
        }
    ]

    dev = SpinCoreDevice(board_number=0, core_clock_mhz=100, debug=0)
    info = dev.initialize()
    print("initialize:", info)
    if info.get("ok"):
        try:
            print("board info:", dev.get_board_info())
            handles = dev.execute_msb_sequence(prog)
            print("handles:", handles)
        finally:
            dev.shutdown()
    else:
        print("Failed to open SpinCore:", info.get("message"))
