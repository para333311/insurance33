"""연 1회 보험료 재확인 알림 — 자동차보험(2월 갱신)·실손보험 갱신 시기 지난 뒤 텔레그램으로 알린다."""
from report import send

TEXT = (
    "📅 <b>보험료 연 1회 점검</b>\n"
    "자동차보험(2월 갱신)·실손보험 갱신 보험료가 바뀌었는지 확인해서 "
    "policies.json / SUMMARY.md 업데이트해 주세요."
)

if __name__ == "__main__":
    send(TEXT)
    print("보냄")
