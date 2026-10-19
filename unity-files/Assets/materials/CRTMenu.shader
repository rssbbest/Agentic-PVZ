Shader "UI/CRTMenu"
{
    // Material for the full-screen RawImage. The controller script drives the "Driven by script" values.
    // With a TV frame: the glass window morphs from the full screen to the frame's GlassRect and the
    // frame sprites (drawn on top) cover everything around it.
    Properties
    {
        [PerRendererData] _MainTex ("Game Render", 2D) = "white" {}

        [Header(Driven by script)]
        _Amount ("Effect Amount", Range(0,1)) = 1
        _Aspect ("Screen Aspect", Float) = 1.7778
        _ContentAspect ("Game Render Aspect", Float) = 1.7778
        _GlassCenter ("Glass Center (view units)", Vector) = (0,0,0,0)
        _GlassHalf ("Glass Half Size (view units)", Vector) = (1.33,0.9,0,0)
        _UseGlassRect ("Use Glass Rect from script (0/1)", Float) = 0
        _UTime ("Unscaled Time", Float) = 0
        _PixelSize ("Texel Size (1/w, 1/h)", Vector) = (0.002, 0.002, 0, 0)

        [Header(Screen area (used when no Glass Rect is assigned in the controller))]
        _ScreenSize ("Screen Size (1 = full width / height)", Vector) = (0.583, 0.778, 0, 0)
        _ScreenOffset ("Screen Offset (-1..1, 0 = centered)", Vector) = (-0.229, 0, 0, 0)

        [Header(Glass shape)]
        _Curvature ("Bulge", Range(0,0.4)) = 0.12
        _CornerRadius ("Glass Corner Radius (fraction of glass height)", Range(0,0.6)) = 0.285
        _ContentZoom ("Extra Screen Zoom (fills edges)", Range(1,1.2)) = 1.015
        _Tilt ("Viewing Angle Tilt (x,y)", Vector) = (0.01, -0.008, 0, 0)

        [Header(Bezel and room outside the glass)]
        _BezelWidth ("Bezel Width (0.16 = about 86px on 1080p)", Range(0.02,0.6)) = 0.16
        _BezelColor ("Bezel Color (black = 0,0,0)", Color) = (0, 0, 0, 1)
        _BezelEdgeColor ("Bezel Outline Color (set black to hide)", Color) = (0.22, 0.2, 0.19, 1)
        _BezelGlow ("Screen Light Spilling On Bezel", Range(0,1)) = 0.0
        _RoomAlpha ("Room Opacity (0 = transparent, frame shows)", Range(0,1)) = 0
        _RoomColor ("Room Color (solid mode / when Room Opacity > 0)", Color) = (0.004, 0.004, 0.006, 1)

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
            float _Amount, _Aspect, _ContentAspect, _UseGlassRect, _UTime;
            float4 _GlassCenter, _GlassHalf, _ScreenSize, _ScreenOffset, _PixelSize;
            float _Curvature, _CornerRadius, _ContentZoom;
            float4 _Tilt;
            float _BezelWidth, _BezelGlow, _RoomAlpha;
            float4 _BezelColor, _BezelEdgeColor, _RoomColor;
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
                float stepT = floor(t * _FlickerFps);

                // ---- view space: y spans -1..1 across the screen height ----
                float2 view = float2(_Aspect, 1.0);
                float2 p = (i.uv - 0.5) * 2.0 * view;

                // ---- glass morphs from the full screen to the TV window ----
                float useRect = step(0.5, _UseGlassRect);
                float2 targetHalf   = lerp(view * _ScreenSize.xy,   _GlassHalf.xy,   useRect);
                float2 targetCenter = lerp(view * _ScreenOffset.xy, _GlassCenter.xy, useRect);
                float2 G = lerp(view, targetHalf, amt);
                p -= lerp(float2(0.0, 0.0), targetCenter, amt);
                p /= 1.0 + dot(p / G, _Tilt.xy) * amt;           // fake viewing angle

                float rad = _CornerRadius * G.y * amt;
                float d = sdRoundBox(p, G - rad, rad);
                float aa = fwidth(d) + 1e-5;
                float glassMask = 1.0 - smoothstep(-aa, aa, d);

                // ---- game image "cover" fitted into the glass (sides/top cropped, never stretched) ----
                float2 c = float2(_ContentAspect, 1.0);
                float s = max(G.x / c.x, G.y);
                float2 q = p / (c * s);
                float qxm = G.x / (c.x * s);                     // extent of q at the glass edges
                float qym = G.y / s;

                // ---- barrel distortion, pre-shrunk so the bulged image always fills the glass ----
                float k = _Curvature * amt;
                float fit = max(qxm * (1.0 + k * qym * qym), qym * (1.0 + k * qxm * qxm)) * lerp(1.0, _ContentZoom, amt);
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
                col *= 1.0 + _ScanlineStrength * amt * 0.5;

                // ---- RGB aperture mask ----
                float tri = frac(uv.x * _MaskCount) * 3.0;
                float3 mk = float3(tri < 1.0, (tri >= 1.0 && tri < 2.0), tri >= 2.0);
                col *= lerp(float3(1, 1, 1), 0.55 + 0.9 * mk, _MaskStrength * amt);

                // ---- flicker, static, vignette ----
                col *= 1.0 + (hash11(stepT) - 0.5) * _Flicker * amt;
                col *= 1.0 + band * _RollStrength * amt * 0.5;
                col += (hash21(i.uv * 900.0 + stepT) - 0.5) * _Noise * amt;
                col *= 1.0 - _Vignette * amt * smoothstep(0.5, 1.5, length(q));

                // dark inner edge of the glass
                col *= 1.0 - 0.55 * smoothstep(-0.08, 0.0, d) * amt;

                // soft window reflection on the glass
                float2 hp = (q - float2(-0.45, 0.55)) * float2(0.9, 1.6);
                col += exp(-dot(hp, hp) * 3.0) * 0.10 * _Reflection * amt;

                // ---- procedural bezel + room ----
                float bw = _BezelWidth;
                float bd = saturate(d / bw);
                float light = 0.85 + 0.2 * (q.y - q.x) * 0.5;
                float groove = smoothstep(0.0, 0.35, bd);
                float rim = smoothstep(0.80, 0.98, bd);
                float3 bezel = _BezelColor.rgb * light * (lerp(0.25, 1.0, groove) + rim * 0.4);
                bezel *= 1.0 + (hash21(i.uv * 700.0) - 0.5) * 0.2;

                float3 spill = tex2D(_MainTex, clamp(g, -1.0, 1.0) * 0.5 + 0.5).rgb;
                bezel += spill * _BezelGlow * exp(-bd * 6.0) * amt;

                float bezelMask = 1.0 - smoothstep(bw - aa, bw + aa, d);

                // thin outline on the outer edge of the bezel
                float edgeBand = smoothstep(bw - 0.016, bw - 0.012, d) * bezelMask;
                bezel = lerp(bezel, _BezelEdgeColor.rgb, edgeBand);

                float3 room = _RoomColor.rgb * (1.0 - 0.6 * length(i.uv - 0.5));
                room += spill * 0.05 * exp(-max(d - bw, 0.0) * 3.0) * amt;

                float3 outc = lerp(room, bezel, bezelMask);
                outc = lerp(outc, col, glassMask);
                float alpha = lerp(_RoomAlpha, 1.0, max(bezelMask, glassMask));   // room outside the bezel can be see-through
                return fixed4(saturate(outc) * i.color.rgb, alpha * i.color.a);
            }
            ENDCG
        }
    }
}
