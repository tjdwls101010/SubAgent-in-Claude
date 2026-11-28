# delegate 구현 기록 (2026-11-27~28)

계획 `261127_delegate 스킬; codex·claude 작업자를 한 스킬로.md`를 구현하면서 계획과 달라진 결정, 미룬 일, 운영에서 배운 사실을 남긴다. 각 결정의 근거는 해당 PR 본문과 커밋 메시지에 있다.

## 단계와 PR

| 계획 단계 | PR |
|---|---|
| ① 레지스트리·claude 드라이버 | #1 |
| ② codex 드라이버 | #2 |
| ③ 섞인 그룹·clean | #3, 머지 후 리뷰 수정 #18 |
| ④ send·attention | #4, 머지 후 리뷰 수정 #16 |
| ⑤ 모델·한도 | #5, 머지 후 리뷰 수정 #14·#15 |
| ⑥ Claude 작업자 예약 | #6, 리뷰 수정 #12 |
| ⑦ SKILL.md | #9, 동작 변경 뒤 다시 맞춤 #19 |
| ⑧ 예약 이행 잡 | #7, 리뷰 수정 #11·#13 |
| ⑨ 릴리스 | #10(v0.1.0), #20(v0.1.1) |
| 전환 1·2 (전역 문장, 설치) | Agentic-Coding #40 |
| 전환 3 (예약 이행) | 이행 잡 실행, 아래 "예약 이행" |
| 전환 6 (옛 claude 링크 삭제) | Agentic-Coding #41 |
| 그 밖 | #8 테스트 하네스의 작업자 표식, #17 병렬 테스트 실행기, #21 레포 이름 |

## 계획과 달라진 결정

성진의 결정:
- 한도로 끝난(`quota_exhausted`) 예약 실행은 어떤 자동 트리거도 다시 돌리지 않고, 요약과 doctor로 알리기만 한다(계획은 read-only만 한 번 재시도). 사람이 GitHub에서 시작한 Re-run은 돈다. 한도로 끝난 일회성은 워크플로가 꺼지므로 Re-run도 할 수 없다.
- 전역 CLAUDE.md의 delegate 항목에 "검증을 Claude 작업자로만 했다면 다른 모델 계열의 시각이 없다고 말한다"는 고지를 남긴다(계획 초안은 이 문장을 뺐다).
- Obsidian 예약: `매일_PORTFOLIO_매도조건점검`을 지웠고, `매일_MACRO_개장전점검`을 월~금 22:30 KST로 옮겼으며 "발표를 기다리며 sleep하지 않는다"를 더했다. `261128_SKH_외국인순매수`·`261128_SS_외국인순매수`를 지웠다. 그래서 이행 대상은 40개가 아니라 37개(반복 1, 일회성 36)가 됐다.
- 예약 이행 순서: 계획은 반복 예약 하나를 옮겨 기대 slot에서 완료하는 것을 본 뒤 나머지를 옮기는 것이었다. 실제로는 두 가지로 그 관찰을 대신했고, 같은 날(11/28) 모두 옮겼다.
  - Obsidian 카나리: 새 스킬의 일회성이 slot에서 완료되고 꺼지고 dispatcher가 지워지는 것을 봤다.
  - 옮긴 MACRO 워크플로의 `mode=check` 실행이 Obsidian runner에서 새 경로로 통과했다.
- 레포 이름: GitHub은 `Agentic-Delegate`로 바꿨고, 마켓 이름도 `agentic-delegate`가 됐다. 로컬 폴더 `~/Coding/subagent in claude`는 그대로 둔다.
- codex 리뷰 모델: astra medium 대신 sol medium을 쓴다. 비용 차이가 크고, 경합·롤백 경계를 보는 리뷰에 충분했다.

성진이 클로드에게 위임한 결정(SKILL.md, #9 본문):
- S6의 #1·#10이 기준선과 갈리는 것을 그대로 둔다. 계획이 "얼마나 위임할 가치가 있나"를 SKILL.md 밖에 둔다.
- 이름 붙인 엔진의 한도가 다해도 엔진 교체를 규칙으로 막지 않는다. 바뀌는 것을 사실로 주고, 고른 쪽과 이유를 한 줄로 말하게 한다.
- #2·#11이 `full` 대신 `write`로 나오는 것을 그대로 둔다.

클로드의 결정:
- `CLAUDE_SKILL_RUN` 표식은 남긴다. 계획은 옛 스킬 삭제 뒤 지우라고 했지만, 옛 스킬 원본(`~/Coding/claude in claude`)이 남아 있어 링크만 다시 걸면 되살아난다. 그때 작업자가 그 길로 우회하지 못하게 막는다.
- 리뷰 루프는 "평소 사용에서 닿는 지적이 없다"에서 멈췄다. 고치지 않고 둔 지적과 이유는 #11~#18의 PR 본문에 있다.

## 예약 이행 결과

- 이행 잡 `verify`: `ok: true`, 문제 0이었다.
- 이 맥에는 옛 `claude-schedule.*` dispatcher가 0개 남았고, `delegate-schedule.tjdwls101010-Obsidian.*`가 37개다.
- 옮긴 워크플로는 파일 이름·제목·cron·프롬프트·definition을 그대로 두고 `concurrency.group: claude-<name>`을 유지한다.
- journal은 Obsidian 본 체크아웃의 `.delegate-runs/.migration/`에 있다. 예약마다 rollback이 가능하다. 단, 옛 claude 스킬 링크를 다시 걸어야 옛 명령이 돈다.

## 성진이 챙길 것

- 2027-03-08 미국 서머타임 시작 전에 MACRO를 21:30 KST로 옮긴다. 서머타임 중에는 8:30 ET 발표가 21:30 KST, 개장이 22:30 KST다.
- Obsidian의 leftover worktree `.claude/worktrees/macro-45994995199-1-1`과 `refs/claude/leftovers/*` 6개는 손으로 정리한다. 이행 잡과 delegate는 건드리지 않는다.
- Obsidian PR #225(11/27 MACRO 1부)는 열린 채다. 다음 MACRO 실행이 열린 MACRO PR을 이어 쓰게 되어 있다.

## 운영에서 배운 사실

- codex Plus의 5시간 창은 작다. astra high 리뷰 여섯 개를 한꺼번에 띄우자 약 2분 만에 다 썼고, 결과 없이 7일 창만 17%p 썼다. astra medium 리뷰 하나는 5시간 창의 약 35%, sol medium은 약 7%를 쓴다. 리뷰는 한 번에 하나씩, 보통은 sol로 한다.
- 예약 실행은 성진의 대화 세션과 같은 Claude 한도 풀을 쓴다. 예약 시각 직전의 5시간 창에 무거운 작업자를 몰아 띄우면, 11/26 PORTFOLIO처럼 그 실행이 한도로 끝난다.
- 예약 작업자가 실행 안에서 나중 사건(발표)을 sleep으로 기다리면 타임아웃을 그 기다림에 쓴다. 11/25·11/27 MACRO가 22:30 정각에 잘린 원인이다. SKILL.md에 사실로 적었다.
- Claude 작업자는 Bash를 돌린 디렉터리 아래에 빈 `.claude/.cc-writes`를 남긴다(Claude Code 샌드박스). 스킬 폴더 안에 생기면 `test_structure`가 실패한다. 리뷰어를 레포 안에서 돌린 뒤에는 지운다.
- 순차 스위트는 약 19분, `tests/parallel.py`는 약 5분이다. 작업자에게는 지적마다 그 테스트만 돌리고, 전체 순차 스위트는 마지막에 한 번 돌리게 한다.
- 예약 스모크용 private 레포는 `tjdwls101010/delegate-schedule-smoke`이고, runner는 `~/actions-runner-delegate-smoke`에 있다. Obsidian runner처럼 키체인의 로그인을 읽으며, plist에서 `SessionCreate`를 지웠다.
