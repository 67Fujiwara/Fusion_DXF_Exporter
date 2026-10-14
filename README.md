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

1. Shift+S → DXFExporter を選んで **実行**
2. 書き出すフォルダを決める。やり方は 2 つ
   - **Download Open Folder を ON**：データパネルで最後に入ったフォルダが対象。実行前に左上のグリッドの
     アイコンでデータパネルを開き、目当てのフォルダの中に入っておく
   - **Download Open Folder を OFF**：ダイアログの **Project** でプロジェクトを選び、**Folder** で
     フォルダを下りていく（サブフォルダ名を選ぶと中に入る。「↑ 上のフォルダへ」で戻る）。
     止まった所が対象で、次回も同じ場所が選ばれた状態で開く
3. **Target** に「プロジェクト / … / フォルダ名」が出るので、そのフォルダで合っているか見る
4. 設定を確認して OK

Fusion が「開いているフォルダ」として返すのは、データパネルで最後に入ったフォルダで、
いま画面に出している図面の場所ではありません。迷うときは OFF にして Project / Folder で選ぶほうが確実です。

既定では **「Download Open Folder」ON ＋ Export Types = dxf** なので、
「開いているフォルダ以下の f2d 図面を全部 DXF にする」がそのまま走ります。
前回の設定は `last_settings.json`（`DXFExporter` フォルダに自動生成）に残り、次回の初期値になります。

実行中は Fusion の中に **進捗パネル**が出ます。今の段階（一覧を取得中 / 開いています / 書き出し中 / 閉じています）、
ファイル名、何件目か、経過時間、キャンセルボタンがあります。
Fusion がドキュメントを次々に開くので、終わるまで PC は他の作業に使いにくいです。
キャンセルすると今のファイルを終えてから止まります。
終わると「Saved / Skipped / Errored」と、かかった時間の内訳（一覧取得・開く・書き出し・閉じる）が出て、
スクリプトは終了します。

### フリーズしているのか、動いているのか

ファイルを 1 つ開いている間、Fusion は操作を受け付けません。これは Fusion の API がスクリプトを
メインスレッドで同期実行するためで、Windows が「応答なし」と出すこともあります。見分け方は次の 2 つです。

- **進捗パネルの段階とファイル名が進んでいれば動いています。** 1 ファイルあたり「開いています」が数十秒
  続くのは普通です（図面は参照先の 3D モデルも一緒に読み込みます）
- **ログ**（`<Directory>/<日時>.txt`）には 15 秒ごとに `... still opening X, 45 s so far` という行が入ります。
  メモ帳などで開いて、この行の秒数が増えていれば待っている最中です

「開いています」のまま 5 分以上進まないときは、見えない所で Fusion が確認ダイアログを出している可能性があります
（参照先モデルの更新確認など）。「Open Documents Hidden」を OFF にして同じファイルを試し、
何のダイアログが出るか見てください。

### 設定項目

| 項目 | 意味 | 既定 |
|---|---|---|
| Directory / Browse… | 出力先。Browse… でフォルダ選択ダイアログが開く | デスクトップ/Fusion360Export |
| Export Types | f2d → `pdf` `dxf` `dwg`、f3d → `f3d` `step` `stl` `igs` `sat` `smt` `3mf` | dxf |
| Download Open Folder | ON: データパネルで開いているフォルダを対象にする。OFF: 下の Project / Folder で選ぶ | ON |
| Project / Folder | Download Open Folder が OFF のとき、対象フォルダを選ぶ。プロジェクト一覧は OFF にした瞬間に取りに行く | 前回の場所 |
| Target | （表示のみ）これから見に行くフォルダ。データパネルのパンくずと同じ並び | — |
| Include Subfolders | そのフォルダの下のサブフォルダも全部たどる。OFF なら開いているフォルダの直下だけ | ON |
| Open Documents Hidden (faster) | ドキュメントを画面に出さずに開く。画面が切り替わらず、描画も省ける。書き出しが進まない・中身が欠けるときは OFF に | ON |
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
| プロジェクト単位か、その直下のフォルダまでしか選べない | 任意の深さのフォルダを選べる |
| ファイルごとに必ず `file.versions`（サーバー往復）を読む | Versions が 0（既定）なら読まない |
| ドキュメントは常に表示して開く（1 枚ごとに画面が切り替わる） | 既定で非表示のまま開く（Open Documents Hidden） |
| 対象外の形式でも一通りコードを通る | f2d は図面形式だけ、f3d はモデル形式だけ見る。出力が全部揃っていればドキュメントを開かない |

ドキュメントを開く時間そのものは Fusion 側の都合なので、ここは変えられません。

### 遅いときの見方

終了時の要約に `Took 85.3 s:  listing 1.2 s, opening 70.4 s, exporting 12.1 s, closing 1.6 s` のような内訳が出ます。
ログ（`<Directory>/<日時>.txt`）には 1 行ごとに経過秒が付いています。

| 時間が多い所 | 意味 | できること |
|---|---|---|
| opening | Fusion が図面と、図面が参照する 3D モデルをクラウドから落として開いている | 「Open Documents Hidden」が ON か確かめる。それ以上はスクリプトからは縮められない |
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

- 本体は `DXFExporter/DXFExporter.py`。進捗パネルは `DXFExporter/resources/status.html`。Fusion は実行のたびに読み直すので、直したらそのまま実行すればよい
- 進捗パネルが作れない環境では Fusion 標準の進捗ダイアログに切り替わる
- Fusion なしで動くテスト: `python3 DXFExporter/test_core.py`（adsk をモックして書き出しループだけ確認）
- 元コードのライセンスは `LICENSE.upstream`（Unlicense）。このリポジトリも同じ扱いです
