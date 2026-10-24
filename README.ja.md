<div align="center">

<img src="assets/oh-my-hermes-wordmark.png" alt="OH-MY-HERMES" width="100%">

<img src="assets/omh-logo.png" alt="oh-my-hermes のロゴ: 白い角丸の正方形タイルに太い黒線で描いた oh-my-hermes のキャラクター" width="180">

# oh-my-hermes

**Hermes はそのまま。lane ごとに合うモデル、並列作業、そして「完了」の前に証拠を。**

[![GitHub Release](https://img.shields.io/github/v/release/rlaope/oh-my-hermes?color=58a6ff&labelColor=black&logo=github&style=flat-square)](https://github.com/rlaope/oh-my-hermes/releases) [![npm downloads](https://img.shields.io/npm/dt/oh-my-hermes?color=3fb950&labelColor=black&logo=npm&label=downloads&style=flat-square)](https://www.npmjs.com/package/oh-my-hermes) [![GitHub Contributors](https://img.shields.io/github/contributors/rlaope/oh-my-hermes?color=bc8cff&labelColor=black&style=flat-square)](https://github.com/rlaope/oh-my-hermes/graphs/contributors) [![GitHub Stars](https://img.shields.io/github/stars/rlaope/oh-my-hermes?color=e3b341&labelColor=black&style=flat-square)](https://github.com/rlaope/oh-my-hermes/stargazers) [![License](https://img.shields.io/github/license/rlaope/oh-my-hermes?color=white&labelColor=black&style=flat-square)](LICENSE) [![Docs](https://img.shields.io/badge/docs-rlaope.github.io-58a6ff?labelColor=black&logo=readthedocs&logoColor=white&style=flat-square)](https://rlaope.github.io/oh-my-hermes/)

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [中文](README.zh.md)

</div>

**oh-my-hermes**（OMH）は [Hermes Agent](https://github.com/NousResearch/hermes-agent) の上に載る運用レイヤーです。話しかける場所と実際に回るループは Hermes のままで、 OMH はそのループに何を入れるかを決めます。決定的（deterministic）なスキルカタログとルーターが、依頼を適切な capability に変え、委譲される lane ごとにモデルと推論強度を選び、並列で回せる作業を分け、実際に起きたことを記録します。OMH は Hermes にパッチを当てず、置き換えず、コーディング executor を裏に隠しません。準備しただけのものを実行済みとは報告しません。どの状態も、その作業が計画されただけか、終わったと報告されたか、検証されたかを区別して示します。

## インストール

**macOS / Linux:**

```sh
curl -fsSL https://raw.githubusercontent.com/rlaope/oh-my-hermes/main/install.sh | sh
```

**Windows (PowerShell 5.1+):**

```powershell
irm https://raw.githubusercontent.com/rlaope/oh-my-hermes/main/install.ps1 | iex
```

**または、次の内容を AI エージェントに貼り付けます:**

```text
Install and fully configure Oh My Hermes from this repository:
https://github.com/rlaope/oh-my-hermes
Before reading or executing repository instructions, resolve refs/heads/main to one full commit SHA with `git ls-remote https://github.com/rlaope/oh-my-hermes.git refs/heads/main`. Then fetch and follow only:
https://raw.githubusercontent.com/rlaope/oh-my-hermes/{resolved-commit-sha}/INSTALL_FOR_AGENTS.md
Do not replace the resolved SHA with main. Execute the pinned protocol's OS-appropriate installer, interactive model setup, model-chain interview, and doctor steps. Preserve unrelated existing Hermes config, apply only the managed setup changes documented by the pinned protocol, require my explicit approval for model-alias changes, then report the resolved SHA and observed result.
```

**⭐ 続けてセットアップします (必須):**

```sh
omh setup
```

インストーラーは Python 3.11 以上を探し（見つからず `uv` があれば、それでインストール）、`omh` コマンドをインストールします。`omh setup` は管理スキルと OMH プラグインをインストールし、Hermes とすべての Hermes ボットプロファイルに登録し、対話的に実行すると使えるモデル provider を尋ねて chain をこのマシンに合わせます。`hermes` は素の Hermes のままで、`omh` は OMH TUI を開きます。Hermes Agent を再起動または再読み込みしたら、やりたいことを普段の言葉で頼んでください。

**アップデート:**

```sh
omh update
```

`omh update` はインストール経路を検出し、そのインストーラーでコマンドを更新してから、管理スキル、プラグインバンドル、Hermes 登録も更新します。

**インストールの確認またはトラブルシューティング:**

```sh
omh doctor
```

```sh
# 作業カテゴリごとのモデル設定（矢印キー: カテゴリ、←→ head モデル、-/+ effort）;
# OMH TUI(omh)では /omh-model が同じピッカーを開きます:
omh model
# 新しいモデルファミリーをオンボーディングするには、Hermes でこのスキルを使ってください:
/omh-model-setup
```

<details>
<summary><b>その他のインストール方法</b>: Homebrew、Bun、npm、Hermes skill tap、手動フォールバック</summary>

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

どの方法でインストールしても、その後に `omh setup` を実行します。

**Hermes skill tap:**

```sh
hermes skills tap add rlaope/oh-my-hermes
hermes skills install rlaope/oh-my-hermes/skills/omh-routing --yes
```

**パッケージマネージャーでの手動フォールバックまたは削除:**

| インストール元 | CLI のアップグレード | CLI の削除 |
| --- | --- | --- |
| Homebrew | `brew upgrade rlaope/tap/omh` | `brew uninstall omh` |
| Bun | `bun update -g --latest oh-my-hermes` | `bun remove -g oh-my-hermes` |
| npm | `npm update -g oh-my-hermes` | `npm uninstall -g oh-my-hermes` |

`omh update` がインストーラーを見つけられないと報告したときだけ、マネージャーのコマンドを直接使います。コマンドパッケージを削除しても OMH の状態は残ります。完全に削除するには、マネージャーの削除コマンドの前に `omh uninstall --all` を実行します。

`--full` インストールを core に戻すようなメンテナンス手順は [Installation](docs/INSTALLATION.md#reconciling-an-existing-full-install-back-to-core) にあります。

</details>

> [!TIP]
> 一緒に参加しましょう！
>
> <table>
>   <tr>
>     <td width="124"><a href="https://x.com/rlaope"><img alt="X link" src="https://img.shields.io/badge/Follow-%40rlaope-00CED1?style=flat-square&logo=x&labelColor=black" width="112" /></a></td>
>     <td><code>oh-my-hermes</code> の更新情報は、リリースノートやプロジェクトのニュースとともに X の <a href="https://x.com/rlaope">@rlaope</a> で共有されます。</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://github.com/rlaope"><img alt="GitHub Follow" src="https://img.shields.io/github/followers/rlaope?style=flat-square&logo=github&labelColor=black&color=24292f" width="112" /></a></td>
>     <td>その他のプロジェクト、リリース、進行中の作業は GitHub で <a href="https://github.com/rlaope">@rlaope</a> をフォローしてください。</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://discord.gg/6EjTP3cWM"><img alt="Discord invite" src="https://img.shields.io/badge/Join-Discord-5865F2?style=flat-square&logo=discord&logoColor=white&labelColor=black" width="112" /></a></td>
>     <td>Discord の <a href="https://discord.gg/6EjTP3cWM">Oh-My-Hermes Community</a> に参加して、質問したり、ワークフローを共有したり、他のユーザーと交流しましょう。</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://github.com/rlaope/oh-my-hermes/graphs/contributors"><img alt="AI agent collaborators" src="https://img.shields.io/badge/With-AI%20agents-6f42c1?style=flat-square&labelColor=black" width="112" /></a></td>
>     <td><code>oh-my-hermes</code> の開発を支える AI エージェント <a href="https://github.com/frirenai"><strong>Friren</strong></a> と <a href="https://github.com/sionic-khope"><strong>Kami</strong></a> とともに作られています。</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://nousresearch.com/"><img alt="Thanks to Nous Research" src="https://img.shields.io/badge/Thanks-Nous%20Research-4B2E83?style=flat-square&labelColor=black" width="112" /></a></td>
>     <td>Hermes Agent を生み出した <a href="https://nousresearch.com/">Nous Research</a> に感謝します。</td>
>   </tr>
> </table>

## なぜ oh-my-hermes か

**lane ごとに合うモデル。** すべての依頼はディスパッチ前にスコアリングされ、スコアを動かしたシグナルには必ず名前が付きます。リネームは quick レーンへ、「X の参照をすべて見つけろ」は exhaustive-search シグナルに掛かり、取りこぼさないモデルへ行きます。委譲される lane はカテゴリ、つまり編集できるモデルと推論強度の chain に載ります。provider がモデルを拒否すれば chain は次へ進み、そのモデルを提供できない provider を引き継ぐことになるディスパッチは、黙ってダウングレードされる代わりに拒否されます。固定した 30 タスクのコーパスで、standard レーンの head を GPT モデルにしたルーティングは、マシン既定と同じ 18 件の通過をコスト 19%、壁時計時間 21% で保ちました（[測定記録](benchmarks/live-model-tools/v1/README.md)）。

**モデルファミリーごとのプロンプティング。** ファミリーごとにキャリブレーションブロックが一つあり、どの文もそのファミリーの文書化された性質を狙って書かれています。 Claude にはチェックリストがすべてだと、Gemini にはツール出力のない主張は証拠ではないと、Qwen3-Coder には thinking タグを出さないようにと伝えます。ルートがある所ではブロックを測定し、効いている間だけ残します。Astra の最初の草案にあった一文は、モデルに作業を続けさせようとして、通過率は変わらないまま解けないタスクにトークンを余分に使ったため、削られました。

**安全な所でだけ並列に、戻るときは型付きで。** `ulw-work` は承認済みの計画を、ファイルを共有しないユニットに分け、ユニットごとに一つの固定 SHA から分岐したワークツリーを与えます。各ユニットは型付きの結果として戻ります: プロセス終了、スキーマ有効、検証を観測、統合準備完了。証拠のない exit 0 は、ゲートが確認するまで `reported done` に留まります。

**専門スキルが実行に入る。** 専門家を呼び出す必要はありません。100 を超える専門スキル（フロントエンド、バックエンド、Rust、ネイティブデバッグ、推論サービング、デザインと検証のゲート、セキュリティレビュー、リファクタ計画）が、依頼がその領域に触れるとツール呼び出しとして実行に入り、エージェントが「完了」と認める基準を引き上げます。英語でも韓国語でも、ルーターが専門家を選びます。

**アーキテクチャを一枚に。** リポジトリの図を頼むと、`codebase-uml` がコードから直接描きます。パッケージ、モジュール、すべての import 辺が出て、循環には印が付きます。`refactor-plan` は上位の発見事項を段階に変え、各段階はテストで挙動を固定した一つの PR として入ります。

**思い込みではなく、承認された記憶。** 何も黙って記憶されません。候補はセッションから取り込まれてレビューカードに載り、理由を書き残したうえで記憶・却下・保留されます。次のセッションは、タスクに合わせて順位付けされ、トークン予算に収めた recall pack を受け取ります。Hermes 自身のメモリは読みも書き換えもしません。

**すべてを一つの画面で。** `omh` は OMH TUI を開きます。委譲された lane ごとにモデル、effort、ターン、トークン、コストが 1 行でライブ更新され、プロンプトの上にはその実行の phase todo があります。コスト 0 はホストが確認したときだけ表示され、価格の分からない呼び出しは `$0` ではなく `unknown` と表示されます。

<details>
<summary><b>サーフェスごとに一場面</b>: カテゴリ、キャリブレーション、並列作業、HUD、スキル、アーキテクチャ、記憶</summary>

<br>

<p align="center">
  <img src="assets/showcase-02-categories.svg" alt="omh coding category-maestro show: エグゼキュータ別カテゴリチェーン、オペレータのオーバーライド、拒否されたディスパッチ" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-03-calibration.svg" alt="ファミリーごとのキャリブレーション 1 行、gpt-6-astra のモデル契約、測定に基づく改訂" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-04-parallel.svg" alt="ulw-work のファンアウト: ファイルが重ならない 3 ユニット、ユニットごとのワークツリー、型付き状態、1 ターンで出したツール呼び出し" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-05-hud.svg" alt="OMH HUD: レーンごとのモデル・effort・ターン・トークン・コスト出所・証拠状態と phase todo" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-06-skills.svg" alt="ツール呼び出しとして実行に入る omh-* 専門スキル、実行を囲む専門家の軌道、3 つの数字" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-07-architecture.svg" alt="codebase-uml が 2 つの循環とともにリポジトリを描き、その横に発見事項と段階的なリファクタ計画、測定された前後、フェーズごとの dock 行" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-08-memory.svg" alt="長期記憶: 承認カード、1 つの記録のライフサイクル、attention ティア、次のセッション向けの予算付き recall pack" width="1080">
</p>

</details>

## `dag-ulw` によるグラフエンジニアリング

エージェント一つでは手に余る大きな仕事を `dag-ulw` に渡します。親エージェントが仕事を小さなノードのグラフとして計画し（ノードごとにカテゴリ、プロンプト、依存関係、書き込めるファイル）、OMH は実行前にグラフを検証してボックスグラフとして描きます。準備のできたノードは Hermes 自身の子エージェント上で `delegate_task` によって並列に走り、それぞれのカテゴリのモデルで動きます。別のノードが目標を検証し、失敗したノードは報告前に修復されます。同時に走りうる二つのノードは、その間に辺がない限り同じ書き込み範囲を共有しません。

```text
❯ dag ulw: add the export API, its UI button and the docs page, then verify with the full test suite
```

<p align="center">
  <img src="assets/omh-dag-ulw-tui.gif" alt="/dag-ulw を実行する OMH TUI: プロンプトを入力すると 7 レーンのボックスグラフが定義され、レーンが Claude Opus 5.5、GPT-6.1 Sol、DeepSeek V4.1 Flash、GLM 5.3、Kimi K3.1 Pro でレイヤーごとに走り、すべて accepted で終わります" width="960">
  <br><sub><code>/dag-ulw</code> を実行する OMH TUI: ボックスグラフのあと、5 つのモデルファミリーで 7 つのレーンがレイヤーごとに走ります。実際のレンダラーを、時間を早回ししたスクリプトによるデモ実行で動かしたもので、モデルは実際には動いていません。<a href="assets/omh-dag-ulw-tui.mp4">高画質の動画 (MP4)</a></sub>
</p>

## ウルトラスキル

10 個の `ulw-` workflow。チャットでトリガーを言えば Hermes がルーティング — 全カタログは [Workflow Reference](docs/WORKFLOWS.md)。

| Skill | 何をするか |
| --- | --- |
| ⚡ `ulw-context` | レビュー済みのプロジェクト用語を揃え、確認済み候補を取り込み、用語にルーティング権限を与えず次の判断点を質問します。 |
| ⚡ `ulw-interview` | 何が欲しいのか正確に分かるまで、一度に一つずつ質問します。 |
| ⚡ `ulw-research` | 実際のコードとウェブを調べ、出典を残し、怪しければ裏取りします。 |
| ⚡ `ulw-plan` | 選択肢の比較、リスク、完了基準まで合意したレビュー済み計画を作ります。 |
| ⚡ `ulw-work` | 承認済み計画を、同じファイルに触れない並列レーンで実行します。 |
| ⚡ `dag-ulw` | 大きな作業を小さな並列ノードのグラフとして実行し、別ノードで目標を検証して、失敗した部分を修復します。 |
| ⚡ `ulw-maestro` | Claude Code または Codex に委任したタスクを実行 — そのCLI自身のインストール済みスキルから組み立てたプロンプトで、ライブに起動し、ドック行とスティアリング可能なセッションを持ちます。 |
| ⚡ `ulw-loop` | 計画 → 実装 → レビューを、ゴールが本当に通るまで回します。 |
| ⚡ `ulw-qa` | わざと過酷なシナリオで攻撃し、壊れた所を直します。 |
| ⚡ `ulw-perf` | 本当に遅く高コストな場所を測り、ホットパスを一つずつ修正します。 |

### おすすめの組み合わせ

Hermes のプロンプトにこう入力します。`ulw-` の接頭辞が workflow を選び、残りは普段の言葉で書いた依頼です。

**1. 形にする。** アイデアがまだ曖昧なら `ulw-interview`、変更内容は決まっていてもリスクがあるなら `ulw-plan`。

```text
❯ ulw-interview: I want a CLI that tracks the ISS and warns me before it passes overhead
❯ ulw-plan: analyze this project's circular dependencies and plan the refactor
```

**2. 作る。** `ulw-work` が承認済みの計画を、同じファイルに触れない並列レーンで実行します。

```text
❯ ulw-work: run the accepted plan
```

**3. 仕上げる。** `ulw-loop` は指定したゲートが通るまで回り続けます。

```text
❯ ulw-loop: keep going until npm test passes
```

### Claude Code と Codex に仕事を渡す

コーディングエージェントを指名すると、`ulw-maestro` はそのエージェント自身のインストール済みスキルからプロンプトを組み立ててディスパッチし、再開・操作できるセッションとともにユニットごとの行を OMH HUD に追加します。エージェントを代わりに選ぶことも、マージすることもありません。

```text
❯ ulw-maestro: send the retry-queue fix to Claude Code and the lint cleanup to Codex, dispatch both now
```

## Hermes との連携の仕組み

Hermes Agent はすでにループを回しています。OMH はそのループに何を入れるかを決めます。どの lane にどのモデルと推論強度を与えるか、コード変更の owner は誰か、どのスキルを適用するか、何を完了とみなすかです。どこでも二つのルールが守られます。モデル選択とコーディングの所有権は別々の決定であり、準備されたものを実行済みとして報告することは決してありません。生成カタログ、trigger、証拠ルールは [Workflow Reference](docs/WORKFLOWS.md) にあります。

### 作業の流れ

**理解 → 調査 → 判断 → 計画 → 実行 → 検証 → 運用 → 学習**

| 段階 | 行うこと |
| --- | --- |
| 理解 | 意図、制約、プロジェクト用語、停止条件を確認します。 |
| 調査 | 仮定を、情報源に裏付けられた製品・コード・運用に関する背景情報に置き換えます。 |
| 判断 | 選択肢、トレードオフ、意思決定者を明確にします。 |
| 計画 | 合意した範囲、コード変更の担当者、テスト、完了条件を実行可能な計画にまとめます。 |
| 実行 | 準備を実行とみなすことなく、選定した担当者に範囲を限定した引き継ぎを行います。 |
| 検証 | 実際に確認したテスト、レビュー、CI、および実行時の証拠に基づいて判定します。 |
| 運用 | リリースの健全性、インシデント、ロールバックの状況、後続作業を常に可視化します。 |
| 学習 | レビュー済みで適用範囲が明確な教訓だけを、プロジェクトメモリやワークフローの改善に反映します。 |

**ハイライト**

| インテリジェンス | OMH が追加するもの |
| --- | --- |
| 🧭 **Mixture-of-models ルーティング** | 委譲される lane ごとに、dispatch 時点でカテゴリ（モデル + 推論強度）が付きます。provider がモデルを拒否すれば chain を下り、仕事をしなかった子は緑の行ではなく `failed` と表示されます。 |
| 🎛️ **モデル系列ごとのキャリブレーション** | GPT-6 Astra、GPT-5.6、Claude 5.1、GLM 5.3、Kimi、Gemini、Qwen、DeepSeek など、系列と世代ごとにプロンプティングを調整し、ベンチマークのペアが効果を示す間だけ維持します。 |
| 🗂️ **自分で所有するカテゴリ** | executor ごとに十二の標準カテゴリ、`omh model-chains set` による並べ替え、マシンごとに chain を並べ替える entitlement インタビュー、実行前にリクエストがどこへルーティングされるかを示すライブビュー。 |
| 🖥️ **ネイティブ TUI サーフェス** | OMH HUD（カテゴリ・ターン・コスト・キャッシュ付きのライブ行）、プロンプト上の phase todo、`parallel shot ×N`、full-row diff バンド、自分で選ぶテーマ。`omh` が開く OMH TUI に表示され、Hermes にパッチは当てません。 |
| ⚡ **観測できる並列作業** | 独立した作業を、ファイル所有権が重ならない fanout unit に分割し、provider 負荷に応じた admission control、型付きの結果 sidecar、返ってきた結果を読む verification gate を付けます。 |
| 🎼 **Maestro handoff** | Codex、Claude Code、その他の CLI のための明示的な第二 lane。readiness probe、capability snapshot、owner-fit レポート、実行ごとのモデルと強度の指定。opt-in であり、既定の経路ではありません。 |
| 💸 **価格付きコストテレメトリ** | すべての HUD 行と run summary にトークン数とドル金額を表示します。出典を明記した料金表で計算し、計算できない run は `$0` ではなく `unknown` と読めます。 |
| 🧠 **長期プロジェクトメモリ** | Hermes が読み込むファイルベースの memory provider、admission と retention のポリシー、レビュアーが承認する書き込み、freshness と budget を持つ recall pack。Hermes 自身のメモリには触れません。 |
| 🔎 **構造的コード検索** | 計測に基づく `ast-grep` プレイブック（28 言語、grep フォールバック）と、リポジトリ全体のアーキテクチャ図を描く `omh codegraph uml` を、executor がコードを読む地点に注入します。 |
| 🛡️ **自分で書くガードレール** | ルールを外れた tool call を自分のルール文で遮断する toolcall rules、stub やスキップされたテストを証拠と認めない completion-integrity gate、危険な操作のための approval tier。 |
| ♾️ **ウルトラワークフローエンジン** | 並列デリバリー lane、ledger と実際の完了 gate で回る計測ループ、エンジン実行前の decision-frontier インタビュー。一覧は上のウルトラスキルにあります。 |
| 📦 **決定的なカタログ** | 一つのソースから生成される 100 以上のインストール可能なスキル、ネガティブケースを含む routing precision コーパス、1 バイトのずれで CI が失敗する drift gate。 |

### 主張より証拠

OMH は自分が見たことだけを起きたと報告します。表示される状態は常に「どの段階か」と「OMH がどれだけ確信しているか」の二部構成です。

| 表示 | 意味 |
| --- | --- |
| `Plan · not run` | prompt や plan の準備ができています。**まだ何も動いていません。** |
| `Code · running` | executor が今動いており、OMH が観測しています。 |
| `Code · reported done` | executor が終わったと言いました。結果は誰も確認していません。 |
| `Test · verified` | test、review、CI gate が実際に通過しました。 |

executor が終わったと言うことと結果が確認されたことは別ですが、多くのツールは両方を「完了」と書きます。能力影響は一つのマーケティング用スコアに畳まず、独立した次元ごとに報告します。詳しくは [Capability Impact](docs/CAPABILITY_IMPACT.md)。単独の Hermes と OMH 経由の Hermes を比べる製品 A/B レーンは、コーパスとオフラインパイロットまで揃っており、測定済みの実行はまだ公開されていません: [`benchmarks/product-ab/v1/README.md`](benchmarks/product-ab/v1/README.md)。

## 推奨モデル

OMH には次の編集可能な順序付き recommendation chain が含まれています。guided model setup は、ユーザーが active と確認した candidate だけを基準に chain を解決します。その結果は準備済み routing config であり、provider availability、credential、dispatch、execution の証拠ではありません。

| カテゴリ alias | 用途 | 編集可能な recommendation 順序 |
| --- | --- | --- |
| `ultrabrain` | 最も深い推論 | GPT-6 Astra (xhigh) |
| `deep` | 強力なデフォルト層 | Claude Opus 5.5、次に GPT-6.1 Sol、次に Kimi K3 (high) |
| `architect` | アーキテクチャ・システム設計 | Claude Opus 5.5、次に GPT-6 Astra、次に Claude Fable 5.1、次に Kimi K3 (xhigh) |
| `unspecified-high` | デフォルト作業モデル | GPT-6.1 Sol、次に Claude Opus 5.5、次に Kimi K3 (medium) |
| `unspecified-low` | 低コストのフォールバック | GLM 5.3、次に DeepSeek V4.1 Flash、次に GPT-6.1 Sol、次に Claude Sonnet 5.5 (low) |
| `quick` | 短いタスク | GLM 5.3 Flash、次に GPT-6 Luna、次に Claude Haiku 5.5、次に Kimi K3 (low) |
| `writing` | 文章・ドキュメント | Claude Opus 5.5、次に Claude Sonnet 5.5、次に Kimi K3、次に Gemini 3.1 Pro (medium) |
| `visual-engineering` | フロントエンド・ビジュアル | Claude Opus 5.5、次に Claude Fable 5.1、次に Gemini 3.1 Pro、次に Kimi K3 (high) |
| `artistry` | 型にはまらない創作 | Gemini 3.1 Pro、次に Claude Opus 5.5、次に Claude Fable 5.1、次に Kimi K3 (high) |
| `capable` | 汎用の高性能作業 | Claude Opus 5.5、次に GPT-6.1 Sol、次に Claude Fable 5.1、次に Kimi K3 (medium) |
| `simple-work` | 簡単な日常タスク | GPT-6 Luna、次に Claude Haiku 5.5、次に DeepSeek V4.1 Flash (low) |
| `deep-work` | 最高深度の長時間タスク | Claude Opus 5.5、次に GPT-6.1 Sol (max) |

Ultrafast ティアを試したいなら — Kimi K3 Ultrafast(300 TPS)、GLM 5.3 Ultrafastは [OpenGateway](https://opengateway.ai/) で利用できます。

すべての chain はコードを触らずに編集でき、`omh setup` がシードする一つのファイルで管理されます:

```sh
$ cat ~/.omh/routing/model-chains.json
{
  "categories": {},
  "schema_version": "mixture_chain_overrides/v1"
}
```

`categories` が空なら、上の既定 chain がそのまま有効です。ここに書いたカテゴリは、ルーティング、fallback、HUD ラベルのすべてでその chain を置き換えます:

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

いま有効な chain は `omh model-chains show` で確認します。`omh model-chains`（または `omh model`）は矢印キーのピッカーを開き、OMH TUI の中では `/omh-model` が同じピッカーを開きます。スクリプトからは `omh model-chains set quick "kimi-k3-ultrafast:low, glm-5.3-ultrafast:low"` で同じ変更ができます。OMH は Hermes がすでに連携している provider を読み取り（id と変数名だけで、キーや token は読みません）、各 chain で使える項目が先頭に来るよう並べ替えます。何も削除せず、確認のために何も呼び出しません。Claude Code や Codex を直接起動する Maestro レーンには、専用のカテゴリダイヤル `omh coding category-maestro` があります。provider、fallback、完全な優先順位は [Guided Model Setup](docs/INSTALLATION.md#guided-model-setup) と [docs/FANOUT.md](docs/FANOUT.md) を参照してください。

## ドキュメント

- [ドキュメントマップ](docs/README.md)
- [インストールと更新](docs/INSTALLATION.md)
- [エージェント向けインストール手順](INSTALL_FOR_AGENTS.md)
- [製品方針と境界](docs/DIRECTION.md)
- [アーキテクチャ](docs/ARCHITECTURE.md)
- [機能 manifest](docs/CAPABILITIES.md)
- [Workflow reference](docs/WORKFLOWS.md)
- [ロール](docs/ROLES.md)
- [活用事例](docs/APPLICATION_CASES.md)
- [モデルルーティング、ファンアウト契約、リクエスト採点](docs/FANOUT.md)
- [ファンアウト executor 証拠: セッション、障害診断、容量（agent/operator 向け）](docs/FANOUT-EXECUTOR-EVIDENCE.md)
- [Agent ボードとネイティブ Kanban 連携（agent/operator 向け）](docs/AGENT-BOARD.md)
- [モデルごとのキャリブレーションマップ](MODEL_OPTI.md)
- [証拠ルールと能力影響](docs/CAPABILITY_IMPACT.md)
- [長期記憶モデル](docs/MEMORY.md)
- [Codegraph コマンド](docs/CODEGRAPH.md)
- [Show-reel ツールキット](docs/SHOW-REEL-TOOLKIT.md)
- [Hermes で超大容量 PDF を処理する](docs/LONG-DOCUMENT-READING.md)
- [ライブモデルベンチマークと測定結果](benchmarks/live-model-tools/v1/README.md)
- [リリースと開発](docs/RELEASE.md)
- [ウェブサイト](https://rlaope.github.io/oh-my-hermes/)（[ソース](site/index.html)）

## 開発

ソースチェックアウト時:

```sh
PYTHONPATH=tests uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src tests
uv run python -m omh.cli docs workflows --check
git diff --check
```

## コントリビューター

oh-my-hermes に貢献してくださった皆さまに感謝します。

<a href="https://github.com/rlaope/oh-my-hermes/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=rlaope/oh-my-hermes&max=100&columns=10" alt="oh-my-hermes contributors" />
</a>

## ライセンス

oh-my-hermes は [MIT License](LICENSE) で公開されています。 [Team Art & Engineering](https://rlaope.github.io/artengine-lab/) のオープンプロジェクトとして開発されており、更新情報は [@rlaope](https://github.com/rlaope) で確認できます。
