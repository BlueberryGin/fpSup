---
name: fp-usb-transfer
description: SIGMA fp(Ver.5.02)USB shell 的高速上下行 —— MEM1(worker CMD 6-9)讓主機↔相機記憶體寫 398 MB/s、讀 382 MB/s，並由相機端的窗口表保護；怎麼用 Python/daemon 傳、怎麼上傳檔案進卡、怎麼把卡上的檔案下載回來、怎麼寫新的高速指令（參數與資料走 MEM1、觸發走借來的 shell 指令）和高速 hook 回呼（hook 寫環、主機用 MEMR 拉），以及失敗時怎麼查（fault 紀錄、原廠緩衝 0xC3025744）。Use whenever bytes have to move between the Mac and the camera's memory or card faster than `mem set`/`mem get`, when writing a host tool or camera routine that exchanges bulk data, when uploading/downloading files over the shell, or when a transfer fails, hangs ~50 s, or a command seems to vanish.
---

# USB 高速上下行（MEM1）

2026-10-06 上機實測（v3 卡 `projects/usb-shell-sup/cards/20261006-mem1/`、fpshd 3.2.0）：

| | 速度 | 備註 |
|---|---|---|
| 主機 → 相機記憶體（MEMW） | **398 MB/s** | 16 MiB 一筆，USB 3 Gen1 天花板 |
| 相機記憶體 → 主機（MEMR） | **382 MB/s** | 同上 |
| 池內隨機寫+讀回 | 5000 次 0 失敗 | 4 B～960 KB 混合 |
| 相機端 CRC（option 2） | 2.7 MB/s | CPU 摸每個位元組，**不要用** |
| 卡 ↔ 相機記憶體 | 卡本身（SD ≈94、讀 183 MB/s） | 檔案 API，見下 |

相機不複製、不算 CRC：worker 把 TRB 直接指到呼叫者的位元組。完整性靠
USB 3 封包 CRC + 相機回報實收位元組數 + **主機整份讀回比對**（預設開）。
設計與三個掉命令的根因在 `fpSup/fp_usb_shell/docs/TRANSFER.md`「MEM1」節。

## 先確認在用的是 MEM1

```sh
cd fpSup/fp_usb_shell
printf 'STATUS\n' | nc -U /tmp/fpshd.sock      # version=3.2.0 ... mem=MEM1-probed
printf 'MCAPS\n'  | nc -U /tmp/fpshd.sock      # OKMC 16776192 0x<pool> 1048576 + 8 個窗口
```

- `ERR unknown` → daemon 是舊的：`printf 'QUIT\n' | nc -U /tmp/fpshd.sock`，再 `./fpshd &`（`make fpshd` 會帶 `-lz`）。
- `ERR mcaps unsupported` → 相機跑的是舊 worker：換卡上 `\fpSup\00SHELL.BIN`（`v3/build_v3.shell_sup(burst15=True)`，`v3/deploy_v3.py CARD --only fpSup/00SHELL.BIN`），再 `python3 -B ui_drive.py reboot`（約 10 s，使用者已授權自己重開）。
- 舊 worker 時 `putfile`/`getfile` 自動退回舊路徑，不用改呼叫端。

## 保護：窗口表（相機端執行，不靠主機自律）

- 每一筆傳輸的**全部**位元組必須落在**同一個**有該權限的窗口裡，否則拒絕（code 4），什麼都不 arm。
- 隱含窗口：worker 自己的池（`MCAPS` 第 2、3 欄）——整塊可讀；**只有 +0x10000 之後可寫**（前面是命令框、擷取緩衝、窗口表、fault 紀錄）。
- 其他記憶體要先授權：`mem_window(slot, base, len, perm)`，perm 1 讀 / 2 寫 / 3 讀寫 / 0 撤銷；8 個 slot，slot 7 是 `read_direct` 的臨時授權。
  - 必須在 `0x40000000..0x80000000`（DMA 可達的 cached 別名）。映像 `0xC0000000` 起與 cave `0xC07xxxxx` **DMA 到不了**，用舊的 `read_bulk`/`put_slow`。
  - 可寫窗口還要 ≥ `0x44000000`、不得蓋到池前 64 KiB 與 worker 程式碼。
  - **只授權你自己配到的塊**（配置器、`cave.claim`）。窗口表擋的是寫錯位址，不擋你授權錯。
- 寫入以 **補齊到 1 KiB 的長度** 檢查：窗口尾端不足 1 KiB 的零頭寫不進去（會被拒，這是正確行為）。
- 資料沒到 → 相機先 EndTransfer 再做別的；worker 啟動（含熱抽換）清空窗口表。
- 拒絕碼：1 框（magic/版本/flags/CRC）、2 參數、4 沒有窗口、5 StartTransfer 被拒、6 資料沒到。

## Python（`putfile.py`）—— 寫工具時用這幾個

```python
import putfile as pf
caps = pf.mem_caps()                 # dict 或 None（舊 worker/daemon）
pool = caps['pool']; stage = pool + 0x10000; room = caps['pool_size'] - 0x10000

pf.mem_write(addr, data)             # 寫 + 整份讀回比對（verify=True）；失敗 raise RuntimeError
pf.mem_read(addr, n)                 # -> bytes
pf.mem_window(0, base, size, 3)      # 授權；用完 pf.mem_window(0, 0, 0, 0)
pf.put(addr, blob, 'label')          # 通用寫入：DMA 區走 MEM1，cave 走舊的檢查路徑
pf.read_direct(addr, n)              # 通用讀取：自己開 slot 7 臨時窗口、讀完撤銷
```

- `sync=True`（預設）在 DMA 前後整個 D-cache clean+invalidate（`0xC000E91C`，關中斷做 set/way）。CPU 剛寫過的資料要讀出來、或寫進去之後 CPU 要讀，都需要；純 DMA 對 DMA（例如卡讀進來的緩衝）可以 `sync=False`，實測速度差不多。
- 錯誤字串：`ERR mem refused ...` = 相機拒絕、**什麼都沒動**，可以改用別的路；`ERR mem uncertain ...` = 結果未知，**停下來**，讀回或查 fault 紀錄後再決定，不要盲目重送到別的位址（同位址重送是冪等的）。
- `FPSUP_NO_MEM1=1` 強制走舊路徑（例如要用舊路徑部署一個修好的 worker）。
- 測試一律 mock `putfile.mem_caps`，否則會透過正在跑的 daemon 碰到真相機。

## Daemon socket（給非 Python 工具）

一行一個請求，資料走 POSIX shm（呼叫端建立，至少 n bytes），不經 socket：

```
MCAPS                                   -> OKMC <xfer_max> <pool> <pool_size> (<base> <len> <perm>)x8
WIN <slot> <base> <len> <perm>          -> OKW | ERR win ...
MEMR <addr> <len> <opts> /<shm>         -> OKR <len> <ms>   | ERR mem refused|uncertain at=... done=...
MEMW <addr> <len> <opts> /<shm>         -> OKW <len> <ms>   | ERR mem ...
```

opts：1 = cache sync，2 = 相機 CRC（別用）。daemon 自己切成 ≤ 16 MiB−1 KiB 的傳輸、遇錯即停。
MEMW 是兩段式：命令 → 相機 arm 資料 TRB 後回 READY → 主機送資料 → DONE{實收, crc}。
READY 之後任何失敗，daemon 會先等過相機的資料逾時（700 ms + 1 ms/32 KiB）才送下一個命令，
否則下一個命令框會被當資料寫進目的地。自己實作協定時這條不能省。

## 上傳檔案進卡

```sh
python3 -B putfile.py local.bin '\DIR\NAME.BIN'          # 小於約 950 KB：暫存在 worker 池
python3 -B v3/deploy_v3.py CARD_DIR --only fpSup/X.BIN    # 部署卡片檔：先刪、寫、整檔讀回
```

- 流程：資料 MEMW 進池的暫存區（+0x10000，讀回比對）→ 借 echo 跑 `asm/putfile.S` 用韌體檔案 API 寫卡 → `dir` 確認大小。
- **mode 7 覆寫不截斷**：小檔蓋大檔會留舊尾巴 → 先刪（`deploy_v3.py` 會做；單檔用 `putfile.delete_file('\\DIR\\NAME.EXT')`，它拒絕根目錄、`\DCIM`、`\CINEMA`）。
- 暫存上限是池（1 MiB 減 64 KiB 減 file object）。**更大的檔**：跟配置器要一塊（下面「配大塊」），`mem_window` 授權、`mem_write` 進去，再 `putfile.py --buf <addr>` 從那塊寫卡；寫完撤銷窗口、釋放。這條組合還沒上機跑過，第一次要讀回卡上檔案驗證。

## 從卡下載檔案

```sh
python3 -B getfile.py '\DIR\NAME.BIN' out.bin                # 大小從 dir 取
python3 -B getfile.py '\DIR\NAME.BIN' out.bin --size 921600  # dir 被截斷時（假陰性）自己給
```

- 流程：`asm/getfile.S` 跟配置器（class 10）要 size+4 KiB → 檔案 API 讀進去 → `read_direct`（MEM1，臨時窗口）拉回 → 釋放。
- 900 KB 實測：寫 1 s、讀 1 s（多半是借 echo 與檔案 API 的固定成本），內容逐位元組相同。
- 「not in dir」只代表 `dir` 回覆被截斷，不代表檔案不存在 —— 給 `--size`。
- class 10 是 CinemaDNG 幀緩衝的通道：**錄影中不要 getfile**（配置器不能跨錄影）。

## 配大塊（給大檔或高速工作用）

```python
import cave
from callfn import call
desc = cave.claim('mytool.desc', 16)            # 描述子：別放堆疊、別挑固定位址
for w in range(4): pf.mem_set(desc + 4 * w, 0)
call(0xC001D740, r0=desc, r1=0, r2=size, r3=0)  # class 0（USER）；不要用 10
ok, buf = call(0xC001D7F0, r0=desc)              # 0 = 被拒
try:
    pf.mem_window(0, buf, size, 3)
    ...
finally:
    pf.mem_window(0, 0, 0, 0)
    call(0xC001D7A0, r0=desc, r1=2)              # 釋放
```

2026-10-06 class 0 給到 16 MiB、32/48/64 MiB 被拒（2026-08-27 class 10 曾給到 32 MiB）——用前先試、被拒要處理。
**不要跨錄影握著**。只在閒置時配、用完立刻還。

## 寫新的高速指令（相機端程式 + 主機）

現成的觸發只有「借 shell 指令的 handler」（`callfn.py`、`shellcmd.S`；見 skill fp-firmware-call），
一次觸發是幾個 `mem set` + 一個 `echo` 往返（毫秒級，未精量）。要快，就讓**資料不走觸發**：

1. 主機配一塊（上節）並授權窗口。
2. 參數區與資料都放在那塊裡：主機用 **一次 MEMW** 寫參數+輸入（不要一個字一個字 `mem set`，它還會掉寫入）。
3. 觸發：`callfn` 或借來的 echo handler 只帶「塊的位址」一個參數；程式在 shell 任務裡跑（可以阻塞、可以呼叫韌體）。
4. 結果寫回同一塊，主機 **一次 MEMR** 拉回。CPU 寫過的結果要 `sync=True`。
5. 參數區頭放魔數 + 序號 + 狀態字，程式最後才寫狀態字；主機看狀態字判斷有沒有跑完，不要只看 echo 有沒有回。

相機端程式碼本身仍在 cave（`cave.claim` 的塊，`put()` 走舊路徑寫），寫完跑 `0xC000E91C` + `0xC000EABC`。

## 寫高速 hook 回呼（相機推、主機拉）

hook（例如陀螺 2500 Hz 回呼、幀回呼）**不能碰 USB、不能阻塞、不能拿 mutex**。資料出相機的做法：

1. 環要放在**常駐**的記憶體：hook 在錄影中也會跑，而配置器臨時要的塊不能跨錄影（會凍結）。用 sup 自己在載入時 claim 的塊或它自己的池，不要用上面「配大塊」那種用完即還的塊。主機對那段授權**唯讀**窗口（≥ 0x40000000 且 DMA 可達）。
2. hook 只做「把記錄寫進環、最後更新寫入索引」：記錄先寫、`dmb`、再寫索引。
3. 主機輪詢：MEMR 讀索引（幾個字，`sync=True`）→ MEMR 讀新的那段環（`sync=True`，因為是 CPU 寫的）。382 MB/s 的通道，輪詢間隔由你決定。
4. 繞圈用序號判斷有沒有被覆蓋；主機慢了要能看出掉了幾筆。

**狀態：設計，未上機。** 注意 `sync=True` 是整個 D-cache set/way 並關中斷，對 2500 Hz 的 hook 有延遲影響 —— 第一次上機要量 hook 有沒有掉樣本（陀螺有現成的掉幀計數）。
EP83（相機主動推）：主機端在 fpshd 裡，**相機端從未送出過資料**，見 fp-camera-control；在它驗證之前，用上面的主機拉。

## 出事時怎麼查（不要猜）

| 症狀 | 先看 |
|---|---|
| `ERR mem refused code=4` | 窗口：位址+補齊 1 KiB 是否在**一個**有權限的窗口內；`MCAPS` 看窗口表 |
| `uncertain`、之後 shell 約 50 s 不回 | worker 在等；等它回來再查，**不要拔線**（拔線才會真的弄壞狀態） |
| 命令「消失」 | 原廠 OUT 緩衝：`p = mem_get(0xC3025744)[0]`，`mem_get(p, 8)` 若是 `FPSH`+你的 seq → 被驅動 arm 走了（NOPTP 補丁沒生效？確認 `0xC0033B3C == 0xE3A00000`、`0xC01E7704 == 0x4628E01E`） |
| worker 掛了什麼 | fault 紀錄：池 `+0x6C00` 計數（初值未清，看差值），`+0x6C10` 起 16 × 32 B `{rounds, phase, cmd, seq, arm_out, wait_out, arm_in, wait_in}`；phase 1 收到命令、2 MEMW 資料已 arm、3 READY 已送、4 資料已到、5 MEMR 區塊 |
| worker 狀態 | `0xC072F000` 16 字：+0C rounds、+20 served、+24 faults、+34 phase |

開機前幾筆 `arm_out=-1` 的 fault 是端點還沒建好，正常。

## 為什麼是這樣（改 worker 前讀）

- **所有等待看 TRB 的 HWO，不看事件旗標**（`wait_done`）。usbTask 用「任意事件」在等，會吃掉我們端點的完成位元；實測命令框已在緩衝裡、`Wait(OUT)` 卻等滿 50 s。
- **每個 OUT 完成後 `0xC31E3974 = 1`**，且 NOPTP 把 `FUN_c01e76b8` 的自動 arm 關掉：否則 XferNotReady 時驅動把 OUT arm 到 `[0xC3025744]`，主機的下一個命令落進原廠緩衝。
- **arm 命令 TRB 前先清命令框頭**：誤醒時不會把上一個命令再跑一次。
- **NOPTP 兩個補丁字**在 shell sup 補丁表（`patches.NOPTP`），v3 關機寫回原廠值。介面是 vendor class，沒有主機會講 PTP，關掉不損失功能。
- 測試：`test_worker_mem.py`（unicorn 跑真 worker，mutation 清單在其歷史記錄裡 23/23）、`host/tests/test_mem_host.py`（真 fpshd.c + 假 libusb）、`test_putfile_mem1.py`、`v3.test_v3`。改 worker 後全部跑，並做 mutation。

## 保持誠實

這份是 2026-10-06 一個 session 的量測。相機告訴你不同的事時，改這份並寫出新證據。
未上機的部分（大檔 `--buf`、hook 環、>16 MiB 多筆）標著「未上機」——跑過之後再改掉標記。
