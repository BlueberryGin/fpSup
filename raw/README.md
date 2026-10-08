# RAW firmware probes

[English](#english) | [繁體中文](#繁體中文)

## English

This directory contains camera-side probes and their host-side safety gates for
the fp RAW path. A probe belongs here only when its live evidence, offline
verification, and unfinished work are stated separately.

| Area | Status | Contents |
|---|---|---|
| [FHD lossless codec](lossless_codec/) | Scratch and movie-path encode-to-RAM passed live; 8-frame burst passed; compressed writer/card/playback not established | Exact CinemaDNG writer probe, staged codec preflights, assembly sources, and unit tests; current gates in [`ROADMAP.md`](../../projects/lossless-sup/notes/ROADMAP.md) |

These tools inject code into RAM through `fp_usb_shell`; they are not firmware
updates and do not produce an unsigned `.bin` for flashing.

## 繁體中文

這裡收錄 fp RAW 路徑的相機端探針，以及主機端安全檢查。每一組探針都必須把
「實機證據」、「離線驗證」與「尚未完成」分開寫清楚。

| 項目 | 狀態 | 內容 |
|---|---|---|
| [FHD 無損 codec](lossless_codec/) | scratch 與 movie-path encode-to-RAM 已實機通過；8-frame burst 通過；compressed writer／寫卡／回放尚未成立 | CinemaDNG exact-writer 探針、分階段 codec 預檢、組合語言原始碼與單元測試；目前 gates 見 [`ROADMAP.md`](../../projects/lossless-sup/notes/ROADMAP.md) |

這些工具透過 `fp_usb_shell` 把程式注入 RAM；它們不是韌體更新，也不會產生可刷入的
未簽章 `.bin`。
