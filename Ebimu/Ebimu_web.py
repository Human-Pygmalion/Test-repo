#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EBIMU-9DOF 웹 3D 뷰어  -  Ubuntu / Raspberry Pi

센서 자세를 브라우저에서 3D 상자로 본다. 터미널 막대그래프로는 어느 쪽으로
기울었는지 감이 안 오기 때문이다.

사용법
    python3 ebimu_web.py -p /dev/ttyUSB0
    python3 ebimu_web.py -p /dev/ttyUSB0 --web-port 8080
    python3 ebimu_web.py -p /dev/ttyUSB0 --layout quat,gyro,accel

    실행하면 http://localhost:8000 을 브라우저로 연다.
    같은 네트워크의 다른 기기에서 보려면 --host 0.0.0.0 을 준다
    (라즈베리파이에 센서를 꽂고 노트북에서 보는 경우).

    종료: Ctrl-C

왜 로컬 서버인가
    브라우저는 시리얼 포트를 직접 못 읽는다. 이 스크립트가 포트를 읽어
    SSE(Server-Sent Events)로 브라우저에 흘려보낸다.

항목 판정과 중력 계산은 Ebimu_live.py 것을 그대로 쓴다. --layout, 저장된
ebimu_layout.txt, --list-blocks 모두 같게 동작한다.
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import Ebimu_live as live       # noqa: E402  항목/중력 계산을 그대로 씀

try:
    import serial
except ImportError:
    sys.exit("pyserial 이 없습니다.  pip install pyserial")


# ────────────────────────────────────────────────────────────────
# 브라우저에 보낼 한 프레임
# ────────────────────────────────────────────────────────────────
def frame(sh, layout):
    vals = list(sh.values)
    labs, note, hint, names = live.resolve(len(vals), layout)

    quat = None
    span = live.block_slice(names, "quat")
    if span and span[1] <= len(vals):
        quat = list(live.to_quaternion(vals[span[0]:span[1]]))

    euler = None
    span = live.block_slice(names, "euler")
    if span and span[1] <= len(vals):
        euler = list(vals[span[0]:span[1]])

    grav, kind = live.gravity_of(vals, names)
    gap = live.accel_gap(vals, names, grav) if grav else None

    # 가속도계가 재는 방향. 자세 계산과 무관한 독립적인 값이라 대조 근거가 된다.
    accel = None
    span = live.block_slice(names, "accel")
    if span and span[1] <= len(vals):
        accel = [round(v, 4) for v in vals[span[0]:span[1]]]

    return {
        "values": [round(v, 4) for v in vals],
        "labels": [l[0] for l in labs],
        "units": [l[1] for l in labs],
        "quat": quat,
        "euler": euler,
        "gravity": [round(v, 4) for v in grav] if grav else None,
        "accel": accel,
        "gravityFrom": kind,
        "tilt": round(live.tilt_deg(grav), 2) if grav else None,
        "accelGap": round(gap, 4) if gap is not None else None,
        "note": note,
        "hint": hint,
        "hz": round(sh.hz, 1),
        "count": sh.count,
        "bad": sh.bad,
    }


# ────────────────────────────────────────────────────────────────
# 웹 페이지
#
# 라이브러리를 쓰지 않는다. CSS 3D 변환만으로 상자를 돌리므로 인터넷이
# 없어도 되고, 라즈베리파이에서도 가볍다.
# ────────────────────────────────────────────────────────────────
PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EBIMU 3D</title>
<style>
  /* 보드 치수는 매뉴얼 9장 그대로: 16.3(W) x 18.6(H) x 3.05(D) mm, x9 배 */
  :root {
    --w:147px; --d:167px; --t:27px;      /* 보드 가로 / 세로 / 두께 */
    --bg:#0f1115; --panel:#171a21; --line:#262b36;
    --fg:#e6e9ef; --dim:#8b93a7; --warn:#ffb454; --bad:#ff6b6b; --ok:#7bd88f;
    --ax:#ff5a5a; --ay:#54d17a; --az:#5aa9ff;   /* 매뉴얼 4-3~4-5 축 색 */
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:10px 16px; border-bottom:1px solid var(--line);
           display:flex; gap:16px; align-items:baseline; flex-wrap:wrap; }
  h1 { font-size:15px; margin:0; font-weight:600; }
  .meta { color:var(--dim); font-size:12px; }
  .wrap { display:grid; grid-template-columns:1fr 320px;
          height:calc(100vh - 46px); }
  @media (max-width:800px){ .wrap{ grid-template-columns:1fr; height:auto; } }

  .stage { position:relative; display:grid; place-items:center;
           perspective:1000px; overflow:hidden; }
  .scene { transform-style:preserve-3d;
           transform:rotateX(-20deg) rotateY(-32deg); }
  .stage { cursor:grab; }
  .stage.drag { cursor:grabbing; }
  .view { position:absolute; right:14px; bottom:12px; color:var(--dim);
          font-size:11px; text-align:right; line-height:1.7; }
  .view button { font:inherit; color:var(--fg); background:#222836;
                 border:1px solid var(--line); border-radius:3px;
                 padding:2px 8px; cursor:pointer; margin-left:4px; }
  .view button:hover { background:#2b3243; }

  /* 바닥 격자와 중력(월드 기준이라 항상 아래) */
  .floor { position:absolute; width:420px; height:420px; margin:-210px 0 0 -210px;
           left:50%; top:50%; transform:rotateX(90deg) translateZ(-170px);
           background:
             repeating-linear-gradient(0deg,#39415a 0 1px,transparent 1px 35px),
             repeating-linear-gradient(90deg,#39415a 0 1px,transparent 1px 35px),
             repeating-linear-gradient(0deg,#4a5470 0 2px,transparent 2px 175px),
             repeating-linear-gradient(90deg,#4a5470 0 2px,transparent 2px 175px);
           box-shadow:inset 0 0 90px rgba(0,0,0,.75); }
  /* 보드가 격자 위에 떠 있다는 것을 보이게 하는 그림자 */
  .shadow { position:absolute; left:50%; top:50%; width:170px; height:190px;
            margin:-95px 0 0 -85px; border-radius:50%;
            transform:rotateX(90deg) translateZ(-169px);
            background:radial-gradient(closest-side,rgba(0,0,0,.55),transparent); }
  /* 데이터로 그리는 벡터 (중력 / 가속도계). 축 화살표와 같은 교차 평면 구조. */
  .vec { position:absolute; left:0; top:0; width:0; height:0;
         transform-origin:0 0; transform-style:preserve-3d; }
  .vec .rod { position:absolute; left:-3px; top:0; width:6px; height:104px;
              background:currentColor; }
  .vec .rod.b { transform:rotateY(90deg); }
  .vec .tip { position:absolute; left:-9px; top:102px; width:0; height:0;
              border-left:9px solid transparent; border-right:9px solid transparent;
              border-top:22px solid currentColor; }
  .vec .tip.b { transform:rotateY(90deg); }
  .vec span { position:absolute; top:124px; left:10px; white-space:nowrap;
              font-size:12px; font-weight:700; color:currentColor; }
  .gv { color:#c6ccdb; }        /* 자세에서 계산한 중력 */
  .av { color:#ff9f43; }        /* 가속도계가 잰 방향 */

  /* 센서 보드 */
  .body { position:absolute; left:50%; top:50%; width:0; height:0;
          transform-style:preserve-3d; transition:transform .05s linear; }
  .face { position:absolute; transform-style:preserve-3d; }
  .top, .bot { width:var(--w); height:var(--d);
               margin:calc(var(--d) / -2) 0 0 calc(var(--w) / -2); }
  .fb       { width:var(--w); height:var(--t);
              margin:calc(var(--t) / -2) 0 0 calc(var(--w) / -2); }
  .lr       { width:var(--d); height:var(--t);
              margin:calc(var(--t) / -2) 0 0 calc(var(--d) / -2); }
  .top { transform:translateY(calc(var(--t) / -2)) rotateX(90deg);
         background:#12301f; border:1px solid #0a1c12; }
  .bot { transform:translateY(calc(var(--t) / 2)) rotateX(90deg);
         background:#0d2418; border:1px solid #0a1c12; }
  .fb.f { transform:translateZ(calc(var(--d) / 2)); background:#0e2718; }
  .fb.b { transform:translateZ(calc(var(--d) / -2)) rotateY(180deg); background:#0b1f13; }
  .lr.l { transform:translateX(calc(var(--w) / -2)) rotateY(-90deg); background:#0b1f13; }
  .lr.r { transform:translateX(calc(var(--w) / 2)) rotateY(90deg); background:#0e2718; }

  /* 보드 위 부품과 castellated 패드 (매뉴얼 38쪽 배치) */
  .part { position:absolute; background:#1b1b1f; border:1px solid #2a2a30;
          border-radius:1px; }
  .chip { background:#17171b; border-color:#33333a; }
  .pad  { position:absolute; width:18px; height:10px; border-radius:1px;
          background:linear-gradient(#ffe9a8,#d9b64e);
          box-shadow:0 0 3px rgba(255,220,130,.55); }
  .mark { position:absolute; left:6px; bottom:5px; width:0; height:0;
          border-left:8px solid #7d8b7f; border-bottom:8px solid transparent; }

  /* 축 화살표 -- 보드에 붙어 같이 돈다.
     매뉴얼 4-1 처럼 보드 '윗면' 에서 나간다 (translateY 로 두께 절반만큼 올림).
     평면 두 장을 90도로 교차시켜 입체로 보이게 한다. 어느 각도에서 봐도
     납작해 보이지 않는다. */
  .axis { position:absolute; left:0; top:0; width:0; height:0;
          transform-origin:0 0; transform-style:preserve-3d; }
  .axis .rod { position:absolute; left:-5px; top:0; width:10px; height:128px;
               background:currentColor; }
  .axis .rod.b { transform:rotateY(90deg); }
  .axis .tip { position:absolute; left:-13px; top:126px; width:0; height:0;
               border-left:13px solid transparent; border-right:13px solid transparent;
               border-top:30px solid currentColor; }
  .axis .tip.b { transform:rotateY(90deg); }
  .axis span { position:absolute; top:150px; left:12px; white-space:nowrap;
               font-size:13px; font-weight:700; letter-spacing:.03em;
               color:currentColor; }
  .ax  { color:var(--ax);
         transform:translateY(calc(var(--t) / -2)) rotateZ(-90deg); }
  .ay  { color:var(--ay);
         transform:translateY(calc(var(--t) / -2)) rotateX(90deg); }
  .az  { color:var(--az);
         transform:translateY(calc(var(--t) / -2)) rotateZ(180deg); }

  /* 회전 방향 표시 -- 매뉴얼의 회색 곡선 화살표와 '+' 자리 */
  .spin { position:absolute; width:54px; height:54px; margin:-27px 0 0 -27px;
          border-radius:50%; }
  .spin { border-color:#79839a; border-right-color:transparent;
          border-bottom-color:transparent; opacity:.75; }
  .spin i { position:absolute; right:-11px; top:22px; font-style:normal;
            color:#79839a; font-size:12px; font-weight:700; }
  .sx { transform:translateX(46px) rotateY(90deg); }    /* X 축 둘레 */
  .sy { transform:translateZ(46px); }                   /* Y 축 둘레 */
  .sz { transform:translateY(-46px) rotateX(90deg); }   /* Z 축 둘레 */

  .panel { border-left:1px solid var(--line); background:var(--panel);
           overflow-y:auto; padding:12px 14px; }
  .panel h2 { font-size:12px; color:var(--dim); margin:16px 0 6px;
              font-weight:600; letter-spacing:.06em; text-transform:uppercase; }
  .panel h2:first-child { margin-top:0; }
  table { width:100%; border-collapse:collapse; }
  td { padding:2px 0; white-space:nowrap; }
  td.n { text-align:right; font-variant-numeric:tabular-nums; }
  td.u { color:var(--dim); padding-left:6px; width:44px; }
  .warn{color:var(--warn)} .bad{color:var(--bad)} .ok{color:var(--ok)}
  .off {color:var(--bad)}
  .note { color:var(--dim); font-size:12px; margin-top:4px; }
  .legend { position:absolute; left:14px; bottom:12px; color:var(--dim);
            font-size:11px; line-height:1.7; }
  .legend b { font-weight:700; }
</style></head><body>
<header>
  <h1>EBIMU 3D</h1>
  <span class="meta" id="conn">연결 중…</span>
  <span class="meta" id="stat"></span>
  <span class="meta" id="note"></span>
</header>
<div class="wrap">
  <div class="stage">
    <div class="scene">
      <div class="floor"></div>
      <div class="shadow"></div>
      <div class="body" id="box">
        <div class="face top" id="top"></div>
        <div class="face bot"></div>
        <div class="face fb f"></div><div class="face fb b"></div>
        <div class="face lr l"></div><div class="face lr r"></div>
        <div class="axis ax"><i class="rod"></i><i class="rod b"></i>
             <i class="tip"></i><i class="tip b"></i><span>X+ ROLL</span></div>
        <div class="axis ay"><i class="rod"></i><i class="rod b"></i>
             <i class="tip"></i><i class="tip b"></i><span>Y+ PITCH</span></div>
        <div class="axis az"><i class="rod"></i><i class="rod b"></i>
             <i class="tip"></i><i class="tip b"></i><span>Z+ YAW</span></div>
        <div class="vec gv" id="gv"><i class="rod"></i><i class="rod b"></i>
             <i class="tip"></i><i class="tip b"></i><span>g 계산</span></div>
        <div class="vec av" id="av"><i class="rod"></i><i class="rod b"></i>
             <i class="tip"></i><i class="tip b"></i><span>g 가속도계</span></div>
        <div class="spin sx"><i>+</i></div>
        <div class="spin sy"><i>+</i></div>
        <div class="spin sz"><i>+</i></div>
      </div>
    </div>
    <div class="view">
      끌어서 돌리기 · 휠로 확대<br>
      <button data-view="top">위</button>
      <button data-view="front">앞</button>
      <button data-view="side">옆</button>
      <button data-view="iso">기본</button>
    </div>
    <div class="legend">
      <b style="color:var(--ax)">X+ ROLL</b> (패드 열 방향) ·
      <b style="color:var(--ay)">Y+ PITCH</b> ·
      <b style="color:var(--az)">Z+ YAW</b> · 회색 원 = + 회전 방향<br>
      <b style="color:#c6ccdb">g 계산</b> = 자세에서 계산한 중력 ·
      <b style="color:#ff9f43">g 가속도계</b> = 가속도계가 잰 방향<br>
      정지 상태에서 둘이 겹치면 정상 · 보드 16.3 × 18.6 × 3.05 mm
    </div>
  </div>
  <div class="panel">
    <h2>자세</h2><table id="att"></table>
    <h2>중력방향</h2><table id="gravt"></table>
    <div class="note" id="gnote"></div>
    <h2>수신 값</h2><table id="vals"></table>
  </div>
</div>
<script>
// 보드 윗면 부품 -- 매뉴얼 38쪽 도면의 배치를 옮긴 것. 보기용이다.
(function board(){
  const top = document.getElementById("top");
  const parts = [                       // [left%, top%, w%, h%, 큰칩 여부]
    [12,10,34,13,0], [20,26,42,26,1], [58,34,26,14,0],
    [14,58,18,8,0],  [40,60,14,8,0],  [60,58,10,8,0],
    [22,74,30,7,0],  [58,72,16,7,0],
  ];
  for (const [l,t,w,h,big] of parts) {
    const d = document.createElement("div");
    d.className = "part" + (big ? " chip" : "");
    d.style.cssText = `left:${l}%;top:${t}%;width:${w}%;height:${h}%`;
    top.appendChild(d);
  }
  // castellated 패드: 위아래 가장자리에 2.54mm 간격
  for (const edge of [2, 88]) {
    for (let i = 0; i < 5; i++) {
      const d = document.createElement("div");
      d.className = "pad";
      d.style.cssText = `left:${11 + i * 19}%;top:${edge}%`;
      top.appendChild(d);
    }
  }
  const m = document.createElement("div"); m.className = "mark"; top.appendChild(m);
})();

// ── 시점 ─────────────────────────────────────────────────────
// 보는 각도만 바꾼다. 센서 자세(.body)와는 별개다 -- 시점을 돌려도 값은 안 변한다.
const VIEWS = { iso:[-20,-32], top:[-89,0], front:[0,0], side:[0,-90] };
const cam = { rx:-20, ry:-32, zoom:1 };
const scene = document.querySelector(".scene");
const stage = document.querySelector(".stage");

function applyCam(){
  scene.style.transform =
    `scale(${cam.zoom}) rotateX(${cam.rx}deg) rotateY(${cam.ry}deg)`;
}
let drag = null;
stage.addEventListener("pointerdown", e => {
  if (e.target.tagName === "BUTTON") return;
  drag = { x:e.clientX, y:e.clientY, rx:cam.rx, ry:cam.ry };
  stage.classList.add("drag");
  stage.setPointerCapture(e.pointerId);
});
stage.addEventListener("pointermove", e => {
  if (!drag) return;
  cam.ry = drag.ry + (e.clientX - drag.x) * 0.4;
  // 위아래는 +-89 도에서 멈춘다. 넘어가면 위아래가 뒤집혀 방향 감각이 사라진다.
  cam.rx = Math.max(-89, Math.min(89, drag.rx - (e.clientY - drag.y) * 0.4));
  applyCam();
});
for (const ev of ["pointerup","pointercancel","pointerleave"])
  stage.addEventListener(ev, () => { drag = null; stage.classList.remove("drag"); });
stage.addEventListener("wheel", e => {
  e.preventDefault();
  cam.zoom = Math.max(0.4, Math.min(3, cam.zoom * (e.deltaY > 0 ? 0.9 : 1.1)));
  applyCam();
}, { passive:false });
for (const b of document.querySelectorAll(".view button"))
  b.addEventListener("click", () => {
    [cam.rx, cam.ry] = VIEWS[b.dataset.view]; applyCam();
  });
applyCam();

// 쿼터니언 (w,x,y,z) -> 3x3 회전행렬 (몸체 -> 월드)
function quatToR(q) {
  const [w,x,y,z] = q;
  return [
    [1-2*(y*y+z*z), 2*(x*y-w*z),   2*(x*z+w*y)],
    [2*(x*y+w*z),   1-2*(x*x+z*z), 2*(y*z-w*x)],
    [2*(x*z-w*y),   2*(y*z+w*x),   1-2*(x*x+y*y)],
  ];
}
// 오일러(도, ZYX: yaw->pitch->roll) -> 쿼터니언 (w,x,y,z)
function eulerToQuat(r,p,y) {
  const d = Math.PI/180;
  const cr=Math.cos(r*d/2), sr=Math.sin(r*d/2);
  const cp=Math.cos(p*d/2), sp=Math.sin(p*d/2);
  const cy=Math.cos(y*d/2), sy=Math.sin(y*d/2);
  return [cr*cp*cy+sr*sp*sy, sr*cp*cy-cr*sp*sy,
          cr*sp*cy+sr*cp*sy, cr*cp*sy-sr*sp*cy];
}
// 센서 좌표(Z 가 위) -> 화면 좌표(CSS 는 Y 가 아래).
//   화면X = 센서X,  화면Y = -센서Z,  화면Z = 센서Y
const P = [[1,0,0],[0,0,-1],[0,1,0]];
function mul(A,B){ return A.map((r,i)=>B[0].map((_,j)=>
  r.reduce((s,_,k)=>s+A[i][k]*B[k][j],0))); }
function T(A){ return A[0].map((_,j)=>A.map(r=>r[j])); }
function css(R){                       // matrix3d 는 열 우선
  const M = mul(mul(P,R),T(P));
  return `matrix3d(${M[0][0]},${M[1][0]},${M[2][0]},0,`
       + `${M[0][1]},${M[1][1]},${M[2][1]},0,`
       + `${M[0][2]},${M[1][2]},${M[2][2]},0,0,0,0,1)`;
}
// 센서 좌표 벡터 v 를 향해 화살표를 눕힌다.
//   화살표는 기본으로 로컬 +Y(아래)를 향한다. 화면 좌표로 바꾼 뒤,
//   rotateY(a)rotateX(b) 로 (0,1,0) -> v 가 되게 하는 a, b 를 구한다.
function aim(el, v) {
  if (!v) { el.style.display = "none"; return; }
  const n = Math.hypot(v[0], v[1], v[2]);
  if (n < 1e-6) { el.style.display = "none"; return; }
  el.style.display = "";
  const sx = v[0]/n, sy = -v[2]/n, sz = v[1]/n;      // 센서 -> 화면
  const b = Math.acos(Math.max(-1, Math.min(1, sy))) * 180/Math.PI;
  const a = Math.atan2(sx, sz) * 180/Math.PI;
  el.style.transform = `rotateY(${a}deg) rotateX(${b}deg)`;
}

function rows(t, list){
  t.innerHTML = list.map(([n,v,u,c]) =>
    `<tr><td>${n}</td><td class="n ${c||''}">${v}</td>`
    + `<td class="u">${u||''}</td></tr>`).join("");
}
const $ = id => document.getElementById(id);

const es = new EventSource("/stream");
es.onopen  = () => { $("conn").textContent = "연결됨"; $("conn").className="meta ok"; };
es.onerror = () => { $("conn").textContent = "끊김 — 스크립트가 살아있는지 확인";
                     $("conn").className="meta off"; };
es.onmessage = (ev) => {
  const d = JSON.parse(ev.data);
  $("stat").textContent = `${d.hz} Hz · ${d.count} packets · drops ${d.bad}`;
  $("note").textContent = d.note + (d.hint ? "  ※ " + d.hint : "");
  $("note").className = d.hint ? "meta warn" : "meta";

  const q = d.quat ? d.quat
          : (d.euler ? eulerToQuat(d.euler[0], d.euler[1], d.euler[2]) : null);
  if (q) $("box").style.transform = css(quatToR(q));

  if (d.euler) rows($("att"), [["Roll", d.euler[0].toFixed(2), "deg"],
                               ["Pitch",d.euler[1].toFixed(2), "deg"],
                               ["Yaw",  d.euler[2].toFixed(2), "deg"]]);
  else if (d.quat) rows($("att"), d.quat.map((v,i)=>["wxyz"[i], v.toFixed(4), ""]));
  else rows($("att"), [["자세 없음","",""]]);

  // 중력 화살표는 받은 계산값 그대로 그린다. 고정값이 아니다.
  aim($("gv"), d.gravity);
  // 가속도계가 재는 것은 중력의 반대 방향이라 부호를 뒤집는다
  aim($("av"), d.accel ? d.accel.map(v => -v) : null);

  if (d.gravity) {
    rows($("gravt"), [
      ["Grav X", d.gravity[0].toFixed(3), ""],
      ["Grav Y", d.gravity[1].toFixed(3), ""],
      ["Grav Z", d.gravity[2].toFixed(3), ""],
      ["기울기", d.tilt.toFixed(1), "deg"],
      ...(d.accelGap === null ? [] :
        [["가속도차", d.accelGap.toFixed(3), "", d.accelGap > 0.05 ? "bad":"ok"]]),
    ]);
    $("gnote").textContent =
      (d.gravityFrom === "euler"
        ? "오일러(ZYX 전제)에서 계산 — 가속도차로 대조하세요"
        : "쿼터니언에서 계산")
      + (d.accelGap !== null && d.accelGap > 0.05
        ? " · 정지 상태인데 가속도차가 크면 부착/순서 확인" : "");
  } else {
    rows($("gravt"), [["자세가 없어 계산 못 함","",""]]);
    $("gnote").textContent = "";
  }

  rows($("vals"), d.values.map((v,i) =>
    [d.labels[i] || `Val ${i}`, v.toFixed(3), d.units[i] || ""]));
};
</script></body></html>
"""


# ────────────────────────────────────────────────────────────────
# HTTP
# ────────────────────────────────────────────────────────────────
class Server(ThreadingHTTPServer):
    """브라우저가 연결을 끊는 것은 정상이다.

    탭을 닫거나 새로고침하면 소켓이 끊기는데, 기본 구현은 그때마다 traceback 을
    찍는다. 터미널이 그것으로 덮이면 정작 봐야 할 시리얼 오류가 묻힌다.
    """

    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    shared = None
    layout = None

    def log_message(self, *a):
        pass          # 요청 로그로 터미널을 채우지 않는다

    def do_GET(self):
        if self.path.startswith("/stream"):
            return self.stream()
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while self.shared.running:
                data = json.dumps(frame(self.shared, self.layout))
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(1.0 / 30)
        except (BrokenPipeError, ConnectionResetError):
            pass      # 브라우저 탭을 닫은 것이다. 서버는 계속 돈다


def main():
    ap = argparse.ArgumentParser(description="EBIMU-9DOF 웹 3D 뷰어")
    ap.add_argument("-p", "--port", help="시리얼 포트, 예: /dev/ttyUSB0")
    ap.add_argument("-b", "--baud", type=int, default=115200, help="기본 115200")
    ap.add_argument("--layout", help="출력 항목 지정, 예: euler,gyro,accel,temp")
    ap.add_argument("--list-blocks", action="store_true",
                    help="--layout 에 쓸 수 있는 항목 목록")
    ap.add_argument("--web-port", type=int, default=8000, help="웹 포트, 기본 8000")
    ap.add_argument("--host", default="127.0.0.1",
                    help="기본 127.0.0.1 (이 기기에서만). 다른 기기에서 보려면 0.0.0.0")
    args = ap.parse_args()

    if args.list_blocks:
        live.list_blocks()
        return

    if args.layout:
        layout = (live.parse_layout(args.layout), "지정")
        live.save_layout_file(layout[0])
    else:
        saved = live.load_layout_file()
        layout = (saved, live.LAYOUT_FILE) if saved else None

    port = args.port or input("EBIMU Port (예: /dev/ttyUSB0): ").strip()
    try:
        ser = serial.Serial(port, args.baud, timeout=0.2)
    except Exception as e:
        sys.exit(f"[!] 포트를 열 수 없습니다: {e}\n"
                 f"    - 포트 확인: ls /dev/ttyUSB* /dev/ttyACM* /dev/serial*\n"
                 f"    - 권한:     sudo usermod -aG dialout $USER  (재로그인)")

    sh = live.Shared()
    threading.Thread(target=live.reader, args=(ser, sh, False), daemon=True).start()

    Handler.shared, Handler.layout = sh, layout
    httpd = Server((args.host, args.web_port), Handler)
    shown = "localhost" if args.host == "127.0.0.1" else args.host
    print(f"\n  브라우저로 여세요:  http://{shown}:{args.web_port}")
    if args.host == "127.0.0.1":
        print("  다른 기기에서 보려면 --host 0.0.0.0 을 주세요")
    print("  종료: Ctrl-C\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sh.running = False
        httpd.server_close()
        ser.close()
        print("종료했습니다.")


if __name__ == "__main__":
    main()
