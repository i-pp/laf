<!--
KISS Open License Version 1.0 (KOL-v1)

SPDX-FileCopyrightText: 2026 I-plusplus Inc.
SPDX-License-Identifier: LicenseRef-KOL-v1

Copyright remains with its respective holders; original copyright and attribution notices must be preserved.
Contributors may add copyright notices for their own original modifications.
Distributed modifications must be identified as required by Section 4(c) of KOL-v1.

Use, modification, distribution, and incorporation into commercial or proprietary software are permitted
without license fees or source disclosure, subject to all terms of the complete KOL-v1 license.

Contributors grant the patent permissions stated in KOL-v1. Outputs are not covered merely because a
KOL-licensed tool produced them; embedded licensed material remains subject to applicable license terms.

THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OR LIABILITY TO THE EXTENT PERMITTED BY APPLICABLE LAW.

This header is a summary, not a separate license or an amendment to KOL-v1.
All terms and conditions of KOL-v1 apply. See the accompanying LICENSE file for the complete license text.

Scope: This notice applies only to the project-authored metadata or documentation in this exact file. External material it describes retains its separate terms.
-->

# MCP SDK をホストに導入する

## 最初に確認すること

この CLI は、MCP SDK **1.30.0を含む固定29パッケージだけ**を、ホスト上に
残るユーザー専用 venv へ入れます。システム Python、PATH、MCPクライアント、
Router、モデル、サービスは変更しません。**既に導入済みなら再導入は不要です。**
SDK を入れるだけでは LAF adapter は有効になりません。

初回は、リポジトリのルートで次を実行します。`inspect` は読み取りだけです。

```sh
python3 -I -B scripts/laf-sdk.py inspect
```

`ready_for_explicit_action` と空の `preflight_issues` を確認してから取得します。
この操作は公式 PyPI の資料と wheel を新しい bundle に保存しますが、導入しません。

```sh
python3 -I -B scripts/laf-sdk.py prepare
```

出力の `review_path` にある `review.json` のライセンス、advisory、対象環境を確認し、
その出力に含まれる **`install_dry_run_command`、続いて `install_command`** を使います。
別の環境や記事から SHA256 をコピーしないでください。コマンドには今回の bundle に
対応する確認値と導入先が入ります。dry-run は導入を行いません。

この手順は SDK だけの導入です。[adapter の設定と試験](laf-mcp.md)は別工程であり、
LAF 全体の Quick Start ではありません。自動インストールやサービス起動は行いません。

## 保存先と前提

引数を省略すると、次の保存先を使います。

- 取得資料: `$HOME/laf-sdk-1.30.0-bundle`
- 永続的な導入先: `$HOME/.local/share/laf-sdk-1.30.0`
- 導入後の Python: `$HOME/.local/share/laf-sdk-1.30.0/venv/bin/python`

各保存先の親ディレクトリは既存で、自分が所有し、他ユーザーが書き込めない
必要があります。`prepare` には新しい bundle、`install` には新しい prefix が必要です。
既存のディレクトリは上書きも削除もしません。途中状態がある場合は、原因を確認して
別の新しい保存先を指定します。確認済み bundle は同じ環境での再開に使えます。
新しい bundle が必要な場合の例です。実在する安全な親ディレクトリを使ってください。

```sh
python3 -I -B scripts/laf-sdk.py prepare \
  --bundle /ABSOLUTE/NEW/bundle --prefix /ABSOLUTE/NEW/prefix
```

対象は Linux x86_64 の既存 CPython 3.12 または3.14です。
`venv` と pip 22.3以上が必要ですが、この CLI は不足分をホストに追加しません。
導入には既存 pip のメタデータパーサーと `--python` を使い、venv は
`--without-pip` で作ります。`apt`、`get-pip`、`sudo`、
`--break-system-packages` による回避処理はありません。
Ubuntu の PEP 668 対応システム Python でも、専用 venv のみを導入先にします。
SDK 用 venv には pip/build ツールを追加しないでください。

## CLI が検証するもの

`prepare` は、実行中 Python の互換 wheel tags と依存 marker を使います。
3.12用 wheel を3.14ホスト向けとして流用しません。固定候補は29パッケージで、
`pyjwt[crypto]` の cryptography / cffi / pycparser も含みます。

- 公式 `pypi.org` のリリース情報と `files.pythonhosted.org` の binary wheel のみ取得。
  リダイレクト・環境変数の proxy・代替 index・sdist・MCP SDK の追加 extras は使いません。
- パッケージ名、版、ファイル名、サイズ、SHA256、METADATA、WHEEL tags を照合。
  SDK wheel は確認済みの上流 SHA256 とも照合します。
- Python条件、依存制約、extra の到達範囲を検証。直接 URL の依存を拒否。
- PyPI の脆弱性情報と wheel のライセンス宣言・文面を記録。
  advisory、欠落、不明な宣言、互換 wheel 不在なら停止。
- ピンが公式の最新安定1.xでなくなっていたら、勝手に更新せず停止。

これらは取得時点の確認です。PyPI の情報が空でも、脆弱性が存在しないことや
包括的な安全性を証明しません。ライセンス資料の保存も法的な承認ではありません。
通信を許可されていない環境では停止します。proxy が設定されている場合も、
直接接続へ切り替えず、取得前に停止します。別経路への自動切り替えはありません。

`install` は通信せず、確認済みのローカル wheel と完全な hash lock だけを使います。
最終保存先に venv を作り、Python の prefix / site-packages を検証してから
`--no-index --no-deps --require-hashes` で導入します。
続いて `pip check`、SDK import、正確なパッケージ版を確認します。
全て通過した場合だけ `installed.json` を作ります。venv は移動しません。

依存物の改ざん検知用 hash lock と、MCP の同一ジョブ要求を照合する fingerprint は
別用途です。どちらもリポジトリ内のソースファイル別 hash 一覧を作る機能ではありません。

## 失敗した場合

新しく作ったディレクトリは途中の状態で残します。導入途中のパッケージが
新規 prefix 内だけに存在する場合もありますが、完了 marker は作りません。
既存の環境・コマンド・サービスには触れません。自動削除・上書き・再利用はせず、
エラーに残した場所を表示します。その状態を完成済みとして使わないでください。
不要な領域の削除は、内容を確認したうえで利用者が判断します。

2026-11-11に修正した作成権限の不具合では、呼び出し元の `umask=0002` を継承した
venv が `pyvenv.cfg` を `0664` で作り、CLI 自身の厳格な検査で止まっていました。
修正版は `subprocess.run(..., umask=0o077)` で子プロセスだけを private にします。
呼び出し元の `umask` と既存ファイルは変えず、所有者・書き込み権限の検査も緩めません。
`chmod` や旧 prefix の削除で回避する必要はありません。
[CPython 3.14.4 の venv 実装](https://github.com/python/cpython/blob/v3.14.4/Lib/venv/__init__.py)
でも、このファイルは通常の書き込み用 `open` で作ることを確認しています。

## PyJWT の更新理由

2026-11-11の公式 PyPI 情報では、旧候補の2.13.0に advisory の記録があり、
2.15.1では `vulnerabilities` が空でした。2.15.1は MIT、Python >=3.9で、
MCP SDK 1.30.0 の `pyjwt[crypto]>=2.10.1` を満たします。
cryptography への依存条件と crypto extra は旧候補と同じです。

- [2.15.1の公式リリース情報](https://pypi.org/pypi/pyjwt/2.15.1/json)
- [decode options の更新に関する advisory](https://github.com/advisories/GHSA-gvp8-978c-rx2q)
- [2.15.0で修正された再帰処理の advisory](https://github.com/advisories/GHSA-42vr-xj54-vc7v)
- [2.15.0で修正された OKP key の advisory](https://github.com/advisories/GHSA-x33g-cr3x-6449)

decode options の advisory は修正版の欄が未指定ですが、2.15.1の上流ソースに
[options をコピーする修正](https://github.com/jpadilla/pyjwt/commit/0c87c8c8b1a74cac99ad8115f3050efcb7fbed35)
が含まれることも確認しました。2.14.0は残る advisory の対象なので選んでいません。
取得時には全29パッケージの advisory を確認し、1件でも報告されれば停止します。
警告を無視する例外は追加していません。

## 測定した範囲

installer 修正版は56件の試験を通過しました。synthetic wheel・fake HTTP・fake install
runner 等の52件と、既存 CPython 3.12.14 で空の venv を作る実試験4件です。
実試験は一時ディレクトリで `venv --without-pip` を使い、SDK は導入していません。

- 旧動作の再現: `umask=0002` なら `pyvenv.cfg` は `0664` となり、厳格な検査が拒否。
- 修正後: 呼び出し元が `0002`、`0022`、`0077` のどれでも同ファイルは `0600`、
  venv ディレクトリは `0700` となり、同じ検査が通過。
- 呼び出し元の `umask` 不変、system site-packages 無効、pip 未導入を確認。

利用者自身による Ubuntu 26.04.1 x86_64 / CPython 3.14.4 への導入成功も確認済みです。
SDK 1.30.0、PyJWT 2.15.1、全29固定版、`pip check`、SDK import、prefix 分離が通過しました。
その既存 SDK を使う adapter の stdio・fake-provider 試験も後に通過しました。
[検証記録](laf-mcp-validation.md)は、この SDK の導入と実サービスへの接続を区別します。
GPU/NPU、8–16 GB VRAM、実モデル、別PC、実 MCP クライアント設定、配備は未検証です。

## ライセンスと変更

first-party CLI・pin候補・文書は KOL-v1 です。リポジトリの `LICENSE` と `NOTICE` を
参照してください。SDK・依存 wheel は同梱せず、取得時には各ライセンスが適用されます。

2026-11-11: SDK-only CLI、固定候補、試験、導入文書を追加。PyJWT を2.15.1へ更新し、
子プロセスだけの作成マスクを `0077` に固定。既存環境を上書きしない条件を維持。
2026-11-12: 個人別の再開コマンド・確認値を一般向け導入手順と実測結果へ置き換え。
CLI の動作・固定版・権限検査には変更なし。
