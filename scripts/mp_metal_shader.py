"""Translate the MP Visualizer GLSL fragment shader into Metal Shading Language.

The visualizer's look lives in one 3,400-line GLSL file
(``shaders/mp_liquid_visualizer.frag``). Rewriting it by hand into MSL would
mean maintaining two shaders that drift apart, and every future preset tweak
would have to be made twice. This module converts the GLSL at load time
instead, so Metal draws the same picture as Vulkan and OpenGL from a single
source of truth.

The dialects are close but not identical. Everything below is a real
difference that was confirmed against Apple's Metal compiler, not a guess:

``vec2``/``vec3``/``vec4``/``mat2``
    Metal spells them ``float2``/``float3``/``float4``/``float2x2``.
``mod(a, b)``
    Metal has ``fmod``, which truncates and returns the wrong sign for negative
    inputs. GLSL's ``mod`` is ``a - b*floor(a/b)``, so an overload set is
    emitted that reproduces it exactly (most of the shader's animation phases
    are negative at some point, so this is a visible difference, not pedantry).
``atan(y, x)``
    Metal names the two-argument form ``atan2``.
``inversesqrt``
    Metal calls it ``rsqrt``.
``texture(sampler, uv)``
    Metal separates the sampler from the texture; ``texture()`` becomes
    ``tex.sample(sampler, uv)``.
``v.xy *= m``
    GLSL allows a compound assignment through a swizzle. Metal rejects it
    ("non-const reference cannot bind to vector element"), so it is rewritten
    to ``v.xy = v.xy * m``. There are 138 of these in the shader.
``const`` locals
    Metal accepts ``const`` but the initializer may not always be constexpr,
    so plain ``const`` locals are kept and only the global ones become
    ``constant``.
``void main()`` writing an ``out`` variable
    Metal fragments return their colour, so the body becomes a helper that
    returns ``float4`` and the early ``return;`` becomes ``return outColor;``.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# GLSL type/keyword -> MSL spelling. Order matters for the regex: the longer
# matrix name has to be rewritten before ``mat2`` can match inside it.
_TYPE_MAP = (
    ("mat4x4", "float4x4"), ("mat3x3", "float3x3"), ("mat2x2", "float2x2"),
    ("mat4", "float4x4"), ("mat3", "float3x3"), ("mat2", "float2x2"),
    ("vec4", "float4"), ("vec3", "float3"), ("vec2", "float2"),
    ("ivec4", "int4"), ("ivec3", "int3"), ("ivec2", "int2"),
    ("bvec4", "bool4"), ("bvec3", "bool3"), ("bvec2", "bool2"),
)

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TYPE_RE = re.compile(r"\b(" + "|".join(name for name, _ in _TYPE_MAP) + r")\b")

# ``ident.swizzle op= ...`` where op is one of + - * /. The lookbehind keeps
# struct member access (``u.res.xy``) out of the rewrite: only a plain local
# can be assigned through its swizzle.
_SWIZZLE_COMPOUND = re.compile(
    r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_]*)\.([xyzwrgbastpq]{1,4})"
    r"\s*([*+\-/])=([^;]*);"
)

_TEXTURE_CALL = re.compile(r"\btexture\s*\(")

# Statements whose keyword is followed directly by a call, e.g. ``return f(x)``.
_STATEMENT_KEYWORDS = {
    "return", "if", "else", "while", "for", "do", "switch", "case",
    "break", "continue", "discard", "in", "out", "inout", "const",
}

# Uniform-block field: ``vec4 name;``
_UBO_FIELD = re.compile(r"^\s*(vec[234])\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$")


def _match_paren(text: str, open_index: int) -> int:
    """Index of the ``)`` that closes the ``(`` at ``open_index``."""
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_args(text: str) -> List[str]:
    """Split a call's argument list on top-level commas."""
    args, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(text[start:index])
            start = index + 1
    args.append(text[start:])
    return [arg.strip() for arg in args]


def rewrite_calls(source: str, name: str, arity: int, replacement: str) -> str:
    """Rewrite ``name(...)`` with exactly ``arity`` arguments.

    ``replacement`` is a format string receiving the argument list, e.g.
    ``"atan2({})"``. Calls with a different argument count are left alone so
    the one-argument ``atan`` keeps working.
    """
    out: List[str] = []
    index = 0
    pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"\s*\(")
    while True:
        match = pattern.search(source, index)
        if match is None:
            out.append(source[index:])
            break
        open_index = match.end() - 1
        close_index = _match_paren(source, open_index)
        if close_index < 0:
            out.append(source[index:])
            break
        args = _split_args(source[open_index + 1:close_index])
        out.append(source[index:match.start()])
        if len(args) == arity:
            out.append(replacement.format(", ".join(args)))
        else:
            out.append(source[match.start():close_index + 1])
        index = close_index + 1
    return "".join(out)


def rewrite_swizzle_compounds(source: str) -> str:
    """``v.xy *= m`` -> ``v.xy = v.xy * (m)``.

    Metal has no compound assignment through a swizzle because a swizzle is a
    value, not a reference. Semantically the rewrite is exact: the right-hand
    side is evaluated once, and wrapping it in parentheses keeps operator
    precedence identical.
    """

    def replace(match: re.Match) -> str:
        name, swizzle, op, rhs = match.groups()
        return f"{name}.{swizzle} = {name}.{swizzle} {op} ({rhs.strip()});"

    return _SWIZZLE_COMPOUND.sub(replace, source)


def rewrite_texture_calls(source: str, sampler_name: str = "mp_cover_sampler") -> str:
    """``texture(tex, uv)`` -> ``tex.sample(sampler, uv)``."""
    out: List[str] = []
    index = 0
    while True:
        match = _TEXTURE_CALL.search(source, index)
        if match is None:
            out.append(source[index:])
            break
        open_index = match.end() - 1
        close_index = _match_paren(source, open_index)
        if close_index < 0:
            out.append(source[index:])
            break
        args = _split_args(source[open_index + 1:close_index])
        out.append(source[index:match.start()])
        if len(args) == 2:
            out.append(f"{args[0]}.sample({sampler_name}, {args[1]})")
        else:
            out.append(source[match.start():close_index + 1])
        index = close_index + 1
    return "".join(out)


def rewrite_types(source: str) -> str:
    """Replace GLSL vector/matrix type names with their Metal spelling."""
    return _TYPE_RE.sub(lambda match: dict(_TYPE_MAP)[match.group(1)], source)


def extract_uniform_block(source: str) -> Tuple[str, List[Tuple[str, str]], str]:
    """Pull the ``uniform VisualizerUBO { ... } u;`` block out of the GLSL.

    Returns ``(remaining_source, fields, instance_name)`` where ``fields`` is a
    list of ``(glsl_type, name)`` pairs in declaration order. Generating the
    Metal struct from the GLSL keeps the two layouts identical without a second
    hard-coded copy that can drift.
    """
    match = re.search(
        r"layout\s*\([^)]*\)\s*uniform\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{(.*?)\}\s*([A-Za-z_][A-Za-z0-9_]*)\s*;",
        source, re.S,
    )
    if match is None:
        return source, [], ""
    fields: List[Tuple[str, str]] = []
    for line in match.group(2).splitlines():
        field = _UBO_FIELD.match(line.split("//")[0])
        if field:
            fields.append((field.group(1), field.group(2)))
    remaining = source[:match.start()] + source[match.end():]
    return remaining, fields, match.group(3)


def extract_samplers(source: str) -> Tuple[str, List[str]]:
    """Remove ``uniform sampler2D`` declarations, returning their names."""
    names = re.findall(r"layout\s*\([^)]*\)\s*uniform\s+sampler2D\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", source)
    stripped = re.sub(r"layout\s*\([^)]*\)\s*uniform\s+sampler2D\s+[A-Za-z_][A-Za-z0-9_]*\s*;", "", source)
    return stripped, names


def strip_layout_declarations(source: str) -> str:
    """Drop ``layout(...) in/out`` stage variables and ``#version``."""
    source = re.sub(r"^\s*#version[^\n]*\n", "", source, flags=re.M)
    source = re.sub(r"^\s*layout\s*\([^)]*\)\s*(?:in|out)\s+[^;]+;\s*\n", "", source, flags=re.M)
    return source


def promote_program_constants(source: str) -> str:
    """``const float PI = ...;`` at file scope -> ``constant float PI = ...;``.

    Metal only allows program-scope variables in the ``constant`` address
    space. A plain file-scope ``const`` is rejected outright.
    """
    return re.sub(r"^const\s+", "constant ", source, flags=re.M)


# A definition starts in column 0 with a return type and a name, then a brace.
# The parameter list allows one level of nesting for types such as
# ``texture2d<float>``; a plain ``[^)]*`` would stop inside the angle brackets.
_FUNC_DEF = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\(((?:[^()]|\([^()]*\))*)\)\s*\{",
    re.M,
)


def _function_spans(source: str) -> List[Tuple[str, str, str, int, int, int]]:
    """Every top-level function as ``(return, name, params, start, body_start, end)``.

    ``end`` is the index just past the closing brace. Only definitions whose
    signature starts in column 0 are considered, which is how this shader is
    laid out and avoids matching calls inside expressions.
    """
    spans = []
    for match in _FUNC_DEF.finditer(source):
        open_index = match.end() - 1
        depth = 0
        for index in range(open_index, len(source)):
            char = source[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    spans.append((match.group(1), match.group(2), match.group(3),
                                  match.start(), open_index, index + 1))
                    break
    return spans


def _uses_global(body: str, name: str) -> bool:
    """True when ``name`` appears as a bare identifier (not a member access)."""
    return _identifier_re(name).search(body) is not None


_IDENTIFIER_CACHE: Dict[str, "re.Pattern[str]"] = {}
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.])[A-Za-z_][A-Za-z0-9_]*")


def _identifier_re(name: str) -> "re.Pattern[str]":
    """A cached ``(?<!x)name(?!y)`` pattern."""
    pattern = _IDENTIFIER_CACHE.get(name)
    if pattern is None:
        pattern = re.compile(r"(?<![A-Za-z0-9_.])" + re.escape(name) + r"(?![A-Za-z0-9_])")
        _IDENTIFIER_CACHE[name] = pattern
    return pattern


def bare_identifiers(text: str) -> "frozenset[str]":
    """Every identifier in ``text`` that is not a struct member access.

    Scanning the whole body once and asking a set is dramatically faster than
    asking ``_uses_global`` per name: the call-graph walk used to re-scan the
    same 3,400-line bodies thousands of times, which cost two seconds of every
    translation.
    """
    return frozenset(_TOKEN_RE.findall(text))


def thread_globals(source: str, globals_: Sequence[Tuple[str, str]]) -> str:
    """Pass ``globals_`` into every function that reads them, transitively.

    The GLSL relies on ``u`` (the uniform block) and ``uCoverTex`` being global,
    which Metal does not permit: every function that touches them needs them as
    parameters. This walks the call graph so a function that only *calls*
    another one still gets the parameters, and rewrites the call sites to match.
    """
    wanted = [name for name, _decl in globals_]
    decls = dict(globals_)
    spans = _function_spans(source)
    if not spans:
        return source

    bodies = {name: source[body_start:end] for _ret, name, _params, _start, body_start, end in spans}
    declared = {name: _params for _ret, name, _params, _start, _body_start, _end in spans}
    tokens = {name: bare_identifiers(body) for name, body in bodies.items()}
    needs = {name: {g for g in wanted if g in found} for name, found in tokens.items()}

    # Transitive closure over the call graph: if A calls B and B needs the
    # uniform, A needs it too so it can forward it.
    calls = {name: {callee for callee in bodies if callee in found}
             for name, found in tokens.items()}
    changed = True
    while changed:
        changed = False
        for name, callees in calls.items():
            for callee in callees:
                if callee == name:
                    continue
                if needs[callee] - needs[name]:
                    needs[name] |= needs[callee]
                    changed = True

    # ``needs`` drives call-site forwarding. A function that already declares
    # the parameter only forwards it; it does not take a second copy.
    extra_params: Dict[str, List[str]] = {}
    for name, need in needs.items():
        params = declared[name] or ""
        already = {g for g in wanted
                   if re.search(r"(?<![A-Za-z0-9_])" + re.escape(g) + r"(?![A-Za-z0-9_])", params)}
        extra_params[name] = [g for g in wanted if g in need and g not in already]

    targets = {name: [g for g in wanted if g in need] for name, need in needs.items()}

    # Two passes: signatures first, then every call site. The call-site pass
    # cannot happen inside the signature loop because a function is often
    # defined after its caller and the caller's body is still unrewritten.
    for _ret, name, params, start, body_start, end in reversed(spans):
        extra = extra_params[name]
        if not extra:
            continue
        signature = source[start:body_start]
        params_text = params.rstrip()
        joined = (params_text + ", " if params_text else "") + ", ".join(decls[g] for g in extra)
        head = signature[:len(signature) - len(signature.lstrip())]
        source = source[:start] + f"{head}{_ret} {name}({joined}) " + source[body_start:]
    return _add_call_arguments(source, targets, bodies)


def _add_call_arguments(source: str, targets: Dict[str, List[str]], bodies) -> str:
    """Append the threaded arguments at every internal call site.

    The whole file is scanned, not per body: the shader has plenty of nested
    calls (``getPresetPalette(...)`` inside ``renderCosmicBackground``) and a
    per-body pass misses those because the body text is rewritten twice.
    """
    names = [name for name, extra in targets.items() if extra]
    if not names:
        return source
    out: List[str] = []
    index = 0
    pattern = re.compile(r"(?<![A-Za-z0-9_.])(" + "|".join(re.escape(n) for n in names) + r")\s*\(")
    while True:
        match = pattern.search(source, index)
        if match is None:
            out.append(source[index:])
            break
        name = match.group(1)
        open_index = match.end() - 1
        close_index = _match_paren(source, open_index)
        if close_index < 0:
            out.append(source[index:])
            break
        # Only rewrite calls that do not already carry the threaded arguments,
        # so a second run of the translator is idempotent.
        if _is_definition_site(source, match.start()):
            out.append(source[index:close_index + 1])
            index = close_index + 1
            continue
        args = source[open_index + 1:close_index]
        extra = targets[name]
        if args.rstrip().endswith(", ".join(extra)):
            out.append(source[index:close_index + 1])
        else:
            joined = (args.rstrip() + ", " if args.strip() else "") + ", ".join(extra)
            out.append(source[index:match.start()] + f"{name}({joined})")
        index = close_index + 1
    return "".join(out)


def _is_definition_site(source: str, name_start: int) -> bool:
    """True when ``name_start`` begins a function definition, not a call.

    A definition's line is ``<return type> <name>(``; a call is always part of
    an expression. Rewriting a definition as if it were a call is what produced
    duplicated parameter lists before this check existed.
    """
    line_start = source.rfind("\n", 0, name_start) + 1
    prefix = source[line_start:name_start]
    if "\n" in prefix:
        return False
    stripped = prefix.strip()
    if not stripped:
        return False
    # ``return foo(...)`` has a keyword before the call, not a type.
    if stripped in _STATEMENT_KEYWORDS:
        return False
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped) is not None



PREAMBLE = """// Generated from shaders/mp_liquid_visualizer.frag by mp_metal_shader.py.
// Do not edit by hand: the GLSL is the single source of truth.
#include <metal_stdlib>
using namespace metal;

// GLSL-compatible mod(): truncating fmod() would flip the sign of every
// negative animation phase, which the shader relies on.
inline float  mp_mod(float  a, float  b) { return a - b * floor(a / b); }
inline float2 mp_mod(float2 a, float2 b) { return a - b * floor(a / b); }
inline float3 mp_mod(float3 a, float3 b) { return a - b * floor(a / b); }
inline float4 mp_mod(float4 a, float4 b) { return a - b * floor(a / b); }
inline float  mp_mod(float a, float b, float c) { return mp_mod(mp_mod(a, b), c); }

// Linear filtering with repeat addressing, matching the Vulkan sampler.
constexpr sampler mp_cover_sampler(coord::normalized,
                                   address::repeat,
                                   filter::linear,
                                   mip_filter::linear);

struct MPVertexOut {
    float4 position [[position]];
    float2 uv;
};

vertex MPVertexOut mp_visualizer_vertex(uint vertexID [[vertex_id]]) {
    // Fullscreen triangle: (-1,-1) (3,-1) (-1,3) covers the viewport with one
    // primitive and no vertex buffer, exactly like the Vulkan vertex shader.
    float2 corner = float2(float((vertexID << 1) & 2), float(vertexID & 2));
    MPVertexOut out;
    out.position = float4(corner * 2.0 - 1.0, 0.0, 1.0);
    // Metal's clip space has +y upwards while the shader (and Vulkan) address
    // the framebuffer with y growing downwards, so vUv.y has to be inverted or
    // the whole scene renders upside down.
    out.uv = float2(corner.x, 1.0 - corner.y);
    return out;
}
"""


def ensure_returns_out_color(source: str, body_name: str) -> str:
    """Give the body helper a final ``return outColor;``.

    The GLSL ``main`` assigns its global ``out`` variable and falls off the end.
    Metal requires every non-void function to return, and a fall-through is not
    a compile error - it is undefined behaviour, which showed up as pure static
    instead of the scene. This appends the return to the helper's last
    statement, after the closing brace of the function.
    """
    marker = f"float4 {body_name}("
    start = source.find(marker)
    if start < 0:
        return source
    open_index = source.find("{", start)
    if open_index < 0:
        return source
    depth = 0
    for index in range(open_index, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                # Insert just before the body's closing brace.
                return source[:index] + "\n    return outColor;\n" + source[index:]
    return source


def translate(source: str, *, fold_preset: Optional[int] = None,
              march_steps: Optional[int] = None) -> str:
    """Convert the visualizer GLSL into a self-contained MSL library.

    ``fold_preset`` replaces ``u.preset.x`` with a literal so Apple's compiler
    can eliminate every scene that is not on screen. That matters: with the id
    as a uniform the whole 3,400-line program stays live, and the register
    pressure alone costs a large fraction of the frame time.

    ``march_steps`` shortens the raymarch loop. The Vulkan macOS path uses 64
    steps and Windows uses far fewer; the value is a quality/peed dial.
    """
    text, fields, instance = extract_uniform_block(source)
    text, samplers = extract_samplers(text)
    text = strip_layout_declarations(text)

    # One function uses ``float u`` as a local blade coordinate. Metal has no
    # globals, so the uniform block becomes a parameter and the two would
    # collide, so the instance gets a name no local uses.
    if instance == "u":
        text = re.sub(r"(?<![A-Za-z0-9_.])u\.", "mpu.", text)
        instance = "mpu"

    # Stage names in the GLSL body become parameters of the helper below.
    text = rewrite_texture_calls(text)
    text = replace_identifiers(text, {
        "mod": "mp_mod",
        "inversesqrt": "rsqrt",
    })
    text = rewrite_calls(text, "atan", 2, "atan2({})")
    text = rewrite_swizzle_compounds(text)

    if fold_preset is not None and instance:
        literal = f"({float(int(fold_preset)):.1f})"
        text = text.replace(f"{instance}.preset.x", literal)

    if march_steps is not None:
        text = text.replace("for (int i = 0; i < 84; i++)",
                            f"for (int i = 0; i < {int(march_steps)}; i++)", 1)

    # ``void main()`` writes a global ``out`` variable; Metal fragments return
    # their colour instead. The body becomes a helper so the early ``return;``
    # (the Dynamic Waves fast path) still works.
    body_name = "mp_visualizer_body"
    sampler_name = samplers[0] if samplers else "uCoverTex"
    text = re.sub(
        r"\bvoid\s+main\s*\(\s*(?:void)?\s*\)\s*\{",
        (f"float4 {body_name}(float2 vUv, constant MPUniforms &{instance or 'u'}, "
         f"texture2d<float> {sampler_name}) {{\n"
         f"    float4 outColor = float4(0.0, 0.0, 0.0, 1.0);"),
        text, count=1,
    )
    text = text.replace("return;", "return outColor;")

    text = promote_program_constants(text)
    text = rewrite_types(text)

    # ``u`` and the cover texture are file-scope in GLSL and illegal at file
    # scope in Metal, so every function that reads them takes them as
    # parameters. This has to run on the finished text, after the body helper
    # has been created, so its parameters are part of the analysis.
    uniform_globals: List[Tuple[str, str]] = []
    if instance:
        uniform_globals.append((instance, f"constant MPUniforms &{instance}"))
    for sampler in samplers:
        uniform_globals.append((sampler, f"texture2d<float> {sampler}"))
    text = thread_globals(text, uniform_globals)
    text = ensure_returns_out_color(text, body_name)

    struct = ["struct MPUniforms {"]
    for glsl_type, name in fields:
        struct.append(f"    float{glsl_type[-1]} {name};")
    struct.append("};")
    if not fields:
        struct = ["struct MPUniforms { float4 res; };"]

    fragment = f"""
fragment float4 mp_visualizer_fragment(
    MPVertexOut in [[stage_in]],
    constant MPUniforms &{instance or 'u'} [[buffer(0)]],
    texture2d<float> {samplers[0] if samplers else 'uCoverTex'} [[texture(0)]]
) {{
    return {body_name}(in.uv, {instance or 'u'}, {samplers[0] if samplers else 'uCoverTex'});
}}
"""

    return "\n".join([
        PREAMBLE,
        "\n".join(struct),
        text.rstrip(),
        fragment,
    ])


def replace_identifiers(source: str, mapping: Dict[str, str]) -> str:
    """Rename bare identifiers, leaving longer names containing them alone."""
    if not mapping:
        return source
    pattern = re.compile(r"(?<![A-Za-z0-9_.])(" + "|".join(map(re.escape, mapping)) + r")(?![A-Za-z0-9_])")

    def swap(match: re.Match) -> str:
        # Do not touch a member access such as ``foo.mod``; only bare calls.
        start = match.start()
        if start and source[start - 1] == ".":
            return match.group(1)
        return mapping[match.group(1)]

    return pattern.sub(swap, source)


def source_digest(source: str) -> str:
    """Stable short hash used to key the compiled-library cache."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def glsl_digest(path=None) -> str:
    """Digest of the GLSL input, so a cached translation can be invalidated."""
    return source_digest(glsl_for_metal(path))


def translate_cached(path=None, *, fold_preset: Optional[int] = None,
                     march_steps: Optional[int] = None,
                     cache_dir=None) -> str:
    """``translate()`` with an on-disk cache.

    Translating the shader costs ~0.4 s of pure Python on a first launch, which
    is a visible delay before the window appears. The result depends only on
    the GLSL source, the preset and the march step count, so it can be cached
    and reloaded in a few milliseconds. Anything that fails while reading or
    writing the cache silently falls back to translating, because a cold start
    must never be broken by a stale or unreadable cache file.
    """
    source_path = Path(path) if path is not None else _default_shader_path()
    directory = Path(cache_dir) if cache_dir is not None else _cache_dir()
    key = f"msl-p{fold_preset if fold_preset is not None else 'x'}-" \
          f"s{march_steps if march_steps is not None else 'x'}-{glsl_digest(source_path)}.metal"
    target = directory / key
    try:
        if target.is_file():
            cached = target.read_text(encoding="utf-8")
            if cached.strip():
                return cached
    except OSError:
        pass
    result = translate(source_path.read_text(encoding="utf-8"),
                       fold_preset=fold_preset, march_steps=march_steps)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Write through a temporary file so a killed process cannot leave a
        # half-written shader that would fail to compile on the next launch.
        staging = target.with_suffix(".tmp")
        staging.write_text(result, encoding="utf-8")
        staging.replace(target)
    except OSError:
        pass
    return result


def _cache_dir() -> Path:
    return Path.home() / ".config" / "mix-manager" / "metal-shaders"


def translate_file(path, **kwargs) -> str:
    return translate(Path(path).read_text(encoding="utf-8"), **kwargs)


def glsl_for_metal(path=None) -> str:
    """The GLSL source this module translates, for tests and diagnostics."""
    source_path = Path(path) if path is not None else _default_shader_path()
    return source_path.read_text(encoding="utf-8")


def _default_shader_path() -> Path:
    return Path(__file__).resolve().parent.parent / "shaders" / "mp_liquid_visualizer.frag"
