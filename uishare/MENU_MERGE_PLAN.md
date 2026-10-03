# 選單合併方案：一份 UI 的 Python，三種「添加」

2026-10-03。規劃，尚未實作（字串池提示除外，已完成）。背景與現況：`research/ui/MERGE_BY_ADDITION.md`、
本目錄 `PLAN.md`、`research/ui/UI_SERVICE_INVENTORY.md`。

目標（使用者，2026-10-03）：
- 一份**以 UI 為主的 Python**，讓 sup 宣告「我要加什麼」，而不是自己去改原廠資源；
- **add 選單列**：Lossless 加「Lossless RAW」列；
- **add 選項**：OG3K / OG2K 在解析度清單加自己的選項；COLOR 清單加 RAW（raw-view）；
- 要能擴充：之後的 sup（Sensor Lab、lcdflip、…）用同一套，不用每次為合併另寫一套。

---

## 1. 架構：Python 當大腦，相機上只有一個笨的套用器

```
            建置時（host，每個 sup 各自跑）                     開機時（相機，每個 sup 的 entry 依序跑）
 ┌───────────────────────────────────────┐            ┌────────────────────────────────────────┐
 │ fpSup/uishare/ui/  (Python 套件)      │            │ fpSup/uishare/ui_apply.c  (C，小)       │
 │  Stock: 從釘死的韌體映像讀原廠頁、CSV、 │  FPUI 區塊 │  照 FPUI 的指令一條條做：               │
 │         字串池、上限、ID              │ ─────────▶ │  - 找/建共用的頁面複本、CSV 複本        │
 │  Patch: add_row / add_option / …      │ （放進 sup │  - 附加記錄、重配 ID、加計數            │
 │  → 產生 FPUI（片段 + 指令 + 提示）    │  的 BIN）  │  - 字串走 uis_intern_hinted             │
 │  → manifest（給人看、給測試比對）     │            │  - 發放選項 index、列的 Y               │
 └───────────────────────────────────────┘            └────────────────────────────────────────┘
```

- **所有需要理解 NBU/CSV 格式的工作都在 Python**：找 donor、複製記錄、算上限、算字串提示、
  列出每一個要跟著加的計數。它有整個原廠映像可以查、有測試可以跑，出錯在建置時就停。
- **相機上的 C 只做機械動作**：複製、附加、在指定欄位加一個數、寫入發放到的值。它不懂「一列」是什麼。
  這讓 C 小而穩、很少需要改；新功能多半只是 Python 用既有動作組出新的片段。
- **在開機時組合，不在合併網頁組合**：每個 sup 單獨建置、單獨發布；合併網頁只放段落、什麼都不用懂（維持現狀）。
  兩個 sup 誰先誰後都得到正確結果，因為所有座標（ID、index、Y、字串位移）都是開機時才發。

### 為什麼是「附加 + 加計數」

Lossless 現在的頁面已經證明了這個形式可行 [實機]：複製出來的記錄**附加在頁面最後**、用物件 ID 與父物件連起來，
再把父物件的子數量 +1、頁首 `0x10002` 的預算陣列加上新記錄的需求、ModeChange 加一個根。
沒有在中間插入、沒有搬動既有記錄的位移。第二個 sup 要在同一頁加列，只是再做一次同樣的事。

## 2. Python 端：`fpSup/uishare/ui/`

既有零件直接收進來，不重寫：`research/ui/tools/nbu_probe/nbu_format.py`（記錄格式、上限）、
`nbu_components.py`（物件樹）、`nbr_stock.py`（CSV 與 NBR 目錄）、`lossless/menu/build_menu_candidate.py`
（複製同頁列、改名、預算、ModeChange）、`pack_menu_file.py`（字串提示）、OG 的 `build_og3k_ui_candidate.py`（QS 記錄）。

```python
from uishare.ui import Stock, Patch

stock = Stock.load()                       # 釘死的 out/MAIN_c0000000.bin，SHA 不符就停
p = Patch('lossless', stock)

# add 選單列 —— Lossless
p.page('MainB2').add_row(
    clone='B2_6',                          # 同頁的列才行（fp-native-ui §8b）
    title='Lossless RAW',
    variable='MV_fpLossless',              # 私有 app 變數，記憶體裡，每次開機 0
    values=('0080', '0081'),               # 關閉 / 開啟，用原廠字
    footer='Footer05', no_jump=True)

blob, manifest = p.build()                 # FPUI 區塊 + 說明
```

```python
# add 選項 —— OG3K（OG2K 同一行，只差 label/icon）
p = Patch('og3k', stock)
opt = p.list('B2_5_4').add_option(         # 錄影設定 → 解析度
    label='OG3K', summary='OG3K',
    stock_value='UHD',                     # 設定區只存原廠合法值（第一守則），我們的狀態靠記號
    marker='og3k.selected',
    qs=True)                               # 需要 QS 圖磚；見 §5

# add 選項 —— COLOR 的 RAW（raw-view）
p = Patch('raw-view', stock)
p.list('ListColorButtonMenu', variants=('_EXCL', '_EXCL2')).add_option(
    label='RAW', icon='rv_raw_icon.xci',
    stock_value='OFF', marker='rawview.selected')
```

每個操作在 Python 裡展開成它真正需要的所有改動。以 `add_option` 為例（清單都來自 fp-native-ui §2 實機規則）：

| 要動的 | 規則 |
|---|---|
| CSV 加一列（每個變體都加） | 結尾 CRLF、依表頭找欄 |
| controller max（**全部**同列數的上限，含 IconChange） | §2 規則 5、9 |
| ListItem 實例數 = max(8, 列數) | 規則 7 |
| kind 16 popup 的每值 `controlValueEvent` + `controlFocus` | 規則 8 |
| 收合摘要 IconText 子物件 + MenuItem_Select prop 0x12 | §7 摘要規律 |
| row↔enum、enum↔state 表 | §4（movw/movt 改指，段落宣告以便關機寫回） |
| 選項的 index | **不寫死**：開機時發放，`ALLOC_INDEX` |

Python 把「哪一個欄位、加多少、以哪個原廠物件為錨」全部算好；相機只照做。

### 擴充性

- **新操作**（`hide_row`、`set_option_enabled`、`add_page`…）：多半只是 Python 新函式，用既有動作組成，**C 不用動**。
- **新動作**（C 才需要改）：FPUI 有版本號；不認得的動作 → 整個 sup 的 UI 添加跳過、其他照常（uishare 規則 4）。
- **新 sup**：不需要知道其他 sup 存在；只要用這個套件。
- **新韌體版本**：`Stock` 換映像，錨點（原廠物件 ID + 指紋）重新驗證；對不上就建置失敗。

## 3. 相機端：`ui_apply.c` 與 FPUI 格式

### FPUI 區塊（每個 sup 一個，放在自己的 BIN 段落裡）

小。一列約幾 KB、一個不含 QS 的選項約 1–3 KB，**不再需要像 FPLM 那樣把整頁 180 KB 塞在 BIN 尾段**，
也就不再需要 0xF000 尾段與讀取上限的特別處理（Lossless 改用後，尾段機制可以退休）。

```
"FPUI" | 版本 | 指令數 | 字串表（含原廠提示） | 片段資料 | 指令…
```

### 指令（v1，刻意少）

| 指令 | 做什麼 |
|---|---|
| `GUARD 位址, 原廠字…` | 先驗證，任何一條不符 → 這個 sup 的 UI 全部不裝 |
| `PAGE 頁名, 原廠位移` | 取得這頁的**共用複本**（沒有就從 RAM 的原廠頁複製一份，留空間、帶 `FSPG` 標頭），之後的指令作用在它上面 |
| `APPEND 片段, 本地ID數` | 附加記錄；本地 ID 換成這頁發放的新 ID |
| `ADD32 / ADDF32BE 錨點, 欄位, 值` | 計數、上限、預算、子數量加上去（錨點 = 原廠物件 ID 或本地 ID） |
| `STR 欄位, 字串#` | `uis_intern_hinted` 拿位移寫進去 |
| `ALLOC 名稱 → 槽` | 從這頁/這個清單的計數器發一個號（選項 index、列的 Y），寫進指定欄位，也回傳給 sup |
| `CSV 檔名, 列模板` | 取得這個 CSV 的共用複本（`FSCV` 標頭），附加一列（模板裡的 `${index}` 換成發到的號） |
| `DONE` | 全部成功才切換：頁面 `entry+08`、CSV 改指表 |

- **全有或全無**：先在複本上做完，最後才切換指標。失敗只影響這個 sup 自己的 UI。
- **不掃描**：所有位置 Python 都算好；字串用提示（§6）。開機成本 = 複製一頁（一次）+ 片段大小。
- **共用複本自我描述**：`FSPG`（頁）、`FSCV`（CSV）標頭和 `FSPL` 一樣：magic、版本、容量、使用長度、下一個 ID／index。
  第二個 sup 找到標頭就接著加。

### 單一主人的 hook：可串接的跳板

有兩個 hook 點，同一時間只能有一個主人：
- **取檔位置 `C05E5BEC`**（raw-view 的 H1）→ CSV 改指；
- **記錄解析 `C05E6400`**（OG 的 QS）。

約定：第一個需要的 sup 裝一個帶標頭的跳板（`FSHK` | 版本 | handler 數 | handler 表），之後的 sup 把自己加進表，
不再「看到不是原廠字就退讓」。CSV 改指的 handler 由 `ui_apply.c` 自己提供（不是 sup 各寫一個），
所以 raw-view 的 H1 會被取代。

### 函式庫，不是獨立的 sup

`ui_apply.c` + `ui_pool.c` 估計 3–5 KB，編進每個需要的 sup（現在的 uishare 做法）。
獨立的「UI 服務 sup」的好處（一份程式碼）不足以抵銷它的成本（每張卡都得帶、要凍結 ABI、要改載入契約）；
真正需要單一主人的只有上面兩個 hook，用跳板約定解決。這個判斷在 v1 做完後可以重新評估。

## 4. 三個使用者的改法與「沒有退步」的證明

**每一個改寫的驗收標準：單獨一個 sup 的結果，要和今天在相機上測過的位元組一模一樣。**
這樣改架構不會帶來新的相機風險；合併才是新東西。

| sup | 今天 | 改成 | 單獨時要逐位元相同的對象 |
|---|---|---|---|
| Lossless | 整頁 MainB2 換成複本（FPLM 尾段 196 KB） | `add_row(MainB2)`，FPUI 幾 KB | 開機後的 MainB2 頁 = 今天 FPLM 裡的頁（字串位移在換算後相同） |
| raw-view | H1 換掉 3 個 CSV；9 個版面字原地改；COLOR 第 17 列寫死 | `add_option(ListColorButtonMenu)` + 共用 CSV 改指；版面字改成 `ADDF32BE` | 3 個 CSV 內容、9 個字的值、GUI→enum 對照 |
| OG3K / OG2K | B2_5_4 第 3 列原地改；~360 筆 QS 記錄經 `C05E6400`；字串別名 hook | `add_option(B2_5_4)`；字串改走 `uis_intern`（拿掉 `C05E5B58`）；QS 見 §5 | B2_5 的 CSV 與上限；QS 記錄 |

raw-view 和 OG 的「選擇了我的選項之後要做什麼」（錄影核心、增益、顯示）不屬於 UI，不變；
它們改成從 `ALLOC` 回傳的 index 判斷「使用者選的是不是我」，不再假設自己是第 17 列 / 第 3 格。

### 第一守則在 add_option 裡

選項的值**不能**用原廠沒有的設定值寫進設定區。`add_option` 一律要求 `stock_value`：
設定區只存那個原廠合法值，「選的是我」由記憶體記號表示（Sensor Lab 240 已用這個做法，實機通過）。
⚠ fp-camera-control 筆記記載 OG3K 的解析度值是 **4**（UHD 3、FHD 2）。如果 4 就是原廠有定義、選單不給選的
CUSTOM，那 OG3K 正在做第一守則要求先詳盡調查的事。**待核對**；改用 `add_option` 時一併改成「原廠值 + 記號」。

## 5. QS（只有 OG 需要，最貴）

QS 版面開機就解析並常駐；一個新解析度選項在 QS 牽動約 360 筆記錄（152 筆在 QS）。
v1 **不把 QS 通用化**：OG 先保留自己的 QS 記錄表，但改掛在 `C05E6400` 的共用跳板上（不再獨佔），
其餘 Settings 部分改用 `add_option`。等第二個需要 QS 的 sup 出現，再把 OG 的做法抽成 `add_option(qs=True)`。

## 6. 開機速度

- **字串池（已完成，2026-10-03）**：`uis_intern_hinted` + FPLM v3（每個引用帶原廠提示）。Lossless 的 7 個不同字串，
  以前每個都把 17 萬 bytes 的原廠池整個掃一遍，現在原廠有的只核對 n+1 bytes，原廠沒有的只看附加區。
  測試：`test_ui_pool` 20 項（4 個新 mutation）、`test_menu_page` 32 項（新 mutation 1 個）、card emulation、lcdflip 都過。
  **相機上還沒量**：改前 `LOAD_DONE_US` 20.05 s，改後待量（同一張卡、各開機 3 次）。
- **FPUI 之後**：不再讀 196 KB 尾段；頁面複本從 RAM 複製（一次 memcpy）。

## 7. 要先在相機上證明的事（不證明就不往下做）

| # | 問題 | 最小實驗 |
|---|---|---|
| P1 | 兩個 sup 先後附加到同一頁，頁面正常 | MainB2 加兩列（Lossless + 一個假的第二列），兩種載入順序 |
| P2 | CSV 改指（共用跳板在 `C05E5BEC`）可取代 raw-view 的 H1 | 單獨 raw-view，CSV 改走 `FSCV` 複本；結果與今天相同 |
| P3 | 執行期把一個選項加進 B2_5_4（Settings，不含 QS） | 用 `add_option` 加一個無作用的測試選項，看得到、選得到、設定區只存原廠值 |
| P4 | `C05E6400` 跳板串兩個 handler | OG 的 QS handler + 一個只計數的 handler |

另外：**raw-view 在 lossless+gyro+og3k 卡上選單壞掉的原因還沒找到**（2026-10-03，hook 與資料都在，見對話紀錄）。
P2 之前要先二分出原因，否則新機制可能把同一個問題帶過去。

## 8. 順序與規模

| 階段 | 內容 | 規模 | 相機 |
|---|---|---|---|
| 0 | 字串池提示 | 完成 | 待量開機時間 |
| 1 | raw-view 合併問題二分 | S | 要 |
| 2 | `ui/` 套件骨架：`Stock`、FPUI 格式、`ui_apply.c`（PAGE/APPEND/ADD/STR/ALLOC/DONE）+ 主機測試 + unicorn | M | — |
| 3 | `add_row`，Lossless 改用；單獨時位元相同；P1 | M | P1 |
| 4 | 共用跳板 + `CSV`；raw-view 改用 `add_option`；P2 | M–L | P2 |
| 5 | `add_option` 進 B2_5_4；P3；OG3K/OG2K 的 Settings 部分改用；拿掉字串別名 hook | L | P3 |
| 6 | OG 的 QS 掛到跳板；P4 | M | P4 |
| 7 | Sensor Lab（240、14-bit、反灰）改用 | M | 要 |
| 另案 | OG3K + OG2K 同卡：錄影核心 88 段同位址，需要合成一個 OG 核心 | L | 要 |

每一階段都要重發用到它的 sup；舊版和新版不能混用時，合併網頁要標示不相容（和 FSPL 當時一樣）。

## 9. 不做的事

- 不做獨立的 UI 服務 sup（§3）。
- 不在合併網頁組合 UI（合併網頁維持只放段落）。
- v1 不做 QS 的通用化。
- 不改 loader、stage2、AutoRun、entry ABI。

---

## 10. 實作狀態（2026-10-03，全部離線；尚未上機）

| 項目 | 狀態 | 位置 / 測試 |
|---|---|---|
| 字串池提示 `uis_intern_hinted` | 完成 | `ui_pool.c`；`test_ui_pool` 20 項（4 新 mutation） |
| FPUI 格式 + Python 參考套用器 | 完成 | `ui/fpui.py`（PAGE/GUARD/INSERT/ADD32/ADDF/ALLOC/DONE + HOOK_FV/FILE/CSV_ADD/CSV_CELL/FILE_SET/FILE_DONE/EXPECT/SETSTR_SLOT） |
| C 套用器 | 完成 | `ui_apply.c`；`test_ui_apply` 14 項（8 mutation），與參考逐位元相同，在真的原廠 NBU 上 |
| 共用取檔處理器 | 完成 | `fv_handler.S`（raw-view stub_fv 的共用版）；unicorn 執行 3 項 + 3 個 asm mutation |
| UI 段落（任何 sup 用 `--boot-bin` 帶上） | 完成 | `section.S` + `build_section.py`（套用器 ~7 KB，無重定位） |
| `add_row` | 完成 | `ui/rows.py`；Lossless 改用（`lossless/menu/build_fpui.py`）：套到原廠頁 = 今天上機那頁 |
| `add_option` | 完成（Settings；不含 QS） | `ui/options.py` `color_raw()`、`resolution()`；`test_options`：各自 = 今天的 sup；三個 sup 同卡任一順序 C = 參考 |
| Lossless | 改用 | FPUI 尾段 30 KB（原 FPLM 196 KB）；`test_menu_page` 26 項（17 mutation）；card emulation 16 項（含 ARM 啟動器在模擬器裡把列組進原廠 MainB2） |
| raw-view | 改用 → v0.2.3test | `rv_launch.S` `UISHARE=1`（`--legacy-ui` 仍逐位元重現 v0.2.2test）；`test_rawview_image` 29 項（UI 段落在真映像上跑：H1 → 共用處理器 → 組好的 CSV/圖示）；**修了 stub_ai**（見下） |
| OG3K / OG2K | 改用 → v0.2.8a / v0.1.5a | `OG_UISHARE=1`；`test_boot_entry_chain` 兩種模式都 14+3+8 全過 |
| 合併網頁 | 支援 | 尾段放在讀取上限；卡自帶的入口跳板會被展開（raw-view、OG 現在都有兩個入口）；`test_compose` 71/214、重現檢查全過 |
| lcdflip | 相容 | 會跳過 Lossless 的 FPUI 尾段（開發用 `--add-sup` 組合） |

**相機回報（10/03，raw-view v0.2.3test 首版單獨）**：「更多選項」不見、載入慢。
原因與修正：AEL 頁的 MenuMode 字放在 `ColorModeAdvancedSettings` 的頁面複本裡沒生效 → 改回 raw-view 啟動器原地寫（同 v0.2.2test／bCOLOR）；
頁面逐 byte 複製 → 複本保持來源對齊、整字搬，UI 段落 71 萬 → 9.5 萬條指令。四個發布品與 4 張測試卡已重建。

**順帶找到的 bug（raw-view，相機 10/03 的症狀）**：`stub_ai` 用原廠字串池的**絕對位址**比對字形名稱；
有 Lossless（任何附加字串的 sup）時 UI 改用共用池副本，比對永遠不中 → SA/GA 框顯示 −5/+5。
改成比「目前池裡的位移」；新測試在舊碼上重現失敗。「更多選項進不去」尚無對應解釋，待上機。

### 沒做的（刻意延後）

- **QS 共用**：OG 的 360 筆 QS 記錄仍用自己的 `C05E6400` hook（目前只有 OG 用它，沒有衝突）。
- **OG 的字串別名 hook（`C05E5B58`）改 `uis_intern`**：需要把 ui_record 表改成 32 位元位移（OG inventory 報告 §c）。不影響合併。
- **Sensor Lab**：另一個 session 正在改（`sl_*`），避開。
- **OG3K + OG2K 同卡**：錄影核心 88 段同位址；UI 已可並存，核心另案。
- **第一守則**：OG 存解析度 4（CUSTOM）與 OGSAVE；raw-view 關機時存色彩 0x10–0x17。都是既有行為，沒改，要決定。

### 上機測試清單（建議順序，每項一張卡）

| # | 卡 | 看什麼 |
|---|---|---|
| 1 | Lossless v0.1.2test 單獨 | 選單列出現、可切 ON/OFF；`LOAD_DONE_US` 比 20 s 少（字串池提示） |
| 2 | raw-view v0.2.3test 單獨 | COLOR 第 17 列 RAW、圖示、SA/GA 框、更多選項；與 v0.2.2test 相同 |
| 3 | OG3K v0.2.8a 單獨 | 解析度第 3 列 OG3K（多了 Popup 欄）、摘要 OG3K、可選、錄影 |
| 4 | 合併：shell + lossless + gyro + og3k + raw-view（FS2） | 三個選單都在；SA/GA 正確；更多選項；Lossless 錄影；OG3K 錄影 |
| 5 | 合併 + 重開機多次 | 共用頁面／CSV 每次開機重新組，沒有累加 |
