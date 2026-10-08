# fpScreenFlip — 螢幕翻轉（Loader v3 sup，離線，未上機）

狀態：**OFFLINE_ONLY_NOT_CAMERA_TESTED**。2026-10-06 照 Loader v3 重寫（`projects/usb-shell-sup/notes/LOADER_V3.md`）；
舊的 v2 版（stage2 `--boot-bin`、啟動器自己 H_GET、設了就翻）搬到 `v2_retired/`，不再維護。

## 行為

**每個顯示模式各自一個翻轉值，切換「進入」那個模式時才套用；在選單裡設定不會動螢幕。**

- 位置：SYSTEM 第 2 頁 → 模式設定（`Y2_5`）→ 自訂 N →「更多選項」（`Y2_5_1`）→ 第 3 頁 TOOLS 第五列 **Screen Flip**。
- 值：0 Off `0x000000`、1 180 `0x333000`、2 Mirror `0x001000`（只有影像層，UI 不動）、3 180+Mirror `0x332000`，
  寫 `0x30190044` bits 12..23；觸控 180° 旗標 `0xC3760E44` 在 1、3 為 1（觸控物件 vtable 不符就拒絕 1、3）；寫完呼叫 `C02E4420` 重排。
- 表：**STILL 與 CINE 各有自訂 1–4**（韌體本來就兩組，見 `research/firmware/notes/DISPLAY_MODE_SWITCH.md`）→ 8 格。
  第 5 格（mode 4）是「LCD 關閉」，不翻。ShootingStyle = 1 時 CINE 也用 STILL 那組（同韌體）。
- 觸發（sup 自己的任務，每 50 ms）：
  - 目前（組, 模式）和上次不同 → 套用該格的值。涵蓋 DISP、遠端、停用目前的自訂、STILL↔CINE、ShootingStyle，以及**開機那一次**。
  - Y2_5_1 正在編的自訂（`0xC37628A4` = MenuDisplayCustomHandler+0x28）換了 → 用 `C0593F30` 排一筆 `MV_fpScreenFlip = 表[組][N]`，列顯示那個自訂的值。
  - 列被改（`C0560FB0` 訂閱，UI 執行緒）→ 只寫進 `表[組][N]`。
- 為什麼輪詢、不用設定觀察者：開機與 STILL/CINE 切換**沒有** 0xD4 事件；觀察者只有 10 格、在 setter 的任務上跑（同一份筆記 §2、§4）。

## 設定存在自己的 BIN

`native/entry.S` 的 `settings`（檔內 +32+`settings_off`，20 B）：`{"FFST", 1, STILL 自訂1–4, CINE 自訂1–4（各 1 byte）, check}`。
開機時從塊裡讀（加載器已把整個檔讀進塊）；表與檔不同且**連續 1 秒沒變、沒在錄影**時，任務以 open 7 + seek 原地改寫 20 B 再讀回比對。
紀錄壞了：表從 0 開始、**永不回寫**（避免寫錯檔）。不寫相機設定區；`MV_fpScreenFlip` 只是列的工作副本。

## 載入（v3 entry 契約）

```
\fpSup\40FLIP.BIN = [FSB1][entry.S][flip.c + uishare ui_pool.c/ui_apply.c，一個 unit、無重定位][FPUI 列區塊][清零：struct fpf_card]
```

1. `claim_res(0x30190044, EXCL)`、`claim_res(0xC3760E44, EXCL)` —— 別的 sup 佔了面板 → `RELEASE`，什麼都沒寫；
   再以 SHARED_UI claim uishare 三個字串點 `C05E5B58 / C05E61C8 / C05E61E0`（原廠字 `3FFFF1B1 / 428A6942 / 69406902`，
   builder 對 `build_section.sites` 與映像核對）。
2. `fpf_card_init`：檢查 19 個韌體函式的第一個字（不符 → `RELEASE`）→ 讀設定 → 註冊 `MV_fpScreenFlip`
   （此後一律 `KEEP`：registry 借用塊裡的名字）→ `uia_apply` 加列 → 訂閱 → 建任務（優先度 20，名 `FPFLIP`）。
3. 改到的韌體字**只有那三個字串點**（列的文字雖是字面，`ui_apply` 仍把它們掛進共用字串層）。它們進 journal、關機寫回，
   新版 uishare 也靠這筆登記認出我們的層。**2026-10-06 第一版（18:57 上機那份）漏了這三個 claim**，寫了卻沒申報；
   測試當時只驗「journal 是空的」，反而把它蓋掉。現在測試驗證「映像裡被寫的字 = claim 的字、關機寫回」。

回呼與任務找狀態靠 `entry.S` 的兩個 shim 讀塊內 `g_state`（`-fropi` 的 C 不能有可寫全域）。

```sh
python3 -B fpSup/screenflip/build_v3_flip.py --out <新目錄>                     # 只有 40FLIP.BIN
python3 -B fpSup/screenflip/build_v3_flip.py --card [--no-shell] --out <新目錄>  # 整張 v3 卡
```

## 測試

| 指令 | 內容 |
|---|---|
| `cd fpSup/screenflip && python3 -B -m unittest test_v3_flip` | 13 項（含字串點被別人 EXCL → RELEASE）（unicorn、真 loader.S + LOADER.BIN + 40FLIP.BIN、真映像；UI 物件照實機佈置，**列真的裝上**並與參考套用器逐位元組相同）：claim 與入口；面板被佔 → RELEASE 什麼都沒寫；韌體字不符 → RELEASE 不註冊；變數已存在不重註冊；區塊被拒 → KEEP 但不訂閱不建任務；**設定不翻、切進去才翻**、LCD 關閉不動、不重複套用；兩組各自的自訂與 ShootingStyle；觸控未知拒絕；超範圍/未開頁的寫入忽略；存檔（錄影中不存、未滿 1 秒不存、讀回、不重複存）；存過的表開機載入並套用開機模式；壞紀錄不回寫 |
| `cd fpSup/screenflip/menu && python3 -B -m unittest test_flip_menu` | 8 項，列區塊（未改） |

Mutation 14/14 抓到（2026-10-06）：設定就套用、只有一組、忽略 ShootingStyle、錄影中存檔、不等 1 秒、拿掉觸控拒絕、LCD 關閉也套用、不推編輯值、拿掉韌體檢查、壞紀錄也回寫、不 claim 面板、不重排、鏡像位元錯、忽略編輯索引。
共用 `v3.test_v3` 21 項仍通過。

## 要上機才能回答的問題

1. `0xC37628A4` 開 Y2_5_1 後真的是 N；`C0593F30` 排的寫入會不會更新已顯示的列、會不會觸發我們的訂閱回呼（會的話值相同，無害）。
2. 從我們的任務（非 UI 執行緒）寫暫存器 + 呼叫 `C02E4420` 是否安全、畫面是否正確重排。
3. getter `C0061BA8 / C0058340 / C0061A68` 從我們的任務呼叫是否安全（內部有鎖）。
4. DISP 切換到翻轉生效的延遲（≤ 50 ms 輪詢 + 重排）。
5. `Y2_5_1` 執行期換頁、第 1 頁的列搬到第 3 頁的顯示與焦點（v2 時就未驗）。
6. 值 2（只翻影像水平）從沒在相機上寫過。
7. HDMI 插拔把暫存器清 0：不會自己補回，要切一次模式。
8. 存檔：open 7 原地改寫 20 B 後讀回；`40FLIP.BIN` 被 macOS 加 `._` 檔無影響（加載器略過）。
9. CINE 組是否真的有第 5 格；EVF 時翻 LCD 看不到（無害）。

## 上機（2026-10-06）

卡 `projects/screenflip/cards/20261006-v3/` 的 40FLIP（舊 uishare、**缺字串點 claim**）與 10LOSS / 20GYR2 / 30OG3K 同卡：
LOADED、READY、列在 y 405。使用者親自操作：設定不翻、DISP 切進去才翻，「看起來完美」。
讀回：10 次切換全部套用、表 CINE 自訂 2 = 3。**存檔沒有發生**（`saves 0 / save_failed 0`，`self_path` 正確）——
擋住的是 `recording()`。**根因（2026-10-07 反組譯）**：`0xC375896C` 是 shell `status` 指令的參數表，只在 shell 第一次跑 `status`
時（`C040FDE0`）才被設成 `C00178D8()+0x18`，正常開機是 0 → 判成錄影中 → 永不存。改成直接呼叫 `C00178D8()`（開機就建好的
系統狀態物件 `0xC3033834`）讀 `+0x18+0x84`；測試把舊讀法放回去會失敗。**未上機**。

修正版（新 uishare + 字串點 SHARED_UI claim）：`projects/screenflip/cards/20261006-v3b/`，未上機，已被取代。

**2026-10-07 跟上 uishare 字串層 v3**（標頭 +00 名字 = 檔名主檔名、靠 Loader v3 登記簿認層、`-mstackrealign` 與入口絕對 8 對齊）：
`projects/screenflip/cards/20261007-v3c/`（帶 00SHELL），未上機；再跟上 ui_pool.c 01:37 的 svc_holder 修正 → `20261007-v3d/`。

**v3d 上機（2026-10-07 02:4x）**：卡上 LOADER（7,260 B）+ 00SHELL、10LOSS、20GYR2、30OG3K、40FLIP、45RAWV，六個全部 LOADED、無 CONFLICT；
40FLIP 三個字串點有登記；READY、列 y 405、任務在跑。字串層讀回三個點都是 **40FLIP → 30OG3K → 10LOSS → 原廠**，全是 v3。
存檔問題已找到根因並修（見上），未上機。模擬裡三個點都是同一層、名字 `40FLIP`、版本 3。
同卡所有用字串層的 sup 必須同一版 uishare（v1/v2/v3 互不承認）。
相機卡上已有 v3 的 LOADER/00SHELL 時只要換 `fpSup/40FLIP.BIN`（`deploy_v3.py CARD --only fpSup/40FLIP.BIN`）。

## 發布

`fpSup/releases/fpsup-screenflip-v0.1.0test/`（`tools/release_v3.py`，40FLIP.BIN sha256 `cc3d4c05…`，與測試建出的位元組相同），
**未上機**。另一個 session 的草稿改名保留在 `releases/_draft-fpsup-screenflip-v0.1.0test-f1a273f5/`。
發布前要上機：改值 → 等 1 秒以上 → 重開 → 值還在；單獨 Mirror。
