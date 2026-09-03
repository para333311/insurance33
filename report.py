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

# 파라님 지시(2026-09-03): 대상자 나이 기준 추정 보험료가 이 값 이상이면 아예 보여주지 않는다.
MAX_PREMIUM = 100_000

# 공시는 40세 예시보험료만 준다(141개 중 125개가 40세 기준, 나머지는 미기재). 대상자 나이 보험료는
# 어느 공시에도 없어 보험사 견적을 받아야 한다. 그래서 나이 배수로 추정한다 —
# 하나손보 상품요약서의 40/50/60세 표에서 10년마다 약 1.35배로 실측됐다(14,380→19,340→26,190).
# 어디까지나 후보를 좁히기 위한 추정이다. 실제 보험료는 상품 구조에 따라 크게 다를 수 있다.
AGE_STEP = 1.35


def ins_age(birth, today=None):
    """보험나이 — 만 나이에서 마지막 생일 뒤 6개월이 지났으면 +1."""
    today = today or date.today()
    y, m, d = map(int, birth.split("-"))
    age = today.year - y - ((today.month, today.day) < (m, d))
    months = (today.year - y) * 12 + (today.month - m) - (today.day < d) - age * 12
    return age + (1 if months >= 6 else 0)


def family():
    """생년월일이 있으면 보험나이를 정확히, 생년만 있으면 대략으로 센다(파라님이 나중에 채워주신다)."""
    fam = json.loads((ROOT / "family.json").read_text(encoding="utf-8"))
    for p in fam:
        p["approx"] = not p.get("birth")
        p["age"] = date.today().year - int(p["year"]) if p["approx"] else ins_age(p["birth"])
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
    return f"{n / 10000:.1f}만" if n >= 10000 else f"{n:,}"


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


def signup_url(prod):
    """상품명에 걸 링크. 요약서 PDF 가 아니라 보험사 사이트로 보낸다.

    간병보험은 대면·TM 채널이라 온라인 가입 페이지 자체가 없는 경우가 대부분이다.
    그래서 회사별로 상담신청 페이지를 아는 것은 sites.json 에 적고, 없으면 공시에 실린
    보험사 상품공시 페이지로 보낸다.
    """
    sites = {}
    f = ROOT / "sites.json"
    if f.exists():
        sites = {k: v for k, v in json.loads(f.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    return sites.get(prod["company"]) or prod.get("link") or prod.get("summary_url") or ""


def premium(prod, sex):
    try:
        return int(str(prod.get("premium_w" if sex == "여" else "premium_m")).replace(",", ""))
    except (TypeError, ValueError):
        return None


def est_premium(prod, person):
    base = premium(prod, person["sex"])
    return None if base is None else round(base * AGE_STEP ** ((person["age"] - 40) / 10))


def qualifies(prod, person):
    """나이도 되고 추정 보험료도 한도 안이어야 후보다. 보험료를 모르면 후보로 치지 않는다."""
    if not prod.get("age_max") or prod["age_max"] < person["age"]:
        return False
    e = est_premium(prod, person)
    return e is not None and e < MAX_PREMIUM


def eligible_marks(prod, fam):
    if prod.get("age_max") is None:
        return "?"
    return "".join(("○" if qualifies(prod, p) else "✕") for p in fam)


def cheapest(prod, fam):
    vals = [est_premium(prod, p) for p in fam if qualifies(prod, p)]
    return min(vals) if vals else 10 ** 9


def full_report(cur, fam):
    prods = cur["products"]
    keep = [x for x in prods if any(qualifies(x, p) for p in fam)]
    keep.sort(key=lambda x: cheapest(x, fam))

    L = [f"🏥 <b>간병·치매보험</b>  {esc(cur['scanned_at'][:10])}"]
    L.append(" · ".join(f"{esc(p['name'])} {'~' if p['approx'] else ''}{p['age']}세" for p in fam))
    L.append(f"추정 보험료 {MAX_PREMIUM // 10000}만원 미만 <b>{len(keep)}개</b>")

    for i, x in enumerate(keep, 1):
        L.append("")
        L.append(f"<b>{i}. {esc(x['company'])}</b>")
        L.append(f"<a href=\"{esc(signup_url(x))}\">{esc(x['name'][:52])}</a>")
        L.append("💰 " + " · ".join(
            f"{esc(p['name'])} <b>{won(est_premium(x, p))}</b>" if qualifies(x, p)
            else f"<s>{esc(p['name'])}</s>" for p in fam))
        tail = f"📅 {x.get('age_min')}~{x['age_max']}세 가입"
        if x.get("phone"):
            tail += f"   ☎ {esc(x['phone'])}"
        if x.get("summary_url"):
            tail += f"   <a href=\"{esc(x['summary_url'])}\">📄요약서</a>"
        L.append(tail)

    age_ok = [x for x in prods if any(x.get("age_max") and x["age_max"] >= p["age"] for p in fam)]
    kept = {id(x) for x in keep}
    over = [x for x in age_ok if id(x) not in kept and any(premium(x, p["sex"]) is not None for p in fam)]
    cheap_unknown = [x for x in prods if not x.get("age_max")
                     and any((premium(x, p["sex"]) or 10 ** 9) * 3 < MAX_PREMIUM for p in fam)]
    if cheap_unknown:
        L.append("\n❓ <b>싼데 가입나이를 못 읽음</b> — 전화로 확인할 값어치 있음")
        for x in cheap_unknown[:5]:
            L.append(f"· {esc(x['company'])} {esc(x['name'][:40])}"
                     + (f"  ☎ {esc(x['phone'])}" if x.get("phone") else ""))

    L.append(f"\n<i>숨김 — 비쌈 {len(over)} · 나이 미달 {len(prods) - len(age_ok)}</i>")
    L.append(f"<i>공시는 40세 예시보험료만 줍니다. 위 금액은 10년당 {AGE_STEP}배(요약서 실측)로 나이를 반영한 "
             f"추정이며, 실제 보험료는 전화 견적이라야 나옵니다.</i>")
    return "\n".join(L)


def diff_report(prev, cur, fam):
    L = [f"🏥 <b>간병·치매보험</b> · {esc(cur['scanned_at'])}"]
    L.append(f"보험나이 " + " · ".join(f"{esc(p['name'])} {p['age']}" for p in fam)
             + f" · 40세 예시보험료 {MAX_PREMIUM // 10000}만원 미만만")
    changes = 0
    for src in ("손보협회", "생보협회", "보험다모아"):
        ps, cs = prev["sources"].get(src, {}), cur["sources"].get(src, {})
        if not (ps.get("ok") and cs.get("ok")):
            continue
        # 한도를 넘는 상품은 새로 생겨도 알리지 않는다 — 볼 이유가 없다
        old = {x["id"]: x for x in prev["products"] if x["source"] == src and any(qualifies(x, p) for p in fam)}
        new = {x["id"]: x for x in cur["products"] if x["source"] == src and any(qualifies(x, p) for p in fam)}
        for i in new.keys() - old.keys():
            x = new[i]
            changes += 1
            age = f"{x['age_min']}~{x['age_max']}세" if x.get("age_max") else "가입나이 미확인"
            L.append(f"\n🆕 {eligible_marks(x, fam)} <b>{esc(x['company'])}</b> {esc(x['name'])}")
            L.append(f"   남 {won(x.get('premium_m'))} · 여 {won(x.get('premium_w'))} · {age}")
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
