# Qoo10 재팬 랭킹 자동 추적기 — 처음부터 다시 세팅하기

## 1단계. 기존 저장소 내용 지우기

가장 깔끔한 방법은 **저장소를 통째로 새로 만드는 것**이에요.

1. GitHub에서 기존 `Qoo10rank` 저장소로 들어가세요.
2. 위쪽 **Settings** 탭 → 맨 아래로 스크롤 → **Delete this repository** 눌러서 삭제하세요.
   (아니면 지우지 않고 기존 파일들을 하나씩 지워도 되지만, 새로 만드는 게 훨씬 간단해요.)
3. GitHub 메인 화면에서 **New repository**로 같은 이름(`Qoo10rank`)으로 다시 만드세요.
   - Private로 만들어도 괜찮아요.
   - README 자동 생성 체크는 꺼둔 채로 만들어도 상관없어요.

## 2단계. 이 폴더 안의 파일을 통째로 업로드하기

1. 방금 만든 새 저장소 페이지에서 **Add file → Upload files** 버튼을 누르세요.
2. 제가 드린 폴더(`qoo10-tracker-v2`) 안의 파일과 폴더를 **전부** 그 업로드 화면에 끌어다 놓으세요.
   - `.github/workflows/track.yml`까지 포함해서 폴더 구조가 그대로 유지되게 올라가야 해요
     (브라우저에 폴더째로 끌어다 놓으면 구조가 유지돼요).
3. 아래 **Commit changes** 버튼을 눌러서 업로드를 완료하세요.
4. 업로드가 끝나면 저장소 맨 위 화면(루트)에 `scrape.py`, `config.json`, `requirements.txt`,
   `run_scheduler.py`, `README.md`, 그리고 `.github` 폴더가 보여야 정상이에요.

## 3단계. 권한 켜기 (한 번만 하면 돼요)

1. 저장소 위쪽 **Settings** 탭 클릭
2. 왼쪽 메뉴에서 **Actions → General** 클릭
3. 아래쪽 **Workflow permissions**에서 **"Read and write permissions"** 선택
4. **Save** 버튼 꼭 누르기 (누르지 않으면 저장이 안 돼요)

## 4단계. 한 번 수동으로 실행해서 확인하기

1. 저장소 위쪽 **Actions** 탭 클릭
2. 왼쪽에서 **Qoo10 Rank Tracker** 클릭
3. 오른쪽 **Run workflow** 버튼 → 다시 **Run workflow** 눌러서 수동 실행
4. 1~2분 기다린 후, 실행 목록에서 방금 돈 항목을 클릭해서 초록색 체크가 뜨는지 확인
5. **Code** 탭으로 돌아가서 저장소 맨 위(루트) 화면을 보세요 —
   `rank_log.csv`, `screenshots` 폴더, `debug_latest.html`이 새로 생겼어야 해요.
   (`.github/workflows` 폴더 안이 아니라, `scrape.py`가 있는 바로 그 위치예요.)

## 5단계. 결과 확인하기

- `rank_log.csv`를 열어서 마지막 줄의 `status` 컬럼을 보세요.
  - `found`면 상품을 잘 찾은 거예요 — 정상 작동!
  - `not_found`면 상품을 못 찾은 거예요. 이럴 땐 같은 폴더의 `debug_latest.html`을
    다운로드해서 브라우저로 열어보면, 스크립트가 실제로 어떤 화면을 읽었는지 볼 수 있어요.
- `screenshots` 폴더에 이미지가 하나라도 생겼는지도 확인해보세요.

이 5단계를 따라 했는데도 여전히 아무 파일도 안 생기면, 4단계에서 본 **Actions 실행 로그
(초록/빨강 체크 눌러서 나오는 상세 화면)를 캡처**해서 보여주세요 — 로그에 정확한
에러 메시지가 찍혀 있어서 바로 원인을 알 수 있어요.

## 나중에 순위를 시각화해서 보려면

이전에 드린 `qoo10-tracker-dashboard.html` 파일을 열어서, `rank_log.csv`와
`screenshots` 폴더 안의 이미지들을 끌어다 놓으면 그래프와 표로 볼 수 있어요.
