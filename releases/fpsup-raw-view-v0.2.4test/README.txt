================================================================
 fpsup-raw-view  v0.2.4test
 SIGMA fp — RAW monitoring: the LCD shows what 12-bit CinemaDNG records
================================================================

English below the line of dashes; 中文在後面。
Illustrated explainer (EN / 简 / 繁):
  https://ijigen.github.io/fpSup/explainers/raw-view.html

WHAT IT IS

  Adds "RAW" as the 17th row of the COLOR menu. With it selected
  and the camera recording CinemaDNG 12-bit, live view shows the
  RAW the camera is about to record, not a finished picture:

  - Standby uses the recording gain. What clips on the screen
    clips in the DNG, and what does not, does not.
  - Sensor saturation is display white at every ISO.
  - Two curves, chosen with the box on the right of the RAW row
    (the box shows SA or GA):
      SA  Latitude, Stop Aligned: the per-ISO latitude of the fp
          (stops above and below 18% grey) spread over the screen
          with Rec.709's own stop spacing.
      GA  Latitude, Gray Aligned: the "LA - SIGMA Rec709" curves of
          SIGMA fp Rec709 LUT & Operations Guide v3 (Ole Berek).
    Both use the latitude figures from that guide.

  Two settings on the AEL page do something different in RAW:

  - Contrast: how the bottom of the latitude is shown. Monitoring
    only; the CinemaDNG is not affected.
      0     all 12.5 stops fit on the screen. The live view can
            resolve only about 10.9 stops below clipping, so the
            bottom ~1.6 stops show as fine noise dots: dots mean
            there is detail, pure black means crushed.
      +0.2  only the 10.9 stops the live view resolves; the bottom
            ~1.6 stops go black and the rest get more room.
  - Saturation: whether the camera's colour calibration is used.
      0     the default. No calibration on screen, and the
            CinemaDNG is exactly the same as a normal recording.
      +0.2  the camera's own calibration matrix (the one the other
            colour modes use) is applied on screen AND written into
            the CinemaDNG ColorMatrix tags, so Resolve shows the
            colours you monitored, at the same white balance as at 0.
            The raw pixels are not changed.
  - Sharpness is held at 0 while RAW is on.

  The recorded raw data is not altered.

  **Firmware Ver.5.02 only.** Test build.

WHAT CHANGED SINCE v0.2.3test

  - Fixed: with Saturation at +0.2, Resolve (and any DNG reader)
    showed the clip about 1100 K cooler than the same clip shot at 0
    or in OFF -- daylight read as ~7600 K instead of ~6500 K. The
    camera's white balance was right; the colour tags written at
    +0.2 described it as a different white. Now the white is the
    same at 0 and +0.2, and only the colour changes. Found and
    worked out by Luka (thank you).
  - Saturation 0 is unchanged: the CinemaDNG is exactly the same as
    a normal OFF recording. Monitoring and the raw pixels did not
    change.

  Checked on a camera (development build with the same RAW-View
  code): OFF, SA and GA at 0 write the stock tags; SA at +0.2 writes
  the new ones. This release card itself was not started on a camera.

WHAT CHANGED IN v0.2.3test

  - The RAW row is now ADDED to the COLOR list when the camera starts,
    through the same shared mechanism the other fpSup cards use for
    their menu entries, instead of RAW-View replacing the list on its
    own. The row, its icon and the menu behave as in v0.2.2test; what
    changes is that RAW-View can now share a card with other fpSup
    cards that add menu entries (Lossless, OpenGate).
  - Fixed: on a card that also carries Lossless, the box on the right
    of the RAW row showed -5 / +5 instead of SA / GA.
  - Nothing about monitoring, playback or the recorded files changed.


  Known, unchanged since v0.2.0test: with RAW on, the colour mode the
  camera saves at power-off is a value the stock firmware does not
  define (it is how RAW is remembered). This is under review
  (2026-10-01 settings rule).

WHAT CHANGED IN v0.2.2test

  - Playback is stock again. With RAW on, CinemaDNG clips played back
    on the camera through the RAW view curve for the current ISO, and
    lost their own ISO gain, so an ISO 100 and an ISO 800 clip looked
    equally bright. Playback now looks exactly as it does in any
    other colour mode; RAW view comes back when you return to shooting.

  Since v0.2.0test:
  - RAW is remembered across a power-off, with SA/GA, Contrast and
    Saturation: start the camera and it comes back in RAW as you
    left it. (Saved at a normal power-off; see LIMITS.)
  - Half the size, so it now fits in fpSup-Merge together with the
    shell, gyro and open gate. The camera works the curves out at
    start-up instead of carrying them on the card.
  - SA near clipping is closer to its design curve (v0.2.0test was
    up to 0.5% off there); GA is unchanged.

  Since v0.1.0test: SA follows Rec.709's stop spacing; Contrast and
  Saturation became 0 / +0.2; crushed shadows always read black; the
  box on the RAW row shows SA / GA.

THE CARD IN THIS FOLDER

  AutoRun.txt + fpSup.BIN + FPSUPUI/ (keep the three together).
  Every start runs the AutoRun, then shows the banner
  fpSup-RAW-v0.2.4test!  For Fast Start 3, or to combine with other
  fpSup cards, use fpSup-Merge.

INSTALL

  1. Copy the card's files to the ROOT of the SD card the camera
     boots from, the FPSUPUI folder too.
  2. Start the camera with the USB cable unplugged.
  3. Recording format CinemaDNG, 12-bit. COLOR menu -> row 17, RAW.

LIMITS

  - CinemaDNG 12-bit only. MOV and 8/10-bit are not supported.
    UHD records 8-bit CinemaDNG: in UHD the screen jumps about one
    stop brighter when recording starts.
  - RAW is saved at a normal power-off only; after a battery pull
    the last change is lost, as with the camera's own settings. It
    is not saved while a custom mode (C1-C3) is active.
  - RAW is saved as a colour mode the stock firmware does not know.
    Started WITHOUT this card, the camera shows Standard; pick any
    colour mode to clear it, or put the card back. Without the card,
    avoid the COLOR button menu while it shows Standard: it can
    change Standard's saved adjustments.
  - Choosing OFF in Quick Set does not leave RAW; choose OFF (or
    another mode) in the COLOR menu.
  - Auto exposure can meter up to 1-2 EV darker than stock in
    scenes with bright highlights: live view now has the highlight
    room 12-bit recording has.
  - In fpSup-Merge it combines with the shell, gyro, open gate and
    Lossless.

REMOVE

  Delete the files (or take the card out) and switch the camera off.
  Every firmware word the card changed is written back as the
  camera powers off.

----------------------------------------------------------------
 fpsup-raw-view  v0.2.4test
 SIGMA fp —— RAW 監看:螢幕顯示 12-bit CinemaDNG 實際錄到的東西
----------------------------------------------------------------

圖解說明(EN / 简 / 繁):
  https://ijigen.github.io/fpSup/explainers/raw-view.html

這是什麼

  在 COLOR(色彩模式)選單加上第 17 列「RAW」。選到它、而且錄影格式是
  CinemaDNG 12-bit 時,即時影像顯示的是即將錄下的 RAW,而不是修飾過的畫面:

  - 待機使用錄影時的增益:螢幕上過曝的地方 DNG 也過曝,沒過曝的就沒有。
  - 每個 ISO 的感光元件飽和 = 顯示白。
  - 兩條曲線,用 RAW 列右側的框選(框上顯示 SA 或 GA):
      SA  Latitude — Stop Aligned:fp 各 ISO 的寬容度(中灰以上、以下幾檔),
          依 Rec.709 本身的檔距鋪在螢幕上。
      GA  Latitude — Gray Aligned:SIGMA fp Rec709 LUT & Operations Guide v3
          (Ole Berek)的「LA - SIGMA Rec709」曲線。
    兩者都用這份指南的寬容度數據。

  RAW 時 AEL 頁有兩項作用不同:

  - 對比度:寬容度底部怎麼顯示。只影響監看,CinemaDNG 不受影響。
      0     12.5 檔全部放進螢幕。預覽在削波以下只分得出約 10.9 檔,
            所以最底約 1.6 檔以細小噪點出現:有點就是有細節,純黑才是壓死。
      +0.2  只放預覽分得出的 10.9 檔;最底約 1.6 檔變黑,其餘檔位更寬。
  - 飽和度:要不要用相機的色彩校正。
      0     預設。螢幕不套校正,CinemaDNG 與一般錄下的檔案完全相同。
      +0.2  螢幕套用相機自己的校色矩陣(其他色彩模式用的那一組),
            同一組也寫進 CinemaDNG 的 ColorMatrix,Resolve 顯示的顏色與監看一致,
            白平衡與飽和度 0 時相同。RAW 像素本身不動。
  - RAW 時清晰度固定為 0。

  錄下的 RAW 資料不會被改動。

  **僅限韌體 Ver.5.02。** 測試版。

與 v0.2.3test 的差別

  - 修正:飽和度 +0.2 時,Resolve(或任何 DNG 軟體)顯示的白比同樣場景在 0 或 OFF
    錄的偏冷約 1100 K —— 日光讀成約 7600 K 而不是約 6500 K。相機的白平衡是對的,
    是 +0.2 寫進的色彩標籤把它描述成另一個白。現在 0 與 +0.2 的白相同,只有顏色不同。
    這個問題由 Luka 發現並推導出解法(謝謝)。
  - 飽和度 0 不變:CinemaDNG 與一般 OFF 錄下的完全相同。監看與 RAW 像素都沒變。

  上機確認(開發版,RAW-View 程式相同):OFF、SA、GA 在 0 寫原廠標籤;
  SA 在 +0.2 寫新標籤。這張釋出卡本身尚未在相機上開機。

與 v0.2.2test 的差別

  - RAW 列改為開機時「添加」到 COLOR 清單,和其他 fpSup 卡加選單的方式相同,
    所以可以和 Lossless、OpenGate 同卡。
  - 修正:和 Lossless 同卡時,RAW 列右側的框顯示 -5 / +5 而不是 SA / GA。

與 v0.2.1test 的差別

  - 回放恢復原廠顯示。RAW 開著時,相機回放 CinemaDNG 會套用目前 ISO 的 RAW 監看曲線,
    而且片段自己的 ISO 增益被抹掉,所以 ISO 100 和 ISO 800 的片段看起來一樣亮。
    現在回放和其他色彩模式完全一樣;回到拍攝畫面時 RAW 監看自動恢復。

  自 v0.2.0test 起:
  - 關機後會記住 RAW,連同 SA/GA、對比度、飽和度:開機後自動回到上次的 RAW 設定。
    (正常關機時存檔;見「限制」。)
  - 大小減半,現在可以在 fpSup-Merge 裡和 shell、陀螺儀、open gate 一起合併。
    曲線改由相機開機時計算,不再整份帶在卡上。
  - SA 在削波附近更接近設計曲線(v0.2.0test 在那裡最多偏 0.5%);GA 不變。

  自 v0.1.0test 起:SA 依 Rec.709 檔距;對比度與飽和度改為 0 / +0.2;
  壓死的暗部一定顯示純黑;RAW 列右側的框顯示 SA / GA。

這個資料夾裡的卡

  AutoRun.txt + fpSup.BIN + FPSUPUI/(三者放在一起)。每次開機跑 AutoRun,
  然後顯示 fpSup-RAW-v0.2.4test!  要 Fast Start 3 或和其他 fpSup 卡合併,
  請用 fpSup-Merge。

安裝

  1. 把卡的檔案複製到相機開機用的 SD 卡**根目錄**
     (FPSUPUI 資料夾一起)。
  2. 不插 USB 線開機。
  3. 錄影格式選 CinemaDNG 12-bit,COLOR 選單 → 第 17 列 RAW。

限制

  - 只支援 CinemaDNG 12-bit。MOV 與 8/10-bit 不支援。
    UHD 錄的是 8-bit CinemaDNG:UHD 下開始錄影時畫面會亮約一檔。
  - 只在正常關機時存檔;拔電池會失去最後一次的改動,和相機自己的設定一樣。
    使用自訂模式(C1–C3)時不會存 RAW。
  - RAW 是以原廠不認得的色彩模式存檔。**不插這張卡**開機時相機會顯示 Standard;
    選任一個色彩模式即可清掉,或插回卡。沒插卡時顯示 Standard 的期間,
    請避免打開 COLOR 按鈕選單:它可能改到 Standard 存的調整值。
  - 在 QS(快速設定)選 OFF 不會離開 RAW;請在 COLOR 選單選 OFF 或其他模式。
  - 場景有亮部時,自動曝光可能比原廠暗 1~2 EV:即時影像現在有 12-bit 錄影
    同樣的高光空間。
  - 在 fpSup-Merge 裡可以和 shell、陀螺儀、open gate、Lossless 合併。

移除

  刪掉檔案(或拔卡),然後關機。卡片改過的每個韌體字,會在相機關機時寫回。
