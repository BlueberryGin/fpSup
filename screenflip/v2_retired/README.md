# fpScreenFlip — 螢幕翻轉（離線，未上機）

狀態：**OFFLINE_ONLY_NOT_CAMERA_TESTED**。沒有寫卡、沒有碰相機；卡建在暫存目錄、在 unicorn 裡開過機。

取代 `fpSup/lcdflip/`（舊原型：System 3 LCD 設定頁、整頁換掉 FPLF、借 `B1_6.cvm`，從未上機）。

## 在哪裡、選什麼

**SYSTEM 第 2 頁 → 模式設定（`Y2_5`）→ 自訂 1–4 →「更多選項」（`Y2_5_1`）→ 第 3 頁 TOOLS**，
原本四列（水平儀 / 亮度等級顯示屏 / 斑馬紋 / 音訊參數）下面第五列 **Screen Flip**：

| 值 | 選項 | 顯示層暫存器 `0x30190044` bits 12..23 | 觸控 180° 旗標 `0xC3760E44` |
|---|---|---|---|
| 0 | Off | `0x000000` | 0 |
| 1 | 180 | `0x333000`（影像與 UI 都轉） | 1 |
| 2 | Mirror | `0x001000`（只有影像層左右鏡像，UI 不動） | 0 |
| 3 | 180+Mirror | `0x332000`（影像層只上下翻 = 180 再鏡像；UI 轉 180） | 1 |

- 每層 4 bits：bit0 水平、bit1 垂直；層 3 = LCD 影像、層 4/5 = LCD OSD（UI）。來源：記憶 `lcd-flip-layer-bits`（2026-09-30 實機量的 `0x3000` / `0x333000`，`0x332000` 是使用者定版）。
  **值 2（`0x001000`，只翻水平）從沒在相機上寫過**，由位元定義推得。
- 其他位元保留（讀、清 12..23、OR）。觸控旗標只在觸控物件 vtable `[0xC3760E48] == 0xC0CBE37C` 時寫；不符時拒絕會轉 UI 的值（1、3），0 與 2 照常。
- 寫完呼叫 `C02E4420`（顯示訊息 4，重送圖層設定），否則要半按才會正確（實機症狀）。
- 值存在私有整數 `MV_fpScreenFlip`：只在 UI 變數表（記憶體），每次開機 0，**不寫設定區**（第一守則）。
  它是全域的：四個自訂模式的「更多選項」頁都看得到同一列、同一個值。

## 怎麼加進選單（uishare「添加」規範）

`menu/build_flip_page.py` 依 fp-native-ui §8 做整頁：複製**同一個資源組** `Y2_5_1` 第 1 頁的
`1_04_CINE`（時間碼顯示，`MenuItem_Select_4`）。選它的理由：

- 彈出清單是四個**固定子物件** `00..03`（各自 drawText），**不經 CSV** → 不用取檔處理器、不借 `.cvm`、不掛任何 hook；
- 它本來就在 y 405 —— TOOLS 頁的第五格（列距 81，頁高 486）；
- 子樹是一段連續記錄，它播放的群組全在子樹內；唯一的外部引用是頁面的 `STILL_CINE` 模式群組。

複本的改動（全部 typed、對原廠位元組檢查）：父物件 92 → 101（tab 3）、根名 `fpScreenFlip_Row`、
`MV_DSPMODFIX_TimeCode`（5 欄）→ `MV_fpScreenFlip`、標題與選項文字改字面（resolver 旗標 0）、
根的模式片段 STILL 0 / CINE 1 / STILL_Like 0 → 1 / 1 / 1（全模式顯示）。保留同頁共用的
`submenu_05`、`MENU_Level3cu`、`submenu_width*` 與 `DSPMODFIX_TimeCodeAj/Fix` 動畫群組名。

`menu/build_flip_fpui.py` 用 `uishare/ui/rows.row_block` 把整頁反推成 FPUI 區塊（11,444 B），
`prove_same` 證明「區塊套到原廠頁 = 建置器整頁」。列的 y 由 ALLOC 計數器 `Y2_5_1.tools.rows`
（基準 405、步距 81）開機時發；物件 ID、私有字串位移也都開機時發。
`rows.row_block` 為此多了 `root_clip` 參數（預設 1，Lossless 位元組不變），因為這個根的模式片段 id 是 2。

## 卡（SUP_BUILD_RULES）

`build_card.py`：共用 `build_autorun.py --loader`，產品只有**一個** `--boot-bin`：

```
[native/launch.S][native/flip.c + uishare/ui_pool.c + ui_apply.c，一個 unit、無重定位][FPUI 區塊]
```

launcher（lossless `card.S` 前半）向配置器要自己的 USER 區塊（20 KiB）、整段複製、清零、D/I 發布，
把 `state->ui_at/ui_len` 指向常駐的區塊，呼叫 `fpf_card_init`，依序、前一步成功才做下一步：

1. 檢查四個韌體函式的第一個字（`C05DB418` lookup、`C05DB308` register、`C0560FB0` subscribe；`C02E4420` relayout 在每次呼叫前查）；
2. 註冊 `MV_fpScreenFlip`（整數 0；名字放常駐區，registry 借用指標）；
3. `uia_apply`：複製 `Y2_5_1`、插入這列、換執行期項目 `+08`；
4. 訂閱變數寫入（`C0560FB0`，Sensor Lab r31 實機用過）。回呼在 UI 執行緒、新值在 descriptor +8，無狀態，直接套用。

任何一步失敗：沒有列、相機原廠。**不改任何韌體字**（沒有 hook、沒有固定位址段落，stage2 不需要 journal）。

```sh
python3 -B fpSup/screenflip/build_card.py --out <新目錄>              # 開發卡（含 USB shell）
python3 -B fpSup/screenflip/build_card.py --release --out <新目錄>    # 不含 shell
python3 -B fpSup/screenflip/build_card.py --fs3 --out <新目錄>        # 開發卡 + Fast Start 3
```

同一 loader 設定下只改 payload，AutoRun 不變（已比對：改架構前後 AutoRun sha256 相同 `5dc89b85…`）。

## 測試

| 指令 | 內容 |
|---|---|
| `cd fpSup/screenflip/menu && python3 -B -m unittest test_flip_menu` | 8 項：區塊 = 整頁；套用後從位元組讀回的語意（tab 3 第 5 列、y 405、全模式、變數、四個選項、標題、STILL_CINE 登記 clip 2）；不碰檔案/不 hook；第二個 sup 的列得到 486；**C 套用器 = 參考**（單獨、與 Lossless 同卡兩種順序，且不寫韌體映像）；6 個 mutation |
| `cd fpSup/screenflip && python3 -B -m unittest test_card_emulation` | 7 項（unicorn、真映像、真 loader/stage2/entries.S，入口**不 stub**）：段落在卡上、不多加固定段落；註冊/套用/訂閱的引數與順序、頁 = 參考；常駐回呼實跑：四個值的暫存器、觸控旗標、relayout 次數；未知觸控物件；變數已存在不重註冊；韌體字不符 → 不加列；區塊被拒 → 不訂閱 |

模擬卡用 `--release`：開發卡的第一個入口是 USB shell worker，需要 USB 堆疊，模擬器沒有；開發卡只核對版面。
Mutation（第一次就過，故意改壞）：鏡像位元、訂閱錯函式、拿掉 relayout、鏡像也設觸控、UI 失敗仍繼續、區塊長度 0、不註冊變數 —— 全部被抓到（2026-10-04 手動執行）。

同時重跑沒有退步：uishare `test_ui_pool test_ui_apply test_options` 54 項、lossless `native/test_menu_page.py` 26 項。

## 要上機才能回答的問題

1. `Y2_5_1` 的執行期換頁（實機只證實過 MainB2 與 B2_5）。
2. 第 1 頁的列搬到第 3 頁（同資源組不同 tab）能否正常顯示、選取（§8b 只證實「同頁」）。
3. 焦點：從音訊參數按 DOWN 能否到第五列、再回去；popup 開關；底部提示。
4. 字面 ASCII 標籤在彈出清單與收合摘要（Lossless 證實過字面標題）。
5. 訂閱回呼是否在選單寫入時被叫；在回呼裡呼叫 `C02E4420` 會不會阻塞、重排是否足夠（不夠的備案：每層 `FUN_c00df428`）。
6. 值 2（只翻影像水平）的實際畫面；值 3 的方向是否符合「180°+鏡像」的期待。
7. HDMI 插拔會把暫存器清 0，要等使用者再選一次才恢復（沒有輪詢）。
8. 暖開機（撥電源開關）後暫存器是否被韌體重設；若殘留翻轉而變數是 0，選一次 Off 即寫回。
9. 觸控在 Mirror（值 2）不翻 —— UI 沒動所以應該正確，待確認。

上機須另行授權；建議順序：單獨開發卡 → 走到 TOOLS 第五列截圖 → 逐一選 0..3 截圖 → 開機/關機十次（SUP_BUILD_RULES §6）。
