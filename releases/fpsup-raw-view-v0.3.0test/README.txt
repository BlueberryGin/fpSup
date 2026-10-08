================================================================
 fpsup-raw-view  v0.3.0test
 SIGMA fp — RAW monitoring: the LCD shows what 12-bit CinemaDNG records
 Loader v3 card
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
            colours you monitored. The raw pixels are not changed.
  - Sharpness is held at 0 while RAW is on.

  The recorded raw data is not altered.

  **Firmware Ver.5.02 only.** Test build.

WHAT CHANGED SINCE v0.2.3test

  - Moved to Loader v3: one file, \fpSup\45RAWV.BIN, next to the
    other fpSup products. Merging = copying their fpSup folders
    together. If another product already uses a place in the
    firmware that RAW view needs, RAW view does not load and changes
    nothing; the camera starts without it.
  - RAW (on/off, SA/GA, contrast, saturation) is now saved in RAW
    view's own file on the SD card, not in the camera's settings.
    The camera's colour mode stays OFF, a mode the firmware knows,
    so without the card the camera simply shows OFF.
    A camera that still holds the old card's unknown colour value is
    read once at the first start with this card and set back to OFF.
  - Saturation is saved as its full value (the old card kept one bit).
  - Monitoring, playback and the recorded files: unchanged.

HOW TO USE

  1. Copy AutoRun.txt and the fpSup folder to the ROOT of the SD card
     the camera boots from. Upgrading from an older card: delete the
     old fpSup.BIN (and FPSUPUI) from the card root; a card from before
     Loader v3 cannot be mixed with this one.
  2. Start the camera with the USB cable unplugged.
  3. Recording format CinemaDNG, 12-bit. COLOR menu -> last row, RAW.

  To combine with other fpSup products released for Loader v3, copy
  their fpSup folders over this one: every product carries the same
  LOADER.BIN and UI frames, and its own NNNAME.BIN.

LIMITS

  - CinemaDNG 12-bit only. MOV and 8/10-bit are not supported.
    UHD records 8-bit CinemaDNG: in UHD the screen jumps about one
    stop brighter when recording starts.
  - RAW is saved when the camera saves its own settings at power-off,
    and only when it changed. After a battery pull the last change
    is lost. It is not saved while a custom mode (C1-C3) is active.
    The SD card must not be write-protected.
  - If the colour mode was changed while the card was out, RAW is not
    turned back on: choose it again.
  - Choosing OFF in Quick Set does not leave RAW; choose OFF (or
    another mode) in the COLOR menu.
  - Auto exposure can meter up to 1-2 EV darker than stock in
    scenes with bright highlights: live view now has the highlight
    room 12-bit recording has.

REMOVE

  Delete the files (or take the card out) and switch the camera off.
  Every firmware word the card changed is written back as the
  camera powers off; the camera's own settings hold no RAW-view value.

WHAT HAS BEEN VERIFIED

  Only what MANIFEST.txt lists counts. 45RAWV.BIN in this folder
  (sha256 701532fe...) has been tested on a computer only: the real
  loader and firmware image in an emulator, 18 tests (load, every
  changed word declared, write-back at power-off, refusing to load
  when a place is taken, the save into its own file and the restore).
  On the camera (2026-10-07) the build before it was tested, which
  differs only in two constants of the shared menu code: six products
  on one card all loaded; the RAW row, SA/GA; saving into the file and
  restoring after a restart; switching back to Standard was saved too.
  Not yet tested: saving at a real power-off with the switch, and a
  recording compared against v0.2.3test.

  **Firmware Ver.5.02 only.** Test build.

----------------------------------------------------------------
 fpsup-raw-view  v0.3.0test
 SIGMA fp —— RAW 監看:螢幕顯示 12-bit CinemaDNG 實際錄到的東西
 Loader v3 卡
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
            同一組也寫進 CinemaDNG 的 ColorMatrix,Resolve 顯示的顏色與監看一致。
            RAW 像素本身不動。
  - RAW 時清晰度固定為 0。

  錄下的 RAW 資料不會被改動。

  **僅限韌體 Ver.5.02。** 測試版。

與 v0.2.3test 的差別

  - 改用 Loader v3:一個檔 \fpSup\45RAWV.BIN,和其他 fpSup 產品並排。
    合併 = 把各產品的 fpSup 資料夾複製在一起。若別的產品已經佔用 RAW 監看
    需要的韌體位置,RAW 監看就不載入、什麼都不改,相機照常開機。
  - RAW 設定(開關、SA/GA、對比、飽和度)改存在 RAW 監看自己在 SD 卡上的檔案,
    不再存進相機設定。相機的色彩模式維持 OFF(原廠認得的值),
    所以沒插卡時相機就是顯示 OFF。
    相機裡若還留著舊卡存的未定義色彩值,插這張卡第一次開機時會讀一次並改回 OFF。
  - 飽和度存完整的值(舊卡只存一個位元)。
  - 監看、回放、錄下的檔案:不變。

安裝

  1. 把 AutoRun.txt 與 fpSup 資料夾複製到相機開機用的 SD 卡**根目錄**。
     從舊卡升級:刪掉卡根目錄的舊 fpSup.BIN(與 FPSUPUI);Loader v3 之前的卡
     不能和這張混用。
  2. 不插 USB 線開機。
  3. 錄影格式選 CinemaDNG 12-bit,COLOR 選單 → 最後一列 RAW。

  要和其他 Loader v3 的 fpSup 產品合併:把它們的 fpSup 資料夾複製過來覆蓋即可,
  每個產品都帶同一份 LOADER.BIN 與 UI 畫面,加上自己的 NNNAME.BIN。

限制

  - 只支援 CinemaDNG 12-bit。MOV 與 8/10-bit 不支援。
    UHD 錄的是 8-bit CinemaDNG:UHD 下開始錄影時畫面會亮約一檔。
  - 在相機關機存自己設定的時候存檔,而且有改才存。拔電池會失去最後一次的改動。
    使用自訂模式(C1–C3)時不會存 RAW。SD 卡不能鎖寫入。
  - 沒插卡時改過色彩模式的話,RAW 不會自動打開,請再選一次。
  - 在 QS(快速設定)選 OFF 不會離開 RAW;請在 COLOR 選單選 OFF 或其他模式。
  - 場景有亮部時,自動曝光可能比原廠暗 1~2 EV:即時影像現在有 12-bit 錄影
    同樣的高光空間。

移除

  刪掉檔案(或拔卡),然後關機。卡片改過的每個韌體字,會在相機關機時寫回;
  相機自己的設定裡沒有任何 RAW 監看的值。

驗證過的

  以 MANIFEST.txt 列的檔案為準。本資料夾的 45RAWV.BIN(sha256 701532fe…)
  只在電腦上測過:真的 loader 與韌體映像在模擬器裡跑 18 項(載入、改過的字都有申報、
  關機寫回、位置被佔就不載入、存進自己的檔案與還原)。
  上機(2026-10-07)測的是前一個建置,差別只在共用選單程式的兩個常數:
  六個產品同卡全部載入;RAW 列、SA/GA;存進檔案、重開後還原;切回 Standard 也存得進。
  尚未測:真的撥開關關機時存檔;錄影與 v0.2.3test 對照。

  **僅限韌體 Ver.5.02。** 測試版。
