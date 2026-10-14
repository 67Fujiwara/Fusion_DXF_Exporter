# Fusion DXF Exporter（スクリプト版）

Fusion のクラウド上にある **f2d 図面を DXF / DWG / PDF**、**f3d を STEP / STL / f3d など**に
一括で書き出す Fusion スクリプトです。[aconz2/Fusion360Exporter](https://github.com/aconz2/Fusion360Exporter)
（パブリックドメイン）を元に、DXF 対応と速度面の手直しをしています。

アドインではなく**スクリプト**なので、常駐もツールバーのボタンもありません。
使うときに Shift+S から実行し、終わると自動で終了します。

## 入れ方

1. このリポジトリを clone するか「Code > Download ZIP」で落として解凍し、中の **`DXFExporter` フォルダ**を
   消さない場所に置く（フォルダ名は変えない。Fusion はフォルダ名と同じ名前の `.py` を探す）
2. Fusion で **ユーティリティ > アドイン > スクリプトとアドイン**（Shift+S）
3. 「スクリプト」タブの **＋** → `DXFExporter` フォルダを選ぶ
4. 一覧の「マイスクリプト」に **DXFExporter** が出れば完了

## 使い方

1. データパネルで書き出したいフォルダを開く
2. Shift+S → DXFExporter を選んで **実行**
3. 設定を確認して OK

既定では **「Download Open Folder」ON ＋ Export Types = dxf** なので、
「開いているフォルダ以下の f2d 図面を全部 DXF にする」がそのまま走ります。
前回の設定は `last_settings.json`（`DXFExporter` フォルダに自動生成）に残り、次回の初期値になります。

実行中は進捗バー（何件目か・キャンセル）が出ます。Fusion がドキュメントを次々に開くので、
終わるまで PC は他の作業に使いにくいです。キャンセルすると今のファイルを終えてから止まります。
終わると「Saved / Skipped / Errored」と、かかった時間の内訳（一覧取得・開く・書き出し・閉じる）が出て、
スクリプトは終了します。

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

### 遅いときの見方

終了時の要約に `Took 85.3 s:  listing 1.2 s, opening 70.4 s, exporting 12.1 s, closing 1.6 s` のような内訳が出ます。
ログ（`<Directory>/<日時>.txt`）には 1 行ごとに経過秒が付いています。

| 時間が多い所 | 意味 | できること |
|---|---|---|
| opening | Fusion が図面と、図面が参照する 3D モデルをクラウドから落として開いている | 「Open Documents Hidden」を ON にして比べる。それ以上はスクリプトからは縮められない |
| exporting | Fusion の DXF 書き出しそのもの | 無い。シート数・ビュー数に比例する |
| listing | フォルダやファイルの情報をハブから取っている | 普通は数秒以内。多ければ回線の問題 |
| closing | ドキュメントを閉じている | 普通は 1 秒未満 |

スクリプト自身の処理は 1 ファイルあたり 1 秒かかりません。

## DXF / DWG について

図面の DXF / DWG 書き出しは **2026 年 9 月版の Fusion API** で追加された
`DrawingExportManager.createDXFExportOptions` / `createDWGExportOptions` を使います。
古い Fusion では「This Fusion build cannot export drawings as DXF」とログに残してその図面を errored に数え、
残りの処理は続けます（Fusion を更新するか、Export Types から dxf/dwg を外してください）。

## スクリプトとアドインの違い

| | スクリプト（この版） | アドイン |
|---|---|---|
| 起動 | Shift+S → 選んで実行 | ツールバーのボタン |
| Fusion 起動時 | 何もしない | 自動でロードして常駐 |
| ファイル構成 | `DXFExporter.py` 1 本 | 入口とコアの 2 本＋アイコン |
| 書き出しの中身・速さ | 同じ | 同じ |

## 開発メモ

- 本体は `DXFExporter/DXFExporter.py` の 1 ファイル。Fusion は実行のたびに読み直すので、直したらそのまま実行すればよい
- Fusion なしで動くテスト: `python3 DXFExporter/test_core.py`（adsk をモックして書き出しループだけ確認）
- 元コードのライセンスは `LICENSE.upstream`（Unlicense）。このリポジトリも同じ扱いです
