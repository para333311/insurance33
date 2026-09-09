"""policies.json 에서 금액이 안 채워진 본인부담 보험(유지 중인 것만)을 찾아 텔레그램으로 알린다.
빠진 게 없으면 조용히 넘어간다 — GitHub Actions 가 매주 돌려도 다 채워지면 더 이상 알림이 안 온다.

    python remind_missing_premium.py           빠진 게 있으면 텔레그램 전송
    python remind_missing_premium.py --print    화면에만
"""
import json
import sys
from pathlib import Path

from report import send

ROOT = Path(__file__).resolve().parent


def missing_items():
    data = json.loads((ROOT / "policies.json").read_text(encoding="utf-8"))
    out = []
    for person, info in data.items():
        if not isinstance(info, dict) or "policies" not in info:
            continue
        for p in info["policies"]:
            if (p.get("premium") is None
                    and p.get("status") == "유지(정상)"
                    and p.get("본인부담") is not False
                    and p.get("recurring") is not False):
                out.append((person, p))
    return out


def main():
    miss = missing_items()
    if not miss:
        print("빠진 금액 없음 — 알림 안 보냄")
        return
    lines = ["💸 <b>보험료 확인 필요</b> — 아직 금액이 안 채워졌습니다"]
    for person, p in miss:
        lines.append(f"· {person} — {p['company']} {p['product']}")
    text = "\n".join(lines)
    if "--print" in sys.argv:
        print(text)
    else:
        send(text)
        print(f"보냄 ({len(miss)}건)")


if __name__ == "__main__":
    main()
