# server_api.py
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional, Literal, Tuple
import threading, json, os, ast, importlib, importlib.util, sys, uuid
from dataclasses import dataclass
from contextlib import asynccontextmanager

# ---------- FastAPI ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # === 原本 startup 內容 ===
    refresh_drivers_in_config()
    data = load_config()
    for inst in data["instrument"]:
        try:
            cfg = InstrumentConfig(**inst)
            cfg = _apply_port_magic(cfg)
            if cfg.connect:
                dev = create_device(cfg)
                connect_device(cfg.id, dev)
                module, file, class_name, funcs = drivers_index[cfg.driverId]
                for cap in cfg.capabilities:
                    if cap.driver not in funcs:
                        continue
                    try:
                        getattr(dev, cap.driver)(*cap.args, **cap.kwargs)
                    except Exception as e:
                        errors[cfg.id] = f"Capability '{cap.driver}' failed: {e}"
        except Exception as e:
            errors[inst["id"]] = str(e)
    update_instrument_state_table()
    # ========================
    yield
    # 這裡可以放 shutdown 清理邏輯（可選）
    for inst_id in list(instruments.keys()):
        try:
            disconnect_device(inst_id)
        except Exception:
            pass

app = FastAPI(title="Instrument Server", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 開發期先全開；正式請改白名單
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return RedirectResponse("/docs")

@app.get("/healthz")
def healthz():
    return {"ok": True}

# ---------- Constants / Paths ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "instrument_server.json")
PKG_DIR = os.path.join(BASE_DIR, "instrument_package")
os.makedirs(PKG_DIR, exist_ok=True)

# ---------- Types ----------
InstrumentState = Literal["connected", "connect_fail", "disconnected", "not_found"]

# ---------- Pydantic Models for API ----------
class CapabilityCall(BaseModel):
    driver: str                               # function name on driver class
    args: List[Any] = []
    kwargs: Dict[str, Any] = {}

class InstrumentConfig(BaseModel):
    id: Optional[str] = None
    name: str
    driverId: str
    port: Optional[str] = None
    init_args: List[Any] = []
    init_kwargs: Dict[str, Any] = {}
    connect: bool = True
    capabilities: List[CapabilityCall] = []

class DriverFileIn(BaseModel):
    filename: str                              # e.g. "HighFinesse.py"
    content: str                               # file content

class Command(BaseModel):
    instrument: str                            # id or name
    command: str
    args: List[Any] = []
    kwargs: Dict[str, Any] = {}

# ---------- Runtime State ----------
instruments: Dict[str, Any] = {}               # key: instrumentId -> device instance
locks: Dict[str, threading.Lock] = {}
errors: Dict[str, str] = {}                    # instrumentId -> last error string
by_name_index: Dict[str, str] = {}             # name -> instrumentId

# in-memory driver table (derived from config file)
# driverId -> (module,file,class_name, functions)
drivers_index: Dict[str, Tuple[str, str, str, List[str]]] = {}

# ---------- Utilities: Config I/O ----------
def _default_config() -> Dict[str, Any]:
    return {"driver": [], "instrument": [], "instrument_state": []}

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        return _default_config()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # normalize keys
    data.setdefault("driver", [])
    data.setdefault("instrument", [])
    data.setdefault("instrument_state", [])
    return data

def save_config(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def uuid_for_driver(module: str, class_name: str) -> str:
    """
    Stable driver UUID derived only from the module (filename without .py).
    Renaming the class inside the same file will NOT change this id.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"instrument_package/{module}"))

def uuid_for_instrument(name: str, driver_id: str) -> str:
    # stable-ish if same name+driver; but if you want pure random, use uuid4()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"instrument/{name}#{driver_id}"))

# ---------- Utilities: Driver Scanning (AST, no import execution) ----------

@dataclass
class ParamMeta:
    name: str
    type: Optional[str] = None
    default: Optional[str] = None
    choices: Optional[List[str]] = None      # from Literal[...]

@dataclass
class ScannedFunction:
    name: str
    signature: str
    doc: Optional[str] = None
    params: Optional[List[ParamMeta]] = None

@dataclass
class ScannedDriver:
    file: str
    module: str
    class_name: str
    class_doc: Optional[str]
    init_positional: List[ParamMeta]
    init_keyword: List[ParamMeta]
    functions: List[ScannedFunction]
    id: str


REQUIRED_METHODS = {"initialize", "shutdown", "is_opened"}

def _fn_signature_from_ast(fn: ast.FunctionDef) -> str:
    """Lightweight signature str from AST (without types for brevity)."""
    params = []

    # positional-or-keyword (skip self)
    names = fn.args.args[1:] if fn.args.args else []
    defaults = fn.args.defaults or []
    num_kw = len(defaults)
    split_at = len(names) - num_kw
    for i, a in enumerate(names):
        nm = a.arg
        if i < split_at:
            params.append(nm)
        else:
            d_ast = defaults[i - split_at]
            d_val = _ast_param_default(d_ast) or "..."
            params.append(f"{nm}={d_val}")

    # keyword-only
    kwonly = getattr(fn.args, "kwonlyargs", []) or []
    kwdefs = getattr(fn.args, "kw_defaults", []) or []
    for i, a in enumerate(kwonly):
        nm = a.arg
        d_ast = kwdefs[i] if i < len(kwdefs) else None
        if d_ast is None:
            params.append(f"{nm}=...")
        else:
            d_val = _ast_param_default(d_ast) or "..."
            params.append(f"{nm}={d_val}")

    # *args / **kwargs
    if fn.args.vararg:
        params.append(f"*{fn.args.vararg.arg}")
    if fn.args.kwarg:
        params.append(f"**{fn.args.kwarg.arg}")

    return f"({', '.join(params)})"

def scan_driver_file(path: str) -> List[ScannedDriver]:
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []

    fname = os.path.basename(path)
    module = os.path.splitext(fname)[0]
    out: List[ScannedDriver] = []

    for node in tree.body:
        # accept ANY top-level class as a driver candidate
        if not isinstance(node, ast.ClassDef):
            continue

        class_doc = _ast_get_docstring(node)

        # collect methods
        methods: Dict[str, ast.FunctionDef] = {
            b.name: b for b in node.body if isinstance(b, ast.FunctionDef)
        }

        # must have ALL required methods
        if not all(m in methods for m in REQUIRED_METHODS):
            continue

        # parse __init__ for args meta
        init_pos, init_kw = [], []
        if "__init__" in methods:
            init_pos, init_kw = _split_init_params(methods["__init__"])

        # collect callable functions (excluding required + private)
        fn_list: List[ScannedFunction] = []
        for name, fn in methods.items():
            if name in REQUIRED_METHODS or name.startswith("_"):
                continue
            sig = _fn_signature_from_ast(fn)
            doc = _ast_get_docstring(fn)
            params = _fn_params_from_ast(fn)
            fn_list.append(ScannedFunction(name=name, signature=sig, doc=doc, params=params))

        drv_id = uuid_for_driver(module, node.name)
        out.append(ScannedDriver(
            file=fname, module=module, class_name=node.name,
            class_doc=class_doc,
            init_positional=init_pos, init_keyword=init_kw,
            functions=fn_list, id=drv_id
        ))
    return out

def scan_all_drivers() -> List[ScannedDriver]:
    result: List[ScannedDriver] = []
    for fname in os.listdir(PKG_DIR):
        if not fname.endswith(".py"):
            continue
        if fname == "__init__.py":
            continue
        result.extend(scan_driver_file(os.path.join(PKG_DIR, fname)))
    return result

def refresh_drivers_in_config() -> Dict[str, Any]:
    data = load_config()
    scanned = scan_all_drivers()

    # keep a map of old drivers by module (before refresh)
    old_drivers_by_module = {d.get("module"): d for d in data.get("driver", []) if d.get("module")}

    data["driver"] = []
    drivers_index.clear()

    # build new driver table and an id remap (old_id -> new_id) based on module
    id_remap: Dict[str, str] = {}

    for d in scanned:
        new_id = uuid_for_driver(d.module, d.class_name)  # now module-only
        # register into runtime index
        drivers_index[new_id] = (d.module, d.file, d.class_name, [f.name for f in d.functions])

        # build driver entry written to config
        data["driver"].append({
            "id": new_id,
            "file": d.file,
            "module": d.module,
            "name": d.class_name,
            "function": [f.name for f in d.functions],
            "meta": {
                "class_doc": d.class_doc,
                "init": {
                    "positional_args": [pm.__dict__ for pm in d.init_positional],
                    "keyword_args":   [pm.__dict__ for pm in d.init_keyword],
                },
                "functions": [
                    {
                        "name": f.name,
                        "signature": f.signature,
                        "doc": f.doc,
                        "params": [pm.__dict__ for pm in (f.params or [])]
                    }
                    for f in d.functions
                ]
            }
        })

        # if we had an old driver for the same module, remember to remap
        old_entry = old_drivers_by_module.get(d.module)
        if old_entry:
            old_id = old_entry.get("id")
            if old_id and old_id != new_id:
                id_remap[old_id] = new_id

    # apply id remap to instruments so existing configs keep working
    if id_remap:
        for it in data.get("instrument", []):
            did = it.get("driverId")
            if did in id_remap:
                it["driverId"] = id_remap[did]

    save_config(data)
    return data

# ---------- Utilities: Dynamic Import of a driver (for instantiation) ----------
def import_driver_class(driver_id: str):
    if driver_id not in drivers_index:
        raise HTTPException(400, f"Unknown driverId: {driver_id}")
    module, file, class_name, _funcs = drivers_index[driver_id]

    # Prefer package import if instrument_package is a package
    pkg_modname = f"instrument_package.{module}"
    try:
        mod = importlib.import_module(pkg_modname)
    except Exception:
        # fallback to file-based import
        file_path = os.path.join(PKG_DIR, file)
        spec = importlib.util.spec_from_file_location(pkg_modname, file_path)
        if spec is None or spec.loader is None:
            raise HTTPException(500, f"Cannot load driver file: {file}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[pkg_modname] = mod
        spec.loader.exec_module(mod)
    if not hasattr(mod, class_name):
        raise HTTPException(500, f"Driver class {class_name} not found in {file}")
    return getattr(mod, class_name)

# ---------- Utilities: State helpers ----------
def state_of(instrument_id: str) -> InstrumentState:
    if instrument_id not in instruments:
        return "not_found"
    dev = instruments[instrument_id]
    try:
        opened = bool(getattr(dev, "is_opened", lambda: False)())
        return "connected" if opened else "disconnected"
    except Exception:
        return "connect_fail"

def update_instrument_state_table():
    data = load_config()
    out = []
    # build name->id index as well
    by_name_index.clear()

    for inst in data["instrument"]:
        inst_id = inst["id"]
        by_name_index[inst["name"]] = inst_id

        # 實際狀態：以 is_opened() 為準
        opened = None
        if inst_id in instruments and hasattr(instruments[inst_id], "is_opened"):
            try:
                opened = bool(instruments[inst_id].is_opened())
            except Exception as e:
                errors[inst_id] = str(e)
                opened = False

        # instrument_state: 僅保留你要的欄位，connect 以 opened 為準
        out.append({
            "nameid": inst_id,
            "driverid": inst["driverId"],
            "connect": bool(opened) if opened is not None else False,
            "last_error": errors.get(inst_id)
        })

    data["instrument_state"] = out
    save_config(data)

# ---------- Core: Create / Connect device ----------
def _apply_port_magic(inst_cfg: InstrumentConfig) -> InstrumentConfig:
    # If "port" is given, map to common kw names without clobbering user's provided kwargs
    if inst_cfg.port:
        if "devpath" not in inst_cfg.init_kwargs:
            inst_cfg.init_kwargs["devpath"] = inst_cfg.port
        # also keep "port" in kwargs if some drivers expect it
        inst_cfg.init_kwargs.setdefault("port", inst_cfg.port)
    return inst_cfg

def create_device(inst_cfg: InstrumentConfig):
    if inst_cfg.driverId not in drivers_index:
        raise HTTPException(400, f"Unknown driverId: {inst_cfg.driverId}")
    cls = import_driver_class(inst_cfg.driverId)
    dev = cls(*inst_cfg.init_args, **inst_cfg.init_kwargs)
    return dev

def connect_device(instrument_id: str, dev: Any) -> None:
    try:
        result = dev.initialize()
        if isinstance(result, dict) and not result.get("ok", True):
            raise RuntimeError(result.get("message") or "initialize() reported failure")
        instruments[instrument_id] = dev
        locks.setdefault(instrument_id, threading.Lock())
    except Exception as e:
        errors[instrument_id] = str(e)
        raise

def disconnect_device(instrument_id: str) -> None:
    if instrument_id not in instruments:
        return
    dev = instruments[instrument_id]
    try:
        if hasattr(dev, "shutdown"):
            dev.shutdown()
    except Exception:
        pass
    instruments.pop(instrument_id, None)
    locks.pop(instrument_id, None)

# ---------- Startup: scan drivers and auto-load instruments ----------
@app.on_event("startup")
def _startup():
    # rebuild driver table from files
    refresh_drivers_in_config()
    # load instruments and auto-connect where requested
    data = load_config()
    for inst in data["instrument"]:
        try:
            cfg = InstrumentConfig(**inst)
            cfg = _apply_port_magic(cfg)
            if cfg.connect:
                dev = create_device(cfg)
                connect_device(cfg.id, dev)
                # apply capabilities after connect
                module, file, class_name, funcs = drivers_index[cfg.driverId]
                for cap in cfg.capabilities:
                    if cap.driver not in funcs:
                        # ignore invalid caps to avoid blocking startup
                        continue
                    try:
                        getattr(dev, cap.driver)(*cap.args, **cap.kwargs)
                    except Exception as e:
                        errors[cfg.id] = f"Capability '{cap.driver}' failed: {e}"
        except Exception as e:
            errors[inst["id"]] = str(e)
    update_instrument_state_table()

# ---------- Endpoints: Drivers (list/add/replace/delete/scan) ----------
@app.get("/drivers")
def list_drivers():
    data = load_config()
    return data["driver"]

@app.post("/drivers/scan")
def rescan_drivers():
    data = refresh_drivers_in_config()
    update_instrument_state_table()
    return data["driver"]

@app.put("/drivers/file")
def upsert_driver_file(req: DriverFileIn):
    if not req.filename.endswith(".py"):
        raise HTTPException(400, "Only .py files are allowed")
    path = os.path.join(PKG_DIR, req.filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(req.content)
    # after writing, rescan
    data = refresh_drivers_in_config()
    update_instrument_state_table()
    return {"ok": True, "drivers": data["driver"]}

@app.delete("/drivers/{driver_id}")
def delete_driver(driver_id: str, delete_file: bool = False):
    data = load_config()
    # ensure no instrument uses it
    for inst in data["instrument"]:
        if inst["driverId"] == driver_id:
            raise HTTPException(409, "Driver is in use by an instrument")
    # find entry
    target = next((d for d in data["driver"] if d["id"] == driver_id), None)
    if not target:
        raise HTTPException(404, "Driver not found")
    data["driver"] = [d for d in data["driver"] if d["id"] != driver_id]
    save_config(data)
    # optionally delete file
    if delete_file:
        try:
            os.remove(os.path.join(PKG_DIR, target["file"]))
        except FileNotFoundError:
            pass
    # refresh runtime
    refresh_drivers_in_config()
    update_instrument_state_table()
    return {"ok": True}

# ---------- Endpoints: Instruments (CRUD + connect controls) ----------
@app.get("/instruments")
def list_instruments():
    # 先重建 instrument_state
    update_instrument_state_table()
    data = load_config()

    # 建立 driverId -> functions 對照
    drv_funcs = {d["id"]: d["function"] for d in data["driver"]}

    # 快速查表 instrument_id -> 實際 connect 狀態
    actual_connect = {}
    for s in data["instrument_state"]:
        actual_connect[s["nameid"]] = bool(s.get("connect", False))

    result = []
    for inst in data["instrument"]:
        inst_id = inst["id"]
        # 用實際狀態覆寫 connect（你要看到「is_opened==True」就 connect: true）
        inst_out = {**inst, "connect": actual_connect.get(inst_id, False)}
        result.append({
            **inst_out,
            "allowed_capabilities": drv_funcs.get(inst["driverId"], []),
            # 附帶當前 state 物件（含 connect 與 last_error）
            "state": next((s for s in data["instrument_state"] if s["nameid"] == inst_id), None)
        })
    return result

@app.put("/instruments")
def upsert_instrument(cfg: InstrumentConfig):
    # validate driverId & capability names
    if cfg.driverId not in drivers_index:
        raise HTTPException(400, f"Unknown driverId: {cfg.driverId}")
    allowed = set(drivers_index[cfg.driverId][3])
    for c in cfg.capabilities:
        if c.driver not in allowed:
            raise HTTPException(400, f"Capability '{c.driver}' not in driver functions")

    data = load_config()

    # --- name uniqueness guard ---
    if not cfg.id:
        # Create path: name must be unique among all instruments
        if any(it["name"] == cfg.name for it in data["instrument"]):
            raise HTTPException(409, f"Instrument name '{cfg.name}' already exists")
        cfg.id = uuid_for_instrument(cfg.name, cfg.driverId)
    else:
        # Update path: if renaming, the new name must not be used by others
        if any(it["name"] == cfg.name and it["id"] != cfg.id for it in data["instrument"]):
            raise HTTPException(409, f"Instrument name '{cfg.name}' already exists")

    # port magic
    cfg = _apply_port_magic(cfg)

    # upsert into config
    exists = False
    for i, it in enumerate(data["instrument"]):
        if it["id"] == cfg.id:
            data["instrument"][i] = json.loads(cfg.model_dump_json())
            exists = True
            break
    if not exists:
        data["instrument"].append(json.loads(cfg.model_dump_json()))
    save_config(data)

    # apply to runtime: (re)connect if connect=True, else disconnect if exists
    if cfg.connect:
        disconnect_device(cfg.id)
        dev = create_device(cfg)
        try:
            connect_device(cfg.id, dev)
            for cap in cfg.capabilities:
                try:
                    getattr(dev, cap.driver)(*cap.args, **cap.kwargs)
                except Exception as e:
                    errors[cfg.id] = f"Capability '{cap.driver}' failed: {e}"
        except Exception:
            # leave as not connected; error stored
            pass
    else:
        disconnect_device(cfg.id)

    update_instrument_state_table()
    return {"ok": True, "id": cfg.id}


@app.delete("/instruments/{instrument_id}")
def remove_instrument(instrument_id: str):
    data = load_config()
    before = len(data["instrument"])
    data["instrument"] = [it for it in data["instrument"] if it["id"] != instrument_id]
    if len(data["instrument"]) == before:
        raise HTTPException(404, "Instrument not found")
    save_config(data)
    disconnect_device(instrument_id)
    errors.pop(instrument_id, None)
    update_instrument_state_table()
    return {"ok": True}

def _id_or_name_to_id(name_or_id: str) -> str:
    # if exists as id, return; else try mapping by name
    data = load_config()
    ids = {it["id"] for it in data["instrument"]}
    if name_or_id in ids:
        return name_or_id
    # rebuild name index
    for it in data["instrument"]:
        by_name_index[it["name"]] = it["id"]
    if name_or_id in by_name_index:
        return by_name_index[name_or_id]
    raise HTTPException(404, "Instrument not found")

@app.post("/instruments/{name_or_id}/connect")
def connect_instrument(name_or_id: str):
    inst_id = _id_or_name_to_id(name_or_id)
    data = load_config()
    cfg_dict = next((it for it in data["instrument"] if it["id"] == inst_id), None)
    if not cfg_dict:
        raise HTTPException(404, "Instrument not found")
    cfg = InstrumentConfig(**cfg_dict)
    cfg = _apply_port_magic(cfg)
    # (re)connect
    disconnect_device(inst_id)
    dev = create_device(cfg)
    connect_device(inst_id, dev)
    # run capabilities
    for cap in cfg.capabilities:
        try:
            getattr(dev, cap.driver)(*cap.args, **cap.kwargs)
        except Exception as e:
            errors[inst_id] = f"Capability '{cap.driver}' failed: {e}"
    update_instrument_state_table()
    return {"ok": True}

@app.post("/instruments/{name_or_id}/disconnect")
def disconnect_instrument(name_or_id: str):
    inst_id = _id_or_name_to_id(name_or_id)
    disconnect_device(inst_id)
    update_instrument_state_table()
    return {"ok": True}

@app.post("/instruments/{name_or_id}/reconnect")
def reconnect_instrument(name_or_id: str):
    inst_id = _id_or_name_to_id(name_or_id)
    data = load_config()
    cfg_dict = next((it for it in data["instrument"] if it["id"] == inst_id), None)
    if not cfg_dict:
        raise HTTPException(404, "Instrument not found")
    cfg = InstrumentConfig(**cfg_dict)
    cfg = _apply_port_magic(cfg)
    try:
        disconnect_device(inst_id)
    except Exception:
        pass
    dev = create_device(cfg)
    connect_device(inst_id, dev)
    # re-apply capabilities
    for cap in cfg.capabilities:
        try:
            getattr(dev, cap.driver)(*cap.args, **cap.kwargs)
        except Exception as e:
            errors[inst_id] = f"Capability '{cap.driver}' failed: {e}"
    update_instrument_state_table()
    return {"ok": True}

# ---------- RPC ----------
@app.post("/rpc")
def rpc(cmd: Command):
    # allow instrument=id or name
    name_or_id = cmd.instrument
    inst_id = _id_or_name_to_id(name_or_id)

    if inst_id not in instruments:
        # try auto-connect if configured
        data = load_config()
        cfg_dict = next((it for it in data["instrument"] if it["id"] == inst_id), None)
        if not cfg_dict:
            raise HTTPException(404, "Instrument not found")
        cfg = InstrumentConfig(**cfg_dict)
        cfg = _apply_port_magic(cfg)
        try:
            dev = create_device(cfg)
            connect_device(inst_id, dev)
        except Exception as e:
            errors[inst_id] = str(e)
            raise HTTPException(503, f"Reconnect failed: {e}")

    dev = instruments[inst_id]

    if not hasattr(dev, cmd.command):
        raise HTTPException(400, f"Command {cmd.command} not found")

    with locks.setdefault(inst_id, threading.Lock()):
        try:
            result = getattr(dev, cmd.command)(*cmd.args, **cmd.kwargs)
            return {"ok": True, "result": result}
        except Exception as e:
            errors[inst_id] = str(e)
            raise HTTPException(500, f"Execution error: {e}")

# --- add these helpers near the top of the file (after imports) ---
# ---- AST typing helpers ----
from typing import Optional

def _ast_get_docstring(node: ast.AST) -> Optional[str]:
    return ast.get_docstring(node)

def _unparse(n) -> Optional[str]:
    try:
        return ast.unparse(n)
    except Exception:
        try:
            return repr(ast.literal_eval(n))
        except Exception:
            return None

def _ann_literal_choices(ann) -> Optional[list]:
    """
    Return list of choices if annotation is typing.Literal[...].
    Works for `Literal["a","b"]` or `typing.Literal[...]`.
    """
    try:
        # Literal[...] → ast.Subscript(value=Name/Attribute 'Literal', slice=...)
        if not isinstance(ann, ast.Subscript):
            return None
        val = ann.value
        name = None
        if isinstance(val, ast.Name):
            name = val.id
        elif isinstance(val, ast.Attribute):
            name = val.attr
        if name != "Literal":
            return None

        # py3.8/3.9: slice in .slice; in 3.9+ it's AnnAssign differently; handle simple tuple/elts
        sl = ann.slice
        elts = []
        if isinstance(sl, ast.Tuple):
            elts = sl.elts
        elif hasattr(ast, "Index") and isinstance(sl, ast.Index) and isinstance(sl.value, ast.Tuple):
            elts = sl.value.elts
        else:
            elts = [getattr(sl, "value", sl)]

        out = []
        for e in elts:
            s = _unparse(e)
            if s is None:
                continue
            # strip quotes if repr-like
            if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
                s = s[1:-1]
            out.append(s)
        return out or None
    except Exception:
        return None

def _ann_type_str(ann) -> Optional[str]:
    s = _unparse(ann)
    if not s:
        return None
    # normalize common forms
    s = s.replace("typing.", "")
    return s

def _default_from_pair(names: list, defaults: list, i: int):
    """Return textual default for positional arg index i given right-aligned defaults."""
    num_kw = len(defaults)
    split_at = len(names) - num_kw
    if i < split_at:
        return None
    d_ast = defaults[i - split_at]
    return _unparse(d_ast)

def _kwonly_default(fn: ast.FunctionDef, i: int):
    kwdefs = getattr(fn.args, "kw_defaults", []) or []
    if i >= len(kwdefs):
        return None
    d_ast = kwdefs[i]
    return None if d_ast is None else _unparse(d_ast)

def _ast_param_default(expr) -> Optional[str]:
    try:
        return ast.unparse(expr)  # Py>=3.9
    except Exception:
        try:
            return repr(ast.literal_eval(expr))  # best-effort for simple literals
        except Exception:
            return None

def _split_init_params(fn: ast.FunctionDef) -> Tuple[List[ParamMeta], List[ParamMeta]]:
    pos, kw = [], []
    if not isinstance(fn, ast.FunctionDef):
        return pos, kw
    args = fn.args
    names = args.args[1:] if args.args else []  # skip self
    annos = [getattr(a, "annotation", None) for a in names]
    defaults = args.defaults or []

    # positional (no default)
    num_kw = len(defaults)
    split_at = len(names) - num_kw
    for i, a in enumerate(names):
        nm = a.arg
        anno = annos[i]
        type_s = _ann_type_str(anno) if anno is not None else None
        choices = _ann_literal_choices(anno) if anno is not None else None
        d = _default_from_pair(names, defaults, i)
        meta = ParamMeta(name=nm, type=type_s, default=d, choices=choices)
        if i < split_at:
            pos.append(meta)
        else:
            kw.append(meta)

    # kwonly
    for i, a in enumerate(getattr(args, "kwonlyargs", []) or []):
        nm = a.arg
        anno = getattr(a, "annotation", None)
        type_s = _ann_type_str(anno) if anno is not None else None
        choices = _ann_literal_choices(anno) if anno is not None else None
        d = _kwonly_default(fn, i)
        kw.append(ParamMeta(name=nm, type=type_s, default=d, choices=choices))
    return pos, kw

def _fn_params_from_ast(fn: ast.FunctionDef) -> List[ParamMeta]:
    params: List[ParamMeta] = []
    names = fn.args.args[1:] if fn.args.args else []  # skip self
    defaults = fn.args.defaults or []
    annos = [getattr(a, "annotation", None) for a in names]
    num_kw = len(defaults)
    split_at = len(names) - num_kw
    for i, a in enumerate(names):
        nm = a.arg
        anno = annos[i]
        type_s = _ann_type_str(anno) if anno is not None else None
        choices = _ann_literal_choices(anno) if anno is not None else None
        d = _default_from_pair(names, defaults, i)
        params.append(ParamMeta(name=nm, type=type_s, default=d, choices=choices))
    # kwonly
    for i, a in enumerate(getattr(fn.args, "kwonlyargs", []) or []):
        nm = a.arg
        anno = getattr(a, "annotation", None)
        type_s = _ann_type_str(anno) if anno is not None else None
        choices = _ann_literal_choices(anno) if anno is not None else None
        d = _kwonly_default(fn, i)
        params.append(ParamMeta(name=nm, type=type_s, default=d, choices=choices))
    # *args/**kwargs 先不暴露細項
    return params