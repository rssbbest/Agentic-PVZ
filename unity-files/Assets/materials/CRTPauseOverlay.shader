Shader "UI/CRTPauseOverlay"
{
    // Put this on a Material, assign it to CRTPauseController.
    // Works on a full-screen RawImage in a Screen Space - Overlay canvas (URP or built-in).
    Properties
    {
        [PerRendererData] _MainTex ("Game Render", 2D) = "white" {}

        [Header(Driven by script)]
        _Amount ("Effect Amount (0 = plain game, 1 = full CRT)", Range(0,1)) = 1
        _Aspect ("Screen Aspect", Float) = 1.7778
        _UTime ("Unscaled Time", Float) = 0
        _PixelSize ("Texel Size (1/w, 1/h)", Vector) = (0.002, 0.002, 0, 0)

        [Header(Glass shape)]
        _Curvature ("Bulge", Range(0,0.4)) = 0.12
        _Border ("Visible TV Border Size (0.06 = small)", Range(0,0.3)) = 0.06
        _ContentZoom ("Extra Screen Zoom (fills edges)", Range(1,1.2)) = 1.015
        _Tilt ("Viewing Angle Tilt (x,y)", Vector) = (0.025, -0.02, 0, 0)
        _CornerRadius ("Glass Corner Radius", Range(0,0.6)) = 0.14

        [Header(Bezel)]
        _BezelWidth ("Bezel Width", Range(0.02,0.6)) = 0.16
        _BezelColor ("Bezel Color", Color) = (0.035, 0.035, 0.04, 1)
        _RoomColor ("Room Color", Color) = (0.004, 0.004, 0.006, 1)

        [Header(CRT look)]
        _ScanlineCount ("Scanlines", Float) = 240
        _ScanlineStrength ("Scanline Strength", Range(0,1)) = 0.4
        _MaskCount ("RGB Mask Columns", Float) = 480
        _MaskStrength ("RGB Mask Strength", Range(0,1)) = 0.25
        _Blur ("Phosphor Blur", Range(0,3)) = 1.2
        _Aberration ("Color Fringing", Range(0,3)) = 1.0
        _RollSpeed ("Roll Bar Speed", Float) = 0.12
        _RollStrength ("Roll Bar Strength", Range(0,1)) = 0.5
        _Jitter ("Line Jitter", Range(0,3)) = 1.0
        _Flicker ("Flicker", Range(0,0.3)) = 0.06
        _FlickerFps ("Flicker / Noise Rate (Hz)", Float) = 24
        _Noise ("Static Noise", Range(0,0.2)) = 0.04
        _Vignette ("Vignette", Range(0,1.5)) = 0.7
        _Reflection ("Glass Reflection", Range(0,1)) = 0.5
    }

    SubShader
    {
        Tags { "Queue"="Transparent" "IgnoreProjector"="True" "RenderType"="Transparent" "PreviewType"="Plane" "CanUseSpriteAtlas"="True" }
        Cull Off
        Lighting Off
        ZWrite Off
        ZTest Always
        Blend SrcAlpha OneMinusSrcAlpha

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            struct appdata
            {
                float4 vertex : POSITION;
                float4 color  : COLOR;
                float2 uv     : TEXCOORD0;
            };

            struct v2f
            {
                float4 pos   : SV_POSITION;
                fixed4 color : COLOR;
                float2 uv    : TEXCOORD0;
            };

            sampler2D _MainTex;
            float _Amount, _Aspect, _UTime;
            float _Border, _ContentZoom;
            float4 _PixelSize;
            float _Curvature;
            float4 _Tilt;
            float _CornerRadius, _BezelWidth;
            float4 _BezelColor, _RoomColor;
            float _ScanlineCount, _ScanlineStrength, _MaskCount, _MaskStrength;
            float _Blur, _Aberration, _RollSpeed, _RollStrength, _Jitter;
            float _Flicker, _FlickerFps, _Noise, _Vignette, _Reflection;

            v2f vert (appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = v.uv;
                o.color = v.color;
                return o;
            }

            float hash11(float p)
            {
                p = frac(p * 0.1031);
                p *= p + 33.33;
                p *= p + p;
                return frac(p);
            }

            float hash21(float2 p)
            {
                float3 p3 = frac(float3(p.x, p.y, p.x) * 0.1031);
                p3 += dot(p3, p3.yzx + 33.33);
                return frac((p3.x + p3.y) * p3.z);
            }

            // Signed distance to a rounded box. Negative = inside.
            float sdRoundBox(float2 p, float2 halfSize, float r)
            {
                float2 d = abs(p) - halfSize;
                return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - r;
            }

            float3 blurTap(float2 uv, float2 dx)
            {
                return tex2D(_MainTex, uv).rgb * 0.5
                     + (tex2D(_MainTex, uv - dx).rgb + tex2D(_MainTex, uv + dx).rgb) * 0.25;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                float amt = _Amount;
                float t = _UTime;
                float stepT = floor(t * _FlickerFps);   // stepped time = choppy CRT-ish noise

                // ---- layout (view height = 2, so y spans -1..1) ----
                float2 b = float2(_Aspect, 1.0);
                float2 G = b - _Border * amt;                    // glass half-size: small even border around it
                float2 p = (i.uv - 0.5) * 2.0 * b;
                p /= 1.0 + dot(p / G, _Tilt.xy) * amt;           // fake perspective / viewing angle

                // ---- glass outline (rounded rectangle) ----
                float rad = _CornerRadius * amt;
                float d = sdRoundBox(p, G - rad, rad);
                float aa = fwidth(d) + 1e-5;
                float glassMask = 1.0 - smoothstep(-aa, aa, d);

                // ---- game image fitted into the glass at its own aspect (never stretched) ----
                float s = max(G.x / b.x, G.y);
                float2 q = p / (b * s);                          // x spans -1..1 across the glass

                // ---- barrel distortion, pre-shrunk so the bulged image always fills the glass ----
                float k = _Curvature * amt;
                float r = G.y / s;
                float fit = (1.0 + k * r * r) * lerp(1.0, _ContentZoom, amt);
                float2 g = q * (1.0 + k * (q.yx * q.yx)) / fit;
                float2 uv = g * 0.5 + 0.5;

                // ---- rolling bar + per-line jitter ----
                float dband = frac(uv.y + t * _RollSpeed);
                float band = smoothstep(0.0, 0.12, dband) * (1.0 - smoothstep(0.12, 0.30, dband));
                uv.x += band * 0.004 * _RollStrength * sin(uv.y * 40.0 + t * 20.0) * amt;
                float lineId = floor(uv.y * _ScanlineCount);
                uv.x += (hash21(float2(lineId, stepT)) - 0.5) * 0.0015 * _Jitter * amt;

                // ---- sample with phosphor blur + color fringing ----
                float2 dx = float2(_PixelSize.x * _Blur * amt, 0.0);
                float2 ca = g * 0.004 * _Aberration * amt;
                float3 col;
                col.r = blurTap(uv + ca, dx).r;
                col.g = blurTap(uv,      dx).g;
                col.b = blurTap(uv - ca, dx).b;

                // ---- scanlines ----
                float sl = 0.5 + 0.5 * sin(uv.y * _ScanlineCount * 6.2831853);
                col *= 1.0 - _ScanlineStrength * amt * (1.0 - sl);
                col *= 1.0 + _ScanlineStrength * amt * 0.5;      // brightness compensation

                // ---- RGB aperture mask ----
                float tri = frac(uv.x * _MaskCount) * 3.0;
                float3 mk = float3(tri < 1.0, (tri >= 1.0 && tri < 2.0), tri >= 2.0);
                col *= lerp(float3(1, 1, 1), 0.55 + 0.9 * mk, _MaskStrength * amt);

                // ---- flicker, static, vignette ----
                col *= 1.0 + (hash11(stepT) - 0.5) * _Flicker * amt;
                col *= 1.0 + band * _RollStrength * amt * 0.5;
                col += (hash21(i.uv * 900.0 + stepT) - 0.5) * _Noise * amt;
                col *= 1.0 - _Vignette * amt * smoothstep(0.5, 1.5, length(q));

                // dark inner edge of the glass (recessed into the bezel)
                float edgeDark = smoothstep(-0.12, 0.0, d);
                col *= 1.0 - 0.55 * edgeDark * amt;

                // soft window reflection on the glass
                float2 hp = (q - float2(-0.45, 0.55)) * float2(0.9, 1.6);
                col += exp(-dot(hp, hp) * 3.0) * 0.10 * _Reflection * amt;

                // ---- bezel (plastic frame) ----
                float bw = _BezelWidth;
                float bd = saturate(d / bw);                         // 0 at glass edge -> 1 at outer rim
                float light = 0.85 + 0.2 * (q.y - q.x) * 0.5;        // light from top-left
                float groove = smoothstep(0.0, 0.35, bd);            // shadowed lip next to glass
                float rim = smoothstep(0.80, 0.98, bd);              // catch-light on outer edge
                float3 bezel = _BezelColor.rgb * light * lerp(0.25, 1.0, groove) + rim * 0.05 * light;
                bezel += (hash21(i.uv * 700.0) - 0.5) * 0.01;        // plastic grain

                // screen light spilling onto the bezel
                float3 spill = tex2D(_MainTex, clamp(g, -1.0, 1.0) * 0.5 + 0.5).rgb;
                bezel += spill * 0.18 * exp(-bd * 6.0) * amt;

                float bezelMask = 1.0 - smoothstep(bw - aa, bw + aa, d);

                // ---- room behind the TV ----
                float3 room = _RoomColor.rgb * (1.0 - 0.6 * length(i.uv - 0.5));
                room += spill * 0.05 * exp(-max(d - bw, 0.0) * 3.0) * amt;

                // ---- compose ----
                float3 outc = lerp(room, bezel, bezelMask);
                outc = lerp(outc, col, glassMask);

                return fixed4(saturate(outc) * i.color.rgb, 1.0);
            }
            ENDCG
        }
    }
}
