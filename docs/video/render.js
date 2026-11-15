// Renders the video scenes with Playwright (pre-installed Chromium).
//   node render.js slide <scene.json> <out.png>
//   node render.js terminal <timeline.json> <seconds> <out-dir>   (writes a .webm into out-dir)
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const W = 1920, H = 1080;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const BASE_CSS = `
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: ${W}px; height: ${H}px; overflow: hidden; }
  body { font-family: "Inter", "DejaVu Sans", sans-serif; color: #e6edf3;
         background: radial-gradient(1200px 800px at 20% 10%, #16233f 0%, #0b1220 60%, #070b14 100%); }
  .footer { position: absolute; left: 96px; right: 96px; bottom: 44px; display: flex; justify-content: space-between;
            font-size: 24px; color: #7d8ba3; letter-spacing: 0.02em; }
  .accent { color: #3dd6c8; }
`;

function slideHtml(scene) {
  const bullets = (scene.bullets || []).map((b) => {
    const mono = /→/.test(b);
    return `<li class="${mono ? "mono" : ""}">${esc(b)}</li>`;
  }).join("");
  const isTitle = !!scene.subtitle;
  return `<!doctype html><html><head><meta charset="utf-8"><style>${BASE_CSS}
    .wrap { position: absolute; left: 96px; top: ${isTitle ? 300 : 120}px; right: 96px; }
    h1 { font-size: ${isTitle ? 112 : 64}px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.05; }
    h1 small { display: block; font-size: 36px; font-weight: 400; color: #9fb0c8; margin-top: 22px; letter-spacing: 0; }
    .bar { width: 120px; height: 8px; background: #3dd6c8; border-radius: 4px; margin: ${isTitle ? 48 : 28}px 0 ${isTitle ? 56 : 48}px; }
    ul { list-style: none; }
    li { font-size: ${isTitle ? 38 : 42}px; line-height: 1.35; padding-left: 52px; position: relative; margin-bottom: ${isTitle ? 22 : 30}px; color: #d8e0ea; }
    li::before { content: ""; position: absolute; left: 0; top: 0.55em; width: 18px; height: 18px; border-radius: 50%; background: #3dd6c8; }
    li.mono { font-family: "DejaVu Sans Mono", monospace; font-size: 34px; color: #9ee7df; }
    .num { position: absolute; right: 96px; top: 64px; font-size: 28px; color: #7d8ba3; }
  </style></head><body>
    <div class="num">${esc(scene.num)}</div>
    <div class="wrap">
      <h1>${esc(scene.title)}${scene.subtitle ? `<small>${esc(scene.subtitle)}</small>` : ""}</h1>
      <div class="bar"></div>
      <ul>${bullets}</ul>
    </div>
    <div class="footer"><span>github.com/arielgiamportone/paradox-db-py</span><span class="accent">pip install paradoxdb</span></div>
  </body></html>`;
}

function terminalHtml(timeline, seconds) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>${BASE_CSS}
    .title { position: absolute; left: 96px; top: 48px; font-size: 34px; color: #9fb0c8; }
    .win { position: absolute; left: 96px; right: 96px; top: 116px; bottom: 100px; background: #0d1117; border: 1px solid #263046;
           border-radius: 16px; box-shadow: 0 30px 80px rgba(0,0,0,.5); overflow: hidden; }
    .chrome { height: 54px; background: #161b22; border-bottom: 1px solid #263046; display: flex; align-items: center; padding-left: 22px; gap: 12px; }
    .dot { width: 16px; height: 16px; border-radius: 50%; }
    .screen { padding: 22px 30px; font-family: "DejaVu Sans Mono", monospace; font-size: 25px; line-height: 1.42;
              white-space: pre-wrap; word-break: break-all; color: #c9d1d9; height: calc(100% - 54px); overflow: hidden; }
    .screen { font-size: 29px; line-height: 1.4; }
    .p { color: #3dd6c8; font-weight: bold; } .c { color: #ffffff; } .o { color: #c9d1d9; }
    .cur { display: inline-block; width: 14px; height: 28px; background: #e6edf3; vertical-align: -4px; animation: b 1s steps(1) infinite; }
    @keyframes b { 50% { opacity: 0; } }
  </style></head><body>
    <div class="title">${esc(timeline.title || "")}</div>
    <div class="win"><div class="chrome"><div class="dot" style="background:#ff5f57"></div><div class="dot" style="background:#febc2e"></div><div class="dot" style="background:#28c840"></div></div>
    <div class="screen" id="s"></div></div>
    <div class="footer"><span>${esc(timeline.footer || "demo sobre tablas sintéticas · nada de esto es un dato real")}</span><span class="accent">pip install paradoxdb</span></div>
    <script>
      const steps = ${JSON.stringify(timeline.steps)};
      const total = ${Number(seconds)};
      const s = document.getElementById("s");
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      // Typing and output run at a natural pace (mildly scaled); whatever narration
      // time is left becomes reading pauses after each command.
      let natural = 0;
      for (const st of steps) natural += st.cmd.length * 0.045 + 0.5 + Math.min(st.out.length, 40) * 0.06;
      const budget = Math.max(total - 2.0, 3);
      const k = Math.min(Math.max((budget - steps.length * 1.2) / natural, 0.6), 1.4);
      const used = natural * k + 0.7;
      const pause = Math.max((budget - used) / steps.length, 1.2) * 1000;
      const scroll = () => { s.scrollTop = s.scrollHeight; };
      async function run() {
        await sleep(700);
        for (const st of steps) {
          const line = document.createElement("div");
          line.innerHTML = '<span class="p">$ </span><span class="c"></span><span class="cur"></span>';
          s.appendChild(line); scroll();
          const c = line.querySelector(".c");
          for (const ch of st.cmd) { c.textContent += ch; await sleep(45 * k); }
          await sleep(500 * k);
          line.querySelector(".cur").remove();
          for (const o of st.out) {
            const d = document.createElement("div"); d.className = "o"; d.textContent = o || " ";
            s.appendChild(d); scroll(); await sleep(60 * k);
          }
          await sleep(pause);
        }
        const tail = document.createElement("div");
        tail.innerHTML = '<span class="p">$ </span><span class="cur"></span>';
        s.appendChild(tail); scroll();
        document.title = "done";
      }
      run();
    </script>
  </body></html>`;
}

(async () => {
  const [mode, input, a, b] = process.argv.slice(2);
  const data = JSON.parse(fs.readFileSync(input, "utf8"));
  const browser = await chromium.launch();
  if (mode === "slide") {
    const page = await browser.newPage({ viewport: { width: W, height: H } });
    await page.setContent(slideHtml(data), { waitUntil: "load" });
    await page.waitForTimeout(300);
    await page.screenshot({ path: a, type: "png" });
  } else if (mode === "terminal") {
    const seconds = Number(a);
    const ctx = await browser.newContext({ viewport: { width: W, height: H }, recordVideo: { dir: b, size: { width: W, height: H } } });
    const page = await ctx.newPage();
    await page.setContent(terminalHtml(data, seconds), { waitUntil: "load" });
    await page.waitForFunction(() => document.title === "done", null, { timeout: (seconds + 60) * 1000 });
    await page.waitForTimeout(1500);
    const video = page.video();
    await ctx.close();
    const p = await video.path();
    fs.renameSync(p, path.join(b, path.basename(input, ".json") + ".webm"));
  } else {
    throw new Error("mode must be slide or terminal");
  }
  await browser.close();
})();
