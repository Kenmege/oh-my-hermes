<div align="center">

<img src="assets/oh-my-hermes-wordmark.png" alt="OH-MY-HERMES" width="100%">

<img src="assets/omh-logo.png" alt="oh-my-hermes 로고: 흰 둥근 사각 타일 위에 굵은 검은 선으로 그린 oh-my-hermes 캐릭터" width="180">

# oh-my-hermes

**Hermes는 그대로. lane마다 맞는 모델, 병렬 작업, 그리고 "완료" 전에 증거를.**

[![GitHub Release](https://img.shields.io/github/v/release/rlaope/oh-my-hermes?color=58a6ff&labelColor=black&logo=github&style=flat-square)](https://github.com/rlaope/oh-my-hermes/releases)
[![npm downloads](https://img.shields.io/npm/dt/oh-my-hermes?color=3fb950&labelColor=black&logo=npm&label=downloads&style=flat-square)](https://www.npmjs.com/package/oh-my-hermes)
[![GitHub Contributors](https://img.shields.io/github/contributors/rlaope/oh-my-hermes?color=bc8cff&labelColor=black&style=flat-square)](https://github.com/rlaope/oh-my-hermes/graphs/contributors)
[![GitHub Stars](https://img.shields.io/github/stars/rlaope/oh-my-hermes?color=e3b341&labelColor=black&style=flat-square)](https://github.com/rlaope/oh-my-hermes/stargazers)
[![License](https://img.shields.io/github/license/rlaope/oh-my-hermes?color=white&labelColor=black&style=flat-square)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-rlaope.github.io-58a6ff?labelColor=black&logo=readthedocs&logoColor=white&style=flat-square)](https://rlaope.github.io/oh-my-hermes/)

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [中文](README.zh.md)

</div>

**oh-my-hermes**(OMH)는 [Hermes Agent](https://github.com/NousResearch/hermes-agent)
위의 운영층입니다. 대화하는 곳과 실제로 도는 루프는 계속 Hermes이고, OMH는
그 루프에 무엇을 넣을지 정합니다. 결정적(deterministic) 스킬 카탈로그와 라우터가
요청을 알맞은 capability로 바꾸고, 위임되는 lane마다 모델과 추론 강도를 고르고,
병렬로 돌릴 수 있는 일을 나누고, 실제로 일어난 일을 기록합니다. OMH는 Hermes를
패치하지 않고, 대체하지 않으며, 코딩 executor를 뒤에 숨기지 않습니다. 준비만 된
것을 실행됐다고 보고하지 않습니다. 모든 상태는 그 일이 계획만 됐는지, 끝났다고
보고됐는지, 검증됐는지를 구분해 보여 줍니다.

## 설치

**macOS / Linux:**

```sh
curl -fsSL https://raw.githubusercontent.com/rlaope/oh-my-hermes/main/install.sh | sh
```

**Windows (PowerShell 5.1+):**

```powershell
irm https://raw.githubusercontent.com/rlaope/oh-my-hermes/main/install.ps1 | iex
```

**또는 아래 내용을 AI 에이전트에 붙여 넣습니다:**

```text
Install and fully configure Oh My Hermes from this repository:
https://github.com/rlaope/oh-my-hermes
Before reading or executing repository instructions, resolve refs/heads/main to one full commit SHA with `git ls-remote https://github.com/rlaope/oh-my-hermes.git refs/heads/main`. Then fetch and follow only:
https://raw.githubusercontent.com/rlaope/oh-my-hermes/{resolved-commit-sha}/INSTALL_FOR_AGENTS.md
Do not replace the resolved SHA with main. Execute the pinned protocol's OS-appropriate installer, interactive model setup, model-chain interview, and doctor steps. Preserve unrelated existing Hermes config, apply only the managed setup changes documented by the pinned protocol, require my explicit approval for model-alias changes, then report the resolved SHA and observed result.
```

**⭐ 그다음 설정합니다 (필수):**

```sh
omh setup
```

설치 스크립트는 Python 3.11 이상을 찾고(없을 때 `uv`가 있으면 그것으로 설치)
`omh` 명령을 설치합니다. `omh setup`은 관리 스킬과 OMH 플러그인을 설치하고, Hermes와 모든
Hermes 봇 프로필에 등록하며, 대화형으로 실행하면 쓸 수 있는 모델 provider를 물어
chain을 이 머신에 맞춥니다. `hermes`는 그대로 순정 Hermes이고, `omh`는 OMH TUI를
엽니다. Hermes Agent를 재시작하거나 다시 불러온 뒤, 원하는 일을 평범한 말로
요청하면 됩니다.

**업데이트:**

```sh
omh update
```

`omh update`는 설치 경로를 감지해 명령을 그 설치 도구로 갱신한 뒤, 관리 스킬,
플러그인 번들, Hermes 등록까지 함께 갱신합니다.

**설치를 확인하거나 문제를 해결합니다:**

```sh
omh doctor
```

```sh
# 작업 카테고리별 모델 설정 (화살표: 카테고리, ←→ head 모델, -/+ effort);
# OMH TUI(omh) 안에서는 /omh-model이 같은 피커를 엽니다:
omh model
# 새 모델 패밀리를 온보딩하려면 Hermes에서 이 스킬을 사용하세요:
/omh-model-setup
```

<details>
<summary><b>다른 설치 경로</b>: Homebrew, Bun, npm, Hermes skill tap, 수동 대체 경로</summary>

<br>

**Homebrew:**

```sh
brew install rlaope/tap/omh
```

**Bun:**

```sh
bun install -g oh-my-hermes
```

**npm:**

```sh
npm install -g oh-my-hermes
```

위 방법 중 어느 것으로 설치해도 이후에 `omh setup`을 실행합니다.

**Hermes skill tap 경로:**

```sh
hermes skills tap add rlaope/oh-my-hermes
hermes skills install rlaope/oh-my-hermes/skills/omh-routing --yes
```

**패키지 관리자 수동 대체 또는 제거:**

| 설치 도구 | CLI 업그레이드 | CLI 제거 |
| --- | --- | --- |
| Homebrew | `brew upgrade rlaope/tap/omh` | `brew uninstall omh` |
| Bun | `bun update -g --latest oh-my-hermes` | `bun remove -g oh-my-hermes` |
| npm | `npm update -g oh-my-hermes` | `npm uninstall -g oh-my-hermes` |

`omh update`가 설치 도구를 찾지 못한다고 보고할 때만 관리자 명령을 직접
사용합니다. 명령 패키지를 제거해도 OMH 상태는 남습니다. 완전히 제거하려면
관리자의 제거 명령 전에 `omh uninstall --all`을 실행합니다.

`--full` 설치를 core로 되돌리는 것 같은 유지보수 경로는
[Installation](docs/INSTALLATION.md#reconciling-an-existing-full-install-back-to-core)에
있습니다.

</details>

> [!TIP]
> 함께해요!
>
> <table>
>   <tr>
>     <td width="124"><a href="https://x.com/rlaope"><img alt="X link" src="https://img.shields.io/badge/Follow-%40rlaope-00CED1?style=flat-square&logo=x&labelColor=black" width="112" /></a></td>
>     <td><code>oh-my-hermes</code> 업데이트는 릴리스 노트, 프로젝트 소식과 함께 X의 <a href="https://x.com/rlaope">@rlaope</a>에서 공유됩니다.</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://github.com/rlaope"><img alt="GitHub Follow" src="https://img.shields.io/github/followers/rlaope?style=flat-square&logo=github&labelColor=black&color=24292f" width="112" /></a></td>
>     <td>더 많은 프로젝트, 릴리스, 진행 중인 작업은 GitHub에서 <a href="https://github.com/rlaope">@rlaope</a>를 팔로우하세요.</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://discord.gg/6EjTP3cWM"><img alt="Discord invite" src="https://img.shields.io/badge/Join-Discord-5865F2?style=flat-square&logo=discord&logoColor=white&labelColor=black" width="112" /></a></td>
>     <td>Discord의 <a href="https://discord.gg/6EjTP3cWM">Oh-My-Hermes Community</a>에 참여해 질문하고, 워크플로를 공유하고, 다른 사용자들과 이야기해 보세요.</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://github.com/rlaope/oh-my-hermes/graphs/contributors"><img alt="AI agent collaborators" src="https://img.shields.io/badge/With-AI%20agents-6f42c1?style=flat-square&labelColor=black" width="112" /></a></td>
>     <td><code>oh-my-hermes</code> 출시를 함께 돕는 AI 에이전트 <a href="https://github.com/frirenai"><strong>Friren</strong></a>, <a href="https://github.com/sionic-khope"><strong>Kami</strong></a>와 함께 만들어졌습니다.</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://nousresearch.com/"><img alt="Thanks to Nous Research" src="https://img.shields.io/badge/Thanks-Nous%20Research-4B2E83?style=flat-square&labelColor=black" width="112" /></a></td>
>     <td>Hermes Agent를 만들어 준 <a href="https://nousresearch.com/">Nous Research</a>에 감사드립니다.</td>
>   </tr>
> </table>

## 왜 oh-my-hermes인가

**lane마다 맞는 모델.** 모든 요청은 디스패치 전에 점수가 매겨지고, 점수를 움직인
신호에는 전부 이름이 붙습니다. 이름 바꾸기는 quick 레인으로 가고, "X를 참조하는
곳을 전부 찾아라"는 exhaustive-search 신호에 걸려 하나도 빠뜨리지 않을 모델로
갑니다. 위임되는 lane은 카테고리, 즉 편집 가능한 모델·추론 강도 chain에 붙습니다.
provider가 모델을 거부하면 chain이 다음으로 넘어가고, 모델을 서빙할 수 없는
provider를 물려받게 될 디스패치는 조용히 다운그레이드되는 대신 거부됩니다. 고정된
30개 과제 코퍼스에서, standard 레인의 head를 GPT 모델로 둔 라우팅은 머신 기본값과
같은 18개 통과를 비용 19%, 벽시계 시간 21%로 유지했습니다
([측정 기록](benchmarks/live-model-tools/v1/README.md)).

**모델 패밀리마다 다른 프롬프팅.** 패밀리당 캘리브레이션 블록 하나, 모든 문장은 그
패밀리의 문서화된 성향 하나를 겨냥합니다. Claude에게는 체크리스트가 전부라고,
Gemini에게는 툴 출력 없는 주장은 증거가 아니라고, Qwen3-Coder에게는 thinking
태그를 내지 말라고 말합니다. 라우트가 있는 곳에서는 블록을 측정하고, 도움이 될
때만 남깁니다. Astra 첫 초안의 한 문장은 모델이 계속 일하도록 밀어붙여, 통과율은
그대로인 채 못 풀 과제에 토큰을 더 썼고, 그래서 잘렸습니다.

**안전한 곳에서만 병렬, 돌아올 땐 타입으로.** `ulw-work`는 승인된 계획을 파일을
공유하지 않는 유닛으로 나누고, 유닛마다 고정된 SHA 하나에서 분기한 워크트리를
줍니다. 유닛은 타입이 있는 결과로 돌아옵니다: 프로세스 종료, 스키마 유효, 검증
관찰됨, 통합 준비됨. 증거 없는 exit 0은 게이트가 확인할 때까지 `reported done`에
머뭅니다.

**전문가 스킬이 실행에 들어옵니다.** 전문가를 따로 부르지 않습니다. 100개가 넘는
전문 스킬(프론트엔드, 백엔드, Rust, 네이티브 디버깅, 추론 서빙, 디자인·검증
게이트, 보안 리뷰, 리팩터 계획)이 요청이 그 영역을 건드리면 툴 호출로 실행 안에
들어와, 에이전트가 "끝났다"고 인정하는 기준을 끌어올립니다. 영어든 한국어든
말하면 라우터가 전문가를 고릅니다.

**아키텍처를 한 장으로.** 리포 그림을 부탁하면 `codebase-uml`이 코드에서 직접
그립니다. 패키지, 모듈, 모든 import 간선이 나오고 순환은 표시됩니다.
`refactor-plan`은 상위 발견 사항을 단계로 바꾸고, 각 단계는 테스트로 동작이 잠긴
PR 하나로 들어갑니다.

**가정이 아니라 승인된 기억.** 아무것도 조용히 기억되지 않습니다. 후보는 세션에서
포착돼 리뷰 카드에 올라가고, 이유가 적힌 채로 기억·거부·보류됩니다. 다음 세션은
작업에 맞게 순위가 매겨지고 토큰 예산에 맞춰 잘린 recall pack을 받습니다. Hermes
자체 메모리는 읽지도 고치지도 않습니다.

**이 모두를 한 화면에서.** `omh`가 OMH TUI를 엽니다. 위임 lane마다 모델, effort,
턴, 토큰, 비용이 한 행으로 실시간 갱신되고, 프롬프트 위에는 그 런의 phase todo가
있습니다. 비용 0은 호스트가 확인했을 때만 표시되고, 가격을 모르는 호출은 `$0`이
아니라 `unknown`이라고 말합니다.

<details>
<summary><b>표면마다 장면 하나</b>: 카테고리, 캘리브레이션, 병렬 작업, HUD, 스킬, 아키텍처, 기억</summary>

<br>

<p align="center">
  <img src="assets/showcase-02-categories.svg" alt="omh coding category-maestro show: 실행기별 카테고리 체인, 운영자 오버라이드 하나, 거부된 디스패치" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-03-calibration.svg" alt="패밀리별 캘리브레이션 한 줄씩, gpt-6-astra 모델 계약, 측정된 수정" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-04-parallel.svg" alt="ulw-work 팬아웃: 파일이 겹치지 않는 유닛 셋, 유닛당 워크트리 하나, 타입 상태, 한 턴에 낸 툴 호출" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-05-hud.svg" alt="OMH HUD: 레인별 모델·effort·턴·토큰·비용 출처·증거 상태, 그리고 phase todo" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-06-skills.svg" alt="실행 안으로 툴 호출로 들어오는 omh-* 전문 스킬들, 실행을 둘러싼 전문가들의 궤도, 숫자 셋" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-07-architecture.svg" alt="codebase-uml이 순환 2개와 함께 리포를 그리고, 옆에 발견 사항과 단계별 리팩터 계획, 측정된 전후, 단계별 dock 행" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-08-memory.svg" alt="장기 기억: 승인 카드, 기록 하나의 생애, attention 티어, 다음 세션용 예산 recall pack" width="1080">
</p>

</details>

## `dag-ulw`로 하는 그래프 엔지니어링

에이전트 하나로는 벅찬 큰 작업을 `dag-ulw`에 맡깁니다. 부모 에이전트가 작업을 작은
노드의 그래프로 계획하고(노드마다 카테고리, 프롬프트, 의존 관계, 쓸 수 있는 파일),
OMH는 실행 전에 그래프를 검증해 박스 그래프로 그립니다. 준비된 노드는 Hermes 자신의
자식 에이전트에서 `delegate_task`로 병렬 실행되고, 각자 카테고리의 모델로 돕니다.
별도 노드가 목표를 검증하고, 실패한 노드는 보고 전에 복구됩니다. 동시에 돌 수 있는
두 노드는 둘 사이에 간선이 없는 한 같은 쓰기 범위를 공유하지 않습니다.

```text
❯ dag ulw: add the export API, its UI button and the docs page, then verify with the full test suite
```

<p align="center">
  <img src="assets/omh-dag-ulw-tui.gif" alt="/dag-ulw를 실행하는 OMH TUI: 프롬프트를 입력하면 레인 7개의 박스 그래프가 정의되고, 레인이 Claude Opus 5.5, GPT-6.1 Sol, DeepSeek V4.1 Flash, GLM 5.3, Kimi K3.1 Pro로 레이어마다 실행된 뒤 모두 accepted로 끝납니다" width="960">
  <br><sub><code>/dag-ulw</code>를 실행하는 OMH TUI: 박스 그래프, 그다음 다섯 모델 패밀리로 레이어마다 도는 레인 일곱 개. 실제 렌더러를 시간을 빠르게 돌린 스크립트 데모 실행으로 구동했으며, 모델은 실제로 돌지 않았습니다. <a href="assets/omh-dag-ulw-tui.mp4">고화질 영상 (MP4)</a></sub>
</p>

## 울트라 스킬

10개의 `ulw-` workflow. 대화에서 트리거만 말하면 Hermes가 라우팅합니다 —
전체 카탈로그는 [Workflow Reference](docs/WORKFLOWS.md).

| Skill | 무엇을 하나 |
| --- | --- |
| ⚡ `ulw-context` | 검토된 프로젝트 용어를 맞추고, 확인된 후보를 캡처하며, 용어에 라우팅 권한을 주지 않은 채 다음 결정 지점을 질문합니다. |
| ⚡ `ulw-interview` | 원하는 게 정확히 뭔지 알 때까지 한 번에 하나씩 묻습니다. |
| ⚡ `ulw-research` | 실제 코드와 웹을 뒤져 조사하고, 출처를 남기고, 의심스러우면 검증합니다. |
| ⚡ `ulw-plan` | 선택지 비교, 리스크, 완료 기준까지 합의된 검토 계획을 만듭니다. |
| ⚡ `ulw-work` | 승인된 계획을 같은 파일을 건드리지 않는 병렬 레인으로 실행합니다. |
| ⚡ `dag-ulw` | 큰 작업을 작은 병렬 노드의 그래프로 실행하고, 별도 노드로 목표를 검증한 뒤 실패한 부분을 복구합니다. |
| ⚡ `ulw-maestro` | Claude Code 또는 Codex에 위임한 작업을 실행 — 해당 CLI에 설치된 스킬로 구성한 프롬프트로 실시간 구동되며, 대시보드 행과 조종 가능한 세션을 제공합니다. |
| ⚡ `ulw-loop` | 계획 → 구현 → 리뷰를 목표가 진짜 통과할 때까지 돌립니다. |
| ⚡ `ulw-qa` | 일부러 험한 시나리오로 공격해 보고, 깨지는 곳을 고칩니다. |
| ⚡ `ulw-perf` | 어디가 진짜 느리고 비싼지 측정한 뒤, 핫패스를 하나씩 고칩니다. |

### 추천 조합

Hermes 프롬프트에 이렇게 입력합니다. `ulw-` 접두어가 workflow를 고르고,
나머지는 평범한 말로 쓴 요청입니다.

**1. 모양 잡기.** 아이디어가 아직 흐릿하면 `ulw-interview`, 바꿀 내용은
정해졌지만 위험하면 `ulw-plan`.

```text
❯ ulw-interview: I want a CLI that tracks the ISS and warns me before it passes overhead
❯ ulw-plan: analyze this project's circular dependencies and plan the refactor
```

**2. 만들기.** `ulw-work`가 승인된 계획을 같은 파일을 건드리지 않는 병렬
레인으로 실행합니다.

```text
❯ ulw-work: run the accepted plan
```

**3. 끝내기.** `ulw-loop`는 지정한 게이트가 통과할 때까지 계속 돕니다.

```text
❯ ulw-loop: keep going until npm test passes
```

### Claude Code와 Codex에 일 맡기기

코딩 에이전트를 직접 지정하면 `ulw-maestro`가 그 에이전트에 설치된 스킬로
프롬프트를 구성해 디스패치하고, 유닛마다 이어서 조종할 수 있는 세션과 함께
OMH HUD에 행을 추가합니다. 에이전트를 대신 고르지 않고, 머지도 하지 않습니다.

```text
❯ ulw-maestro: send the retry-queue fix to Claude Code and the lint cleanup to Codex, dispatch both now
```

## Hermes와 함께 동작하는 방식

Hermes Agent는 이미 루프를 돌립니다. OMH는 그 루프에 무엇을 넣을지 정합니다.
어느 lane에 어떤 모델과 추론 강도를 줄지, 코드 변경의 owner는 누구인지,
어떤 스킬이 적용되는지, 무엇을 완료로 볼지입니다. 어디서나 두 규칙이
지켜집니다. 모델 선택과 코딩 소유권은 별개의 결정이고, 준비된 것은 절대
실행된 것으로 보고하지 않습니다. 생성 카탈로그와 trigger, 증거 규칙은
[Workflow Reference](docs/WORKFLOWS.md)에 있습니다.

### 작업 흐름

**이해 → 조사 → 결정 → 계획 → 실행 → 검증 → 운영 → 학습**

| 단계 | 무엇을 하는가 |
| --- | --- |
| 이해 | 의도, 제약, 프로젝트 용어, 중단 조건을 확인합니다. |
| 조사 | 제품·코드·운영 맥락을 추측이 아니라 확인된 출처에 근거해 정리합니다. |
| 결정 | 선택지별 장단점과 결정권자를 명확히 합니다. |
| 계획 | 승인된 범위, 코드 변경 책임자, 테스트, 완료 기준을 실행 가능한 계획으로 정리합니다. |
| 실행 | 선택된 담당자에게 범위를 명확히 한 작업을 인계하고 실제로 실행된 내용을 추적합니다. 준비된 인계만으로는 실행 증거가 되지 않습니다. |
| 검증 | 실제로 관측한 테스트 결과, 리뷰 결과, CI 상태, 런타임 증거를 바탕으로 판정합니다. |
| 운영 | 릴리스 상태, 장애 현황, 롤백 여부, 후속 작업을 계속 확인할 수 있게 합니다. |
| 학습 | 검토를 거쳐 적용 범위가 정해진 교훈만 프로젝트 메모리에 남기거나 작업 흐름 개선에 반영합니다. |

**하이라이트**

| 인텔리전스 | OMH가 더하는 것 |
| --- | --- |
| 🧭 **Mixture-of-models 라우팅** | 위임되는 lane마다 dispatch 시점에 카테고리(모델 + 추론 강도)가 붙습니다. provider가 모델을 거부하면 chain을 따라 내려가고, 일을 하지 않은 자식은 초록 행이 아니라 `failed`로 표시됩니다. |
| 🎛️ **모델 계열별 캘리브레이션** | GPT-6 Astra, GPT-5.6, Claude 5.1, GLM 5.3, Kimi, Gemini, Qwen, DeepSeek 등 계열과 세대별로 프롬프팅을 튜닝하고, 벤치마크 쌍이 효과를 보여줄 때만 유지합니다. |
| 🗂️ **직접 소유하는 카테고리** | executor마다 열두 개의 기본 카테고리, `omh model-chains set`으로 순서 변경, 머신별로 chain을 재정렬하는 entitlement 인터뷰, 실행 전에 요청이 어디로 라우팅될지 보여주는 실시간 뷰를 제공합니다. |
| 🖥️ **네이티브 TUI 표면** | OMH HUD(카테고리·턴·비용·캐시가 붙은 실시간 행), 프롬프트 위 phase todo, `parallel shot ×N`, full-row diff 밴드, 직접 고르는 테마를 `omh`가 여는 OMH TUI에서 보여주며 Hermes를 패치하지 않습니다. |
| ⚡ **관측 가능한 병렬 작업** | 독립적인 작업을 파일 소유권이 겹치지 않는 fanout unit으로 나누고, provider 부하에 따른 admission control, 타입이 있는 결과 sidecar, 돌아온 결과를 읽는 verification gate를 붙입니다. |
| 🎼 **Maestro handoff** | Codex, Claude Code 등 다른 CLI를 위한 명시적 두 번째 lane입니다. readiness probe, capability snapshot, owner-fit 리포트, 실행별 모델·강도 지정을 제공하며 opt-in이고 기본 경로가 아닙니다. |
| 💸 **가격이 붙은 비용 텔레메트리** | 모든 HUD 행과 run summary에 토큰 수와 달러 금액을 표시합니다. 출처를 밝힌 요금표로 계산하고, 계산할 수 없는 run은 `$0`이 아니라 `unknown`으로 읽힙니다. |
| 🧠 **장기 프로젝트 메모리** | Hermes가 로드하는 파일 기반 memory provider, admission·retention 정책, 리뷰어가 승인하는 쓰기, freshness와 budget이 있는 recall pack을 제공합니다. Hermes 자체 메모리는 건드리지 않습니다. |
| 🔎 **구조적 코드 검색** | 측정 기반 `ast-grep` 플레이북(28개 언어, grep 폴백)과 저장소 전체 아키텍처 그림을 그리는 `omh codegraph uml`을 executor가 코드를 읽는 지점에 주입합니다. |
| 🛡️ **직접 쓰는 가드레일** | 규칙을 어긴 tool call을 사용자의 규칙 텍스트로 차단하는 toolcall rules, stub과 skip된 테스트를 증거로 인정하지 않는 completion-integrity gate, 위험한 작업을 위한 approval tier를 제공합니다. |
| ♾️ **울트라 워크플로 엔진** | 병렬 전달 lane, ledger와 실제 완료 gate로 도는 측정 루프, 엔진 실행 전의 decision-frontier 인터뷰를 제공합니다. 목록은 위 울트라 스킬 섹션에 있습니다. |
| 📦 **결정적 카탈로그** | 하나의 소스에서 생성되는 100개 이상의 설치형 스킬, 부정 케이스를 포함한 routing precision 코퍼스, 한 바이트만 어긋나도 CI가 실패하는 drift gate를 제공합니다. |

### 주장보다 증거

OMH는 직접 본 것만 일어났다고 말합니다. 화면에 뜨는 상태는 항상 두 부분입니다:
어느 단계인지, 그리고 OMH가 그걸 얼마나 확신하는지.

| 표시 | 의미 |
| --- | --- |
| `Plan · not run` | prompt나 plan이 준비됐습니다. **아직 아무것도 안 돌았습니다.** |
| `Code · running` | executor가 지금 돌고 있고 OMH가 보고 있습니다. |
| `Code · reported done` | executor가 끝났다고 말했습니다. 결과는 아무도 확인 안 했습니다. |
| `Test · verified` | test, review, CI gate가 실제로 통과했습니다. |

executor가 끝났다고 말한 것과 결과가 확인된 것은 다른데, 대부분의 도구가 둘 다
"완료"라고 씁니다. 능력 영향은 하나의 마케팅 점수로 접지 않고 독립된 차원으로
보고합니다. 자세한 내용은 [Capability Impact](docs/CAPABILITY_IMPACT.md).
단독 Hermes와 OMH를 거친 Hermes를 비교하는 제품 A/B 레인은 코퍼스와 오프라인
파일럿까지 갖췄고, 아직 게시된 측정 실행은 없습니다:
[`benchmarks/product-ab/v1/README.md`](benchmarks/product-ab/v1/README.md).

## 권장 모델

OMH에는 다음과 같이 편집 가능한 순서형 recommendation chain이 포함되어 있습니다. guided model setup은 사용자가 active라고 확인한 candidate만 기준으로 chain을 해석합니다. 그 결과는 준비된 routing config이며 provider availability, credential, dispatch, execution evidence가 아닙니다.

| 카테고리 alias | 용도 | 편집 가능한 recommendation 순서 |
| --- | --- | --- |
| `ultrabrain` | 가장 깊은 추론 | GPT-6 Astra (xhigh) |
| `deep` | 강력한 기본 티어 | Claude Opus 5.5, 다음 GPT-6.1 Sol, 다음 Kimi K3 (high) |
| `architect` | 아키텍처·시스템 설계 | Claude Opus 5.5, 다음 GPT-6 Astra, 다음 Claude Fable 5.1, 다음 Kimi K3 (xhigh) |
| `unspecified-high` | 기본 작업 모델 | GPT-6.1 Sol, 다음 Claude Opus 5.5, 다음 Kimi K3 (medium) |
| `unspecified-low` | 저비용 폴백 | GLM 5.3, 다음 DeepSeek V4.1 Flash, 다음 GPT-6.1 Sol, 다음 Claude Sonnet 5.5 (low) |
| `quick` | 짧은 작업 | GLM 5.3 Flash, 다음 GPT-6 Luna, 다음 Claude Haiku 5.5, 다음 Kimi K3 (low) |
| `writing` | 문서·산문 | Claude Opus 5.5, 다음 Claude Sonnet 5.5, 다음 Kimi K3, 다음 Gemini 3.1 Pro (medium) |
| `visual-engineering` | 프론트엔드·비주얼 | Claude Opus 5.5, 다음 Claude Fable 5.1, 다음 Gemini 3.1 Pro, 다음 Kimi K3 (high) |
| `artistry` | 비정형 창작 | Gemini 3.1 Pro, 다음 Claude Opus 5.5, 다음 Claude Fable 5.1, 다음 Kimi K3 (high) |
| `capable` | 범용 고성능 작업 | Claude Opus 5.5, 다음 GPT-6.1 Sol, 다음 Claude Fable 5.1, 다음 Kimi K3 (medium) |
| `simple-work` | 간단한 일상 작업 | GPT-6 Luna, 다음 Claude Haiku 5.5, 다음 DeepSeek V4.1 Flash (low) |
| `deep-work` | 최상위 깊이의 긴 작업 | Claude Opus 5.5, 다음 GPT-6.1 Sol (max) |

Ultrafast 티어가 궁금하다면 — Kimi K3 Ultrafast(300 TPS), GLM 5.3 Ultrafast — [OpenGateway](https://opengateway.ai/)에서 만나볼 수 있습니다.

모든 chain은 코드를 건드리지 않고 편집할 수 있고, `omh setup`이 시드하는 파일 하나로
관리됩니다:

```sh
$ cat ~/.omh/routing/model-chains.json
{
  "categories": {},
  "schema_version": "mixture_chain_overrides/v1"
}
```

`categories`가 비어 있으면 위의 기본 chain이 그대로 살아 있습니다. 여기에 적은
카테고리는 라우팅·fallback·HUD 라벨 모두에서 해당 chain을 대체합니다:

```json
{
  "schema_version": "mixture_chain_overrides/v1",
  "categories": {
    "architect": [
      {"model": "claude-fable-5-1", "reasoning_effort": "xhigh"},
      {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"}
    ],
    "quick": [
      {"model": "kimi-k3-ultrafast", "reasoning_effort": "low"},
      {"model": "glm-5.3-ultrafast", "reasoning_effort": "low"}
    ]
  }
}
```

현재 적용 중인 chain은 `omh model-chains show`로 확인합니다. `omh model-chains`(또는
`omh model`)는 화살표 피커를 열고, OMH TUI 안에서는 `/omh-model`이 같은 피커를
엽니다. 스크립트에서는
`omh model-chains set quick "kimi-k3-ultrafast:low, glm-5.3-ultrafast:low"`로 같은
변경을 합니다. OMH는 Hermes가 이미 연결해 둔 provider를 읽고(id와 변수 이름만,
키나 token은 읽지 않습니다) 각 chain에서 쓸 수 있는 항목이 앞에 오도록
재정렬합니다. 아무것도 지우지 않고, 확인을 위해 아무것도 호출하지 않습니다.
Claude Code나 Codex를 직접 띄우는 Maestro 레인은 자기만의 카테고리 다이얼
`omh coding category-maestro`를 가집니다. provider, fallback, 전체 우선순위는
[Guided Model Setup](docs/INSTALLATION.md#guided-model-setup)과
[docs/FANOUT.md](docs/FANOUT.md)를 참조하세요.

## 문서

- [문서 지도](docs/README.md)
- [설치와 업데이트](docs/INSTALLATION.md)
- [에이전트 설치 프로토콜](INSTALL_FOR_AGENTS.md)
- [제품 방향과 경계](docs/DIRECTION.md)
- [아키텍처](docs/ARCHITECTURE.md)
- [기능 manifest](docs/CAPABILITIES.md)
- [Workflow reference](docs/WORKFLOWS.md)
- [역할](docs/ROLES.md)
- [활용 사례](docs/APPLICATION_CASES.md)
- [모델 라우팅, 팬아웃 계약, 요청 채점](docs/FANOUT.md)
- [팬아웃 executor 증거: 세션, 장애 진단, 용량(agent/operator 참고)](docs/FANOUT-EXECUTOR-EVIDENCE.md)
- [Agent 보드와 네이티브 Kanban 협업(agent/operator 참고)](docs/AGENT-BOARD.md)
- [모델별 캘리브레이션 맵](MODEL_OPTI.md)
- [증거 규칙과 능력 영향](docs/CAPABILITY_IMPACT.md)
- [장기 기억 모델](docs/MEMORY.md)
- [Codegraph 명령](docs/CODEGRAPH.md)
- [Show-reel 툴킷](docs/SHOW-REEL-TOOLKIT.md)
- [Hermes로 초대용량 PDF 처리하기](docs/LONG-DOCUMENT-READING.md)
- [라이브 모델 벤치마크와 측정 결과](benchmarks/live-model-tools/v1/README.md)
- [릴리스와 개발](docs/RELEASE.md)
- [웹사이트](https://rlaope.github.io/oh-my-hermes/) ([소스](site/index.html))

## 개발

소스 체크아웃 시:

```sh
PYTHONPATH=tests uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src tests
uv run python -m omh.cli docs workflows --check
git diff --check
```

## 기여자

oh-my-hermes에 기여해 주신 모든 분들께 감사드립니다.

<a href="https://github.com/rlaope/oh-my-hermes/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=rlaope/oh-my-hermes&max=100&columns=10" alt="oh-my-hermes contributors" />
</a>

## 라이선스

oh-my-hermes는 [MIT License](LICENSE)로 배포됩니다.
[Team Art & Engineering](https://rlaope.github.io/artengine-lab/)의 공개 프로젝트로
개발되고 있으며, 프로젝트 소식은 [@rlaope](https://github.com/rlaope)에서 확인할 수
있습니다.
