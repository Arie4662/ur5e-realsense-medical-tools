# UR5e、RealSense 與醫療器械資料集

本儲存庫保存本機控制介面、RealSense 影像工作流程、醫療器械標註工具，以及原始照片與標註快照。主要執行環境是 macOS；尚未驗證 Windows 或 Linux 的完整硬體控制流程。

## 目前資料與模型狀態

- 原始 JPG：**1,427 張**，保留使用者提供的五個分類資料夾。
- 人工標註：**95 張已確認、5 張草稿**；已確認資料目前全屬於 train，val/test 都是 0。
- 自動候選紀錄：**1,422 份**，包含未找到可靠框的紀錄；部分已經人工覆核。候選框使用綠布背景規則產生，未覆核內容不會匯入訓練資料。
- **未包含訓練完成的模型權重或辨識準確率結果。** 尚需完成框選覆核與獨立驗證集；自動抓取執行器尚未實作。

| ID | 資料夾／中文類別 | 訓練類別名稱 | 張數 |
|---|---|---|---:|
| 0 | 圓頭手術剪 | round_tip_scissors | 237 |
| 1 | 彎止血鉗 | curved_hemostat | 295 |
| 2 | 直止血鉗 | straight_hemostat | 299 |
| 3 | 短鑷子 | short_forceps | 299 |
| 4 | 長鑷子 | long_forceps | 297 |

## 下載後開始標註

在儲存庫根目錄執行（Python 3.9 或以上；標註服務只用標準函式庫）：

```sh
python3 -B prepare_dataset.py
python3 -B annotation_server.py
```

開啟 <http://127.0.0.1:8766/>。準備程序會核對全部照片的 SHA-256，並產生此電腦專用的 `dataset_annotations/medical_tools/project.json`；不修改標註。此檔案不納入 Git。若整個工作目錄搬家，先核對舊設定，再移除該設定檔並重新執行準備程序。

macOS 也可在準備完成後雙擊 `啟動標註介面.command`。畫面支援候選框調整、人工框選、草稿、覆核與 train/val/test 指派。分類依照資料夾；物件框的位置仍需人工確認。

## 訓練資料

已提供分類 CSV、圖片雜湊、人工框、候選框、修改歷史及 YOLO 匯出工具。框選標註保存在 `dataset_annotations/medical_tools/`；原始照片保存在 `data/medical_tools_dataset/`。

```sh
# 五類物件偵測資料
python3 -B export_training_data.py --mode all
# 將彎／直止血鉗合併為單一 hemostat 類別
python3 -B export_training_data.py --mode hemostat
```

每次匯出會建立獨立資料夾，含圖片、YOLO TXT、`data.yaml` 與來源／標註版本清單。也可直接在標註網頁匯出。只納入已確認標註；草稿與未覆核候選不會被當成真實標籤。現有快照沒有 val，因此匯出會回報 `training_ready: false`。請以不同拍攝場景建立驗證資料，同一拍攝批次的相鄰照片應留在同一集合。

`requirements-training.txt` 記錄來源電腦已有的 Ultralytics 版本；目前專案尚無完整訓練實驗、模型權重或測試指標。控制台只支援單類別 hemostat 的 YOLOv8 detection ONNX（未內建 NMS），設定範例為 `models/hemostat.example.json`；不能直接放入五類輸出模型。

## RealSense 與手臂介面

```sh
# macOS 主介面，僅監聽本機
/usr/bin/python3 -B -u realsense_live.py
```

開啟 <http://127.0.0.1:8765/>。相機需要另外安裝相容的 RealSense SDK／Python 綁定；影像處理套件版本記錄在 `requirements-vision.txt`。來源 Mac 的相機與影像處理使用 `/usr/local/bin/python3`；主介面使用系統 Python。這些執行檔位置依原本硬體環境設定，其他電腦須先核對。

相機背景服務在來源 Mac 的啟動方式（需在專案根目錄、由使用者於終端機輸入 sudo 密碼）：

```sh
sudo /usr/local/bin/python3 -u ./realsense_live.py --camera-worker
```

手臂 IP 預設 `192.168.10.2`，介面提供六軸與機座座標的笛卡爾低速按住移動、放開停止及通訊看門狗。軟體啟動不會自動啟用移動。TCP／夾持中心及末端相機手眼校正仍待設定；軟體停止不是實體急停，連續控制的實體停止距離尚未完成驗證。`jog_panel.py` 是早期短脈衝介面；目前使用 `realsense_live.py` 的整合控制台。

完整原始操作記錄與限制見 [docs/LOCAL_WORKFLOW.md](docs/LOCAL_WORKFLOW.md)。該文件保留來源 Mac 路徑供追溯；本頁命令適用於下載副本。

## 檔案導覽與驗證

- `annotation_*.py/html/js`：標註服務、資料管理與介面。
- `autolabel_dataset.py`：綠布背景的候選框提案；不覆蓋人工標註。
- `build_classification_index.py`：依原始資料夾產生照片分類清單。
- `robot_bridge.py`、`tool_state.py`：控制通訊及 TCP／夾爪狀態讀取。
- `vision_workspace.py`、`vision_processing.py`：影格量測、樣本儲存與模型接入。
- `UPLOAD_MANIFEST.json`：本次快照範圍與狀態。
- `DATASET_SHA256SUMS.txt`：所有原始 JPG 的完整性清單。

```sh
python3 -B -m unittest discover -p 'test_*.py'
node --test test_annotation_geometry.cjs test_console_client.cjs
```

Python 測試需要影像處理套件，使用假資料／假手臂，不能替代實機安全驗證。儲存庫排除即時相機串流、控制工作階段、暫存檔與診斷遙測；本機正在執行的介面不受此上傳副本影響。
