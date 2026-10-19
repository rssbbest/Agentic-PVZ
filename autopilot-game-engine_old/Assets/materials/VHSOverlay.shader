Shader "UI/VHSOverlay"
{
    Properties
    {
        _Intensity ("Intensity", Range(0,1)) = 1
        _Bands ("Band Count", Float) = 320
        _Dir ("Direction (1 = FF, -1 = Rewind)", Float) = 1
        _T ("Time", Float) = 0
    }
    SubShader
    {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" "IgnoreProjector"="True" }
        Blend SrcAlpha OneMinusSrcAlpha
        ZWrite Off Cull Off ZTest Always

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            float _Intensity, _Bands, _Dir, _T;

            struct v2f { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };

            v2f vert (appdata_base v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = v.texcoord;
                return o;
            }

            float hash(float2 p)
            {
                p = frac(p * float2(123.34, 456.21));
                p += dot(p, p + 45.32);
                return frac(p.x * p.y);
            }

            fixed4 frag (v2f i) : SV_Target
            {
                float2 uv = i.uv;
                float tick = floor(_T * 30);

                // --- thin horizontal noise lines (short dashes) ---
                float row = floor(uv.y * _Bands);
                float n = hash(float2(row, tick));
                float start = hash(float2(row + 7.3, tick));
                float len = hash(float2(row + 3.1, tick)) * 0.3 + 0.02;
                float streak = step(0.80, n) * step(start, uv.x) * step(uv.x, start + len);

                // --- thin scrolling tracking bands (fine noise, not solid) ---
                float bandY1 = frac(_T * 0.6 * _Dir);
                float bandY2 = frac(_T * 0.6 * _Dir + 0.5);
                float band = smoothstep(0.025, 0.0, abs(uv.y - bandY1))
                           + smoothstep(0.015, 0.0, abs(uv.y - bandY2));
                float bandNoise = hash(float2(floor(uv.x * 500), row + tick));
                float trackingBand = band * step(0.5, bandNoise) * 0.15;

                // --- very fine grain + scanlines ---
                float grain = hash(uv * 800 + tick) * 0.05;
                float scan = (sin(uv.y * _ScreenParams.y * 3.14159) * 0.5 + 0.5) * 0.04;

                float a = saturate(streak * 0.5 + trackingBand + grain + scan) * _Intensity;

                float3 col = lerp(float3(1,1,1), float3(0.7,1,1), step(0.5, hash(float2(row, tick + 9))));
                return fixed4(col, a);
            }
            ENDCG
        }
    }
}