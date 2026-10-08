# sensor lab sup

[English](#english) | [繁體中文](#繁體中文)

IMX410 modes, ISO, gain and Custom Sensor control.
**Status (2026-10-04): prototype with limited on-camera tests; hardware acceptance
pending. The archived r80 card has a confirmed factory-memory overwrite. The
r81 USER-memory card freezes immediately when REC is pressed. Its recording picker
has a confirmed wrong profile-ID read after relocation. A corrected r83 card
passes offline checks but has not been written to the SD card or retested on camera.**

IMX410 模式、ISO、增益與 Custom Sensor 控制。
**狀態(2026-10-04)：原型，已有局部實機測試，尚未完成驗收。封存 r80 卡已確認
覆寫原廠資料；r81 USER 記憶體卡在按下錄影時立即凍結。已確認搬遷後的錄影
picker 讀錯 profile ID。修正後的 r83 卡通過離線檢查，尚未寫卡或相機重測。**

---

## English

### Established research

The ISO/gain chain has been decompiled, including conversion gain, analog gain,
readout and the ADC stage. The [interactive explainer](https://ijigen.github.io/fpSup/explainers/imx410-iso-gain.html)
distinguishes firmware-confirmed behavior from OTP values that need measurement.

The three picker tables (`0xC0BE5810`, `0xC0BE59B0`, `0xC0BE5B50`) select sensor
modes by geometry and frame rate. The stock FHD 29.97 slot selects mode 106;
a recorded take confirmed 106 at `0xC343B590`. The corresponding FHD 29.97
CinemaDNG rolling-shutter result is 10.556 ms. These are specific mode results,
not a claim that all sensor behavior is solved.

**Correction:** `0xC37CE210` is a downstream geometry cache, not an authoritative
source. The earlier `{1936, 1090, 3244544}` observation does not establish causality;
a later take produced valid 1936×1090 DNGs while that cache was all zero.
The geometry intervention was demonstrated in the FieldAngle record assembled
by `FUN_c043a158`, before its derived values are calculated.

### Current implementation and remaining work

Custom Sensor has a seven-axis recipe engine and a tabbed settings page. Development
records through r79 report on-camera control of the recipe and several 12-bit
recordings, including a native 6064×3412 take that stopped after 16 frames when
the buffer filled. An earlier r80 USB attempt stopped before installation; the
user now reports that a prior fixed-address r80 card did not freeze immediately
on recording. Its packaged bytes overwrite nonzero factory data in an area the shared
loader does not restore. The full research workspace rejects that old fixed
cave layout. The new B2_5 build copies a 9,040-byte core pack and a 652-byte
journal guard (9,692 bytes total) into allocated USER memory, applies 125
relocations, reserves cave space only for 19 core and 10 late UI veneers, and
protects 35 stock words including six external pointer words. It contains no
fixed Sensor Lab cave section. Plain/debug/Fast plain/Fast debug
VBIN sizes are 54,712/57,240/55,344/57,920 bytes, all below the 0xF000
menu-block boundary. Offline full-chain Unicorn runs restored 290 non-cave
journal records in plain mode and 297 in debug mode, leaving all 20,480 bytes
of the factory C0730000..C0735000 area unchanged. The saved r81 debug non-Fast
card is a standalone Sensor Lab test build, not a multi-product merge; it is
retained for diagnosis and should not be used for recording acceptance. These
offline results did not establish recording behavior: the user reports that
pressing REC on r81 freezes the camera immediately. Exact r81 BIN inspection found
that `imx410_fmt` used the old `PROFILE_ID - RECIPE` distance of `0x1AC` after
packing made it `0x1A4`; the picker read the second COLTAB pointer (`0xC0BE3FA4`) instead
of profile ID 87. Source now loads the relocated `PROFILE_ID` directly, and the
late UI `sl_excl_9` code slot moved by 8 bytes. This is a concrete failure mechanism;
the saved r83 debug non-Fast candidate passed the eight-build matrix, exact
artifact checks, 15 corrupt-artifact mutations and full-chain offline boot checks.
On the packed picker, r81 writes `0xC0BE3FA4` to `ENTRY+4`, whereas r83 writes 87.
The r83 card has not been copied to fpSup2 or tested on camera; fpSup2 was last
written with the known-bad r81 and is currently unmounted. The r80 report does not
make its confirmed factory-data overwrite safe. UI, power cycle, warm start and
the `1478` display anomaly still need investigation.

The intended MODE rule is to use only FILTER's Frame rate for FPS filtering;
the user's recording FPS setting must not narrow the list. The deployed r81
card lacks this correction. The r83 candidate has passed offline validation.
After selecting a mode, unavailable recording rates must
still be greyed according to that mode. The corrected UI awaits camera testing.

The SAVE tab, RAW14 recording/gain/packing, per-clip playback geometry, arbitrary
preview aspect ratios, CRCT validation, storage and long-running stability tests
remain open. A UHD 8-to-12-bit selection freeze is unresolved. Fast development
packaging writes persistent settings; multi-product merged packaging is unsupported.

---

## 繁體中文

### 已有研究證據

ISO／增益鏈已反編譯，涵蓋轉換增益、類比增益、讀出與 ADC 階段。
[互動說明](https://ijigen.github.io/fpSup/explainers/imx410-iso-gain.html)明確區分
韌體確認的行為與仍需量測的 OTP 值。

三張 picker 表(`0xC0BE5810`、`0xC0BE59B0`、`0xC0BE5B50`)依幾何與格率選擇
sensor mode。原廠 FHD 29.97 那格是 mode 106，實錄後 `0xC343B590` 也讀回 106；
對應 FHD 29.97 CinemaDNG 的捲簾為 10.556 ms。這是特定模式的結論，不代表所有
感測器行為都已解完。

**更正：**`0xC37CE210` 是下游幾何快取，不能當權威來源。
早期 `{1936, 1090, 3244544}` 的觀測不能證明因果；後來該快取全為 0 時仍錄出
正確的 1936×1090 DNG。畫布的因果驗證是在 `FUN_c043a158` 組好 FieldAngle record、
尚未推導下游值之前介入。

### 現行實作與缺口

Custom Sensor 已有七軸配方引擎與分頁設定畫面。開發紀錄截至 r79，記載面板已在
實機控制配方並錄出數種 12-bit 片段；原生 6064×3412 的一次測試在 16 格後因
緩衝滿而自行停錄。既有 r80 USB 部署紀錄在安裝前中止；使用者此次回報較早的
r80 固定位址卡按錄影沒有立即凍結。**r80 成品仍覆寫共用載入器
不會還原的原廠非零資料，整張卡的固定位址 cave 配置也違反現行分配規則；
完整研究工作區已拒絕建置這種舊配置。**新的 B2_5 建置把 9,040 B
核心封裝及 652 B journal guard（合計 9,692 B）放在配置所得的 USER 記憶體，
完成 125 筆重定位；cave 只經 `CAVE_BUMP` 放 19 個核心與 10 個後期 UI
veneer。連同 6 個外部指標字，共有 35 個受保護原廠字位置；最終 BIN 沒有
Sensor Lab 的固定 cave 區段。
普通／debug／Fast 普通／Fast debug VBIN 分別為 54,712／57,240／55,344／
57,920 B，均低於 `0xF000` 選單區塊起點；整條載入鏈的離線 Unicorn 模擬
還原普通版 290 筆、debug 版 297 筆非 cave journal 記錄，原廠 C0730000..
C0735000 的 20,480 B 維持原樣。保存的 r81 debug 非 Fast 測試卡是獨立
Sensor Lab 成品，並非多產品合併卡；它只保留供問題追溯，不應再用於錄影驗收。
這些離線結果未能驗證錄影：使用者回報 r81 按下錄影立即凍結。追查精確
r81 BIN 發現 `imx410_fmt` 仍沿用搬遷前 `PROFILE_ID - RECIPE = 0x1AC`，
USER 封裝後實際距離為 `0x1A4`，因此錄影 picker 讀出 `COLTAB+4` 的第二個指標
`0xC0BE3FA4`，而非 profile ID **87**。來源已改成讀重定位後的
`PROFILE_ID`，後期 UI 的 `sl_excl_9` 程式槽位亦移開 8 B。修正後的 r83
debug 非 Fast 候選卡已通過八組建置矩陣、成品檢查、15 種損壞成品測試及整卡
離線模擬；直接執行封裝後 picker 時，r81 寫入 `0xC0BE3FA4`，r83 寫入
**87**。r83 尚未寫入 fpSup2 或實機重測；fpSup2 最後一次寫入的仍是已知
有問題的 r81，目前卡片未掛載。r80 的比較回報不代表其原廠資料覆寫安全；
實際 UI、開關機、暖啟動與 `1478` 異常也仍待查。

MODE 清單的規則是格率篩選只由 FILTER 的 Frame rate 控制；使用者的錄影
FPS 設定不應縮減 MODE 名單。已寫卡的 r81 沒有這項更正；r83 候選卡已通過
離線驗證。選取 mode 後，錄影設定中的 Frame rate 仍應依該
mode 的能力反灰。修正後的選單行為尚待實機驗證。

SAVE 分頁、RAW14 實錄／增益／封裝、逐段幾何回放、任意比例預覽框、CRCT、儲存
與長時間穩定性驗證仍待完成。UHD 由 8 切至 12-bit 的凍結也未解。
Fast 開發封裝會寫持久設定；多產品合併封裝仍不支援。

---

Full research tree / 完整研究樹中的現況入口：`projects/res-lab/README.md`。
Design and evidence / 設計與證據：`projects/open-gate/notes/IMX410_FULL_DESIGN.md`、
`CANVAS_IS_NOT_THE_SETTINGS_BLOCK.md` §13、`CANVAS_MOVED_AT_C043A19C.md`、
`research/imx410/notes/FRAME_RATE_IS_VMAX.md`。
Those files belong to the full research workspace and may be absent from this public checkout.
這些證據檔屬於完整研究工作區，不保證存在於公開 checkout。
