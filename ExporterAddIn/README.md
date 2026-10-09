# Fusion Exporter アドイン（DXF 対応・軽量版）

Fusion のクラウド上にある **f2d 図面を DXF / DWG / PDF**、**f3d を STEP / STL / f3d など**に
一括で書き出す Fusion アドインです。[aconz2/Fusion360Exporter](https://github.com/aconz2/Fusion360Exporter)
（パブリックドメイン）を元に、アドイン化と DXF 対応、速度面の手直しをしています。

書き出した DXF は Panel Studio の「盤サイズ → DXF 取り込み」や、キャンバスの下敷きにそのまま使えます。

## 入れ方

1. この `ExporterAddIn` フォルダを PC の好きな場所に置く（リポジトリごと clone でも、フォルダだけコピーでも可）
2. Fusion で **ユーティリティ > アドイン > スクリプトとアドイン**（Shift+S）
3. 「アドイン」タブの **＋** → このフォルダを選ぶ
4. 一覧に出た **Exporter** の「実行」を ON → ユーティリティ > アドイン パネルにボタンが出る
5. **「起動時に実行」** にチェック（manifest で既定 ON にしてあるので、出ていればそのままで可）

以後は Fusion を起動するたびにボタンが出ています。押すと設定ダイアログが**すぐに**開きます。

## 使い方

1. データパネルで書き出したいフォルダを開く
2. ツールバーの **Exporter** を押す
3. 設定を確認して OK

既定では **「Download Open Folder」ON ＋ Export Types = dxf** なので、
「開いているフォルダ以下の f2d 図面を全部 DXF にする」が最短 3 クリックで走ります。
前回の設定は `last_settings.json`（このフォルダに自動生成）に残り、次回の初期値になります。

実行中は Fusion がドキュメントを次々に開くので、終わるまで PC は他の作業に使いにくいです。
終わると「Saved / Skipped / Errored / Took n s」の要約が出ます。

### 設定項目

| 項目 | 意味 | 既定 |
|---|---|---|
| Directory | 出力先 | デスクトップ/Fusion360Export |
| Export Types | f2d → `pdf` `dxf` `dwg`、f3d → `f3d` `step` `stl` `igs` `sat` `smt` `3mf` | dxf |
| Download Open Folder | データパネルで開いているフォルダ以下を対象にする | ON |
| Show Project Folders / Export Projects | Download Open Folder を OFF にしたとき、プロジェクト（またはその直下フォルダ）を選ぶ | — |
| Open Documents Hidden (faster) | ドキュメントを画面に出さずに開く。描画が省けるぶん速い。図面の書き出しで中身が欠けるようなら OFF に | OFF |
| Unhide All Bodies | f3d の非表示ボディも含めて書き出す（f3d 形式以外） | ON |
| Versions | 過去バージョンも書き出す。0 なら最新だけ | 0 |
| Save Sketches as DXF | f3d の各スケッチを DXF にする | OFF |
| Version Separator is Space | `name_v3.dxf` ではなく `name v3.dxf` | OFF |
| Export Non-Design Files | f3d/f2d 以外の添付ファイルをそのままダウンロード | OFF |

出力は `<Directory>/<フォルダ階層>/<名前>_v<版>.<拡張子>`。**既にあるファイルは開かずに飛ばす**ので、
2 回目以降は増えたぶんだけ書き出す「同期」として使えます。ログは `<Directory>/<日時>.txt`。

## 速くするためにしていること

| 元の挙動 | この版 |
|---|---|
| ダイアログを出す前に全プロジェクト一覧を取りに行く（数秒待つ） | Download Open Folder が ON の間は取りに行かない。OFF にした瞬間に初めて取る |
| ファイルごとに必ず `file.versions`（サーバー往復）を読む | Versions が 0（既定）なら読まない |
| ドキュメントは常に表示して開く | 「Open Documents Hidden」で非表示で開ける |
| 対象外の形式でも一通りコードを通る | f2d は図面形式だけ、f3d はモデル形式だけ見る。出力が全部揃っていればドキュメントを開かない |

ドキュメントを開く時間そのものは Fusion 側の都合なので、ここは変えられません。

## DXF / DWG について

図面の DXF / DWG 書き出しは **2026 年 9 月版の Fusion API** で追加された
`DrawingExportManager.createDXFExportOptions` / `createDWGExportOptions` を使います。
古い Fusion では「This Fusion build cannot export drawings as DXF」とログに残してその図面を errored に数え、
残りの処理は続けます（Fusion を更新するか、Export Types から dxf/dwg を外してください）。

## 開発メモ

- `ExporterAddIn.py` = 入口（ボタンの登録と片付けだけ）、`exporter_core.py` = 本体
- `exporter_core.py` を直しているときは `ExporterAddIn.py` の `DEV_RELOAD = True` にすると、
  ボタンを押すたびに読み直します（配布時は False に戻す）
- Fusion なしで動くテスト: `python3 test_core.py`（adsk をモックして書き出しループだけ確認）
- 元コードのライセンスは `LICENSE.upstream`（Unlicense）
