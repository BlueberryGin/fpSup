================================================================
 Screen flip  fpsup-screenflip-v0.1.0test
 SIGMA fp Ver.5.02 only -- Loader v3 card
================================================================

WHAT IT IS

  Flips the rear screen. Four choices: Off, 180, Mirror, 180+Mirror.
  Each custom display mode has its own choice, separately for STILL
  and CINE (custom 1-4 in each). The screen flips when you switch
  INTO that mode (DISP button, turning the camera on, STILL/CINE
  switch); changing the value in the menu does not flip the screen
  by itself.

  - 180 turns the whole screen, picture and menus, and the touch
    panel follows it.
  - Mirror flips only the picture left-right; menus stay as they are.
  - The "LCD off" display mode is never flipped.
  - Your choices are kept in the card's own \fpSup\40FLIP.BIN, not
    in the camera's settings. Take the card out and the camera is
    back to normal.

  This replaces lcdflip (an "LCD Flip 180" row on the LCD settings
  page). lcdflip was never released and is no longer developed.

  中文:
  翻轉背螢幕。四個選項:Off、180、Mirror、180+Mirror。
  每個自訂顯示模式各有一個值,STILL 與 CINE 各自的自訂 1-4 分開。
  切換「進入」那個模式時才翻(DISP 鍵、開機、STILL/CINE 切換);
  在選單裡改值不會立刻翻螢幕。
  - 180:整個螢幕(影像與選單)翻轉,觸控跟著翻。
  - Mirror:只有影像左右鏡像,選單不動。
  - 「LCD 關閉」那個顯示模式不翻。
  - 設定存在卡上自己的 \fpSup\40FLIP.BIN,不寫進相機設定。
    拔掉卡,相機就回到原樣。
  取代 lcdflip(放在 LCD 設定頁的「LCD Flip 180」列);lcdflip
  從未發布,已停止開發。

HOW TO USE

  Copy AutoRun.txt and the fpSup folder to the root of the SD card and
  start the camera.  To combine with other fpSup products released for
  Loader v3, copy their fpSup folders over this one: every product
  carries the same LOADER.BIN and UI frames, and its own 40FLIP.BIN.

  Upgrading from an older (single fpSup.BIN) card: delete the old
  fpSup.BIN from the card root.  A card from before Loader v3 cannot be
  mixed with this one.

  Where the setting is: MENU > SYSTEM, page 2 > display mode settings
  > pick Custom 1-4 > More options > page 3 (TOOLS), 5th row
  "Screen Flip".  Set a value, leave the menu, then switch to that
  custom mode with DISP to see it flip.  If you changed the custom
  mode you are already in, switch away and back once.

  Combining: every product on the card that adds menu text must come
  from the same generation of Loader v3 releases (the shared menu-text
  code changed format on 2026-10-07); an older one is skipped at start.

  If you plug or unplug HDMI the screen goes back to normal; switch
  display mode once to flip it again.

  中文:把 AutoRun.txt 與 fpSup 資料夾放進 SD 卡根目錄後開機。
  設定位置:MENU > SYSTEM 第 2 頁 > 顯示模式設定 > 選自訂 1-4 >
  更多選項 > 第 3 頁(TOOLS)第 5 列「Screen Flip」。設好後離開選單,
  用 DISP 切到那個自訂模式就會翻。改的若是目前所在的自訂模式,
  切走再切回來一次。插拔 HDMI 會恢復正常,再切一次顯示模式即可。
  同卡合併:會加選單文字的產品必須是同一代 Loader v3 發布
  (共用選單文字格式 2026-10-07 定版),較舊的會在開機時被略過。

WHAT CHANGED

  - First release. Loader v3 card: one file per product in \fpSup\.
  - 首次發布。

WHAT HAS BEEN VERIFIED

  Offline, on THIS release's own files (MANIFEST.txt hashes): the card
  boots in an emulator against the real firmware image, the menu row
  is added byte-for-byte as expected, the screen flips only when
  entering a mode, STILL and CINE keep separate values, an "LCD off"
  mode is left alone, and the choices are saved into 40FLIP.BIN once
  they have not changed for a second and the camera is not recording.

  On the camera, with DEVELOPMENT builds of the same design (not these
  files): the row shows and works, 180 and 180+Mirror flip when you
  switch into the mode and not when you set them, and the card ran
  together with Lossless, Gyro, Open Gate 3K and RAW view.
  Not yet seen on the camera:
  - saving: the development builds never saved (they read the wrong
    "is recording" flag); this release fixes it, untested on the camera;
  - Mirror alone (picture only) has never been tried on the camera.

  THIS RELEASE HAS NOT BEEN WRITTEN TO A CARD OR TESTED ON THE CAMERA.

  中文:
  離線驗證(本版檔案本身,雜湊見 MANIFEST.txt):模擬器裡對真韌體映像開機、
  選單列逐位元組正確、只在切入模式時翻、STILL/CINE 各自的值、「LCD 關閉」不動、
  值 1 秒沒變且不在錄影時才存進 40FLIP.BIN。
  相機上(同設計的開發版,不是本版檔案):選單列可用;180 與 180+Mirror 在切入
  模式時翻、設定時不翻;與 Lossless、Gyro、Open Gate 3K、RAW view 同卡運作。
  尚未在相機上看過:
  - 存檔:開發版從沒存成功(讀錯了「錄影中」旗標);本版已修,未上機;
  - 單獨 Mirror(只翻影像)從未在相機上試過。
  本版尚未寫卡、尚未上機。
