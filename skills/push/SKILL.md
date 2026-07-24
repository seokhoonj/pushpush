---
name: push
description: "대화 중에 나온 결과·알림·파일을 Telegram·Slack·Discord로 보낸다. 자체 로직 없이 pushpush 패키지의 send()를 호출하며, 발송 전 반드시 대상(route)·내용·첨부를 사용자에게 확인받는다. Trigger phrases: 텔레그램으로 보내줘, 슬랙으로 보내, 디스코드로 보내, 알림 보내줘, 푸시 보내줘, push this, send to telegram/slack/discord."
---

# push — 대화 결과를 메신저로 보내기

메신저 발송은 **되돌릴 수 없는 외부 전송**이다. 잘못 보낸 메시지는 회수할 수 없고,
잘못된 채널에 간 파일은 그쪽에 남는다. 그래서 이 skill의 절반은 발송 자체가 아니라
발송 전 확인이다.

## 이 skill이 하지 않는 것

로직을 갖지 않는다. HTTP 요청 조립, capability 검사(파일 지원 여부·크기·서식), route
해석은 **전부 pushpush 패키지가 한다.** 여기서 그걸 다시 구현하거나, provider별 한도를
여기에 복사해두지 마라. 그러면 규칙이 두 집에 살게 되고 둘은 반드시 갈라진다. 이 skill은
`send(...)` 호출 하나를 조립해서 실행할 뿐이다.

## 패키지를 어디서 찾나

**경로를 하드코딩하지 마라.** 이 skill은 저장소 안에서 심링크된 것이므로, 저장소는 이
skill 폴더의 **두 단계 위**다. 세션에서 처음 필요할 때 한 번 찾고, 그 뒤로는 그 값을
재사용한다.

```sh
python3 -c "
import os, pathlib
skill = pathlib.Path(os.path.realpath(os.path.expanduser('~/.claude/skills/push')))
venv  = skill.parents[1] / '.venv' / ('Scripts' if os.name == 'nt' else 'bin') / 'python'
print(venv if venv.exists() else 'NOT FOUND')
"
```

`NOT FOUND`가 나오면 지어내지 말고 **사용자에게 저장소 위치를 묻는다.** 아래 예시들은
이렇게 찾은 경로를 `$PUSHPUSH_PY`로 적는다.

## 절차

### 1. 무엇을, 어디로 보낼지 정리한다

빠진 게 있으면 **추측하지 말고 묻는다.**

- `to` — 어느 route로 (생략하면 `default_route`)
- `text` — 메시지 본문
- `media` — 보낼 파일 경로 (선택)
- `caption` — 파일에 붙일 한 줄 (media가 있을 때만)
- `markup` — `"plain"`(기본)·`"markdown"`·`"html"`. html은 Telegram만.
- `silent` — 알림음 없이 보낼지

어떤 route가 있는지 모르면 먼저 읽는다:

```sh
$PUSHPUSH_PY -c "
from pushpush import load_config
config = load_config()
print('routes:', ', '.join(sorted(config.route_by_name)))
print('default:', config.default_route)
for name, route in config.route_by_name.items():
    print(f'  {name}: {route.provider.name}', route.destination or '')
"
```

### 2. 발송 전 확인받는다 — 건너뛰지 않는다

route가 어느 서비스·어디로 가는지 눈으로 확인할 수 있게 펼쳐서 보여준다:

```
보낼 내용을 확인해 주세요:

  route   alerts (telegram, chat 123456789)
  내용    반도체 수급 급락 -- 확인 필요
  첨부    chart.png (240 KB)
  서식    plain

보낼까요?
```

첨부가 없으면 `첨부    (없음)`이라고 명시한다.

승인 없이 보내지 않는다. 사용자가 "보내줘"라고 이미 말했더라도, route·내용·첨부가
확정된 형태로 한 번은 보여준다 — 사용자가 승인한 것은 "보낸다"는 행위이지 아직 이
구체적 내용이 아니다.

**본인 채널이 아닌 곳(팀 채널, 남의 봇)에는 절대 테스트 메시지를 보내지 않는다.** 동작
확인이 목적이면 본인 route로만 보낸다.

### 3. 보낸다

본문에 줄바꿈·따옴표·한글이 섞이므로 셸 인자로 넘기지 말고 파일로 쓴다.

```python
# <scratchpad>/push.py
from pushpush import send

receipt = send(
    "반도체 수급 급락 -- 확인 필요",
    to      = "alerts",
    media   = "/path/to/chart.png",   # 없으면 생략
    caption = "today",                # media가 있을 때만
)
print("provider:", receipt.provider)
print("message-id:", receipt.message_id)
```

```sh
$PUSHPUSH_PY <scratchpad>/push.py
```

### 4. 결과를 그대로 보고한다

`send`가 예외 없이 돌아왔으면 발송 완료다. `receipt.provider`와 `receipt.message_id`를
보고한다. 실패는 예외로 오지, 조용한 성공으로 뭉뚱그려지지 않는다.

## 예외가 났을 때

패키지 예외는 이미 사용자가 읽을 수 있는 문장이다. **`str(err)`를 그대로 전달한다.**
아래 표는 예외별로 덧붙일 행동만 적는다.

| 예외 | 덧붙일 행동 |
|---|---|
| `MissingSecretError` | **토큰·웹훅 URL을 대화에 붙여넣게 하지 마라.** 본인 터미널에서 `getpass`로 넣는 명령을 안내한다(README 3단계). |
| `InsecureCredentialsError` | 없음 — 예외에 `chmod 600` 명령이 들어 있다. |
| `MediaUnsupportedError` | 그 서비스는 파일을 못 나른다(지금은 Slack). Telegram·Discord로 보내거나 링크를 텍스트에 담을지 묻는다. |
| `MediaTooLargeError` | 파일을 줄이거나 다른 route로 보낼지 묻는다. |
| `MarkupUnsupportedError` | 그 서식을 그 서비스가 안 그린다. `markup="plain"`으로 다시 보낼지 묻는다. |
| `InvalidPushError` | 보낼 내용이 없거나, media 없는 caption, destination이 빠짐. 사용자에게 받아 다시 조립한다. |
| `SendFailedError` | 서비스가 거부했다(폐기된 토큰, 틀린 chat_id). 예외가 서비스의 사유를 담고 있다. |
| `UnknownRouteError` / `UnknownProviderError` | 설정에 없는 route·provider. 아래 "첫 설정"으로. |
| `urllib.error.URLError` | 네트워크 자체 실패. 잠시 뒤 재시도할지 묻는다. |

## 첫 설정

`ConfigError`가 나면 설정 파일이 없는 것이다. 형식은 저장소의 `README.md` 2·3단계에
있다.

**경로를 적어두지 마라.** 패키지가 `PUSHPUSH_CONFIG`·`PUSHPUSH_CREDENTIALS`·
`XDG_CONFIG_HOME`을 보고 정하므로, 패키지에 물어라:

```sh
$PUSHPUSH_PY -c "
from pushpush import default_config_path, default_credentials_path
print('config:     ', default_config_path())
print('credentials:', default_credentials_path())
"
```

**설정과 자격증명은 저장소 안에 만들지 않는다** — 위가 찍어주는 자리(둘 다 저장소 밖)에
만든다. **토큰·웹훅 URL을 대화나 스크립트에 평문으로 쓰지 마라.** 사용자에게 `getpass`를
쓰는 명령을 안내하고 본인 터미널에서 실행하게 한다.
