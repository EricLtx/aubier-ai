"""The COM adapter of a learned driver (L-P1): the only module that touches COM. Vendored into every
generated driver as vendor/vkforge_runtime/com.py (L-P19): the stdlib, vkdriver.protocol and, lazily
and on Windows only, comtypes. It names no application (L-P31): what differs between Word and
PowerPoint at this level is binding["application"], the ontology's applications block for the app."""
import ctypes
import gc
import hashlib
import os
import sys
import threading
from pathlib import Path

from vkdriver.protocol import DriverRefusal

CALL_DEADLINE_S = 30.0   # one COM call (L-P33); a qualification pass has 120 s (qualify.PASS_DEADLINE_S)
CLEANUP_DEADLINE_S = 5.0  # the close that ends an action, and the end of the session: often after a timeout
DIALOG_CLASS = "#32770"  # the Win32 dialog box class: a sign-in, an overwrite, a repair prompt
WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def executables() -> set:
    """The executables this driver drives: its manifest's `applications`, which the host puts in
    VK_DRIVER_APPLICATIONS (Task 1) and `vkforge qualify` too (Task 8)."""
    return {e.strip().upper() for e in os.environ.get("VK_DRIVER_APPLICATIONS", "").split(",") if e.strip()}


def dialogs(exes: set) -> list:
    """The visible top-level dialog boxes (class #32770) of the processes running one of `exes`."""
    if sys.platform != "win32" or not exes:
        return []
    from ctypes import wintypes

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = (enum_proc, wintypes.LPARAM)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    found = []

    def image_of(pid: int) -> str:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            return Path(buf.value).name.upper() if ok else ""
        finally:
            kernel32.CloseHandle(handle)

    def each(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value == DIALOG_CLASS and user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if image_of(pid.value) in exes:
                found.append(hwnd)
        return True

    user32.EnumWindows(enum_proc(each), 0)
    return found


def close_window(hwnd) -> None:
    """WM_CLOSE, posted: the box's own close, never one of its answers (spec §6)."""
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def guarded(fn, what: str, deadline_s: float = CALL_DEADLINE_S, find_dialogs=None, close=None):
    """fn() on its own thread, joined for `deadline_s` (L-P33). Past it, the application's dialog boxes
    are closed and the action refuses `dialog`, the box never answered (spec §6); with none open it
    refuses `timeout`. A thread that finds comtypes loaded joins the multithreaded apartment that
    Application keeps alive, so the proxies one call's thread made serve the next."""
    find_dialogs = find_dialogs or (lambda: dialogs(executables()))
    close = close or close_window
    box = {}

    def run():
        com = sys.modules.get("comtypes")    # loaded by Application only: the fake's hook runs without COM
        if com is not None:
            com.CoInitializeEx(com.COINIT_MULTITHREADED)
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 — raised again on the caller's thread
            box["error"] = e
        finally:
            if com is not None:
                com.CoUninitialize()

    worker = threading.Thread(target=run, name=f"com {what}", daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        boxes = find_dialogs()
        for hwnd in boxes:
            close(hwnd)
        if boxes:
            raise DriverRefusal("dialog", f"{what}: a dialog box of the application held the call; "
                                          "closed, never answered")
        raise DriverRefusal("timeout", f"{what}: no answer within {deadline_s:g} s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _com_errors() -> tuple:
    """What a failed COM call raises: OSError and, once Application has loaded comtypes, COMError."""
    com = sys.modules.get("comtypes")
    return (OSError,) if com is None else (OSError, com.COMError)


def _version_key(name: str):
    major, _, minor = name.partition(".")
    return int(major, 16), int(minor or "0", 16)          # registry versions are hexadecimal `major.minor`


def _not_registered(guid: str) -> DriverRefusal:
    return DriverRefusal("precondition", f"type library {guid} is not registered; remedy: install Microsoft 365 desktop")


def typelib_path(guid: str) -> str:
    """inventory.typelib_path, line for line (this module is vendored and cannot import it), so the
    probe hashes the very file inventory hashed (L-P22): HKCR\\TypeLib\\{guid}, its parsable versions
    highest first (hexadecimal `major.minor`, any other subkey skipped), 0\\win64 then 0\\win32."""
    if sys.platform != "win32":
        raise _not_registered(guid)
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"TypeLib\{guid}") as key:
            versions = []
            try:
                while True:
                    versions.append(winreg.EnumKey(key, len(versions)))
            except OSError:                                 # the end of the enumeration
                pass
        parsable = []
        for version in versions:
            try:
                parsable.append((_version_key(version), version))
            except ValueError:                              # a subkey that is not `major.minor`: not a version
                continue
        for _, version in sorted(parsable, reverse=True):
            for bitness in ("win64", "win32"):
                try:
                    return winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf"TypeLib\{guid}\{version}\0\{bitness}")
                except OSError:
                    continue
    except OSError:
        pass
    raise _not_registered(guid)


def live_typelib_sha256(guid: str) -> str:
    """The type library file the registry names for `guid` today (typelib_path), hashed as the
    catalogue's `typelib_sha256` is (L-P22)."""
    path = typelib_path(guid)
    try:
        return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        raise DriverRefusal("precondition", f"type library file of {guid} cannot be read; "
                                            "remedy: repair Microsoft 365 desktop") from None


def probe(binding: dict) -> None:
    """L-P22: the type library on this node is the one the driver was learned on (binding.json is in its
    driver_id); else every action refuses before its document is opened."""
    if live_typelib_sha256(binding["typelib"]["guid"]) != binding["typelib_sha256"]:
        raise DriverRefusal("precondition", "office version changed; remedy: vk driver learn again")


class Application:
    """The application binding["application"] names, the user's own instance (spike verdict 3). Attached
    to that instance, the adapter changes only its DisplayAlerts, read first and put back by quit()
    (L-P47); an instance the adapter started itself, quit() ends (L-P46). `connect` returns
    (application, started); the default is the running instance, else a new one."""

    def __init__(self, binding: dict, connect=None):
        self.binding, self.spec = binding, binding["application"]
        self.verbs = {v["verb"]: v for v in binding["verbs"]}
        self.deadline_s, self.cleanup_deadline_s = CALL_DEADLINE_S, CLEANUP_DEADLINE_S
        self.app, self.started, self.alerts_before = None, False, None
        progid = self.spec["progid"]
        try:
            self.app, self.started = (connect or self._connect)()
            if not self.started:      # the user's instance: its own setting, put back at the end (L-P47)
                self.alerts_before = guarded(lambda: self.app.DisplayAlerts, "DisplayAlerts")
            guarded(lambda: setattr(self.app, "DisplayAlerts", self.spec["alerts_off"]), "DisplayAlerts")
        except _com_errors() as e:
            raise DriverRefusal("precondition", f"{progid} cannot be started: {e}") from e

    def _connect(self) -> tuple:
        progid = self.spec["progid"]
        if sys.platform != "win32":
            raise DriverRefusal("precondition", f"{progid} cannot be started: not Windows")
        cookie = ctypes.c_void_p()
        ctypes.oledll.ole32.CoIncrementMTAUsage(ctypes.byref(cookie))   # the calls' apartment lives with the driver
        import comtypes
        import comtypes.client

        def start():
            try:
                return comtypes.client.GetActiveObject(progid), False
            except (OSError, comtypes.COMError):
                return comtypes.client.CreateObject(progid), True

        return guarded(start, f"start {progid}")

    def _documents(self):
        return getattr(self.app, self.spec["collection"])

    def probe(self) -> None:
        probe(self.binding)

    def open(self, path: str):
        """The document, opened. A failure while opening (no such file, a damaged one, the application's
        refusal) is `precondition`, as the fake's; a failure inside a verb is `internal` in both. The
        plan's "not open elsewhere" holds in this very instance too: there Open hands back a document the
        user already has open, unsaved edits included; refused, and that document is not closed."""
        try:
            open_before = guarded(lambda: {d.FullName for d in _items(self._documents())}, "open", self.deadline_s)
            doc = guarded(lambda: self._documents().Open(path, **self.spec["open"]), "open", self.deadline_s)
            already = guarded(lambda: doc.FullName, "open", self.deadline_s) in open_before
            read_only = not already and guarded(lambda: bool(doc.ReadOnly), "open", self.deadline_s)
        except _com_errors() as e:
            raise DriverRefusal("precondition", f"document cannot be opened: {e}") from e
        if already:
            raise DriverRefusal("precondition", "document already open in the application (the user's window)")
        if read_only:
            self.close(doc)
            raise DriverRefusal("precondition", "document opened read-only (locked elsewhere)")
        return doc

    def call(self, verb: str, doc, **args):
        try:
            return guarded(lambda: VERBS[verb](self, doc, **args), verb, self.deadline_s)
        except _com_errors() as e:
            raise DriverRefusal("internal", f"{verb}: {e}") from e

    def close(self, doc) -> None:
        """Closed without saving, under the short cleanup deadline; its own refusal is written to stderr,
        never raised: the action's first refusal is the one the host gets (after a timeout, the close
        meets the same wedged application)."""
        try:
            guarded(lambda: doc.Close(**self.spec["close"]), "close", self.cleanup_deadline_s)
        except (DriverRefusal, *_com_errors()) as e:
            sys.stderr.write(f"close: {e}\n")

    def quit(self) -> None:
        """The end of the session (L-P46, L-P47), once. Attached to the user's instance: its DisplayAlerts
        put back, nothing else. Started by this adapter: the references this process dropped are
        collected, then Quit, unless a document is still open in the instance (one the user opened there,
        or one a failed close left): nothing is closed then, the instance is left running, stderr says
        so. Every reference to the application is released at the end. A failure is written to stderr,
        never raised: this runs when serve returns and at exit."""
        if self.app is None:
            return
        try:
            if not self.started:
                if self.alerts_before is not None:
                    guarded(lambda: setattr(self.app, "DisplayAlerts", self.alerts_before), "DisplayAlerts",
                            self.cleanup_deadline_s)
            else:
                guarded(gc.collect, "quit", self.cleanup_deadline_s)
                if guarded(lambda: self._documents().Count, "quit", self.cleanup_deadline_s):
                    sys.stderr.write(f"quit: {self.spec['progid']} still holds documents; left running\n")
                else:
                    guarded(lambda: self.app.Quit(), "quit", self.deadline_s)
        except (DriverRefusal, *_com_errors()) as e:
            sys.stderr.write(f"quit: {e}\n")
        finally:
            held, self.app = [self.app], None
            try:      # the last reference dropped in the apartment it was made in
                guarded(lambda: (held.clear(), gc.collect()), "release", self.cleanup_deadline_s)
            except DriverRefusal as e:
                sys.stderr.write(f"quit: {e}\n")


# --- the verbs (L-P40) -------------------------------------------------------------------------------
# Each verb takes its member chain and its constants from the binding (`app.verbs[verb]`): the chain's
# head is reached from the opened document, every member after it is walked as the binding names it.
# What the runtime knows is how to reach a head and what a verb does with the object at the chain's
# end (a font's Name and Size, a header's text); it holds no member table of its own. Every verb runs
# inside Application.call, hence under `guarded`.

_DRIVEN = ("bound", "partial")
_COLLECTIONS = {"Section": "Sections", "Slide": "Slides"}   # heads that are every item of a document collection
_MASTER = "SlideMaster"                                      # the `Master` head: the deck's slide master
_SELECTED = ("Paragraph", "Shape")                           # heads a selector picks
_PLACEHOLDER = {"title": "Title", "body": "Body"}            # a Shape selector: the placeholder type it names
_PRIMARY = "Primary"                                         # the header or footer of a section's body pages


def _entry(app, verb: str) -> dict:
    entry = app.verbs.get(verb)
    if entry is None or entry.get("status") not in _DRIVEN or not entry.get("member"):
        raise DriverRefusal("precondition", f"verb {verb} is not bound on this application")
    return entry


def _chain(entry: dict) -> tuple:
    head, *names = entry["member"].split(".")
    return head, names


def _walk(obj, names):
    for name in names:
        obj = getattr(obj, name)
    return obj


def _items(collection) -> list:
    return [collection.Item(i) for i in range(1, collection.Count + 1)]


def _constant(entry: dict, value) -> int:
    """The constant the binding carries for `value`: the member of the verb's enum whose lower-case name
    ends with `value`'s, the least qualified when several do (wdStyleTitle, not wdStyleBookTitle or
    wdStyleSubtitle; ppPlaceholderBody, not ppPlaceholderVerticalBody). A built-in style is reached by
    its constant, never by a localised name (L-P42). None, or two equally qualified: `internal`."""
    enums = entry.get("constants") or {}
    if isinstance(value, str) and value:
        wanted = value.lower()
        for enum in sorted(enums):
            found = sorted((len(name), name) for name in enums[enum] if name.lower().endswith(wanted))
            if len(found) == 1 or (len(found) > 1 and found[0][0] < found[1][0]):
                return enums[enum][found[0][1]]
    raise DriverRefusal("internal", f"no unique constant for {value} in {', '.join(sorted(enums)) or 'no enum'}")


def _paragraphs(app, doc, selector: str) -> list:
    """The paragraphs a selector names, from the collection the binding's select_paragraph reaches."""
    root, names = _chain(_entry(app, "select_paragraph"))
    if root != _chain(_entry(app, "save"))[0]:
        raise DriverRefusal("precondition", "select_paragraph does not start at the document on this application")
    paragraphs = _walk(doc, names)
    count = paragraphs.Count
    if selector in ("first", "title"):
        wanted = [1]
    elif selector == "body":
        wanted = list(range(2, count + 1))
    elif selector == "all":
        wanted = list(range(1, count + 1))
    elif selector.startswith("index:") and selector[6:].isdigit():
        wanted = [int(selector[6:])]
    else:
        raise DriverRefusal("precondition", f"selector {selector} is not one of first, body, all, title, index:<n>")
    if any(not 1 <= i <= count for i in wanted):
        raise DriverRefusal("precondition", f"selector {selector} selects no paragraph of this document")
    return [paragraphs.Item(i) for i in wanted]


def _placeholders(app, doc, selector: str) -> list:
    """A Shape selector resolves over the master's placeholders (L-P40: title_font is mined from the
    master): the shapes whose placeholder type, read through the apply_paragraph_style chain, is the
    binding's constant for the selector."""
    if selector not in _PLACEHOLDER:
        raise DriverRefusal("precondition", f"selector {selector} names no placeholder of the master")
    entry = _entry(app, "apply_paragraph_style")
    _, names = _chain(entry)
    wanted = _constant(entry, _PLACEHOLDER[selector])
    found = []
    for shape in _items(getattr(doc, _MASTER).Shapes):
        try:
            kind = _walk(shape, names)
        except _com_errors():               # not a placeholder
            continue
        if kind == wanted:
            found.append(shape)
    return found


def _heads(app, doc, verb: str, head: str, selector=None) -> list:
    """The objects `head` names in the opened document (L-P40)."""
    if head == _chain(_entry(app, "save"))[0]:   # the document's own type: the type `open` returns
        return [doc]
    if head in _SELECTED:
        if not isinstance(selector, str):
            raise DriverRefusal("precondition", f"verb {verb} needs a selector")
        return (_paragraphs if head == "Paragraph" else _placeholders)(app, doc, selector)
    if head in _COLLECTIONS:
        return _items(getattr(doc, _COLLECTIONS[head]))
    if head == "Master":
        return [getattr(doc, _MASTER)]
    raise DriverRefusal("precondition", f"the runtime does not reach {head} from the document")


def _ends(app, doc, verb: str, selector=None) -> list:
    """The objects at the end of `verb`'s chain, one per head."""
    head, names = _chain(_entry(app, verb))
    return [_walk(target, names) for target in _heads(app, doc, verb, head, selector)]


def _bands(app, doc, verb: str) -> list:
    """The text of the headers or footers `verb` names: the primary one of each section when the chain
    ends on a collection indexed by the binding's constants (Section.Footers), else the object itself
    (Master.HeadersFooters.Footer)."""
    entry = _entry(app, verb)
    bands = _ends(app, doc, verb)
    if entry.get("constants"):
        bands = [band.Item(_constant(entry, _PRIMARY)) for band in bands]
    return [band.Range if hasattr(band, "Range") else band for band in bands]


def _lines(text: str) -> list:
    """Text as its non-blank lines: a COM text range ends its paragraphs with \\r, the miner joins them with \\n."""
    return [line for line in text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0b", "\n").split("\n")
            if line.strip()]


def apply_paragraph_style(app, doc, selector: str, style: str) -> None:
    entry = _entry(app, "apply_paragraph_style")
    head, names = _chain(entry)
    value = _constant(entry, style)
    for target in _heads(app, doc, "apply_paragraph_style", head, selector):
        setattr(_walk(target, names[:-1]), names[-1], value)


def verify_paragraph_style(app, doc, selector: str, style: str) -> bool:
    entry = _entry(app, "verify_paragraph_style")
    value = _constant(entry, style)
    got = _ends(app, doc, "verify_paragraph_style", selector)
    return bool(got) and all(_is_style(doc, g, value) for g in got)


def _is_style(doc, got, value: int) -> bool:
    """A number read back is compared with the constant; a style object by its name in the node's
    language against the name of the document's style for that constant, never a name of ours (L-P42)."""
    if isinstance(got, int):
        return got == value
    return got.NameLocal == doc.Styles.Item(value).NameLocal


def set_font_run(app, doc, selector: str, font: dict) -> None:
    for f in _ends(app, doc, "set_font_run", selector):
        f.Name = font["name"]
        f.Size = font["size"]


def verify_font_run(app, doc, selector: str, font: dict) -> bool:
    got = _ends(app, doc, "verify_font_run", selector)
    return bool(got) and all(f.Name == font["name"] and float(f.Size) == float(font["size"]) for f in got)


def set_header(app, doc, text: str) -> None:
    for band in _bands(app, doc, "set_header"):
        band.Text = text.replace("\n", "\r")


def set_footer(app, doc, text: str) -> None:
    for band in _bands(app, doc, "set_footer"):
        band.Text = text.replace("\n", "\r")


def verify_header(app, doc, text: str) -> bool:
    bands = _bands(app, doc, "verify_header")
    return bool(bands) and all(_lines(band.Text) == _lines(text) for band in bands)


def verify_footer(app, doc, text: str) -> bool:
    bands = _bands(app, doc, "verify_footer")
    return bool(bands) and all(_lines(band.Text) == _lines(text) for band in bands)


def save(app, doc) -> None:
    for method in _ends(app, doc, "save"):
        method()


def export(app, doc, format: str, path: str) -> None:
    value = _constant(_entry(app, "export"), format)
    for method in _ends(app, doc, "export"):
        method(os.path.abspath(path), value)         # (output path, fixed format): the order both libraries share


def verify_pdf(app, doc, path: str) -> bool:
    _entry(app, "verify_pdf")
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"%PDF"
    except OSError:
        return False


VERBS = {"apply_paragraph_style": apply_paragraph_style, "set_font_run": set_font_run,
         "set_header": set_header, "set_footer": set_footer, "save": save, "export": export,
         "verify_paragraph_style": verify_paragraph_style, "verify_font_run": verify_font_run,
         "verify_header": verify_header, "verify_footer": verify_footer, "verify_pdf": verify_pdf}
