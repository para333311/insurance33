"""data/latest.json 을 가족 보험나이에 대어 텔레그램으로 보낸다.

    python report.py           prev.json 이 있으면 변화만, 없으면 전체 표
    python report.py --full    전체 표 강제
    python report.py --print   보내지 않고 화면에만

토큰은 리포 밖 ~/volcano-notify/insurance-bot.json 에서 읽는다. 리포는 공개다.
"""
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KEYFILE = Path.home() / "volcano-notify" / "insurance-bot.json"


def ins_age(birth, today=None):
    """보험나이 — 만 나이에서 마지막 생일 뒤 6개월이 지났으면 +1."""
    today = today or date.today()
    y, m, d = map(int, birth.split("-"))
    age = today.year - y - ((today.month, today.day) < (m, d))
    months = (today.year - y) * 12 + (today.month - m) - (today.day < d) - age * 12
    return age + (1 if months >= 6 else 0)


def family():
    fam = json.loads((ROOT / "family.json").read_text(encoding="utf-8"))
    for p in fam:
        p["age"] = ins_age(p["birth"])
    return fam


def family_name(name):
    """'(무)참좋은더보장간병보험2607(3종)' → '(무)참좋은더보장간병보험2607' — 종·형 변형을 한 묶음으로."""
    n = re.split(r"\s*[\(\[_]?\s*\d+\s*종", name)[0]
    n = re.sub(r"\s*\(\d+\)\s*$", "", n)
    return n.strip() or name


def esc(s):
    return html.escape(str(s or ""), quote=False)


def won(v):
    try:
        n = int(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return "-"
    return f"{n // 10000}만" if n >= 10000 else f"{n:,}"


def load(name):
    p = DATA / name
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    ov = ROOT / "override.json"
    rules = json.loads(ov.read_text(encoding="utf-8")) if ov.exists() else {}
    for x in d.get("products", []):
        for prefix, amax in rules.items():
            if x["id"].startswith(prefix) or prefix in x["name"]:
                x["age_max"] = amax
                x["evidence"] = f"수동 확인: 최고 {amax}세"
    return d


def eligible_marks(prod, fam):
    if prod.get("age_max") is None:
        return "?"
    return "".join(("○" if prod["age_max"] >= p["age"] else "✕") for p in fam)


def full_report(cur, fam):
    L = [f"🏥 <b>간병·치매보험 전수 조사</b> · {esc(cur['scanned_at'])}"]
    L.append("보험나이 " + " · ".join(f"{esc(p['name'])} <b>{p['age']}</b>" for p in fam))
    for p in fam:
        n = sum(1 for x in cur["products"] if x.get("age_max") and x["age_max"] >= p["age"])
        L.append(f"  {esc(p['name'])} 가입 가능 {n}개")
    L.append("")
    L.append("표시: " + "".join(f"[{esc(p['name'][0])}]" for p in fam) + " 순서 · ○가능 ✕불가 ?미확인 · 보험료는 40세 예시(남/여)")
    groups = {}
    for x in cur["products"]:
        key = (x["source"], x["company"], family_name(x["name"]))
        groups.setdefault(key, []).append(x)
    ranked = []
    for key, items in groups.items():
        amax = max((i["age_max"] for i in items if i.get("age_max")), default=None)
        ranked.append((amax if amax is not None else -1, key, items))
    order = {"손보협회": 0, "생보협회": 1, "보험다모아": 2}
    ranked.sort(key=lambda r: (order.get(r[1][0], 9), -r[0], r[1][1]))
    last_src = None
    for amax, (src, co, fam_name), items in ranked:
        if src != last_src:
            L.append(f"\n<b>── {esc(src)} ──</b>")
            last_src = src
        rep = max(items, key=lambda i: i.get("age_max") or 0)
        marks = eligible_marks(rep, fam)
        amin = min((i["age_min"] for i in items if i.get("age_min")), default=None)
        age = f"{amin}~{amax}세" if amax and amax > 0 else "가입나이 미확인"
        first = rep.get("age_first")
        if first and amax and first != amax:
            age += f" (첫 표기 {first})"
        var = f" ({len(items)}종)" if len(items) > 1 else ""
        if any(i.get("elderly") for i in items):
            var += " 🧓고령자탭"
        prem = ""
        if rep.get("premium_m") or rep.get("premium_w"):
            prem = f" · {won(rep.get('premium_m'))}/{won(rep.get('premium_w'))}"
        L.append(f"{marks} <b>{esc(co)}</b> {esc(fam_name)}{var} — {age}{prem}")
        if rep.get("evidence"):
            L.append(f"   <i>{esc(rep['evidence'][:70])}</i>")
    return "\n".join(L)


def diff_report(prev, cur, fam):
    L = [f"🏥 <b>간병·치매보험</b> · {esc(cur['scanned_at'])}"]
    L.append("보험나이 " + " · ".join(f"{esc(p['name'])} {p['age']}" for p in fam))
    changes = 0
    for src in ("손보협회", "생보협회", "보험다모아"):
        ps, cs = prev["sources"].get(src, {}), cur["sources"].get(src, {})
        if not (ps.get("ok") and cs.get("ok")):
            continue
        old = {x["id"]: x for x in prev["products"] if x["source"] == src}
        new = {x["id"]: x for x in cur["products"] if x["source"] == src}
        for i in new.keys() - old.keys():
            x = new[i]
            changes += 1
            age = f"{x['age_min']}~{x['age_max']}세" if x.get("age_max") else "가입나이 미확인"
            L.append(f"\n🆕 <b>{esc(x['company'])}</b> {esc(x['name'])} — {age} {eligible_marks(x, fam)}")
            if x.get("evidence"):
                L.append(f"   <i>{esc(x['evidence'][:70])}</i>")
            if x.get("summary_url"):
                L.append(f"   {esc(x['summary_url'])}")
        for i in old.keys() - new.keys():
            changes += 1
            L.append(f"\n🚫 사라짐 <b>{esc(old[i]['company'])}</b> {esc(old[i]['name'])}")
        for i in old.keys() & new.keys():
            a, b = old[i], new[i]
            if a.get("age_max") != b.get("age_max") and b.get("age_max"):
                changes += 1
                L.append(f"\n🔁 가입나이 변경 <b>{esc(b['company'])}</b> {esc(b['name'])} — {a.get('age_max')}→{b['age_max']}세 {eligible_marks(b, fam)}")
    old_e = {(e["company"], e["name"]) for e in prev.get("elderly_care", [])}
    new_e = {(e["company"], e["name"]) for e in cur.get("elderly_care", [])}
    for co, nm in new_e - old_e:
        changes += 1
        L.append(f"\n📌 고령자(60세↑) 탭에 간병·치매 등장: <b>{esc(co)}</b> {esc(nm)}")
    L.append("")
    if changes == 0:
        L.append("변동 없음 · " + " · ".join(
            f"{esc(k)} {v.get('count', 0)}" for k, v in cur["sources"].items() if v.get("ok")))
    for k, v in cur["sources"].items():
        if not v.get("ok"):
            L.append(f"⚠️ 장애: {esc(k)} 못 읽음 — {esc(v.get('error', ''))}")
    return "\n".join(L)


def split_messages(text, limit=3900):
    parts, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            parts.append(buf)
            buf = ""
        buf += line + "\n"
    if buf.strip():
        parts.append(buf)
    return parts


def send(text):
    cfg = json.loads(KEYFILE.read_text(encoding="utf-8"))
    for part in split_messages(text):
        body = urllib.parse.urlencode({
            "chat_id": cfg["chat_id"], "text": part, "parse_mode": "HTML", "disable_web_page_preview": "true",
        }).encode()
        r = urllib.request.urlopen(f"https://api.telegram.org/bot{cfg['token']}/sendMessage", data=body, timeout=30)
        j = json.loads(r.read())
        if not j.get("ok"):
            raise RuntimeError(f"텔레그램 실패: {j}")


if __name__ == "__main__":
    cur = load("latest.json")
    if not cur:
        sys.exit("data/latest.json 없음 — scan.py 먼저")
    prev = load("prev.json")
    fam = family()
    if "--full" in sys.argv or not prev:
        text = full_report(cur, fam)
    else:
        text = diff_report(prev, cur, fam)
    if "--print" in sys.argv:
        print(text)
    else:
        send(text)
        print(f"보냄 ({len(text)}자)")
