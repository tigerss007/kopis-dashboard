"""사이트 구성: 어떤 연도의 대시보드 페이지를 만드는지.

새 연도(예: 2026)를 추가하는 순서:
  1) 아래 ALL_YEARS 맨 앞에 연도를 넣는다.
  2) python web/build_site_data.py 2026        — 그 해 대시보드(web/2026/index.html)와 요약을 만든다.
  3) 시장 전체 수치(보고서)가 나오면 python web/extract_market_report.py <2026년 보고서.pdf>
  4) python web/build_main.py                   — 메인 페이지(web/index.html)를 다시 만든다.
  5) 배포(GitHub Pages는 docs/ 를 서비스한다): web/index.html -> docs/index.html,
     web/<연도>/index.html -> docs/<연도>/index.html 을 복사해 커밋·푸시한다.
메인 페이지는 이 목록과 market_data.json의 연도에서 연도 열을 자동으로 만든다.
(진행 중인 해는 보고서 전까지 시장 통계가 없어 메인의 시장 차트에는 안 나오고 대시보드 카드에만 나온다.)
"""

ALL_YEARS = [2025, 2024, 2023]  # 최신 연도가 앞
