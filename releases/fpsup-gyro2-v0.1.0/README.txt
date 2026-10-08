fpSup-Gyro2  fpsup-gyro2-v0.1.0
===============================

ENGLISH

What it is
  Gyro2 writes the SIGMA fp's own gyroscope and level data into every
  CinemaDNG frame it records. The "Gyroflow (fpSup)" plugin for DaVinci
  Resolve reads it straight from the frames and stabilises the clip.
  There are no extra files to keep next to the clip and no manual sync:
  each frame is timed by the sensor's own frame clock.
  The stabilisation itself is done by Gyroflow (https://gyroflow.xyz/).
  The plugin is a fork of Gyroflow's gyroflow-plugins (GPL-3.0), source at
  https://github.com/ijigen/gyroflow-plugins. Report plugin problems to
  fpSup, not to Gyroflow.

How it differs from fpSup-Gyro (Gyro1)
  - Gyro1 writes a .gcsv and a .json next to each clip; you load them in
    the Gyroflow app and sync by hand every take.
  - Gyro2 puts the data inside each DNG. You use it in Resolve with the
    Gyroflow (fpSup) plugin. No sync step.
  - Gyro2 also carries the rolling-shutter readout time, the lens
    correction data (it follows focus), and the level sensor.
  - Works together with fpSup-Lossless.

What you need
  - SIGMA fp on firmware Ver.5.02.
  - CinemaDNG recording (MOV is not supported).
  - The "Gyroflow (fpSup)" OpenFX plugin for DaVinci Resolve.
  - A lens with electronic contacts gives lens data automatically. A
    manual lens works too: enter its focal length in the plugin.

Install
  Copy AutoRun.txt and the fpSup folder to the root of the SD card, as
  with the other fpSup products. To combine with other fpSup products,
  put the fpSup folders together (LOADER.BIN is the same in all).

Use
  1. Record CinemaDNG as usual.
  2. In Resolve, put the clip on the timeline and add the effect
     "Gyroflow (fpSup)" (group fpSup). It loads the gyro data by itself.
  3. Adjust Smoothness and Zoom limit to taste.
  4. Manual lens: in the "fpSup lens" group, set Manual lens focal (mm).

Tested
  These exact bytes, on the camera (2026-10-07):
  - Open Gate 3K and Open Gate 2K at 29.97p, with Lossless on.
  - Shutter 1/50.
  - Electronic lenses (LUMIX S 40mm F2) and a manual lens (Nokton 28mm F1.4).

Not tested yet
  - FHD and UHD, 59.94p, 120p and other frame rates (earlier test builds
    of Gyro2 worked at FHD 59.94p; these bytes have not been tried there).
  - Takes longer than a few minutes.

Known limits
  - Hand-held close-ups (under about 1 m): the lens also moves sideways,
    which the gyro cannot see, so some shake stays.
  - Fast shake at slow shutter speeds leaves motion blur that no
    stabilisation can remove.
  - If the camera skips frames while recording, Resolve splits the clip
    in two; each part stabilises on its own.

Remove
  Delete fpSup/20GYR2.BIN from the card.


中文

這是什麼
  Gyro2 把 SIGMA fp 的陀螺儀與水平儀資料寫進錄下的每一張 CinemaDNG。
  DaVinci Resolve 的「Gyroflow (fpSup)」外掛直接從畫面檔讀取並防震。
  不用另外保存檔案，也不用手動同步：每一幀都用感光元件自己的幀時鐘定時。
  防震本身是 Gyroflow（https://gyroflow.xyz/）做的。外掛是 Gyroflow 的
  gyroflow-plugins 的分支（GPL-3.0），原始碼在 https://github.com/ijigen/gyroflow-plugins。
  外掛的問題請回報給 fpSup，不要回報給 Gyroflow。

跟 fpSup-Gyro（Gyro1）的差別
  - Gyro1 在片段旁邊另寫 .gcsv 和 .json，要在 Gyroflow 程式裡載入，每段都要手動同步。
  - Gyro2 把資料放在每張 DNG 裡，在 Resolve 用 Gyroflow (fpSup) 外掛，不用同步。
  - Gyro2 也帶有果凍效應的讀出時間、鏡頭校正資料（會跟著對焦）、水平儀。
  - 可以和 fpSup-Lossless 一起用。

需要
  - SIGMA fp，韌體 Ver.5.02。
  - 錄 CinemaDNG（不支援 MOV）。
  - DaVinci Resolve 的「Gyroflow (fpSup)」OpenFX 外掛。
  - 有電子接點的鏡頭會自動帶鏡頭資料；手動鏡頭也能用，在外掛填焦距即可。

安裝
  跟其他 fpSup 產品一樣，把 AutoRun.txt 和 fpSup 資料夾複製到 SD 卡根目錄。
  要和其他 fpSup 產品合併，把各自的 fpSup 資料夾疊在一起（LOADER.BIN 都相同）。

使用
  1. 照常錄 CinemaDNG。
  2. 在 Resolve 把片段放上時間軸，加上效果「Gyroflow (fpSup)」（在 fpSup 分組），它會自己讀陀螺資料。
  3. 依喜好調整 Smoothness 與 Zoom limit。
  4. 手動鏡頭：在「fpSup lens」群組填 Manual lens focal (mm)。

測試過
  以下是這組位元組實際上機的結果（2026-10-07）：
  - 開放片幅 3K 與 2K，29.97p，開啟 Lossless。
  - 快門 1/50。
  - 電子鏡頭（LUMIX S 40mm F2）與手動鏡頭（Nokton 28mm F1.4）。

還沒測試
  - FHD、UHD、59.94p、120p 等其他格式與幀率（Gyro2 早期測試版在 FHD 59.94p 正常，但這組位元組還沒在那些格式試過）。
  - 超過幾分鐘的長時間錄影。

已知限制
  - 手持拍近物（約 1 公尺內）：鏡頭同時在平移，陀螺量不到，會留一點晃。
  - 慢快門下的快速晃動會有動態模糊，防震修不掉。
  - 錄影時相機若跳幀，Resolve 會把片段分成兩段，各段各自防震。

移除
  刪除卡上的 fpSup/20GYR2.BIN。
