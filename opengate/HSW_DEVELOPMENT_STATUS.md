# HSW / FastSpeed FSW 開發狀態

> 狀態日期：2026-09-23（Asia/Taipei）
>
> 適用機型／韌體：SIGMA fp Ver.5.02
>
> 正式程式目標：`OG_TARGET=hsw`
>
> 成熟度：研究原型、已上機、未發布；**不是 release candidate**

## 1. 文件目的、命名與證據規則

使用者口中的 **FastSpeed / FSW**，在目前 OpenGate 原始碼與研究筆記裡沒有同名
符號。唯一符合的開發線是 **HSW**；程式入口是
`projects/open-gate/build/og3k_plan.py` 的 `OG_TARGET=hsw`。
`fastpath.py` 只是 HSW 寫檔路徑的診斷工具，不是另一個模式或產品。

本文件是 HSW 的**當前整合狀態**。完整實驗過程仍保留在：

- `../../projects/open-gate/notes/HSW_240FPS_MODE12.md`：按時間追加的上機紀錄；
- `../../projects/open-gate/build/og3k_plan.py`：目前實際建置邏輯；
- `../explainers/sensor-to-cinemadng.html`：感光元件到 DNG writer 的整體解說。

研究筆記保留了被後來實驗推翻的舊假設。發生衝突時，依下列順序判讀：

1. 較新的、可辨識素材編號的相機實測；
2. 現行 source 的實際預設值與離線檢查；
3. 本文件的整合判定；
4. 舊 README、舊 manifest、較早的推論。

本文用以下標記區分信心水準：

| 標記 | 意義 |
|---|---|
| **H** | 已有相機硬體實測 |
| **O** | 只有離線建置／靜態檢查 |
| **I** | 由現有證據推論，尚未直接驗證 |
| **U** | 未驗證或仍未知 |
| **R** | 已由實驗排除／收回 |

目前工作區沒有 A001 原始片段與完整原始 log；本文的 H 級數字來自既有實驗筆記，
本輪無法從素材重新計算。這是證據保存缺口，不應把筆記數字誤寫成獨立複驗。

## 2. 一句話結論

**HSW 的 mode 12、選取、幾何與高速前段已打通；真正阻塞發布的是外接 SSD 的
8-bit fast-writer 資料位移、full-height 240 fps 的持續吞吐、overflow stop 的
資源回收，以及尚未完成的快門角度／UI／產物治理。**

目前可以證明：

- **H** 2016×672 的 mode 12 確實能以 239.760240 fps 擷取；
- **H** 180 fps / 8-bit 的 request→finish 管線可持續 23.5 秒且零掉格；
- **H** 240 fps / 10-bit 可得到 139 格、0.58 秒的乾淨短 burst；
- **U** 尚無 full-height 240 fps / 8-bit 的乾淨持續錄影；
- **U** 尚無可促版的 HSW 卡、tag 或 release package。

因此，HSW 現在是「核心可行、產品未完成」，不是「240 已完成」，也不是「完全
不能錄 240」。

## 3. 模式、幾何與使用者可見影像

### 3.1 mode 12 的固定事實

| 欄位 | 值 | 證據 |
|---|---:|---|
| sensor mode | 12 / `MONIT1_240` | H |
| metadata index | 42 | O |
| raster | 2016×672 | H |
| hmax / tail / vmax | 330 / 330 / 910 | H |
| frame cycles | 300,300 @ 72 MHz | O，並由實拍格率支持 |
| 實際格率 | 239.760240 fps | H |
| rolling shutter | 3.080 ms | H／計算一致 |
| timing record | `0xC0B59A40` | O |
| stock timing words | `0x014A014A / 0x0006038E` | O |
| metadata nominal fps | 240 | O |
| sensor aspect / crop | 3:2 / full | O |

mode 12 是 3×6 非對稱縮減：

- 水平縮減 3×、垂直縮減 6×；
- 涵蓋完整 3:2 全片幅視野；
- 記錄像素被垂直壓扁 2:1；
- 2016×672 DNG 裁為 2000×662 後，後製需垂直拉伸 2×，約為 2000×1324。

DNG 目前沒有可靠欄位自動告訴後製這個 pixel aspect。發布文件必須明寫；播放器、
GainMap 與鏡頭校正也不能假設方形像素。

### 3.2 HSW 的價值邊界

HSW 在 120 fps 時，2000×662 只有 1,324,000 有效像素；原廠 FHD120 約
2,073,600 像素。HSW 少約 36%，比例還需要後製拉伸。因此 HSW 的產品價值主要是：

1. 239.76 fps；
2. 完整 3:2 視野；
3. 3.08 ms rolling shutter。

若 240 fps 不能可靠成立，HSW 相對原廠 FHD120 的價值很弱。

## 4. 現行建置行為，不是舊 README 所描述的行為

### 4.1 預設值

截至本文件日期，`og3k_plan.py` 的重要預設是：

| 選項 | 預設 | 判定 |
|---|---|---|
| `OG_TARGET` | 建置時指定 `hsw` | HSW 入口 |
| `OG_HSW_FPS` | `per-rate` | 每個選單列有各自 timing |
| `OG_HSW_TOP` | 空 | 不自動提供 180/240 診斷覆寫 |
| `OG_HSW_TOP_SLOT` | `120` | 有 TOP 時預設借 120 格 |
| `OG_HSW_ROWS` | `672` | full-height 路線 |
| `OG_HIRATE` | `all` | 七個高速欄位全寫 |
| `OG_BUFCLASS` | `off` | 必須維持 off |
| `OG_POOLDEPTH` | `off` | pool prototype 不部署 |
| `OG_FMT_GUARD` | `1` | 格式守衛開啟 |

舊 HSW README 所寫「十個格率鍵全部跑 239.76」已不符合現行預設。
`per-rate` 會只改 tail／VMAX，hmax 保持 330，因此 rolling shutter 仍是
3.080 ms。

### 4.2 per-rate timing 表

| 選單／診斷率 | hmax / tail / vmax | 實際 fps |
|---|---:|---:|
| 23.98 | 330 / 330 / 9100 | 23.97602 |
| 24 | 330 / 630 / 9090 | 24.00000 |
| 25 | 330 / 420 / 8727 | 25.00000 |
| 29.97 | 330 / 330 / 7280 | 29.97003 |
| 48 | 330 / 480 / 4545 | 48.00000 |
| 50 | 330 / 540 / 4363 | 50.00000 |
| 59.94 | 330 / 330 / 3640 | 59.94006 |
| 100 | 330 / 600 / 2181 | 100.00000 |
| 119.88 | 330 / 330 / 1820 | 119.88012 |
| 診斷 180 | 330 / 330 / 1212 | 180.01800 |
| 240 | 330 / 330 / 910 | 239.76024 |

要在現行九格 UI 中使用 240，需以 `OG_HSW_TOP=240` 把它掛到既有槽；
預設掛 120，USB-shell 診斷常掛 30：

```text
OG_TARGET=hsw
OG_HSW_FPS=per-rate
OG_HSW_TOP=240
OG_HSW_TOP_SLOT=30
```

後者的 container 仍是 29.97，因此 239.76 擷取會成為約 8×慢動作。這只是診斷
映射，不是完成的 239.76 UI。

### 4.3 原生 239.76 選單仍未產品化

格率 enum↔row 配對表 `0xC2E44EE4` 只有九筆且沒有空位。現行 240 只能借用
既有格率槽；新增 enum 11 的原生列仍缺完整設定層映射。發布前必須選擇：

- 正式擴充／私有化 picker map；或
- 明確把某個既有列重新命名並限定為 HSW 專卡。

不能讓 UI 顯示 29.97／119.88，卻不告知使用者感光元件正在跑 239.76。

## 5. 已完成與已驗證里程碑

### 5.1 感光元件與前段

- **H** HSW 可由選單選中，FLAG、selector 與 mode 12 命中；
- **H** DNG 幾何為 2016×672、active crop 2000×662；
- **H** donor p13 的 2016×1344 window mismatch 已以 `write_window` 修正；
- **H** p13 補七個 high-rate 欄位後，>60 fps 的早期凍結已解除；
- **R** RWZM 對 HSW 記錄幾何沒有作用，不能用它把 672 縮成別的 raster。

七個 high-rate 欄位的整組已實機成立，但最小必要子集尚未二分。現階段應保留
`OG_HIRATE=all`，不要為了「精簡」重新引入 >60 fps 凍結。

### 5.2 180 fps / 8-bit 持續路徑

關閉錯誤的 `OG_BUFCLASS=29` 後，A001_144..149 六段皆
`req_cnt == fin_cnt`，請求數為：

```text
79 / 180 / 340 / 1317 / 4232 / 2137
```

A001_148：

| 指標 | 結果 |
|---|---:|
| frames | 4,232 |
| wall time | 23.511 s |
| capture rate | 180.0 fps |
| data | 6.07 GB |
| sustained throughput | 258 MB/s |
| `sdb` | 20–22 |
| 同時在途 | 約 42–43 frames |
| dropped requests | 0 |

這證明 180 fps 的排程與 SSD request/finish 管線可持續運作。**但 A001_148 的
8-bit DNG 仍有 §7 的位移；「零掉格」不等於「影像檔乾淨」。**

### 5.3 第一段乾淨 240 burst

A001_160，10-bit、239.760 fps、固定快門 1/250：

| 指標 | 結果 |
|---|---:|
| req / fin | 139 / 139 |
| 壞格／位移 | 0 / 139 |
| 真實擷取時間 | 約 0.58 s |
| 寫入觀察窗 | 246 MB / 0.776 s |
| 觀察吞吐 | 317 MB/s |
| 最低 `sdb` | 7 |

固定快門被硬體夾為 1/250，也支持感光元件確實在 239.76 fps。這段證明 240
不是完全不可用，但 pool 已接近抽乾，不能外推成可持續錄影。

## 6. 資料率模型：必須使用整個 DNG，而不是只算像素

舊文件中的 317／397／476 MB/s 只算 active pixel payload，沒有計入每格
79,872 bytes 的 header。SSD 與 pool 驗收必須使用實際檔案長度：

| bit depth | pixel payload / frame | full DNG / frame | 239.760240 fps |
|---|---:|---:|---:|
| 8-bit | 1,354,752 B | 1,434,624 B | 343.97 MB/s |
| 10-bit | 1,693,440 B | 1,773,312 B | 425.17 MB/s |
| 12-bit | 2,032,128 B | 2,112,000 B | 506.37 MB/s |

實驗中唯一接近飽和的直接量測是約 317 MB/s。它不一定是絕對硬上限，但
full-height 8-bit 的 343.97 MB/s 比它高約 8.5%。因此：

> 「修掉 8-bit 位移，full-height 240 就完成」目前證據不足。

recorder log 的 MB/s 欄使用單格 request→finish 延遲換算，曾把實際 258 MB/s
顯示成約 367–377 MB/s。正式吞吐只能用整批 bytes ÷ wall time 計算。

## 7. 主要瓶頸與根因狀態

### 7.1 P0：8-bit＋外接 SSD 的 direct-I/O 位移

正常 full-height 8-bit DNG 是 1,434,624 bytes。壞格的 TIFF/DNG 內容會整塊
往後移：

| shift | 後果 |
|---:|---|
| 0 | 正常 |
| 7,168 B | 尾端少約 3.55 rows |
| 65,536 B | 尾端少約 32.5 rows |

半行位移還會翻轉 Bayer phase，使底部顏色錯亂。A001_148 的分布：

```text
{0: 1236, 7168: 1417, 65536: 1579}
pattern: good 16 / zero 27 / good 5 / zero 32 / good 16 / zero 32
period: 128 frames
good: 29.2%
```

已證明：

- descriptor 的 header length 始終是 `0x13800 = 79,872`；
- SSD A001_166 中，log 的 128 格全是 `0x13800`，素材卻有 65% 位移；
- 零不是 producer／descriptor 寫入，是外接 writer 後段自行插入；
- 8-bit SD 對照有 26/26 無位移；目前範圍已縮到外接路徑；
- `cdng_fix.py` 只能切掉前導零並補尾端，**被截掉的影像無法恢復**。

相關 fast path：

```text
FUN_c069AD80
  -> open flags 0x1806
  -> filesystem type 10
  -> FUN_c0646BF8(fs, 4, dirent+0x5C8, 2)   # write-cache mode 2
  -> FUN_c0646050(...)                       # cache ordering/flush
  -> FUN_c0645D80(...)                       # actual device write
```

強制 `FUN_c069AD80` 回 0、繞過 fast path，實機幾乎立即凍結。此路徑對高速
寫入是必要條件，修法只能是在 fast path 內把 offset／length／padding 算對。

### 7.2 P0：吞吐與延遲尖峰

即使資料位移修好，full-height 8-bit 仍需約 344 MB/s。現有證據只有：

- 258 MB/s 可持續 23.5 秒；
- 317 MB/s 時 pool 已降到 `sdb=7`；
- 尚無 ≥344 MB/s 的乾淨持續量測。

必須分開兩件事：

1. 平均持續吞吐是否足夠；
2. SSD 126–183 ms 等級的 latency spike 是否會抽乾 96-entry pool。

只增加 pool 可能吸收尖峰，不能補足平均頻寬。

### 7.3 P0：overflow stop、descriptor 與 RAW leak

原廠 pool 為 `MPoolFixed<4380U,96U>`：

| 欄位 | 值 |
|---|---:|
| object pointer | `0xC2F1F3D8` |
| vptr | `0xC0B99474` |
| node stride | `0x1128` |
| free-count offset | `+0x112C` |
| usable in-flight threshold | 約 94 |

一次 vbuf stop 的實測結果：

- free count 96 → 52；
- 44 個 descriptor 沒回到自由串列；
- RAW used 增加 63,441,728 bytes；
- 44 × 1,441,792 = 63,438,848 bytes，與量測吻合。

`FUN_c0377118 -> FUN_c0381008` 只把在途請求摘鏈，沒有經
`FUN_c0375030` 歸還 descriptor 與 RAW buffer。`fin == req` 的乾淨停止不漏；
overflow／vbuf stop 會漏，且目前只有拔電池冷開能確實恢復。

SSD 上的自動停止還可能把 USB 與 media subsystem 一起拖死，導致事後無法讀現場。
在 stop cleanup 修好前，任何「長錄成功率」都會被前一段留下的 pool 狀態污染。

### 7.4 P1：per-rate 模式的 shutter-angle 錯誤

mode 12 metadata `+0x40=240`。實機已證明，即使 timing 改為 180.018：

```text
角度模式 180° -> ExposureTime 1/500
固定快門要求 1/60 -> 被實際 frame period 夾成 1/200
```

角度換算讀的是 donor metadata 240，不是實際 sensor timing。現行 source 又把
HSW 排除在 `_ANGLE_FIX_TARGETS` 外，所以：

- 真實 240：stock 240 計算正確；
- per-rate 23.98–180：角度曝光全部有錯誤風險；
- container fps 也不能拿來代替 sensor fps。

正確方向是由 format-table hit 鎖存**實際 sensor fps class**，讓四個既有
shutter-angle callsite 讀此 latch；不可修改 mode 12 共用 metadata。

若第一版只做 240-only 卡，可先封鎖其他 per-rate 選項，縮小風險面；若保留
per-rate UI，這個修正是發布阻斷項。

### 7.5 P1：GainMap／鏡頭校正

OpcodeList3 GainMap 以方形像素、正常 aspect 計算，卻套在 2016×672 的 2:1
垂直壓縮影像，造成右側暗矩形。關閉 lens/profile correction 可移除現象，
但機內正式修法尚未完成。

候選方向：

- 依 HSW 有效視野與 pixel aspect 重算 GainMap coverage；或
- HSW DNG 明確省略不適用的 GainMap tag。

### 7.6 P1：產物與文件不是可稽核真相來源

目前 HSW source 位於 `../../projects/open-gate`，該目錄不在 Git repo。
`build_og3k_gyro.py` 雖有 `hsw: 0.1.0test` 字串，但沒有 HSW tag 或 release。

現行 HSW builder 還會產生互相矛盾的說明：

- manifest 寫「沒有相機結果」；
- README 寫「十個 format rows 全部 239.76」；
- source 預設其實是 `per-rate`；
- manifest 沒有明列 `OG_HSW_TOP`、slot、rows、pool depth 等關鍵輸入；
- 同一份 README 可同時出現在 672 與 448 兩個不同 binary。

正式候選包必須把建置環境、plan digest、payload digest 與相機證據分層記錄，
不能再靠目錄名或 README 敘述猜是哪一份 build。

## 8. 候選解法

### 8.1 短期可行性路線：448-row、64 KiB 對齊

現行 source 已提供 `OG_HSW_ROWS=448`：

```text
header                  79,872
2016 × 448 × 1 byte    903,168
total                   983,040 = 15 × 65,536
240 fps                 235.69 MB/s
active crop             2000 × 438
```

優點：

- 每格正好是 64 KiB 整數倍，直接測試 padding／alignment 假設；
- 資料率由約 344 降到約 236 MB/s，低於已證明的 258 MB/s 持續路徑；
- 不必先深入 Thumb writer 即可快速得到 go/no-go 證據。

代價：

- 少 1/3 垂直視野；
- 垂直拉伸 2× 後約為 2.25:1；
- 不再是原本完整 3:2 HSW；
- 目前只有 source 與離線建置，**沒有相機實測**。

第一次 A/B 必須使用原廠 96-entry pool，保持：

```text
OG_BUFCLASS=off
OG_POOLDEPTH=off
OG_HIRATE=all
OG_HSW_ROWS=448
```

不要把 448、pool=300 與其他 writer patch 一次合併，否則無法知道是哪一項生效。

### 8.2 保留完整 672 行：修 mode-2 writer

下一個有資訊價值的觀測點是 Thumb hot path `FUN_c0645D80`，記錄每次裝置寫入的：

- logical／physical offset；
- length；
- cache block size；
- first-segment/header 的對齊決策；
- 產生 7,168 與 65,536 padding 前後的狀態。

在部署 tracer 前，工具必須先具備：

1. Thumb-2 trampoline，不能使用現行 `ogdev.py arm` 的 ARM `B`；
2. 掛鉤前精確比對原指令／before-image；
3. payload 先上傳、逐字 readback；
4. 透過 ROM `0xC000E91C` 後接 `0xC000EABC` 發布 D/I cache；
5. hook 最後才武裝；
6. bounded、preallocated logging，hot path 不配置記憶體；
7. 無論成功／例外都還原原指令，再做 cache publish 與 readback。

現行 `ogdev.py` 使用 `mem_set` 後直接 arm，沒有上述 cache publish，也只會產生
ARM branch；它不能直接拿來掛這個 Thumb writer。

目標不是關掉 fast path，而是找出 mode 2 把第一段靠右或補成下一個 64 KiB
邊界的算術，修正後保留 direct-I/O 效能。

### 8.3 停止清理先於 pool 擴充

正式方向：

1. 找出 vbuf stop 中每個 in-flight request 的所有權；
2. 依原廠正常 finish 的次序歸還 descriptor；
3. 釋放／歸還對應 RAW buffer；
4. 清空 queue 後驗證 free count 回到 96；
5. 重複 start/stop，不得累積 used RAW 或 descriptor loss。

在此之前，overflow stop 後唯一可靠操作規則仍是 battery-out cold boot。

### 8.4 `OG_POOLDEPTH=300` 目前不是解法

`poolinit.S` 的現況有至少三個阻斷項：

1. 仍從 RAW / NonCacheBuffer id 10 配 descriptor pool；先前同一路線已在 240
   下拖死 USB worker，而 descriptor 是高頻 CPU 結構，不應放 non-cache；
2. `push {r4,r5,r6,r7,lr}` 後立即呼叫韌體函式，call boundary 的 SP 只有
   4-byte alignment，不符合 8-byte ABI；
3. 一次性 flag 在配置前就設為完成；配置失敗可能留下半初始化狀態。

正確 prototype 必須：

- 從與原廠相同的 cached C++ heap 配置；
- 在韌體自己的正確 task context 執行；
- 保持 8-byte stack alignment；
- 完整初始化、guard／count／鏈結驗證後，才原子換 pointer 與 bounds；
- 失敗時不改任何全域狀態並可完整 rollback。

而且必須先修 stop leak；否則 pool=300 只把失敗延後。

## 9. 已排除或不得再走的方向

| 舊方向／敘述 | 現在判定 |
|---|---|
| HSW 從未上機 | **R**；已有多輪 119.88／180／240 實測 |
| 十列一律跑 239.76 | **R**；現行預設是 per-rate |
| HSW 一律不需 angle fix | **R**；只對真實 240 成立 |
| 317/397/476 MB/s 是完整寫檔需求 | **R**；未含 79,872 B header |
| 修掉 8-bit 位移，240 就必然完成 | **U**；仍缺 ≥344 MB/s 持續證據 |
| `OG_BUFCLASS=29` 增加緩衝可救 240 | **R**；它把外接寫入串列化到約 5 fps |
| 關閉 fast path 可避免位移 | **R**；實機立即凍結 |
| SSD Standard mode 不會凍 | **R**；Standard／Custom 都曾凍結 |
| RWZM 可替 HSW 改記錄高度 | **R**；實機無作用 |
| shutter-angle 可量真實 sensor fps | **R**；它讀 metadata 240 |
| 目錄先配置 26/105 MB 可判定成功失敗 | **R**；配置早於實際寫入 |
| recorder log 的 MB/s 是持續吞吐 | **R**；它是單格延遲換算 |
| 加深 pool 就能解決 | **R**；不能修平均頻寬或 stop leak |
| NonCache RAW 適合 descriptor pool | **R**；高頻存取會拖垮 worker |

## 10. 建議執行順序

### Gate 0：建立單一真相來源

- 把 HSW source 納入 Git 或可重建的 patch set；
- 修正 HSW README／manifest 的「未上機」與「全列 240」；
- manifest 加入所有 `OG_*` 輸入、firmware hash、plan hash、payload hash；
- 決定首版是 240-only 還是 per-rate；
- 240-only 可暫避 angle 問題；per-rate 必須先修 sensor-fps latch。

**出口條件：** 同一份設定只能生成一個可識別產物；看到 manifest 即可重建。

### Gate 1：448-row 單變量 A/B

建議順序：

1. cold boot；
2. 240 / 8-bit / 448 rows / stock pool；
3. 先錄短段，確認可停止；
4. 2 秒、10 秒、30 秒逐級；
5. 每段分析所有 DNG，不抽樣；
6. 每段前後讀 descriptor free count、RAW used 與 stop reason；
7. 任一 overflow／freeze 後 cold boot，不沿用污染狀態。

**出口條件：** 30 秒、零位移、零截尾、`req==fin`、pool 回復、正常停止。

### Gate 2：產品決策

- Gate 1 成功且可接受 2.25:1：可建立「HSW-Lite」測試候選；
- 必須保留完整 3:2 視野：進 Gate 3；
- Gate 1 仍有位移：也進 Gate 3，並否定「只要 64 KiB 整除」的假設。

### Gate 3：完整 672 writer 修正

- 完成安全 Thumb tracer；
- 量 `FUN_c0645D80` offset／length；
- 找到 mode-2 padding 的生成位置；
- 以最小 patch 修正，不繞過 fast path；
- 先在低率驗證，再升到 180／240；
- 獨立證明 SSD sustained ≥344 MB/s。

### Gate 4：停止清理與 pool

- 先修 descriptor／RAW cleanup；
- 做至少 10 段 start/stop，不得遞減；
- writer 已正確但仍因 latency spike 抽乾時，才重做 cached pool prototype；
- pool prototype 必須獨立 A/B，不與 writer fix 同時首次上機。

### Gate 5：產品與回歸

- 原生 240 UI／清楚的借槽標示；
- shutter-angle；
- GainMap／profile correction；
- HSW、FHD、UHD、STILL、CINE↔MOV；
- cold boot／拔卡／換 SSD；
- 8/10/12-bit 的支援矩陣與拒絕條件；
- 正式 README、manifest、promotion checklist、tag。

## 11. 相機驗證矩陣

### 11.1 每次 build 必記

- firmware version 與 SHA-256；
- Git commit；若 source 尚未入 Git，記完整 patch digest；
- 所有 `OG_*` 環境變數；
- generated plan word count／digest；
- AutoRun／fpSup.BIN／UI payload SHA-256；
- SSD 型號、容量、cluster size、write mode；
- bit depth、container rate、實際 sensor timing、rows；
- cold-boot 起始狀態。

### 11.2 每段 take 必收

- take id；
- request count／finish count；
- frame files count；
- first/last request timestamp；
- 整批 bytes／wall time；
- `sdb` 最低值與 stop reason；
- descriptor free count 前／後；
- RAW used 前／後；
- 每格 TIFF 起點、StripOffsets、StripByteCounts、檔尾長度；
- shift 分布 `{0, 7168, 65536, other}`；
- EXIF ExposureTime；
- 停止後 USB、媒體、播放與下一段錄影是否正常。

### 11.3 建議測項

| ID | 條件 | 目的 |
|---|---|---|
| T0 | cold boot、HSW 選取、不錄影 | UI、picker、mode／profile 基線 |
| T1 | 固定慢快門要求，讀 EXIF clamp | 證明真實 sensor fps |
| T2 | SD、8-bit、低率 | writer 對照 |
| T3 | SSD、448、240、2 s | alignment 可行性 |
| T4 | SSD、448、240、10/30 s | sustained 與 stop |
| T5 | T4 重複至少 10 段 | leak／生命週期 |
| T6 | SSD、672、低率／180 | full-height writer 回歸 |
| T7 | SSD、672、240 | 最終 full-height gate |
| T8 | 各 per-rate、180° angle | shutter-angle latch |
| T9 | lens correction on/off | GainMap |
| T10 | FHD/UHD/STILL/CINE↔MOV | 非 HSW 回歸 |

真實 240 不能只靠 angle 模式判定。可使用：

- 固定快門要求超過 frame period，EXIF 被夾到約 1/250；
- 實際 frame count ÷ capture wall time；
- 已校準的 request timestamps。

## 12. Release exit criteria

HSW 不得在下列條件未全部滿足前稱為 release candidate：

- [ ] 明確、可重建、已版本控制的 source 與 manifest；
- [ ] 真實 239.760240 fps 已以固定快門／計時證明；
- [ ] 目標模式連續至少 30 秒；
- [ ] 約 7,193 格全部檢查，零 shift、零截尾、零壞 TIFF；
- [ ] `req_cnt == fin_cnt`；
- [ ] descriptor 與 RAW 使用量在每段後完全回復；
- [ ] 至少 10 段 start/stop 無累積；
- [ ] 無 USB／media freeze，停止後可立刻錄下一段；
- [ ] 吞吐以整批 wall time 達標；
- [ ] 不能依賴 `cdng_fix.py` 才得到可用影像；
- [ ] shutter-angle 與實際 sensor fps 一致；
- [ ] GainMap／鏡頭校正不產生暗矩形；
- [ ] UI 不誤報 rate／resolution；
- [ ] FHD、UHD、STILL、CINE↔MOV 回歸通過；
- [ ] 完整 cold-boot rollback 與移除卡片後 stock 行為通過；
- [ ] 至少兩顆 SSD 或明確列出通過的唯一媒體相容表。

若產品選擇 448-row，release 名稱與 README 必須明示裁掉 1/3 垂直視野，不得沿用
「完整 3:2 HSW」描述。

## 13. 2026-09-23 離線稽核結果

本輪只做離線、唯讀相機外部稽核；沒有部署、沒有操作相機。

通過：

| 檢查 | 結果 |
|---|---|
| 預設 HSW `Plan().check()` | PASS，962 writes |
| 240/slot30/448 `Plan().check()` | PASS，962 writes |
| HSW loader tests | 10/10 PASS |
| 240/slot30/448/pool300 static plan | PASS，1045 writes；**不代表可安全上機** |
| 448 release-style build | PASS，80 sections |
| 672 release-style build | PASS，80 sections |

稽核候選設定：

```text
OG_TARGET=hsw
OG_HSW_FPS=per-rate
OG_HSW_TOP=240
OG_HSW_TOP_SLOT=30
OG_BUFCLASS=off
OG_POOLDEPTH=off
OG_HIRATE=all
OG_FMT_GUARD=1
OG_HSW_ROWS=448 or 672
```

產物：

| candidate | fpSup.BIN SHA-256 | used bytes |
|---|---|---:|
| 448 rows | `519015a8694c9991f5c2f9ab5f4001a5837e0f5a92cad7ba0544572bad79ae29` | 15,192 |
| 672 rows | `9b247805b27469b5f06c7f3bf29d5d50c97c7e36fe4a951f5a4d8bd7ff7ea60c` | 15,268 |

兩者 AutoRun SHA-256 都是
`8682a97012f8e617e7982dfe0599c7c760f8eeaf184fbc944dac696f7ff38812`。

重要異常：

- 兩種候選的 generated README SHA-256 完全相同；
- README 沒有反映 448／672 差異；
- manifest 只從 UI asset 間接顯示 `font_S_imageSize_438`／`662`；
- manifest 沒有完整列出 build environment；
- angle manifest 仍聲稱 HSW timing 不變，與 `per-rate` 預設衝突。

以上候選只存在於稽核時的暫存輸出，不是 release artifact。

Git 稽核背景：

- `fpSup` 當時 HEAD：`44fda85`；
- HSW build／notes 位於 Git 外的 `projects/open-gate`；
- `fpSup/explainers/sensor-to-cinemadng.html` 的最新 HSW 摘要尚未提交；
- 正式 `fpSup/opengate/README.md` 仍是 OG3K 發布頁，不代表 HSW。

## 14. 未決問題

1. 448-row 是否真的讓外接 mode-2 writer 零位移？
2. 448-row 連續 30 秒是否能保持 pool 與 stop 正常？
3. full-height SSD 的持續天花板是否能超過 344 MB/s？
4. 7,168／65,536 padding 的精確 offset／length 算術在哪一層產生？
5. SSD host driver 的空 `XC_MediaDriverHost::v16` 是否與 freeze 有因果關係？
6. stop cleanup 能否安全歸還在途 descriptor／RAW，而不 double-free？
7. 239.76 原生 picker row 要擴表、替換，還是只做獨立 240-only UI？
8. HSW per-rate 的實際 sensor-fps latch 應掛在哪個最小 callsite 集合？
9. GainMap 應重算還是省略？
10. HSW source 要搬入 `fpSup`、submodule，還是以可重建 patch set 管理？

## 15. 當前推薦決策

### 若目標是最快得到可用 240

先做 448-row、stock pool 的單變量上機 A/B。成功後把它明確定位為裁幅
HSW-Lite，再補 stop、angle、GainMap 與回歸。

### 若目標是保留完整 3:2 視野

不要再調 canvas／RWZM 或關 fast path；先把安全 Thumb tracing 工具補齊，
定位並修正 mode-2 writer，再證明 ≥344 MB/s 持續吞吐。

### 無論選哪條

都不要啟用現行 `poolinit.S`，不要把舊 README 當狀態來源，也不要在 overflow
後不 cold boot 就繼續比較下一段。
