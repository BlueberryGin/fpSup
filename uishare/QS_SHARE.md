# QS 共用:多個 sup 各自往 Quick Set 加解析度狀態

2026-10-06,**設計(Phase 1),未實作、未上機**。目標是 `add_option(qs=True)`:每個 sup 把自己那一格加進 QS,
不需要一個中央的 compositor,也不需要找到別的 sup。
前文:[MENU_MERGE_PLAN.md](MENU_MERGE_PLAN.md) §5/§7(P4)、[NESTED_HOOKS.md](NESTED_HOOKS.md)、
[research/ui/QUICK_SET.md](../../research/ui/QUICK_SET.md)、
[projects/jose-hurtado/notes/FORMATS_V3.md](../../projects/jose-hurtado/notes/FORMATS_V3.md)。

## 0. 起點:兩套現成的做法其實是同一套

OG3K(`projects/open-gate/build/og3k_ui.S` 的 `ui_record`)與 Jose 的核心(`C0732DD0`)是**同一支程式的兩份**:
同樣的狀態字位移、同樣的二分搜尋表 `{來源記錄位址, meta, FNV-1a}`、同樣的五種 kind。
表比對(`scratchpad` 腳本,讀 OG 的 `selected_plans()` 與 Jose 表 `C073321C`):

| | OG3K | Jose | 說明 |
|---|---|---|---|
| 記錄數 | 360 | 379 | **OG 的 360 個位址全部在 Jose 表裡**,Jose 多 19 筆 |
| kind 1 layout-keys | 315 | 315 | 同一批;新影格時間全部 **66**(第三個狀態) |
| kind 2 state-end | 36 | 36 | 終點 33 → 66 |
| kind 0 QS-limit | 2 | 2 | 插入 max 屬性:OG **2.0**、Jose **7.0** |
| kind 3 摘要 | 1 | 1 | `C1A7246B`(B2_5) |
| kind 4 literal-delta | 6 | 25 | 底列、游標、Settings 的多列 |

**關鍵觀察:兩者在 QS 大圖磚上都只加一個狀態(t = 66)。** 第三狀態的影像名稱是別名字串 id
(`F2`/`F4`),畫的時候由字串層解析 —— 所以「現在選的是哪個格式」靠換別名內容,不靠多加狀態。
Jose 8 列也只有一個 t = 66 的狀態。⚠ 他在選擇時怎麼把 `F2..F4` 換成目前格式的名字,還沒在他的位元組裡找到寫入者(§8)。

## 1. 一個新解析度狀態碰到哪些記錄

全部在 `C05E6400`(NBU 記錄解析)時改**複本**,原廠映像不動。依 QUICK_SET.md:`20/21_MV_Resolution` 群組
在 `QS_CINE` 24 筆(**8 個版面變體**各 3)、`QS_STILLlike` 24、`QS_Common` 6;其餘是群組的 companion clip。

| 類別 | 記錄 | 依「總數」嗎 | 共用版的運算 |
|---|---|---|---|
| 大圖磚的第三狀態(kind 1) | 315 筆,三個 QS 集合 × 8 變體 | **否**(只有一個 t=66 狀態) | `ensure_key(66)`:沒有就插,有就不動(冪等) |
| 狀態曲線終點(kind 2) | 36 筆 | 否 | `end = max(end, 66)` |
| 大圖磚第三狀態的影像名 | kind 1 的別名屬性(prop 0/1/2/6) | 否 | 別名 id 由共用層發,解析成「目前選的那個格式」的名字 |
| QS controller max(kind 0) | `C237249F`、`C25E405A` | **是** | 沒有 max 屬性就插入原廠值 1.0,再 `+1.0`(每個 sup 一次) |
| 底列標籤 | `C23535A3`(QS_CINE)、`C25B9EF1`(STILLlike)是 OG 那一格;Jose 另用 `C2354087/4AF3/5569/5F8F/6A55`、`C25BAA2F/B4EB/BF61/C9D7/D4C5` | **每格一個物件** | 第 k 格在 t=700(`0x2BC`)加一個影格,圖名 = 該 sup 的字串,**執行期填字串層發的位移(或 §4.3 的共用 id),不可沿用 FP3K 計畫寫死的 `FFFFFFF3`**(§1.1);**k 由 CSV_ADD 開機時發的列號決定** |
| 底列位置曲線 | `C2358981`:原廠 7 個影格(t=0,33,66,100,133,166,200;x=190..742 步距 92) | **是** | Jose 8 格:全體 x−46、加第 8 影格 t=231 x=788。共用版:由總數 N 算(§3) |
| 底列時間終點 | `C234E465` +39:200 → 231 | **是** | 由 N 算 |
| 游標 clip 名 | `C237256F/25CE`、`C25E40DC/413B` +36:原廠 `Cursor2`(`0xF4C8`)→ OG `Cursor3`(`0xF4D0`)→ Jose `Cursor7`(`0xF504`) | **是** | 由 N 查表;原廠池有 `Cursor2..7`、`Cursor9`(另有 `Cursor01..04`、`Cursor10`、`Cursor6_ImageSize`),沒有 `Cursor8` → **N 有上限** |
| Settings(B2_5,屬 add_option 不是 QS) | 摘要 `C1A7246B`、`C1A725AC/26ED/282E/296A/2AA6`(每列摘要)、popup 位移 `C1A70827`(0 → −164.0)、`C1A74F57`(6 列同值 `0x9B94`) | 摘要否;popup 位移**是** | 交給 add_option 的 PAGE/SETSTR_SLOT;popup 位移由 N 算 |
| 列 ↔ enum 轉換 | 韌體 `C06BDA3C/40` 的 movw/movt(Jose 指到自己的表;OG 用原廠空著的 (4,2)) | 每格一對 | 共用轉換表(§4.4) |

「由 N 算」的欄位目前只有兩個實測點:**N=3(OG3K,上機過)、N=8(Jose,上機過)**,加上原廠 N=2。
其他 N 的值是內插,**每個新的 N 都要上機看一次**。

### 1.1 寫死的別名 id(d9 2026-10-06 查明:今天 OG3K QS 底列缺圖的原因)

底列那兩筆 literal-delta(kind 4)—— `C23535A3`(QS_CINE,新位元組 offset 304)與 `C25B9EF1`(STILLlike,offset 104)——
的替換位元組裡**寫死了別名 id `0xFFFFFFF3`**(OG3K_SET)。舊版靠 OG 自己的 `C05E5B58` hook 解 `FFFFFFF2..F6`;
巢狀字串版拿掉了那個 hook,只有 kind 1 與 kind 3 在執行期換成 STRBASE 發的位移,kind 4 照抄 → 沒人回答 `FFFFFFF3` → 缺圖。
跟 v3、跟新舊 uishare 無關。Cursor 那 4 筆 kind 4 沒有別名。

**共用層的規則:**
- 記錄裡任何字串/圖名欄位**一律在執行期填**:自己的字串用字串層發的位移;第三狀態的「目前格式」用 §4.3 的共用 id。
  配方資料裡只放「這裡填哪一個字串」的標記,不放 id 值。
- **建置檢查(失敗就停)**:掃所有配方的新位元組(literal-delta 的插入/替換、layout-keys 的別名欄),出現
  `FF FF FF F2..F6`,或任何 `≥ 0xF0000000` 的 big-endian 字(負數別名 id 的形狀),建置失敗。§4.3 的共用 id 不落在這個範圍。
- **Jose 的 379 筆同樣寫死**:literal-delta 的新位元組裡有 `FFFFFFD0..D4`(B2_5 的每列摘要)、`FFFFFFF3`、
  `FFFFFFE1/E4/E7/EA/ED`(QS_CINE 與 STILLlike 的底列六格);kind 1 的別名欄是 `F2`/`F4`。
  他現在沒事,是因為他自己的字串層(`uis_layer_fixed`)會回答 `FFFFFFD0..F6`;轉成共用層時這些都要改成執行期填。


## 2. 記錄 hook 的套娃:每層一個真正的 handler

2026-10-06 使用者問「351 筆是韌體原本的記錄,為什麼不能直接套娃」—— **可以**。原本擔心的是:
`ui_record` 用「`reader+36` == 原廠 NBU 基底」認記錄,外層把基底改成 `複本 − 位置` 之後內層就認不出。
解法是**把識別跟著傳下去**,不靠基底。

### C05E6400 的呼叫慣例(讀 `og3k_ui.S` 與 Jose `C0732DD0`,兩份相同)

只有 `r0` = reader(Thumb)。用到的欄位:`+4` 位置 pos(記錄在 NBU 內的位移)、`+8` resource context、
`+36` 基底 base;記錄在 `base + pos`。餵複本的方法是 `base = 複本 − pos`,pos **不動**;解析完把 base 放回、
`pos = pos0 + 原長度`(跳過原記錄)。

所以 **pos 本身就是穩定的 ID**:每一層都不改 pos。缺的只是「這個 base 是不是原廠 NBU 的一份複本、是誰做的」。
**不另設魔術字或 ID 規則**(使用者 2026-10-06):跟新版字串層同一套,用 loader v3 帶進來的檔名。

- **層標頭**:`+0 檔名 8B`(`svc->self()`,例如 `31S16`)、`+8 版本`、`+12 next`、`+16 記錄表`、`+20 列號 k`。
- **複本前面的標頭** = 做複本那一層的 `{檔名 8B, 版本, 原廠基底}`。
- 每層 entry 時把 `svc` 存進自己的塊(加載器的塊整個開機都在),解析時用它認層。

```
base == 原廠基底                                    → 原廠記錄,S = 原廠基底 + pos
[base+pos-16..-9] 的檔名是 C05E6400 的持有者之一
  (svc->holder(C05E6400, i) 逐一比對)、版本相同、
  [base+pos-4] == 原廠基底                           → 別的層做的複本,S 同上
其他                                                → 不是原廠 NBU 的記錄,直接交給 next
```

FNV 一律算**原廠映像裡 S 的位元組**(不是目前版本),所以前面的層改過也照樣對得上;不符就整筆不碰。

### 每層的流程

```
S, 目前版本 cur = 認記錄(reader)
if 我的表沒有 S:            tail call next(reader)                 ← 不複製、不碰
if cur 是原廠:               複製到自己堆疊上的 1 KiB 緩衝(前面放自己的 {檔名, 版本, 原廠基底}),base 改指它
else:                        就在外層的複本上改(外層的堆疊框還活著,緩衝容量寫在標頭)
做冪等編輯(下表) → call next(reader) → 回來後 base、pos 放回「我收到時」的值、pos = pos0 + 收到時的長度
```

最外層收到的是原廠,所以它最後放回的就是韌體期待的 `base = 原廠、pos = pos0 + 原長度`。
內層的放回只是讓自己對稱,會被外層覆蓋。容量:最大記錄 < 512 B(OG 量過),每層最多加一個影格與一格標籤,
1 KiB 緩衝夠 8 層;超過就不改、照樣交給 next(該 sup 的格會缺,LOAD.LOG 記一筆)。

### 冪等編輯與順序無關

| 編輯 | 怎麼寫成冪等 | 為什麼與順序無關 |
|---|---|---|
| 大圖磚第三狀態(351 筆) | 「t=66 的影格**沒有才補**」;終點 `max(end, 66)` | 第一個補的人之後,其他層看到已存在 → 不動;補的內容每層相同(§4.3 的共用 id) |
| QS controller max | 「沒有 max 屬性就插原廠 1.0」,然後 `+1.0` | 每層每次解析剛好一次;加法可交換 |
| 第 k 格底列標籤 | 只寫自己的 k(CSV_ADD 開機發的列號) | 不同層寫不同物件 |
| 由 N 決定的欄位 | **設成 f(N)**,N 讀共用 CSV 複本的列數(開機時所有 sup 的 CSV_ADD 都已完成,解析在那之後) | 每層算出同一個 N、寫同一個值;寫幾次都一樣 |

所以任何載入順序得到的記錄,只差「第 k 格是誰」;k 同時決定 CSV 列、轉換表列、底列標籤,三處一致。
Phase 2 的測試:兩層 A→B 與 B→A 的結果 = 參考實作一次加兩格(k 對調後逐位元組相同);再加「同一層掛兩次」= 掛一次(冪等)。

### 351 筆配方放哪

uishare 本來就是**編進每個 sup 的函式庫**,所以「uishare 內建」和「每個 sup 各帶一份」是同一件事:
需要 QS 狀態的 sup 連結 `qs_*` 時就帶著它(表約 351×12 B ≈ 4.2 KB + 編輯)。冪等,重複套用無害。
**建議內建在 uishare**(一份原始碼、從 OG 的 plan 產生、版本一致),不讓各 sup 自己抄表。

### 跟上一版(最外層統一套用)比

| | 統一套用(上一版) | 每層套娃(現在) |
|---|---|---|
| 內層程式會不會執行 | 不會,只讀它的標頭 | 會,每層自己處理 |
| 要不要走訪別層的標頭 | 要(查登記簿) | 不走訪;只在拿到複本時查一次登記簿(複本標頭的檔名是不是 `C05E6400` 的持有者) |
| N 從哪來 | 數層數 | 讀共用 CSV 的列數 |
| 複本數 | 一份 | 一份(第一個要改的層做,其他就地改) |
| 跟字串層的一致性 | 不同模型 | 同一個模型:各層處理自己的、其餘交給 next |
| 新風險 | — | 就地改外層緩衝要守容量;複本標頭的誤判(檔名要是登記簿裡的持有者、版本與原廠基底都要對,加 FNV 對原廠) |

## 3. N 決定的欄位(f(N),先只支援實測過的)

| N | 游標 clip | 底列 x 起點 / 影格數 / 終點 | QS max | popup 位移 |
|---|---|---|---|---|
| 2(原廠) | Cursor2 | 190 / 7 / 200 | 1.0(無屬性) | 0 |
| 3(OG3K,實機) | Cursor3 | 190 / 7 / 200(OG 沒動) | 2.0 | 0(OG 沒動) |
| 8(Jose,實機) | Cursor7 | 144 / 8 / 231 | 7.0 | −164.0 |
| 4..7 | Cursor4..6?(`Cursor5` 存在) | ? | N−1 | ? |

**N = 4..7 的值是推測,套用器第一版只接受 N ∈ {2,3,8},其他 N 讓多出來的 sup RELEASE**(或接受推測值但標成待驗;要決定,§9)。
⚠ 8 格用 `Cursor7`、而 3 格用 `Cursor3`:名字和格數的關係不是 N 也不是 N−1,原廠池沒有 `Cursor8`(有 `Cursor9`)。要上機看 N=8 的游標能不能走到第 8 格。

## 4. 其他三個共用點

`C05D90F0`、`C05E84D8` 的層也用同一個標頭(`+0 檔名 8B`、版本、next),認層 = 查登記簿 + 比對檔名。

### 4.1 重新解析(`C05D90F0`)

QS 版面開機就解析並被 `UicResourceCache` 多握一個參考,hook 裝好之前解析的那份會一直是原廠的。
OG 的 `ui_layout` 在 hook 裝好後的第一次版面載入時記下快取裡三個 QS 版面的 tree,符合條件時用原廠的
`UicResourceCache::v3` 放掉,讓它經過 `C05E6400` 重新解析。**Jose 沒有掛 `C05D90F0`**(他靠 `C005C054` 的 crop 翻轉重建 LV)。

共用版:搬進 uishare,每個 QS 層都掛一層(SHARED_UI)。多層同時存在時:第一層放掉之後 tree 換了,
其他層的「tree 相同」檢查不成立 → 自動忘記,不會放兩次。所以可以每層都掛、各自跑,順序無關。
「armed」條件改成「最外層套用器已就緒」(OG 現在是 `ui_aliases[0] != 0`)。

**2026-10-07 上機訂正**:大圖磚第三狀態的圖名在**版面解析時**就定(不是繪製時);韌體在 setter 還在執行、設定仍是舊值時就
重解析 QS 版面(OG 的 `C005C020` 前置 hook 可能就是觸發點),所以圖名「慢一步」;開機時 QS 版面在層掛上前解析,所以沒有自訂圖。
修法:`resolution_qs` 預設開「換到我的格式就重解一次」(`QS_FLAG_SWITCH`),且 `LAST_ENUM` 初值改成「未見過」——
開機時值已是我的也算一次切換。重解析發生在下一次版面載入(打開 QS),那時設定已是新值。不在 `C005C020` 記錄新值:
那個點 OG 與 Jose 都是 EXCL 掛著,掛上的先後也決定不了誰先跑。測試 `test_qs_layer.test_the_cameras_timing`。

**2026-10-07 上機凍結(v5c)**:v5c 開機後**第一次打開 QS(QSON)整台凍結**——不只 USB,相機畫面也不能操作。
v5c 的規則是「要載入的版面 = 快取握的 QS 版面時放掉它的 tree」,也就是**在 `C05D90F0` 載入 X 的過程中放掉 X 自己的 tree**,
放完才接原廠載入。LAST_ENUM 初值「未見過」讓開機那次切換第一次 QSON 就成立,所以必定走到。
根因推論(未上機證實):use-after-free —— 原廠載入接著用的是剛放掉的 tree(或 v3 沒把 `[X+0xC]` 清 0,
原廠以為已解析好就直接用),UI 任務當掉 → 整機凍結。與「整機凍結、不是只有 USB」一致。

**現行規則(v5c 之後)**:只在載入**別的**、不是三個 QS 版面之一的版面時,才看快取握的那個 QS 版面 L:
L 仍是快照記下的 tree(開機那次),或切換規則掛著(`PENDING`),且 L 只有快取握(參考數 1)、快取不忙、
L 不是目前/保留畫面(QS 關著)→ 用 v3 放掉一次。開機那次因此等到第一次載入非 QS 版面才放;下一次 QSON 重解析。
QS 自己載入時、另一個 QS 版面載入時一律不放。快照時還沒建好的 QS 版面會在之後用名字補查。
`resolution_qs` 預設先改回 `flags=0` 止血,新規則測過後改回 `flags=1`(現值)。
測試:`test_qs_layer.TestLayout`(模型在「正在載入 X 時放掉 X 的 tree」就報不安全)、
`TestLayoutMutations`(v5c 規則等 10 種變異都會被抓到)。

**`RCACHE_V3` 的實際語意(2026-10-07 反組譯坐實，取代原先未經 RE 的假設)**:
- `UicResourceCache::v3` @`C0566498`(ARM;vtable `0xC0D0EF88` 的 +0x14)的 **r0 是快取物件本身**,不是 tree:
  `[this+4]` 非 0 時 → `+0x28`=1 → `C0560B68` → `C05D93A0(L)` → `[L+0xC]` 非 0 才 `C05D91A0(L)` →
  ctx 載入器 vtable(`0xC2E00360`)+0x10 = `C05E83F0`:tree 參考數(`tree+0xC`,`C05CFAE8` 減 1)原本是 1 時釋放 tree 並 **`[L+0xC]=0`**
  (`C05E8456`);原本 >1 時只減 1、`[L+0xC]` 不動 → 清快取名字(`+8`,0x20 bytes)、`+0x28`=0、**`[this+4]=0`**。
- 原廠呼叫:`C0565090`(`CustomScreenLoader::v1` 事件 0x2602 = 某版面剛新建好)在新建的是 QS 名字之一、而快取握的不是它時，
  `(*vtbl+0x14)(cache)`。快取重新接手在 `C05650D8`(事件 0x2600/0x2601):握的不是它就放舊的、`C0560B08` 載入它、`+4`=它。
- 放掉後下一次載入 L:`C05D90F0` → 載入器 +0xC = `C05E82F0`:`[L+0xC]==0` → 經 `C05E6400` **重新解析**(我們的記錄層);
  `[L+0xC]!=0` → 只把參考數 +1(`C05CFAE0`)、沿用舊 tree,不重新解析。
- **結論**:「清成 0」在「以快取物件呼叫、且參考數為 1」時成立;**我們(以及 OG 的 `og3k_ui.S ui_layout`)把 tree 當 r0 傳進去，不成立**——
  v3 會把 tree 當快取:讀 `[tree+4]` 當版面去卸載、把 `tree+8..+0x27`(含參考數 `+0xC`)清 0、寫 `tree+0x28`。這很可能是 v5c 凍結的
  主因之一(再加上它在 L 自己的載入中做)。
- **已改碼**:`qs_layout` 改呼叫 `RCACHE_V3(RCACHE)`;測試模型的 v3 照上面的語意寫(r0 不是快取就判不安全，參考數 1 才清 `[L+0xC]`、
  清 `+4`),QS 版面顯示時快取重新接手。變異「v3 收到 tree」會被抓到。**OG 的 `og3k_ui.S` 也有同一個錯(傳 tree),不在我能改的範圍，
  要轉給 d9。**

### 4.2 私有圖檔包(`C05E84D8` NBR 載入)

OG 與 Jose 都在原廠 NBR 載入之後,用同一個載入函式再登記一個私有 pack(OG `ensure_ui_pack`)。
這本來就是可疊的:每層「先呼叫下一層,原廠 NBR 載完後登記自己的 pack」,每個 resource context 一次。
claim SHARED_UI。名稱衝突(兩個 pack 都有 `OG3K_QS`)由 sup 自己避免:名稱加檔名前綴,或沿用 Jose 已經不同的名字。
Jose 另外改 `C0D4DE04`(資源目錄記錄,讓原廠載入器自己載他的 pack)—— 共用版不用這條,改走 4.2。

### 4.3 字串:大圖磚第三狀態的影像名(共用的開機發號 id)

第三狀態只有一個,要顯示「目前選的格式」的圖。**不用固定 id**(使用者 2026-10-06 暫定),改成統一規則下的共用發號 id:

1. **第一個需要的 sup(發號者)**:`claim_res(RES_QSCUR, SHARED_UI)` 成功且 `svc->holder(RES_QSCUR, 0)` 是自己 →
   在自己的字串層正常發 3 個位移(QS/SET/FONT 圖名),寫進自己字串層標頭的新欄位 `H_QSCUR[3]`。
2. **之後的 sup**:同樣 `claim_res(RES_QSCUR, SHARED_UI)`;`holder(RES_QSCUR, 0)` 是別人 → 從 `C05E5B58` 沿字串層鏈走
   (同 `ui/chain.py`:B.W → veneer → 入口 − 入口偏移 = 標頭),找到標頭檔名 = 發號者的那層,讀它的 `H_QSCUR[3]`。
   找不到或版本不符 → entry 失敗(RELEASE)。
3. **解析**:每個 QS sup 的字串層 resolve 在範圍檢查之前先看「id ∈ 共用 3 個 且 目前解析度 enum == 我的 enum」→ 回我的圖名;
   否則照常(不是我的就交給 next)。
4. **沒人回答時**(選的是原廠 UHD/FHD,第三狀態本來不顯示;或暫態):id 會一路傳到發號者自己那層。發號者的規則是
   「共用 id 且 enum 不是我 → **先呼叫 next**,next 回 0 才回自己的備用名」—— 所以發號者要比別人晚回答,
   這是字串層唯一需要「呼叫 next 再看結果」而不是尾呼叫的地方。發號者以內的層若 enum 相符,照常先被問到並回答。

**可行性**:可行,不需要新的 sloader 服務(只用 `claim_res`、`holder`,都已存在)。代價:
- 字串層標頭多 3 個字(`H_QSCUR`),**字串層版本要 +1**(同卡所有 uishare sup 要同版,規則照舊)。
- 發號者那層的共用 id 分支要多一段「呼叫 next 再決定」(要守住字串層「r1/r3/r4/ip/lr/sp 不變」的約定,改成真正的呼叫要另外存 lr)。
- 發號者的身分只靠載入順序(檔名排序最前的 QS sup),它若 RELEASE,它的 claim 被撤銷、還沒發號 → 下一個 sup 成為發號者。不會出現「id 發了但發號者不在」。
- 讀「目前解析度 enum」要一個韌體 getter(解析時讀,不快取);OG/Jose 都已有自己的讀法,共用層統一一個。

### 4.4 列 ↔ enum 轉換表

韌體轉換函式用 `C06BDA3C/40` 的 movw/movt 載入成對表的位址。共用版:第一個需要的 sup 從
`svc->cave_alloc` 要一張表(8 對 × 8 B,含小標頭),複製原廠的對、改指 movw/movt(claim SHARED_UI);
之後的 sup 認出表頭就追加自己的 (enum, 列)。可交換:只追加、不改別人的對。

## 5. 各 sup 的貢獻要怎麼寫

每個 sup 的 QS 資料 = **一列描述**,不是整張 360 筆表:

```
qs_option(label="S16 2096x1238", qs_image="S16_QS", set_image="S16_SET", font_image="S16_FONT",
          pack=私有 NBR pack, enum=5)
```

315+36 筆大圖磚記錄、Cursor/底列的 N 表、QS max 都是 uishare 內建的共用配方(從 OG 的 plan 產生,
一份,所有 sup 相同);sup 只帶自己的標籤、影像名、pack、enum。

| sup | 現在 | 改成 |
|---|---|---|
| OG3K / OG2K(d9 的) | 自己的 `ui_record` 360 筆 + `ui_layout` + `ui_load`,`C05E6400/84D8/90F0` EXCL | `qs_option(...)` 一列;三個 Thumb hook 改由 uishare 的 QS 層掛(SHARED_UI)。`og_v3.c` / `build_v3_og.py` 要 d9 改:拿掉 og3k_ui.S 的三個 hook 與記錄表、FPUI 加一個 QS_OPTION op |
| Jose 的每個格式 | 核心 379 筆 + compositor 依 slot 填 | 每個格式一個 sup,各一列 `qs_option` |

**Jose 的二進位,可以沿用的**:每個格式的圖檔 pack(piece 2,`C07A4BF0..`)、標籤字串、enum、registry/picker/
donor 時序等錄影資料。**必須換掉的**:`C05E6400` 處理器與 379 筆表、`C05E84D8` 的 pack 登記、`C0D4DE04`、
CSV 整檔替換 `C12C7CF8`、controller max `C1A709BC`、compositor 裡填別名與底列的部分。
⚠ **QS 只是拆開的一半。** 他的 20 個 ARM hook(picker、`regfrclass/regfrmax/regcrop*`、`regseltail`…)
也是依 slot 集中處理;拆成一格式一檔,這些也要改成每個格式一層「只處理自己的 enum、其他交給下一層」。
那部分不在本筆記範圍,但不做就拆不開。

## 6. 第一守則

QS 共用不寫設定區:改的都是解析時的記錄複本、私有 pack、記憶體裡的轉換表。QS 選到的值經轉換表變成
解析度 enum,存進設定區的是那個 sup 宣告的 enum —— 跟 Settings 那條路完全相同,不多存任何東西。
(Jose 的 5/8/9 使用者 2026-10-06 已表示不在意;OG 的 4 仍是 MENU_MERGE_PLAN §4 的待核對項。)

## 7. P4 上機實驗(最小)

**基準不能用今天的 OG3K**(d9 2026-10-06):v3 OG3K 上機時 QS 底列圖不見,巢狀字串版(`OG_UISHARE=1`)的 QS
從沒上機過,出貨 v0.2.8a 是舊 FSPL 別名版。原因(巢狀版本身,或 v3 私有 pack 在堆積塊裡登記失敗)d9 在查。
所以參考答案改成兩份**已知正確**的畫面,先拍下來存檔:

- **原廠**:不放任何 sup,QS 錄影/靜態-like 兩種版面 × UHD/FHD 的圖磚、底列、游標截圖(N=2)。
- **出貨 OG3K v0.2.8a**(舊 AutoRun 卡,上機證實過):同一組截圖 + 選 OG3K(N=3)。
- 另外離線存兩者「解析後的記錄複本」:原廠記錄 + OG 的 360 筆 plan 套用結果 = N=3 的參考位元組
  (`layout-repair-plan.json` 的 new,FP3K 交接已在相機證實),Phase 2 的套用器必須逐位元組重現。

步驟:

1. **一層**:新 LOADER + 一個只帶 `resolution_qs(OG3K…)` 的測試 sup(不含錄影核心,enum 指向原廠值,不存新值)。
   截圖對 v0.2.8a 的 N=3 截圖;**通過條件之一:QS 底列三格都有圖**(今天 v3 OG3K 第三格缺圖,§1.1;
   使用者 2026-10-06 決定 OG 不做短期修補,上線前維持缺圖);另掛只計數的 `99QSCNT` 層,LOAD.LOG 記它看到的記錄數 > 0 → 證明串接。
2. **兩層**:再加一個假格式 `35QSTST`(N=4,enum 指向原廠值)。QS 4 格、游標走得到第 4 格、選到時大圖磚顯示它的圖。
   **改檔名前綴換順序再做一次**,畫面相同(只差第 3、4 格是誰)。N=4 的 f(N) 在這一步定案。
3. 冷開機與 Fast Start 3 暖開機各做一次(§9.5 的時機)。

**2026-10-07 上機結果(第 1 步的實質部分,用真的 OG3K 而非測試 sup):通過。**
卡:`LOADER.BIN` 7,260 B(sha `41f3218a…`,含 `svc_holder` 也比對 res claim 的修正)、`30OG3K.BIN` 52,496 B
(sha `dcc6a728…`,`projects/open-gate/build/cards/20261007-v3-chain-og3k/`,鏈式核心 + `resolution_qs`)、
另有 00SHELL/10LOSS/20GYR2/40FLIP/45RAWV,六個都 LOADED、無 CONFLICT。
- `menu SetMovRecSize` = 4;QS 主畫面第二次繪製(`gui image log`)載入 48 張,含 `30OG3K_QS.xci`(86×49)、
  `30OG3K_FONT.xci`(150×38),無缺圖。
- **底列三格(UHD/FHD/OG3K)都有圖**:使用者在相機上開 QS → RES. → OK 目視確認。§1.1 的缺圖已修好。
- 之前四次上機 OG3K 都 RELEASE(`OGU00090025` = UIA_HOOK @ QS_OPTION):根因是 sloader `svc_holder()` 跳過 res claim,
  `uis_qs_shared` 拿不到發號者。主機 fixture 的 holder 比真的寬,所以測試沒抓到。
- 未做:`99QSCNT` 計數層、第 2 步(N=4、換順序)、第 3 步(冷/暖開機)。

### 7.1 上機檢查:開機時設定是自訂格式,QS 卻一張自訂圖都沒有(2026-10-07)

離線推不出確定原因;最可能是開機時 QS 版面在我們的層掛上**之前**就解析了,而 §4.1 的重新解析層沒放掉它。讀兩個 QS 層
(每個 QS sup 一層;標頭 = 該 sup 塊裡 `qs_build` 的 `header`,也可從 `C05D90F0` 的 B.W → 跳板 → 入口,再
`入口 − [入口−4]` 找到)的這幾個字(v2 標頭,`qs_layer.h`):

| 位移 | 欄位 | 判讀 |
|---|---|---|
| `+24` | 狀態 | 1 = 就緒;0 = 還沒掛好 |
| `+112` | `QS_H_LAY_TAKEN` | 1 = 已在掛上後第一次版面載入記下快照;0 = 掛上後**還沒有任何版面載入**經過這層 |
| `+116..+139` | 三組 `{layout, tree}` | 快照當時快取裡三個 QS 版面與它們的 tree;tree 0 = 已放掉或已忘記;**非 0 且仍等於版面目前的 tree(`[layout+0xC]`)= 還沒被放掉** |
| `+140` | `QS_H_RELEASES` | 放掉的次數;0 = 從沒放過 |
| `+148` | 狀態字 | `0x80000001` = pack 登記失敗(QS 會維持原廠) |

判讀:
- `+112 = 0`:開機後沒有版面經過 `C05D90F0` → 開一次 QS 再讀。
- `+112 = 1`、`+140 = 0`、快照 tree 仍等於目前 tree:放掉的條件沒成立 —— 再讀 `RCACHE 0xC37B7254 +4`(快取握的版面)、
  `+0x28`(忙碌)、tree `+0xC`(參考數應為 1)、`[layout+4]+0x84C / +0x848`(目前/保留畫面);哪一個不符就是原因。
- `+140 ≥ 1` 而仍無自訂圖:看 `+148` 與 `gui image log` 是否有載入失敗;再看字串層(`C05E5B58` 鏈)`chain.qs` 讀出的
  共用 id 與各層 enum。

**確認 v3 行為(§4.1,已由反組譯坐實，上機複核)要讀的字**(QS 關著時、載入非 QS 版面前後各讀一次;L = `[0xC37B7254+4]`):
- 放掉前:`[L+0xC]`(tree)非 0、`[tree+0xC]` = 1、`0xC37B7254+0x28` = 0、`[L+4]+0x84C / +0x848` 都不是 L。
- 放掉後:`[0xC37B7254+4]` = 0、`[L+0xC]` = 0;各 QS 層標頭 `+140` 加 1、`+116..+139` 那組 tree 為 0、`+144` 為 0。
- 之後打開 QS 一次:`[L+0xC]` 是新的非 0 tree、`[0xC37B7254+4]` 又是 L(快取重新接手)、畫面上有自訂圖。


## 8. 未解

- Jose 選擇時如何讓 `F2..F4` 指向目前格式的圖(別名表 `C0734AC8` 的寫入者沒找到,可能在 picker 或 regseltail)。
- N = 4..7 的游標 clip、底列 x 起點、popup 位移。`Cursor` 名與格數的關係。
- 底列 8 個標籤物件以外,原廠還有沒有第 9 個可用(上限)。
- `C2358981` 的 −46 是「置中」還是別的規則。

## 9. 給 d9 的 API 草案(OG3K/OG2K 改用 QS 共用層)

使用者 2026-10-06 決定 OG3K/OG2K 也改用共用層。`og_v3.c` / `build_v3_og.py` 由 d9 改,uishare 提供下列函式庫。
**草案,未實作**;名字與欄位在 Phase 2 可能小改,改了會先通知。

### 9.1 建置端(Python,`ui/qs.py`,接在現有 `ui.options` 旁)

```python
blk = ui.options.resolution_qs(        # 取代 options.resolution();Settings + QS 一次產生
    label='OG3K 3008x2000',            # Settings 清單列(CSV_ADD)
    summary='OG3K',                    # 收合摘要(per-state)
    footer='OG3K',                     # QS 底列第 k 格的文字
    images=('QS', 'SET', 'FONT'),   # 短名;建置時改成 '<檔名>_QS' 等(§9.4)
    pack=pack_bytes,                   # 私有 NBR pack(OG 現在的 UI_PACK 內容)
    enum=4,                            # 存進設定區的解析度值(轉換表的 (enum, k))
)
```

產生的 FPUI 區塊包含:CSV_ADD(拿到 k)、Settings 的 PAGE/ADDF/SETSTR_SLOT(同 `resolution()`)、
**新 op `QS_OPTION`**(k、底列字串、三個圖名字串、enum、pack 片段)。字串照舊走 `blk.string`/字串層。
`EXPECT state 2` 拿掉 —— k 由開機時決定,不再要求第 3 列。

### 9.2 執行期(C,編進 sup,跟 `uia_apply` 同一個 unit)

| 函式 | 做什麼 |
|---|---|
| `uia_apply(block, len, &outcome)` | **簽名不變**。遇到 `QS_OPTION` 時呼叫下面的 `qs_install` |
| `int qs_install(const struct sl_svc *svc, void *layer_mem, const struct qs_option *opt)` | claim `C05E6400`、`C05D90F0`、`C05E84D8`、`C06BDA3C/40` 為 **SHARED_UI**(任一失敗 → 回錯誤,sup 照規則 RELEASE);掛三層(標頭 `+0 檔名`);在共用轉換表追加 (enum, k);登記 pack |
| `uint32_t qs_layer_bytes(void)` | `layer_mem` 要多大(三個 handler + 351 筆配方 ≈ 6 KB,放在 sup 自己的塊裡) |

`struct qs_option`(由 `QS_OPTION` op 填,sup 不用自己組):`k`、`enum`、`footer_str`、`img_str[3]`、
`pack`/`pack_len`、`version`。

### 9.3 OG 這邊要拿掉的

| 現在(og3k_ui.S / og_v3.c) | 之後 |
|---|---|
| `ui_record`(`C05E6400`)+ 360 筆 `ui_records`/`ui_edits` 表 | 拿掉;351 筆第三狀態配方、QS max、Cursor、底列由 uishare 層做 |
| `ui_layout`(`C05D90F0`)、`REQ`/`LLOG` 狀態 | 拿掉;uishare 的重新解析層 |
| `ui_load`/`ensure_ui_pack`(`C05E84D8`)、`STATE` 的 pack 欄位 | 拿掉;pack 交給 `QS_OPTION` |
| `ui_aliases`(5 個 STRBASE:`FP3K_QS/SET/FONT` 的 F2..F4、摘要 F6)、`build_v3_og.py` 走 FPUI 改 STRBASE 位址那段、`og_v3.c` 改完重算 FPUI `+0x0C` FNV 那段 | 拿掉(d9 自己改);圖名與摘要由字串層 + §4.3 的共用 id 處理 |
| 三個 Thumb hook 的 claim(EXCL) | 改由 `qs_install` claim SHARED_UI |
| 「選的是第 3 列(state 2)」的假設、原廠轉換表的空對 (4,2) | 改用 k 與共用轉換表 |
| 留下的 | 錄影核心、picker、donor 時序、GTAB 等**全部不變**;只有 UI 的部分換掉 |

### 9.4 OG3K 與 OG2K 兩份描述;圖名衝突

**規則:enum 由 sup 宣告、固定;k 只是畫面上的位置(CSV 列、底列格、轉換表裡的列號),絕不影響 enum。**
OG 核心寫死 enum 4(`og3ksel` 的 `cmp r1,#4`、`og3krestore` 的 `OG_ENUM 4`、`og3kfmt`)—— 這些全部不用改。
轉換表存 (enum, k):k 會隨載入順序變,enum 不會;核心讀的設定值永遠是 enum。

OG2K 與 OG3K 的 360 筆選取相同(同一份 layout plan),差別只在標籤、摘要、pack 裡的圖(`_hud_tile` 依 label 產生)。
共用層把 360 筆變成 uishare 的一份配方,兩者只剩描述不同:

```
OG3K: label='OG3K 3008x2000', summary='OG3K', footer='OG3K', images=('QS','SET','FONT'), pack=OG3K 的, enum=4
OG2K: label='OG2K 2000x1334', summary='OG2K', footer='OG2K', images=('QS','SET','FONT'), pack=OG2K 的, enum=4
```

**圖名衝突**(d9):OG2K 現在的別名與 pack 資源名仍是 `OG3K_QS/SET/FONT`(`ALIAS_STRINGS` 寫死)。
共用版規則:**sup 只給短名,`qs_install` 在建置時把 pack 裡的資源名與字串都改成 `<檔名>_<短名>`**
(例如 `30OG2K_QS`;檔名由建置參數給,與 loader 的檔名一致)。兩個 pack 因此不可能同名。
pack 間仍然同名時(例如手動改檔名造成):後登記的那個 pack 照樣登記,但 `qs_install` 先查名字,撞名就回錯誤 → RELEASE,不靜靜顯示別人的圖。

兩者 enum 相同(都是 4)且錄影核心同位址,本來就互斥(EXCL);共用層不改變這點。
⚠ enum 4 是 MENU_MERGE_PLAN §4 的待核對項(第一守則),本筆記不處理。

### 9.5 時機:entry 決定一切,之後只剩等待

**使用者 2026-10-06(經 d9):UI 裝不起來就整個 sup 不載入(RELEASE),不接受「錄影核心載入、選單缺一格」。**
hook 一旦武裝就不能安全卸載,所以 `qs_install` 必須**在 entry 時就判斷完能不能裝成**,並預留一切之後要用的資源。
GUI 就緒之後只做「不可能失敗」的動作。Fast Start 3 暖開機時 GUI 太晚**不算失敗**,只是等待。

#### 每一種失敗搬到哪裡

| 失敗 | 在 entry 怎麼判定 / 預留 | 就緒後還會不會發生 |
|---|---|---|
| 掛點 claim(`C05E6400/84D8/90F0`、`C06BDA3C/40`、`C05E5B58/61C8/61E0`、`C05E5BEC`) | claim 本身就在 entry;任一被拒 → RELEASE | 不會 |
| 名字撞 | 資源名一律 `<檔名>_<短名>`,檔名在 `\fpSup` 裡唯一;entry 再走一次 `svc->holder(C05E84D8, i)` 確認沒有同檔名;**與原廠 NBR 撞名在建置時對原廠映像檢查** | 不會 |
| CSV_ADD(拿到 k) | CSV 複本在共用取檔表(`C05E5BEC`,資料在堆積,不需要 GUI)→ entry 就加列、拿到 k | 不會 |
| 字串發號 | 字串層在 entry 掛好、位移在 entry 發(只用原廠池映像,不需要 GUI) | 不會 |
| 對照表空間 | entry 用 `cave_alloc` 建/找共用表並寫入 (enum, k);表滿 → RELEASE | 不會 |
| N 超出支援範圍 | entry 時 N = 目前列數(含自己);不在 f(N) 表裡 → RELEASE。之後載入的 sup 若讓 N 超出,**是它自己 RELEASE**,所以最終的 N 一定是支援的值 | 不會 |
| 複本緩衝容量 | 緩衝大小在建置時對「原廠最大記錄 + N 最大時所有層的增量」證明足夠(OG 的 512 B、Jose 的 1 KiB 都上機跑過);entry 檢查 N ≤ 表的上限 | 不會 |
| Settings 頁的 PAGE(controller max、摘要) | 今天的 `uia_apply` 需要 reader(`find_entry`)才失敗於 FS3。改成兩段:**entry 從映像複製頁面、套上 +1 與摘要**(共用頁面複本用 `claim_res(頁面, SHARED_UI)` 找到前一個 sup 的複本,合作 +1);**就緒時只把 reader 的頁面項目指到複本**。頁面項目是否存在由 entry 的 GUARD 對映像確定 | 不會(只剩寫指標) |
| pack 格式 | 建置時與 entry 都驗:標頭、長度、雜湊、對齊(§9.5 上方的搬移) | 不會 |

#### 就緒時做的事(都不會失敗)

1. 把 reader 的頁面項目指到 entry 做好的頁面複本(冪等;多層都做,結果相同)。
2. 登記私有 pack(見下方殘留風險)。
3. 狀態字 0 → 1;重新解析層(§4.1)放掉就緒前解析的 QS 版面。

#### entry 判定不了的,剩一項

**原廠 NBR 載入函式在登記 pack 時自己配記憶體。** 這個配置可能失敗,而它發生在 GUI 就緒時,不在 entry。處理方式:

- **冷開機/一般 AutoRun**:entry 時 resource context 通常已經存在 → **在 entry 就登記 pack**,失敗就 RELEASE。延後只發生在 Fast Start 3 暖開機。
- **延後的情況**:entry 時**預先保留**同樣大小的堆積(大小在建置時用原廠載入器量出,寫進描述),
  就緒時先釋放保留的那塊、立刻呼叫載入函式。同一個 GUI 任務內連續做,中間被別人拿走的機會很小,但**不是零**。
- 萬一仍失敗:狀態字 `0x8000_0001`、LOAD.LOG 一行;該層對所有記錄改成直接交給 next(QS 維持原廠,不出現半套畫面)。
  這是唯一會出現「錄影核心在、QS 缺這一格」的路,**要使用者知道並接受,或要求 Fast Start 3 時就不載入這類 sup**(要決定)。

「GUI 一直沒來」不是失敗:層一直未就緒、等同原廠;主機端看狀態字一直是 0。

#### 狀態字與 LOAD.LOG

層標頭 `+24`:0 未就緒、1 就緒、`0x8000_0001` 就緒時 pack 登記失敗(唯一的延後失敗)。
entry 時的失敗不進狀態字(sup 已 RELEASE),只寫 LOAD.LOG:`QS <檔名> FAIL <原因> <k> <N>`,
原因是 `CLAIM` / `NAME` / `CSV` / `STR` / `TABLE` / `NMAX` / `PAGE` / `PACK` / `RESERVE`;
成功寫 `QS <檔名> OK <k> <N>`,就緒寫 `QS <檔名> READY`。

- **pack 位置與對齊**(d9 修正):加載器 `mem_get` 給的塊基底**只保證 8 對齊**(今天上機的 `0x451B8BC0` 剛好 64 對齊是巧合)。
  原廠 NBR pack 實際要幾對齊**查不到文件**:NBU 記錄本身是不對齊的(例如 `C1A7246B`),解析器容得下;
  pack 裡的 `.xci` 圖是否交給要求對齊的 DMA/解碼器,沒追。**已知能用的只有 32(舊卡 `0xC0793060`,v0.2.8a 上機)與 64**。
  做法(**不動 sloader**):用 LOADER_V3 §3 已有的規則「塊內對齊由 block_size 預留、執行期對齊」——
  sup 的 block_size 多留 64 B,`qs_install` 在 entry 把 pack 搬到塊內第一個 64 對齊的位置。
  要改成「加載器保證塊 64 對齊」才需要動 sloader,那得先跟 58 協調。
- **pack 登記的兩個觸發**(照 OG 現在的做法):① `C05E84D8` 載入原廠 NBR(`0xC0D22400`)時;② `C05E6400` 每筆原廠記錄時,
  以 `reader+8` 的 resource context 為鍵、每個 context 一次。
- ~~底列缺圖風險~~ **已查明**(d9):不是 pack 登記,是 kind 4 記錄寫死別名 id `FFFFFFF3`(§1.1)。`ensure_ui_pack` 的兩個觸發可以照搬。

### 9.6 要先跟 sigmafp-re-58 協調的

它規劃的「All Sups」選單(`projects/usb-shell-sup/notes/ALL_SUPS_MENU.md`)也會動 uishare/sloader。
本草案動到 sloader 的只有:**不加新服務**(用現有 `holder`、`self`、`cave_alloc`)。uishare 會新增檔案
(`qs_*.S/.c`、`ui/qs.py`)與一個 FPUI op(`QS_OPTION`)—— op 編號要跟它的計畫對一下,避免撞號。

### 11.2 字串層 v5 與 QS 層 v2:自描述標頭(2026-10-07)—— 最後一次因入口偏移而全體重建

- 標頭存自己的長度與每個入口的偏移;每個入口前一個字是它自己的偏移。認層、走鏈、找發號者一律
  「入口 − [入口−4]」再核對標頭裡的偏移,**不再用寫死的入口位移**(`ui_pool.c header_of`、`qs_layer.c qs_header_of`、
  `ui/chain.py header_of`)。字串層標頭 72 B(+56 長度、+60/+64/+68 三入口);QS 層標頭 176 B(+160 長度、+164/+168/+172)。
- 版本只在欄位語意/佈局改變時升;新欄位只往尾端加,讀 +72(QS +176)之後的欄位要先看長度。
- 測試 `test_layer_compat.py`:同一個模型裡連結兩個 uishare 建置(B 的 owns/remain 入口移位、填 `udf`),A/B/A 與 B 在最外層
  都互認、走鏈、讀 QSCUR,unicorn 實跑兩個建置的 ARM 碼;`test_qs_layer.TestTwoBuilds` 對 QS 層做同樣的事。
  mutation:讀者改回寫死偏移(C、QS、chain.py)與 NEXT_OWN 用自己的偏移都會失敗;NEXT_RES 那個是等價變異
  (resolve 一律緊接標頭,兩個建置相同)。

## 12. 自訂格式的選擇不進設定區(使用者 2026-10-07 決定;設計,未實作)

取代 k+2 與固定登記表兩案。**設定區只存原廠合法值(UHD 3 / FHD 2);「選的是哪個自訂格式」執行期記在共用變數、
跨關機記在各 sup 自己的 BIN。** = MENU_MERGE_PLAN §2/§4 的 `stock_value + marker`。

### 12.1 執行期:選單照原廠運作的內部值,加一個以檔名識別的「目前選擇」

- 韌體的選單、QS、游標、摘要都靠「值 ↔ 列」轉換(`C06BDA30` 的 (值, 列) 表)。游標要停在我們那列,**RAM 裡的屬性值
  (`0xC31ACC1C+0x14`)必須跟原廠列不同**;否則得 hook 每個讀目前值的 UI 路徑,漏一個就錯。所以屬性值在 RAM 裡用
  **內部值 E = k + 2**(這次開機 CSV_ADD 給的列號,只在 RAM;今天 OG 的 4、Jose 的 5/8/9 也是這樣跑)。
  E 是位置性的,但只活一次開機;跨開機的身分是檔名(12.3)。
- **「目前選擇」變數(使用者指定)**:放在 QS 共用 id 發號者(第一個 `claim_res(UIS_RES_QSCUR)` 的 sup)**字串層標頭的新欄位**
  `H_SEL`(8 bytes 檔名,0 = 原廠 UHD/FHD)。其他 sup 經 `holder(UIS_RES_QSCUR, 0)` + 檔名沿字串鏈找到那層讀寫
  —— 跟 QS 共用 id 同一機制,不改 sloader、不放 LOADER.BIN。
  - ⚠ 字串層標頭再 +2 字(`H_SEL`)→ **字串層版本 3→4**,入口位移再動;同卡所有用字串層的 sup 要一起重建(同 v3 那次)。
- **誰寫 `H_SEL`**:解析度 setter `C005C020`(r0 物件、r1 值、r2 旗標)上加一層 CHAIN(uishare QS 層的第四個入口,SHARED_UI):
  呼叫下一層之後,值 == 我的 E → `H_SEL` = 我的檔名、設欠帳(寫我的旗標 1);`H_SEL` == 我而值不是我的 E → 清成 0、欠帳(旗標 0)。
  ⚠ OG(`og3k_plan.SEL_HOOK = 0xC005C020`)與 Jose 都掛這個點(EXCL);要改成串在這層之下或改用它(見 12.6)。
- **錄影核心**改成讀 marker:`H_SEL` == 自己的檔名,或等價地「目前值 == 自己這次的 E」(E 由 QS_OPTION 在 commit 時寫進 sup 自己的資料,仿 STRBASE)。

### 12.2 設定區只存原廠值(第一守則)

- 每個 sup 宣告 `stock_value`(2 或 3):選能接受該格式所有格率的原廠解析度(§12.9 (d)):OG4K/OG3.5K → UHD(3),OG3K/OG2K/S16 → FHD(2)。
  比較「維持選前的值」:要再記一個值,拔卡後結果取決於使用者上次停在哪,較難預期;最接近值固定、可測。
- **存檔替換層**:在設定存檔的共同入口(關機存檔 `FUN_c0088bb0`/`C0088C60`,raw-view 與 res-lab 已 CHAIN 掛在那)加一層:
  目前值不是 2/3 → 先把**要被序列化的那個欄位**改成 `H_SEL` 那個 sup 的 `stock_value`,再交給下一層;存完把 RAM 值改回 E
  (關機途中不必,但 `menu save` 之後相機還要繼續用)。
- **做之前的離線 RE 關卡**:①解析度實際被序列化的欄位(屬性物件 `+0x14`、`XC_UserSetting 0xC3074BE8..`、
  `XC_CommonSaveData 0xC307523C..` 哪一個);②**所有**觸發存檔的路徑(關機、`menu save`、設定重置、PTP `XCSetLong`…)
  是否都經過那個共同點;③存檔前是否已有別的副本被複製。任一條路徑沒蓋到,第一守則就不成立。
- 拔電池:不存檔 → flash 留上次的原廠值。拿掉 sup:設定區本來就是原廠值。
- ⚠ 搭配值:例如 S16@100fps 存成「FHD + 100 fps」,原廠 FHD 不支援 100 → 開機是否正規化要一起查;必要時替換層也把
  格率換成該原廠解析度可用的值(同樣只存原廠合法值)。

### 12.3 跨關機:由發號者一個檔記「被選中的 sup 檔名」(使用者 2026-10-07 改定)

- **誰存**:本次開機的 QS 發號者(第一個 `claim_res(UIS_RES_QSCUR)` 的 sup = 載入順序第一個 QS sup)。
- **存哪**:發號者自己 BIN 的 **+32 小設定區**(照 raw-view 第 5 步,上機過),內容 `{'FSEL', 檔名 8 B(空 = 原廠), 0…}` 32 B,
  建置時全 0。寫回自己的檔用 entry 的 r2(自己的路徑)。
- **開機**:發號者的 BIN 已整檔在塊裡 → entry 直接讀 +32,不用開檔。檔名對到卡上一個已載入(KEEP)的 QS sup → 它被選中
  (`H_SEL` = 該檔名),GUI 就緒、非錄影中由**被選中的那個 sup** 用原廠 setter 設成它這次的 E(MovSigProcess 重啟沿用 og3krestore)。
  空、卡上沒有那個 sup、或發號者換人(新的第一個 QS sup 沒有記錄)→ 原廠 `stock_value`。
  ⚠ 發號者在自己 entry 時,後面的 sup 還沒載入:比對要延後到 GUI 就緒(那時所有 sup 都在),用 holder 名單確認那個檔名在場。
- **切換**:只寫發號者一個檔 —— `H_SEL` 改變 → 欠帳;**非錄影中**,下一次版面載入且值停留 ~0.5 s 時寫(關機存檔那層補漏)。
  不再需要各格式旗標,也沒有放手/舉手兩段寫入;中途失敗 = 檔案仍是舊名字(或寫壞 → 讀回不符,下次重寫)。
- **寫法**(記憶 card-file-delete、file-api-open-modes):open mode 7(覆寫不截斷,只改 +32 的 32 B,檔長不變)→ seek 32 → 寫 32 B
  → seek 32 → **讀回比對**;open 回 0 是失敗;seek 失敗不寫。沒寫成:塊裡標成未存,下次再寫;不重試迴圈、不影響錄影。
- 卡唯讀/寫失敗:RAM 的 `H_SEL` 照常;下次開機是上次寫成功的狀態或原廠值。拔掉被選中的 sup:名字對不到 → 原廠值。
- 換組合:發號者是「第一個 QS sup」,組合變了發號者可能換人 → 新發號者沒有記錄 → 原廠值(使用者接受)。

### 12.4 「內部值」只在 RAM

- CSV/轉換表仍需要不同的數值才能分列 → 內部值 E 存在(屬性物件、轉換表,都在 RAM)。
- 證明它不出 RAM:12.2 的 RE 列出所有序列化路徑,替換層掛在共同點;unicorn 跑真的存檔函式(從替換層進入),
  斷言送進序列化的欄位 ∈ {2, 3}(內部值 4..9 各試),拿掉替換層必須失敗。

### 12.5 第一守則的證明

1. 內部值只在 RAM(12.4)。2. 每條存檔路徑都經替換層(12.2 RE + unicorn 斷言 + mutation)。
3. 上機:選自訂格式 → 正常關機 → 拔卡 → 開機,解析度必須是 `stock_value`(讀回屬性值與設定區副本)。
4. 相機 flash 不多任何值;卡上只多各 sup BIN 裡 +32 的旗標。

### 12.6 各方要改的

| 誰 | 改什麼 |
|---|---|
| uishare | 字串層 v4(`H_SEL`,新版能認 v3 層);QS_OPTION 加 `stock_value` 與「E 寫回 sup 資料」;`C005C020` setter CHAIN 層;存檔前正規化(關機 `C0023C68`)+ `C0088C60` 掃 bank 槽;開機套用;發號者 +32 檔名讀寫(mode 7、讀回);測試 |
| OG(d9) | 核心 `cmp r1,#4`(og3ksel)、og3kfmt、og3krestore 的 `OG_ENUM 4` → 讀 E(或 `H_SEL` == 自己);`C005C020` 自己的 hook 改成串在 uishare 層下(CHAIN)或把它要做的事移到 uishare 層之後的回呼;og3krestore 的開機還原改由 uishare 套用(它的 MovSigProcess 步驟搬進 uishare);OGSAVE(跨電源狀態)拿掉;30OG3K/30OG2K 的 BIN 預留 +32 設定區(可能是發號者);`resolution_qs(stock_value=3)` |
| Jose 31FMT(我們的 builder) | registry `+0` 在 commit 時寫 E;他掛的 `C005C020`/`C005C054`(regseltail)要串在 uishare 層之下;BIN 預留 +32 設定區(它可能是發號者);疑似常數 1–4 處確認 |
| res-lab、raw-view、lossless | 只因字串層 v4 重建;raw-view 已 CHAIN 在關機存檔,與替換層同點,順序無關(替換層只改解析度欄位) |

### 12.7 工作量

| 項目 | 估計 |
|---|---|
| 離線 RE:序列化欄位、所有存檔路徑、搭配值正規化(12.2 關卡) | 1–1.5 天 |
| uishare:字串層 v4 + 依賴方回歸 | 0.5 天 |
| uishare:QS_OPTION(stock_value、E 寫回)、setter CHAIN 層、存檔替換層 | 1.5 天 |
| uishare:旗標讀寫(+32、mode 7、讀回、清舊設新、欠帳時機)、開機套用 | 1–1.5 天 |
| 測試:unicorn 存檔斷言 + mutation、setter 層、換組合/拔 sup/兩旗標皆設的開機模擬、寫入失敗 | 1 天 |
| d9:OG 核心改讀 E、setter hook 改串、og3krestore/OGSAVE 移除、BIN 設定區 | 1 天 |
| Jose 31FMT:registry 寫 E、兩個 hook 改串、三旗標、常數確認 | 1 天 |
| 上機:12.5 第 3 條、撥開關關機寫卡、P4 第 2 步 | 0.5–1 天 |

合計約 8–9 天;RE 若發現存檔路徑不集中,替換層要分散掛,再加 1–2 天。

### 12.8 離線 RE 關卡:第一輪結果(2026-10-07,只讀反編譯 `out/decomp/full`,未上機)

**已確定**

1. **解析度的原始值存在哪**:選單設定儲存體單例 `MenuSettingStorage_w71c1` = `0xC31AE3B0`(取得函式 `FUN_c008acf0`);
   工作 bank 在 `+0x4F0C`(`+0xB19C` 旗標設時改用 `+0xA7D4`);解析度在 **bank `+0x790`**,一般情況 = `0xC31B3A4C`(2026-10-07 訂正:先前誤算成 `0xC31AFA4C`)。
   路徑:屬性物件 `0xC31ACC1C` 的 vtable `+0x18`(`MenuItem<eXC_MenuMovRecSize>::v4` `C00A8BA0`)→ `FUN_c00b81e8`
   → 分派表 `0xC07461DC[0x69]` = `FUN_c00b5c28` → bank + 0x790。屬性物件 `+0x14` 只是衍生值快取。
2. **選單設定怎麼被存**:`XC_SettingSaveDataMgrCore<MenuSetting>`。`v2`(`FUN_c0088bb0`)先把工作 bank 存回它在映像裡的槽
   (vtable `+0x3C` = `v13` `C0089250`),再經 **`C0088C60` 呼叫 `FUN_c0088660`** 把 0x4F00 bytes 的映像(`S+0xC`,
   含各 bank 槽與 `+0x2A40` 的 `'MDL\0'` 標頭)複製進存檔緩衝並設髒旗標;**`FUN_c0088660` 全映像只有這一個呼叫點** ——
   選單設定這一份的單一咽喉點成立。`menu save` 也走 `FUN_c0088bb0`(raw-view 第 5 步實測)。
3. **其他 bank**:自訂模式(C1/C2/C3 之類)存 bank 也走 `v13` 把工作 bank 複製進槽 → 選了自訂格式時存自訂 bank,
   槽裡會帶內部值,下一次 `v2` 就寫出去。所以替換不能只改工作 bank,**要在 `C0088C60` 掃全部槽**
   (槽指標用韌體自己的 vtable `+0x54`(bank 號)在執行期取,不要猜版面)。

**新發現的缺口(第一守則)**

4. **存檔管理員有三個**(RTTI):`<MenuSetting>`、`<UserSettingSaveData>`(`XC_UserSetting`,資料 `0xC3074BE8..`)、
   `<XC_CommonSaveData>`。`XC_UserSetting` 裡有**解析度衍生出來的錄影寬高**(`CANVAS_IS_NOT_THE_SETTINGS_BLOCK.md` §1:
   `SetMovRecSize` → 主設定 `0xC3074BE8` = 寬×高)。選了自訂格式時那裡是自訂尺寸(例 3032×2012),
   由 UserSetting 管理員在關機時存進 flash —— **只換列舉值不夠**;今天的 OG 與 Jose 其實已經會把自訂寬高存進去。
5. **因此建議改用韌體自己正規化**:在**所有**存檔管理員執行之前,用原廠 setter `C005C020` 把值設成 `stock_value`
   → 韌體自己重算工作 bank、`XC_UserSetting` 的寬高與其他衍生值(格率限制也走它自己的規則);存完(若不是關機,
   例如 `menu save`)再設回 E。這樣三個管理員看到的都是原廠一致的狀態,不用逐欄位替換。
   `C0088C60` 的掃槽仍保留(管自訂 bank)。

**還沒查到、關卡尚未通過的**

- (a) **PowerOffMgr 的存檔順序與「三個管理員之前」的那個點**(或 `menu save`、PTP、設定重置各自的入口)—— 正規化要掛在那。
- (b) 所有觸發 `<UserSettingSaveData>`/`<XC_CommonSaveData>` 存檔的路徑。
- (c) `XC_UserSetting` 裡由解析度衍生的欄位清單(用來寫 unicorn 斷言)。
- (d) 換成 `stock_value` 時格率是否被原廠規則正規化(Jose/OG 的 `regfrmax C00A87E4` 等 hook 在那時看到的是原廠值,
  應該走原廠行為 —— 要確認)。
- (e) 在關機流程裡呼叫 setter 的副作用(UI、LV 重啟)是否安全。

估計再 1 天離線 RE 可以回答 (a)–(d);(e) 需要上機。

### 12.9 離線 RE 關卡:第二輪(a)–(d)(2026-10-07,反編譯 + 映像,未上機)

**(a) 所有存檔管理員之前的那一點:`C0023C68`。** `XC_PowerOffMgr::v1`(`C0023C50`,關機執行緒)依序做:
`C0023C68 bl FUN_c0023eb8(this, 原因)` → 逐一呼叫關機監聽清單(`+0xC..+0x34`,各自 vtable `+0xC`)→
`C0023CEC/F0`:`FUN_c01f98f0(FUN_c0021368())` 寫出 `0xC30749A8` 的存檔緩衝 → `C0023CF4/F8`:`FUN_c0022820(FUN_c0021478())`
寫出選單設定的緩衝(`0xC3074ABC`,就是 `C0088C60` 填的那個)。所以在 **`C0023C68` 這個呼叫點掛一層(CHAIN,呼叫點 hook)**,
先正規化再呼叫原本的 `FUN_c0023eb8`,就在所有監聽與兩次寫出之前。⚠ 這個函式自己的框架是 4 mod 8(push 6 + sub 0x24),
hook 要用絕對對齊(§4 的做法)。

**(b) 寫進 flash 的路徑只有兩個寫出函式**(全映像掃 BL):
- `FUN_c0022820`(選單設定緩衝):**只有** `C0023CF8`(關機)。`menu save` / `v2` 只是填緩衝(`C0088C60`),不寫 flash。
- `FUN_c01f98f0`(`0xC30749A8` 緩衝):`C0023CF0`(關機)與 **`C04F0098`**,後者在 `FUN_c04eff90` —— 回傳 `0x2001/0x2002`
  的 PTP 廠商指令處理器,把主機送來的資料寫進存檔區後立刻寫出。主機觸發、罕見,但寫出時會把當下 RAM 的
  `XC_UserSetting` 一起寫出 → **第二個正規化點:`C04F0094`/`C04F0098` 前後**(進來時正規化、寫出後設回 E)。

**(c) 衍生值**:`XC_SettingSaveDataMgrCore<UserSettingSaveData>::v1`(`C0021F58`)把 `XC_UserSetting` 資料
(`0xC3074BE8`,0x5E4 bytes,type 8)整塊當存檔記錄算校驗 —— **`XC_UserSetting` 本身就是存檔記錄**,它的每個欄位都會進 flash,
包括由解析度衍生的錄影寬高(`CANVAS_IS_NOT_THE_SETTINGS_BLOCK.md`:兩對寬高)。setter 設回 `stock_value` 會讓韌體重算它們。
✎ **2026-10-07 更正**:OG 目前**不**改 `0xC074222C` —— `og3ksel.S` 只宣告 `.equ PAIR` 而從未使用(v2 起不改,
setter 收到 4 時原廠查不到尺寸就保持原值,`XC_UserSetting` 寬高停在上一個原廠值;`projects/jose-hurtado/notes/JOSE_VS_OURS.md` §6)。
以下這段是當初的假設,保留作紀錄:
⚠ **OG 的 setter hook 會改 `MenuItemMovieRecSize` 的 enum-2 尺寸表(`0xC074222C`)**:若設回 FHD 時那張表還是 OG 的尺寸,
重算出來的仍是自訂寬高 → OG 必須在值是原廠值時把表還原(d9 確認/修)。斷言用:正規化後 `[0xC3074BE8]`/`+4` 等於原廠表
(`{2: 1920×1080, 3: 3840×2160}`)的值。

**(d) 格率**:靜態沒找到「換解析度時格率被夾」的單一函式(格率清單走 `regfrclass C04A121C`/`regfrmax C00A87E4` 這類,
Jose 也 hook 了)。**低風險做法:`stock_value` 選一個能接受該格式所有格率的原廠解析度**:
fp 的 FHD 到 119.88、UHD 到 29.97,所以 **OG3K(到 59.94)、OG2K、S16 → FHD;OG4K、OG3.5K(到 29.97)→ UHD**。
(OG3K 原本建議 UHD,改成 FHD。)原廠在開機時如何處理不合法組合仍未知,留給 (e) 上機。

**結論**:關卡可以過 —— 三個寫出路徑(關機兩個、PTP 一個)都有可掛的共同點;剩下的只有 (e) 上機與 OG 尺寸表還原。

### 12.10 改採 Sensor Lab 240「做法 1」:從頭到尾不改原廠設定(使用者 2026-10-07;評估,未實作)

取代 §12.2/§12.9 的存檔前正規化(不需要 `C0023C68`/`C04F0098`)。

**240 做法 1 實際怎麼做(`imx410_rate_native.S` ROLE 6/7,`IMX410_FULL_DESIGN.md` §8.11)**:
列 ↔ 值的轉換表裡,**第 9 列(240)用的就是原廠值 7**(與 119.88 那列重複);原廠照常把列轉成 7 存進設定。
hook 只掛在 `MV_FrameRate` 的兩個 setter 回呼(`FUN_c057a6f8`、`FUN_c05821b8`,一個是 Settings、一個是另一條提交路徑):
看提交的列號 → 記號 `RATE240` = 0/1 → **把兩個值 7 的對子調順序**,讓「值 → 列」找到使用者選的那列(游標停對)
→ 摘要狀態字也跟著換。**從不寫原廠設定。**

**套到解析度(UI 側,uishare)**

- 轉換表(§4.4 已由我們建)裡,每個格式的列用**它的 `stock_value`**(FHD 2 或 UHD 3),不用內部值;E 完全不需要。
- 掛 `MV_MovRecSize`(解析度的 UI 變數)的 setter 回呼(對應 240 的兩個;**位址要 RE 找**,240 那邊轉換器槽是 `+0x60`/`+0x64`):
  提交列號 k → `H_SEL` = 列 k 的 sup 檔名(選 UHD/FHD 列 → 0)→ 把同值的對子調到最前(游標/QS 停在選的那列)→ 摘要狀態字換。
  原廠照常把 FHD/UHD 存進設定。
- QS:大圖磚第三狀態由「值 → 列」來,列 ≥ 2 一律顯示 t=66 那格(§1);圖名由 `H_SEL` 那個 sup 的字串層回答(§4.3 機制)。
- 開機:發號者 +32 的檔名對到在場的 sup → `H_SEL` = 它、對子排序 → 游標停在它那列;設定區本來就是它的 `stock_value`,
  不必呼叫 setter。若設定區的值與它的 `stock_value` 不同(使用者沒插卡時改過)→ 不套用,`H_SEL` = 0(照 raw-view 的規則)。
- **要 hook 的點**:setter 回呼 **2 個**(Settings 與 QS 各一條提交路徑,待 RE;240 也是兩個)+ 解析度摘要狀態的資料表(240 是
  `C06CB6D0 → C2E44EE4`,解析度的對應表待 RE,預期是資料不是 hook)。不需要 getter hook、不需要存檔 hook。
- **第一守則**:setter 只會拿到原廠值 → 設定區、`XC_UserSetting` 都只有原廠值與原廠算出的衍生值;**不需要正規化**。

**錄影側**

- ✎ **2026-10-07 更正**:下面「OG 改原廠尺寸表」的前提不成立 —— OG 不寫 `0xC074222C`(`og3ksel.S` 的 `PAIR` 未使用),
  `XC_UserSetting` 寬高停在原廠值,與 Jose 相同;剩下要確認的只有「enum 4 時原廠保持原值」這個實測在新路徑仍成立。原文保留:
- **OG(d9)**:現在改原廠尺寸表 `0xC074222C`(enum-2 寬高)→ 原廠重算出的 `XC_UserSetting` 寬高變成自訂,而它是存檔記錄
  (`C0021F58`,type 8,0x5E4)→ **違反做法 1**。要改成「原廠表不動,在消費點依 `H_SEL` 給自訂值」。已知的消費點
  (OG 筆記):錄影幾何來源 `CameraMgrSetting +0x40`(+16/+10)、record-start 的兩對寬高重算(`CANVAS…` §7)、DNG 尺寸、
  LV 顯示 profile(GTAB 預覽高)、picker(`FUN_c0437078`)。picker/GTAB/donor 時序寫的是 RAM 裡的韌體表,不進存檔,可留;
  **只有「讓原廠衍生出自訂寬高」那一條要換**。估 2–4 天(RE 消費點是大頭,d9 的領域)。
- **Jose**:他**沒有改** `0xC074222C`(值補丁是 CSV、`C1A709BC`、`C06BDA3C/40/48`、`C0D4DE04`、`C0CDC058` 解析度刷新清單、
  `C07426F8` crop 描述的函式指標、S16 模式表)—— 他的 enum 5/8/9 查不到原廠尺寸,`XC_UserSetting` 寬高停在原廠值。
  要改的只有「存了 5/8/9」這件事:轉換表改用 `stock_value`,registry `+0` 的 enum 比對改成比 `H_SEL`(他的 C 代碼沒有原始碼,
  用 commit 時寫入的方式把 registry `+0` 換成一個「我是否被選中」可比的值,或在 picker 前加一層判斷)。估 1–2 天。

**剩下會碰 flash 的路徑**:做法 1 下我們不寫任何原廠設定;要確認的只剩 ① OG 改完後 `XC_UserSetting` 寬高確實保持原廠值
(unicorn 斷言:選自訂格式前後 `0xC3074BE8` 寬高不變);② Jose 的 `regseltail` 會暫時翻 Crop Mode(`XC_UserSetting +0x334`),
翻轉中若剛好關機會存下翻轉值 —— 值本身是原廠合法的 0..3,不違反「只存原廠值」,但與使用者原設定不同(極小視窗)。

**工作量**:UI 側(uishare)RE 0.5 天 + 實作 1.5 天 + 測試 1 天;OG 2–4 天(d9);Jose 1–2 天;合計約 6–9 天。
比 §12.9 的正規化少了兩個存檔 hook 與關機中呼叫 setter 的風險,但多了 OG 的消費點改寫。

## 10. 要決定的

使用者 2026-10-06:QS 要能選(A);記錄 hook 改成每層套娃(本版 §2)。剩下:

1. N = 4..7 先拒絕(只認實測過的 3 和 8),還是用推測值、標成待驗?(f(N) 的表要靠 P4 上機補)
2. ~~固定字串 id~~ → 暫定改用共用的開機發號 id(§4.3,可行)。
3. ~~OG3K/OG2K 要不要也改~~ → **改**(使用者 2026-10-06);API 草案 §9。
4. Jose 的 20 個錄影 hook 要不要一起拆成每格式一層(不拆就只能拆 UI)。
5. ~~UI 失敗時要不要載入錄影核心~~ → **RELEASE**(使用者 2026-10-06);§9.5 把所有失敗移到 entry。
   剩一項:Fast Start 3 延後登記 pack 時原廠載入器配置失敗(已預留記憶體,機會很小但非零)—— 接受,還是 FS3 時就不載入這類 sup?
6. QS 與 All Sups 選單誰先做(都動 uishare/sloader)。

## 11. 實作狀態(Phase 2,2026-10-06,離線;未上機、未 commit)

只新增檔案,**沒改任何既有共用檔**(ui_pool/ui_apply/ui_strings/sloader 都沒動),所以依賴方不必重建。

| 檔案 | 內容 |
|---|---|
| `gen_qs_recipe.py` → `ui/qs_recipe.json` | 371 筆配方:clone 315、end 36、footer_label 12(k=2..7)、cursor 4、qs_max 2、footer_n8 2。來源 = FP3K plan(OG 的 360 筆去掉 Settings 摘要)+ Jose 的底列 3..7 與 N=8 值。建置時:clone 演算法必須逐位元組重現 FP3K plan;第 2 列 OG 與 Jose 必須相同;**插入位元組出現 ≥0xF0000000 的字就失敗**(§1.1) |
| `ui/qs.py` | Python 參考:每條規則對每筆記錄只有一個目標;`layer()` = 目前==目標→不動、目前==原廠→寫、其他→衝突;`encode()` = C 讀的表(6,980 B) |
| `qs_apply.c/.h` | C 套用器(純函式,Thumb -fropi 無重定位,約 1.3 KB) |
| `qs_layer.S` + `qs_layer.c/.h` | C05E6400 的層:標頭(檔名/版本/next/表/k/狀態/ids/N/…)、認層(原廠 pos + 複本標頭的檔名是 `C05E6400` 的持有者)、第一個要改的層在自己堆疊做 1,040 B 複本(記錄 512 + 給內層的 scratch 512)、內層就地改;`qs_check`(entry 時:掛點、N)與 `qs_hang`(commit,不會失敗) |
| `qs_build.py` | sup 要放進塊的位元組:`[層標頭+入口+replay][C][配方表]` 約 10.7 KB |

測試(全部 `python3 -B -m unittest`):

| 測試 | 數量 | 內容 |
|---|---|---|
| `test_qs` | 12 | N=3 一層 = FP3K plan(359 筆逐位元組);N=8 六層 = Jose 實際交給解析器的位元組;任意順序相同;掛兩次 = 一次;衝突;第 k 格只給 k;不支援的 N;緩衝夠(最大 488 B);配方無寫死別名 id;建置檢查抓得到 FP3K 的 `FFFFFFF3` |
| `test_qs_c` | 6 | C 與 Python:371 筆 × 17 種情境逐位元組相同;find/FNV;N 支援表;Thumb -fropi 無重定位 |
| `test_qs_layer` | 8 | unicorn 在真映像上:模擬加載器(self/holder/cave/publish)掛層,對 371 筆每筆從 `C05E6400` 呼叫:解析器拿到的 = Python 參考;reader 回到原廠基底、pos 跳過**原廠**長度;N=3 一層、N=4 兩層兩種順序結果相同、N=8 六層兩種順序;複本標頭名字是登記簿裡的;未列名的複本不信任(對照組:列名的會改);FNV 不符不碰;N 不支援 / 掛點是別人的 → `qs_check` 拒絕、掛點不動 |

mutation:`qs.py` 8/8、`qs_apply.c` 8/8(另一個是等價變異,已換掉)、`qs_layer.c` 10/10。
既有 uishare 測試(`test_ui_strings/ui_apply/ui_pool/options`)與新測試一起跑 103 項全過。

### 11.1 第二輪(2026-10-06 晚):三個共用層、對照表、QS_OPTION、Settings、字串層 v3

| 項目 | 檔案 | 測試 |
|---|---|---|
| `C05D90F0` 重新解析層(含可開關的「換到我的格式就重解一次」) | `qs_layer.S/.c`(一個標頭、三個入口、三個 replay) | `test_qs_layer` TestLayout 6 |
| `C05E84D8` 私有 pack 層(兩個觸發、每個 context 一次、塊內 64 對齊、失敗 → QS 維持原廠) | 同上,`qs_pack_place` | TestPack 6 |
| 共用列 ↔ enum 對照表(第一個 sup 從 cave 建表,只複製原廠 UHD/FHD 兩對;之後追加;enum 固定、k 只是位置) | 同上;`qs_check` 先預留 cave,`qs_hang` 不會失敗 | TestPairs 5(含實跑韌體轉換函式 `C06BDA30`) |
| `QS_OPTION`(17)、`IF_SLOT`(18) | `ui_apply.c`、`ui/fpui.py`(只新增;舊區塊逐位元組不變,有基準雜湊測試) | `test_qs_option` |
| `ui.options.resolution_qs()` + Settings N>3:每列摘要(k=2..4 換字、5..7 開旗標並插 5 bytes)、popup 位移 f(N)(3、8 實機,4..7 直線內插**未驗證**)、六個頁名(由 k=3 的 sup 做) | `ui/options.py` | 六個 sup 的 B2_5 = Jose 的位元組;一個 sup = OG 的 `resolution()` |
| §4.3 字串層 v3:標頭 +3 字(QS id / enum / 名字)、版本 2→3、入口 56/244/368 | `ui_strings.S`、`ui_strings_code.h`、`ui_pool.c/h`(`uis_qs_shared`、`uis_layer_add_qs`)、`ui/chain.py`(位移改讀產生的標頭) | 新舊 uishare 測試;unicorn 實跑 ARM 解析:值是誰的 enum 就回誰的名字、其他值回發號者的 |

依賴方回歸(全部用 v3 字串層重建後):lossless `test_v3_lossless`+`test_card_emulation` 48、`native/test_menu_page` 26、
`build_v3_lossless.py` 可建;OG `test_v3_og`+`test_v3_reslab`+`test_og_nested_strings` 32;raw-view `test_v3_rawview` 18;
screenflip `test_v3_flip` 13;Jose `test_v3_fmt` 14;`v3.test_v3` 22 —— 全過。

**堆疊對齊(2026-10-06)**:字串層 ENTER 改 16 bytes、uishare C 加 `-mstackrealign`;守衛 `test_stack_alignment.py`(見 NESTED_HOOKS.md §4)。

**同卡同版**:v3 字串層與 v2 互不承認。卡上用字串層的 sup(10LOSS、30OG3K/30OG2K、res-lab、50RAWV、40FLIP、31FMT)要一起用這版重建;
LOADER.BIN 不變(服務表沒動)。

**還沒做的**

- `uia_apply` 的 QS_OPTION 只在主機(C 對參考)驗過;ARM 版經 QS_OPTION 呼叫層函式這條路沒在 unicorn 跑過(層函式本身在 unicorn 裡跑過)。
- FS3 延後登記 pack 時的記憶體預留(§9.5)沒做(使用者接受殘留)。
- Jose 的格式改用 `resolution_qs`(拆檔)、他的 20 個錄影 hook 未拆。
- 舊說明:

- **§4.3 共用的「目前格式」id**:要改字串層(標頭 +3 字、入口全部位移、版本 2→3)。
  卡上與 d9 正在建的都是 v2 層,v2/v3 不能同卡 —— 改之前要所有依賴方同意一起重建(待決定)。
  ⚠ 另一個未知會決定這條路能不能用:**圖名是在解析時還是繪製時解析**。若是解析時,切換兩個自訂格式之間
  大圖磚不會換圖,要靠 §4.1 的重新解析。P4 第 2 步要看這個。
- `C05D90F0`(重新解析)與 `C05E84D8`(私有 pack)兩個共用層。
- `QS_OPTION` FPUI op 與 `uia_apply` 的整合(§9),共用列 ↔ enum 對照表(§4.4)。
- Settings 那邊 N>3 的每列摘要與 popup 位移(Jose 的 B2_5 7 筆)歸 add_option,未做。

## 13. 移交 res-custom:QS 解析度狀態的結論(2026-10-07)

**使用者決定**:解析度格式的 QS 大圖磚、底列、選擇、游標**統一由 res-custom 處理**,uishare 不再做通用 QS 共用層
(B 案「每列自己的狀態」停在前提 RE,未實作)。uishare 的字串層、FPUI/`uia_apply`、選單列維持;`qs_layer*` 不再改。
以下是到目前為止的結論，全部離線，標「實機」的除外。

### 13.1 上機看到的(2026-10-07,res-custom 卡 32RESCUS + 31OG3K/31OG2K)
- 大圖磚**永遠慢一步**:圖名在**版面解析時**就定;韌體在 setter 還沒寫入新值時就重新解析 QS(實機)。
- 開機後在 4 開 QS:大圖磚疊字/雜訊。原因：我們放掉快取(`v3(RCACHE)`)後沒有人重載 ——
  **QS 大圖磚的圖是 `UicResourceCache` 自己載的**(`v2` `C05663E8`:依模式挑 QS 名字 → `C0560A80` 載版面 →
  `C0560C48` → `C05D2ED0` 載整棵 tree 的圖);開 QS 的畫面載入不載這些圖。快取空著 → 圖沒載 → 雜訊(實機:L=0、log 無 QS 圖)。
- `RCACHE_V3` 的 r0 是快取物件(§4.1);OG 的 `og3k_ui.S ui_layout` 傳 tree,是錯的(未修，屬 d9)。
- 若真要「重解析」:原廠等價動作是 `v3(RCACHE)` 後立刻 `v2(RCACHE)`(重載 + 載圖 + 重新握住);但仍躲不掉 setter 內舊值的那次解析。
  所以結論是**不要靠動態圖名 + 重解析**。

### 13.2 時間軸(B 案前提;只有 k=2 實機)
- 解析度群組(tag `0x1000B`,`20/21_MV_Resolution`)原廠 `nA=1`、終點 `b=33`:兩個狀態 t=0(UHD)、t=33(FHD)
  (`research/ui/QUICK_SET.md`)。OG3K 加 t=66 給列 2 —— **實機可用**。
- 底列位置曲線 `C2358981` 原廠影格 t=0,33,66,100,133,166,200 → 間距 100/3。推論：**列 k 的時間 = ⌊100k/3⌋**
  (0,33,66,100,133,166,200,233)。k≥3 **未驗證**:Jose(N=8)只加一個 t=66 影格，列 3..7 超過終點被夾在 66,
  所以他(和我們的 §4.3)才需要「目前格式」的動態圖名。Jose 底列終點用 231 而不是 233,因為被夾住所以看不出差別。
- 影格值是階梯(取 ≤t 的最後一個影格)。所以影格放 ⌊100k/3⌋ 時，不管控制器時間是 33.33k 還是取整，都會落在自己那格(推論)。
- **上機驗證法**:N=4,列 3 加 t=100 影格(圖名固定)、終點 100;選列 3 看大圖磚是不是列 3 的圖。

### 13.3 每列一個狀態時要動的記錄(依 `ui/qs_recipe.json`,371 筆)
| 規則 | 筆數 | B 案怎麼做 | 依 N? |
|---|---|---|---|
| clone(大圖磚狀態,3 個 QS 集合 × 8 變體的各屬性) | 315 | 列 k 在 ⌊100k/3⌋ 插一個影格，圖名固定為該格式自己的;**要依時間排序插入**(現行 `clone_key` 是接在最後面，只對單一 t=66 正確) | 否(每列一個) |
| end(曲線終點) | 36 | `end = max(end, ⌊100k/3⌋)` | 否 |
| footer_label(底列第 k 格的字) | 12(列 2..7 × CINE/STILLlike) | 只寫自己那一格，圖名 = 自己的字串 | 每格一個 |
| qs_max(controller max) | 2 | 原廠沒有 max 屬性，插 1.0 再每列 +1.0 → N−1 | 是 |
| cursor(游標 clip 名) | 4 | `Cursor{N-1}`;原廠池有 Cursor2..7、Cursor9,**沒有 Cursor8** | 是 |
| footer_n8(底列位置與終點) | 2 | 只有 N=8 的值(Jose,實機);N=4..7 未知 | 是 |
| Settings popup 位移(B2_5) | 1+6 | `RES_POPUP_F`,只驗過 N=3、8 | 是 |
- **冪等、與順序無關仍成立**:每列只寫自己的影格(插入點由時間決定)、end 取 max、依 N 的欄位每列寫同一個值。
  但前提是 N 在第一筆記錄解析前就定了(N = 開機時 CSV 的列數)。
- **上限**:底列只有 8 格的實例(Jose);游標 clip 沒有 Cursor8,N 最多 8(Cursor7)。`RES_K_MAX = 7`。

### 13.4 B 案可以拿掉的(降低風險)
- §4.3 共用 QS id(`UIS_RES_QSCUR`、`uis_qs_shared`、字串層的 QSID/QSENUM/QSNAMES):圖名固定，不需要依值解析。
- `QS_FLAG_SWITCH`、重解析的放開規則、`RCACHE_V3` 呼叫:只剩**開機那一次**(層掛上前已解析的版面)要處理。
  做法是在第一次非 QS 版面載入時 `v3(RCACHE)` + `v2(RCACHE)`;或讓 sup 在 QS 版面第一次解析前就掛好(最好)。
- 動態圖名的「慢一步」整類問題消失。

### 13.5 給 res-custom 的介面提醒
- 大圖磚和游標顯示哪一格，取決於 widget 讀到的值經轉換器(`C06BDA30`,§4.4 的 (enum,row) 表)算出的列。
  **若設定區只存原廠值(UHD/FHD)**,widget 會顯示 UHD/FHD 那格。
  所以 res-custom 要讓 QS 讀到的值(或轉換器的結果)在選了自訂格式時回傳自己那一列，例如在 getter 或轉換器加一層：選了自訂 → 回傳它的 enum 或列。
- setter 寫入在 QS 版面重新解析**之後**(實機);B 案圖名固定，所以不受影響。
