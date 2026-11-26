import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as k from "./anatomy/iso-kit.mjs";
import * as A from "./anatomy/audit.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUTPUT = join(HERE, "..");
const WIDTH = 690;
const HEIGHT = 455;
const PLATE = { x: 0, y: 0, w: 94, d: 48.5, r: 5.9 };
const DECK = { x: 3.1, y: 2.8, w: 65.6, d: 42.9, r: 4.6 };
const AUX = { x: 71, y: 2.8, w: 20, d: 42.9, r: 4.4 };
const CAMERAS = [
  ["iso", 64, 43, "메인"],
  ["top", 90, 84, "위"],
];
const KEYS = [
  ["top-left", 14.3, 13.35, "위 왼쪽 · Enter", "Enter"],
  ["top-center", 35.8, 13.35, "위 가운데 · Boss", "Boss"],
  ["top-right", 57.3, 13.35, "위 오른쪽 · 전체 선택", "all"],
  ["bottom-left", 14.3, 35.15, "아래 왼쪽 · 잘라내기", "cut"],
  ["bottom-center", 35.8, 35.15, "아래 가운데 · 복사", "copy"],
  ["bottom-right", 57.3, 35.15, "아래 오른쪽 · 붙여넣기", "paste"],
  ["screen-toggle", 81, 35.15, "HDMI 화면 버튼 · 고정 기능", "screen"],
];
const KNOB = [81, 13.35];
const TRAVEL = 0.46;
const ALLOY = ["#525861", "#69717D", "#8B95A1", "#ADB6C1", "#C6CDD5", "#3E454F", "#DFE4EA"];
const DARK = ["#151922", "#222833", "#303744", "#424A57", "#3E4651", "#111620", "#566171"];
const WELL = ["#0D1119", "#151B26", "#202733", "#2B3441", "#1C2330", "#10151D", "#45505F"];
const CAP = ["#1C2431", "#263141", "#364354", "#4C5D70", "#435265", "#141D2A", "#6B7E94"];
const GLASS = ["#252E3B", "#323D4C", "#434F60", "#5B6B7D", "#364455", "#161F2C", "#73849A"];
const ROTOR = ["#39434E", "#5A646F", "#73808D", "#A4AEBA", "#ADB6C0", "#303947", "#D0D7DF"];
const ACCENT = "#365ADF";
const CAP_HIGHLIGHT = "#A8BEFF";
const ROTOR_HIGHLIGHT = "#2948BF";
const LABEL_INK = "#222C40";
const ENGRAVING = "#EEF1F7";

const esc = (s) => s.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const planAt = (x, y, w, d = w, r = 1.8) => ({ x: x - w / 2, y: y - d / 2, w, d, r });
const path = (d, attrs = "") => d ? `<path d="${d}" ${attrs}/>` : "";
const n = (x) => Number(x.toFixed(2));
const stroke = (color, width = 0.65) => `fill="none" stroke="${color}" stroke-width="${width}" stroke-linejoin="round" stroke-linecap="round"`;

function paint(p, material, { surface = false, details = "" } = {}) {
  const [s0, s1, s2, s3, top, outline, bevel] = material;
  return [
    path(p.fill, `fill="${s0}"`),
    ...p.shades.map((d, i) => path(d, `fill="${[s0, s1, s2, s3][i]}"`)),
    path(p.top, `class="${surface ? "surface" : "face"}" fill="${top}"`),
    path(p.crease, `class="iso-line iso-crease" ${stroke(outline, 0.65)}`),
    path(p.bevel, `class="iso-line iso-bevel" ${stroke(bevel, 0.85)}`),
    details,
    path(p.outline, `class="iso-line iso-edge" ${stroke(outline, 0.78)}`),
  ].join("");
}

const legends = {
  all: '<path d="m-5.3-3.2 1 1 1.55-1.7M-5.3 0l1 1 1.55-1.7M-5.3 3.2l1 1 1.55-1.7M-.85-3h6M-.85.2h6M-.85 3.4h6"/>',
  cut: '<circle cx="-3.2" cy="2.7" r="1.55"/><circle cx="3.2" cy="2.7" r="1.55"/><path d="M-2 1.45 4.3-4.6M2 1.45-4.3-4.6"/>',
  copy: '<rect x="-1.9" y="-2.05" width="6.35" height="7" rx=".65"/><path d="M1.1-3.2v-1.25a.6.6 0 0 0-.6-.6h-4.2a.6.6 0 0 0-.6.6V1.1a.6.6 0 0 0 .6.6h.7"/>',
  paste: '<path d="M-1.9-1.3h-2.5v6.4h6.1v-2M-1.1-3.1v-1.8h4.9l1.4 1.4v5.9h-6.3v-5.5Z"/><path d="M3.8-4.9v1.5h1.4"/>',
  screen: '<rect x="-5" y="-3.45" width="9.4" height="6.2" rx=".65"/><path d="M-2.2 4.25h4.8M3.6-4.9l2.4 2.4m0-2.4L3.6-2.5"/>',
};

function render(view, azimuth, elevation, label, press = 0, compact = false) {
  const bounds = [
    ...k.boxCorners(PLATE, 0, 17.4),
    ...KEYS.flatMap(([,x,y])=>k.boxCorners(planAt(x,y,14.2,14.5),26.65,26.65)),
    ...k.boxCorners(planAt(...KNOB,15,15),33.6,33.6),
  ];
  const P = k.fitProjection(bounds, WIDTH, HEIGHT, { pad: 63, azimuth, elevation });
  const R = A.recorder(P);
  const project = (p) => k.iso(p, P).map(n);
  const put = (name, center, svg, shapes) =>
    R.put({ name, at: center, svg, shapes: shapes.map((shape) => R.solid(shape)) });
  const slabData = (name, plan, z, h, material, options = {}) => {
    const steps = options.steps ?? (compact ? 2 : 6);
    return {
      shape: A.slab(plan, z, h, {name, ...(options.meta ?? {})}, steps),
      svg: paint(k.slabOf(plan, z, h, P, steps, options.bevel ?? 0.42), material, options),
    };
  };
  const cylinderData = (name, x, y, radius, z, h, material, options = {}) => ({
    shape: A.cylinder(x, y, radius, z, h, {name}),
    svg: paint(k.cylinder(x, y, radius, z, h, P, compact ? 24 : 64, options.bevel ?? 0.28), material, options),
  });
  const putParts = (name, center, parts, wrap = (body) => body) =>
    put(name, center, wrap(parts.map((part) => part.svg).join("")), parts.map((part) => part.shape));
  const slab = (name, plan, z, h, material, options) =>
    putParts(name, [plan.x + plan.w / 2, plan.y + plan.d / 2, z + h / 2],
      [slabData(name, plan, z, h, material, options)]);
  const number = (number) => number.toFixed(2);

  function ports() {
    const [front, right] = [Math.sin(azimuth * Math.PI / 180) > 0,
      Math.cos(azimuth * Math.PI / 180) > 0];
    const shell = k.insetPlan(PLATE,.55);
    const y = front ? shell.y + shell.d : shell.y;
    const side = (x, yy, z, face, body) =>
      `<g pointer-events="none" transform="${k.sideMatrix([x,yy],z,P,face)}">${body}</g>`;
    const jack = (type, w, h) => {
      const rx = type === "c" ? h/2 : type === "a" ? .35 : .14;
      const layers = [
        `<rect x="${-w/2}" y="${-h/2}" width="${w}" height="${h}" rx="${rx}" fill="#0D1719" stroke="#879694" stroke-width=".25"/>`,
        `<rect x="${-w/2+.38}" y="${-h/2+.37}" width="${w-.76}" height="${h-.74}" rx="${Math.max(.08,rx-.23)}" fill="#071113"/>`,
      ];
      if (type === "a") {
        layers.push(`<rect x="${-w/2+.86}" y=".13" width="${w-1.72}" height="1.07" rx=".06" fill="#4381A1"/>`);
        for(let i=0;i<4;i++){
          layers.push(`<rect x="${-w/2+2.22+i*(w-4.4)/3}" y=".36" width=".49" height=".52" rx=".09" fill="#B7C5B2"/>`);
        }
      }
      if (type === "c") layers.push(`<rect x="${-w/2+1.47}" y="-.28" width="${w-2.94}" height=".56" rx=".22" fill="#58696A"/>`);
      if (type === "hdmi") layers.push(`<path d="M${-w/2+.71} -1H${w/2-.71}V.29l-.77.79H${-w/2+1.48}l-.77-.79Z" fill="#26373A"/>`);
      return layers.join("");
    };
    const longPorts = front ? [[57, "a", 13.1, 5], [75, "c", 9, 3.3]]
      : [[27, "a", 13.1, 5], [49, "hdmi", 14.1, 4], [70, "c", 9, 3.3]];
    const marks = longPorts.map(([x,type,w,h])=>side(x,y,8.1,"left",jack(type,w,h)));
    const x = right ? shell.x + shell.w : shell.x;
    if (Math.abs(Math.cos(azimuth*Math.PI/180)) > .08) {
      if (right) marks.push(side(x,24.5,8.1,"right",jack("c",9,3.3)));
      else {
        marks.push(side(x,25,10.35,"right",jack("slot",19.3,1.85)));
        marks.push(side(x,25,6.5,"right",jack("slot",10.5,1.55)));
      }
    }
    return marks.join("");
  }

  for (const [i, [x, y]] of k.corners(PLATE, 8).entries()) {
    const foot = planAt(x, y, 9.5, 7, 2.9);
    slab(`stand.foot-${i}`, foot, 0, 1.5, WELL, {meta:{ground:true}});
    slab(`foot-carrier-${i}`, k.insetPlan(foot, .55), 1.5, 1.3, DARK);
  }
  slab("lower housing", k.insetPlan(PLATE, 0.55), 2.8, 11.2, DARK, {bevel:.95,details:ports()});
  slab("housing joint", PLATE, 14, 0.75, WELL, {bevel:.14});
  slab("silver perimeter", PLATE, 14.75, 2.15, ALLOY, {bevel:.78});
  slab("main key bay", DECK, 16.9, .5, DARK, {bevel:.18});
  slab("rotary bay", AUX, 16.9, .5, DARK, {bevel:.18});

  const engraving = (x, y, z, mark) => {
    const matrix = k.topMatrix([x, y], z, P, "x");
    if (!(mark in legends)) {
      return `<text class="key-legend" transform="${matrix}" y="1.2" text-anchor="middle" fill="${ENGRAVING}" font-family="sans-serif" font-weight="700" font-size="${mark === "Enter" ? "3.4" : "3.6"}" pointer-events="none">${mark}</text>`;
    }
    return `<g class="key-legend" transform="${matrix}" fill="none" stroke="${ENGRAVING}" stroke-width=".65" stroke-linecap="round" stroke-linejoin="round" pointer-events="none">${legends[mark]}</g>`;
  };

  for (const [slot, x, y, title, mark] of KEYS) {
    const name = slot;
    const motion = press;
    const well = planAt(x, y, 19.5, 19.8, 2.15);
    const crown = planAt(x, y, 14.2, 14.5, 1.55);
    const skirt = planAt(x, y, 17.9, 18.15, 2.1);
    const middle = planAt(x, y, 16, 16.35, 1.75);
    slab(`${name}.rim`, well, 17.4, .68, CAP, {bevel:.22});
    slab(`${name}.bed`, k.insetPlan(well, .74), 18.08, .42, WELL, {bevel:.1});
    const parts = [
      slabData(`${name}.actuator`, planAt(x, y, 5.9, 5.9, .9), 18.5, 2.75 - motion, DARK),
      slabData(`${name}.skirt`, skirt, 21.25 - motion, 1.25, GLASS, {bevel:.45}),
      slabData(`${name}.body`, middle, 22.5 - motion, 3.7, CAP, {bevel:.66}),
      slabData(`${name}.crown`, crown, 26.2 - motion, .45, GLASS, {bevel:.16, surface:true}),
    ];
    // The stem compresses visually under the cap. It remains occluded by the skirt
    // in both views and isn't translated with the visible shell on click.
    const anchor = engraving(x, y, 26.65 - motion, mark);
    const hit = path(k.planOutline(skirt, 26.65 - motion, P, 6), 'class="hit" fill="transparent" pointer-events="all"');
    const selection = path(k.planOutline(k.insetPlan(crown, .35), 26.65 - motion, P, 6),
      `class="selection-indicator" opacity="${compact && slot === "top-left" ? 1 : 0}" pointer-events="none" ${stroke(CAP_HIGHLIGHT,2)}`);
    const stem = parts.shift();
    putParts(`${name} actuator`, [x,y,19.2], [stem]);
    putParts(name, [x,y,24.4 - motion], parts, (body) =>
      `<g id="${compact ? "figma" : view}-${slot}" data-control="${slot}" role="button" tabindex="0" aria-pressed="false" aria-label="${esc(title)}" style="--travel:${number(k.screenRise(TRAVEL, P))}px"><title>${esc(title)}</title><g class="cap-travel">${body}${anchor}${selection}${hit}</g></g>`);
  }

  const [x,y] = KNOB;
  const knobBase = planAt(x, y, 19, 19.7, 1.75);
  slab("encoder plinth", knobBase, 17.4, 1.05, ALLOY, {bevel:.35});
  putParts("encoder supports", [x,y,20], [
    cylinderData("encoder isolation washer", x,y,8.1,18.45,1.2,WELL),
    cylinderData("encoder bottom collar", x,y,7.55,19.65,1.1,ALLOY),
    cylinderData("encoder stem", x,y,4.2,20.75,2.3,DARK),
  ]);
  const knobTop = 33.6;
  const topMatrix = k.topMatrix([x,y],knobTop,P,"x");
  const dialRing = k.pathOf(Array.from({length:72}, (_,index) => {
    const angle = index * 5;
    const radians = angle * Math.PI / 180;
    return project([x + Math.cos(radians) * 7.2, y + Math.sin(radians) * 7.2, knobTop]);
  }), true);
  const dialHit = path(dialRing, 'class="hit" fill="transparent" pointer-events="all"');
  const dialSelection = path(dialRing, `class="selection-indicator" opacity="0" pointer-events="none" ${stroke(ROTOR_HIGHLIGHT,2)}`);
  const pointer = `<g transform="${topMatrix}" pointer-events="none"><g class="knob-travel" data-knob-mark=""><rect x="3.7" y="-0.43" width="3" height=".86" rx=".43" fill="${ENGRAVING}"/></g></g>`;
  const rotorParts = [
    cylinderData("encoder lower edge", x,y,7.8,23.05,.7,DARK,{bevel:.14}),
    cylinderData("encoder drum", x,y,8.15,23.75,8.2,ROTOR,{bevel:.22}),
    cylinderData("encoder upper guard", x,y,7.95,31.95,1.15,ALLOY,{bevel:.23}),
    cylinderData("encoder crown", x,y,7.45,33.1,.5,ROTOR,{bevel:.16}),
  ];
  // Machined ribs are shallow surface inlays; their faded tips stop inside the ring.
  const ribs = k.knurl(x,y,8.15,24.2,31.6,compact ? 38 : 72,P,.08);
  rotorParts[1].svg += path(ribs, `stroke="#485467" stroke-opacity=".68" stroke-width=".75" stroke-linecap="round" fill="none"`);
  putParts("knurled rotary encoder", [x,y,27.5], rotorParts, (body) =>
    `<g class="encoder" id="${compact ? "figma" : view}-dial" data-control="dial" role="button" tabindex="0" aria-pressed="false" aria-label="다이얼 · 볼륨 조절"><title>다이얼 · 볼륨 조절</title>${body}${dialSelection}${dialHit}${pointer}</g>`);

  A.settle(R,P);
  R.items.sort((a,b) => a.key - b.key);
  const model = R.items.map((item) => item.svg).join("");
  const groundPath = k.planOutline(k.insetPlan(PLATE, -2.3), 0, P, 8);
  const callouts = [];
  function addCallout(slot, point, title, row) {
    const [ax,ay] = project(point);
    const cardWidth = slot === "dial" || slot === "screen-toggle" ? 132 : 192, cardHeight = 33;
    const by = row === "top" ? 26 : HEIGHT - 56;
    const bx = Math.max(27, Math.min(WIDTH-cardWidth-27, ax - cardWidth/2));
    const attachY = row === "top" ? by+cardHeight : by;
    const branchY = row === "top" ? attachY+9 : attachY-9;
    const attachX = Math.min(bx+cardWidth-14,Math.max(bx+14,ax));
    const cord = k.pathOf([[ax,ay],[ax,branchY],[attachX,attachY]]);
    callouts.push(`<g class="float-label" data-callout="${slot}" display="none" pointer-events="none">${path(cord,`class="iso-line" ${stroke(ACCENT,1.18)}`)}<circle cx="${ax}" cy="${ay}" r="4.4" fill="#FFFFFF" stroke="${ACCENT}" stroke-width="1.45"/><rect x="${bx}" y="${by}" width="${cardWidth}" height="${cardHeight}" rx="10" fill="#FFFFFF" stroke="#DDE3F2" filter="url(#callout-shadow-${view})"/><text x="${bx+cardWidth/2}" y="${by+21}" text-anchor="middle" class="float-value" fill="${LABEL_INK}" font-family="inherit" font-size="13" font-weight="700" data-action-label="${slot}">${esc(title)}</text></g>`);
  }
  for (const [slot,kx,ky] of KEYS) {
    const frontRow = ky > 20;
    const title = slot === "screen-toggle" ? "HDMI 화면 전환" : "";
    // Use the cap edge nearest the label, keeping the leader clear of the engraving.
    addCallout(slot, [kx, ky + (frontRow ? 5.8 : -5.8), 26.65], title, frontRow ? "bottom" : "top");
  }
  addCallout("dial",[x,y-2,knobTop],"볼륨 조절","top");
  const defs=`<defs><filter id="shadow-${view}" x="-30%" y="-55%" width="160%" height="220%"><feGaussianBlur stdDeviation="12"/></filter><filter id="callout-shadow-${view}" x="-15%" y="-30%" width="130%" height="180%"><feDropShadow dx="0" dy="4" stdDeviation="5" flood-color="#202B42" flood-opacity=".16"/></filter></defs>`;
  const ground=path(groundPath,`class="model-shadow" fill="#28344A" opacity=".24" filter="url(#shadow-${view})" transform="translate(0 10)"`);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" data-view="${view}" data-selected="top-left" viewBox="0 0 ${WIDTH} ${HEIGHT}" width="${WIDTH}" height="${HEIGHT}" role="group" aria-label="CH75K ${label} 보기"${view === "iso" ? "" : " hidden"}>${compact ? "" : defs + ground}<g class="device-parts">${model}</g>${compact ? "" : '<g class="model-callouts">' + callouts.join("") + "</g>"}</svg>`;
  return {R,P,svg};
}

if (process.argv.includes("--figma")) {
  const { R, P } = render(...CAMERAS[0], 0, true);
  const compact = (markup) => markup
    .replaceAll(/<title>.*?<\/title>/g, "")
    .replaceAll(/<path [^>]* (?:opacity="0"|fill="transparent")[^>]*\/>/g, "")
    .replaceAll(/ (?:class|data-[\w-]+|role|tabindex|aria-[\w-]+|pointer-events|style)="[^"]*"/g, "")
    .replaceAll(/ style="--travel:[^"]*"/g,"")
    .replaceAll(/ d="([MLZ\d.\s-]+)"/g, (_, d)=>` d="${d.replaceAll(/L(?=[-\d])/g," ")}"`);
  const chunks = [];
  let chunk = "";
  for (const {svg} of R.items) {
    const next = compact(svg);
    if (chunk.length + next.length > 27000 && chunk) {
      chunks.push(chunk);
      chunk = "";
    }
    chunk += next;
  }
  if (chunk) chunks.push(chunk);
  const wrap = content => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${WIDTH} ${HEIGHT}" width="${WIDTH}" height="${HEIGHT}">${content}</svg>`;
  const data = {chunks:chunks.map(wrap),anchor:k.iso([14.3,7.55,26.65],P)};
  writeFileSync(join(HERE,"figma-parts.json"), JSON.stringify(data));
  process.stdout.write(`Figma vector: ${data.chunks.length} ordered parts\n`);
  process.exit(0);
}
const views = CAMERAS.map((camera) => render(...camera));
if (process.argv.includes("--geometry-only")) {
  writeFileSync(join(HERE,"geometry.svg"), views[0].svg);
}
const template = join(HERE,"template.html");
if (existsSync(template) && !process.argv.includes("--geometry-only")) {
  const shell = readFileSync(template,"utf8");
  if (!shell.includes("{{CH75K_GEOMETRY}}")) throw new Error("The mapper HTML template is missing its geometry slot.");
  if (!shell.includes("{{CH75K_ICON}}")) throw new Error("The mapper HTML template is missing its app icon slot.");
  const icon = readFileSync(join(OUTPUT,"assets","app-icon.svg")).toString("base64");
  writeFileSync(join(OUTPUT,"mapper.html"), shell
    .replaceAll("{{CH75K_ICON}}", `data:image/svg+xml;base64,${icon}`)
    .replace("{{CH75K_GEOMETRY}}", views.map(({svg}) => svg).join("\n")));
}
for (const [i,{R}] of views.entries()) process.stdout.write(`${CAMERAS[i][0]}: ${R.solids.length} vector solids\n`);
if (process.argv.includes("--audit")) {
  for (const [i, {R,P}] of views.entries()) {
    process.stdout.write(`\n${CAMERAS[i][0]} · 정지 · `);
    A.auditOrExit(R, P);
    const pressed = render(...CAMERAS[i], TRAVEL);
    process.stdout.write(`\n${CAMERAS[i][0]} · 키 이동 끝 · `);
    A.auditOrExit(pressed.R, pressed.P);
  }
}
