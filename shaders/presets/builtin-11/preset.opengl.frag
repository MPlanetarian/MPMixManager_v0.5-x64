#version 330 core
in vec2 vUv;
out vec4 outColor;

uniform vec4 u_res;
uniform vec4 u_audio0;
uniform vec4 u_audio1;
uniform vec4 u_audio2;
uniform vec4 u_bands0;
uniform vec4 u_bands1;
uniform vec4 u_bands2;
uniform vec4 u_bands3;
uniform vec4 u_preset;
uniform vec4 u_stream;
uniform sampler2D uCoverTex;


// ---------------------------------------------------------------------------
// MP Visualisation Generator — generated signature scene (v2.0)
// Style: Stream of Frequency — Horizontal Ribbon Vault (12 FFT Bands)
// Core: Origami Tech Dodecahedron Core | Camera: Dynamic Sequence & Multi-Angle Cuts | Backdrop: Spectrum Ribbon Wash + Horizontal FFT Ripple Bands
// Symmetry: None (Organic Asymmetric Space)
// The animation vocabulary synthesizes all 13 shipped MP Visualizer presets:
// orbiting/sequencing/snap-turn cameras, SDF raymarching, morphing polyhedra
// (sphere, dodecahedron, hyper-torus, octahedron, twin bridged orbs, pod),
// liquid metaballs, tri-axis gyroscopic accretion rings, mycelial conduits,
// kinetic hex-prism flipper wings, 6-form Chromatic Storm vessel crossfades,
// 12-band Stream of Frequency ribbon vaults & vertical glyph rain, PID motes,
// non-Euclidean symmetry folding, cover-art chromatic dispersion, 5-tap AO,
// volumetric glow, 3-channel RGB Fresnel glass refraction & ACES tone mapping.
// ---------------------------------------------------------------------------
const float PI = 3.14159265359;
const float TAU = 6.28318530718;
const float STYLE = 7.0;
const float CORE_MODE = 2.0;
const float CAMERA_MODE = 1.0;
const float BACKDROP_MODE = 4.0;
const float SYMMETRY_MODE = 0.0;
const float SPEED = 1.6400;
const float INTENSITY = 1.5500;
const float PUNCH = 0.8700;
const float COMPLEXITY = 0.9700;
const float MORPH_RATE = 0.9300;
const float BLOOM_STRENGTH = 1.6500;
const float REFRACTION_STRENGTH = 1.1600;
const float DISPERSION_STRENGTH = 0.4100;
const float CAMERA_ZOOM = 1.2900;
const float BASS_DRIVE = 1.5800;
const float MID_DRIVE = 1.0300;
const float TREBLE_DRIVE = 1.5700;
const float SEED = 376072.0000;
const int MARCH_STEPS = 72;
const int SAT_COUNT = 3;
const int RING_COUNT = 3;
const int TENDRIL_COUNT = 5;
const int SHARD_COUNT = 7;
const vec3 PAL0 = vec3(0.46667, 0.25882, 0.45490);
const vec3 PAL1 = vec3(0.34510, 0.53333, 0.47843);
const vec3 PAL2 = vec3(0.55686, 0.76471, 0.64314);
const vec3 PAL3 = vec3(0.96471, 0.97255, 0.94902);
const vec3 PAL4 = vec3(0.23137, 0.34902, 0.36471);
const vec3 PAL5 = vec3(0.03529, 0.03529, 0.03529);
const vec3 PAL6 = vec3(0.58824, 0.24314, 0.40392);
const vec3 PAL7 = vec3(0.33333, 0.63529, 0.34902);

float hash11(float p) {
    p = fract(p * 0.1031);
    p *= p + 33.33;
    p *= p + p;
    return fract(p);
}

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

vec3 hash33(vec3 p3) {
    p3 = fract(p3 * vec3(0.1031, 0.1030, 0.0973));
    p3 += dot(p3, p3.yxz + 33.33);
    return fract((p3.xxy + p3.yxx) * p3.zyx);
}

float valueNoise3(vec3 x) {
    vec3 i = floor(x);
    vec3 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(
        mix(mix(hash11(dot(i + vec3(0.0, 0.0, 0.0), vec3(1.0, 57.0, 113.0))),
                hash11(dot(i + vec3(1.0, 0.0, 0.0), vec3(1.0, 57.0, 113.0))), f.x),
            mix(hash11(dot(i + vec3(0.0, 1.0, 0.0), vec3(1.0, 57.0, 113.0))),
                hash11(dot(i + vec3(1.0, 1.0, 0.0), vec3(1.0, 57.0, 113.0))), f.x), f.y),
        mix(mix(hash11(dot(i + vec3(0.0, 0.0, 1.0), vec3(1.0, 57.0, 113.0))),
                hash11(dot(i + vec3(1.0, 0.0, 1.0), vec3(1.0, 57.0, 113.0))), f.x),
            mix(hash11(dot(i + vec3(0.0, 1.0, 1.0), vec3(1.0, 57.0, 113.0))),
                hash11(dot(i + vec3(1.0, 1.0, 1.0), vec3(1.0, 57.0, 113.0))), f.x), f.y), f.z);
}

mat2 rot2(float a) {
    float c = cos(a), s = sin(a);
    return mat2(c, -s, s, c);
}

vec2 mirrorUv(vec2 uv) {
    vec2 m = fract(uv * 0.5) * 2.0;
    return clamp(1.0 - abs(m - 1.0), 0.03, 0.97);
}

float smin(float a, float b, float k) {
    float h = clamp(0.5 + 0.5 * (b - a) / max(k, 0.0001), 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

float sdSphere(vec3 p, float r) {
    return length(p) - r;
}

float sdTorus(vec3 p, vec2 t) {
    vec2 q = vec2(length(p.xz) - t.x, p.y);
    return length(q) - t.y;
}

float sdBox(vec3 p, vec3 b) {
    vec3 q = abs(p) - b;
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
}

float sdCapsule(vec3 p, vec3 a, vec3 b, float r) {
    vec3 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / max(dot(ba, ba), 0.0001), 0.0, 1.0);
    return length(pa - ba * h) - r;
}

float sdHexPrism(vec3 p, vec2 h) {
    const vec3 k = vec3(-0.8660254, 0.5, 0.57735);
    p = abs(p);
    p.xy = p.xy - (2.0 * min(dot(k.xy, p.xy), 0.0) * k.xy);
    vec2 d = vec2(
        length(p.xy - vec2(clamp(p.x, -k.z * h.x, k.z * h.x), h.x)) * sign(p.y - h.x),
        p.z - h.y
    );
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0));
}

float sdCylinder(vec3 p, vec2 h) {
    vec2 d = abs(vec2(length(p.xz), p.y)) - h;
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0));
}

float sdOctahedron(vec3 p, float s) {
    p = abs(p);
    return (p.x + p.y + p.z - s) * 0.57735027;
}

float sdDodecahedron(vec3 p, float r) {
    const vec3 n1 = normalize(vec3(0.0, 1.0, 1.61803398875));
    const vec3 n2 = normalize(vec3(0.0, 1.0, -1.61803398875));
    const vec3 n3 = normalize(vec3(1.0, 1.61803398875, 0.0));
    const vec3 n4 = normalize(vec3(-1.0, 1.61803398875, 0.0));
    const vec3 n5 = normalize(vec3(1.61803398875, 0.0, 1.0));
    const vec3 n6 = normalize(vec3(-1.61803398875, 0.0, 1.0));
    p = abs(p);
    float d = dot(p, n1);
    d = max(d, dot(p, n2));
    d = max(d, dot(p, n3));
    d = max(d, dot(p, n4));
    d = max(d, dot(p, n5));
    d = max(d, dot(p, n6));
    return d - r;
}

float bandValue(int idx) {
    if (idx == 0) return u_bands0.x;
    if (idx == 1) return u_bands0.y;
    if (idx == 2) return u_bands0.z;
    if (idx == 3) return u_bands0.w;
    if (idx == 4) return u_bands1.x;
    if (idx == 5) return u_bands1.y;
    if (idx == 6) return u_bands1.z;
    if (idx == 7) return u_bands1.w;
    if (idx == 8) return u_bands2.x;
    if (idx == 9) return u_bands2.y;
    if (idx == 10) return u_bands2.z;
    if (idx == 11) return u_bands2.w;
    if (idx == 12) return u_bands3.x;
    if (idx == 13) return u_bands3.y;
    if (idx == 14) return u_bands3.z;
    return u_bands3.w;
}

vec3 paletteFast(float t) {
    float s = fract(t * 0.5 + 0.5);
    vec3 c = mix(PAL5, PAL0, smoothstep(0.0, 0.16, s));
    c = mix(c, PAL1, smoothstep(0.14, 0.32, s));
    c = mix(c, PAL2, smoothstep(0.30, 0.48, s));
    c = mix(c, PAL6, smoothstep(0.46, 0.64, s));
    c = mix(c, PAL7, smoothstep(0.62, 0.78, s));
    c = mix(c, PAL3, smoothstep(0.76, 0.90, s));
    c = mix(c, PAL4, smoothstep(0.88, 1.0, s));
    return c;
}

vec3 coverSample(vec2 uv) {
    vec2 q = mirrorUv(uv * 0.72 + vec2(0.5) + 0.022 * sin(u_res.z * 0.06 + uv.yx * 5.0));
    return texture(uCoverTex, q).rgb;
}

vec3 coverSampleDispersed(vec2 uv, vec2 dir, float amount) {
    float d = amount * DISPERSION_STRENGTH;
    vec3 col;
    col.r = texture(uCoverTex, mirrorUv(uv + dir * d)).r;
    col.g = texture(uCoverTex, mirrorUv(uv)).g;
    col.b = texture(uCoverTex, mirrorUv(uv - dir * d)).b;
    return col;
}

vec3 signaturePalette(float t, vec2 uvHint) {
    vec3 procCol = paletteFast(t + 0.07 * sin(u_res.z * 0.11 + t * 6.0));
    vec3 art = coverSample(uvHint);
    float luma = dot(art, vec3(0.299, 0.587, 0.114));
    art = clamp(mix(vec3(luma), art, 1.48), 0.0, 1.0);
    return mix(procCol * (0.86 + 0.24 * PUNCH), art, 0.32 + 0.16 * PUNCH);
}

vec3 applySymmetry(vec3 p, float tt, float bassPhase) {
    if (SYMMETRY_MODE < 0.5) {
        return p;
    }
    vec3 q = p;
    if (SYMMETRY_MODE < 1.5) {
        // Bilateral mirror fold with soft breathing offset
        q.x = abs(q.x) - (0.18 + 0.06 * sin(tt * 0.35 + bassPhase * 0.2));
        q.xy = q.xy * rot2(0.15 * sin(tt * 0.22));
        return q;
    }
    if (SYMMETRY_MODE < 2.5) {
        // Quad-axis kaleidoscopic fold
        q.xy = abs(q.xy);
        if (q.y > q.x) {
            float tmp = q.x;
            q.x = q.y;
            q.y = tmp;
        }
        q.xy = q.xy - vec2(0.24 + 0.08 * sin(tt * 0.30), 0.14);
        q.xz = q.xz * rot2(0.25 * sin(tt * 0.18));
        return q;
    }
    // Non-Euclidean Mirrored Madness abs-fold lattice
    q.xy = abs(q.xy) - vec2(0.36 + 0.12 * sin(tt * 0.38 + bassPhase * 0.22),
                            0.26 + 0.08 * cos(tt * 0.32));
    q.xz = q.xz * rot2(0.52 + 0.15 * sin(tt * 0.24));
    q.yz = abs(q.yz) - vec2(0.22 + 0.07 * cos(tt * 0.42), 0.16);
    return q;
}

float evalCoreShape(vec3 p, float rBase, float tt, float bass, float lowMid, float mid,
                    float beat, float bassPhase, float midPhase, float highPhase) {
    float ripple = 0.048 * INTENSITY * (0.35 + bass * 1.15)
        * sin(p.x * 6.2 + tt * 0.85 + bassPhase * 0.4)
        * cos(p.y * 5.8 - tt * 0.70 + midPhase * 0.3)
        * sin(p.z * 5.2 + tt * 0.55 + highPhase * 0.25);

    if (abs(CORE_MODE - 1.0) < 0.5) {
        return sdSphere(p, rBase) + ripple;
    }
    if (abs(CORE_MODE - 2.0) < 0.5) {
        vec3 q = p;
        q.xz = q.xz * rot2(tt * 0.16 + midPhase * 0.1);
        q.yz = q.yz * rot2(tt * 0.11);
        return sdDodecahedron(q, rBase * 0.95) - 0.024 + ripple * 0.35;
    }
    if (abs(CORE_MODE - 3.0) < 0.5) {
        vec3 q = p;
        q.xz = q.xz * rot2(-tt * 0.14 + bassPhase * 0.08);
        q.xy = q.xy * rot2(tt * 0.12);
        return sdOctahedron(q, rBase * 1.24) - 0.015 + ripple * 0.30;
    }
    if (abs(CORE_MODE - 4.0) < 0.5) {
        vec3 q = p;
        q.xy = q.xy * rot2(tt * 0.18 + highPhase * 0.06);
        float d1 = sdTorus(q, vec2(rBase * 0.86, 0.20 + 0.05 * bass)) + ripple * 0.45;
        vec3 q2 = p;
        q2.yz = q2.yz * rot2(1.047 + tt * 0.14);
        float d2 = sdTorus(q2, vec2(rBase * 0.62, 0.14 + 0.04 * mid));
        return smin(d1, d2, 0.16);
    }
    if (abs(CORE_MODE - 5.0) < 0.5) {
        vec3 cA = vec3(0.68 + 0.18 * sin(tt * 0.37), 0.25 * cos(tt * 0.29), 0.15 * sin(tt * 0.31));
        vec3 cB = vec3(-0.68 + 0.18 * cos(tt * 0.33), -0.25 * sin(tt * 0.41), -0.15 * cos(tt * 0.27));
        float dA = sdSphere(p - cA, rBase * 0.78) + ripple * 0.7;
        float dB = sdSphere(p - cB, rBase * 0.66) + ripple * 0.6;
        float dBridge = sdCapsule(p, cA, cB, 0.08 + 0.07 * bass + 0.05 * beat);
        return smin(smin(dA, dB, 0.42), dBridge, 0.32);
    }
    if (abs(CORE_MODE - 6.0) < 0.5) {
        vec3 q = p;
        q.xy = q.xy * rot2(-0.26 + 0.08 * sin(tt * 0.18));
        float dPod = sdCapsule(q, vec3(0.0, 0.0, -1.45), vec3(0.0, 0.0, 1.45), rBase * 0.56) + ripple * 0.5;
        float dBulb = sdSphere(q - vec3(0.0, 0.04, 0.22 * sin(tt * 0.4)), rBase * 0.88);
        return smin(dPod, dBulb, 0.28 + 0.10 * mid);
    }

    // CORE_MODE == 0: Continuous 4-stage cyclic topology morph
    float morphT = fract(tt * 0.048 * max(MORPH_RATE, 0.05) + SEED * 0.13);
    float dSphere = sdSphere(p, rBase) + ripple;
    vec3 pDod = p;
    pDod.xz = pDod.xz * rot2(tt * 0.14 + midPhase * 0.08);
    pDod.yz = pDod.yz * rot2(tt * 0.09);
    float dDodec = sdDodecahedron(pDod, rBase * 0.96) - 0.025;
    vec3 pTor = p;
    pTor.xy = pTor.xy * rot2(tt * 0.16 + highPhase * 0.05);
    float dTorus = sdTorus(pTor, vec2(rBase * 0.82, 0.21 + 0.05 * bass))
        + 0.024 * sin(pTor.x * 8.0 + tt) * (1.0 + lowMid);
    vec3 pOct = p;
    pOct.xz = pOct.xz * rot2(-tt * 0.12 + bassPhase * 0.06);
    float dOcta = sdOctahedron(pOct, rBase * 1.22);

    float dMorph;
    if (morphT < 0.25) {
        float f = smoothstep(0.0, 1.0, morphT * 4.0);
        dMorph = mix(dSphere, dDodec, f);
    } else if (morphT < 0.50) {
        float f = smoothstep(0.0, 1.0, (morphT - 0.25) * 4.0);
        dMorph = mix(dDodec, dTorus, f);
    } else if (morphT < 0.75) {
        float f = smoothstep(0.0, 1.0, (morphT - 0.50) * 4.0);
        dMorph = mix(dTorus, dOcta, f);
    } else {
        float f = smoothstep(0.0, 1.0, (morphT - 0.75) * 4.0);
        dMorph = mix(dOcta, dSphere, f);
    }
    return min(dMorph, sdSphere(p, rBase * 0.45 + 0.06 * beat));
}

// ---------------------------------------------------------------------------
// Chromatic Storm 6-vessel sequence helper (Style 6)
// ---------------------------------------------------------------------------
float csHull(vec3 p, float len, float girth) {
    float dBody = sdCapsule(p, vec3(len * 0.15, 0.0, 0.0), vec3(-len * 0.75, 0.0, 0.0), girth);
    float dNose = sdSphere(p - vec3(len * 0.55, 0.0, 0.0), girth * 0.85);
    return smin(dBody, dNose, 0.16);
}

vec2 mapChromaticStorm(vec3 w, float tt, float subBass, float bass, float treble, float beat) {
    float rate = 0.016 * max(MORPH_RATE, 0.15);
    float cycle = fract(tt * rate + SEED * 0.07);
    int seg = int(clamp(floor(cycle * 6.0), 0.0, 5.0));
    float local = cycle * 6.0 - float(seg);
    float blend = smoothstep(0.70, 1.0, local);

    float dMix = 0.0;
    float mBest = 1.0;
    float bestDistance = 1e6;

    for (int pass = 0; pass < 2; pass++) {
        float weight = (pass == 0) ? (1.0 - blend) : blend;
        if (weight <= 0.001) continue;
        int cur = (pass == 0) ? seg : ((seg + 1) % 6);
        float dSeq = 1e5;
        float mSeq = 1.0;

        if (cur == 0) {
            // Clamshell vessel with glowing furnace core
            float gap = 0.24 + 0.04 * sin(tt * 0.70) + 0.05 * bass + 0.03 * beat;
            float girth = 0.17 + 0.02 * subBass;
            float dTop = sdCapsule(w - vec3(0.0, gap, 0.0), vec3(0.52, 0.0, 0.0), vec3(-0.74, 0.0, 0.0), girth);
            float dBot = sdCapsule(w - vec3(0.0, -gap, 0.0), vec3(0.52, 0.0, 0.0), vec3(-0.74, 0.0, 0.0), girth);
            float dShip = smin(min(dTop, dBot), sdSphere(w - vec3(0.78, 0.0, 0.0), 0.24), 0.20);
            float dCore = sdSphere(w - vec3(-0.05, 0.0, 0.0), 0.21 + 0.05 * subBass + 0.04 * beat);
            float dStrut = min(
                sdCapsule(w, vec3(-0.35, 0.12, 0.0), vec3(-1.15, 0.42, 0.12), 0.030),
                sdCapsule(w, vec3(-0.35, -0.12, 0.0), vec3(-1.15, -0.42, 0.12), 0.030)
            );
            dSeq = dShip; mSeq = 1.0;
            if (dStrut < dSeq) { dSeq = dStrut; mSeq = 4.0; }
            if (dCore < dSeq) { dSeq = dCore; mSeq = 2.0; }
        } else if (cur == 1) {
            // Helix plasma coil along dark spine
            float dSpine = sdCapsule(w, vec3(1.02, 0.0, 0.0), vec3(-1.12, 0.0, 0.0), 0.055);
            float dHelix = 1e5;
            for (int i = 0; i < 6; i++) {
                float su = float(i) / 5.0;
                float ang = su * TAU + tt * (0.72 + 0.55 * treble);
                float rad = 0.38 + 0.08 * bass;
                vec3 c = vec3(mix(0.85, -0.95, su), cos(ang) * rad, sin(ang) * rad);
                dHelix = min(dHelix, sdSphere(w - c, 0.078 + 0.018 * beat));
            }
            float dHead = sdSphere(w - vec3(1.06, 0.0, 0.0), 0.13 + 0.04 * beat);
            dSeq = dSpine; mSeq = 1.0;
            if (dHelix < dSeq) { dSeq = dHelix; mSeq = 2.0; }
            if (dHead < dSeq) { dSeq = dHead; mSeq = 3.0; }
        } else if (cur == 2) {
            // Twin smooth ships linked at nose
            float sep = 0.55 + 0.12 * sin(tt * 0.55) + 0.10 * bass;
            float dHull = 1e5;
            float dLamp = 1e5;
            for (int i = 0; i < 2; i++) {
                float sgn = float(i) * 2.0 - 1.0;
                vec3 b = w - vec3(0.0, sgn * sep, 0.0);
                b.xz = b.xz * rot2(sgn * 0.12 * sin(tt * 0.40));
                dHull = min(dHull, csHull(b, 1.05, 0.13 + 0.02 * bass));
                dLamp = min(dLamp, sdSphere(b - vec3(0.62, 0.0, 0.0), 0.065 + 0.025 * beat));
            }
            float dTether = sdCapsule(w, vec3(0.20, sep * 0.45, 0.0), vec3(0.20, -sep * 0.45, 0.0), 0.026 + 0.010 * beat);
            dSeq = dHull; mSeq = 1.0;
            if (dTether < dSeq) { dSeq = dTether; mSeq = 3.0; }
            if (dLamp < dSeq) { dSeq = dLamp; mSeq = 2.0; }
        } else if (cur == 3) {
            // Gyre rings spinning on spine
            float dSpine = sdCapsule(w, vec3(1.05, 0.0, 0.0), vec3(-1.05, 0.0, 0.0), 0.05);
            float dCore = sdSphere(w, 0.21 + 0.05 * beat);
            float dRings = 1e5;
            for (int i = 0; i < 3; i++) {
                float fi = float(i);
                vec3 b = w;
                b.x -= (fi - 1.0) * 0.52;
                float dir = (i == 1) ? -1.0 : 1.0;
                b.yz = b.yz * rot2(tt * (0.35 + 0.12 * fi) * dir);
                dRings = min(dRings, sdTorus(b, vec2(0.40 + fi * 0.08 + 0.05 * bass, 0.018 + 0.006 * beat)));
            }
            dSeq = dSpine; mSeq = 1.0;
            if (dRings < dSeq) { dSeq = dRings; mSeq = 4.0; }
            if (dCore < dSeq) { dSeq = dCore; mSeq = 2.0; }
        } else if (cur == 4) {
            // Comet head with flowing ember wake
            float dHead = csHull(w - vec3(0.70, 0.0, 0.0), 0.70, 0.16 + 0.02 * bass);
            float dWake = 1e5;
            for (int i = 0; i < 5; i++) {
                float fi = float(i);
                float along = -0.10 - fi * 0.34 - 0.12 * sin(tt * 0.90 + fi);
                float sway = 0.12 * sin(tt * 0.70 + fi * 1.3);
                float sz = 0.12 - fi * 0.016 + 0.03 * treble;
                vec3 c = vec3(along, sway, 0.08 * cos(fi + tt * 0.55));
                dWake = min(dWake, sdSphere(w - c, sz));
            }
            dSeq = dHead; mSeq = 1.0;
            if (dWake < dSeq) { dSeq = dWake; mSeq = 3.0; }
        } else {
            // Nova shock ring & radial spars
            float pulse = 0.55 + 0.28 * sin(tt * 0.80) + 0.16 * beat + 0.08 * bass;
            float dCore = sdSphere(w, 0.17 + 0.06 * subBass + 0.04 * beat);
            float dRing1 = sdTorus(w, vec2(abs(pulse) + 0.16, 0.022));
            vec3 q = w;
            q.xy = q.xy * rot2(tt * 0.35);
            float dRing2 = sdTorus(q, vec2(abs(pulse) * 0.70 + 0.20, 0.016));
            float dSpar = 1e5;
            for (int i = 0; i < 3; i++) {
                float a = float(i) * 2.0943951 + tt * 0.30;
                float rad = 0.35 + abs(pulse) * 0.45;
                vec3 tip = vec3(cos(a) * rad, 0.0, sin(a) * rad);
                dSpar = min(dSpar, sdCapsule(w, tip * 0.25, tip, 0.028));
            }
            dSeq = dCore; mSeq = 2.0;
            if (dRing1 < dSeq) { dSeq = dRing1; mSeq = 4.0; }
            if (dRing2 < dSeq) { dSeq = dRing2; mSeq = 2.0; }
            if (dSpar < dSeq) { dSeq = dSpar; mSeq = 4.0; }
        }

        if (pass == 0) {
            dMix += dSeq * weight;
            mBest = mSeq;
            bestDistance = dSeq;
        } else {
            dMix += dSeq * weight;
            if (dSeq < bestDistance && blend > 0.45) {
                mBest = mSeq;
                bestDistance = dSeq;
            }
        }
    }
    return vec2(dMix, mBest);
}

// ---------------------------------------------------------------------------
// Stream of Frequency 3D Rivers (Style 7 = Horizontal Vault, Style 8 = Vertical Rain)
// ---------------------------------------------------------------------------
float sofPidBursts(vec3 p, float vertical, float tt) {
    float heat = max(u_stream.z, 0.35 + 0.45 * u_audio2.x);
    float front = fract(tt * 0.115 + u_stream.y);
    float d = 1e5;
    for (int i = 0; i < 6; i++) {
        float fi = float(i);
        vec3 h = hash33(vec3(fi * 2.7 + u_stream.w * 1.3 + SEED, fi * 1.9, SEED));
        vec3 c;
        if (vertical > 0.5) {
            float ang = h.x * TAU + tt * 0.25;
            float rad = 1.15 * (1.0 + 0.45 * h.z);
            c = vec3(cos(ang) * rad,
                     mix(1.50, -1.50, front) + (h.y - 0.5) * 0.55,
                     sin(ang) * rad);
        } else {
            c = vec3(mix(-3.60, 3.60, front) + (h.x - 0.5) * 0.70,
                     (h.y - 0.5) * 1.55,
                     (h.z - 0.5) * 0.85);
        }
        d = min(d, sdSphere(p - c, 0.018 + 0.028 * h.z * heat));
    }
    return d;
}

vec2 mapStreamHorizontal(vec3 p, float tt, float bass, float beat) {
    float spanHalf = 3.80;
    float stackHalf = 2.15;
    float along = clamp(p.x / (2.0 * spanHalf) + 0.5, 0.0, 1.0);
    float arc = sin(along * PI + tt * 0.08) * 0.16;
    float taper = mix(0.42, 1.0, smoothstep(-spanHalf, spanHalf, p.x));
    float stepScale = 0.86 * clamp(taper, 0.42, 1.0);

    float dRibbon = 1e5;
    for (int i = 0; i < 12; i++) {
        float fi = float(i);
        float bVal = bandValue(i) * INTENSITY;
        float level = 0.52 + 0.48 * bVal;
        float centre = (fi - 5.5) * (stackHalf / 5.5) * taper + arc;
        centre += 0.065 * sin(p.x * 1.7 + tt * 0.58 + fi * 0.9);
        float depth = 0.48 * sin(p.x * 1.25 + tt * 0.44 + fi * 0.55) * taper;
        float radius = (0.040 + 0.028 * level + 0.012 * bass) * taper;
        float d = length(vec2(p.y - centre, p.z - depth)) - radius;
        d = max(d, abs(p.x) - spanHalf);
        dRibbon = min(dRibbon, d * stepScale);
    }

    float dRail = 1e5;
    for (int k = 0; k < 2; k++) {
        float fk = float(k) * 2.0 - 1.0;
        float centre = fk * (stackHalf + 0.24) * taper + arc;
        float depth = 0.50 * fk * sin(p.x * 0.9 + tt * 0.35) * taper;
        float d = length(vec2(p.y - centre, p.z - depth)) - 0.022 * taper;
        d = max(d, abs(p.x) - spanHalf * 1.04);
        dRail = min(dRail, d * stepScale);
    }

    float head = fract(tt * 0.115);
    vec3 headPos = vec3(mix(-spanHalf, spanHalf, head), sin(head * PI + tt * 0.08) * 0.16, 0.10 * sin(tt * 0.9));
    float dHead = sdSphere(p - headPos, 0.080 + 0.030 * beat);
    float dMote = sofPidBursts(p, 0.0, tt);

    vec2 res = vec2(dRibbon, 1.0);
    if (dRail < res.x) res = vec2(dRail, 2.0);
    if (dHead < res.x) res = vec2(dHead, 3.0);
    if (dMote < res.x) res = vec2(dMote, 4.0);
    return res;
}

vec2 mapStreamVertical(vec3 p, float tt, float bass, float treble, float beat) {
    float rainRad = 1.18;
    float rainHalf = 1.65;
    float dColumn = 1e5;
    float dGlyph = 1e5;

    for (int i = 0; i < 12; i++) {
        float fi = float(i);
        float bVal = bandValue(i) * INTENSITY;
        float level = 0.52 + 0.48 * bVal;
        float ang = (fi + 0.5) * (TAU / 12.0) + tt * 0.05;
        vec2 radial = vec2(cos(ang), sin(ang));
        vec2 tang = vec2(-radial.y, radial.x);

        float dRad = length(p.xz - radial * rainRad) - (0.034 + 0.018 * level);
        float spine = max(dRad, abs(p.y) - rainHalf);
        dColumn = min(dColumn, spine);

        float fallSpeed = 0.55 + 0.42 * hash11(fi * 3.7 + 1.0 + SEED);
        float period = 0.52;
        float phase = hash11(fi * 11.3 + SEED) * period;
        float yy = mod(p.y + tt * fallSpeed + phase + 0.5 * period, period) - 0.5 * period;
        vec3 local = vec3(dot(p.xz, tang), yy, dot(p.xz, radial) - rainRad);
        vec3 glyphHalf = vec3(0.054 + 0.030 * level, 0.110 + 0.080 * level, 0.022 + 0.014 * beat);
        float d = sdBox(local, glyphHalf);
        dGlyph = min(dGlyph, max(d, abs(p.y) - rainHalf));
    }

    float dRings = 1e5;
    for (int k = 0; k < 3; k++) {
        float fk = float(k);
        float ry = mix(-rainHalf, rainHalf, fract(tt * 0.09 + fk * 0.33));
        vec3 q = p - vec3(0.0, ry, 0.0);
        float d = sdTorus(q, vec2(rainRad + 0.07 + 0.05 * beat + 0.04 * bass, 0.015 + 0.010 * treble));
        dRings = min(dRings, d);
    }

    float dMote = sofPidBursts(p, 1.0, tt);
    vec2 res = vec2(dColumn, 1.0);
    if (dRings < res.x) res = vec2(dRings, 2.0);
    if (dGlyph < res.x) res = vec2(dGlyph, 3.0);
    if (dMote < res.x) res = vec2(dMote, 4.0);
    return res;
}

vec3 background(vec3 rd, vec2 uv, float t) {
    float subBass = u_audio0.x * BASS_DRIVE;
    float bass = u_audio0.y * BASS_DRIVE;
    float mid = u_audio0.w * MID_DRIVE;
    float treble = u_audio1.y * TREBLE_DRIVE;
    float rms = u_audio1.w;
    float beat = u_audio2.x * BASS_DRIVE;
    float bassPhase = u_audio2.y;
    float highPhase = u_audio2.w;
    float tt = t * SPEED;

    vec2 p3d = rd.xy / max(abs(rd.z) + 0.16, 0.22);
    p3d = clamp(p3d, -2.2, 2.2);
    float r3d = length(p3d);

    if (abs(BACKDROP_MODE - 1.0) < 0.5) {
        // Gravitational Lensing Singularity + Photon Ring + Flux Shafts
        vec2 centered = uv - 0.5;
        float r = length(centered);
        float theta = atan(centered.y, centered.x);
        float lensStrength = 0.075 * (1.0 + 0.38 * bass);
        float defl = lensStrength / (r + 0.08);
        vec2 lensedUv = centered * (1.0 - defl) + 0.5;
        lensedUv = (lensedUv - 0.5) * rot2(0.14 * sin(tt * 0.25 - r * 3.5)) + 0.5;
        vec3 coverTex = coverSampleDispersed(lensedUv, rd.xy, 0.014 * (1.0 + 0.65 * beat));
        float radVig = smoothstep(0.94, 0.14, r);
        vec3 bg = mix(PAL5 * 0.45, pow(clamp(coverTex, 0.0, 1.0), vec3(1.85)) * (0.44 + 0.32 * bass + 0.24 * beat), 0.52 + 0.48 * radVig);

        float photonRing = exp(-pow((r - 0.28) * 14.0, 2.0));
        vec3 ringFluxCol = mix(PAL0, PAL2, 0.5 + 0.5 * sin(theta * 3.0 + tt));
        bg += ringFluxCol * photonRing * (0.35 + 0.48 * beat) * PUNCH;

        vec2 fluxUv = (uv - 0.5) * rot2(0.42);
        float fluxMask = exp(-pow(fluxUv.x * (2.8 - 0.4 * bass), 2.0));
        float fluxStreaks = pow(0.5 + 0.5 * sin(fluxUv.x * 38.0 + sin(fluxUv.y * 7.0 + tt * 0.9) * 2.2), 3.0);
        vec3 fluxCol = mix(PAL1, PAL6, 0.5 + 0.5 * sin(fluxUv.y * 4.0 - tt * 0.6));
        bg += fluxCol * fluxMask * (0.14 + 0.22 * fluxStreaks) * (0.55 + 0.50 * mid);
        return bg;
    }

    if (abs(BACKDROP_MODE - 2.0) < 0.5) {
        // 3D Mirrored Perspective Grid + Chromatic Dispersion + Aurora
        vec2 waveUv = p3d * 0.36;
        waveUv += vec2(
            sin(p3d.y * 3.2 + tt * 0.38 + bassPhase * 0.30),
            cos(p3d.x * 3.2 - tt * 0.32 + highPhase * 0.25)
        ) * (0.040 + 0.028 * bass);
        vec2 finalUv = mix(mirrorUv(uv * 0.90 + 0.05), mirrorUv(waveUv + 0.5), 0.56);
        vec3 coverTex = coverSampleDispersed(finalUv, rd.xy, 0.016 * (1.0 + 0.75 * beat));

        float bgCycle = tt * 0.04;
        vec3 colA = mix(PAL5 * 0.6, PAL0 * 0.35, 0.5 + 0.5 * sin(bgCycle));
        vec3 colB = mix(PAL1 * 0.30, PAL2 * 0.28, 0.5 + 0.5 * sin(bgCycle * 1.4 + 1.2));
        vec3 bg = mix(colA, colB, clamp(length(rd.xy) * 0.85, 0.0, 1.0));
        bg += coverTex * (0.40 + 0.24 * rms);

        float gridY = abs(rd.y);
        if (gridY > 0.05) {
            vec2 gridCoord = rd.xz / (gridY + 0.001);
            gridCoord.y += tt * 0.28 + bassPhase * 0.15;
            vec2 gFract = abs(fract(gridCoord * 0.85) - 0.5);
            float gDist = min(gFract.x, gFract.y);
            float gridLines = smoothstep(0.042, 0.0, gDist) * exp(-abs(gridCoord.y) * 0.18) * (0.35 + 0.45 * bass);
            bg += mix(PAL0, PAL6, 0.5) * gridLines * PUNCH;
        }
        float aurora = clamp(0.18 * sin(rd.x * 5.0 + rd.y * 4.0 + tt * 0.6) + 0.12 * cos(rd.x * 9.0 - rd.y * 7.0 - tt * 0.45), 0.0, 1.0);
        bg += PAL2 * aurora * (0.55 + 0.55 * mid);
        return bg;
    }

    if (abs(BACKDROP_MODE - 3.0) < 0.5) {
        // Pale Studio Void + Chromatic Storm Shear + Furnace Glow
        vec2 warpUv = p3d * 0.30 + vec2(
            sin(p3d.y * 2.1 + tt * 0.26 + bassPhase * 0.20),
            cos(p3d.x * 2.1 - tt * 0.22 + highPhase * 0.16)
        ) * (0.030 + 0.026 * bass);
        vec2 baseUv = mix(mirrorUv(uv * 0.78 + 0.11), mirrorUv(warpUv + 0.5), 0.58);
        vec3 coverTex = coverSampleDispersed(baseUv, rd.xy, 0.018 * (1.0 + 0.85 * beat));
        float lumaBg = dot(coverTex, vec3(0.299, 0.587, 0.114));
        coverTex = clamp(mix(vec3(lumaBg), coverTex, 1.25) * 1.04 + 0.02, 0.0, 1.0);
        vec3 paleVoid = mix(PAL3, vec3(0.898, 0.906, 0.936), 0.65);
        float vigBg = smoothstep(1.65, 0.28, r3d);
        vec3 bg = mix(coverTex * 0.94, paleVoid, (1.0 - vigBg) * 0.28);
        float furnaceGlow = exp(-abs(rd.y + 0.35) * 4.2);
        bg += PAL0 * furnaceGlow * (0.045 + 0.040 * mid + 0.030 * subBass);
        return bg;
    }

    if (abs(BACKDROP_MODE - 4.0) < 0.5) {
        // Spectrum Ribbon Wash + Horizontal FFT Ripple Bands
        vec2 waveUv = p3d * 0.30;
        waveUv.x -= tt * 0.15;
        waveUv += vec2(
            sin(p3d.y * 2.6 + tt * 0.30 + bassPhase * 0.25),
            cos(p3d.x * 2.2 - tt * 0.24 + highPhase * 0.18)
        ) * (0.034 + 0.026 * bass);
        vec2 finalUv = mix(mirrorUv(uv * 0.90 + 0.05), mirrorUv(waveUv + 0.5), 0.54);
        vec3 coverTex = coverSampleDispersed(finalUv, rd.xy, 0.014 * (1.0 + 0.60 * beat));
        vec3 voidCol = mix(PAL5 * 0.85, PAL4 * 0.30, clamp(length(rd.xy) * 0.80, 0.0, 1.0));
        float bandsMix = 0.35 * (u_bands0.x + u_bands1.y + u_bands2.z);
        float ripple = (0.5 + 0.5 * sin(p3d.y * 16.0 + tt * 1.35 + bandsMix * 5.5)) * smoothstep(1.15, 0.15, abs(p3d.y));
        vec3 colRipple = mix(PAL0, PAL1, 0.5 + 0.5 * sin(tt * 0.5 + p3d.x * 3.0));
        return mix(voidCol, coverTex, 0.48) + colRipple * ripple * 0.22 * (0.70 + 0.70 * mid);
    }

    // BACKDROP_MODE == 0: Deep Cosmic Nebula + Diagonal Aurora Beam + Bokeh Splatter
    float horizon = clamp(rd.y * 0.5 + 0.5, 0.0, 1.0);
    vec3 c = mix(PAL5 * 0.85, PAL0 * 0.22, horizon);
    vec2 flowUv = (uv - 0.5) * 0.88 * rot2(0.04 * sin(tt * 0.14)) + 0.5
        + 0.032 * vec2(sin(uv.y * 5.0 + tt * 0.35), cos(uv.x * 5.0 - tt * 0.30));
    vec3 coverTex = coverSampleDispersed(flowUv, rd.xy, 0.012 * (1.0 + 0.55 * beat));
    c = mix(c, coverTex * 0.64, 0.36 + 0.14 * rms);

    float neb = valueNoise3(rd * 3.4 + vec3(0.0, 0.0, tt * 0.032));
    c += paletteFast(neb + 0.18) * (0.09 + 0.11 * PUNCH) * (0.45 + 0.55 * rms);

    // Diagonal Aurora Light Shaft
    vec2 diagUv = (uv - 0.5) * rot2(-0.58);
    float beamMask = exp(-pow(diagUv.x * (3.1 - 0.45 * bass), 2.0));
    float beamStreaks = pow(0.5 + 0.5 * sin(diagUv.x * 42.0 + sin(diagUv.y * 8.0 + tt * 0.8) * 2.5), 3.0);
    vec3 beamColor = mix(PAL0, PAL1, 0.5 + 0.5 * sin(diagUv.y * 3.5 - tt * 0.5));
    beamColor = mix(beamColor, PAL6, smoothstep(0.14, 0.42, abs(diagUv.x)));
    c += beamColor * beamMask * (0.14 + 0.22 * beamStreaks) * (0.50 + 0.50 * mid + 0.35 * beat);

    // 3D Starlight & Bokeh Field
    vec3 cell = floor(rd * 145.0);
    float star = hash21(cell.xy + cell.z * 9.73 + SEED);
    if (star > 0.9958) {
        float glint = smoothstep(0.9958, 1.0, star);
        c += mix(PAL3, vec3(0.88, 0.94, 1.0), 0.6) * glint * (0.60 + 0.55 * treble);
    }
    float vign = 1.0 - 0.28 * dot(uv - 0.5, uv - 0.5);
    return c * clamp(vign, 0.0, 1.0);
}

vec2 mapScene(vec3 p) {
    float t = u_res.z;
    float subBass = u_audio0.x * BASS_DRIVE;
    float bass = u_audio0.y * BASS_DRIVE;
    float lowMid = u_audio0.z * MID_DRIVE;
    float mid = u_audio0.w * MID_DRIVE;
    float highMid = u_audio1.x * MID_DRIVE;
    float treble = u_audio1.y * TREBLE_DRIVE;
    float air = u_audio1.z * TREBLE_DRIVE;
    float rms = u_audio1.w;
    float beat = u_audio2.x * BASS_DRIVE;
    float bassPhase = u_audio2.y;
    float midPhase = u_audio2.z;
    float highPhase = u_audio2.w;
    float tt = t * SPEED;

    // Dedicated Stream of Frequency 3D Rivers (Styles 7 & 8). The rivers are a
    // long bounded corridor, so a mirror fold would collapse their span; they
    // keep their own geometry and ignore the symmetry control.
    if (abs(STYLE - 7.0) < 0.5) {
        return mapStreamHorizontal(p, tt, bass, beat);
    }
    if (abs(STYLE - 8.0) < 0.5) {
        return mapStreamVertical(p, tt, bass, treble, beat);
    }

    vec3 w = p;
    if (abs(STYLE - 10.0) < 0.5) {
        // Mirrored Madness rapid non-linear snap turns & transient micro-glitch
        float snapAngle = floor(bassPhase * 0.35) * 1.5707963
            + smoothstep(0.0, 0.22, fract(bassPhase * 0.35)) * 1.5707963;
        float twitch = (beat > 0.58 ? 0.085 : 0.0) * sin(highPhase * 7.5);
        w.xz = w.xz * rot2(snapAngle + tt * 0.14 + twitch);
        w.xy = w.xy * rot2(0.32 * sin(tt * 0.25) + twitch * 0.55);
    } else {
        w.xz = w.xz * rot2(tt * 0.075 + 0.14 * sin(tt * 0.11 + bassPhase * 0.1));
        w.yz = w.yz * rot2(0.06 * sin(tt * 0.09 + midPhase * 0.08));
    }
    w = applySymmetry(w, tt, bassPhase);

    // Chromatic Storm 6-vessel sequence crossfader (Style 6)
    if (abs(STYLE - 6.0) < 0.5) {
        return mapChromaticStorm(w, tt, subBass, bass, treble, beat);
    }

    // Resonant Fluid Gyroscope (Style 5)
    if (abs(STYLE - 5.0) < 0.5) {
        float rBase = 0.58 + INTENSITY * (0.16 * subBass + 0.08 * beat);
        float dCore = evalCoreShape(w, rBase, tt, bass, lowMid, mid, beat, bassPhase, midPhase, highPhase);

        // Tri-Axis Interlocking Gyroscopic Fluid Toroids
        vec3 g1 = w; g1.xz = g1.xz * rot2(tt * 0.12 + bassPhase * 0.06);
        float dG1 = sdTorus(g1, vec2(1.18 + 0.15 * bass, 0.13 + 0.04 * subBass))
            + 0.025 * sin(g1.x * 5.5 + tt * 0.5);
        vec3 g2 = w; g2.yz = g2.yz * rot2(1.047 + tt * 0.09); g2.xy = g2.xy * rot2(tt * 0.07);
        float dG2 = sdTorus(g2, vec2(1.44 + 0.16 * lowMid, 0.11 + 0.03 * bass));
        float dLiquid = smin(smin(dCore, dG1, 0.15), dG2, 0.14);

        // Relativistic Accretion Halos
        vec3 h1 = w; h1.yz = h1.yz * rot2(0.26 + 0.04 * sin(tt * 0.05));
        float dHalos = sdTorus(h1, vec2(2.05 + 0.20 * subBass, 0.038 + 0.018 * beat))
            + 0.012 * sin(length(h1.xz) * 22.0 - tt * 1.8);

        // Braided Helical Primordial Liquidstreams
        float dStreams = 1e5;
        for (int i = 0; i < TENDRIL_COUNT; i++) {
            float fi = float(i);
            float yRel = w.y;
            float radH = 0.88 + 0.14 * sin(yRel * 2.2 + tt * 0.8 + fi * 1.047) + 0.12 * bass;
            float theta = yRel * 1.35 + tt * 0.70 + fi * (TAU / float(max(TENDRIL_COUNT, 1))) + bassPhase * 0.25;
            vec2 expXz = vec2(cos(theta), sin(theta)) * radH;
            float dS = length(w.xz - expXz) - (0.038 + 0.014 * mid);
            dStreams = min(dStreams, max(dS, abs(yRel) - 2.65));
        }

        // Articulating Biomech Flipper Wings & Satellite Octahedra
        float dTech = 1e5;
        float flap = 0.32 * sin(tt * 0.85 + bassPhase * 0.75) * (1.0 + 0.85 * beat);
        for (int k = 0; k < SHARD_COUNT; k++) {
            float fk = float(k);
            float wAng = fk * (TAU / float(max(SHARD_COUNT, 1)));
            vec3 pWing = w;
            pWing.xz = pWing.xz * rot2(wAng + tt * 0.04);
            pWing.x -= (1.82 + 0.20 * bass + 0.10 * beat);
            pWing.yz = pWing.yz * rot2(flap * ((k % 2 == 0) ? 1.0 : -1.0));
            dTech = min(dTech, sdHexPrism(pWing, vec2(0.32 + 0.05 * beat, 0.032)));
        }

        vec2 res = vec2(dLiquid, 1.0);
        if (dHalos < res.x) res = vec2(dHalos, 2.0);
        if (dStreams < res.x) res = vec2(dStreams, 3.0);
        if (dTech < res.x) res = vec2(dTech, 4.0);
        return res;
    }

    // Particle Collider Synchrotron (Style 9)
    if (abs(STYLE - 9.0) < 0.5) {
        vec3 pCore = w;
        pCore.xz = pCore.xz * rot2(tt * 0.18 + bassPhase * 0.25);
        float dT1 = sdTorus(pCore, vec2(1.55 + 0.18 * bass, 0.10 + 0.03 * lowMid));
        vec3 pCore2 = pCore;
        pCore2.xy = pCore2.xy * rot2(0.85);
        pCore2.xz = pCore2.xz * rot2(-tt * 0.28);
        float dT2 = sdTorus(pCore2, vec2(1.85 + 0.15 * mid, 0.08 + 0.02 * treble));
        float dSpindle = evalCoreShape(pCore, 0.48 + 0.20 * subBass + 0.10 * beat, tt, bass, lowMid, mid, beat, bassPhase, midPhase, highPhase);
        float dGlass = smin(smin(dT1, dT2, 0.14), dSpindle, 0.16);

        float dHalos = min(
            sdTorus(pCore, vec2(1.55 + 0.18 * bass, 0.026 + 0.020 * beat)),
            sdTorus(pCore2, vec2(1.85 + 0.15 * mid, 0.022 + 0.016 * beat))
        );

        float dBeams = 1e5;
        for (int i = 0; i < 4; i++) {
            float fi = float(i);
            float yRel = pCore.y;
            float radH = 0.92 + 0.14 * sin(yRel * 3.0 + tt * 1.5 + fi * 1.57) + 0.10 * bass;
            float theta = yRel * 2.3 + tt * 1.7 + fi * 1.5707 + bassPhase * 0.4;
            float dS = length(pCore.xz - vec2(cos(theta), sin(theta)) * radH) - (0.032 + 0.016 * mid);
            dBeams = min(dBeams, max(dS, abs(yRel) - 2.5));
        }

        float dCoils = 1e5;
        for (int k = 0; k < 4; k++) {
            vec3 pCoil = pCore;
            pCoil.xz = pCoil.xz * rot2(float(k) * 1.5707963);
            pCoil.x -= (1.98 + 0.12 * bass);
            dCoils = min(dCoils, sdBox(pCoil, vec3(0.22, 0.36, 0.15 + 0.05 * beat)));
        }

        vec2 res = vec2(dGlass, 1.0);
        if (dHalos < res.x) res = vec2(dHalos, 2.0);
        if (dBeams < res.x) res = vec2(dBeams, 3.0);
        if (dCoils < res.x) res = vec2(dCoils, 4.0);
        return res;
    }

    // Twin Planetary Worlds (Style 11)
    if (abs(STYLE - 11.0) < 0.5) {
        vec3 cA = vec3(1.10 + 0.30 * sin(tt * 0.37 + bassPhase * 0.25),
                       0.48 + 0.24 * cos(tt * 0.29 + midPhase * 0.20),
                       0.22 * sin(tt * 0.31));
        vec3 cB = vec3(-1.18 + 0.32 * cos(tt * 0.33 - bassPhase * 0.22),
                       -0.42 + 0.26 * sin(tt * 0.41 + midPhase * 0.18),
                       -0.25 * cos(tt * 0.27));
        float rA = 0.64 + 0.15 * subBass + 0.07 * beat;
        float rB = 0.52 + 0.13 * bass + 0.06 * beat;
        float dOrbA = evalCoreShape(w - cA, rA, tt, bass, lowMid, mid, beat, bassPhase, midPhase, highPhase);
        float dOrbB = evalCoreShape(w - cB, rB, tt, bass, lowMid, mid, beat, bassPhase, midPhase, highPhase);
        float dBridge = sdCapsule(w, cA, cB, 0.08 + 0.09 * rms + 0.05 * beat)
            + 0.035 * sin(length(w) * 9.0 - bassPhase * 2.0);
        float dLiquid = smin(smin(dOrbA, dOrbB, 0.50 + 0.20 * mid), dBridge, 0.42);

        vec3 pRA = w - cA;
        pRA.xy = pRA.xy * rot2(-0.28 + 0.08 * sin(tt * 0.25));
        pRA.yz = pRA.yz * rot2(1.12 + 0.10 * cos(tt * 0.32));
        float dRings = min(
            sdTorus(pRA, vec2(rA * 1.62 + 0.12 * mid, 0.030 + 0.016 * highMid)),
            sdTorus(pRA, vec2(rA * 2.02 + 0.16 * treble, 0.020 + 0.014 * beat))
        );
        vec3 pRB = w - cB;
        pRB.xy = pRB.xy * rot2(0.42 + 0.09 * cos(tt * 0.28));
        pRB.yz = pRB.yz * rot2(1.05 - 0.12 * sin(tt * 0.35));
        dRings = min(dRings, sdTorus(pRB, vec2(rB * 1.68 + 0.14 * lowMid, 0.032 + 0.018 * bass)));

        // Cascading root filaments + neural web
        vec3 pWeb = w;
        pWeb.xz = pWeb.xz * rot2(tt * 0.18);
        vec3 webTwist = pWeb + 0.30 * sin(pWeb.yzx * 1.8 + tt * 0.7);
        float dWeb = max(length(abs(fract(webTwist * 0.65) - 0.5) - vec3(0.0, 0.0, 0.42)) - (0.014 + 0.012 * treble), length(w) - 3.4) * 0.6;

        vec2 res = vec2(dLiquid, 1.0);
        if (dRings < res.x) res = vec2(dRings, 2.0);
        if (dWeb < res.x) res = vec2(dWeb, 3.0);
        return res;
    }

    // Standard Modular Styles: 0 (Hybrid), 1 (Orbs), 2 (Rings), 3 (Tendrils), 4 (Shards), 10 (Mirrored Madness)
    bool hy = (STYLE < 0.5) || (abs(STYLE - 10.0) < 0.5);
    bool useOrbs = hy || abs(STYLE - 1.0) < 0.5;
    bool useRings = hy || abs(STYLE - 2.0) < 0.5;
    bool useTendrils = hy || abs(STYLE - 3.0) < 0.5;
    bool useShards = hy || abs(STYLE - 4.0) < 0.5;

    float dOrb = 1e6;
    float dRing = 1e6;
    float dTendril = 1e6;
    float dShard = 1e6;

    if (useOrbs) {
        vec3 center = vec3(0.0, 0.05 * sin(tt * 0.47), 0.0);
        float radius = 0.56 + INTENSITY * (0.16 * bass + 0.08 * beat);
        dOrb = evalCoreShape(w - center, radius, tt, bass, lowMid, mid, beat, bassPhase, midPhase, highPhase);
        for (int i = 0; i < SAT_COUNT; i++) {
            float fi = float(i);
            float bBand = bandValue(i % 4) * INTENSITY;
            float a = tt * (0.35 + 0.09 * fi) + fi * (TAU / float(max(SAT_COUNT, 1))) + SEED * 0.7;
            float rad = 1.04 + 0.14 * fi + 0.14 * bass + 0.12 * bBand;
            vec3 c = vec3(cos(a) * rad,
                          0.36 * sin(tt * 0.53 + fi * 2.1) + 0.12 * sin(a * 2.0),
                          sin(a) * rad);
            float rr = 0.15 + 0.06 * bass + 0.04 * bBand + 0.025 * beat;
            dOrb = smin(dOrb, sdSphere(w - c, rr), 0.28 + 0.10 * bass);
        }
    }

    if (useRings) {
        for (int i = 0; i < RING_COUNT; i++) {
            float fi = float(i);
            float bBand = bandValue(4 + (i % 4)) * INTENSITY;
            vec3 b = w;
            b.yz = b.yz * rot2(0.52 + 0.24 * fi + tt * (0.14 + 0.05 * fi));
            b.xy = b.xy * rot2(0.37 * fi + tt * 0.065 * (fi + 1.0));
            float rad = 0.98 + 0.26 * fi + INTENSITY * (0.13 * bass + 0.06 * bBand + 0.04 * beat);
            float tube = 0.022 + 0.010 * treble + 0.006 * beat;
            float d = sdTorus(b, vec2(rad, tube));
            d += 0.012 * sin(atan(b.y, b.x) * 6.0 + tt * 1.35 + fi * 1.7) * (0.4 + 0.6 * PUNCH);
            dRing = min(dRing, d);
        }
    }

    if (useTendrils) {
        for (int i = 0; i < TENDRIL_COUNT; i++) {
            float fi = float(i);
            float bBand = bandValue(8 + (i % 4)) * INTENSITY;
            float phase = tt * (0.50 + 0.08 * fi) + fi * (TAU / float(max(TENDRIL_COUNT, 1))) + SEED * 1.3;
            float amp = 0.42 + INTENSITY * (0.10 * mid + 0.08 * bBand);
            float span = 2.40;
            float x0 = -0.5 * span + fi * (span / float(max(TENDRIL_COUNT, 1)));
            float x1 = x0 + 0.56;
            vec3 a = vec3(x0,
                          amp * sin(x0 * 2.40 + phase + bassPhase * 0.16),
                          0.24 * cos(x0 * 3.10 + phase * 0.8 + midPhase * 0.16));
            vec3 b = vec3(x1,
                          amp * sin(x1 * 2.40 + phase + bassPhase * 0.16),
                          0.24 * cos(x1 * 3.10 + phase * 0.8 + midPhase * 0.16));
            dTendril = min(dTendril, sdCapsule(w, a, b, 0.040 + 0.016 * treble));
            for (int j = 0; j < 3; j++) {
                float fj = float(j);
                float along = 0.15 + 0.32 * fj + 0.04 * sin(tt * 1.3 + fi + fj);
                vec3 c = mix(a, b, along);
                float bead = 0.046 + 0.022 * beat + 0.014 * highMid;
                dTendril = min(dTendril, sdSphere(w - c, bead));
            }
        }
    }

    if (useShards) {
        for (int i = 0; i < SHARD_COUNT; i++) {
            float fi = float(i);
            float bBand = bandValue(12 + (i % 4)) * INTENSITY;
            float a = tt * (0.24 + 0.055 * fi) + fi * (TAU / float(max(SHARD_COUNT, 1))) + SEED * 1.9;
            float rad = 1.30 + 0.20 * sin(fi * 2.0 + tt * 0.35) + 0.065 * fi + 0.14 * bBand;
            vec3 c = vec3(cos(a) * rad,
                          0.58 * sin(tt * 0.31 + fi * 1.7) + 0.12 * sin(a * 2.2),
                          sin(a) * rad);
            vec3 q = w - c;
            q.xz = q.xz * rot2(tt * (0.46 + 0.09 * fi));
            q.xy = q.xy * rot2(tt * 0.28 + fi * 0.83);
            float scale = 0.11 + 0.016 * fi + INTENSITY * (0.038 * beat + 0.018 * treble);
            float dShape;
            if (i % 3 == 0) {
                dShape = sdHexPrism(q, vec2(scale * 1.45, 0.026 + 0.008 * air));
            } else if (i % 3 == 1) {
                dShape = sdOctahedron(q, scale * 1.35);
            } else {
                dShape = sdBox(q, vec3(scale, 0.032 + 0.008 * air, scale * 1.55));
            }
            dShard = min(dShard, dShape);
        }
    }

    float d = 1e6;
    float matId = 1.0;
    if (useOrbs) { d = dOrb; matId = 1.0; }
    if (useRings && dRing < d) { d = dRing; matId = 2.0; }
    if (useTendrils && dTendril < d) { d = dTendril; matId = 3.0; }
    if (useShards && dShard < d) { d = dShard; matId = 4.0; }
    return vec2(d, matId);
}

vec3 normalAt(vec3 p) {
    vec2 e = vec2(0.0020, -0.0020);
    return normalize(
        e.xyy * mapScene(p + e.xyy).x +
        e.yyx * mapScene(p + e.yyx).x +
        e.yxy * mapScene(p + e.yxy).x +
        e.xxx * mapScene(p + e.xxx).x
    );
}

float calcAO(vec3 pos, vec3 nor) {
    float occ = 0.0;
    float sca = 1.0;
    for (int i = 0; i < 5; i++) {
        float h = 0.015 + 0.11 * float(i) / 4.0;
        float d = mapScene(pos + h * nor).x;
        occ += (h - d) * sca;
        sca *= 0.72;
    }
    return clamp(1.0 - 2.2 * occ, 0.0, 1.0);
}

void main() {
    vec2 fragCoord = vUv * u_res.xy;
    vec2 uv = (fragCoord - 0.5 * u_res.xy) / u_res.y;
    vec2 screenUv = vec2(vUv.x, vUv.y);
    float t = u_res.z;
    float subBass = u_audio0.x * BASS_DRIVE;
    float bass = u_audio0.y * BASS_DRIVE;
    float mid = u_audio0.w * MID_DRIVE;
    float treble = u_audio1.y * TREBLE_DRIVE;
    float rms = u_audio1.w;
    float beat = u_audio2.x * BASS_DRIVE;
    float bassPhase = u_audio2.y;
    float midPhase = u_audio2.z;
    float highPhase = u_audio2.w;
    float tt = t * SPEED;

    // 3D Camera Modes
    float baseDist = 5.05 / max(CAMERA_ZOOM, 0.35);
    if (abs(STYLE - 7.0) < 0.5) baseDist = 7.40 / max(CAMERA_ZOOM, 0.35);
    if (abs(STYLE - 8.0) < 0.5) baseDist = 5.85 / max(CAMERA_ZOOM, 0.35);

    float camDist = baseDist - INTENSITY * (0.44 * bass + 0.20 * beat)
        + 0.30 * sin(tt * 0.19) + 0.10 * sin(tt * 0.07 + SEED);
    float camYaw = tt * 0.16 + 0.28 * sin(tt * 0.10 + bassPhase * 0.08) + SEED * 0.6;
    float camPitch = 0.17 * sin(tt * 0.14 + midPhase * 0.06);
    float roll = 0.075 * sin(tt * 0.21 + highPhase * 0.05);

    if (abs(CAMERA_MODE - 1.0) < 0.5) {
        // Dynamic Sequence & Multi-Angle Cuts
        float seqCycle = fract(tt * 0.025 + SEED * 0.11);
        int seg = int(clamp(floor(seqCycle * 6.0), 0.0, 5.0));
        float wide = (seg == 2 || seg == 4) ? 0.45 : 0.0;
        camDist = baseDist + wide - INTENSITY * (0.34 * bass + 0.16 * beat) + 0.14 * sin(tt * 0.12);
        camYaw = tt * 0.08 + 0.70 + float(seg) * 0.68;
        camPitch = 0.24 + 0.07 * sin(tt * 0.07 + midPhase * 0.05);
    } else if (abs(CAMERA_MODE - 2.0) < 0.5) {
        // Snap-Turn Kinetic Saccade
        float snap = floor(bassPhase * 0.32) * 1.0471975
            + smoothstep(0.0, 0.20, fract(bassPhase * 0.32)) * 1.0471975;
        camYaw = snap + tt * 0.12 + SEED * 0.5;
        camPitch = 0.22 * sin(tt * 0.25 + midPhase * 0.12);
        roll = 0.14 * sin(tt * 0.35) + (beat > 0.55 ? 0.06 * sin(highPhase * 9.0) : 0.0);
    } else if (abs(CAMERA_MODE - 3.0) < 0.5) {
        // Deep Pendulum Swoop & Roll
        camDist = baseDist * (0.88 + 0.24 * cos(tt * 0.24)) - INTENSITY * 0.38 * bass;
        camYaw = 1.15 * sin(tt * 0.15 + SEED) + 0.25 * bassPhase * 0.1;
        camPitch = 0.48 * sin(tt * 0.19 + midPhase * 0.08);
        roll = 0.20 * sin(tt * 0.22);
    } else if (abs(CAMERA_MODE - 4.0) < 0.5) {
        // Wide Panoramic Vault View
        camDist = baseDist * 1.26 - INTENSITY * (0.36 * bass + 0.18 * beat);
        camYaw = 0.34 * sin(tt * 0.09 + bassPhase * 0.04) + 0.22;
        camPitch = 0.20 + 0.10 * sin(tt * 0.11 + midPhase * 0.05);
    }

    vec3 ro = vec3(
        sin(camYaw) * cos(camPitch) * camDist,
        sin(camPitch) * camDist + 0.16,
        cos(camYaw) * cos(camPitch) * camDist
    );
    vec3 ta = vec3(
        0.13 * sin(tt * 0.23),
        0.10 * cos(tt * 0.19),
        0.0
    );
    vec3 ww = normalize(ta - ro);
    vec3 uu = normalize(cross(ww, vec3(0.0, 1.0, 0.0)));
    vec3 vv = normalize(cross(uu, ww));
    vec2 rotUv = uv * rot2(roll);
    vec3 rd = normalize(rotUv.x * uu + rotUv.y * vv + (1.34 - 0.13 * beat) * ww);

    vec3 bgCol = background(rd, screenUv, t);
    vec3 col = bgCol;
    vec3 glowAccum = vec3(0.0);
    float dist = 0.0;
    float tMax = (abs(STYLE - 7.0) < 0.5 || abs(STYLE - 8.0) < 0.5) ? 16.5 : 13.5;
    bool hit = false;
    float matId = 1.0;

    for (int i = 0; i < MARCH_STEPS; i++) {
        vec3 p = ro + rd * dist;
        vec2 h = mapScene(p);
        float d = h.x;
        float glow = exp(-d * 10.5) / (1.0 + d * d * 95.0);
        if (glow > 0.0006) {
            vec3 gCol = paletteFast(length(p) * 0.23 + t * 0.045 + SEED + h.y * 0.18);
            float gWeight = (h.y > 1.5 && h.y < 3.5) ? 1.25 : 0.85;
            glowAccum += gCol * glow * gWeight * BLOOM_STRENGTH * (0.08 + 0.16 * PUNCH + 0.10 * beat);
        }
        if (d < 0.0022) {
            hit = true;
            matId = h.y;
            break;
        }
        dist += max(d * 0.84, 0.0024);
        if (dist > tMax) break;
    }

    if (hit) {
        vec3 p = ro + rd * dist;
        vec3 n = normalAt(p);
        vec3 v = -rd;
        float ao = calcAO(p, n);
        vec3 lightDir1 = normalize(vec3(0.60, 0.82, 0.45));
        vec3 lightDir2 = normalize(vec3(-0.72, -0.45, -0.52));
        float diff1 = max(dot(n, lightDir1), 0.0);
        float diff2 = max(dot(n, lightDir2), 0.0);
        float fres = pow(clamp(1.0 - max(dot(n, v), 0.0), 0.0, 1.0), 3.0);
        float spec1 = pow(max(dot(reflect(-lightDir1, n), v), 0.0), 56.0);
        float spec2 = pow(max(dot(reflect(-lightDir2, n), v), 0.0), 28.0);
        vec3 reflDir = reflect(rd, n);
        vec3 envRefl = background(reflDir, mirrorUv(screenUv + reflDir.xy * 0.20), t);

        vec3 surf;
        if (matId < 1.5) {
            // Material 1: Translucent Liquid Glass with 3-channel RGB chromatic dispersion
            float iorG = 1.0 / (1.15 + 0.34 * max(REFRACTION_STRENGTH, 0.1));
            float dispShift = 0.035 * DISPERSION_STRENGTH;
            vec3 refrR = refract(rd, n, clamp(iorG + dispShift, 0.45, 0.98));
            vec3 refrG = refract(rd, n, clamp(iorG, 0.45, 0.98));
            vec3 refrB = refract(rd, n, clamp(iorG - dispShift, 0.45, 0.98));
            float rScale = 0.28 * REFRACTION_STRENGTH;
            vec3 art;
            art.r = coverSample(screenUv + refrR.xy * rScale).r;
            art.g = coverSample(screenUv + refrG.xy * rScale).g;
            art.b = coverSample(screenUv + refrB.xy * rScale).b;

            float innerCaust = valueNoise3(p * 3.6 + vec3(tt * 0.42, -bassPhase * 0.3, midPhase * 0.25));
            vec3 inner = signaturePalette(innerCaust + dot(n, vec3(0.45, 0.62, 0.30)) + t * 0.045 + SEED, screenUv);
            float core = pow(max(dot(n, v), 0.0), 1.55);
            surf = mix(art * 0.92, PAL5 * 1.35, 0.38 * core);
            surf = mix(surf, inner, 0.42 + 0.20 * bass);
            surf *= (0.62 + 0.48 * diff1) * (0.58 + 0.42 * ao);
            surf += mix(PAL0, PAL6, 0.5 + 0.5 * n.y) * fres * (1.20 + 0.65 * rms);
            surf += PAL7 * spec2 * 0.70;
        } else if (matId < 2.5) {
            // Material 2: Incandescent Accretion Rings & Molten Furnace Cores
            float wave = 0.5 + 0.5 * sin(length(p) * 19.0 - bassPhase * 2.5 + SEED);
            surf = mix(PAL1, PAL0, wave);
            surf = mix(surf, PAL6, smoothstep(0.35, 0.88, sin(p.x * 6.0 + tt * 0.9)));
            surf *= (0.58 + 0.72 * diff1) * (0.65 + 0.35 * ao) * (1.15 + 0.65 * beat);
            surf += PAL2 * fres * (1.15 + 0.45 * mid);
        } else if (matId < 3.5) {
            // Material 3: Mycelial Conduits, Travelling Light Heads & Falling Glyphs
            float pulse = pow(0.5 + 0.5 * sin(p.y * 8.0 + bassPhase * 2.2 + tt * 0.6), 4.0);
            surf = signaturePalette(p.y * 0.25 + p.x * 0.15 + t * 0.05, screenUv);
            surf = surf * (0.40 + 0.66 * diff1 + 0.22 * diff2) * (0.62 + 0.38 * ao);
            surf += mix(PAL6, PAL3, 0.45) * pulse * (0.75 + 0.90 * bass + 0.45 * beat);
            surf += PAL7 * fres * 1.05;
        } else {
            // Material 4: Kinetic Origami Shards, Chrome Fins & PID Spark Motes
            float facet = 0.5 + 0.5 * sin(dot(p, vec3(3.0, 5.0, 7.0)) + tt * 0.6 + highPhase);
            surf = mix(vec3(0.024, 0.032, 0.068), envRefl, 0.58 + 0.32 * fres);
            surf *= (0.55 + 0.55 * diff1) * (0.60 + 0.40 * ao);
            surf += paletteFast(facet + t * 0.04) * fres * (1.15 + 0.85 * treble);
            surf += PAL6 * spec2 * 0.45;
        }
        surf += vec3(1.0, 0.98, 0.95) * spec1 * (0.85 + 1.05 * beat);
        float fog = smoothstep(tMax * 0.46, tMax, dist);
        col = mix(surf, bgCol, fog * 0.76);
    }

    col += glowAccum * (0.68 + 0.32 * rms + 0.28 * beat);
    float vig = 1.0 - 0.25 * dot(uv, uv);
    col *= clamp(vig, 0.0, 1.0);
    col = (col * (2.51 * col + 0.03)) / (col * (2.43 * col + 0.59) + 0.14);
    col = pow(clamp(col, 0.0, 1.0), vec3(0.92));
    outColor = vec4(col, 1.0);
}
