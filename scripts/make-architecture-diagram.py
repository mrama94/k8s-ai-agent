# Generates docs/architecture-diagram.svg:  python3 scripts/make-architecture-diagram.py
# The layout is explicit (every coordinate is chosen), so change the diagram here and re-run. No dependencies.
import os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
from xml.sax.saxutils import escape

W, H = 1500, 1190
out = []
def add(s): out.append(s)

def text(x, y, s, cls="t", anchor="start"):
    add(f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">{escape(s)}</text>')

def box(x, y, w, h, title, lines=(), cls="box", tcls="bt", subcls="st", gap=15, tx=None):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" class="{cls}"/>')
    cx = x + 12
    text(cx, y + 20, title, tcls)
    for i, ln in enumerate(lines):
        text(cx, y + 20 + gap * (i + 1) + 2, ln, subcls)

def zone(x, y, w, h, label, cls="zone", lcls="zl"):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" class="{cls}"/>')
    text(x + 14, y + 22, label, lcls)

def path(d, color, dashed=False, label=None, lx=0, ly=0, num=None, nx=0, ny=0, anchor="start"):
    cls = f'flow {color}' + (' dash' if dashed else '')
    add(f'<path d="{d}" class="{cls}" marker-end="url(#a-{color})"/>')
    if num is not None:
        add(f'<circle cx="{nx}" cy="{ny}" r="11" class="badge {color}"/>')
        text(nx, ny + 4, str(num), "bn", "middle")
    if label:
        text(lx, ly, label, f"fl {color}", anchor)

# ---------------------------------------------------------------- defs and styles
add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif">')
add('''<title>Kubernetes troubleshooting agent: architecture</title>
<style>
:root{--bg:#f7f9fb;--zone:#eef2f6;--zline:#b8c4d0;--ns:#ffffff;--nsline:#c9d3dd;--box:#ffffff;--boxline:#9fb0c0;--text:#17212b;--sub:#53626f;
--blue:#2563c9;--orange:#c9741a;--green:#23895a;--grey:#6b7886;--purple:#7a4fc0;--hl:#e3efff;--hl2:#fff1df;--hl3:#e0f4ea;--hl4:#f0e8fb}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#10171d;--zone:#161f27;--zline:#33424f;--ns:#1b252e;--nsline:#34444f;--box:#202c36;--boxline:#4a5d6c;--text:#e6edf3;--sub:#9db0bf;
--blue:#6aa4ff;--orange:#f0a050;--green:#4cc88b;--grey:#98a7b5;--purple:#b794f0;--hl:#17314f;--hl2:#4a3217;--hl3:#17402d;--hl4:#35254f}}
:root[data-theme="dark"]{--bg:#10171d;--zone:#161f27;--zline:#33424f;--ns:#1b252e;--nsline:#34444f;--box:#202c36;--boxline:#4a5d6c;--text:#e6edf3;--sub:#9db0bf;
--blue:#6aa4ff;--orange:#f0a050;--green:#4cc88b;--grey:#98a7b5;--purple:#b794f0;--hl:#17314f;--hl2:#4a3217;--hl3:#17402d;--hl4:#35254f}
.bg{fill:var(--bg)}
.zone{fill:var(--zone);stroke:var(--zline);stroke-width:1.5;stroke-dasharray:6 4}
.kind{fill:var(--zone);stroke:var(--zline);stroke-width:2}
.ns{fill:var(--ns);stroke:var(--nsline);stroke-width:1.5}
.box{fill:var(--box);stroke:var(--boxline);stroke-width:1.4}
.boxb{fill:var(--hl);stroke:var(--blue);stroke-width:1.6}
.boxo{fill:var(--hl2);stroke:var(--orange);stroke-width:1.6}
.boxg{fill:var(--hl3);stroke:var(--green);stroke-width:1.6}
.boxp{fill:var(--hl4);stroke:var(--purple);stroke-width:1.6}
.title{font-size:26px;font-weight:700;fill:var(--text)}
.subtitle{font-size:14px;fill:var(--sub)}
.zl{font-size:13px;font-weight:700;fill:var(--sub);letter-spacing:.06em}
.nl{font-size:12px;font-weight:700;fill:var(--sub)}
.bt{font-size:13.5px;font-weight:700;fill:var(--text)}
.st{font-size:11.5px;fill:var(--sub)}
.t{font-size:12px;fill:var(--text)}
.chip{fill:var(--box);stroke:var(--boxline);stroke-width:1}
.chipt{font-size:10px;font-weight:600;fill:var(--text)}
.flow{fill:none;stroke-width:2.4}
.flow.dash{stroke-dasharray:7 5}
.flow.blue{stroke:var(--blue)}.flow.orange{stroke:var(--orange)}.flow.green{stroke:var(--green)}.flow.grey{stroke:var(--grey)}.flow.purple{stroke:var(--purple)}
.badge.blue{fill:var(--blue)}.badge.orange{fill:var(--orange)}.badge.green{fill:var(--green)}.badge.grey{fill:var(--grey)}.badge.purple{fill:var(--purple)}
.bn{font-size:12px;font-weight:700;fill:#fff}
.fl{font-size:11.5px;font-weight:600}
.fl.blue{fill:var(--blue)}.fl.orange{fill:var(--orange)}.fl.green{fill:var(--green)}.fl.grey{fill:var(--grey)}.fl.purple{fill:var(--purple)}
.lg{font-size:12.5px;fill:var(--text)}
.lgh{font-size:13px;font-weight:700;fill:var(--text)}
</style>''')
add('<defs>')
for c, var in (("blue", "--blue"), ("orange", "--orange"), ("green", "--green"), ("grey", "--grey"), ("purple", "--purple")):
    add(f'<marker id="a-{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" style="fill:var({var})"/></marker>')
add('</defs>')
add(f'<rect width="{W}" height="{H}" class="bg"/>')

# ---------------------------------------------------------------- title
text(24, 34, "Kubernetes troubleshooting agent: architecture", "title")
text(24, 56, "An AI agent that investigates, proposes and (after approval) fixes problems in a cluster. Everything runs on one Mac; Git is the source of truth.", "subtitle")

# ---------------------------------------------------------------- YOU column
zone(20, 96, 236, 880, "YOU")
box(36, 126, 204, 96, "You, the developer", ["edit, commit, push to GitHub", "approve or reject agent plans"], "boxb")
box(36, 246, 204, 112, "Browser", ["Agent chat and alerts  :8000", "Grafana  :3000", "Argo CD  :8080"], "boxb")
box(36, 382, 204, 112, "lab CLI", ["lab up | down | stop | start", "lab status | doctor | forward", "wraps bootstrap.sh"], "box")
box(36, 518, 204, 150, "Kept on your Mac, not in Git", ["Argo CD deploy key (read-only)", "Sealing key backup", "Image pull token (source)", "~/.config/k8s-agent-lab/"], "boxp")

# ---------------------------------------------------------------- MAC zone
zone(280, 96, 910, 880, "YOUR MAC  (Apple Silicon)")
box(312, 128, 266, 66, "kubectl port-forward", ["auto-restarting  (scripts/portforward.sh)"], "box")
box(852, 128, 306, 82, "Ollama  +  qwen2.5:7b", ["the model; runs on the Mac's GPU, outside the", "cluster. One server, :11434"], "boxb")

# kind cluster
add('<rect x="298" y="236" width="876" height="722" rx="12" class="kind"/>')
text(312, 258, "kind cluster  agent-lab  (Docker)", "zl")

# namespace: demo (left)
add('<rect x="312" y="272" width="220" height="232" rx="10" class="ns"/>'); text(324, 292, "namespace  demo", "nl")
box(326, 304, 192, 74, "storefront", ["healthy, owned by Git", "(Argo CD: manual sync)"], "boxg", gap=14)
box(326, 392, 192, 100, "Test workloads", ["broken on purpose, one", "by one; the agent fixes", "them live; Argo CD shows", "the drift, never reverts it"], "box", gap=14)

# namespace: ai-agent (middle)
add('<rect x="568" y="272" width="300" height="340" rx="10" class="ns"/>'); text(580, 292, "namespace  ai-agent", "nl")
add('<rect x="580" y="302" width="276" height="196" rx="8" class="boxb"/>')
text(592, 322, "The agent  (FastAPI + LangGraph)", "bt")
steps = ["Investigate", "Propose", "Approve", "Apply", "Verify"]
sx = 592
for s_ in steps:
    wch = {"Investigate": 68, "Propose": 50, "Approve": 50, "Apply": 38, "Verify": 40}[s_]
    add(f'<rect x="{sx}" y="334" width="{wch}" height="22" rx="5" class="chip"/>')
    text(sx + wch / 2, 349, s_, "chipt", "middle")
    sx += wch + 2
for i, ln in enumerate(["12 read-only tools  (pods, events, logs,", "metrics, objects, runbooks)", "7 write actions, only after you approve", "Guards in code  +  RBAC in Kubernetes", "Code (not the model) verifies the fix"]):
    text(592, 378 + i * 16, ln, "st")
text(592, 470, "Alert runs queue: one at a time", "st")
box(580, 512, 124, 86, "SQLite", ["on a volume:", "runs, summaries,", "alert list"], "box", gap=14)
box(732, 512, 124, 86, "Sealed Secrets", ["webhook token", "image pull", "credential"], "boxp", gap=14)

# namespace: argocd (right)
add('<rect x="904" y="272" width="256" height="232" rx="10" class="ns"/>'); text(916, 292, "namespace  argocd", "nl")
add('<rect x="916" y="302" width="232" height="190" rx="8" class="boxg"/>')
text(928, 322, "Argo CD  (slimmed)", "bt")
for i, ln in enumerate(["reads Git every few minutes", "root app lists the apps:", "platform, secrets, sealed-secrets,", "agent  :  auto-sync + self-heal", "demo  :  manual sync, no self-heal", "(drift shown, never undone)"]):
    text(928, 342 + i * 17, ln, "st")

# namespace: kube-system (under demo)
add('<rect x="312" y="522" width="220" height="104" rx="10" class="ns"/>'); text(324, 542, "namespace  kube-system", "nl")
box(326, 552, 192, 62, "API server  +  RBAC", ["RBAC: the hard limit", "metrics-server"], "box", gap=14)

# namespace: sealed-secrets (under argocd)
add('<rect x="904" y="522" width="256" height="104" rx="10" class="ns"/>'); text(916, 542, "namespace  sealed-secrets", "nl")
box(916, 552, 232, 62, "Sealed Secrets controller", ["decrypts Git's encrypted files", "into ordinary Secrets"], "boxp", gap=14)

# namespace: monitoring (bottom, wide)
add('<rect x="312" y="652" width="848" height="290" rx="10" class="ns"/>'); text(324, 672, "namespace  monitoring", "nl")
box(330, 690, 150, 66, "kube-state-metrics", ["turns object state", "into metrics"], "box", gap=14)
box(512, 690, 180, 66, "Prometheus", ["scrapes every 30 s", "alert rules, 6 h of data"], "box", gap=14)
box(724, 690, 170, 66, "Alertmanager", ["groups alerts, sends", "a webhook to the agent"], "boxo", gap=14)
box(330, 800, 150, 66, "Alloy", ["tails every pod's logs", "through the API"], "box", gap=14)
box(512, 800, 180, 66, "Loki", ["stored logs, 6 h", "(incl. deleted pods)"], "box", gap=14)
box(724, 800, 170, 66, "Grafana", ["dashboard + alert", "history + logs panel"], "box", gap=14)
box(926, 690, 220, 176, "Alert rules cover", ["Pending, CrashLoop, ImagePull,", "not ready, replicas missing,", "scaled to zero, no endpoints,", "StatefulSets, DaemonSets,", "failed Jobs, suspended CronJobs"], "box", gap=15)
text(330, 912, "No persistent storage here: Prometheus and Loki start empty after a rebuild.", "st")

# ---------------------------------------------------------------- GITHUB zone
zone(1220, 96, 262, 880, "GITHUB")
box(1236, 128, 230, 132, "Repository  (private)", ["main branch", "agent/   apps/   platform/", "demo/   bootstrap/   scripts/", "encrypted secrets only"], "boxg")
box(1236, 304, 230, 176, "GitHub Actions", ["ci: unit tests, lint,", "secret scan, manifest and", "Helm schema checks", "agent-image: tests first, then", "build arm64, push, commit tag"], "boxg")
box(1236, 524, 230, 96, "ghcr.io  (private)", ["k8s-agent:sha-xxxxxxx", "pulled with a sealed token"], "boxg")

# ---------------------------------------------------------------- flows
# 1 you ask: browser -> port-forward -> agent
path("M 240 300 H 262 V 161 H 312", "blue", num=1, nx=262, ny=232)
path("M 578 161 H 730 V 302", "blue")
text(594, 152, "chat, approve, reject", "fl blue")
# 2 agent <-> ollama
path("M 800 302 V 244 H 1005 V 212", "blue", num=2, nx=900, ny=244)
text(1015, 232, "prompts and answers", "fl blue")
# 3 agent -> API server (read everywhere, patch only demo)
path("M 580 430 H 542 V 584 H 520", "blue", num=3, nx=542, ny=470)
# 4 alert flow: ksm -> prometheus -> alertmanager -> agent
path("M 480 723 H 510", "orange")
path("M 692 723 H 722", "orange")
path("M 809 690 V 650 H 718 V 500", "orange", num=4, nx=764, ny=650)
text(820, 680, "webhook to the agent", "fl orange")
# 5 evidence: agent -> prometheus / loki
path("M 580 400 H 558 V 652 H 602 V 688", "grey", dashed=True, num=5, nx=558, ny=620)
# monitoring internals
path("M 480 833 H 510", "grey")
path("M 722 833 H 694", "grey")
path("M 760 800 L 690 758", "grey")
# 9 secrets: controller -> agent ns
path("M 916 584 H 886 V 556 H 860", "purple", num=9, nx=886, ny=584)
# 7 gitops: repo -> argo ; argo applies -> ai-agent
path("M 1236 205 H 1204 V 400 H 1150", "green", num=7, nx=1204, ny=300)
path("M 916 420 H 872", "green", dashed=True)
text(894, 412, "applies", "fl green", "middle")
# 6 delivery: developer push, repo -> actions, actions -> ghcr, actions -> repo (tag)
path("M 138 126 V 82 H 1351 V 126", "green", num=6, nx=520, ny=82)
text(540, 77, "git push", "fl green")
path("M 1320 262 V 302", "green")
path("M 1320 482 V 522", "green")
path("M 1452 392 H 1472 V 196 H 1468", "green", dashed=True)
text(1328, 288, "push triggers CI", "fl green")
# 8 image pull
path("M 1236 580 H 1180", "green", num=8, nx=1208, ny=580)
# lab -> cluster
path("M 240 438 H 296", "grey", dashed=True)

# ---------------------------------------------------------------- legend
lx, ly = 24, 1000
add(f'<rect x="20" y="{ly - 14}" width="1462" height="176" rx="10" class="zone" style="stroke-dasharray:none"/>')
text(lx + 10, ly + 6, "How to read it", "lgh")
cols = [
 [("1", "blue", "You chat in the browser; a port-forward reaches the agent."), ("2", "blue", "The agent asks the local model what it thinks."), ("3", "blue", "The agent reads the cluster; after approval it patches the demo namespace only.")],
 [("4", "orange", "Prometheus sees a problem; Alertmanager sends a webhook; the agent investigates and stops at approval."), ("5", "grey", "The agent looks up metric history and stored logs.")],
 [("6", "green", "You push; Actions test, build the image, push it and commit the new tag."), ("7", "green", "Argo CD pulls Git and applies it. Secrets arrive encrypted."), ("8", "green", "The nodes pull the private image."), ("9", "purple", "The controller decrypts sealed secrets (needs the key kept on your Mac).")],
]
colx = [lx + 10, lx + 490, lx + 970]
for ci, items in enumerate(cols):
    y = ly + 28
    for num, color, s_ in items:
        add(f'<circle cx="{colx[ci] + 9}" cy="{y - 4}" r="9" class="badge {color}"/>')
        text(colx[ci] + 9, y, num, "bn", "middle")
        # wrap long lines at ~70 chars
        words, line, lines = s_.split(), "", []
        for w_ in words:
            if len(line) + len(w_) + 1 > 60:
                lines.append(line); line = w_
            else:
                line = (line + " " + w_).strip()
        lines.append(line)
        for k, ln in enumerate(lines):
            text(colx[ci] + 26, y + k * 15, ln, "lg")
        y += 15 * len(lines) + 7
add('</svg>')
open(os.path.join(ROOT, "docs", "architecture-diagram.svg"), "w").write("\n".join(out))
print("written", len("\n".join(out)), "bytes")
