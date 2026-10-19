/* ===========================================================================
   Zombie Defence — living green background (Three.js)
   A toxic-lawn survival scene behind the leaderboard:
     • dark green→black gradient sky dome that breathes with a sickly glow
     • thousands of drifting green spores (GPU points)
     • slow biohazard haze sprites
     • the four defenders (archer / wizard / soldier / clown) as billboard
       sprites that SWING like pendulums and bob while patrolling, with a
       faint horde of smaller copies drifting in the background depth
     • cursor parallax
   Degrades gracefully: no WebGL/Three.js -> CSS gradient remains; a missing
   image simply means that character is skipped.
   =========================================================================== */
(function () {
  "use strict";

  var canvas = document.getElementById("bg-canvas");
  if (!canvas || typeof THREE === "undefined") return;

  var renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
  } catch (e) { return; }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);

  var scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x02140a, 0.00065);

  var camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 1, 4000);
  camera.position.set(0, 40, 540);

  // --- Toxic sky dome ---------------------------------------------------------
  var skyUniforms = {
    uTime:   { value: 0 },
    uTop:    { value: new THREE.Color(0x02070a) }, // near-black
    uMid:    { value: new THREE.Color(0x0c2a16) }, // deep swamp green
    uBottom: { value: new THREE.Color(0x3fae3a) }, // toxic green glow at horizon
  };
  var skyMat = new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, uniforms: skyUniforms,
    vertexShader:
      "varying vec3 vPos; void main(){ vPos=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}",
    fragmentShader: [
      "varying vec3 vPos; uniform float uTime; uniform vec3 uTop,uMid,uBottom;",
      "void main(){",
      "  float h=normalize(vPos).y; float t=clamp(h*0.5+0.5,0.0,1.0);",
      "  float glow=0.82+0.18*sin(uTime*1.6)*sin(uTime*0.7+1.1);",
      "  vec3 lower=mix(uBottom*glow,uMid,smoothstep(0.0,0.44,t));",
      "  vec3 col=mix(lower,uTop,smoothstep(0.44,0.92,t));",
      "  gl_FragColor=vec4(col,1.0);",
      "}"
    ].join("\n"),
  });
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(2000, 32, 24), skyMat));

  // --- Shared glow texture ----------------------------------------------------
  function glowTexture(inner, outer) {
    var s = 128, cv = document.createElement("canvas"); cv.width = cv.height = s;
    var ctx = cv.getContext("2d");
    var g = ctx.createRadialGradient(s/2, s/2, 0, s/2, s/2, s/2);
    g.addColorStop(0, inner); g.addColorStop(0.4, outer); g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, s, s);
    var tex = new THREE.CanvasTexture(cv); tex.minFilter = THREE.LinearFilter; return tex;
  }
  var sporeTex = glowTexture("rgba(190,255,150,1)", "rgba(90,200,60,0.6)");
  var hazeTex  = glowTexture("rgba(30,70,30,0.5)", "rgba(10,30,15,0.22)");

  // --- Drifting spores (points) ----------------------------------------------
  var N = 2000, geo = new THREE.BufferGeometry();
  var pos = new Float32Array(N * 3), vel = new Float32Array(N), drift = new Float32Array(N);
  var SX = 1700, SZ = 900, BOT = -280, TOP = 540;
  function seed(i, anyY) {
    pos[i*3]   = (Math.random()-0.5)*SX;
    pos[i*3+1] = anyY ? BOT + Math.random()*(TOP-BOT) : BOT - Math.random()*120;
    pos[i*3+2] = (Math.random()-0.5)*SZ - 120;
    vel[i] = 10 + Math.random()*34; drift[i] = Math.random()*6.28;
  }
  for (var i = 0; i < N; i++) seed(i, true);
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  var sporeMat = new THREE.PointsMaterial({
    size: 6, map: sporeTex, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, color: 0x8fe85a, opacity: 0.9, sizeAttenuation: true,
  });
  var spores = new THREE.Points(geo, sporeMat); scene.add(spores);

  // --- Biohazard haze ---------------------------------------------------------
  var hazeData = [];
  for (var h = 0; h < 12; h++) {
    var m = new THREE.SpriteMaterial({ map: hazeTex, transparent: true, depthWrite: false,
      opacity: 0.10 + Math.random()*0.12, color: 0x183d1f });
    var sp = new THREE.Sprite(m); var sc = 420 + Math.random()*520;
    sp.scale.set(sc, sc, 1);
    sp.position.set((Math.random()-0.5)*1700, -220 + Math.random()*720, -320 - Math.random()*380);
    scene.add(sp);
    hazeData.push({ sp: sp, vy: 3 + Math.random()*7, vx: (Math.random()-0.5)*3, spin: (Math.random()-0.5)*0.001 });
  }

  // --- Lights -----------------------------------------------------------------
  var keyLight = new THREE.PointLight(0x6cff5a, 1.8, 2600); keyLight.position.set(0, 120, 300);
  scene.add(keyLight); scene.add(new THREE.AmbientLight(0x1d3a22, 0.8));

  // --- Characters: swinging billboard sprites, scattered & blended ----------
  var FILES = ["assets/archer.jpeg", "assets/wizard.jpeg", "assets/soldier.jpeg", "assets/clown.jpeg"];
  var sprites = [];           // every placed instance (hero + echoes), all animated

  // Feathered, colour-matched cut-out: soft radial alpha (no hard ring) plus a
  // green multiply tint so the JPEG sits in the same palette as the toxic fog.
  function blendedTexture(img) {
    var S = 256, c = document.createElement("canvas"); c.width = c.height = S;
    var x = c.getContext("2d");
    var iw = img.width, ih = img.height, side = Math.min(iw, ih);
    x.drawImage(img, (iw - side) / 2, (ih - side) / 2, side, side, 0, 0, S, S);

    // tint toward the scene's toxic green so colours match the environment
    x.globalCompositeOperation = "multiply";
    x.fillStyle = "rgba(150, 230, 140, 0.78)";
    x.fillRect(0, 0, S, S);
    x.globalCompositeOperation = "source-over";
    x.fillStyle = "rgba(10, 30, 14, 0.18)";
    x.fillRect(0, 0, S, S);

    // feathered circular falloff as the alpha mask (soft edge, not a stroked ring)
    x.globalCompositeOperation = "destination-in";
    var g = x.createRadialGradient(S/2, S/2, S*0.28, S/2, S/2, S*0.5);
    g.addColorStop(0, "rgba(0,0,0,1)");
    g.addColorStop(0.75, "rgba(0,0,0,0.9)");
    g.addColorStop(1, "rgba(0,0,0,0)");
    x.fillStyle = g;
    x.fillRect(0, 0, S, S);
    x.globalCompositeOperation = "source-over";

    var t = new THREE.CanvasTexture(c);
    t.minFilter = THREE.LinearFilter;
    return t;
  }

  // Pre-scattered slots: varied depth tiers so instances read as part of the
  // world (near/mid/far) instead of a lined-up row. Positions are jittered,
  // not evenly spaced, and never share the same x/z pocket.
  var SLOTS = [
    { x: -620, y: -70,  z: 180,  scale: 190, opacity: 0.95, tier: "near" },
    { x:  340, y: -30,  z: -40,  scale: 150, opacity: 0.85, tier: "near" },
    { x: -140, y: -110, z: 260,  scale: 170, opacity: 0.9,  tier: "near" },
    { x:  610, y: -90,  z: 90,   scale: 160, opacity: 0.88, tier: "near" },
    { x: -820, y:  40,  z: -260, scale: 95,  opacity: 0.55, tier: "mid" },
    { x:  120, y:  70,  z: -340, scale: 85,  opacity: 0.48, tier: "mid" },
    { x:  780, y:  10,  z: -300, scale: 90,  opacity: 0.5,  tier: "mid" },
    { x: -380, y:  120, z: -520, scale: 60,  opacity: 0.3,  tier: "far" },
    { x:  420, y:  150, z: -560, scale: 55,  opacity: 0.28, tier: "far" },
    { x:   10, y:  180, z: -620, scale: 50,  opacity: 0.24, tier: "far" },
    { x: -980, y: -140, z: -120, scale: 110, opacity: 0.42, tier: "mid" },
    { x:  960, y: -150, z: -160, scale: 105, opacity: 0.4,  tier: "mid" },
  ];

  // shuffle so each image lands on a different mix of slots on every load
  for (var sIdx = SLOTS.length - 1; sIdx > 0; sIdx--) {
    var jIdx = Math.floor(Math.random() * (sIdx + 1));
    var tmpSlot = SLOTS[sIdx]; SLOTS[sIdx] = SLOTS[jIdx]; SLOTS[jIdx] = tmpSlot;
  }

  FILES.forEach(function (url, idx) {
    var image = new Image();
    image.onload = function () {
      var tex = blendedTexture(image);
      // each character gets 3 scattered instances (one per depth tier spread)
      for (var inst = 0; inst < 3; inst++) {
        var slot = SLOTS[(idx * 3 + inst) % SLOTS.length];
        var mat = new THREE.SpriteMaterial({
          map: tex, transparent: true, depthWrite: false,
          opacity: slot.opacity, fog: true,          // let scene fog fade far instances naturally
          blending: THREE.NormalBlending,
        });
        var sp = new THREE.Sprite(mat);
        var jitterScale = slot.scale * (0.9 + Math.random() * 0.2);
        sp.scale.set(jitterScale, jitterScale, 1);
        sp.position.set(
          slot.x + (Math.random() - 0.5) * 40,
          slot.y + (Math.random() - 0.5) * 20,
          slot.z + (Math.random() - 0.5) * 40
        );
        sp.center.set(0.5, 0.0); // pivot at the base so rotation reads as a natural sway
        scene.add(sp);

        var tierSpeed = slot.tier === "near" ? 1 : slot.tier === "mid" ? 0.6 : 0.4;
        sprites.push({
          sp: sp, baseX: sp.position.x, baseY: sp.position.y,
          phase: Math.random() * 6.28,
          swing: (slot.tier === "near" ? 0.1 : 0.16) + Math.random() * 0.08,
          bob: (slot.tier === "near" ? 6 : 10) + Math.random() * 8,
          speed: (0.55 + Math.random() * 0.5) * tierSpeed,
          patrol: (slot.tier === "near" ? 50 : 90) + Math.random() * 60,
        });
      }
    };
    image.src = url; // if it 404s, onload never fires -> character simply absent
  });

  // --- Parallax ---------------------------------------------------------------
  var tX = 0, tY = 40;
  window.addEventListener("pointermove", function (ev) {
    tX = ((ev.clientX / window.innerWidth) * 2 - 1) * 70;
    tY = 40 - ((ev.clientY / window.innerHeight) * 2 - 1) * 30;
  });

  // --- Resize -----------------------------------------------------------------
  window.addEventListener("resize", function () {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  // --- Loop -------------------------------------------------------------------
  var clock = new THREE.Clock(), paused = false;
  document.addEventListener("visibilitychange", function () { paused = document.hidden; });

  function tick() {
    requestAnimationFrame(tick);
    if (paused) return;
    var dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
    skyUniforms.uTime.value = t;

    // spores
    var p = geo.attributes.position.array;
    for (var k = 0; k < N; k++) {
      p[k*3+1] += vel[k] * dt;
      p[k*3]   += Math.sin(t * 1.1 + drift[k]) * 8 * dt;
      if (p[k*3+1] > TOP) seed(k, false);
    }
    geo.attributes.position.needsUpdate = true;
    sporeMat.opacity = 0.72 + 0.22 * Math.abs(Math.sin(t * 1.3));

    // haze
    for (var m = 0; m < hazeData.length; m++) {
      var d = hazeData[m];
      d.sp.position.y += d.vy * dt; d.sp.position.x += d.vx * dt; d.sp.material.rotation += d.spin;
      if (d.sp.position.y > 760) { d.sp.position.y = -280; d.sp.position.x = (Math.random()-0.5)*1700; }
    }

    // hero characters: pendulum swing + bob + slow patrol
    for (var hI = 0; hI < sprites.length; hI++) {
      var o = sprites[hI];
      o.sp.material.rotation = Math.sin(t * o.speed + o.phase) * o.swing;             // swing
      o.sp.position.y = o.baseY + Math.sin(t * o.speed * 1.6 + o.phase) * o.bob;       // bob
      o.sp.position.x = o.baseX + Math.sin(t * 0.25 + o.phase) * o.patrol;             // patrol
    }
    keyLight.intensity = 1.5 + Math.sin(t * 2.0) * 0.5;

    camera.position.x += (tX - camera.position.x) * 0.03;
    camera.position.y += (tY - camera.position.y) * 0.03;
    camera.lookAt(0, 60, 0);
    renderer.render(scene, camera);
  }
  tick();
})();
