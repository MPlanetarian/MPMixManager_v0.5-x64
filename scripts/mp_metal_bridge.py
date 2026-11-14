"""Minimal Apple Metal bridge for the MP Visualizer, driven through ctypes.

The rest of this project deliberately avoids third-party Python bindings: the
Vulkan backend talks to ``libvulkan`` with ``ctypes``, and the macOS liquid
glass renderer poked at ``Metal.framework`` the same way. This module does the
same for the *visualizer*: it owns the Objective-C message dispatch and the
Metal device/pipeline/texture plumbing, so the renderer above it is ordinary
Python.

Why not PyObjC? ``pyobjc`` is not installed on a stock macOS system and the
visualizer has to run on a machine where the user only ran the installer. The
project already ships working ``ctypes`` -> ``objc_msgSend`` code in
``visualizer_window_shape.py``, so this follows the same pattern instead of
adding a dependency.

Objective-C method calls are not type checked at runtime: every distinct
(return type, argument types) combination needs its own ``objc_msgSend`` cast.
Pointers are 64-bit, and ``NSInteger``/``NSUInteger`` are 64-bit on every Apple
Silicon system, which is why the integer types below are ``c_uint64``/``c_int64``.
Passing ``c_int`` where the callee reads 64 bits truncates the register and
crashes inside Metal (the classic "EXC_BREAKPOINT in objc_opt_isKindOfClass").
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import (CFUNCTYPE, POINTER, Structure, byref, c_char_p, c_double,
                    c_int, c_uint64, c_void_p)
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Metal enums we use. Values come from MTLTypes.h.
# ---------------------------------------------------------------------------
MTLPixelFormatRGBA8Unorm = 70
MTLPixelFormatBGRA8Unorm = 80
MTLPixelFormatRGBA16Float = 115

MTLTextureType2D = 2

MTLTextureUsageShaderRead = 1
MTLTextureUsageShaderWrite = 2
MTLTextureUsageRenderTarget = 4

MTLStorageModeShared = 0

MTLLoadActionClear = 2
MTLStoreActionStore = 1

MTLPrimitiveTypeTriangle = 3

MTLBlendFactorSourceAlpha = 4
MTLBlendFactorOneMinusSourceAlpha = 5

MTLCommandBufferStatusCompleted = 4


class MTLRegion(Structure):
    """``MTLRegion``: six ``NSUInteger`` fields passed by value."""
    _fields_ = [
        ("originX", c_uint64), ("originY", c_uint64), ("originZ", c_uint64),
        ("sizeWidth", c_uint64), ("sizeHeight", c_uint64), ("sizeDepth", c_uint64),
    ]


class CGSize(Structure):
    _fields_ = [("width", c_double), ("height", c_double)]


class CGPoint(Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class CGRect(Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


class MetalError(RuntimeError):
    """A Metal call failed; the message carries the NSError description."""


def is_macos() -> bool:
    return sys.platform == "darwin"


# ---------------------------------------------------------------------------
# Objective-C runtime
# ---------------------------------------------------------------------------
class ObjC:
    """``objc_msgSend`` dispatch with per-signature trampolines.

    ``objc_msgSend`` has no prototype (it is variadic on ARM64), so ctypes must
    be told the exact signature of every call. Trampolines are cached by
    signature, which is also what makes this fast enough for a per-frame path.
    """

    def __init__(self) -> None:
        self.lib = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        self.lib.objc_getClass.restype = c_void_p
        self.lib.objc_getClass.argtypes = [c_char_p]
        self.lib.sel_registerName.restype = c_void_p
        self.lib.sel_registerName.argtypes = [c_char_p]
        self._send = ctypes.cast(self.lib.objc_msgSend, c_void_p).value
        self._trampolines: Dict[tuple, object] = {}
        # ``sel_registerName`` is a hash lookup into the runtime's selector
        # table. The renderer asks for the same two dozen selectors every
        # frame, so caching them by name removes that lookup from the hot path.
        self._selector_cache: Dict[str, int] = {}

    def sel(self, name) -> int:
        if isinstance(name, str):
            cached = self._selector_cache.get(name)
            if cached is not None:
                return cached
            resolved = self.lib.sel_registerName(name.encode("utf-8"))
            self._selector_cache[name] = resolved
            return resolved
        return self.lib.sel_registerName(name)

    def cls(self, name) -> int:
        if isinstance(name, str):
            name = name.encode("utf-8")
        return self.lib.objc_getClass(name)

    def send(self, obj, selector, *args, restype=c_void_p, argtypes=()):
        argtypes = tuple(argtypes)
        key = (restype, argtypes)
        fn = self._trampolines.get(key)
        if fn is None:
            fn = CFUNCTYPE(restype, c_void_p, c_void_p, *argtypes)(self._send)
            self._trampolines[key] = fn
        return fn(c_void_p(obj) if not isinstance(obj, c_void_p) else obj,
                  self.sel(selector), *args)

    def nsstring(self, text: str) -> int:
        data = text.encode("utf-8") if isinstance(text, str) else text
        return self.send(self.cls("NSString"), "stringWithUTF8String:",
                         c_char_p(data), argtypes=[c_char_p])

    def to_string(self, obj) -> str:
        if not obj:
            return ""
        raw = self.send(obj, "UTF8String", restype=c_char_p, argtypes=[])
        return ctypes.string_at(raw).decode("utf-8", "replace") if raw else ""

    def alloc_init(self, class_name: str):
        return self.send(self.send(self.cls(class_name), "alloc"),
                         "init", restype=c_void_p, argtypes=[])

    def release(self, obj) -> None:
        if obj:
            self.send(obj, "release", restype=None, argtypes=[])

    def retain(self, obj) -> int:
        return self.send(obj, "retain") if obj else 0


class AutoreleasePool:
    """An ``NSAutoreleasePool`` used as a context manager.

    Command buffers, encoders and drawables are autoreleased; they are only
    reclaimed when a pool drains. Without one per frame the renderer leaks
    until the process is killed.
    """

    __slots__ = ("_objc", "_pool")

    def __init__(self, objc: ObjC) -> None:
        self._objc = objc
        self._pool = objc.send(objc.cls("NSAutoreleasePool"), "new",
                               restype=c_void_p, argtypes=[])

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.drain()

    def drain(self) -> None:
        if self._pool:
            self._objc.send(self._pool, "drain", restype=None, argtypes=[])
            self._pool = 0


# ---------------------------------------------------------------------------
# Metal
# ---------------------------------------------------------------------------
class MetalBridge:
    """Owns one ``MTLDevice``, its command queue, and cached pipelines."""

    def __init__(self) -> None:
        if not is_macos():
            raise MetalError("Metal is only available on macOS")
        for framework in ("Foundation", "AppKit", "QuartzCore", "Metal", "MetalKit"):
            try:
                ctypes.CDLL(f"/System/Library/Frameworks/{framework}.framework/{framework}")
            except OSError as exc:  # pragma: no cover - broken system install
                raise MetalError(f"cannot load {framework}.framework: {exc}") from exc
        self.objc = ObjC()
        self.metal = ctypes.CDLL("/System/Library/Frameworks/Metal.framework/Metal")
        self.metal.MTLCreateSystemDefaultDevice.restype = c_void_p
        self.metal.MTLCreateSystemDefaultDevice.argtypes = []
        self.device = self.metal.MTLCreateSystemDefaultDevice()
        if not self.device:
            raise MetalError("MTLCreateSystemDefaultDevice returned NULL: no Metal GPU")
        self.queue = self.objc.send(self.device, "newCommandQueue",
                                   restype=c_void_p, argtypes=[])
        self.device_name = self.objc.to_string(self.objc.send(self.device, "name"))
        self._libraries: Dict[str, int] = {}
        self._pipelines: Dict[tuple, int] = {}
        # Everything this bridge creates is retained here and released exactly
        # once, in close(). Releasing a Metal object twice is a hard crash
        # (objc_msgSend on a freed object), and the renderer and the bridge
        # both hold references to the same textures, so ownership has to live
        # in one place.
        self._owned: List[int] = []
        self._closed = False

    def _own(self, obj):
        """Take ownership of a +1 Metal object so ``close()`` releases it once."""
        if obj:
            self._owned.append(obj)
        return obj

    # -- device facts ----------------------------------------------------
    @property
    def unified_memory(self) -> bool:
        try:
            return bool(self.objc.send(self.device, "hasUnifiedMemory",
                                       restype=c_int, argtypes=[]))
        except Exception:
            return True

    def supports_family(self, name: str) -> bool:
        try:
            return bool(self.objc.send(self.device, "supportsFamily:",
                                       c_uint64(_GPU_FAMILIES.get(name, 0)),
                                       restype=c_int, argtypes=[c_uint64]))
        except Exception:
            return False

    # -- libraries -------------------------------------------------------
    def compile_library(self, source: str, cache_key: Optional[str] = None):
        """Compile MSL source at runtime, caching the library by ``cache_key``."""
        if cache_key is not None and cache_key in self._libraries:
            return self._libraries[cache_key]
        error = c_void_p()
        library = self.objc.send(
            self.device, "newLibraryWithSource:options:error:",
            self.objc.nsstring(source), c_void_p(0), byref(error),
            argtypes=[c_void_p, c_void_p, POINTER(c_void_p)],
        )
        if not library:
            raise MetalError(
                "Metal shader compilation failed: " + self._error_text(error)
            )
        if cache_key is not None:
            self._libraries[cache_key] = library
        return self._own(library)

    def library_from_file(self, path, cache_key: Optional[str] = None):
        key = cache_key if cache_key is not None else str(path)
        return self.compile_library(Path(path).read_text(encoding="utf-8"), key)

    # -- pipelines -------------------------------------------------------
    def make_pipeline(self, library, vertex_name: str, fragment_name: str,
                      pixel_format: int = MTLPixelFormatBGRA8Unorm,
                      blend: bool = False, label: str = "",
                      archive: Optional["PipelineArchive"] = None,
                      function_constants: Optional[Sequence[bytes]] = None):
        """Build (and cache) a render pipeline for one vertex/fragment pair.

        When ``archive`` is supplied the compiled pipeline is stored there, so
        the next launch loads it from disk instead of running Apple's compiler
        (which takes about 2.5 s for this shader).
        """
        key = (library, vertex_name, fragment_name, int(pixel_format), bool(blend), label)
        cached = self._pipelines.get(key)
        if cached is not None:
            return cached
        objc = self.objc
        vertex = objc.send(library, "newFunctionWithName:", objc.nsstring(vertex_name),
                           argtypes=[c_void_p])
        fragment = objc.send(library, "newFunctionWithName:", objc.nsstring(fragment_name),
                             argtypes=[c_void_p])
        if not vertex or not fragment:
            raise MetalError(
                f"shader function missing: vertex={vertex_name!r} fragment={fragment_name!r}"
            )
        desc = objc.alloc_init("MTLRenderPipelineDescriptor")
        objc.send(desc, "setVertexFunction:", vertex, restype=None, argtypes=[c_void_p])
        objc.send(desc, "setFragmentFunction:", fragment, restype=None, argtypes=[c_void_p])
        if label:
            objc.send(desc, "setLabel:", objc.nsstring(label), restype=None, argtypes=[c_void_p])
        attachment = objc.send(
            objc.send(desc, "colorAttachments"), "objectAtIndexedSubscript:", c_uint64(0),
            argtypes=[c_uint64],
        )
        objc.send(attachment, "setPixelFormat:", c_uint64(pixel_format),
                  restype=None, argtypes=[c_uint64])
        if blend:
            objc.send(attachment, "setBlendingEnabled:", c_int(1), restype=None, argtypes=[c_int])
            for selector, factor in (
                ("setSourceRGBBlendFactor:", MTLBlendFactorSourceAlpha),
                ("setDestinationRGBBlendFactor:", MTLBlendFactorOneMinusSourceAlpha),
                ("setSourceAlphaBlendFactor:", MTLBlendFactorSourceAlpha),
                ("setDestinationAlphaBlendFactor:", MTLBlendFactorOneMinusSourceAlpha),
            ):
                objc.send(attachment, selector, c_uint64(factor), restype=None, argtypes=[c_uint64])

        if archive is not None and archive.supports_binary_archives:
            # Registering the archive with the descriptor lets Metal look the
            # pipeline up on disk and skip compilation entirely.
            objc.send(desc, "setBinaryArchives:",
                      archive.array(), restype=None, argtypes=[c_void_p])

        error = c_void_p()
        pipeline = objc.send(self.device,
                             "newRenderPipelineStateWithDescriptor:error:",
                             desc, byref(error),
                             argtypes=[c_void_p, POINTER(c_void_p)])
        if not pipeline and archive is not None:
            # An archive written by a different shader, OS build or GPU can be
            # stale; falling back to a plain compile keeps the window usable.
            archive.mark_stale()
            objc.send(desc, "setBinaryArchives:", c_void_p(0), restype=None, argtypes=[c_void_p])
            error = c_void_p()
            pipeline = objc.send(self.device,
                                 "newRenderPipelineStateWithDescriptor:error:",
                                 desc, byref(error),
                                 argtypes=[c_void_p, POINTER(c_void_p)])
        if not pipeline:
            detail = self._error_text(error)
            objc.release(vertex)
            objc.release(fragment)
            objc.release(desc)
            raise MetalError(
                f"Metal pipeline creation failed for {fragment_name}: {detail}"
            )
        if archive is not None:
            archive.add(desc)
        objc.release(vertex)
        objc.release(fragment)
        objc.release(desc)
        self._pipelines[key] = pipeline
        return self._own(pipeline)

    # -- resources -------------------------------------------------------
    def make_texture(self, width: int, height: int,
                     pixel_format: int = MTLPixelFormatBGRA8Unorm,
                     usage: int = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget,
                     storage_mode: int = MTLStorageModeShared):
        objc = self.objc
        desc = objc.alloc_init("MTLTextureDescriptor")
        for selector, value in (
            ("setTextureType:", MTLTextureType2D),
            ("setPixelFormat:", int(pixel_format)),
            ("setWidth:", int(width)),
            ("setHeight:", int(height)),
            ("setUsage:", int(usage)),
            ("setStorageMode:", int(storage_mode)),
        ):
            objc.send(desc, selector, c_uint64(value), restype=None, argtypes=[c_uint64])
        texture = objc.send(self.device, "newTextureWithDescriptor:", desc,
                            argtypes=[c_void_p])
        objc.release(desc)
        if not texture:
            raise MetalError(f"cannot create {width}x{height} Metal texture")
        return self._own(texture)

    def make_buffer(self, size: int, storage_mode: int = MTLStorageModeShared):
        buffer = self.objc.send(self.device, "newBufferWithLength:options:",
                                c_uint64(max(1, int(size))),
                                c_uint64(int(storage_mode)),
                                argtypes=[c_uint64, c_uint64])
        if not buffer:
            raise MetalError(f"cannot create {size}-byte Metal buffer")
        return self._own(buffer)

    def buffer_pointer(self, buffer) -> int:
        return int(self.objc.send(buffer, "contents", restype=c_void_p, argtypes=[]))

    def upload_texture(self, texture, data: bytes, width: int, height: int,
                       bytes_per_row: Optional[int] = None) -> None:
        stride = int(bytes_per_row) if bytes_per_row else int(width) * 4
        region = MTLRegion(0, 0, 0, int(width), int(height), 1)
        source = (ctypes.c_char * len(data)).from_buffer_copy(data)
        self.objc.send(texture, "replaceRegion:mipmapLevel:withBytes:bytesPerRow:",
                       region, c_uint64(0), ctypes.cast(source, c_void_p), c_uint64(stride),
                       restype=None,
                       argtypes=[MTLRegion, c_uint64, c_void_p, c_uint64])

    def read_texture(self, texture) -> Tuple[int, int, bytes]:
        """Read a shared-storage texture back as tightly packed BGRA rows."""
        objc = self.objc
        width = int(objc.send(texture, "width", restype=c_uint64, argtypes=[]))
        height = int(objc.send(texture, "height", restype=c_uint64, argtypes=[]))
        row = width * 4
        total = row * height
        target = (ctypes.c_char * total)()
        region = MTLRegion(0, 0, 0, width, height, 1)
        objc.send(texture, "getBytes:bytesPerRow:fromRegion:mipmapLevel:",
                  ctypes.cast(target, c_void_p), c_uint64(row), region, c_uint64(0),
                  restype=None,
                  argtypes=[c_void_p, c_uint64, MTLRegion, c_uint64])
        return width, height, bytes(target)

    def wait_until_completed(self, command_buffer) -> None:
        self.objc.send(command_buffer, "waitUntilCompleted", restype=None, argtypes=[])

    # -- passes ----------------------------------------------------------
    def encode_fullscreen(self, command_buffer, texture, pipeline,
                          buffers: Sequence[Tuple[int, int, int]] = (),
                          textures: Sequence[Tuple[int, int]] = (),
                          clear=(0.0, 0.0, 0.0, 1.0)) -> None:
        """Encode the 3-vertex fullscreen triangle into ``texture``.

        ``buffers`` is ``(index, buffer, offset)`` per bound buffer and
        ``textures`` is ``(index, texture)`` per bound texture.
        """
        objc = self.objc
        descriptor = self._render_pass(texture, clear=clear)
        encoder = objc.send(command_buffer, "renderCommandEncoderWithDescriptor:",
                            descriptor, argtypes=[c_void_p])
        objc.send(encoder, "setRenderPipelineState:", pipeline,
                  restype=None, argtypes=[c_void_p])
        for index, buffer, offset in buffers:
            objc.send(encoder, "setFragmentBuffer:offset:atIndex:",
                      buffer, c_uint64(offset), c_uint64(index),
                      restype=None, argtypes=[c_void_p, c_uint64, c_uint64])
        for index, texture_item in textures:
            objc.send(encoder, "setFragmentTexture:atIndex:", texture_item, c_uint64(index),
                      restype=None, argtypes=[c_void_p, c_uint64])
        objc.send(encoder, "drawPrimitives:vertexStart:vertexCount:",
                  c_uint64(MTLPrimitiveTypeTriangle), c_uint64(0), c_uint64(3),
                  restype=None, argtypes=[c_uint64, c_uint64, c_uint64])
        objc.send(encoder, "endEncoding", restype=None, argtypes=[])

    def _render_pass(self, texture, clear=(0.0, 0.0, 0.0, 1.0)):
        objc = self.objc
        descriptor = objc.send(objc.cls("MTLRenderPassDescriptor"),
                               "renderPassDescriptor", restype=c_void_p, argtypes=[])
        attachment = objc.send(
            objc.send(descriptor, "colorAttachments"),
            "objectAtIndexedSubscript:", c_uint64(0), argtypes=[c_uint64],
        )
        objc.send(attachment, "setTexture:", texture, restype=None, argtypes=[c_void_p])
        objc.send(attachment, "setLoadAction:", c_uint64(MTLLoadActionClear),
                  restype=None, argtypes=[c_uint64])
        objc.send(attachment, "setStoreAction:", c_uint64(MTLStoreActionStore),
                  restype=None, argtypes=[c_uint64])
        objc.send(attachment, "setClearColor:", (c_double * 4)(*clear),
                  restype=None, argtypes=[c_double * 4])
        return descriptor

    def new_command_buffer(self):
        return self.objc.send(self.queue, "commandBuffer", restype=c_void_p, argtypes=[])

    def commit(self, command_buffer, drawable=None, wait: bool = False) -> None:
        objc = self.objc
        if drawable:
            objc.send(command_buffer, "presentDrawable:", drawable,
                      restype=None, argtypes=[c_void_p])
        objc.send(command_buffer, "commit", restype=None, argtypes=[])
        if wait:
            objc.send(command_buffer, "waitUntilCompleted", restype=None, argtypes=[])

    # -- cleanup ---------------------------------------------------------
    def release(self, obj) -> None:
        """Release an object the bridge owns, and stop tracking it.

        Used when a texture is replaced during a resize: the object has to go
        immediately, but ``close()`` must not release it a second time.
        """
        if not obj:
            return
        try:
            self._owned.remove(obj)
        except ValueError:
            pass
        self.objc.release(obj)

    def close(self) -> None:
        """Release every owned Metal object exactly once."""
        if self._closed:
            return
        self._closed = True
        for obj in self._owned:
            self.objc.release(obj)
        self._owned.clear()
        self._pipelines.clear()
        self._libraries.clear()
        if self.queue:
            self.objc.release(self.queue)
            self.queue = 0
        if self.device:
            self.objc.release(self.device)
            self.device = 0

    @staticmethod
    def _error_text(error) -> str:
        if not error:
            return "no NSError returned"
        try:
            objc = ObjC()
            return objc.to_string(objc.send(error, "localizedDescription",
                                            restype=c_void_p, argtypes=[]))
        except Exception:
            return "unknown Metal error"


# MTLGPUFamily values, for the performance feature probes.
_GPU_FAMILIES = {
    "apple1": 1001, "apple2": 1002, "apple3": 1003, "apple4": 1004,
    "apple5": 1005, "apple6": 1006, "apple7": 1007, "apple8": 1008,
    "apple9": 1009, "apple10": 1010,
}


class PipelineArchive:
    """A persistent on-disk ``MTLBinaryArchive`` of compiled pipelines.

    Apple's shader compiler takes ~2.5 s for this visualizer's 3,400-line
    program, and that cost was paid on *every* launch. A binary archive stores
    the compiled result, so subsequent launches load it instead of compiling.

    The archive is keyed by a caller-supplied string (shader digest + preset +
    march steps + pixel format), so a changed shader or setting writes a new
    file rather than reusing a stale one. A stale archive is also detected at
    runtime: if pipeline creation fails with it installed, it is discarded and
    the normal compile path runs.
    """

    def __init__(self, path, bridge: "MetalBridge") -> None:
        self.path = Path(path)
        self.objc = bridge.objc
        self.device = bridge.device
        self._archive = 0
        self._stale = False
        self._added: List[int] = []
        self.supports_binary_archives = self._open()

    def _open(self) -> bool:
        """Load the archive if it exists, otherwise start a new one."""
        objc = self.objc
        descriptor = objc.alloc_init("MTLBinaryArchiveDescriptor")
        if self.path.is_file():
            url = self._file_url(self.path)
            if url:
                objc.send(descriptor, "setUrl:", url, restype=None, argtypes=[c_void_p])
        error = c_void_p()
        archive = objc.send(self.device, "newBinaryArchiveWithDescriptor:error:",
                            descriptor, byref(error),
                            argtypes=[c_void_p, POINTER(c_void_p)])
        objc.release(descriptor)
        if not archive:
            self._stale = True
            return False
        self._archive = archive
        return True

    def _file_url(self, path):
        """``NSURL.fileURLWithPath:`` for a Python path."""
        objc = self.objc
        return objc.send(objc.cls("NSURL"), "fileURLWithPath:",
                         objc.nsstring(str(path)), argtypes=[c_void_p])

    def array(self):
        """The archive as the single-element ``NSArray`` Metal expects."""
        objc = self.objc
        return objc.send(objc.cls("NSArray"), "arrayWithObject:", self._archive,
                         argtypes=[c_void_p])

    def add(self, descriptor) -> None:
        """Store a pipeline descriptor so it can be serialised later.

        The descriptor has to be retained: ``make_pipeline`` releases its own
        reference as soon as the pipeline is built, and the archive cannot
        serialise an object that no longer exists.
        """
        if not self._archive or self._stale:
            return
        try:
            self._added.append(descriptor)
            self.objc.send(descriptor, "retain", restype=c_void_p, argtypes=[])
        except Exception:
            pass

    def save(self) -> bool:
        """Serialise the archive to disk. Returns True when written."""
        if not (self._archive and self._added and not self._stale):
            return False
        objc = self.objc
        error = c_void_p()
        ok = 0
        descriptors, self._added = self._added, []
        try:
            # Every descriptor must be registered before serialising.
            for descriptor in descriptors:
                added = objc.send(self._archive,
                                  "addRenderPipelineFunctionsWithDescriptor:error:",
                                  descriptor, byref(error),
                                  restype=c_int,
                                  argtypes=[c_void_p, POINTER(c_void_p)])
                if not added:
                    self._stale = True
                    break
            if not self._stale:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                url = self._file_url(self.path)
                ok = objc.send(self._archive, "serializeToURL:error:",
                               url, byref(error), restype=c_int,
                               argtypes=[c_void_p, POINTER(c_void_p)])
        finally:
            for descriptor in descriptors:
                objc.release(descriptor)
        return bool(ok)

    def mark_stale(self) -> None:
        """Record that this archive did not match, and delete it."""
        self._stale = True
        try:
            if self.path.is_file():
                self.path.unlink()
        except OSError:
            pass

    def close(self) -> None:
        if self._archive:
            try:
                self.save()
            except Exception:
                pass
            self.objc.release(self._archive)
            self._archive = 0

