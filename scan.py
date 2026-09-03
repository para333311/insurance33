"""4개 공시 소스에서 간병·치매 상품과 가입나이를 읽어 data/latest.json 에 쓴다.

    python scan.py            전부 읽고 latest.json 갱신 (직전 것은 prev.json 으로)
    python scan.py --dry      파일에 쓰지 않고 요약만 출력

소스마다 따로 실패한다 — 하나가 죽어도 나머지는 산다. 죽은 소스는 sources[이름].ok=False 로 남긴다.
"""
import html
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = ROOT / "cache" / "pdf"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"

_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))


def fetch(url, data=None, headers=None, timeout=60):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=h)
    for attempt in range(3):
        try:
            return _opener.open(req, timeout=timeout).read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


AGE_RANGE = re.compile(
    r"(\d{1,3})\s*세\s*[~∼～\-]\s*(?:최대|최고|만)?\s*(\d{1,3})\s*세(?!\s*만기)"
    r"|(\d{1,3})\s*[~∼～]\s*(?:최대|최고|만)?\s*(\d{1,3})\s*세(?!\s*만기)"
)


# '20세~ min(세만기-납입기간, 85세)' 꼴. 상한이 괄호 안 계산식에 숨어 있어 위 정규식이 못 잡는다.
# 농협손보 상품이 전부 이 서식이라 이걸 놓치면 제일 싼 후보가 통째로 '가입나이 미확인' 이 된다.
AGE_MIN_FN = re.compile(r"(\d{1,3})\s*세\s*[~∼～]\s*[Mm]in\s*\(.{0,60}?,\s*(\d{1,3})\s*세", re.S)


def _pairs(text):
    """갱신 나이 구간('갱신 … 22~100세')은 새로 드는 나이가 아니라 뺀다."""
    for m in AGE_RANGE.finditer(text):
        if "갱신" in text[max(0, m.start() - 20): m.start()]:
            continue
        a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        yield int(a), int(b), m
    for m in AGE_MIN_FN.finditer(text):
        yield int(m.group(1)), int(m.group(2)), m


def age_ranges(text):
    return [(a, b) for a, b, _ in _pairs(text) if 0 < a < b <= 110]


AGE_HEAD = re.compile(r"가입\s*(?:가능\s*)?(?:나이|연령)")


def age_ranges_near(text, window=500):
    """'가입나이' 문구 뒤 window 자 안의 구간만. 갱신나이·보험기간 표를 가입나이로 오독하지 않으려고."""
    flat = re.sub(r"\s+", " ", text)
    out = []
    for m in AGE_HEAD.finditer(flat):
        out += age_ranges(flat[m.end(): m.end() + window])
    return out


def kind_of(name):
    care = "간병" in name or "요양" in name
    dem = "치매" in name
    if care and dem:
        return "간병·치매"
    if care:
        return "간병"
    if dem:
        return "치매"
    return "기타"


def pdftotext_bin():
    return shutil.which("pdftotext") or r"C:\Program Files\Git\mingw64\bin\pdftotext.exe"


def pdf_text(seq):
    CACHE.mkdir(parents=True, exist_ok=True)
    pdf = CACHE / f"{seq}.pdf"
    txt = CACHE / f"{seq}.txt"
    if txt.exists():
        return txt.read_text(encoding="utf-8", errors="replace")
    if not pdf.exists():
        pdf.write_bytes(fetch(f"https://kpub.knia.or.kr/file/download/{seq}.do", timeout=120))
    subprocess.run([pdftotext_bin(), "-enc", "UTF-8", str(pdf), str(txt)], capture_output=True, check=False)
    return txt.read_text(encoding="utf-8", errors="replace") if txt.exists() else ""


def evidence_for(text, age_max):
    """가입나이 문구 근처의 문장을 근거로 돌려준다. 없으면 최대 나이가 나온 자리 앞뒤."""
    flat = re.sub(r"\s+", " ", text)
    best = None
    for _, b, m in _pairs(flat):
        if b != age_max:
            continue
        before = flat[max(0, m.start() - 120): m.start()]
        k = before.rfind("가입")
        start = m.start() - (len(before) - k) if k >= 0 else max(0, m.start() - 25)
        cand = flat[start: m.end() + 15].strip()
        if best is None or k >= 0:
            best = cand
            if k >= 0:
                break
    return best or ""


def parse_js_objects(text):
    """kpub 이 돌려주는 {list:[{K:'v',K2:1,...},...]} 꼴을 파싱한다. JSON 이 아니라 JS 리터럴이다."""
    rows = []
    for o in re.findall(r"\{([^{}]*)\}", text):
        d = {}
        for m in re.finditer(r"([A-Z_0-9]+):(?:'((?:[^'\\]|\\.)*)'|([^,]*))", o):
            v = m.group(2) if m.group(2) is not None else m.group(3)
            d[m.group(1)] = v.replace("\\'", "'") if isinstance(v, str) else v
        if "TP_NAME" in d:
            rows.append(d)
    return rows


# ── 소스 1: 손보협회 간병·치매 (PB16) ─────────────────────────────────────────
def kpub_list(tpty):
    raw = fetch(
        "https://kpub.knia.or.kr/popup/disclosureList.do",
        data={"tabType": "1", "tptyCode": tpty, "pageIndex": "1", "pageSize": "20", "refreshYn": "", "detailYn": ""},
        headers={"X-Requested-With": "XMLHttpRequest",
                 "Referer": f"https://kpub.knia.or.kr/popup/disclosurePopup.do?tabType=1&tptyCode={tpty}"},
        timeout=120,
    ).decode("utf-8", "replace")
    rows = parse_js_objects(raw)
    if not rows:
        raise RuntimeError(f"목록이 비어 있음 ({len(raw)} bytes)")
    return rows


_elderly_error = ""


def src_kpub():
    """간병·치매 탭(PB16) + 고령자(60세↑) 탭의 간병·치매 상품. 고령자 탭 상품은 PB16 목록에 없다(2026-09-03 확인)."""
    # 고령자(60세↑) 탭은 2026-08 신설 기능이라 같은 요청도 때에 따라 8개~297개로 들쭉날쭉하다
    # (2026-09-03 확인). 못 읽은 것과 구분이 안 되므로 에러로 잡지 않고 있는 그대로 합친다 —
    # 놓친 상품은 다음 실행에서 다시 잡히고, 어차피 diff_report 는 elderly_care 의 "사라짐"은
    # 알리지 않는다(새로 등장만 알린다).
    global _elderly_error
    rows = kpub_list("PB16")
    elderly = set()
    erows = []
    try:
        erows = [r for r in kpub_list("ELDERLY") if r.get("TPTY_CODE") == "PB16" or kind_of(r.get("TP_NAME", "")) != "기타"]
        elderly = {(r["P_CODE"], r["TP_NAME"]) for r in erows}
        _elderly_error = ""
    except Exception as e:
        _elderly_error = str(e)[:200]
    seen = {}
    for r in rows + erows:
        key = (r["P_CODE"], r["TP_NAME"])
        if key not in seen:
            seen[key] = r
    out = []
    for (pcode, name), r in seen.items():
        seq = str(r.get("SUMMARY_SEQ", ""))
        text = pdf_text(seq) if seq else ""
        ranges = age_ranges_near(text)
        method = "가입나이 문맥"
        if not ranges:
            ranges = age_ranges(text)
            method = "전체 추정"
        amax = max((b for _, b in ranges), default=None)
        amin = min((a for a, _ in ranges), default=None)
        out.append({
            "age_method": method if ranges else "",
            "age_first": ranges[0][1] if ranges else None,
            "id": f"kpub:{pcode}:{name}",
            "source": "손보협회",
            "company": r.get("P_CODE_NM", ""),
            "name": name,
            "kind": kind_of(name),
            "age_min": amin,
            "age_max": amax,
            "evidence": evidence_for(text, amax) if amax else "",
            "premium_m": r.get("TP_M_BILL"),
            "premium_w": r.get("TP_W_BILL"),
            "premium_note": (r.get("TP_ETC") or "")[:60],
            "channel": r.get("TP_NEW_CHANNEL") or r.get("TP_CHANNEL") or "",
            "phone": r.get("ETC5", ""),
            "link": r.get("TP_URL", ""),
            "summary_url": f"https://kpub.knia.or.kr/file/download/{seq}.do" if seq else "",
            "summary_seq": seq,
            "elderly": (pcode, name) in elderly,
        })
    return out


# ── 소스 3: 생보협회 간병/치매보험 ────────────────────────────────────────────
LIA_LIST = "https://pub.insure.or.kr/compareDis/prodCompare/assurance/listNew.do?search_prodGroup=024400010010"
LIA_REMARK = "https://pub.insure.or.kr/compareDis/prodCompare/assurance/remarkViewPopup.do?prodCd={pc}&search_prodGroup=024400010010"
PREM_CELL = re.compile(r"<td([^>]*)><!--\s*보험료:(남자|여자)\s*-->\s*([\d,]+)")


def product_segment(page, pc):
    i = page.find(f"l_prodNm_{pc}")
    if i < 0:
        return ""
    j = page.find("l_prodNm_", i + 10)
    return page[i: j if j > 0 else i + 20000]


def sum_premium(seg, who):
    """주계약(mainTerm) + 공시된 특약을 더한 값과 주계약만의 값.

    생보는 주계약이 3,300원인데 정작 간병 보장은 특약에 있는 상품이 흔하다(ABL 등).
    주계약만 보면 손보 예시보험료(기본계약+주요특약)와 견줄 수 없어 합계로 비교한다.
    """
    main, total = None, 0
    for attrs, w, val in PREM_CELL.findall(seg):
        if w != who:
            continue
        n = int(val.replace(",", ""))
        total += n
        if "mainTerm" in attrs and main is None:
            main = n
    return (str(total) if total else None), (str(main) if main is not None else None)


def src_lia():
    page = fetch(LIA_LIST, timeout=120).decode("utf-8", "replace")
    codes = list(dict.fromkeys(re.findall(r"fn_remarkViewPopupOpen\('([^']+)'\)", page)))
    if not codes:
        raise RuntimeError("상품 코드를 못 찾음 — 페이지 구조가 바뀌었나")
    out = []
    for pc in codes:
        nm = re.search(r'id="l_prodNm_' + re.escape(pc) + r'"[^>]*>([^<]*)', page)
        co = re.search(r'id="l_memberNm_' + re.escape(pc) + r'"[^>]*>([^<]*)', page)
        name = strip_tags(nm.group(1)) if nm else pc
        company = strip_tags(co.group(1)) if co else pc[:3]
        seg = product_segment(page, pc)
        prem_m, main_m = sum_premium(seg, "남자")
        prem_w, main_w = sum_premium(seg, "여자")
        chm = re.search(r"(대면채널|온라인채널|CM채널|TM채널|방카채널|통신판매)", seg)
        channel = chm.group(1).replace("채널", "") if chm else ""
        remark = strip_tags(fetch(LIA_REMARK.format(pc=pc)).decode("utf-8", "replace"))
        m = re.search(r"가입\s*나이\s*:?\s*(.{0,140})", remark)
        text = m.group(1) if m else ""
        ranges = age_ranges(text)
        amax = max((b for _, b in ranges), default=None)
        amin = min((a for a, _ in ranges), default=None)
        out.append({
            "id": f"lia:{pc[:3]}:{name}",
            "source": "생보협회",
            "company": company,
            "name": name,
            "kind": kind_of(name),
            "age_min": amin,
            "age_max": amax,
            "evidence": ("가입나이 " + text[:80]).strip() if text else "",
            "premium_m": prem_m,
            "premium_w": prem_w,
            "premium_main_m": main_m,
            "premium_main_w": main_w,
            "premium_note": "주계약+특약 40세 예시",
            "channel": channel,
            "phone": "",
            "link": LIA_LIST,
            "summary_url": LIA_REMARK.format(pc=pc),
            "summary_seq": "",
        })
    return out


# ── 소스 4: 보험다모아 간병/치매 (C012) — 새 상품 감시용 ───────────────────────
DAMOA = "https://www.e-insmarket.or.kr/guaranteeIns/guaranteeInsList.knia?menuId=C012"


def src_damoa():
    page = fetch(DAMOA, timeout=120).decode("utf-8", "replace")
    i = page.find("상품비교")
    seg = page[i:] if i >= 0 else page
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        cells = [strip_tags(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        cells = [c for c in cells if c]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        name = re.sub(r"\s*상세보기.*$", "", cells[1]).strip()
        age = next((c for c in cells if re.fullmatch(r"\d{1,3}\s*~\s*\d{1,3}", c)), "")
        am = re.fullmatch(r"(\d{1,3})\s*~\s*(\d{1,3})", age)
        out.append({
            "id": f"damoa:{name}",
            "source": "보험다모아",
            "company": name.split()[0] if name else "",
            "name": name,
            "kind": kind_of(name),
            "age_min": int(am.group(1)) if am else None,
            "age_max": int(am.group(2)) if am else None,
            "evidence": f"가입연령 {age}" if age else "",
            "premium_m": None,
            "premium_w": None,
            "premium_note": "",
            "channel": "온라인",
            "phone": "",
            "link": DAMOA,
            "summary_url": "",
            "summary_seq": "",
        })
    if not out:
        raise RuntimeError("상품 행을 못 찾음 — 페이지 구조가 바뀌었나")
    return out


def run():
    result = {"scanned_at": time.strftime("%Y-%m-%d %H:%M"), "sources": {}, "products": [], "elderly_care": []}
    for label, fn in (("손보협회", src_kpub), ("생보협회", src_lia), ("보험다모아", src_damoa)):
        try:
            items = fn()
            result["products"].extend(items)
            result["sources"][label] = {"ok": True, "count": len(items)}
            print(f"[ok] {label} {len(items)}개")
        except Exception as e:
            result["sources"][label] = {"ok": False, "error": str(e)[:200]}
            print(f"[장애] {label}: {e}")
    care = [{"company": x["company"], "name": x["name"]} for x in result["products"] if x.get("elderly")]
    result["elderly_care"] = care
    if _elderly_error:
        result["sources"]["고령자탭"] = {"ok": False, "error": _elderly_error}
        print(f"[장애] 고령자탭: {_elderly_error}")
    else:
        result["sources"]["고령자탭"] = {"ok": True, "count": len(care)}
        print(f"[ok] 고령자탭 간병·치매 {len(care)}개")
    return result


if __name__ == "__main__":
    res = run()
    if "--dry" in sys.argv:
        print(json.dumps({k: v for k, v in res.items() if k != "products"}, ensure_ascii=False, indent=1))
        sys.exit(0)
    DATA.mkdir(exist_ok=True)
    latest = DATA / "latest.json"
    if latest.exists():
        shutil.copyfile(latest, DATA / "prev.json")
    latest.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {latest} ({len(res['products'])}개)")
