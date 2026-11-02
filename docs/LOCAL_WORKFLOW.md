# UR5e 本機六軸點動

雙擊「啟動點動介面.command」，或執行 `/usr/bin/python3 /Users/poemccs_1/Documents/ur5e/jog_panel.py`。

固定目標 192.168.10.2。啟動只讀取狀態，不上電、不釋放煞車、不解除安全停止。確認遠端模式、RUNNING、NORMAL、程式已停止後，按「啟用點動」，再按住某軸正負按鈕。

這是低速短脈衝原型，非連續平滑伺服點動。固定目標速度 1 度/秒，每次 speedj 執行 0.1 秒後在機器人端明確 stopj(0.5)，脈衝間隔至少 0.4 秒。單次按住最多 3 秒，需放開再按。鬆開、游標離開、失焦、空白鍵、Escape、關閉視窗會取消後續脈衝。電腦端 0.15 秒更新租期；過期禁止發送下一脈衝。狀態/網路異常會解除啟用，不自動恢復。

斷線後已送出的脈衝仍可能完成 0.1 秒運動，再執行減速停止；不是瞬間停止，也不是經安全認證的斷線保護。無法在網路中斷時保證送達停止封包，因此停止命令包含在每個機器人程式內。不得以此替代急停、安全防護或現場風險評估。

使用前須確認工具/TCP、負載、安裝、安全限制與周圍空間。此原型沒有碰撞路徑規劃，不可與其他控制器同時使用。軟體停止不是實體急停。

已通過 Python 語法檢查、12 個關節方向的命令格式及遠端/安全狀態判斷檢查。尚未進行真實移動、停止時間量測、斷線注入驗證，不應視為已驗證的生產控制器。

官方命令參考：https://www.universal-robots.com/manuals/EN/PDF/SW5_25_1/scriptmanualG5/script_directory_Poly5.pdf

## 整合控制台（目前使用）

以一般使用者執行 `/usr/bin/python3 -B -u realsense_live.py`，開啟 http://127.0.0.1:8765/ 。HTTP 伺服器只綁定本機。新版相機背景服務需要一次本機 USB 權限授權：先在舊擷取程式按 Control+C，再執行：

```
sudo /usr/local/bin/python3 -u /Users/poemccs_1/Documents/ur5e/realsense_live.py --camera-worker
```

保留這個終端機。之後使用網頁的「啟動相機／停止相機」，不需每次重新輸入命令。密碼只由終端機的 sudo 取得，網頁不會收取密碼。背景服務只接受固定的 start/stop 訊息，不接受網頁提供的 shell 命令。背景服務結束或 Mac 重開機後，需重新啟動一次。不要同時執行 `check_realsense.py`、舊 `--capture` 或其他相機擷取程式。HTTP 服務重啟時停止相機，必須再次按網頁「啟動相機」。

相機擷取在獨立子程序執行，背景服務不載入 SDK；子程序超過 12 秒沒有進度會被關閉，避免 SDK 卡住時連網頁停止按鈕也失效。停止先給子程序 1.5 秒正常退出，未退出才強制終止自己的子程序；不處理其他應用程式。

相機首次開啟使用 640×480 / 30 fps；失敗後降為 15 fps 重試，最多三次失敗。相機啟動、停止與重連都不會啟用手臂。舊 `--capture` 仍可作獨立串流使用，但不支援網頁啟停。

### 手臂按住連續移動

六軸按鈕改為連續低速移動，目標 1°/秒；按住期間持續移動，無固定秒數上限；放開後須重新啟用。保留現場確認、正常遠端狀態、新鮮影像與單一控制者檢查。一次只允許一軸。勿同時執行舊 Tk 點動介面或其他控制器。

主 HTTP／手臂程序使用 `/usr/bin/python3`；本機無移動測試確認該執行環境能接受機器人回連。原 `/usr/local/bin/python3` 的入站連線在此 Mac 立即失效。沒有修改防火牆設定。RealSense USB 擷取與視覺原生套件仍使用 `/usr/local/bin/python3`，並由獨立程序處理。

每次按住只傳送一個 URScript 程式，機器人按控制週期持續執行 speedj；不再每 0.4 秒起停。Mac 只在瀏覽器心跳有效（租期 0.25 秒）、畫面和機器人狀態新鮮時，回覆機器人的請求。機器人端以自身計時監測回覆，0.12 秒沒有有效授權便離開速度迴圈並執行 stopj(0.5)。請求包含隨機識別碼與嚴格遞增序號，授權時間以機器人的請求時間計算，延遲回覆不能續期。持續移動完全依賴新鮮的按壓心跳授權，沒有累計按壓時長上限。Mac 僅在連往手臂的網路介面開啟短暫反向連線埠，且只接受設定的機器人 IP。

首次建立連線時，尚未授權移動的讀取可等待 0.6 秒；只有在請求往返時間小於 0.12 秒時才授權。移動中的逾時條件維持 0.12 秒。

0.12 秒是看門狗設定，不是已量測的停止時間。瀏覽器心跳失效判定、傳輸、排程與減速仍需時間。未在實機驗證連續移動、失焦、斷線及最終停止距離；不可視為安全認證的控制器或實體急停。此修改的測試不會移動手臂。

官方介面參考：
- [UR 速度控制](https://www.universal-robots.com/manuals/EN/HTML/SW10_10/Content/prod-scriptmanual/all_scripts/speedj_qdat.htm)
- [UR Socket timeout](https://www.universal-robots.com/manuals/EN/HTML/SW5_19/Content/prod-scriptmanual/G5/socket_read_binary_integer.htm)
- [UR 反向連線設計](https://docs.universal-robots.com/Universal_Robots_ROS2_Documentation/doc/ur_client_library/doc/architecture/reverse_interface.html)

### 笛卡爾、法蘭與 Robotiq 位置

「六軸關節／笛卡爾」切換時會解除啟用。笛卡爾方向採機座座標系：X／Y／Z 平移 5 mm/s、繞 X／Y／Z 旋轉 1°/s；使用持續 speedl 與 stopl 減速停止。旋轉按鈕是角速度方向，姿態欄位則是 UR 的旋轉向量（rad），不是 Euler 角。按住期間沒有固定秒數上限；尚未做完整實體笛卡爾移動與停止距離驗證。

Cartesian Info 與關節資料從同一個 Robot State 封包讀取。顯示目前 TCP 位姿、法蘭 XYZ 與 TCP 偏移；法蘭位置由 `T_base_flange = T_base_tcp × inverse(T_flange_tcp)` 計算。2026-11-02 實際讀到 TCP 偏移全零，因此介面標示目前作用點是法蘭，尚非夾持中心。程式不會自動修改示教器 TCP。笛卡爾啟用要求使用者核對目前作用點；啟用後如果 TCP 偏移改變，控制立即失效。

Robotiq 只使用 GET POS／STA／OBJ／FLT 讀取，不發送任何開合、啟用、速度或夾持力設定。原始開合值為 0–255；具體型號、開口毫米數與法蘭到夾持中心的校正未提供，故不換算或捏造夾瓦三維座標。

停止原因保留在狀態中，不被後續一般停止訊息覆蓋。前端透過控制狀態版本，拒絕以舊的狀態回應撤銷較新的啟用結果；解除啟用的請求完成後才允許重新啟用。

參考：[UR Robot State 格式](https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/primary-secondary-guide.html)、[UR speedl](https://www.universal-robots.com/manuals/EN/HTML/SW10_15/Content/prod-scriptmanual/all_scripts/speedl_xd_a_t_aRot_a.htm)。

### 止血鉗視覺工作流程

彩色與深度已對齊。在彩色畫面單擊可量測相機 XYZ；拖曳可人工框選止血鉗。操作會凍結該影格並解除手臂控制。凍結影格以記憶體保留最多 5 分鐘、最多 4 個瀏覽器控制者，每人一幀；一般預覽只保留最近 32 幀。量測使用顯示影格的深度與內參，不混用後來的畫面。深度不足與過期影格會拒絕量測。

「儲存止血鉗樣本」在 `vision_data/hemostat/<時間與識別碼>/` 寫入彩色 JPG、對齊的 uint16 小端深度、相機內參與尺度、人工框選 JSON 和 YOLO 格式 labels.txt。資料只存本機。手臂狀態快照沒有硬體時間同步，不可作為手眼校正資料。人工框選不等於自動辨識。

「辨識這一幀」接入本機 ONNX 模型。尚未提供模型時會明確說明，不會以一般物件或輪廓偵測冒充止血鉗辨識。支援單類別 `hemostat`、640×640 輸入、未內建 NMS 的 YOLOv8 detection ONNX，輸出須為 `[1,5,N]` 或 `[1,N,5]`。將模型放在 `models/`，並參照 `models/hemostat.example.json` 建立 `models/hemostat.json`。預處理 RGB / 255、上下各補 80 像素；門檻 0.5，NMS IoU 0.45。模型結果是待實物驗證的候選框，未生成抓取姿態。

「檢查抓取條件」列出缺項，不發送機器人命令。自動抓取目前未實作執行器，按鈕停用。已知相機是末端安裝；仍需止血鉗模型及實物驗證、手眼校正、TCP／夾爪幾何、夾持參數、抓取姿態和已驗證的避障路徑。沒有這些資料，不能從影像選點直接移動到目標。

### 醫療器械資料集框選標註

開啟 http://127.0.0.1:8766/ ，或從控制台的「開啟醫療器械資料集標註」進入。這是獨立的本機程序，不使用機器人或 USB 相機，也不需管理員權限。重新啟動時雙擊「啟動標註介面.command」，或執行 `/usr/bin/python3 -B -u /Users/poemccs_1/Documents/ur5e/annotation_server.py`。

原始資料來源為 `/Users/poemccs_1/Downloads/medical_tools_dataset`，包含 1,427 張照片、五類器械。程式只讀取原始 JPG，標註另存於 `dataset_annotations/medical_tools/annotations/`；歷次儲存前的版本保留在 `history/`。圖片以相對路徑識別並核對 SHA-256，來源圖片更換時拒絕套用舊座標。同名 JPG 分屬不同類別時不會互相覆蓋。

1. 選擇照片資料夾、圖片與「新增框的類別」。預設從直止血鉗開始；照片分類依使用者指定的資料夾名稱設定，框的位置由人工標註或自動候選提供。
2. 在照片拖曳，框住整件可見器械，包括握環、柄與尖端。每件器械一框，影像內其他類別的器械也要完整標註。可切換「選取／調整」，拖曳框或四角；也能輸入原圖像素座標、放大、刪除或復原。
3. 未完成時按「儲存草稿」。無法判斷時「暫略這張」。只有核對所有器械與類別後才勾選確認，按「確認完成並下一張」。空框不會自動當成負樣本，必須明確勾選照片沒有五類器械。
4. 選擇資料用途 train／val／test。同一拍攝批次或相鄰影格應留在同一集合；現有照片背景相似，請用獨立場景驗證，勿把隨機抽出的相鄰照片當成獨立測試。
5. 「匯出到本機」會在 `dataset_annotations/medical_tools/exports/<時間與識別碼>/` 產生 JPG、YOLO TXT、`data.yaml` 與含來源／標註版本的 `manifest.json`。只匯出已確認資料，草稿、暫略與未標註照片排除。每次匯出建立新資料夾，不覆蓋以前的匯出。

五類匯出順序固定為 0 round_tip_scissors、1 curved_hemostat、2 straight_hemostat、3 short_forceps、4 long_forceps。「止血鉗單一類別」將彎、直止血鉗合併為 0 hemostat；已核對、但沒有止血鉗的照片會以空 TXT 匯出為負樣本。框格式為 `class x_center y_center width height`，座標除以原始影像寬高，依 [Ultralytics YOLO detection 格式](https://docs.ultralytics.com/datasets/detect/)。

匯出採用逐張明確指定的集合，不自動隨機分割。缺少 train、val 或有效框時介面會提示尚未適合訓練。若搬移匯出資料夾，需更新 `data.yaml` 的 `path`。本流程只準備人工偵測標註，未訓練模型；框選不是抓取位置，也不是手眼校正資料。

標註服務只綁定 127.0.0.1，寫入 API 檢查 Host／Origin／token，不接受任意讀寫路徑。儲存使用版本比較，另一頁更新後會拒絕舊版本覆蓋。標註 API 與幾何測試共 8 項，使用暫存圖片與輸出；實際資料集不混入測試標註。

#### 自動候選框與人工覆核

2026-11-02 已為這批綠布背景照片執行 `autolabel_dataset.py`：1,311 張有候選框、111 張未找到可靠候選、5 張已有人工草稿而保留。這是資料集專用的背景色／輪廓提案，不是訓練完成的醫療器械辨識模型。照片分類依使用者指定的資料夾名稱設定，沒有宣稱模型辨識信心值或準確率。

工具在縮小影像上估計綠布區域、排除其邊界與部分膚色，提取前景連通區域，必要時合併相近輪廓，最後換回原始像素座標。長鑷子近拍另用局部色彩基準。規則建立於 [OpenCV 連通區域與輪廓工具](https://docs.opencv.org/4.13.0/d3/dc0/group__imgproc__shape.html)。陰影、反光、遮擋、器械超出畫面與多件物品仍可能造成錯框、合併框或漏框，須人工檢查。

提案獨立存於 `dataset_annotations/medical_tools/proposals/`，批次報告為 `autolabel-report.json`。不修改原圖或 `annotations/` 的人工內容。頁面顯示「自動框選 · 待確認」；沒有可靠候選則顯示「需人工框選」，不自動標成負樣本。可用「優先檢查 ⚑」篩選遮擋、多段輪廓、邊緣與長鑷子近拍。

點選框或右側清單後可移動、拉角點、修改類別或刪除；漏掉的物品可用「新增框」補上。框選正確後仍須勾選核對並按「確認完成並下一張」，才會寫入人工確認標註並允許匯出。自動提案、尚未確認的人工草稿都不納入訓練匯出。提案來源保留在後續人工標註的 `suggestion` 欄位。

若需要重跑：`/usr/local/bin/python3 -B -u /Users/poemccs_1/Documents/ur5e/autolabel_dataset.py`，再按「更新候選清單」。已存在的人工標註會略過，舊提案保留在 `proposal_history/`；人工儲存另有跨程序檔案鎖與磁碟版本檢查。使用 OpenCV 的批次程序與僅用標準函式庫的標註伺服器分開執行，不接觸手臂控制。

驗證：`/usr/local/bin/python3 -B -m unittest -v test_annotation_workspace.py test_autolabel.py` 共 9 項，涵蓋人工標註保留、未確認提案禁止匯出、空偵測不作負樣本、來源圖片雜湊、跨程序版本衝突與合成圖形的框座標。這些測試驗證流程，並不代表器械偵測準確率。

### 驗證

`python3 -m unittest -v test_robot_bridge.py test_vision_workspace.py test_console_api.py test_camera_worker.py test_tool_state.py`

測試使用假手臂與暫存伺服器，涵蓋心跳逾時、釋放、攝影機中斷、延遲／重複訊息、連線中放開、機器人端程式的逾時條件、相機啟停 API、影格凍結後預覽快取淘汰、人工標註儲存、跨來源拒絕及未就緒抓取拒絕。這些測試不等同 URScript 實機執行驗證。

補充驗證：23 項 Python 測試與 3 項前端競態測試通過。`diagnostics/probe_reverse.py` 在送出前移除所有移動、速度與停止命令，僅用來驗證控制通訊和機器人端看門狗。2026-11-02 無移動測試確認可連續交換資料，且刻意斷線後約 0.12 秒退出控制迴圈；這不是實體停止距離測試。診斷紀錄保留於 `diagnostics/probe-*.json`。

### 依使用者資料夾建立照片分類清單

使用者已指定依資料夾分類。`build_classification_index.py` 將全部 1,427 張照片的照片類別記錄至 `dataset_annotations/medical_tools/classification/image_classes.csv` 與 `manifest.json`。清單含相對路徑、固定類別 ID、中英文名稱、圖片 SHA-256，以及來源 `user_assigned_folder`。類別數量為圓頭手術剪 237、彎止血鉗 295、直止血鉗 299、短鑷子 299、長鑷子 297。

這是照片層級的分類資料。已有人工框、自動候選框與覆核狀態保持原樣；照片分類完成不代表候選框位置已經人工確認。頁面已分別顯示「照片分類已依資料夾設定」與框選覆核進度。重新產生清單可執行 `/usr/bin/python3 -B build_classification_index.py`，舊清單會另存於 classification/history。
