fpSup-Gyro v1.14.1test — SIGMA fp firmware Ver.5.02 only

PUBLIC TEST BUILD. Original-camera audio ending early or drifting ahead of the
picture has been reported with the GCSV edition. This version reduces the work
done while writing GCSV; nobody has yet confirmed that it fixes the audio.
Please test with expendable footage before using it for an important take.

What changed from v1.14.0

  - Numbers are written directly into the GCSV text buffer. They no longer
    take a second trip through a temporary digit buffer.
  - A pending gyro sample stays in its owned input block while that block is
    being formatted. Only a sample that must survive the call is copied.

Offline ARM execution produced exactly the same GCSV rows as before. On two
synthetic 2,048-record blocks it executed 14–29% fewer ARM instructions and
kept one card write per block. This is an instruction count, not a measured
speedup on the camera or proof of audio synchronisation.

Install

For a merged card, open the Gyro-only selection below; keep only Gyro selected
when testing the reported audio drift. Other selected products change the test.
fpSup-Merge: https://ijigen.github.io/fpSup/tools/card-composer/?pick=gyro
Or unzip the standalone test archive.
Standalone archive: https://github.com/ijigen/fpSup/raw/main/gyro/release/fp-gyro-sup-v1.14.1test.zip
Copy AutoRun.txt, fpSup.BIN and the complete FPSUPUI folder to the root of the
SD card used to boot the camera. The ordinary standalone card does not enable
Fast Start 3; that remains an option on the merge page. Use CinemaDNG. MOV
recording does not produce the GCSV and JSON sidecars.

Each CinemaDNG take writes A001_037.gcsv and A001_037.json inside its clip
folder. Load both with the DNG frames in Gyroflow and synchronise that take.
The DNG files contain timecode and a frame-rate tag; the Gyroflow offset still
needs to be determined for each take.

What to report from a test

  - Record a visible and audible clap near the start and near the end of a
    take long enough to reveal the reported drift. Note whether the WAV ends
    before the last DNG frame, and whether the end contains silence.
  - Keep the original DNG, WAV and GCSV files. Note recording duration, frame
    rate, audio setting, SD card or external SSD, and whether the camera ran
    this exact v1.14.1test card or a merged card.

The previous v1.14.0 card remains available in releases/fpsup-gyro-v1.14.0/.
Offline verification and exact hashes are in MANIFEST.txt. This test build has
not been validated on a camera. It has not been shown to repair audio drift.

中文

這是給網友實測的版本。先前有人回報 GCSV 版錄出的原始影片聲音逐漸超前，
片尾甚至提早結束。本版減少 GCSV 轉文字的工作，但尚未經相機實拍證實能改善聲音。
請先用不重要的素材測試。適用 SIGMA fp Ver.5.02；錄製 CinemaDNG 時會在片段
資料夾產生 .gcsv 和 .json，MOV 不會產生這兩個檔案。

用上方的 fpSup-Merge 專用連結測試時，請只選 Gyro；其他產品會改變測試條件。
也可解開上面的獨立測試包，把 AutoRun.txt、fpSup.BIN 及
整個 FPSUPUI 資料夾放在開機 SD 卡的根目錄。獨立卡不啟用 Fast Start 3；
合併頁可自行選擇。每段素材仍須在 Gyroflow 個別找同步偏移。

請在錄影開頭和結尾各拍一次看得到也聽得到的拍手，保留原始 DNG、WAV、GCSV，
並記錄片長、格率、錄音設定、SD 卡或外接 SSD、實際載入版本。特別確認 WAV
是否在最後一張畫面之前結束，以及尾段是無聲還是完全沒有音訊資料。
本版只有離線 ARM、輸出內容和封裝驗證；聲畫同步仍待實拍。
